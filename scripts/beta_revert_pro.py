"""Revert users from 'pro' back to 'free_beta' when their `pro_expires_at` has passed.

Run at/after BETA_END_DATE to clean up users who opted into Pro during the free
beta window. Re-runnable — only touches rows that still need reverting.

Usage:
    python scripts/beta_revert_pro.py --dry-run   # list affected users
    python scripts/beta_revert_pro.py             # apply

Uses pg_advisory_lock(42) to coordinate with embed/ingest jobs.
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview only.")
    args = parser.parse_args()

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    print("=== beta_revert_pro ===")
    print(f"Mode : {mode}\n")

    conn = _connect()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(42)")
            print("Advisory lock 42 acquired.")

            cur.execute(
                """
                SELECT id, email, subscription_tier, pro_expires_at
                FROM users
                WHERE subscription_tier = 'pro'
                  AND pro_expires_at IS NOT NULL
                  AND pro_expires_at < CURRENT_DATE
                ORDER BY pro_expires_at
                """
            )
            rows = cur.fetchall()
            print(f"Users to revert: {len(rows)}")
            for uid, email, tier, expires in rows[:20]:
                print(f"  - id={uid} email={email} expires={expires}")
            if len(rows) > 20:
                print(f"  ... (+{len(rows) - 20} more)")

            if args.dry_run or not rows:
                conn.rollback()
                print("\nDry-run or nothing to do — no writes.")
                return 0

            ids = tuple(r[0] for r in rows)
            cur.execute(
                """
                UPDATE users
                SET subscription_tier = 'free_beta',
                    subscription_tier_override = 'free_beta',
                    pro_expires_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ANY(%s)
                """,
                (list(ids),),
            )
            conn.commit()
            print(f"\nReverted: {cur.rowcount} users.")
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
