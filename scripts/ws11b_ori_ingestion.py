"""WS11b — ORI batch ingestion of missing civic documents.

Fetches civic documents from the ORI (Open Raadsinformatie) API for
Rotterdam 2018–2026, then upserts them into the documents table.
Newly inserted docs are processed by document_processor.py (chunking + embedding).

Priority order:
  P0 (retrieval-blocking):
  1. schriftelijke_vraag  — 293 inserted (complete)
  2. initiatiefnotitie    — 0 gap (all already in DB via iBabs)

  P1 (visible gaps, run with --include-p1):
  3. initiatiefvoorstel   — gap TBD (likely small)
  4. raadsvoorstel        — ~473 ORI gap (20%)
  5. toezegging           — ~869 ORI gap (30%)
  6. brief_college        — gap TBD
  7. afdoeningsvoorstel   — gap TBD

Checkpoint:
  data/pipeline_state/ws11b_checkpoint.json
  Resume-safe: skips ORI @ids already in checkpoint or already in DB.

Usage:
    # Dry-run (audit ORI counts, no DB writes):
    python scripts/ws11b_ori_ingestion.py --dry-run

    # Ingest P0 types (schriftelijke_vraag + initiatiefnotitie):
    python scripts/ws11b_ori_ingestion.py

    # Also ingest all P1 types (raadsvoorstel, toezegging, brief_college, etc.):
    python scripts/ws11b_ori_ingestion.py --include-p1

    # Resume after interruption:
    python scripts/ws11b_ori_ingestion.py --resume

    # Limit per run (useful for testing):
    python scripts/ws11b_ori_ingestion.py --limit 100
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import psycopg2
from dotenv import load_dotenv

load_dotenv()

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
CHECKPOINT_DIR = PROJECT_ROOT / "data" / "pipeline_state"
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
CHECKPOINT_PATH = CHECKPOINT_DIR / "ws11b_checkpoint.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "ws11b_ingestion.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# Rate limiting: 1 req/sec conservative (ORI has no documented limit)
REQUEST_DELAY_SEC = 1.0
# Batch commit: upsert every N docs
BATCH_SIZE = 50


# ---------------------------------------------------------------------------
# Ingestion targets
# ---------------------------------------------------------------------------

TARGETS = [
    # --- P0: retrieval-blocking ---
    {
        "doc_classification": "schriftelijke_vraag",
        "label": "Schriftelijke vragen",
        "priority": "P0",
        # Fetch via name-pattern per year (MediaObject)
        "fetch_method": "name_pattern",
        "name_patterns": ["schriftelijke vraag", "schriftelijke vragen", "raadsvraag", "SV "],
        "years": list(range(2018, 2027)),
        # Also fetch via ORI classification (Report type)
        "also_by_classification": "Raadsvragen",
    },
    {
        "doc_classification": "initiatiefnotitie",
        "label": "Initiatiefnotities",
        "priority": "P0",
        "fetch_method": "name_pattern",
        "name_patterns": ["initiatiefnotitie", "initiatiefnoti"],
        "years": list(range(2018, 2027)),
        "also_by_classification": None,
    },
    # --- P1: visible retrieval gaps ---
    {
        "doc_classification": "initiatiefvoorstel",
        "label": "Initiatiefvoorstellen",
        "priority": "P1",
        "fetch_method": "name_pattern",
        "name_patterns": ["initiatiefvoorstel"],
        "years": list(range(2018, 2027)),
        "also_by_classification": None,
    },
    {
        "doc_classification": "raadsvoorstel",
        "label": "Raadsvoorstellen",
        "priority": "P1",
        "fetch_method": "name_pattern",
        "name_patterns": ["raadsvoorstel"],
        "years": list(range(2018, 2027)),
        "also_by_classification": "Raadsvoorstellen",
    },
    {
        "doc_classification": "toezegging",
        "label": "Toezeggingen",
        "priority": "P1",
        "fetch_method": "name_pattern",
        "name_patterns": ["toezegging"],
        "years": list(range(2018, 2027)),
        "also_by_classification": "Toezeggingen",
    },
    {
        "doc_classification": "brief_college",
        "label": "Brieven College",
        "priority": "P1",
        "fetch_method": "name_pattern",
        "name_patterns": ["brief college", "collegebrief", "wethoudersbrief", "brief b&w", "brief b en w"],
        "years": list(range(2018, 2027)),
        "also_by_classification": "Brieven B&W",
    },
    {
        "doc_classification": "afdoeningsvoorstel",
        "label": "Afdoeningsvoorstellen",
        "priority": "P1",
        "fetch_method": "name_pattern",
        "name_patterns": ["afdoeningsvoorstel", "afdoening"],
        "years": list(range(2018, 2027)),
        "also_by_classification": None,
    },
]


# ---------------------------------------------------------------------------
# Checkpoint helpers (same pattern as scripts/ocr_recovery.py)
# ---------------------------------------------------------------------------

def load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        try:
            return json.loads(CHECKPOINT_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("[ws11b] Corrupt checkpoint — starting fresh")
    return {"completed_ids": [], "stats": {}}


def save_checkpoint(completed_ids: set, stats: dict) -> None:
    data = {
        "completed_ids": list(completed_ids),
        "stats": stats,
        "last_saved": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp = str(CHECKPOINT_PATH) + ".tmp"
    Path(tmp).write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(tmp, CHECKPOINT_PATH)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _build_db_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if url:
        return url
    h = os.getenv("DB_HOST", "localhost")
    p = os.getenv("DB_PORT", "5432")
    d = os.getenv("DB_NAME", "neodemos")
    u = os.getenv("DB_USER", "postgres")
    pw = os.getenv("DB_PASSWORD", "")
    return f"postgresql://{u}:{pw}@{h}:{p}/{d}"


def doc_exists_in_db(conn, ori_id: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM documents WHERE id = %s", (ori_id,))
    exists = cur.fetchone() is not None
    cur.close()
    return exists


def upsert_doc(conn, doc_data: dict) -> bool:
    """Insert doc via storage.insert_document() logic inline (avoids import cycle)."""
    from services.storage import StorageService
    storage = StorageService()
    return storage.insert_document(doc_data)


# ---------------------------------------------------------------------------
# ORI fetch helpers
# ---------------------------------------------------------------------------

async def fetch_all_for_target(target: dict, dry_run: bool) -> list[dict]:
    """Fetch all ORI docs for a given target across all years."""
    from services.open_raad import OpenRaadService

    ori = OpenRaadService()
    all_docs: list[dict] = []
    seen_ids: set[str] = set()

    # 1. Fetch by name pattern (per year, paginated)
    for year in target["years"]:
        offset = 0
        while True:
            docs, total = await ori.fetch_docs_by_name_pattern(
                name_patterns=target["name_patterns"],
                year=year,
                from_offset=offset,
            )
            if dry_run and offset == 0:
                logger.info(
                    "[ws11b] DRY-RUN  %-25s year=%d  total_in_ori=%d",
                    target["doc_classification"], year, total,
                )
            for doc in docs:
                oid = doc.get("ori_id", "")
                if oid and oid not in seen_ids:
                    seen_ids.add(oid)
                    doc["_year"] = year
                    all_docs.append(doc)

            offset += len(docs)
            if len(docs) == 0 or offset >= total:
                break
            await asyncio.sleep(REQUEST_DELAY_SEC)

    # 2. Also fetch via ORI classification field (Report type)
    if target.get("also_by_classification"):
        offset = 0
        while True:
            docs, total = await ori.fetch_docs_by_classification(
                classification_value=target["also_by_classification"],
                from_offset=offset,
            )
            for doc in docs:
                oid = doc.get("ori_id", "")
                if oid and oid not in seen_ids:
                    seen_ids.add(oid)
                    all_docs.append(doc)

            offset += len(docs)
            if len(docs) == 0 or offset >= total:
                break
            await asyncio.sleep(REQUEST_DELAY_SEC)

    return all_docs


# ---------------------------------------------------------------------------
# Main ingestion loop
# ---------------------------------------------------------------------------

async def ingest_target(
    target: dict,
    conn,
    completed_ids: set,
    stats: dict,
    dry_run: bool,
    limit: int | None,
) -> int:
    """Fetch + upsert docs for one target.  Returns number of docs inserted."""
    civic_type = target["doc_classification"]
    logger.info("[ws11b] === %s (%s) ===", target["label"], civic_type)

    all_docs = await fetch_all_for_target(target, dry_run=dry_run)
    logger.info("[ws11b] ORI returned %d unique docs for %s", len(all_docs), civic_type)

    if dry_run:
        in_db = sum(1 for d in all_docs if doc_exists_in_db(conn, d["ori_id"]))
        missing = len(all_docs) - in_db
        logger.info(
            "[ws11b] DRY-RUN  %s: %d in ORI, %d in DB, %d missing",
            civic_type, len(all_docs), in_db, missing,
        )
        stats.setdefault(civic_type, {})
        stats[civic_type]["ori_count"] = len(all_docs)
        stats[civic_type]["in_db"] = in_db
        stats[civic_type]["missing"] = missing
        return 0

    inserted = 0
    for i, doc in enumerate(all_docs):
        if limit and inserted >= limit:
            logger.info("[ws11b] Reached --limit %d, stopping %s", limit, civic_type)
            break

        ori_id = doc.get("ori_id", "")
        if not ori_id:
            continue
        if ori_id in completed_ids:
            continue
        if doc_exists_in_db(conn, ori_id):
            completed_ids.add(ori_id)
            continue

        # Build document record
        content = doc.get("text", "").strip()
        raw_date = doc.get("last_discussed_at") or doc.get("start_date")
        document_date = raw_date[:10] if raw_date else None  # Keep only YYYY-MM-DD
        doc_data = {
            "id": ori_id,
            "name": doc.get("name") or "",
            "url": doc.get("url") or "",
            "content": content if content else None,
            "category": "municipal_doc",
            "doc_classification": civic_type,
            "municipality": "rotterdam",
            "source": "ori",
            "meeting_id": None,  # Will be derived later if was_generated_by is present
            "document_date": document_date,
        }

        # Attempt meeting linkage via was_generated_by
        wgb = doc.get("was_generated_by")
        if isinstance(wgb, list) and wgb:
            doc_data["meeting_id"] = wgb[0]
        elif isinstance(wgb, str) and wgb:
            doc_data["meeting_id"] = wgb

        ok = upsert_doc(conn, doc_data)
        if ok:
            inserted += 1
            completed_ids.add(ori_id)
            if inserted % BATCH_SIZE == 0:
                save_checkpoint(completed_ids, stats)
                logger.info(
                    "[ws11b] %s: inserted %d / %d so far",
                    civic_type, inserted, len(all_docs),
                )
        else:
            logger.warning("[ws11b] Failed to upsert %s (%s)", ori_id, civic_type)

        await asyncio.sleep(0)  # yield to event loop

    stats.setdefault(civic_type, {})["inserted"] = inserted
    save_checkpoint(completed_ids, stats)
    logger.info("[ws11b] %s: inserted %d new docs", civic_type, inserted)
    return inserted


async def main_async(args: argparse.Namespace) -> None:
    logger.info("=" * 60)
    mode = "DRY-RUN" if args.dry_run else "EXECUTE"
    logger.info("[ws11b] WS11b ORI ingestion — mode: %s", mode)
    if args.resume:
        logger.info("[ws11b] Resume mode: loading checkpoint")
    logger.info("=" * 60)

    checkpoint = load_checkpoint() if args.resume else {"completed_ids": [], "stats": {}}
    completed_ids: set = set(checkpoint.get("completed_ids", []))
    stats: dict = checkpoint.get("stats", {})

    conn = psycopg2.connect(_build_db_url())

    total_inserted = 0
    try:
        for target in TARGETS:
            if target["priority"] == "P1" and not args.include_p1:
                logger.info("[ws11b] Skipping %s (P1 — use --include-p1 to run)", target["label"])
                continue

            inserted = await ingest_target(
                target=target,
                conn=conn,
                completed_ids=completed_ids,
                stats=stats,
                dry_run=args.dry_run,
                limit=args.limit,
            )
            total_inserted += inserted

    finally:
        save_checkpoint(completed_ids, stats)
        conn.close()

    logger.info("=" * 60)
    logger.info("[ws11b] Done.  Total inserted: %d", total_inserted)
    if not args.dry_run and total_inserted > 0:
        logger.info(
            "[ws11b] Next step: run document_processor.py to chunk + embed new docs:"
        )
        logger.info("  python -m services.document_processor --limit 500")
    logger.info("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="WS11b: ORI batch ingestion of missing civic documents")
    parser.add_argument("--dry-run", action="store_true",
                        help="Audit ORI counts vs DB — no writes")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from checkpoint (skip already-completed ORI IDs)")
    parser.add_argument("--include-p1", action="store_true",
                        help="Also ingest P1 types: initiatiefvoorstel, raadsvoorstel, toezegging, brief_college, afdoeningsvoorstel")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max new docs to insert per target (for testing)")
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
