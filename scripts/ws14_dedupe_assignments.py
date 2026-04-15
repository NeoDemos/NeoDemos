"""
WS14 B2 — Deduplicate document_assignments.

Deletes duplicate rows from document_assignments keeping the row with
the lowest id per (document_id, meeting_id, agenda_item_id) triple.

Usage:
    python scripts/ws14_dedupe_assignments.py --dry-run
    python scripts/ws14_dedupe_assignments.py
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


# Count duplicates (rows with rn > 1)
COUNT_DUPS_SQL = """
WITH dups AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY document_id, meeting_id, agenda_item_id
            ORDER BY id
        ) AS rn
    FROM document_assignments
)
SELECT COUNT(*) FROM dups WHERE rn > 1
"""

# Show a sample of what would be deleted
SAMPLE_DUPS_SQL = """
WITH dups AS (
    SELECT
        id,
        document_id,
        meeting_id,
        agenda_item_id,
        ROW_NUMBER() OVER (
            PARTITION BY document_id, meeting_id, agenda_item_id
            ORDER BY id
        ) AS rn
    FROM document_assignments
)
SELECT id, document_id, meeting_id, agenda_item_id
FROM dups
WHERE rn > 1
ORDER BY document_id, meeting_id, agenda_item_id, id
LIMIT 10
"""

# Delete duplicates keeping lowest id per triple
DELETE_DUPS_SQL = """
WITH dups AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY document_id, meeting_id, agenda_item_id
            ORDER BY id
        ) AS rn
    FROM document_assignments
)
DELETE FROM document_assignments
WHERE id IN (SELECT id FROM dups WHERE rn > 1)
"""


def run(dry_run: bool) -> None:
    print("=== WS14 B2: Deduplicate document_assignments ===")
    print(f"Mode : {'DRY RUN' if dry_run else 'LIVE'}")
    print()

    conn = get_conn()
    try:
        cur = conn.cursor()

        # Acquire advisory lock
        cur.execute("SELECT pg_advisory_lock(42)")
        print("Advisory lock 42 acquired.")

        # Count duplicates
        cur.execute(COUNT_DUPS_SQL)
        dup_count = cur.fetchone()[0]
        print(f"Duplicate rows found (rn > 1): {dup_count}")

        if dup_count == 0:
            print("Nothing to do — no duplicates.")
            cur.execute("SELECT pg_advisory_unlock(42)")
            conn.rollback()
            return

        if dry_run:
            print(f"[DRY RUN] Would delete {dup_count} rows from document_assignments.")
            # Show a sample
            cur.execute(SAMPLE_DUPS_SQL)
            sample = cur.fetchall()
            print(f"\nSample (first {len(sample)} duplicate rows that would be deleted):")
            for row_id, doc_id, meeting_id, agenda_item_id in sample:
                print(f"  id={row_id:<8}  document_id={doc_id!r:40s}  meeting_id={str(meeting_id)[:30]:30s}  agenda_item_id={str(agenda_item_id)[:30]}")
            cur.execute("SELECT pg_advisory_unlock(42)")
            conn.rollback()
            return

        # Live run
        cur.execute(DELETE_DUPS_SQL)
        deleted = cur.rowcount
        cur.execute("SELECT pg_advisory_unlock(42)")
        conn.commit()
        print(f"\nDone. Rows deleted: {deleted}")

    except Exception as exc:
        conn.rollback()
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
    parser = argparse.ArgumentParser(description="Delete duplicate rows from document_assignments.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done without committing.")
    args = parser.parse_args()

    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
