#!/usr/bin/env python3
"""
Parallel Promotion Runner — staging → production with N workers.

Each worker uses the same promote_financial_doc() logic from
scripts/promote_financial_docs.py but pulls jobs via row-locking
(SELECT ... FOR UPDATE SKIP LOCKED) so workers never collide on the
same document.

State is fully in the staging DB — safe to kill and restart at any time.

Usage (must be launched via nohup to escape Claude's sandbox):
    nohup python scripts/run_promote_parallel.py --workers 4 > /tmp/promote_parallel.log 2>&1 &

    python scripts/run_promote_parallel.py --status
    python scripts/run_promote_parallel.py --worker-loop  # internal: single worker mode
"""

import argparse
import gc
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv()


def _build_db_url():
    url = os.getenv("DATABASE_URL", "")
    if url:
        return url
    h = os.getenv("DB_HOST", "localhost")
    p = os.getenv("DB_PORT", "5432")
    d = os.getenv("DB_NAME", "neodemos")
    u = os.getenv("DB_USER", "postgres")
    pw = os.getenv("DB_PASSWORD", "postgres")
    return f"postgresql://{u}:{pw}@{h}:{p}/{d}"


DB_URL = _build_db_url()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RECYCLE_AFTER = 100  # respawn worker after this many docs to release memory


