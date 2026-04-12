#!/usr/bin/env python3
"""
Seed programma_aliases — Three-tier mapping pipeline
=====================================================

Maps Rotterdam programma labels to IV3 taakveld codes using a cheapest-first
strategy (no LLM required):

  Tier A: Kruistabel extraction from begroting/jaarstukken table chunks
  Tier B: Keyword pattern matching against programma labels
  Tier C: spaCy NL similarity fallback (nl_core_news_lg)

Each tier only processes programma labels not already mapped by a prior tier
(enforced by the UNIQUE constraint on ``(gemeente, jaar, programma_label)``).

Results are written to a YAML review file by default.  Use ``--commit`` to
write to the ``programma_aliases`` DB table.

Usage:
    # Run all tiers (dry-run)
    python scripts/seed_programma_aliases.py

    # Run a specific tier
    python scripts/seed_programma_aliases.py --tier a
    python scripts/seed_programma_aliases.py --tier b
    python scripts/seed_programma_aliases.py --tier c

    # Filter to specific year(s)
    python scripts/seed_programma_aliases.py --year 2024
    python scripts/seed_programma_aliases.py --year 2020 --year 2024

    # Commit to DB after review
    python scripts/seed_programma_aliases.py --commit

    # Custom output path
    python scripts/seed_programma_aliases.py --output data/financial/my_review.yml
"""

import argparse
import json
import logging
import os
import re
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB helpers (matches existing script patterns)
# ---------------------------------------------------------------------------


def _build_db_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if url:
        return url
    h = os.getenv("DB_HOST", "localhost")
    p = os.getenv("DB_PORT", "5432")
    d = os.getenv("DB_NAME", "neodemos")
    u = os.getenv("DB_USER", "postgres")
    pw = os.getenv("DB_PASSWORD", "postgres")
    return f"postgresql://{u}:{pw}@{h}:{p}/{d}"


DB_URL = _build_db_url()


def _get_conn():
    return psycopg2.connect(DB_URL)


# ---------------------------------------------------------------------------
# Load IV3 reference data
# ---------------------------------------------------------------------------


def load_iv3_taakvelden(conn) -> dict:
    """Return {code: omschrijving} for all IV3 taakvelden."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT code, omschrijving FROM iv3_taakvelden")
            return {row[0]: row[1] for row in cur.fetchall()}
    except Exception:
        conn.rollback()
        logger.warning("iv3_taakvelden table not found, loading from JSON")

    json_path = PROJECT_ROOT / "data" / "financial" / "iv3_taakvelden.json"
    with open(json_path) as f:
        data = json.load(f)
    return {tv["code"]: tv["omschrijving"] for tv in data["taakvelden"]}


# ---------------------------------------------------------------------------
# Discover unmapped programma labels from financial_lines
# ---------------------------------------------------------------------------


def get_all_programma_labels(conn, year_filter: list[int] | None = None) -> list[dict]:
    """Return distinct (gemeente, jaar, programma) from financial_lines.

    Each entry is {gemeente, jaar, programma_label}.
    """
    query = """
        SELECT DISTINCT gemeente, jaar, programma AS programma_label
        FROM financial_lines
        WHERE programma IS NOT NULL
          AND programma != ''
    """
    params = []
    if year_filter:
        placeholders = ", ".join(["%s"] * len(year_filter))
        query += f" AND jaar IN ({placeholders})"
        params.extend(year_filter)

    query += " ORDER BY gemeente, jaar, programma"

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, params)
        return [dict(row) for row in cur.fetchall()]


def get_already_mapped(conn) -> set:
    """Return the set of (gemeente, jaar, programma_label) already in programma_aliases."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT gemeente, jaar, programma_label FROM programma_aliases")
            return {(row[0], row[1], row[2]) for row in cur.fetchall()}
    except Exception:
        conn.rollback()
        return set()


