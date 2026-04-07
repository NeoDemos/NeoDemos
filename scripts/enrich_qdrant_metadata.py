"""
Qdrant Metadata Enrichment Script — Phase 1 of v3 Architecture

Scrolls all points in the notulen_chunks collection and adds:
  - party:      Primary party extracted from chunk text (regex)
  - parties:    All parties mentioned in chunk (list)
  - speaker:    First speaker name found in chunk
  - committee:  Resolved committee name (from meetings.name)
  - meeting_id: From documents table
  - doc_type:   Classified from document name

Safe to run: uses set_payload (additive, never touches vectors).
Checkpoint-resumable. RAM-guarded.

Usage:
    python scripts/enrich_qdrant_metadata.py                    # Full run
    python scripts/enrich_qdrant_metadata.py --limit 100        # Smoke test
    python scripts/enrich_qdrant_metadata.py --resume            # Resume from checkpoint
    python scripts/enrich_qdrant_metadata.py --batch-size 300    # Smaller batches
"""

import os
import sys
import re
import gc
import json
import time
import logging
import argparse
import subprocess
from pathlib import Path
from collections import Counter

import psycopg2
from psycopg2.extras import RealDictCursor
from qdrant_client import QdrantClient
from dotenv import load_dotenv
from tqdm import tqdm

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from services.party_utils import (
    extract_parties_from_text,
    extract_speakers_from_text,
    primary_party,
)

# ── Configuration ──────────────────────────────────────────────────────

DB_URL = os.getenv("DB_URL") or "postgresql://postgres:postgres@localhost:5432/neodemos"
QDRANT_URL = os.getenv("QDRANT_LOCAL_URL") or "http://localhost:6333"
COLLECTION_NAME = "notulen_chunks"

CHECKPOINT_DIR = PROJECT_ROOT / "data" / "pipeline_state"
CHECKPOINT_PATH = CHECKPOINT_DIR / "enrich_metadata_checkpoint.json"
LOG_PATH = PROJECT_ROOT / "logs" / "enrich_metadata.log"

# RAM guard (Rule #2 for 64GB M5 Pro)
RAM_THRESHOLD_GB = 61.0

# ── Logging ────────────────────────────────────────────────────────────

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

# ── Doc type classification (from compute_embeddings.py pattern) ──────

_DOC_TYPE_PATTERNS = [
    (r"motie", "motie"),
    (r"amendement", "amendement"),
    (r"raadsvoorstel", "raadsvoorstel"),
    (r"notulen", "notulen"),
    (r"verslag", "verslag"),
    (r"besluitenlijst", "besluitenlijst"),
    (r"begroting|jaarrekening|financ", "financieel"),
    (r"brief", "brief"),
    (r"annotati", "annotatie"),
    (r"transcript", "virtual_notulen"),  # Committee meeting transcripts — never overwrite with "overig"
]


def classify_doc_type(doc_name: str) -> str:
    """Classify document type from its name."""
    if not doc_name:
        return "overig"
    lower = doc_name.lower()
    for pattern, dtype in _DOC_TYPE_PATTERNS:
        if re.search(pattern, lower):
            return dtype
    return "overig"


# ── RAM guard ─────────────────────────────────────────────────────────

def get_system_ram_used_gb() -> float:
    """Get system RAM usage on macOS via vm_stat."""
    try:
        output = subprocess.check_output(["vm_stat"]).decode()
        stats = {}
        for line in output.split("\n"):
            if ":" in line:
                parts = line.split(":")
                key = parts[0].strip()
                val = parts[1].strip().replace(".", "")
                stats[key] = int(val)
        pagesize = 16384  # ARM macOS
        active = stats.get("Pages active", 0)
        wired = stats.get("Pages wired down", 0)
        compressed = stats.get("Pages occupied by compressor", 0)
        return (active + wired + compressed) * pagesize / (1024 ** 3)
    except Exception:
        return 0.0


# ── Committee mapping ─────────────────────────────────────────────────

