"""
smart_controller.py
─────────────────────────────────────────────────────────────────────
Dynamic TPM-aware controller for the NeoDemos chunking pipeline.

Strategy
────────
- Gemini 2.5 Flash Lite Tier 1: 4,000,000 tokens/minute (TPM)
- Maximum 4 concurrent workers
- Each worker picks documents from a shared Postgres queue
- The controller tracks a rolling 60s token-usage window
- Before each doc is dispatched, the controller ESTIMATES its token cost
  (characters × token_ratio). If the estimated cost would push the rolling
  total above SAFE_TPM_LIMIT (85% of quota), it makes the worker sleep
  until the window clears, OR it routes the worker to a SMALLER document.
- The controller scales workers up (1 → 4) only after each worker proves
  one success.

Token cost estimation
  input_chars / 4  ≈ input tokens  (LLM average: ~4 chars per token)
  + 2000 token output buffer
  ratio: typically 1:1.5 input/output for chunking tasks

Log files
  logs/smart_controller.log   — 30-min status + TPM events
  logs/worker_N.log           — per-worker detail
  logs/mop_up.log             — mop-up pass
  logs/knowledge_graph.log    — Phase C
"""

import os, sys, time, subprocess, threading, psycopg2
from datetime import datetime
from collections import deque

# ── Config ───────────────────────────────────────────────────────────────────
DB_URL       = "postgresql://postgres:postgres@localhost:5432/neodemos"
VENV_PY      = "/Users/dennistak/Documents/Final Frontier/NeoDemos/.venv/bin/python3"
BASE_DIR     = "/Users/dennistak/Documents/Final Frontier/NeoDemos"
LOG_DIR      = os.path.join(BASE_DIR, "logs")
CTRL_LOG     = os.path.join(LOG_DIR, "smart_controller.log")

MAX_WORKERS       = 20
TIER1_TPM         = 4_000_000   # Gemini 2.5 Flash Lite Tier 1
TIER1_RPM         = 4_000       # Gemini 2.5 Flash Lite Tier 1 request limit
SAFE_TPM_LIMIT    = int(TIER1_TPM * 0.75)   # 75% = 3M — conservative head-room
SAFE_RPM_LIMIT    = int(TIER1_RPM * 0.80)   # 80% = 3,200 req/min
STATUS_EVERY      = 1800        # 30-minute status reports
PROBE_INTERVAL    = 5           # seconds between controller loop ticks

# Token estimation: (input chars / 4) + 2000 output buffer + 10% overhead
def estimate_tokens(content_len: int) -> int:
    return int(content_len / 4 * 1.4) + 2000

# ── Shared TPM + RPM Tracking ───────────────────────────────────────────────
# Thread-safe rolling 60-second windows
tpm_lock   = threading.Lock()
tpm_window: deque = deque()  # (float timestamp, int tokens)
rpm_lock   = threading.Lock()
rpm_window: deque = deque()  # (float timestamp,) — one entry per request

def record_tokens(tokens: int):
    now = time.time()
    with tpm_lock:
        tpm_window.append((now, tokens))

def record_request():
    now = time.time()
    with rpm_lock:
        rpm_window.append(now)

def current_tpm() -> int:
    now = time.time()
    cutoff = now - 60.0
    with tpm_lock:
        while tpm_window and tpm_window[0][0] < cutoff:
            tpm_window.popleft()
        return sum(t for _, t in tpm_window)

def current_rpm() -> int:
    now = time.time()
    cutoff = now - 60.0
    with rpm_lock:
        while rpm_window and rpm_window[0] < cutoff:
            rpm_window.popleft()
        return len(rpm_window)

def headroom_tokens() -> int:
    return max(0, SAFE_TPM_LIMIT - current_tpm())

def can_send_request() -> bool:
    """Returns False if either the TPM or RPM ceiling is too close."""
    return current_rpm() < SAFE_RPM_LIMIT and headroom_tokens() > 3000

# ── Logging ───────────────────────────────────────────────────────────────────
def log(msg: str):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(CTRL_LOG, "a") as f:
        f.write(line + "\n")

# ── DB helpers ────────────────────────────────────────────────────────────────
def db_stats():
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute("SELECT count(*) FROM documents WHERE content IS NOT NULL AND length(content) > 15000")
        phase_a = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM chunking_metadata")
        done    = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM document_chunks")
        chunks  = cur.fetchone()[0]
        cur.execute("SELECT status, count(*) FROM chunking_queue GROUP BY status")
        q       = {r[0]: r[1] for r in cur.fetchall()}
        cur.close(); conn.close()
        return phase_a, done, chunks, q
    except Exception as e:
        log(f"DB stats error: {e}")
        return 0, 0, 0, {}