def filter_unmapped(
    labels: list[dict],
    already_mapped: set,
) -> list[dict]:
    """Remove labels already present in programma_aliases."""
    return [
        lbl for lbl in labels
        if (lbl["gemeente"], lbl["jaar"], lbl["programma_label"]) not in already_mapped
    ]


# =========================================================================
# TIER A: Kruistabel extraction
# =========================================================================


def run_tier_a(conn, iv3_lookup: dict, year_filter: list[int] | None = None) -> list[dict]:
    """Tier A: Extract kruistabel mappings from begroting/jaarstukken table chunks.

    Delegates to extract_kruistabel.find_kruistabel_mappings.
    """
    from scripts.extract_kruistabel import find_kruistabel_mappings

    all_mappings = []
    years = year_filter or [None]

    for yr in years:
        mappings = find_kruistabel_mappings(conn, iv3_lookup, year_filter=yr)
        all_mappings.extend(mappings)

    logger.info("[TIER A] Kruistabel: %d mappings found", len(all_mappings))
    return all_mappings


# =========================================================================
# TIER B: Keyword matching
# =========================================================================

# Each rule: (regex_pattern, iv3_code, human_readable_note)
# The pattern is matched case-insensitively against the programma_label.
KEYWORD_RULES = [
    # Veiligheid
    (r"veilig", "1.2", "Openbare orde en veiligheid"),
    (r"brandweer|crisis", "1.1", "Crisisbeheersing en brandweer"),

    # Verkeer & vervoer
    (r"verkeer|vervoer|mobiliteit", "2.1", "Verkeer en vervoer"),
    (r"parkeren", "2.2", "Parkeren"),
    (r"haven|waterwegen", "2.4", "Economische havens en waterwegen"),
    (r"openbaar\s*vervoer", "2.5", "Openbaar vervoer"),

    # Economie
    (r"economie|economisch", "3.1", "Economische ontwikkeling"),
    (r"bedrijvenloket|bedrijfsregelingen", "3.3", "Bedrijvenloket en bedrijfsregelingen"),
    (r"promotie|attractie|citymarketing", "3.4", "Economische promotie"),

    # Onderwijs
    (r"onderwijs(?!huisvesting)", "4.3", "Onderwijsbeleid en leerlingenzaken"),
    (r"onderwijshuisvesting", "4.2", "Onderwijshuisvesting"),

    # Sport, cultuur, recreatie
    (r"sport", "5.1", "Sportbeleid en activering"),
    (r"cultuur", "5.3", "Cultuurpresentatie etc"),
    (r"musea|museum", "5.4", "Musea"),
    (r"erfgoed|monumenten", "5.5", "Cultureel erfgoed"),
    (r"groen|park|recreatie|buitenruimte", "5.7", "Openbaar groen en recreatie"),

    # Sociaal domein
    (r"samenkracht|burgerkracht|burgerparticipatie|wijkaanpak", "6.1", "Samenkracht en burgerparticipatie"),
    (r"wijkteam|eerstelijns|toegang", "6.2", "Toegang en eerstelijnsvoorzieningen"),
    (r"bijstand|uitkering|inkomen(?!sten)", "6.3", "Inkomensregelingen"),
    (r"wsw|beschut\s*werk|sociale\s*werkvoorzien", "6.4", "WSW en beschut werk"),
    (r"werk.*inkomen|participatie(?!.*burger)|arbeidsmarkt|re.?integratie", "6.5", "Arbeidsparticipatie"),
    (r"wmo|maatschappelijke.*ondersteuning", "6.6", "Maatwerkvoorzieningen (WMO)"),
    (r"jeugd|jeugdzorg|jeugdhulp", "6.72", "Maatwerkdienstverlening 18-"),
    (r"ouderen|ouderenbeleid|18\+", "6.71", "Maatwerkdienstverlening 18+"),
    (r"maatschappelijke\s*opvang|vrouwenopvang|beschermd\s*wonen", "6.82", "Geescaleerde zorg 18+"),

    # Volksgezondheid & milieu
    (r"volksgezondheid|gezondheid|zorg(?!.*jeugd)", "7.1", "Volksgezondheid"),
    (r"riool|riolering", "7.2", "Riolering"),
    (r"afval", "7.3", "Afval"),
    (r"milieu|duurzaam|energietransitie|klimaat", "7.4", "Milieubeheer"),
    (r"begraafplaats|crematori", "7.5", "Begraafplaatsen en crematoria"),

    # Volkshuisvesting, ruimtelijke ordening
    (r"ruimte|stedelijk|leefomgeving|gebiedsontwikkeling|stadsontwikkeling", "8.1", "Ruimte en leefomgeving"),
    (r"grondexploitatie|grondbeleid", "8.2", "Grondexploitatie"),
    (r"wonen|woningbouw|volkshuisvesting|bouwen", "8.3", "Wonen en bouwen"),

    # Bestuur & ondersteuning
    (r"bestuur(?!.*bedrijf)|governance|college.*burgemeester|raad(?!s.*lid)", "0.1", "Bestuur"),
    (r"burgerzaken", "0.2", "Burgerzaken"),
    (r"financ|treasury|begroting|schuldenbeheer", "0.5", "Treasury"),
    (r"overhead|bedrijfsvoering|concernondersteuning", "0.4", "Overhead"),
    (r"belasting|ozb|heffing", "0.64", "Belastingen overig"),
    (r"algemene\s*(uitkering|middelen|dekkingsmiddelen)|gemeentefonds", "0.7", "Algemene uitkering gemeentefonds"),
    (r"reserves|mutaties\s*reserves", "0.10", "Mutaties reserves"),
]


