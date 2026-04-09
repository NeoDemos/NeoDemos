#!/usr/bin/env python3
"""
Build Domain Gazetteer — Unified entity dictionary for NER and LLM grounding
=============================================================================

Queries the PostgreSQL kg_entities + kg_mentions tables for the most-mentioned
clean entities per type, merges with the Rotterdam political dictionary and the
raadslid_rollen table, and outputs a single domain_gazetteer.json.

Downstream consumers:
  - MCP search tools (entity resolution)
  - Whisper post-processing (NER correction)
  - RAG chain grounding (entity linking)

Usage:
    python scripts/build_domain_gazetteer.py              # Full build
    python scripts/build_domain_gazetteer.py --dry-run    # Preview stats only
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

# ---------------------------------------------------------------------------
# Project bootstrap
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/neodemos")

LEXICON_PATH = PROJECT_ROOT / "data" / "lexicons" / "rotterdam_political_dictionary.json"
OUTPUT_PATH = PROJECT_ROOT / "data" / "knowledge_graph" / "domain_gazetteer.json"

# ---------------------------------------------------------------------------
# Entity type → (output key, limit)
# ---------------------------------------------------------------------------
ENTITY_QUERIES = [
    ("Organization", "organisations", 500),
    ("Project",      "projects",      200),
    ("Programma",    "programmes",    200),
    ("Location",     "locations",     300),
    ("Committee",    "committees",     50),
    ("Budget",       "financial_entities", 100),
    ("Party",        "parties_from_kg", None),  # None = no limit
]

# Generic noise words to exclude
NOISE_WORDS = {
    "hij", "zij", "geld", "klant", "burger", "kind",
    "De wethouder", "De voorzitter", "de wethouder", "de voorzitter",
    "Voorzitter", "De commissie", "Commissie",
    "De Burgemeester", "De Secretaris", "De raad", "Uw raad",
    "B&W", "cliënt", "primaire overheidstaak", "individuele makers",
    "organisaties", "de groep", "Project", "bouwproject", "brug", "bouw",
    "Het programma", "programma's", "aanpak", "Geld", "kosten",
    "Baten", "Lasten", "raden", "gemeenteraden", "Raadsvoorstellen",
}


def fetch_top_entities(cur, entity_type: str, limit: int | None) -> list[str]:
    """
    Fetch top entities of a given type by mention count.

    Joins kg_entities with kg_mentions and applies quality filters.
    """
    # Build the exclusion list as SQL placeholders
    noise_list = list(NOISE_WORDS)
    placeholders = ", ".join(["%s"] * len(noise_list))

    sql = f"""
        SELECT e.name, COUNT(m.id) AS mention_count
        FROM kg_entities e
        JOIN kg_mentions m ON m.entity_id = e.id
        WHERE e.type = %s
          AND LENGTH(e.name) BETWEEN 3 AND 60
          AND e.name NOT LIKE '{{%%}}'
          AND e.name NOT LIKE '%%\n%%'
          AND e.name NOT IN ({placeholders})
        GROUP BY e.name
        HAVING COUNT(m.id) >= 5
        ORDER BY mention_count DESC
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    params = [entity_type] + noise_list
    cur.execute(sql, params)
    return [row["name"] for row in cur.fetchall()]


def fetch_persons(cur) -> dict:
    """
    Build persons dict from raadslid_rollen.

    When multiple roles exist for the same person, the most recent is primary
    and all roles are stored as a list.
    """
    cur.execute("""
        SELECT volledige_naam, naam, partij, rol,
               periode_van::text AS periode_van,
               periode_tot::text AS periode_tot
        FROM raadslid_rollen
        ORDER BY periode_van DESC
    """)
    rows = cur.fetchall()

    # Group by surname (naam)
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["naam"]].append(row)

    persons = {}
    for surname, roles in grouped.items():
        # roles are already sorted DESC by periode_van, so first is most recent
        primary = roles[0]
        all_roles = [
            {
                "role": r["rol"],
                "party": r["partij"],
                "period_from": r["periode_van"],
                "period_to": r["periode_tot"],
            }
            for r in roles
        ]
        persons[surname] = {
            "full_name": primary["volledige_naam"],
            "party": primary["partij"],
            "role": primary["rol"],
            "period_from": primary["periode_van"],
            "period_to": primary["periode_tot"],
            "roles": all_roles,
        }

    return persons


def load_lexicon() -> dict:
    """Load the Rotterdam political dictionary."""
    with open(LEXICON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def build_gazetteer(dry_run: bool = False) -> dict:
    """Main build routine."""
    print(f"Connecting to database...")
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()

    gazetteer = {
        "_generated": datetime.utcnow().isoformat(),
        "_description": "Unified domain gazetteer for NeoDemos NER and LLM grounding",
    }

    # ------------------------------------------------------------------
    # Step 1: KG entities by type
    # ------------------------------------------------------------------
    print("Querying kg_entities + kg_mentions...")
    for entity_type, key, limit in ENTITY_QUERIES:
        entities = fetch_top_entities(cur, entity_type, limit)
        gazetteer[key] = entities
        print(f"  {key:25s} {len(entities):>5} entities  (type={entity_type}, limit={limit})")

    # ------------------------------------------------------------------
    # Step 2: Persons from raadslid_rollen
    # ------------------------------------------------------------------
    print("Querying raadslid_rollen...")
    persons = fetch_persons(cur)
    gazetteer["persons"] = persons
    print(f"  {'persons':25s} {len(persons):>5} unique surnames")

    cur.close()
    conn.close()

    # ------------------------------------------------------------------
    # Step 3: Merge with lexicon
    # ------------------------------------------------------------------
    print(f"Loading lexicon from {LEXICON_PATH.name}...")
    lexicon = load_lexicon()

    gazetteer["parties"] = lexicon.get("parties", [])
    gazetteer["municipal"] = lexicon.get("municipal_terms", [])
    gazetteer["financial"] = lexicon.get("financial_terms", [])
    gazetteer["rotterdam_places"] = lexicon.get("rotterdam_specific", [])

    # Merge committee_names into committees (deduplicate)
    lexicon_committees = lexicon.get("committee_names", [])
    kg_committees = gazetteer.get("committees", [])
    merged_committees = list(dict.fromkeys(kg_committees + lexicon_committees))
    gazetteer["committees"] = merged_committees
    print(f"  Merged committees: {len(kg_committees)} from KG + {len(lexicon_committees)} from lexicon = {len(merged_committees)} unique")

    # Keep council_members.surnames for reference
    council_surnames = lexicon.get("council_members", {}).get("surnames", [])
    if council_surnames:
        gazetteer["_council_member_surnames_ref"] = council_surnames
        print(f"  Council member surnames (reference): {len(council_surnames)}")

    # ------------------------------------------------------------------
    # Step 4: Write output
    # ------------------------------------------------------------------
    if dry_run:
        print("\n--- DRY RUN: not writing file ---")
    else:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(gazetteer, f, ensure_ascii=False, indent=2)
        print(f"\nWrote {OUTPUT_PATH}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n=== Domain Gazetteer Summary ===")
    for key, value in gazetteer.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict):
            print(f"  {key:30s} {len(value):>6} entries")
        elif isinstance(value, list):
            print(f"  {key:30s} {len(value):>6} entries")
    print("================================")

    return gazetteer


def main():
    parser = argparse.ArgumentParser(
        description="Build unified domain gazetteer from KG entities, lexicon, and raadslid_rollen."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview stats without writing the output file.",
    )
    args = parser.parse_args()

    build_gazetteer(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
