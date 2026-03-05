"""
launch_staggered.py
───────────────────
Master controller that:
  1. Builds the shared Postgres work-queue (all un-processed docs across ALL years)
  2. Launches 10 workers one at a time, 60 seconds apart (to prevent initial API burst)
  3. Logs a status report every 30 minutes
  4. Once all workers finish → triggers the Mop-up pass → then Phase C

Log files:
  logs/swarm_master.log      — staggered launch events + 30-min status reports
  logs/worker_N.log          — per-worker output (N = 1..10)
  logs/mop_up.log            — mop-up pass output
  logs/knowledge_graph.log   — Phase C output
"""

import os
import sys
import time
import subprocess
import psycopg2
from datetime import datetime

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
VENV_PY = "/Users/dennistak/Documents/Final Frontier/NeoDemos/.venv/bin/python3"
BASE_DIR = "/Users/dennistak/Documents/Final Frontier/NeoDemos"
LOG_DIR  = os.path.join(BASE_DIR, "logs")
MASTER_LOG = os.path.join(LOG_DIR, "swarm_master.log")

NUM_WORKERS    = 10
LAUNCH_DELAY   = 60    # seconds between each worker launch (stagger)
STATUS_EVERY   = 1800  # 30 minutes


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(MASTER_LOG, "a") as f:
        f.write(line + "\n")


def db_stats():
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()

        cur.execute("SELECT count(*) FROM chunking_queue WHERE status = 'pending'")
        pending = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM chunking_queue WHERE status = 'in_progress'")
        in_progress = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM chunking_queue WHERE status = 'done'")
        done = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM chunking_queue WHERE status = 'failed'")
        failed = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM document_chunks")
        chunks = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM documents WHERE content IS NOT NULL AND length(content) > 15000")
        phase_a = cur.fetchone()[0]

        cur.close(); conn.close()
        return pending, in_progress, done, failed, chunks, phase_a
    except Exception as e:
        return -1, -1, -1, -1, -1, -1


def is_workers_active():
    try:
        out = subprocess.check_output(["pgrep", "-f", "queue_worker.py"]).decode().strip()
        return bool(out)
    except:
        return False


def launch_worker(worker_id: int):
    log_path = os.path.join(LOG_DIR, f"worker_{worker_id}.log")
    process = subprocess.Popen(
        [VENV_PY, "-u", "scripts/queue_worker.py", "--worker-id", str(worker_id)],
        stdout=open(log_path, "a"),
        stderr=subprocess.STDOUT,
        cwd=BASE_DIR
    )
    log(f"→ Worker {worker_id} launched (PID {process.pid}) → logs/worker_{worker_id}.log")
    return process


def run_phase(name, script, log_path):
    log(f"═══ {name} starting ═══")
    proc = subprocess.Popen(
        [VENV_PY, "-u", script],
        stdout=open(log_path, "a"),
        stderr=subprocess.STDOUT,
        cwd=BASE_DIR
    )
    log(f"  PID: {proc.pid}")
    return proc


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    log("═══════════════════════════════════════════════════════")
    log("  Staggered Swarm Launcher — 10 Workers, 60s Delay")
    log("═══════════════════════════════════════════════════════")

    # Step 1: Build the shared queue
    log("Building shared work queue (all un-processed docs, ALL years)...")
    result = subprocess.run(
        [VENV_PY, "-u", "scripts/build_queue.py"],
        capture_output=True, text=True, cwd=BASE_DIR
    )
    for line in result.stdout.strip().splitlines():
        log(f"  {line}")
    if result.returncode != 0:
        log(f"FATAL: Queue build failed: {result.stderr}")
        sys.exit(1)

    # Step 2: Success-Gated Staggered launch — 1 worker at a time, wait for success
    processes = []
    log(f"Starting Success-Gated Ramp-up (Goal: {NUM_WORKERS} workers).")
    
    for i in range(1, NUM_WORKERS + 1):
        # 1. Launch the worker
        proc = launch_worker(i)
        processes.append(proc)
        
        if i < NUM_WORKERS:
            log(f"Waiting for at least one successful result before launching worker {i+1}...")
            
            # 2. Poll for success
            _, _, start_done, _, _, _ = db_stats()
            wait_start = time.time()
            succeeded = False
            
            while not succeeded:
                time.sleep(15) # Check every 15s
                _, _, current_done, _, _, _ = db_stats()
                
                if current_done > start_done:
                    log(f"Success detected! (Documents done: {current_done}). Ready to ramp up.")
                    succeeded = True
                
                # Safety timeout: if 10 mins pass with no success, launch anyway but log warning
                if time.time() - wait_start > 600:
                    log("WARNING: 10 min timeout reached with no new success. Ramping up anyway to maintain momentum.")
                    succeeded = True
            
            log(f"Staggering {LAUNCH_DELAY}s for API cooldown...")
            time.sleep(LAUNCH_DELAY)

    log(f"All {NUM_WORKERS} workers launched. Transitioning to full monitoring.")

    # Step 3: Monitor loop with 30-min status reports
    last_status = 0
    MOP_UP_DONE = False

    while True:
        current_time = time.time()

        # 30-minute status report
        if current_time - last_status >= STATUS_EVERY:
            pending, in_progress, done, failed, chunks, phase_a = db_stats()
            total = pending + in_progress + done + failed
            pct = round(done / max(total, 1) * 100, 1)
            log(
                f"STATUS | Phase A: {phase_a}/17511 | "
                f"Queue: {done}/{total} done ({pct}%) | "
                f"In-progress: {in_progress} | Failed: {failed} | "
                f"Total chunks: {chunks}"
            )
            last_status = current_time

        # Workers done → run Mop-Up
        if not is_workers_active() and not MOP_UP_DONE:
            pending, _, _, failed, _, _ = db_stats()
            log(f"All workers finished. Queue remaining: {pending} pending, {failed} failed.")

            if pending > 0 or failed > 0:
                log("Re-queuing failed items and launching Mop-Up pass (re-adds failed docs and re-runs)...")
                # Reset failed → pending so queue_worker picks them up again
                conn = psycopg2.connect(DB_URL)
                cur = conn.cursor()
                cur.execute("UPDATE chunking_queue SET status = 'pending', claimed_by = NULL, claimed_at = NULL, error_message = NULL WHERE status = 'failed'")
                conn.commit()
                cur.execute("SELECT count(*) FROM chunking_queue WHERE status = 'pending'")
                requeued = cur.fetchone()[0]
                cur.close(); conn.close()
                log(f"  Re-queued {requeued} failed docs. Launching Mop-Up with 5 gentle workers...")

                mop_procs = []
                for i in range(1, 6):
                    p = launch_worker(i)
                    mop_procs.append(p)
                    if i < 5:
                        time.sleep(90)  # Gentle mop-up stagger

                for p in mop_procs:
                    p.wait()
                log("Mop-Up complete.")

            MOP_UP_DONE = True

        # Mop-Up done → Phase C
        if MOP_UP_DONE and not is_workers_active():
            log("═══ Triggering Phase C: Agentic GraphRAG Build ═══")
            proc_c = run_phase("Phase C", "scripts/build_knowledge_graph.py", os.path.join(LOG_DIR, "knowledge_graph.log"))
            log(f"Phase C running (PID {proc_c.pid}). Master exiting.")
            break

        time.sleep(60)


if __name__ == "__main__":
    main()