def run_tier_b(
    conn,
    iv3_lookup: dict,
    labels: list[dict],
    already_mapped: set,
) -> list[dict]:
    """Tier B: Keyword pattern matching.

    Only processes programma labels not already mapped (by Tier A or manual).
    """
    unmapped = filter_unmapped(labels, already_mapped)
    logger.info("[TIER B] Keyword matching: %d unmapped labels to process", len(unmapped))

    mappings = []

    for lbl in unmapped:
        programma = lbl["programma_label"]
        matched = False

        for pattern, iv3_code, note in KEYWORD_RULES:
            if re.search(pattern, programma, re.IGNORECASE):
                # Verify the IV3 code exists in our reference
                if iv3_code not in iv3_lookup:
                    logger.warning(
                        "[TIER B] IV3 code %s not found in reference for pattern '%s'",
                        iv3_code, pattern,
                    )
                    continue

                mappings.append({
                    "gemeente": lbl["gemeente"],
                    "jaar": lbl["jaar"],
                    "programma_label": programma,
                    "iv3_taakveld": iv3_code,
                    "confidence": "0.90",
                    "source": "keyword",
                    "notes": f"Matched pattern /{pattern}/ -> {iv3_code} ({note})",
                })
                matched = True
                break  # first match wins (rules are ordered by specificity)

        if not matched:
            logger.debug("[TIER B] No keyword match for: '%s' (%s/%d)",
                         programma, lbl["gemeente"], lbl["jaar"])

    logger.info("[TIER B] Keyword matching: %d mappings produced", len(mappings))
    return mappings


# =========================================================================
# TIER C: spaCy NL similarity fallback
# =========================================================================


def _load_spacy_model():
    """Load nl_core_news_lg with graceful fallback."""
    try:
        import spacy
    except ImportError:
        logger.error(
            "[TIER C] spacy not installed. Install with: pip install spacy && "
            "python -m spacy download nl_core_news_lg"
        )
        return None

    try:
        nlp = spacy.load("nl_core_news_lg")
        return nlp
    except OSError:
        logger.error(
            "[TIER C] nl_core_news_lg model not found. Install with: "
            "python -m spacy download nl_core_news_lg"
        )
        return None


