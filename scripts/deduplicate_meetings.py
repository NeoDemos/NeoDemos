"""
WS14 B5 — Deduplicate meetings.

Finds meetings that share the same (start_date, name) and merges duplicates
into a single master record:
  - Reparents agenda_items, documents (direct FKs), and document_assignments
    from losers to master
  - Deletes loser records

Master selection:
  1. Score = agenda_items_count + document_assignments_count
  2. Tie-break 1: records with a non-NULL committee get +1 (iBabs+ORI case)
  3. Tie-break 2: earliest record (lowest id / earliest created_at / lowest
     natural sort on id string) wins

Usage:
    python scripts/deduplicate_meetings.py --dry-run
    python scripts/deduplicate_meetings.py --dry-run --municipality rotterdam --since 2023-01-01
    python scripts/deduplicate_meetings.py --municipality rotterdam
"""

import argparse
import os
import sys
from datetime import date

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


def find_dup_groups(cur, since_date=None):
    """Return duplicate meeting groups: list of (start_date, name, [id, ...])."""
    if since_date:
        cur.execute(
            """
            SELECT start_date::date, name, array_agg(id ORDER BY id)
            FROM meetings
            WHERE start_date::date >= %s
            GROUP BY start_date::date, name
            HAVING COUNT(*) > 1
            ORDER BY start_date::date, name
            """,
            (since_date,),
        )
    else:
        cur.execute(
            """
            SELECT start_date::date, name, array_agg(id ORDER BY id)
            FROM meetings
            GROUP BY start_date::date, name
            HAVING COUNT(*) > 1
            ORDER BY start_date::date, name
            """
        )
    return cur.fetchall()


