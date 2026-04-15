"""WS14 B3 — fallback classifier: NULL-classification docs linked to an agenda
item are bijlagen.

Rationale: the WS11 pattern-based classifier catches ~30 named types but
~65% of 2025-2026 unclassified docs are bracket-id iBabs refs
(`[25BB004202] 25bb004202`) with no keyword in the name. These are all
bijlagen in practice — they're attached to an agenda_item via
document_assignments.

This script defaults any NULL-classification document that has ≥1 row in
document_assignments (agenda_item_id IS NOT NULL) to `doc_classification =
'bijlage'`. Safe to re-run. Use `pg_advisory_lock(42)`.

Usage:
    python scripts/ws14_b3_bijlage_fallback.py --dry-run
    python scripts/ws14_b3_bijlage_fallback.py
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv()


def _connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "neodemos"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD"),
    )


COUNT_SQL = """
    SELECT COUNT(*) FROM documents d
    WHERE d.doc_classification IS NULL
      AND EXISTS (
        SELECT 1 FROM document_assignments da
        JOIN meetings m ON m.id = da.meeting_id
        WHERE da.document_id = d.id
          AND da.agenda_item_id IS NOT NULL
          AND EXTRACT(YEAR FROM m.start_date) BETWEEN %s AND %s
      )
"""

UPDATE_SQL = """
    UPDATE documents d
    SET doc_classification = 'bijlage'
    WHERE d.doc_classification IS NULL
      AND EXISTS (
        SELECT 1 FROM document_assignments da
        JOIN meetings m ON m.id = da.meeting_id
        WHERE da.document_id = d.id
          AND da.agenda_item_id IS NOT NULL
          AND EXTRACT(YEAR FROM m.start_date) BETWEEN %s AND %s
      )
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--year-from", type=int, default=2023, help="Earliest meeting year (default 2023)")
    parser.add_argument("--year-to", type=int, default=2026, help="Latest meeting year (default 2026)")
    args = parser.parse_args()

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    print("=== WS14 B3: bijlage fallback for agenda-linked NULL docs ===")
    print(f"Mode   : {mode}")
    print(f"Years  : {args.year_from} to {args.year_to}\n")

    conn = _connect()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(42)")
            print("Advisory lock 42 acquired.")
            cur.execute(COUNT_SQL, (args.year_from, args.year_to))
            (n,) = cur.fetchone()
            print(f"Candidates (NULL + agenda-linked, {args.year_from}-{args.year_to}): {n}")

            if args.dry_run or n == 0:
                conn.rollback()
                print("Dry-run or nothing to do — no writes.")
                return 0

            cur.execute(UPDATE_SQL, (args.year_from, args.year_to))
            updated = cur.rowcount
            conn.commit()
            print(f"Updated: {updated} rows → doc_classification='bijlage'.")
        return 0
    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(42)")
        except Exception:
            pass
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