def claim_doc_by_size(worker_id: int, max_content_len: int, prefer_small: bool = False):
    """Claim the highest-priority document (either largest or smallest) that fits budget."""
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        
        # If we prefer small, sort ASC. Otherwise DESC (largest first).
        order_dir = "ASC" if prefer_small else "DESC"
        
        cur.execute(f"""
            SELECT q.document_id, length(d.content) as clen
            FROM chunking_queue q
            JOIN documents d ON d.id = q.document_id
            WHERE q.status = 'pending'
              AND NOT EXISTS (
                  SELECT 1 FROM chunking_metadata m
                  JOIN documents md ON md.id = m.document_id
                  WHERE m.document_id = q.document_id 
                     OR (md.name = d.name AND d.name != '' AND d.name IS NOT NULL)
              )
              AND length(d.content) <= %s
            ORDER BY length(d.content) {order_dir}, q.id ASC
            LIMIT 1
            FOR UPDATE OF q SKIP LOCKED
        """, (max_content_len,))
        row = cur.fetchone()
        if row is None:
            cur.close(); conn.close()
            return None, 0
        doc_id, clen = row
        cur.execute("""
            UPDATE chunking_queue SET status='in_progress', claimed_by=%s, claimed_at=NOW()
            WHERE document_id=%s
        """, (worker_id, doc_id))
        conn.commit()
        cur.close(); conn.close()
        return doc_id, clen
    except Exception as e:
        log(f"Claim error (W{worker_id}): {e}")
        return None, 0

def mark_done(doc_id, tokens_used: int):
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    cur.execute("UPDATE chunking_queue SET status='done', completed_at=NOW() WHERE document_id=%s", (doc_id,))
    conn.commit()
    cur.close(); conn.close()
    record_tokens(tokens_used)

def mark_failed(doc_id, reason: str):
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    cur.execute("""
        UPDATE chunking_queue SET status='failed', error_message=%s, completed_at=NOW()
        WHERE document_id=%s
    """, (reason[:500], doc_id))
    conn.commit()
    cur.close(); conn.close()

def reset_failed_to_pending():
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    cur.execute("UPDATE chunking_queue SET status='pending', claimed_by=NULL, claimed_at=NULL, error_message=NULL WHERE status IN ('failed','in_progress')")
    n = cur.rowcount
    conn.commit(); cur.close(); conn.close()
    return n

# ── Worker thread ─────────────────────────────────────────────────────────────
sys.path.insert(0, BASE_DIR)
from scripts.compute_embeddings import FullRAGPipeline, classify_document

def worker_thread(worker_id: int, stop_event: threading.Event):
    """Each worker loops: wait for headroom → claim a suitably-sized doc → chunk it."""
    chunker = FullRAGPipeline()
    chunker.ensure_schema()
    log(f"Worker {worker_id} ready.")

    while not stop_event.is_set():
        # Check both TPM and RPM headroom before claiming work
        if not can_send_request():
            time.sleep(5)
            continue

        # How many tokens can we afford right now?
        hr = headroom_tokens()
        # Cap: don't claim more chars than TPM headroom allows, but also hard-cap at 1_000_000 chars.
        # Documents larger than MAX_CLAIM_CHARS are split into sections by the chunker.
        MAX_CLAIM_CHARS = 1_000_000
        max_chars = min(max(1000, int(hr / 1.4 * 4) - 8000), MAX_CLAIM_CHARS)

        # Workers 15-20 specialize in "small" documents to maintain variety
        prefer_small = (worker_id >= 15)
        doc_id, clen = claim_doc_by_size(worker_id, max_chars, prefer_small=prefer_small)
        if doc_id is None:
            # Queue empty at this size — back off briefly
            time.sleep(10)
            continue

        est_tok = estimate_tokens(clen)
        record_request()   # Count this call toward RPM
        doc_name = doc_id
        try:
            conn = psycopg2.connect(DB_URL)
            cur  = conn.cursor()
            cur.execute("SELECT name, content, meeting_id FROM documents WHERE id=%s", (doc_id,))
            row  = cur.fetchone()
            cur.close(); conn.close()
            if not row:
                mark_failed(doc_id, "Document not found")
                continue
            name, content, meeting_id = row
            doc_name = name or doc_id
            doc_type = classify_document(name or "", content or "")
        except Exception as e:
            mark_failed(doc_id, str(e))
            continue

        log(f"W{worker_id} → {doc_name[:50]} ({clen:,}c / ~{est_tok:,}tok | TPM:{current_tpm():,}/{SAFE_TPM_LIMIT:,} | RPM:{current_rpm()}/{SAFE_RPM_LIMIT})")

        try:
            sections   = chunker._split_into_sections(content or "")
            all_chunks = []
            for s_idx, section in enumerate(sections):
                if not section.strip() or len(section.strip()) < 50:
                    continue
                info = f" (Section {s_idx+1}/{len(sections)})" if len(sections) > 1 else ""
                if len(sections) > 1:
                    log(f"W{worker_id}   ..processing section {s_idx+1}/{len(sections)}...")
                
                chunks = chunker._call_gemini_chunker(doc_type, section, info)
                if chunks:
                    all_chunks.extend(chunks)
                    if len(sections) > 1:
                        log(f"W{worker_id}   ..section {s_idx+1}/{len(sections)} done ({len(chunks)} chunks)")
                time.sleep(2)

            if not all_chunks:
                mark_failed(doc_id, "No chunks produced")
                log(f"W{worker_id} ⚠ No chunks — failed.")
                continue

            conn2 = psycopg2.connect(DB_URL)
            try:
                stored = chunker._store_chunks(doc_id, name or "", doc_type, meeting_id, all_chunks, conn2)
                mark_done(doc_id, est_tok)
                log(f"W{worker_id} ✓ {stored} chunks stored.")
            finally:
                conn2.close()

        except Exception as e:
            mark_failed(doc_id, str(e)[:500])
            log(f"W{worker_id} ❌ {e}")

        # Small sleep to avoid hammering DB after each doc
        time.sleep(3)

    log(f"Worker {worker_id} exiting.")