def run_tier_c(
    conn,
    iv3_lookup: dict,
    labels: list[dict],
    already_mapped: set,
    similarity_threshold: float = 0.70,
) -> tuple[list[dict], list[dict]]:
    """Tier C: spaCy similarity matching.

    Returns (mappings, unmapped) where unmapped are labels that fell below
    the similarity threshold (for manual review).
    """
    unmapped_labels = filter_unmapped(labels, already_mapped)
    logger.info("[TIER C] spaCy similarity: %d unmapped labels to process", len(unmapped_labels))

    if not unmapped_labels:
        return [], []

    nlp = _load_spacy_model()
    if nlp is None:
        logger.warning("[TIER C] Skipping — spaCy model not available")
        return [], [
            {"programma_label": lbl["programma_label"], "gemeente": lbl["gemeente"],
             "jaar": lbl["jaar"], "reason": "spacy_model_unavailable"}
            for lbl in unmapped_labels
        ]

    # Pre-compute spaCy docs for all IV3 taakveld descriptions
    iv3_docs = {}
    for code, omschrijving in iv3_lookup.items():
        iv3_docs[code] = nlp(omschrijving)

    mappings = []
    still_unmapped = []

    for lbl in unmapped_labels:
        programma = lbl["programma_label"]
        prog_doc = nlp(programma)

        # Skip if the programma doc has no vectors (very short / OOV)
        if not prog_doc.has_vector or prog_doc.vector_norm == 0:
            still_unmapped.append({
                "programma_label": programma,
                "gemeente": lbl["gemeente"],
                "jaar": lbl["jaar"],
                "reason": "no_vector",
            })
            continue

        # Find the most similar IV3 taakveld
        best_code = None
        best_sim = -1.0
        best_omschrijving = ""

        for code, iv3_doc in iv3_docs.items():
            if not iv3_doc.has_vector or iv3_doc.vector_norm == 0:
                continue

            sim = prog_doc.similarity(iv3_doc)
            if sim > best_sim:
                best_sim = sim
                best_code = code
                best_omschrijving = iv3_lookup[code]

        if best_code and best_sim >= similarity_threshold:
            mappings.append({
                "gemeente": lbl["gemeente"],
                "jaar": lbl["jaar"],
                "programma_label": programma,
                "iv3_taakveld": best_code,
                "confidence": f"{best_sim:.2f}",
                "source": "spacy",
                "notes": f"spaCy similarity={best_sim:.3f} -> {best_code} ({best_omschrijving})",
            })
            logger.info(
                "[TIER C] '%s' -> %s (%s) [sim=%.3f]",
                programma, best_code, best_omschrijving, best_sim,
            )
        else:
            still_unmapped.append({
                "programma_label": programma,
                "gemeente": lbl["gemeente"],
                "jaar": lbl["jaar"],
                "reason": f"best_similarity={best_sim:.3f} < {similarity_threshold} "
                          f"(best={best_code}: {best_omschrijving})",
            })
            logger.info(
                "[TIER C] UNMAPPED '%s' (best: %s sim=%.3f)",
                programma, best_code, best_sim,
            )

    logger.info(
        "[TIER C] spaCy: %d mappings, %d still unmapped",
        len(mappings), len(still_unmapped),
    )
    return mappings, still_unmapped


# =========================================================================
# DB commit
# =========================================================================


