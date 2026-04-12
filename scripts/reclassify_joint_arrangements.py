#!/usr/bin/env python3
"""Reclassify financial documents by entity (joint arrangement detection).

One-shot script that retroactively applies classify_entity() to all existing
financial documents in staging and production, fixing the scope-hallucination
problem where GRJR/DCMR/VRR/MRDH docs were tagged as gemeente='rotterdam'.

Adds entity_id and scope columns to staging.financial_documents if missing,
then updates every row based on regex classification of title + source_url.

Usage:
    python scripts/reclassify_joint_arrangements.py              # Full run
    python scripts/reclassify_joint_arrangements.py --dry-run    # Preview only
"""

import argparse
import logging
import os
import sys
from collections import Counter
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from pipeline.financial_ingestor import classify_entity

# -- Configuration ---------------------------------------------------------

DB_URL = (
    os.getenv("DATABASE_URL")
    or os.getenv("DB_URL")
    or "postgresql://postgres:postgres@localhost:5432/neodemos"
)

LOG_PATH = PROJECT_ROOT / "logs" / "reclassify_joint_arrangements.log"
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


# -- Schema migration (idempotent) ----------------------------------------

def ensure_columns(conn) -> None:
    """Add entity_id and scope columns to staging.financial_documents if missing."""
    cur = conn.cursor()
    cur.execute("""
        ALTER TABLE staging.financial_documents
            ADD COLUMN IF NOT EXISTS entity_id TEXT DEFAULT 'rotterdam',
            ADD COLUMN IF NOT EXISTS scope TEXT DEFAULT 'gemeente';
    """)
    conn.commit()
    cur.close()
    log.info("Ensured entity_id and scope columns exist on staging.financial_documents")


# -- Reclassify staging ---------------------------------------------------

def reclassify_staging(conn, dry_run: bool) -> Counter:
    """Reclassify all staging.financial_documents rows.

    Returns a Counter of entity_id -> count.
    """
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT fd.id, fd.source_url, d.name AS doc_name
        FROM staging.financial_documents fd
        LEFT JOIN staging.documents d ON d.id = fd.id
        ORDER BY fd.id
    """)
    rows = cur.fetchall()
    cur.close()
    log.info(f"Found {len(rows)} staging financial documents to classify")

    counts: Counter = Counter()
    updates: list[tuple[str, str, str]] = []

    for row in rows:
        doc_id = row["id"]
        title = row.get("doc_name") or doc_id
        url = row.get("source_url")
        entity_id, scope = classify_entity(title, doc_url=url)
        counts[entity_id] += 1
        updates.append((entity_id, scope, doc_id))

    if dry_run:
        log.info("[DRY RUN] Would update staging.financial_documents:")
        for entity_id, count in sorted(counts.items()):
            log.info(f"  {entity_id}: {count} documents")
        return counts

    cur = conn.cursor()
    for entity_id, scope, doc_id in updates:
        cur.execute(
            """
            UPDATE staging.financial_documents
            SET entity_id = %s, scope = %s
            WHERE id = %s
            """,
            (entity_id, scope, doc_id),
        )
    conn.commit()
    cur.close()
    log.info(f"Updated {len(updates)} staging.financial_documents rows")
    return counts


# -- Reclassify production -------------------------------------------------

def reclassify_production(conn, dry_run: bool) -> Counter:
    """Reclassify already-promoted documents in public.documents + financial_lines.

    For production, we update financial_lines.entity_id and financial_lines.scope
    based on the document_id -> title mapping.
    """
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Check if financial_lines table exists (only after migration 0002)
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'financial_lines'
        )
    """)
    has_fl = cur.fetchone()["exists"]
    cur.close()

    if not has_fl:
        log.info("financial_lines table does not exist yet; skipping production reclassify")
        return Counter()

    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT DISTINCT fl.document_id, d.name AS doc_name
        FROM financial_lines fl
        JOIN documents d ON d.id = fl.document_id
    """)
    rows = cur.fetchall()
    cur.close()
    log.info(f"Found {len(rows)} distinct documents in financial_lines to classify")

    counts: Counter = Counter()
    updates: list[tuple[str, str, str]] = []

    for row in rows:
        doc_id = row["document_id"]
        title = row.get("doc_name") or doc_id
        entity_id, scope = classify_entity(title)
        counts[entity_id] += 1
        updates.append((entity_id, scope, doc_id))

    if dry_run:
        log.info("[DRY RUN] Would update financial_lines:")
        for entity_id, count in sorted(counts.items()):
            log.info(f"  {entity_id}: {count} documents")
        return counts

    cur = conn.cursor()
    for entity_id, scope, doc_id in updates:
        cur.execute(
            """
            UPDATE financial_lines
            SET entity_id = %s, scope = %s
            WHERE document_id = %s
            """,
            (entity_id, scope, doc_id),
        )
    conn.commit()
    cur.close()
    log.info(f"Updated financial_lines for {len(updates)} documents")
    return counts


# -- Main ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Reclassify financial documents by entity (joint arrangement detection)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview classifications without writing to DB",
    )
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    log.info(f"=== Reclassify Joint Arrangements ({mode}) ===")

    conn = psycopg2.connect(DB_URL)
    try:
        # Ensure schema columns exist (even in dry-run, this is safe/idempotent)
        ensure_columns(conn)

        # 1. Staging
        staging_counts = reclassify_staging(conn, dry_run=args.dry_run)

        # 2. Production
        prod_counts = reclassify_production(conn, dry_run=args.dry_run)

        # Summary
        log.info("=== Summary ===")
        log.info("Staging financial_documents:")
        for entity_id, count in sorted(staging_counts.items()):
            log.info(f"  {entity_id}: {count}")
        if prod_counts:
            log.info("Production financial_lines:")
            for entity_id, count in sorted(prod_counts.items()):
                log.info(f"  {entity_id}: {count}")
        else:
            log.info("Production: no financial_lines to update")

    except Exception:
        conn.rollback()
        log.exception("Reclassification failed")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