# ── Launch helper ─────────────────────────────────────────────────────────────
def wait_for_first_success():
    """Poll DB for a new timestamp in chunking_metadata indicating a real completion."""
    def get_latest():
        try:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            cur.execute("SELECT MAX(chunking_timestamp) FROM chunking_metadata")
            val = cur.fetchone()[0]
            cur.close(); conn.close()
            return val
        except: return None

    initial_ts = get_latest()
    while True:
        time.sleep(10)
        current_ts = get_latest()
        # If timestamp is brand new (greater than one we started with)
        if current_ts and (not initial_ts or current_ts > initial_ts):
            return True
    return False

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    log("═" * 60)
    log("Smart Controller — Gemini 2.5 Flash Lite | 4M TPM | ≤20 workers")
    log(f"TPM Limit: {TIER1_TPM:,} | Safe Ceiling: {SAFE_TPM_LIMIT:,} | Max Workers: {MAX_WORKERS}")
    log("═" * 60)

    n_reset = reset_failed_to_pending()
    log(f"Reset {n_reset} stale/failed items → pending")

    _, start_done, _, q = db_stats()
    pending = q.get('pending', 0)
    log(f"Queue: {pending:,} pending | {start_done} previously completed")

    stop_event = threading.Event()
    threads    = []
    active     = 0

    # Stagger-up: add first 10 workers immediately, then wait for success before adding others.
    for i in range(1, MAX_WORKERS + 1):
        t = threading.Thread(target=worker_thread, args=(i, stop_event), daemon=True, name=f"W{i}")
        t.start()
        threads.append(t)
        active += 1
        log(f"Worker {i} launched (active: {active})")

        # Launch W1-W10 immediately; gate W11+ behind success
        if i < 10:
            continue

        if i < MAX_WORKERS:
            log(f"Waiting for success before spawning worker {i+1}...")
            found = wait_for_first_success()
            _, new_done, _, _ = db_stats()
            start_done = new_done
            if found:
                log(f"✅ Success detected ({new_done} docs done). Adding next worker in 15s...")
            else:
                log(f"⚠ Timeout — adding next worker anyway.")
            time.sleep(15)  # brief cooldown between worker additions

    log(f"All {MAX_WORKERS} workers running. Monitoring...")

    # Main monitoring loop: 30-min reports
    last_status = 0
    while any(t.is_alive() for t in threads):
        now = time.time()
        if now - last_status >= STATUS_EVERY:
            pa, done, chunks, q = db_stats()
            tpm = current_tpm()
            rpm = current_rpm()
            total = sum(q.values())
            pct   = round(q.get('done', 0) / max(total, 1) * 100, 1)
            log(
                f"STATUS | Phase A: {pa}/17511 | "
                f"Chunked: {done} docs / {chunks} chunks | "
                f"Queue: {pct}% ({q.get('done',0)}/{total}) | "
                f"Failed: {q.get('failed',0)} | "
                f"TPM: {tpm:,}/{SAFE_TPM_LIMIT:,} | "
                f"RPM: {rpm}/{SAFE_RPM_LIMIT}"
            )
            last_status = now
        time.sleep(PROBE_INTERVAL)

    stop_event.set()
    log("All workers finished. Running mop-up...")

    # Mop-up: reset any failures and run a final gentle pass
    n = reset_failed_to_pending()
    if n > 0:
        log(f"Mop-up: re-queued {n} failed docs. Launching 2 gentle workers...")
        stop_event2 = threading.Event()
        mop_threads = [threading.Thread(target=worker_thread, args=(i, stop_event2), daemon=True) for i in (1, 2)]
        for t in mop_threads: t.start()
        for t in mop_threads: t.join()
        stop_event2.set()
        log("Mop-up complete.")

    log("═" * 60)
    log("Triggering Phase C: Agentic GraphRAG Build")
    proc = subprocess.Popen(
        [VENV_PY, "-u", "scripts/build_knowledge_graph.py"],
        stdout=open(os.path.join(LOG_DIR, "knowledge_graph.log"), "a"),
        stderr=subprocess.STDOUT, cwd=BASE_DIR
    )
    log(f"Phase C launched (PID {proc.pid}).")

if __name__ == "__main__":
    main()
