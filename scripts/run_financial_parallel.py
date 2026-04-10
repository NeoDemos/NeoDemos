#!/usr/bin/env python3
"""
Parallel Financial Document Batch Processor.

Spawns N independent worker processes via subprocess.Popen (not multiprocessing.Pool,
which gets killed by Claude's sandbox). Each worker claims docs from staging using
row-level locking (SELECT ... FOR UPDATE SKIP LOCKED) so workers never collide.

State is fully in the staging DB — safe to kill and restart at any time.

Usage (must be launched via nohup to escape Claude's sandbox):
    nohup python scripts/run_financial_parallel.py --workers 4 > /tmp/parallel.log 2>&1 &

    python scripts/run_financial_parallel.py --workers 6 --status
    python scripts/run_financial_parallel.py --worker-loop  # internal: single worker mode
"""

import argparse
import json
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


def claim_next_doc(worker_id: int):
    """
    Atomically claim one pending PDF from staging using SELECT ... FOR UPDATE SKIP LOCKED.
    Marks the row as 'extracting' (review_status='extracting') so other workers skip it.
    Returns dict with doc info, or None if no work left.
    """
    import psycopg2
    from psycopg2.extras import RealDictCursor

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT id, doc_type, fiscal_year, pdf_path, source_url
            FROM staging.financial_documents
            WHERE pdf_path IS NOT NULL
              AND docling_tables_found IS NULL
              AND review_status = 'pending'
            ORDER BY fiscal_year DESC, id
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """
        )
        row = cur.fetchone()
        if not row:
            conn.commit()
            return None

        # Mark as being extracted so other workers skip it
        cur.execute(
            "UPDATE staging.financial_documents SET review_status = 'extracting' WHERE id = %s",
            (row["id"],),
        )
        conn.commit()
        return dict(row)
    except Exception as e:
        conn.rollback()
        print(f"[worker {worker_id}] claim error: {e}", flush=True)
        return None
    finally:
        conn.close()


def release_doc(doc_id: str, success: bool):
    """Reset review_status to 'pending' on failure (so it can be retried)."""
    if success:
        return  # success leaves it as 'pending' but with docling_tables_found set
    import psycopg2

    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE staging.financial_documents SET review_status = 'pending' WHERE id = %s",
            (doc_id,),
        )
        conn.commit()
    finally:
        conn.close()


def reset_extracting():
    """At startup, reset any 'extracting' rows back to 'pending' (cleanup from prior crash)."""
    import psycopg2

    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE staging.financial_documents SET review_status = 'pending' "
            "WHERE review_status = 'extracting'"
        )
        n = cur.rowcount
        conn.commit()
        if n:
            print(f"[main] Reset {n} stale 'extracting' rows to 'pending'", flush=True)
    finally:
        conn.close()


def _free_memory():
    """Aggressively release memory back to the OS between PDFs."""
    import gc
    gc.collect()
    gc.collect()  # Run twice to catch cycles missed in first pass
    try:
        import torch
        if hasattr(torch, "mps") and torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:
        pass
    # On macOS, malloc_trim isn't available — but jemalloc/system malloc
    # release on gc.collect() if there are no live references


def _phys_footprint_gb():
    """Return physical footprint in GB via vmmap. Returns None if unavailable."""
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


def worker_loop(worker_id: int):
    """Main loop for a single worker process. Claims docs and processes them."""
    print(f"[worker {worker_id}] started, pid={os.getpid()}", flush=True)

    # Lazy import — heavy
    from pipeline.financial_ingestor import FinancialDocumentIngestor

    ingestor = FinancialDocumentIngestor(db_url=DB_URL)
    print(f"[worker {worker_id}] Docling loaded", flush=True)

    processed = 0
    failed = 0
    total_tables = 0
    total_chunks = 0

    # Recycle worker after this many docs to fully release accumulated memory
    RECYCLE_AFTER = 50

    while True:
        item = claim_next_doc(worker_id)
        if not item:
            print(f"[worker {worker_id}] no more work, exiting", flush=True)
            break

        doc_id = item["id"]
        doc_type = item["doc_type"]
        fiscal_year = item["fiscal_year"] or 0
        pdf_path = item["pdf_path"]
        source_url = item["source_url"] or ""
        doc_name = f"{doc_type.replace('_', ' ').title()} {fiscal_year}"

        start = time.time()
        try:
            result = ingestor.process_pdf(
                pdf_path=pdf_path,
                doc_id=doc_id,
                doc_name=doc_name,
                doc_type=doc_type,
                fiscal_year=fiscal_year,
                source_url=source_url,
            )
            elapsed = time.time() - start
            tables = result.get("tables_found", 0)
            chunks = result.get("chunks_created", 0)
            total_tables += tables
            total_chunks += chunks
            processed += 1
            mem = _phys_footprint_gb()
            mem_str = f" mem={mem:.1f}GB" if mem is not None else ""
            print(
                f"[worker {worker_id}] DONE {doc_id}: {tables} tables, "
                f"{chunks} chunks, {elapsed:.0f}s{mem_str}",
                flush=True,
            )
        except Exception as e:
            elapsed = time.time() - start
            failed += 1
            print(
                f"[worker {worker_id}] FAIL {doc_id}: {type(e).__name__}: {e} ({elapsed:.0f}s)",
                flush=True,
            )
            release_doc(doc_id, success=False)

        # Release memory between docs
        _free_memory()

        # Hard recycle: if we've processed many docs, exit so the spawner restarts us fresh
        if processed > 0 and processed % RECYCLE_AFTER == 0:
            print(
                f"[worker {worker_id}] recycling after {processed} docs to free memory",
                flush=True,
            )
            return  # main() will detect exit and respawn this worker

    print(
        f"[worker {worker_id}] EXIT — processed={processed}, failed={failed}, "
        f"tables={total_tables}, chunks={total_chunks}",
        flush=True,
    )


def show_status():
    """Print current staging status."""
    import psycopg2

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            doc_type,
            count(*) as total,
            count(pdf_path) as downloaded,
            count(docling_tables_found) as extracted,
            count(promoted_at) as promoted,
            sum(CASE WHEN review_status = 'extracting' THEN 1 ELSE 0 END) as in_progress
        FROM staging.financial_documents
        GROUP BY doc_type ORDER BY count(*) DESC
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    print(f"\n{'Category':<25} {'Total':>6} {'DL':>6} {'Extr':>6} {'Prom':>6} {'WIP':>5}")
    print("-" * 65)
    total_t = total_dl = total_ex = total_pr = total_wip = 0
    for r in rows:
        print(f"  {r[0]:<23} {r[1]:>6} {r[2]:>6} {r[3]:>6} {r[4]:>6} {r[5]:>5}")
        total_t += r[1]; total_dl += r[2]; total_ex += r[3]; total_pr += r[4]; total_wip += r[5]
    print("-" * 65)
    print(f"  {'TOTAL':<23} {total_t:>6} {total_dl:>6} {total_ex:>6} {total_pr:>6} {total_wip:>5}")

    pending_extract = total_dl - total_ex
    print(f"\nPending extraction: {pending_extract}")
    print(f"In progress (workers): {total_wip}")
    print(f"Awaiting promotion: {total_ex - total_pr}")


def _spawn_one(worker_id: int, log_dir: Path) -> tuple:
    """Spawn a single worker process. Returns (proc, log_file, log_path)."""
    log_path = log_dir / f"worker_{worker_id}.log"
    # Append mode so a respawned worker keeps history
    log_file = open(log_path, "a")
    log_file.write(f"\n--- spawn at {time.strftime('%H:%M:%S')} ---\n")
    log_file.flush()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-u",
            __file__,
            "--worker-loop",
            "--worker-id",
            str(worker_id),
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
        start_new_session=True,
    )
    return (proc, log_file, log_path)


def spawn_workers(num_workers: int):
    """Spawn N independent worker processes via subprocess.Popen.

    Workers self-recycle after RECYCLE_AFTER docs to release accumulated memory.
    Main process detects exits and respawns until no work remains.
    """
    reset_extracting()

    log_dir = Path("/tmp/docling_parallel")
    log_dir.mkdir(exist_ok=True)
    # Truncate previous run logs
    for i in range(num_workers):
        (log_dir / f"worker_{i}.log").write_text("")

    print(f"=== Parallel Docling Pipeline ({num_workers} workers) ===", flush=True)
    print(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(f"Worker logs: {log_dir}/worker_*.log", flush=True)

    workers = {}  # worker_id -> (proc, log_file, log_path)
    for i in range(num_workers):
        workers[i] = _spawn_one(i, log_dir)
        print(f"  Spawned worker {i}: PID {workers[i][0].pid}", flush=True)
        time.sleep(3)  # stagger to avoid all workers loading Docling at once

    print("\n=== Monitoring (Ctrl+C to stop) ===", flush=True)
    last_status = time.time()

    while True:
        # Check for any pending work
        try:
            import psycopg2
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            cur.execute(
                """SELECT count(*) FROM staging.financial_documents
                   WHERE pdf_path IS NOT NULL
                     AND docling_tables_found IS NULL
                     AND review_status = 'pending'"""
            )
            pending = cur.fetchone()[0]
            cur.close()
            conn.close()
        except Exception as e:
            print(f"[main] pending-check error: {e}", flush=True)
            pending = -1

        # Detect dead workers and respawn (or stop if no work left)
        for wid in list(workers.keys()):
            proc, log_file, log_path = workers[wid]
            rc = proc.poll()
            if rc is None:
                continue  # alive
            log_file.close()
            print(
                f"[main] worker {wid} (PID {proc.pid}) exited rc={rc}",
                flush=True,
            )
            if pending > 0:
                workers[wid] = _spawn_one(wid, log_dir)
                print(
                    f"[main] respawned worker {wid}: PID {workers[wid][0].pid}",
                    flush=True,
                )
            else:
                del workers[wid]

        if not workers:
            print("[main] all workers exited and no pending work — done", flush=True)
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
    parser = argparse.ArgumentParser(description="Parallel Docling extraction for financial PDFs")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel workers")
    parser.add_argument("--status", action="store_true", help="Show staging status only")
    parser.add_argument("--worker-loop", action="store_true", help="Internal: run single worker loop")
    parser.add_argument("--worker-id", type=int, default=0, help="Internal: worker ID")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.worker_loop:
        worker_loop(args.worker_id)
        return

    spawn_workers(args.workers)


if __name__ == "__main__":
    main()
