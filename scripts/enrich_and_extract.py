#!/usr/bin/env python3
"""
Tier 2 Rule-Based Metadata Extraction for document_chunks
==========================================================

Day-1 quick win: populate 6 new metadata columns on all 1.6M chunks
using deterministic rules only — zero LLM calls.

Fields extracted:
  1. key_entities    TEXT[]  — domain-gazetteer matches from PARENT DOC TITLE
  2. section_topic   TEXT    — committee + truncated doc title
  3. vote_outcome    TEXT    — aangenomen/verworpen/ingetrokken/aangehouden
  4. vote_counts     JSONB   — {"voor": N, "tegen": M}
  5. indieners       TEXT[]  — submitter names from motie/amendement text
  6. motion_number   TEXT    — e.g. M2023-042

Server-side cursor (itersize 2000) + batch UPDATEs (default 500) keep
RAM constant regardless of table size.  Checkpoint every 50 000 rows.

Usage:
    python scripts/enrich_and_extract.py                          # Full run
    python scripts/enrich_and_extract.py --limit 1000             # Smoke test
    python scripts/enrich_and_extract.py --resume                 # Resume from checkpoint
    python scripts/enrich_and_extract.py --batch-size 300         # Smaller batches
    python scripts/enrich_and_extract.py --tier2-only             # (default) Tier 2 rules only
"""

import os
import sys
import json
import re
import argparse
import logging
import time
from pathlib import Path
from datetime import datetime

# ── Project bootstrap ─────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from tqdm import tqdm

from services.party_utils import PARTY_ALIASES, CANONICAL_PARTIES

# ── Configuration ─────────────────────────────────────────────────────

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/neodemos")
GAZETTEER_PATH = PROJECT_ROOT / "data" / "knowledge_graph" / "domain_gazetteer.json"
CHECKPOINT_DIR = PROJECT_ROOT / "data" / "pipeline_state"
CHECKPOINT_PATH = CHECKPOINT_DIR / "tier2_checkpoint.json"
LOG_PATH = PROJECT_ROOT / "logs" / "enrich_and_extract.log"

# Lists in the gazetteer that feed key_entities (NOT parties)
GAZETTEER_LISTS = [
    "organisations", "projects", "programmes", "locations",
    "committees", "rotterdam_places",
]

# ── Logging ───────────────────────────────────────────────────────────

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ── Regexes ───────────────────────────────────────────────────────────

# Doc-type detection from doc name
RE_NOTULEN = re.compile(r"notulen|verslag", re.IGNORECASE)
RE_MOTIE = re.compile(r"motie", re.IGNORECASE)
RE_AMENDEMENT = re.compile(r"amendement", re.IGNORECASE)

# Vote outcome keywords (only valid near motie/amendement/voorstel)
VOTE_KEYWORDS = [
    ("aangenomen", "aangenomen"),
    ("verworpen", "verworpen"),
    ("ingetrokken", "ingetrokken"),
    ("aangehouden", "aangehouden"),
]
RE_VOTE_CONTEXT = re.compile(r"motie|amendement|voorstel", re.IGNORECASE)

# Vote counts: "N (stemmen) voor ... M (stemmen) tegen"
RE_VOTE_COUNTS = re.compile(
    r"(\d+)\s+(?:stemmen?\s+)?voor.*?(\d+)\s+(?:stemmen?\s+)?tegen",
    re.IGNORECASE | re.DOTALL,
)
# "met N stemmen voor en M tegen"
RE_VOTE_COUNTS_ALT = re.compile(
    r"met\s+(\d+)\s+stemmen?\s+voor\s+en\s+(\d+)\s+(?:stemmen?\s+)?tegen",
    re.IGNORECASE,
)
# "met algemene stemmen" — unanimous
RE_UNANIMOUS = re.compile(r"met\s+algemene\s+stemmen", re.IGNORECASE)

# Motion/amendment number: "motie nr. M2023-042" etc.
RE_MOTION_NUMBER = re.compile(
    r"(?:motie|amendement)\s+(?:nr\.?\s*)?([A-Z]?\d{4}[-/]\d{2,4})",
    re.IGNORECASE,
)

# Indiener extraction patterns
RE_INDIENERS = [
    re.compile(r"ingediend\s+door[:\s]+(.+?)(?:\.\s|\n|$)", re.IGNORECASE),
    re.compile(r"ondertekend\s+door[:\s]+(.+?)(?:\.\s|\n|$)", re.IGNORECASE),
    re.compile(r"mede.?ondertekend\s+door[:\s]+(.+?)(?:\.\s|\n|$)", re.IGNORECASE),
]


