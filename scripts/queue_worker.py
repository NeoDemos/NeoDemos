"""
queue_worker.py
───────────────
A single queue worker that competes with other workers to process documents
from a shared Postgres work-queue table (chunking_queue).

Uses SELECT ... FOR UPDATE SKIP LOCKED so multiple workers never process
the same document. Safe to run N copies simultaneously.

Usage (auto-launched by launch_staggered.py):
    python3 -u scripts/queue_worker.py --worker-id 1
"""

import argparse
import os
import sys
import time
import psycopg2
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.compute_embeddings import FullRAGPipeline, classify_document

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
SLEEP_BETWEEN_DOCS = 45.0   # 10 workers × 45s cycle ≈ safe within 1M tokens/min
SLEEP_ON_EMPTY    = 15.0   # Polling delay when queue is empty


def log(worker_id: int, msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [W{worker_id:02d}] {msg}"
    print(line, flush=True)


def claim_next_doc(conn, worker_id: int):
    """Atomically claim the next unclaimed document from the queue."""
    cur = conn.cursor()
    cur.execute("""
        SELECT q.document_id, d.name, d.content, d.meeting_id
        FROM chunking_queue q
        JOIN documents d ON d.id = q.document_id
        WHERE q.status = 'pending'
        ORDER BY q.priority ASC, q.id ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    """)
    row = cur.fetchone()
    if row is None:
        cur.close()
        return None
    doc_id, name, content, meeting_id = row

    cur.execute("""
        UPDATE chunking_queue
        SET status = 'in_progress', claimed_by = %s, claimed_at = NOW()
        WHERE document_id = %s
    """, (worker_id, doc_id))
    conn.commit()
    cur.close()
    return doc_id, name, content, meeting_id


def mark_done(conn, doc_id):
    cur = conn.cursor()
    cur.execute("UPDATE chunking_queue SET status = 'done', completed_at = NOW() WHERE document_id = %s", (doc_id,))
    conn.commit()
    cur.close()


def mark_failed(conn, doc_id, reason: str):
    cur = conn.cursor()
    cur.execute("""
        UPDATE chunking_queue SET status = 'failed', error_message = %s, completed_at = NOW()
        WHERE document_id = %s
    """, (reason[:500], doc_id))
    conn.commit()
    cur.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-id", type=int, required=True)
    args = parser.parse_args()
    worker_id = args.worker_id

    log(worker_id, "Starting up ...")
    chunker = FullRAGPipeline()
    chunker.ensure_schema()

    consecutive_empty = 0

    while True:
        try:
            conn = psycopg2.connect(DB_URL)
        except Exception as e:
            log(worker_id, f"DB connect error: {e}. Retrying in 30s...")
            time.sleep(30)
            continue

        try:
            row = claim_next_doc(conn, worker_id)
            if row is None:
                conn.close()
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    log(worker_id, "Queue empty — checking again in 60s.")
                    consecutive_empty = 0
                time.sleep(SLEEP_ON_EMPTY)
                continue

            consecutive_empty = 0
            doc_id, name, content, meeting_id = row
            doc_type = classify_document(name or "", content or "")
            content_len = len(content or "")
            log(worker_id, f"Processing: {(name or '')[:60]} ({doc_type}, {content_len:,} chars)")

            conn.close()  # Release DB conn during long Gemini call

            # Chunk it
            try:
                sections = chunker._split_into_sections(content or "")
                all_chunks = []
                for s_idx, section in enumerate(sections):
                    if not section.strip() or len(section.strip()) < 50:
                        continue
                    section_info = f" (Section {s_idx+1}/{len(sections)})" if len(sections) > 1 else ""
                    chunks = chunker._call_gemini_chunker(doc_type, section, section_info)
                    if chunks:
                        all_chunks.extend(chunks)
                    time.sleep(2.0)  # Small inter-section pause

                if not all_chunks:
                    conn2 = psycopg2.connect(DB_URL)
                    mark_failed(conn2, doc_id, "No chunks produced after retries")
                    conn2.close()
                    log(worker_id, f"  ⚠ No chunks — marked FAILED.")
                else:
                    conn2 = psycopg2.connect(DB_URL)
                    try:
                        stored = chunker._store_chunks(doc_id, name or "", doc_type, meeting_id, all_chunks, conn2)
                        mark_done(conn2, doc_id)
                        log(worker_id, f"  ✓ {stored} chunks stored.")
                    finally:
                        conn2.close()

            except Exception as e:
                log(worker_id, f"  ❌ Unrecoverable error: {e}")
                try:
                    conn2 = psycopg2.connect(DB_URL)
                    mark_failed(conn2, doc_id, str(e)[:500])
                    conn2.close()
                except:
                    pass

        except Exception as e:
            log(worker_id, f"Outer loop error: {e}")
            try:
                conn.close()
            except:
                pass
            time.sleep(10)
            continue

        # Rate-limit guard between documents
        time.sleep(SLEEP_BETWEEN_DOCS)


if __name__ == "__main__":
    main()
