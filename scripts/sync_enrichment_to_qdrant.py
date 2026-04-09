"""
Qdrant Enrichment Sync Script — Push PostgreSQL metadata to Qdrant payloads

Reads newly populated enrichment fields from PostgreSQL document_chunks and
pushes them to Qdrant payloads using set_payload (additive, never touches vectors).

Fields synced:
  - section_topic   (str)
  - key_entities    (list / jsonb)
  - vote_outcome    (str)
  - vote_counts     (jsonb)
  - indieners       (list / jsonb)
  - motion_number   (str)

Checkpoint-resumable. RAM-guarded. Batched at 500 points by default.

Usage:
    python scripts/sync_enrichment_to_qdrant.py                    # Full run
    python scripts/sync_enrichment_to_qdrant.py --limit 100        # Smoke test
    python scripts/sync_enrichment_to_qdrant.py --resume            # Resume from checkpoint
    python scripts/sync_enrichment_to_qdrant.py --batch-size 300    # Smaller batches
"""

import os
import sys
import gc
import json
import time
import logging
import argparse
import subprocess
from pathlib import Path
from collections import defaultdict

import psycopg2
from psycopg2.extras import RealDictCursor
from qdrant_client import QdrantClient
from dotenv import load_dotenv
from tqdm import tqdm

# ── Project root & env ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

# ── Configuration ─────────────────────────────────────────────────────
DB_URL = os.getenv("DB_URL") or "postgresql://postgres:postgres@localhost:5432/neodemos"
QDRANT_URL = os.getenv("QDRANT_LOCAL_URL") or "http://localhost:6333"
COLLECTION_NAME = "notulen_chunks"

CHECKPOINT_DIR = PROJECT_ROOT / "data" / "pipeline_state"
CHECKPOINT_PATH = CHECKPOINT_DIR / "qdrant_sync_checkpoint.json"
LOG_PATH = PROJECT_ROOT / "logs" / "sync_enrichment_to_qdrant.log"

# RAM guard (64 GB M5 Pro)
RAM_THRESHOLD_GB = 61.0