def score_meeting(cur, meeting_id: str) -> tuple:
    """
    Return a sort key for master selection (higher = preferred master).

    Key: (agenda_items + document_assignments, committee_bonus, -natural_id_rank)

    We want the *highest* scoring candidate, so we return a tuple that sorts
    naturally with max() semantics (larger = better).
    """
    cur.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM agenda_items WHERE meeting_id = %s) +
            (SELECT COUNT(*) FROM document_assignments WHERE meeting_id = %s)
                AS combined_count,
            (SELECT committee FROM meetings WHERE id = %s) IS NOT NULL AS has_committee
        """,
        (meeting_id, meeting_id, meeting_id),
    )
    row = cur.fetchone()
    combined_count = row[0]
    committee_bonus = 1 if row[1] else 0
    # Prefer earlier id string lexicographically on tie (lower = earlier ingested)
    # We negate conceptually by using the id itself and picking min below —
    # handled at call site by sorting.
    return (combined_count + committee_bonus, combined_count, meeting_id)


def reparent_losers(cur, master_id: str, loser_ids: list, dry_run: bool) -> dict:
    """Move all children from losers to master. Returns counts dict."""
    counts = {"agenda_items": 0, "documents": 0, "document_assignments": 0}

    for loser_id in loser_ids:
        if not dry_run:
            # agenda_items
            cur.execute(
                "UPDATE agenda_items SET meeting_id = %s WHERE meeting_id = %s",
                (master_id, loser_id),
            )
            counts["agenda_items"] += cur.rowcount

            # documents (direct FK)
            cur.execute(
                "UPDATE documents SET meeting_id = %s WHERE meeting_id = %s",
                (master_id, loser_id),
            )
            counts["documents"] += cur.rowcount

            # document_assignments — reparent but skip rows that would conflict
            # with an existing assignment on master (same document + same
            # agenda_item_id). Use a subquery-guarded UPDATE.
            cur.execute(
                """
                UPDATE document_assignments
                SET meeting_id = %s
                WHERE meeting_id = %s
                  AND NOT EXISTS (
                      SELECT 1 FROM document_assignments da2
                      WHERE da2.document_id = document_assignments.document_id
                        AND da2.meeting_id = %s
                        AND (
                            da2.agenda_item_id = document_assignments.agenda_item_id
                            OR (da2.agenda_item_id IS NULL AND document_assignments.agenda_item_id IS NULL)
                        )
                  )
                """,
                (master_id, loser_id, master_id),
            )
            counts["document_assignments"] += cur.rowcount

            # Delete any remaining loser assignments (would conflict with master)
            cur.execute(
                "DELETE FROM document_assignments WHERE meeting_id = %s",
                (loser_id,),
            )
        else:
            # Dry run — just count what we would move
            cur.execute(
                "SELECT COUNT(*) FROM agenda_items WHERE meeting_id = %s", (loser_id,)
            )
            counts["agenda_items"] += cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM documents WHERE meeting_id = %s", (loser_id,)
            )
            counts["documents"] += cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM document_assignments WHERE meeting_id = %s",
                (loser_id,),
            )
            counts["document_assignments"] += cur.fetchone()[0]

    return counts


def run(municipality: str, dry_run: bool, since_date) -> None:
    print("=== WS14 B5: Deduplicate meetings ===")
    print(f"Municipality : {municipality}")
    print(f"Since        : {since_date or 'all time'}")
    print(f"Mode         : {'DRY RUN' if dry_run else 'LIVE'}")
    print()

    conn = get_conn()
    try:
        cur = conn.cursor()

        # Acquire advisory lock
        cur.execute("SELECT pg_advisory_lock(42)")
        print("Advisory lock 42 acquired.")

        groups = find_dup_groups(cur, since_date)
        print(f"Duplicate meeting groups found: {len(groups)}")
        if not groups:
            print("Nothing to do.")
            cur.execute("SELECT pg_advisory_unlock(42)")
            conn.rollback()
            return

        total_merged = 0
        total_deleted = 0
        total_children = {"agenda_items": 0, "documents": 0, "document_assignments": 0}

        for grp_idx, (start_dt, name, ids) in enumerate(groups):
            # Score each candidate
            scored = [score_meeting(cur, mid) for mid in ids]
            # Best master: highest score; on tie, lowest id string (earliest)
            # score_meeting returns (committee_aware_count, combined_count, id)
            # Sort descending on first two fields, ascending on id
            scored_sorted = sorted(scored, key=lambda t: (-t[0], -t[1], t[2]))
            master_id = scored_sorted[0][2]
            loser_ids = [t[2] for t in scored_sorted[1:]]

            counts = reparent_losers(cur, master_id, loser_ids, dry_run)
            for k, v in counts.items():
                total_children[k] += v

            if not dry_run:
                for loser_id in loser_ids:
                    cur.execute("DELETE FROM meetings WHERE id = %s", (loser_id,))
                    total_deleted += cur.rowcount

            total_merged += len(loser_ids)

            if (grp_idx + 1) % 20 == 0:
                print(f"  Processed {grp_idx + 1}/{len(groups)} groups...")
                if not dry_run:
                    conn.commit()

        if not dry_run:
            cur.execute("SELECT pg_advisory_unlock(42)")
            conn.commit()
            print(f"\nDone.")
            print(f"  Meetings merged (losers deleted) : {total_deleted}")
            print(f"  agenda_items reparented          : {total_children['agenda_items']}")
            print(f"  documents reparented             : {total_children['documents']}")
            print(f"  document_assignments reparented  : {total_children['document_assignments']}")
        else:
            cur.execute("SELECT pg_advisory_unlock(42)")
            conn.rollback()
            print(f"\n[DRY RUN] Would merge {total_merged} loser meetings across {len(groups)} groups.")
            print(f"  agenda_items to reparent         : {total_children['agenda_items']}")
            print(f"  documents to reparent            : {total_children['documents']}")
            print(f"  document_assignments to reparent : {total_children['document_assignments']}")

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
    parser = argparse.ArgumentParser(description="Deduplicate meetings by (start_date, name).")
    parser.add_argument(
        "--municipality", default="rotterdam", help="Municipality slug (informational, default: rotterdam)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done without committing.")
    parser.add_argument(
        "--since",
        default=None,
        metavar="YYYY-MM-DD",
        help="Only process meetings on or after this date.",
    )
    args = parser.parse_args()

    since_date = None
    if args.since:
        try:
            since_date = date.fromisoformat(args.since)
        except ValueError:
            print(f"ERROR: --since must be YYYY-MM-DD, got: {args.since!r}", file=sys.stderr)
            sys.exit(1)

    run(municipality=args.municipality, dry_run=args.dry_run, since_date=since_date)


if __name__ == "__main__":
    main()
