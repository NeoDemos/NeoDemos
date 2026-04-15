"""
WS14 B1 — Reconcile direct FK orphans into document_assignments.

Backfills the document_assignments junction table from documents that have
direct meeting_id or agenda_item_id FKs but no corresponding junction row.

Usage:
    python scripts/ws14_reconcile_direct_fks.py --dry-run --municipality rotterdam
    python scripts/ws14_reconcile_direct_fks.py --municipality rotterdam
"""

import argparse
import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv()


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "neodemos"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )


# Query to find orphan documents: direct FK exists but no junction row
ORPHAN_QUERY = """
SELECT
    d.id AS document_id,
    d.meeting_id,
    d.agenda_item_id
FROM documents d
WHERE
    d.municipality = %(municipality)s
    AND (d.meeting_id IS NOT NULL OR d.agenda_item_id IS NOT NULL)
    AND NOT EXISTS (
        SELECT 1
        FROM document_assignments da
        WHERE
            da.document_id = d.id
            AND (da.meeting_id = d.meeting_id OR (da.meeting_id IS NULL AND d.meeting_id IS NULL))
            AND (da.agenda_item_id = d.agenda_item_id OR (da.agenda_item_id IS NULL AND d.agenda_item_id IS NULL))
    )
ORDER BY d.id
"""

INSERT_BATCH = """
INSERT INTO document_assignments (document_id, meeting_id, agenda_item_id)
VALUES %s
ON CONFLICT DO NOTHING
"""


def run(municipality: str, dry_run: bool) -> None:
    print(f"=== WS14 B1: Reconcile direct FK orphans ===")
    print(f"Municipality : {municipality}")
    print(f"Mode         : {'DRY RUN' if dry_run else 'LIVE'}")
    print()

    conn = get_conn()
    try:
        cur = conn.cursor()

        # Acquire advisory lock — same lock as pipeline writes (42)
        cur.execute("SELECT pg_advisory_lock(42)")
        print("Advisory lock 42 acquired.")

        # Collect orphans
        cur.execute(ORPHAN_QUERY, {"municipality": municipality})
        rows = cur.fetchall()
        total = len(rows)
        print(f"Orphan documents found: {total}")

        if total == 0:
            print("Nothing to do.")
            cur.execute("SELECT pg_advisory_unlock(42)")
            conn.rollback()
            return

        if dry_run:
            print(f"[DRY RUN] Would insert {total} rows into document_assignments.")
            # Show a sample
            sample_size = min(10, total)
            print(f"\nSample (first {sample_size}):")
            for doc_id, meeting_id, agenda_item_id in rows[:sample_size]:
                print(f"  document_id={doc_id!r:40s}  meeting_id={str(meeting_id)[:30]:30s}  agenda_item_id={str(agenda_item_id)[:30]}")
            cur.execute("SELECT pg_advisory_unlock(42)")
            conn.rollback()
            return

        # Live run — insert in batches of 500
        from psycopg2.extras import execute_values

        inserted_total = 0
        batch_size = 500
        for i in range(0, total, batch_size):
            batch = rows[i : i + batch_size]
            # execute_values needs a list of tuples
            execute_values(
                cur,
                "INSERT INTO document_assignments (document_id, meeting_id, agenda_item_id) VALUES %s ON CONFLICT DO NOTHING",
                batch,
            )
            inserted_total += cur.rowcount
            conn.commit()
            print(f"  Batch {i // batch_size + 1}: inserted {cur.rowcount} rows (running total: {inserted_total})")

        cur.execute("SELECT pg_advisory_unlock(42)")
        conn.commit()
        print(f"\nDone. Total rows inserted: {inserted_total} (of {total} candidates; ON CONFLICT DO NOTHING absorbed the rest).")

    except Exception as exc:
        conn.rollback()
        # Best-effort unlock
        try:
            with conn.cursor() as uc:
                uc.execute("SELECT pg_advisory_unlock(42)")
            conn.commit()
        except Exception:
            pass
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill document_assignments from direct FK orphans.")
    parser.add_argument("--municipality", default="rotterdam", help="Municipality slug (default: rotterdam)")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done without committing.")
    args = parser.parse_args()

    run(municipality=args.municipality, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