# Fields to sync from PostgreSQL → Qdrant
SYNC_FIELDS = [
    "section_topic",
    "key_entities",
    "vote_outcome",
    "vote_counts",
    "indieners",
    "motion_number",
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


# ── Checkpoint ────────────────────────────────────────────────────────

def load_checkpoint() -> dict:
    """Load checkpoint if it exists."""
    if CHECKPOINT_PATH.exists():
        try:
            with open(CHECKPOINT_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_id": None, "processed": 0, "synced": 0}


def save_checkpoint(last_id, processed: int, synced: int):
    """Save checkpoint for resume."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump({
            "last_id": last_id,
            "processed": processed,
            "synced": synced,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }, f)


# ── Helpers ───────────────────────────────────────────────────────────

def _build_payload(row: dict) -> dict:
    """Build a Qdrant payload dict from a PostgreSQL row, skipping null fields."""
    payload = {}
    for field in SYNC_FIELDS:
        val = row.get(field)
        if val is not None:
            # jsonb columns arrive as Python dicts/lists from psycopg2;
            # str columns arrive as str. Both are Qdrant-safe.
            payload[field] = val
    return payload


def _reconnect_qdrant() -> QdrantClient:
    """Create a fresh Qdrant connection."""
    return QdrantClient(url=QDRANT_URL, timeout=60)


# ── Main sync loop ───────────────────────────────────────────────────

def sync(limit: int = None, batch_size: int = 500, resume: bool = False):
    """
    Main sync loop. Reads enriched rows from PostgreSQL in batches
    (server-side cursor, ordered by id) and pushes payloads to Qdrant.
    """
    log.info("=" * 60)
    log.info("  QDRANT ENRICHMENT SYNC — PostgreSQL → Qdrant payloads")
    log.info(f"  Collection: {COLLECTION_NAME}")
    log.info(f"  Batch size: {batch_size}")
    log.info(f"  Limit: {limit or 'unlimited'}")
    log.info(f"  Resume: {resume}")
    log.info(f"  Fields: {', '.join(SYNC_FIELDS)}")
    log.info("=" * 60)

    # Connect
    qdrant = _reconnect_qdrant()
    conn = psycopg2.connect(DB_URL)
    log.info("Connected to Qdrant and PostgreSQL")

    # Verify collection exists
    info = qdrant.get_collection(COLLECTION_NAME)
    log.info(f"Qdrant collection '{COLLECTION_NAME}': {info.points_count:,} points")

    # Checkpoint
    checkpoint = load_checkpoint() if resume else {"last_id": None, "processed": 0, "synced": 0}
    last_id = checkpoint["last_id"]
    processed = checkpoint["processed"]
    synced = checkpoint["synced"]
    if last_id and resume:
        log.info(f"Resuming from id > {last_id} (processed={processed}, synced={synced})")

    # Count total eligible rows for the progress bar
    count_cur = conn.cursor()
    count_sql = """
        SELECT COUNT(*) FROM document_chunks
        WHERE section_topic IS NOT NULL
           OR key_entities IS NOT NULL
           OR vote_outcome IS NOT NULL
    """
    if last_id and resume:
        count_sql += f" AND id > {int(last_id)}"
    count_cur.execute(count_sql)
    total_eligible = count_cur.fetchone()[0]
    count_cur.close()

    if limit:
        total_eligible = min(total_eligible, limit)
    log.info(f"Eligible rows to sync: {total_eligible:,}")

    if total_eligible == 0:
        log.info("Nothing to sync. All enrichment fields are NULL or already processed.")
        conn.close()
        return

    # Use a named server-side cursor for memory-efficient streaming
    read_cur = conn.cursor("sync_enrichment_cursor", cursor_factory=RealDictCursor)

    resume_filter = f"AND dc.id > {int(last_id)}" if (last_id and resume) else ""

    read_cur.execute(f"""
        SELECT id, section_topic, key_entities, vote_outcome,
               vote_counts, indieners, motion_number
        FROM document_chunks dc
        WHERE (section_topic IS NOT NULL
            OR key_entities IS NOT NULL
            OR vote_outcome IS NOT NULL)
        {resume_filter}
        ORDER BY id
    """)

    pbar = tqdm(total=total_eligible, initial=0, desc="Syncing", unit="rows")
    batch_processed = 0

    while True:
        # RAM guard
        ram_used = get_system_ram_used_gb()
        if ram_used > RAM_THRESHOLD_GB:
            log.warning(f"RAM guard: {ram_used:.1f}GB used. Pausing 10s...")
            gc.collect()
            time.sleep(10)
            continue

        # Fetch a batch from the server-side cursor
        rows = read_cur.fetchmany(batch_size)
        if not rows:
            break

        # Group by identical payload to reduce Qdrant HTTP calls
        payload_groups = defaultdict(list)  # json_key -> [point_ids]
        payload_map = {}                    # json_key -> payload dict

        for row in rows:
            chunk_id = row["id"]
            payload = _build_payload(row)
            if not payload:
                # Row matched the WHERE but all SYNC_FIELDS happen to be null
                # (e.g. only vote_counts is set but we checked section_topic/key_entities/vote_outcome)
                processed += 1
                pbar.update(1)
                last_id = chunk_id
                continue

            key = json.dumps(payload, sort_keys=True, default=str)
            payload_groups[key].append(chunk_id)
            payload_map[key] = payload

            processed += 1
            last_id = chunk_id
            pbar.update(1)

            if limit and processed >= limit:
                break

        # Batch write to Qdrant — grouped by identical payload
        for key, point_ids in payload_groups.items():
            payload = payload_map[key]
            for attempt in range(5):
                try:
                    qdrant.set_payload(
                        collection_name=COLLECTION_NAME,
                        payload=payload,
                        points=point_ids,
                        wait=False,  # async for speed
                    )
                    synced += len(point_ids)
                    break
                except Exception as e:
                    if attempt == 4:
                        log.error(
                            f"Failed to set payload for {len(point_ids)} points "
                            f"after 5 attempts: {e}"
                        )
                    else:
                        wait_s = 2 ** (attempt + 1)  # 2, 4, 8, 16s
                        log.warning(f"set_payload retry {attempt + 1} (wait {wait_s}s): {e}")
                        time.sleep(wait_s)
                        if "Connection refused" in str(e) or "Connection reset" in str(e):
                            try:
                                qdrant = _reconnect_qdrant()
                                log.info("Reconnected to Qdrant")
                            except Exception:
                                pass

        # Throttle between batches to reduce Qdrant write pressure
        time.sleep(0.2)
        gc.collect()

        # Save checkpoint after each batch
        save_checkpoint(last_id, processed, synced)
        batch_processed += len(rows)

        if limit and processed >= limit:
            break

    pbar.close()
    read_cur.close()
    conn.close()

    # Final checkpoint
    save_checkpoint(last_id, processed, synced)

    # Report
    log.info("=" * 60)
    log.info("  ENRICHMENT SYNC COMPLETE")
    log.info(f"  Processed: {processed:,}")
    log.info(f"  Synced:    {synced:,}")
    log.info(f"  Last ID:   {last_id}")
    log.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sync enrichment metadata from PostgreSQL to Qdrant payloads"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process at most N chunks (for smoke testing)"
    )
    parser.add_argument(
        "--batch-size", type=int, default=500,
        help="Number of rows per PostgreSQL fetch batch (default: 500)"
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from last checkpoint"
    )
    args = parser.parse_args()

    sync(limit=args.limit, batch_size=args.batch_size, resume=args.resume)