def build_committee_map(conn) -> dict:
    """Build committee ID → readable name mapping from meetings table."""
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT committee, name
        FROM meetings
        WHERE committee IS NOT NULL AND name IS NOT NULL
        ORDER BY committee
    """)
    mapping = {}
    for row in cur.fetchall():
        cid = row[0]
        name = row[1]
        # Skip generic entries like "Agendapunt"
        if name.lower().startswith("agendapunt"):
            continue
        # Strip "zzz " prefix (used for retired committees)
        clean_name = re.sub(r"^zzz\s+", "", name)
        # Strip year range suffix for cleaner labels
        clean_name = re.sub(r"\s*\(\d{4}[-–]\s*\d{4}\)\s*$", "", clean_name)
        # Truncate at 80 chars
        mapping[str(cid)] = clean_name[:80]
    cur.close()
    log.info(f"Built committee map: {len(mapping)} entries")
    return mapping


# ── Checkpoint ────────────────────────────────────────────────────────

def load_checkpoint() -> dict:
    """Load checkpoint if exists."""
    if CHECKPOINT_PATH.exists():
        try:
            with open(CHECKPOINT_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {"next_page_offset": None, "processed": 0, "enriched": 0}


def save_checkpoint(offset, processed: int, enriched: int):
    """Save checkpoint for resume."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump({
            "next_page_offset": offset,
            "processed": processed,
            "enriched": enriched,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }, f)


# ── Main enrichment loop ─────────────────────────────────────────────