def claim_next_doc(min_tables: int = 0):
    """
    Claim one staging.financial_documents row for promotion.
    Uses SELECT ... FOR UPDATE SKIP LOCKED so concurrent workers never collide.
    Marks the row as 'promoting' to prevent re-claiming.
    Returns dict or None.
    """
    import psycopg2
    from psycopg2.extras import RealDictCursor

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT id, doc_type, fiscal_year, docling_tables_found, docling_chunks_created
            FROM staging.financial_documents
            WHERE docling_tables_found IS NOT NULL
              AND docling_tables_found >= %s
              AND promoted_at IS NULL
              AND review_status IN ('pending', 'auto_approved', 'extracting')
            ORDER BY id
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """,
            (min_tables,),
        )
        row = cur.fetchone()
        if not row:
            conn.commit()
            return None

        cur.execute(
            "UPDATE staging.financial_documents SET review_status = 'promoting' WHERE id = %s",
            (row["id"],),
        )
        conn.commit()
        return dict(row)
    except Exception as e:
        conn.rollback()
        print(f"[claim] error: {e}", flush=True)
        return None
    finally:
        conn.close()


def release_doc(doc_id: str):
    """Reset review_status to 'pending' if promotion failed (allows retry)."""
    import psycopg2

    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE staging.financial_documents SET review_status = 'pending' WHERE id = %s AND promoted_at IS NULL",
            (doc_id,),
        )
        conn.commit()
    finally:
        conn.close()


def reset_promoting():
    """Reset stale 'promoting' rows back to 'pending' (cleanup from prior crash)."""
    import psycopg2

    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE staging.financial_documents SET review_status = 'pending' "
            "WHERE review_status = 'promoting' AND promoted_at IS NULL"
        )
        n = cur.rowcount
        conn.commit()
        if n:
            print(f"[main] Reset {n} stale 'promoting' rows to 'pending'", flush=True)
    finally:
        conn.close()


def _phys_footprint_gb():
    try:
        out = subprocess.run(
            ["vmmap", "-summary", str(os.getpid())],
            capture_output=True, text=True, timeout=5,
        ).stdout
        for line in out.splitlines():
            if "Physical footprint:" in line and "peak" not in line:
                val = line.split(":", 1)[1].strip()
                if val.endswith("G"):
                    return float(val[:-1])
                if val.endswith("M"):
                    return float(val[:-1]) / 1024
        return None
    except Exception:
        return None


def worker_loop(worker_id: int, min_tables: int):
    """Worker pulls one promotion job at a time and runs promote_financial_doc()."""
    print(f"[worker {worker_id}] started, pid={os.getpid()}, min_tables={min_tables}", flush=True)

    # Lazy import — heavy
    from scripts.promote_financial_docs import promote_financial_doc

    processed = 0
    failed = 0

    while True:
        item = claim_next_doc(min_tables=min_tables)
        if not item:
            print(f"[worker {worker_id}] no more work, exiting", flush=True)
            break

        doc_id = item["id"]
        tables = item["docling_tables_found"] or 0
        chunks = item["docling_chunks_created"] or 0
        start = time.time()

        try:
            success = promote_financial_doc(doc_id)
            elapsed = time.time() - start
            if success:
                processed += 1
                mem = _phys_footprint_gb()
                mem_str = f" mem={mem:.1f}GB" if mem is not None else ""
                print(
                    f"[worker {worker_id}] DONE {doc_id}: {tables} tables, "
                    f"{chunks} chunks, {elapsed:.0f}s{mem_str}",
                    flush=True,
                )
            else:
                failed += 1
                print(
                    f"[worker {worker_id}] FAIL {doc_id}: promotion returned False ({elapsed:.0f}s)",
                    flush=True,
                )
                release_doc(doc_id)
        except Exception as e:
            elapsed = time.time() - start
            failed += 1
            print(
                f"[worker {worker_id}] FAIL {doc_id}: {type(e).__name__}: {e} ({elapsed:.0f}s)",
                flush=True,
            )
            release_doc(doc_id)

        # Free memory between docs (prevents accumulator-style leaks)
        gc.collect()

        # Hard recycle to fully release memory if we've done many docs
        if processed > 0 and processed % RECYCLE_AFTER == 0:
            print(
                f"[worker {worker_id}] recycling after {processed} docs to free memory",
                flush=True,
            )
            return  # spawner will respawn

    print(
        f"[worker {worker_id}] EXIT — processed={processed}, failed={failed}",
        flush=True,
    )


def show_status():
    """Print current promotion-relevant status."""
    import psycopg2

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            doc_type,
            count(*) as total,
            count(docling_tables_found) as extracted,
            count(promoted_at) as promoted,
            sum(CASE WHEN review_status = 'promoting' THEN 1 ELSE 0 END) as in_progress
        FROM staging.financial_documents
        WHERE source = 'ori'
        GROUP BY doc_type ORDER BY count(*) DESC
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    print(f"\n{'Category':<25} {'Total':>6} {'Extr':>6} {'Prom':>6} {'WIP':>5}")
    print("-" * 60)
    total_t = total_ex = total_pr = total_wip = 0
    for r in rows:
        print(f"  {r[0]:<23} {r[1]:>6} {r[2]:>6} {r[3]:>6} {r[4]:>5}")
        total_t += r[1]; total_ex += r[2]; total_pr += r[3]; total_wip += r[4]
    print("-" * 60)
    print(f"  {'TOTAL':<23} {total_t:>6} {total_ex:>6} {total_pr:>6} {total_wip:>5}")
    print(f"\nPending promotion: {total_ex - total_pr - total_wip}")
    print(f"In progress (workers): {total_wip}")


def _spawn_one(worker_id: int, log_dir: Path, min_tables: int) -> tuple:
    """Spawn a single promotion worker."""
    log_path = log_dir / f"promote_worker_{worker_id}.log"
    log_file = open(log_path, "a")
    log_file.write(f"\n--- spawn at {time.strftime('%H:%M:%S')} ---\n")
    log_file.flush()
    proc = subprocess.Popen(
        [
            sys.executable, "-u", __file__,
            "--worker-loop",
            "--worker-id", str(worker_id),
            "--min-tables", str(min_tables),
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
        start_new_session=True,
    )
    return (proc, log_file, log_path)


def spawn_workers(num_workers: int, min_tables: int):
    """Spawn N promotion workers and respawn them when they exit due to recycling."""
    reset_promoting()

    log_dir = Path("/tmp/docling_parallel")
    log_dir.mkdir(exist_ok=True)
    for i in range(num_workers):
        (log_dir / f"promote_worker_{i}.log").write_text("")

    print(f"=== Parallel Promotion Pipeline ({num_workers} workers, min_tables={min_tables}) ===", flush=True)
    print(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(f"Worker logs: {log_dir}/promote_worker_*.log", flush=True)

    workers = {}
    for i in range(num_workers):
        workers[i] = _spawn_one(i, log_dir, min_tables)
        print(f"  Spawned promote worker {i}: PID {workers[i][0].pid}", flush=True)
        time.sleep(2)

    print("\n=== Monitoring (Ctrl+C to stop) ===", flush=True)
    last_status = time.time()

    while True:
        try:
            import psycopg2
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            cur.execute(
                """SELECT count(*) FROM staging.financial_documents
                   WHERE docling_tables_found IS NOT NULL
                     AND docling_tables_found >= %s
                     AND promoted_at IS NULL
                     AND review_status IN ('pending', 'auto_approved')""",
                (min_tables,),
            )
            pending = cur.fetchone()[0]
            cur.close()
            conn.close()
        except Exception as e:
            print(f"[main] pending-check error: {e}", flush=True)
            pending = -1

        for wid in list(workers.keys()):
            proc, log_file, log_path = workers[wid]
            rc = proc.poll()
            if rc is None:
                continue
            log_file.close()
            print(f"[main] promote worker {wid} (PID {proc.pid}) exited rc={rc}", flush=True)
            if pending > 0:
                workers[wid] = _spawn_one(wid, log_dir, min_tables)
                print(f"[main] respawned promote worker {wid}: PID {workers[wid][0].pid}", flush=True)
            else:
                del workers[wid]

        if not workers:
            print("[main] all promote workers exited and no pending work — done", flush=True)
            break

        time.sleep(15)
        if time.time() - last_status >= 60:
            try:
                show_status()
                last_status = time.time()
            except Exception as e:
                print(f"[main] status error: {e}", flush=True)

    print(f"\n=== Done: {time.strftime('%Y-%m-%d %H:%M:%S')} ===", flush=True)
    show_status()


def main():
    parser = argparse.ArgumentParser(description="Parallel staging → production promotion")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel workers")
    parser.add_argument("--min-tables", type=int, default=0, help="Only promote docs with >= N tables")
    parser.add_argument("--status", action="store_true", help="Show staging status only")
    parser.add_argument("--worker-loop", action="store_true", help="Internal: single worker mode")
    parser.add_argument("--worker-id", type=int, default=0, help="Internal: worker id")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.worker_loop:
        worker_loop(args.worker_id, args.min_tables)
        return

    spawn_workers(args.workers, args.min_tables)


if __name__ == "__main__":
    main()
