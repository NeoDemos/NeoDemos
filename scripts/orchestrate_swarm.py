import os
import time
import subprocess
import psycopg2
from datetime import datetime

# DB Connection
DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"

# PIDs to monitor (Phase A & B)
# Note: These are the PIDs found at 22:01. 
# We also check for any process matching the script names to be extra safe.
MONITOR_SCRIPTS = ["reingest_truncated.py", "compute_embeddings.py"]
PHASE_C_SCRIPT = "scripts/build_knowledge_graph.py"
LOG_FILE = "logs/swarm_orchestrator.log"
STATUS_LOG = "logs/hourly_swarm_status.log"

def get_db_stats():
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        
        # Phase A progress
        cur.execute("SELECT count(*) FROM documents WHERE content IS NOT NULL AND length(content) > 15000")
        phase_a_done = cur.fetchone()[0]
        
        # Phase B progress
        cur.execute("SELECT count(*) FROM chunking_metadata")
        phase_b_docs = cur.fetchone()[0]
        
        cur.execute("SELECT count(*) FROM document_chunks")
        total_chunks = cur.fetchone()[0]
        
        cur.close()
        conn.close()
        return phase_a_done, phase_b_docs, total_chunks
    except Exception as e:
        return f"Error: {e}", 0, 0

def is_swarm_active():
    """Check if any of the monitor scripts are still running."""
    try:
        output = subprocess.check_output(["ps", "aux"]).decode()
        for script in MONITOR_SCRIPTS:
            if script in output:
                return True
        return False
    except:
        return False

def log_message(msg, target_file=LOG_FILE):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(target_file, "a") as f:
        f.write(f"[{timestamp}] {msg}\n")
    print(f"[{timestamp}] {msg}")

def get_missed_count():
    """How many docs still need processing."""
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("""
            SELECT count(*) FROM documents d
            WHERE d.content IS NOT NULL AND length(d.content) > 20
              AND d.id NOT IN (SELECT document_id FROM chunking_metadata)
        """)
        missed = cur.fetchone()[0]
        cur.close(); conn.close()
        return missed
    except Exception as e:
        return -1

def run_phase(name, script_path, log_path):
    """Launch a script and wait for it to complete."""
    venv_python = "/Users/dennistak/Documents/Final Frontier/NeoDemos/.venv/bin/python3"
    if not os.path.exists(venv_python):
        venv_python = "python3"
    log_message(f"→ Launching {name} ({script_path})")
    process = subprocess.Popen(
        [venv_python, "-u", script_path],
        stdout=open(log_path, "a"),
        stderr=subprocess.STDOUT,
        cwd="/Users/dennistak/Documents/Final Frontier/NeoDemos"
    )
    log_message(f"  PID: {process.pid}")
    return process

def main():
    if not os.path.exists("logs"):
        os.makedirs("logs")

    log_message("Starting Swarm Orchestrator v2 (Phase B → Mop-Up → Phase C)")
    
    last_status = 0
    MOP_UP_SCRIPT = "scripts/mop_up_missed.py"
    MOP_UP_DONE = False

    while True:
        current_time = time.time()

        # 1. Progress Update every 30 minutes
        if current_time - last_status >= 1800:
            a_done, b_docs, chunks = get_db_stats()
            missed = get_missed_count()
            status_msg = (
                f"STATUS | Phase A: {a_done}/17511 | "
                f"Phase B: {b_docs} docs processed | "
                f"Chunks: {chunks} | Missed: {missed}"
            )
            log_message(status_msg, STATUS_LOG)
            log_message(status_msg)
            last_status = current_time

        # 2. Phase B complete? → Run Mop-Up first
        if not is_swarm_active() and not MOP_UP_DONE:
            missed = get_missed_count()
            log_message(f"Phase B complete. {missed} documents still need mop-up.")
            if missed > 0:
                log_message("Starting Mop-Up sweep...")
                proc = run_phase("Mop-Up", MOP_UP_SCRIPT, "logs/mop_up.log")
                proc.wait()  # Block until mop-up finishes
                log_message(f"Mop-Up complete. Exit code: {proc.returncode}")
            else:
                log_message("No missed documents — skipping mop-up.")
            MOP_UP_DONE = True

        # 3. Mop-Up done → trigger Phase C
        if MOP_UP_DONE and not is_swarm_active():
            log_message("Triggering Phase C: Agentic GraphRAG Build")
            try:
                proc_c = run_phase("Phase C", PHASE_C_SCRIPT, "logs/knowledge_graph.log")
                log_message(f"Phase C running (PID {proc_c.pid}). Orchestrator exiting.")
                break
            except Exception as e:
                log_message(f"CRITICAL ERROR triggering Phase C: {e}")
                time.sleep(60)
                continue

        time.sleep(60)

if __name__ == "__main__":
    main()