def enrich(limit: int = None, batch_size: int = 500, resume: bool = False):
    """
    Main enrichment loop. Scrolls Qdrant, enriches payloads.
    """
    log.info("=" * 60)
    log.info("  QDRANT METADATA ENRICHMENT — v3 Architecture Phase 1")
    log.info(f"  Collection: {COLLECTION_NAME}")
    log.info(f"  Batch size: {batch_size}")
    log.info(f"  Limit: {limit or 'unlimited'}")
    log.info(f"  Resume: {resume}")
    log.info("=" * 60)

    # Connect
    qdrant = QdrantClient(url=QDRANT_URL, timeout=60)
    conn = psycopg2.connect(DB_URL)
    log.info("Connected to Qdrant and PostgreSQL")

    # Pre-load committee mapping
    committee_map = build_committee_map(conn)

    # Collection info
    info = qdrant.get_collection(COLLECTION_NAME)
    total_points = info.points_count
    log.info(f"Total points in collection: {total_points}")

    # Checkpoint
    checkpoint = load_checkpoint() if resume else {"next_page_offset": None, "processed": 0, "enriched": 0}
    offset = checkpoint["next_page_offset"]
    processed = checkpoint["processed"]
    enriched = checkpoint["enriched"]
    if offset:
        log.info(f"Resuming from offset {offset} (processed={processed}, enriched={enriched})")

    pbar = tqdm(
        total=limit or total_points,
        initial=processed,
        desc="Enriching",
        unit="pts",
    )

    stats = Counter()  # Track what we enriched

    while True:
        # RAM guard
        ram_used = get_system_ram_used_gb()
        if ram_used > RAM_THRESHOLD_GB:
            log.warning(f"RAM guard: {ram_used:.1f}GB used. Pausing 10s for cleanup...")
            gc.collect()
            time.sleep(10)
            continue

        # Scroll batch from Qdrant (no vectors — SSD-only read)
        res = None
        for scroll_attempt in range(5):
            try:
                res, next_offset = qdrant.scroll(
                    collection_name=COLLECTION_NAME,
                    limit=batch_size,
                    with_payload=True,
                    offset=offset,
                    with_vectors=False,
                )
                break
            except Exception as e:
                wait_s = 5 * (scroll_attempt + 1)
                log.warning(f"Scroll failed (attempt {scroll_attempt+1}), waiting {wait_s}s: {e}")
                time.sleep(wait_s)
                try:
                    qdrant = QdrantClient(url=QDRANT_URL, timeout=60)
                    log.info("Reconnected to Qdrant after scroll failure")
                except Exception:
                    pass

        if not res:
            break

        # Collect document_ids for batch PostgreSQL fetch
        doc_ids = set()
        for p in res:
            did = p.payload.get("document_id")
            if did:
                doc_ids.add(str(did))

        # Batch fetch document + meeting metadata from PostgreSQL
        doc_metadata = {}
        if doc_ids:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT d.id::text as document_id,
                       d.meeting_id::text as meeting_id,
                       d.name as doc_name,
                       m.committee as committee_id
                FROM documents d
                LEFT JOIN meetings m ON d.meeting_id = m.id
                WHERE d.id::text IN %s
            """, (tuple(doc_ids),))
            for row in cur.fetchall():
                doc_metadata[row["document_id"]] = row
            cur.close()

        # Process each point and collect batched updates
        # Group points by their new payload to batch set_payload calls
        batch_updates = []  # List of (point_id, new_payload)

        for p in res:
            content = p.payload.get("content", "")
            doc_id = str(p.payload.get("document_id", ""))

            new_payload = {}

            # 1. Party / speaker extraction from text
            parties = extract_parties_from_text(content)
            if parties:
                new_payload["party"] = primary_party(parties)
                new_payload["parties"] = parties
                stats["party_found"] += 1

            speakers = extract_speakers_from_text(content)
            if speakers:
                new_payload["speaker"] = speakers[0]["speaker"]
                stats["speaker_found"] += 1

            # 2. Document metadata from PostgreSQL
            meta = doc_metadata.get(doc_id)
            if meta:
                # Meeting ID
                if meta.get("meeting_id"):
                    new_payload["meeting_id"] = meta["meeting_id"]

                # Committee (resolve ID → name)
                cid = meta.get("committee_id")
                if cid:
                    committee_name = committee_map.get(str(cid))
                    if committee_name:
                        new_payload["committee"] = committee_name
                        stats["committee_found"] += 1

                # Doc type
                doc_name = meta.get("doc_name", "")
                doc_type = classify_doc_type(doc_name)
                new_payload["doc_type"] = doc_type
                stats[f"doctype_{doc_type}"] += 1

            # Only update if we have something new
            if new_payload:
                batch_updates.append((p.id, new_payload))

            processed += 1
            pbar.update(1)

            if limit and processed >= limit:
                break

        # Batch write to Qdrant — group by identical payload JSON to reduce HTTP calls
        from collections import defaultdict
        payload_groups = defaultdict(list)  # json_key → [point_ids]
        payload_map = {}  # json_key → payload dict
        for point_id, payload in batch_updates:
            key = json.dumps(payload, sort_keys=True)
            payload_groups[key].append(point_id)
            payload_map[key] = payload

        for key, point_ids in payload_groups.items():
            payload = payload_map[key]
            for attempt in range(5):
                try:
                    qdrant.set_payload(
                        collection_name=COLLECTION_NAME,
                        payload=payload,
                        points=point_ids,
                        wait=True,
                    )
                    enriched += len(point_ids)
                    break
                except Exception as e:
                    if attempt == 4:
                        log.error(f"Failed to set payload for {len(point_ids)} points after 5 attempts: {e}")
                    else:
                        wait_s = 2 ** (attempt + 1)  # 2, 4, 8, 16s
                        log.warning(f"set_payload retry {attempt+1} (wait {wait_s}s): {e}")
                        time.sleep(wait_s)
                        # Reconnect on connection errors
                        if "Connection refused" in str(e) or "Connection reset" in str(e):
                            try:
                                qdrant = QdrantClient(url=QDRANT_URL, timeout=60)
                                log.info("Reconnected to Qdrant")
                            except Exception:
                                pass

        # Throttle between scroll batches to reduce Qdrant write pressure
        time.sleep(0.3)

        # Batch cleanup
        gc.collect()

        # Save checkpoint
        save_checkpoint(next_offset, processed, enriched)

        if limit and processed >= limit:
            break
        if next_offset is None:
            break

        offset = next_offset

    pbar.close()
    conn.close()

    # Final report
    log.info("=" * 60)
    log.info("  ENRICHMENT COMPLETE")
    log.info(f"  Processed: {processed:,}")
    log.info(f"  Enriched:  {enriched:,}")
    log.info(f"  Stats:")
    for key, count in sorted(stats.items()):
        log.info(f"    {key}: {count:,}")
    log.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enrich Qdrant metadata for v3 architecture")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of points to process")
    parser.add_argument("--batch-size", type=int, default=500, help="Qdrant scroll batch size")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    args = parser.parse_args()

    enrich(limit=args.limit, batch_size=args.batch_size, resume=args.resume)