# ── Gazetteer loading ────────────────────────────────────────────────

def load_gazetteer() -> dict:
    """
    Load domain gazetteer and build lookup structures.

    Returns dict with:
        "by_lower": {lowercase_form: canonical_form}  — for exact token / n-gram matching
        "persons_surnames": {lowercase_surname: canonical_full_name}
    """
    with open(GAZETTEER_PATH) as f:
        raw = json.load(f)

    by_lower: dict[str, str] = {}  # lowercase → canonical

    for list_key in GAZETTEER_LISTS:
        entries = raw.get(list_key, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not entry or not isinstance(entry, str):
                continue
            lower = entry.lower().strip()
            # Skip very short tokens (single chars, "de", etc.) that cause false positives
            if len(lower) < 3:
                continue
            # Skip entries that are just party names — party_utils handles those
            if lower in PARTY_ALIASES:
                continue
            by_lower[lower] = entry  # canonical form preserves original casing

    # Persons: surname → full canonical name (for indiener normalisation)
    persons_surnames: dict[str, str] = {}
    persons_data = raw.get("persons", {})
    if isinstance(persons_data, dict):
        for surname, info in persons_data.items():
            full_name = info.get("full_name", surname) if isinstance(info, dict) else surname
            persons_surnames[surname.lower().strip()] = full_name

    # Also index _council_member_surnames_ref as person surnames
    for surname in raw.get("_council_member_surnames_ref", []):
        if isinstance(surname, str) and surname.strip():
            lower_s = surname.lower().strip()
            if lower_s not in persons_surnames:
                persons_surnames[lower_s] = surname

    log.info(f"Gazetteer loaded: {len(by_lower)} entity forms, {len(persons_surnames)} person surnames")

    return {"by_lower": by_lower, "persons_surnames": persons_surnames}


# ── Politician registry loading ──────────────────────────────────────

def load_politician_registry(conn) -> dict[str, str]:
    """
    Load surname → canonical_name mapping from politician_registry table.
    Used for normalising indiener names.
    """
    mapping: dict[str, str] = {}
    try:
        cur = conn.cursor()
        cur.execute("SELECT surname, canonical_name FROM politician_registry")
        for row in cur.fetchall():
            surname, canonical = row
            if surname and canonical:
                mapping[surname.lower().strip()] = canonical
        cur.close()
        log.info(f"Politician registry loaded: {len(mapping)} entries")
    except Exception as e:
        log.warning(f"Could not load politician_registry (table may not exist): {e}")
        conn.rollback()
    return mapping


# ── Entity extraction from doc title ─────────────────────────────────

def extract_key_entities(doc_title: str, gazetteer: dict) -> list[str]:
    """
    Match doc title tokens and n-grams against domain gazetteer.
    Returns deduplicated list of canonical entity names.
    """
    if not doc_title:
        return []

    by_lower = gazetteer["by_lower"]
    found: list[str] = []
    seen: set[str] = set()

    # Tokenize
    words = doc_title.split()

    # Try 3-grams, 2-grams, then 1-grams (longest match first)
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            ngram = " ".join(words[i:i + n])
            lower_ngram = ngram.lower().strip()
            if lower_ngram in by_lower:
                canonical = by_lower[lower_ngram]
                if canonical not in seen:
                    seen.add(canonical)
                    found.append(canonical)

    return found


# ── Section topic heuristic ──────────────────────────────────────────

def extract_section_topic(doc_title: str, meeting_name: str | None) -> str | None:
    """
    Build section_topic from committee context + truncated doc title.
    Format: "{committee}: {first 10 words of doc title}"
    """
    if not doc_title:
        return None

    # Truncate doc title to first 10 words
    words = doc_title.split()
    truncated = " ".join(words[:10])

    # Try to extract committee from meeting name
    committee = None
    if meeting_name:
        # Meeting names often start with committee: "Commissie BWB - 2024-01-15"
        # or just use the meeting name itself as context
        committee = meeting_name.split(" - ")[0].strip() if " - " in meeting_name else meeting_name.strip()
        # Truncate committee to something reasonable
        committee = committee[:60]

    if committee:
        return f"{committee}: {truncated}"
    return truncated


# ── Vote extraction ──────────────────────────────────────────────────

def extract_vote_outcome(content: str) -> str | None:
    """
    Detect vote outcome from chunk content.
    Only fires when a vote keyword appears within 200 chars of
    'motie', 'amendement', or 'voorstel'.
    """
    if not content:
        return None

    lower = content.lower()

    for keyword, outcome in VOTE_KEYWORDS:
        pos = lower.find(keyword)
        if pos == -1:
            continue
        # Check proximity: is there a context word within 200 chars?
        window_start = max(0, pos - 200)
        window_end = min(len(lower), pos + len(keyword) + 200)
        window = lower[window_start:window_end]
        if RE_VOTE_CONTEXT.search(window):
            return outcome

    return None


def extract_vote_counts(content: str) -> dict | None:
    """
    Extract vote tallies {"voor": N, "tegen": M} from chunk content.
    """
    if not content:
        return None

    # Unanimous
    if RE_UNANIMOUS.search(content):
        return {"voor": 45, "tegen": 0}

    # Standard pattern
    m = RE_VOTE_COUNTS_ALT.search(content)
    if m:
        return {"voor": int(m.group(1)), "tegen": int(m.group(2))}

    m = RE_VOTE_COUNTS.search(content)
    if m:
        return {"voor": int(m.group(1)), "tegen": int(m.group(2))}

    return None


# ── Indiener extraction ──────────────────────────────────────────────

def _split_names(raw: str) -> list[str]:
    """Split a raw names string on commas and 'en', strip whitespace."""
    # Replace " en " with comma for uniform splitting
    cleaned = re.sub(r"\s+en\s+", ", ", raw)
    parts = [p.strip().rstrip(".") for p in cleaned.split(",")]
    # Remove empty strings and very short fragments
    return [p for p in parts if len(p) > 1]


def _normalize_name(name: str, politician_map: dict[str, str], persons_surnames: dict[str, str]) -> str:
    """
    Try to normalize a raw name against politician registry and gazetteer persons.
    Returns best canonical form, or the original cleaned name.
    """
    # Strip common prefixes
    cleaned = re.sub(
        r"^(?:de heer|mevrouw|wethouder|raadslid|burgemeester)\s+",
        "", name, flags=re.IGNORECASE,
    ).strip()

    if not cleaned:
        return name.strip()

    # Strip party in parentheses: "Buijt (VVD)" → "Buijt"
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", cleaned).strip()

    lower = cleaned.lower()

    # Exact match on surname in politician registry
    if lower in politician_map:
        return politician_map[lower]

    # Try last word as surname (for "V. Buijt" → "Buijt")
    parts = cleaned.split()
    if len(parts) > 1:
        last = parts[-1].lower()
        if last in politician_map:
            return politician_map[last]
        if last in persons_surnames:
            return persons_surnames[last]

    # Full name in persons_surnames
    if lower in persons_surnames:
        return persons_surnames[lower]

    return cleaned


def extract_indieners(
    content: str,
    politician_map: dict[str, str],
    persons_surnames: dict[str, str],
) -> list[str]:
    """
    Extract submitter (indiener) names from motie/amendement chunk text.
    """
    if not content:
        return []

    raw_names: list[str] = []

    # Try regex patterns
    for pattern in RE_INDIENERS:
        for m in pattern.finditer(content):
            raw_names.extend(_split_names(m.group(1)))

    # Also look for a signature block at the end of the text:
    # last 500 chars, lines that look like names (possibly with party)
    tail = content[-500:] if len(content) > 500 else content
    tail_lines = tail.strip().split("\n")
    # Walk backwards from end, collecting name-like lines
    for line in reversed(tail_lines):
        line = line.strip()
        if not line:
            continue
        # Stop if we hit a sentence (contains verbs / long text)
        if len(line) > 80:
            break
        # A name line: short, may have party in parens
        # Must contain at least one capital letter
        if re.match(r"^[A-Z]", line) and len(line) < 60:
            # Strip party annotation
            name_part = re.sub(r"\s*\([^)]*\)\s*$", "", line).strip()
            if name_part and len(name_part) > 1:
                raw_names.extend(_split_names(name_part))
        else:
            break  # No longer in a signature block

    if not raw_names:
        return []

    # Normalize and deduplicate
    seen: set[str] = set()
    result: list[str] = []
    for raw in raw_names:
        normalized = _normalize_name(raw, politician_map, persons_surnames)
        key = normalized.lower()
        if key not in seen and len(normalized) > 1:
            seen.add(key)
            result.append(normalized)

    return result


# ── Motion number extraction ─────────────────────────────────────────

def extract_motion_number(content: str) -> str | None:
    """Extract a motion/amendment reference number from chunk text."""
    if not content:
        return None
    m = RE_MOTION_NUMBER.search(content)
    return m.group(1) if m else None


# ── Tier 2 extraction orchestrator ───────────────────────────────────

def extract_tier2(
    row: dict,
    gazetteer: dict,
    politician_map: dict[str, str],
) -> dict:
    """
    Run all Tier 2 rule-based extractions on a single chunk row.

    Returns dict ready for batch UPDATE with keys:
        chunk_id, section_topic, key_entities, vote_outcome,
        vote_counts, indieners, motion_number
    """
    chunk_id = row["id"]
    content = row.get("content") or ""
    doc_name = row.get("doc_name") or ""
    meeting_name = row.get("meeting_name")

    is_notulen = bool(RE_NOTULEN.search(doc_name))
    is_motie = bool(RE_MOTIE.search(doc_name))
    is_amendement = bool(RE_AMENDEMENT.search(doc_name))

    # 1. key_entities — from doc title, not chunk text
    key_entities = extract_key_entities(doc_name, gazetteer)

    # 2. section_topic
    section_topic = extract_section_topic(doc_name, meeting_name)

    # 3. vote_outcome — only for notulen/verslag
    vote_outcome = None
    if is_notulen:
        vote_outcome = extract_vote_outcome(content)

    # 4. vote_counts — only when we found a vote outcome
    vote_counts = None
    if vote_outcome:
        vote_counts = extract_vote_counts(content)

    # 5. indieners — only for motie/amendement docs
    indieners = []
    if is_motie or is_amendement:
        indieners = extract_indieners(
            content,
            politician_map,
            gazetteer["persons_surnames"],
        )

    # 6. motion_number — from any chunk that mentions one
    motion_number = extract_motion_number(content)

    return {
        "chunk_id": chunk_id,
        "section_topic": section_topic,
        "key_entities": key_entities or None,
        "vote_outcome": vote_outcome,
        "vote_counts": Json(vote_counts) if vote_counts else None,
        "indieners": indieners or None,
        "motion_number": motion_number,
    }


# ── Batch write ──────────────────────────────────────────────────────

def write_batch(cur, batch: list[dict]):
    """Write a batch of enrichment results to PostgreSQL."""
    cur.executemany("""
        UPDATE document_chunks SET
            section_topic  = %(section_topic)s,
            key_entities   = %(key_entities)s,
            vote_outcome   = %(vote_outcome)s,
            vote_counts    = %(vote_counts)s,
            indieners      = %(indieners)s,
            motion_number  = %(motion_number)s
        WHERE id = %(chunk_id)s
    """, batch)


# ── Checkpoint ────────────────────────────────────────────────────────

def load_checkpoint() -> dict:
    """Load checkpoint if it exists."""
    if CHECKPOINT_PATH.exists():
        try:
            with open(CHECKPOINT_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_chunk_id": 0, "processed": 0}


def save_checkpoint(last_chunk_id: int, processed: int):
    """Save checkpoint for resumption."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump({
            "last_chunk_id": last_chunk_id,
            "processed": processed,
            "timestamp": datetime.now().isoformat(),
        }, f)


# ── Stats tracking ───────────────────────────────────────────────────

class Stats:
    def __init__(self):
        self.processed = 0
        self.with_entities = 0
        self.with_vote = 0
        self.with_counts = 0
        self.with_indieners = 0
        self.with_motion_nr = 0
        self.start_time = time.time()

    def update(self, result: dict):
        self.processed += 1
        if result["key_entities"]:
            self.with_entities += 1
        if result["vote_outcome"]:
            self.with_vote += 1
        if result["vote_counts"]:
            self.with_counts += 1
        if result["indieners"]:
            self.with_indieners += 1
        if result["motion_number"]:
            self.with_motion_nr += 1

    def report(self) -> str:
        elapsed = time.time() - self.start_time
        rate = self.processed / elapsed if elapsed > 0 else 0
        return (
            f"processed={self.processed:,} | "
            f"entities={self.with_entities:,} | "
            f"votes={self.with_vote:,} | "
            f"counts={self.with_counts:,} | "
            f"indieners={self.with_indieners:,} | "
            f"motion_nr={self.with_motion_nr:,} | "
            f"{rate:,.0f} rows/s"
        )


# ── Main enrichment loop ─────────────────────────────────────────────

def run_tier2(
    limit: int | None = None,
    batch_size: int = 500,
    resume: bool = False,
):
    """Main Tier 2 enrichment loop."""

    log.info("=" * 64)
    log.info("  TIER 2 RULE-BASED METADATA EXTRACTION")
    log.info(f"  Batch size: {batch_size}")
    log.info(f"  Limit: {limit or 'unlimited'}")
    log.info(f"  Resume: {resume}")
    log.info("=" * 64)

    # ── Load gazetteer ────────────────────────────────────────────────
    gazetteer = load_gazetteer()

    # ── Connect (read + write on separate connections) ────────────────
    read_conn = psycopg2.connect(DB_URL)
    write_conn = psycopg2.connect(DB_URL)
    write_cur = write_conn.cursor()

    # ── Load politician registry for indiener normalisation ───────────
    politician_map = load_politician_registry(read_conn)

    # ── Checkpoint ────────────────────────────────────────────────────
    checkpoint = load_checkpoint() if resume else {"last_chunk_id": 0, "processed": 0}
    start_id = checkpoint["last_chunk_id"]
    already_processed = checkpoint["processed"] if resume else 0

    if resume and start_id > 0:
        log.info(f"Resuming from chunk id > {start_id} ({already_processed:,} already done)")

    # ── Get total count for progress bar ──────────────────────────────
    count_cur = read_conn.cursor()
    if start_id > 0:
        count_cur.execute("SELECT COUNT(*) FROM document_chunks WHERE id > %s", (start_id,))
    else:
        count_cur.execute("SELECT COUNT(*) FROM document_chunks")
    total = count_cur.fetchone()[0]
    count_cur.close()

    if limit:
        total = min(total, limit)

    log.info(f"Total chunks to process: {total:,}")

    # ── Server-side cursor for memory-efficient reading ───────────────
    read_cur = read_conn.cursor("tier2_reader", cursor_factory=RealDictCursor)
    read_cur.itersize = 2000

    query = """
        SELECT dc.id, dc.content, dc.document_id,
               d.name  AS doc_name,
               m.name  AS meeting_name,
               m.start_date
        FROM document_chunks dc
        JOIN documents d ON dc.document_id = d.id
        LEFT JOIN meetings m ON d.meeting_id = m.id
        WHERE dc.id > %s
        ORDER BY dc.id
    """
    read_cur.execute(query, (start_id,))

    # ── Process ───────────────────────────────────────────────────────
    stats = Stats()
    batch: list[dict] = []
    last_chunk_id = start_id
    rows_seen = 0

    pbar = tqdm(total=total, initial=0, desc="Tier 2 extraction", unit="chunk")

    try:
        for row in read_cur:
            if limit and rows_seen >= limit:
                break

            result = extract_tier2(row, gazetteer, politician_map)
            batch.append(result)
            stats.update(result)
            last_chunk_id = row["id"]
            rows_seen += 1
            pbar.update(1)

            if len(batch) >= batch_size:
                write_batch(write_cur, batch)
                write_conn.commit()
                batch = []

            # Progress log every 10,000 chunks
            if rows_seen % 10_000 == 0:
                log.info(f"[{rows_seen:>10,}] {stats.report()}")

            # Checkpoint every 50,000 chunks
            if rows_seen % 50_000 == 0:
                save_checkpoint(last_chunk_id, already_processed + rows_seen)

        # Flush remaining batch
        if batch:
            write_batch(write_cur, batch)
            write_conn.commit()

        # Final checkpoint
        save_checkpoint(last_chunk_id, already_processed + rows_seen)

    except KeyboardInterrupt:
        log.warning("Interrupted! Flushing current batch and saving checkpoint...")
        if batch:
            write_batch(write_cur, batch)
            write_conn.commit()
        save_checkpoint(last_chunk_id, already_processed + rows_seen)
        log.info(f"Checkpoint saved at chunk id {last_chunk_id}")
    except Exception:
        log.exception("Fatal error during extraction")
        # Still try to save checkpoint
        save_checkpoint(last_chunk_id, already_processed + rows_seen)
        raise
    finally:
        pbar.close()
        read_cur.close()
        read_conn.close()
        write_cur.close()
        write_conn.close()

    # ── Summary ───────────────────────────────────────────────────────
    log.info("=" * 64)
    log.info("  TIER 2 EXTRACTION COMPLETE")
    log.info(f"  {stats.report()}")
    log.info(f"  Last chunk id: {last_chunk_id}")
    log.info("=" * 64)


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Tier 2 rule-based metadata extraction for document_chunks"
    )
    parser.add_argument(
        "--tier2-only", action="store_true", default=True,
        help="Run only Tier 2 rules (default, and currently the only mode)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process only first N chunks (for testing)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from last checkpoint",
    )
    parser.add_argument(
        "--batch-size", type=int, default=500,
        help="Batch size for UPDATE writes (default 500)",
    )
    args = parser.parse_args()

    run_tier2(
        limit=args.limit,
        batch_size=args.batch_size,
        resume=args.resume,
    )


if __name__ == "__main__":
    main()