def commit_mappings(conn, mappings: list[dict]) -> tuple[int, int]:
    """Write mappings to programma_aliases.

    Returns (inserted, skipped).
    """
    inserted = 0
    skipped = 0

    with conn.cursor() as cur:
        for m in mappings:
            cur.execute(
                """
                INSERT INTO programma_aliases
                    (gemeente, jaar, programma_label, iv3_taakveld, confidence, source)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (gemeente, jaar, programma_label) DO NOTHING
                """,
                (
                    m["gemeente"],
                    m["jaar"],
                    m["programma_label"],
                    m["iv3_taakveld"],
                    Decimal(m["confidence"]),
                    m["source"],
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1

    conn.commit()
    return inserted, skipped


# =========================================================================
# YAML output
# =========================================================================


def write_yaml(
    all_mappings: list[dict],
    unmapped: list[dict],
    output_path: str,
):
    """Write the combined results to a YAML review file."""
    # Group mappings by (source, gemeente, jaar)
    by_tier = defaultdict(lambda: defaultdict(list))
    for m in all_mappings:
        tier_key = m["source"]
        group_key = f"{m['gemeente']}_{m['jaar']}"
        by_tier[tier_key][group_key].append({
            "programma_label": m["programma_label"],
            "iv3_taakveld": m["iv3_taakveld"],
            "confidence": float(m["confidence"]),
            "notes": m.get("notes", ""),
        })

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w") as f:
        f.write("# Programma -> IV3 Taakveld mapping proposals\n")
        f.write("# Generated by scripts/seed_programma_aliases.py\n")
        f.write("# Review before committing with --commit\n\n")

        for tier_name in ["kruistabel", "keyword", "spacy"]:
            if tier_name not in by_tier:
                continue

            f.write(f"\n# === Tier: {tier_name} ===\n")
            f.write(f"{tier_name}:\n")

            for group_key in sorted(by_tier[tier_name].keys()):
                f.write(f"\n  {group_key}:\n")
                for entry in by_tier[tier_name][group_key]:
                    f.write(f"    - programma_label: \"{entry['programma_label']}\"\n")
                    f.write(f"      iv3_taakveld: \"{entry['iv3_taakveld']}\"\n")
                    f.write(f"      confidence: {entry['confidence']}\n")
                    if entry.get("notes"):
                        f.write(f"      notes: \"{entry['notes']}\"\n")

        if unmapped:
            f.write("\n# === Unmapped (needs manual review) ===\n")
            f.write("unmapped:\n")
            for u in unmapped:
                f.write(f"  - programma_label: \"{u['programma_label']}\"\n")
                f.write(f"    gemeente: \"{u['gemeente']}\"\n")
                f.write(f"    jaar: {u['jaar']}\n")
                f.write(f"    reason: \"{u['reason']}\"\n")

    logger.info("Wrote review file to %s", path)


# =========================================================================
# CLI
# =========================================================================


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Seed programma_aliases: map Rotterdam programma labels to IV3 "
            "taakveld codes using a three-tier approach (kruistabel, keyword, spaCy)"
        ),
    )
    parser.add_argument(
        "--tier", choices=["a", "b", "c", "all"], default="all",
        help="Run a specific tier: a=kruistabel, b=keyword, c=spaCy, all=sequential (default: all)",
    )
    parser.add_argument(
        "--year", type=int, action="append", dest="years",
        help="Filter to specific year(s). Can be repeated: --year 2020 --year 2024",
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="Write mappings to programma_aliases table (default: dry-run)",
    )
    parser.add_argument(
        "--output", "-o", type=str,
        default="data/financial/programma_aliases_proposed.yml",
        help="Path for YAML review file",
    )
    parser.add_argument(
        "--similarity-threshold", type=float, default=0.70,
        help="Minimum spaCy similarity for Tier C (default: 0.70)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    conn = _get_conn()
    try:
        iv3_lookup = load_iv3_taakvelden(conn)
        logger.info("Loaded %d IV3 taakvelden codes", len(iv3_lookup))

        # Get all programma labels from financial_lines
        all_labels = get_all_programma_labels(conn, year_filter=args.years)
        logger.info("Found %d distinct (gemeente, jaar, programma) entries in financial_lines",
                     len(all_labels))

        if not all_labels:
            print("\nNo programma labels found in financial_lines.")
            print("Run the financial_lines_extractor first to populate financial_lines.")
            return

        already_mapped = get_already_mapped(conn)
        pre_existing_count = len(already_mapped)
        logger.info("Already mapped in programma_aliases: %d entries", pre_existing_count)

        all_mappings: list[dict] = []
        unmapped: list[dict] = []
        tier_stats: dict[str, int] = {}

        # --- Tier A: Kruistabel ---
        if args.tier in ("a", "all"):
            tier_a_mappings = run_tier_a(conn, iv3_lookup, year_filter=args.years)
            tier_stats["kruistabel"] = len(tier_a_mappings)
            all_mappings.extend(tier_a_mappings)

            # Update already_mapped with Tier A results (so Tier B skips them)
            for m in tier_a_mappings:
                already_mapped.add((m["gemeente"], m["jaar"], m["programma_label"]))

        # --- Tier B: Keyword ---
        if args.tier in ("b", "all"):
            tier_b_mappings = run_tier_b(conn, iv3_lookup, all_labels, already_mapped)
            tier_stats["keyword"] = len(tier_b_mappings)
            all_mappings.extend(tier_b_mappings)

            # Update already_mapped with Tier B results (so Tier C skips them)
            for m in tier_b_mappings:
                already_mapped.add((m["gemeente"], m["jaar"], m["programma_label"]))

        # --- Tier C: spaCy ---
        if args.tier in ("c", "all"):
            tier_c_mappings, tier_c_unmapped = run_tier_c(
                conn, iv3_lookup, all_labels, already_mapped,
                similarity_threshold=args.similarity_threshold,
            )
            tier_stats["spacy"] = len(tier_c_mappings)
            all_mappings.extend(tier_c_mappings)
            unmapped.extend(tier_c_unmapped)

        # --- Write YAML review file ---
        output_path = str(PROJECT_ROOT / args.output)
        write_yaml(all_mappings, unmapped, output_path)

        # --- Summary ---
        print(f"\n{'=' * 70}")
        print(f"  Programma -> IV3 Taakveld Mapping Pipeline")
        print(f"{'=' * 70}")
        print(f"  Input:   {len(all_labels)} distinct programma labels from financial_lines")
        print(f"  Pre-existing mappings: {pre_existing_count} (untouched)")
        print()

        total_mapped = 0
        for tier_name in ["kruistabel", "keyword", "spacy"]:
            count = tier_stats.get(tier_name, 0)
            total_mapped += count
            if tier_name in tier_stats:
                marker = "A" if tier_name == "kruistabel" else ("B" if tier_name == "keyword" else "C")
                conf = {"kruistabel": "1.00", "keyword": "0.90", "spacy": "var"}[tier_name]
                print(f"  Tier {marker} ({tier_name:12s}): {count:4d} mappings  [confidence={conf}]")

        print(f"  {'─' * 50}")
        print(f"  Total new mappings:     {total_mapped:4d}")
        print(f"  Still unmapped:         {len(unmapped):4d}")
        print()

        # Show unmapped labels for attention
        if unmapped:
            print("  Unmapped labels (need manual review):")
            for u in unmapped[:20]:
                print(f"    - {u['programma_label']} ({u['gemeente']}/{u['jaar']})")
                print(f"      Reason: {u['reason']}")
            if len(unmapped) > 20:
                print(f"    ... and {len(unmapped) - 20} more (see YAML file)")
            print()

        print(f"  YAML review file: {output_path}")

        # --- Commit if requested ---
        if args.commit:
            if not all_mappings:
                print("\n  Nothing to commit (0 new mappings).")
            else:
                inserted, skipped = commit_mappings(conn, all_mappings)
                print(f"\n  Committed to DB: {inserted} inserted, {skipped} skipped (already exist)")
        else:
            if all_mappings:
                print(f"\n  Dry-run mode. Use --commit to write {total_mapped} mappings to DB.")

        print(f"{'=' * 70}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
