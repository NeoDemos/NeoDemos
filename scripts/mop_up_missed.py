"""
mop_up_missed.py
────────────────
Re-process every document that exists in `documents` but is MISSING from
`chunking_metadata`.  These are documents that the swarm skipped due to
429 RESOURCE_EXHAUSTED errors.

Runs with a single worker (no swarming) using the new exponential backoff
in _call_gemini_chunker so it won't trip the rate limit again.

Usage:
    python3 -u scripts/mop_up_missed.py 2>&1 | tee logs/mop_up.log
"""

import sys
import os

# Allow importing from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.compute_embeddings import DocumentChunker

import psycopg2
import time
from datetime import datetime

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"

def main():
    print("=" * 60)
    print("MOP-UP PASS: Recovering all missed documents")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 60)

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    # Find all documents NOT yet in chunking_metadata that have content
    cur.execute("""
        SELECT d.id, d.name, d.content, d.meeting_id
        FROM documents d
        WHERE d.content IS NOT NULL
          AND length(d.content) > 20
          AND d.id NOT IN (SELECT document_id FROM chunking_metadata)
        ORDER BY d.id
    """)
    missed = cur.fetchall()
    cur.close()
    conn.close()

    total = len(missed)
    print(f"Documents to re-process: {total}")
    if total == 0:
        print("✅ Nothing to mop up — all documents accounted for.")
        return

    # Reuse the full DocumentChunker (includes all backoff + JSON repair logic)
    chunker = DocumentChunker()
    chunker.ensure_schema()

    success = 0
    errors = 0

    for idx, (doc_id, doc_name, content, meeting_id) in enumerate(missed, 1):
        from scripts.compute_embeddings import classify_document
        doc_type = classify_document(doc_name or "", content or "")
        content_len = len(content or "")

        print(f"\n[{idx}/{total}] {(doc_name or '')[:70]} ({doc_type}, {content_len:,} chars)")

        try:
            sections = chunker._split_into_sections(content or "")
            if len(sections) > 1:
                print(f"  → Large doc: {len(sections)} sections")

            all_chunks = []
            for s_idx, section in enumerate(sections):
                if not section.strip() or len(section.strip()) < 50:
                    continue
                section_info = f" (Section {s_idx+1}/{len(sections)})" if len(sections) > 1 else ""
                chunks = chunker._call_gemini_chunker(doc_type, section, section_info)
                if chunks:
                    all_chunks.extend(chunks)
                # Small sleep between sections to avoid burst
                time.sleep(2.0)

            if not all_chunks:
                print("  ⚠ Still no chunks produced (non-429 reason). Skipping.")
                errors += 1
                continue

            conn2 = psycopg2.connect(DB_URL)
            try:
                stored = chunker._store_chunks(doc_id, doc_name or "", doc_type, meeting_id, all_chunks, conn2)
                print(f"  ✓ {stored} chunks recovered and stored.")
                success += 1
            finally:
                conn2.close()

        except Exception as e:
            errors += 1
            print(f"  ❌ Unrecoverable error: {e}")
            time.sleep(5)

        # Rate limit guard between documents (single worker = safe pace)
        time.sleep(4.0)

        if idx % 100 == 0:
            print(f"\n{'='*55}")
            print(f"PROGRESS: {idx}/{total} | Recovered: {success} | Errors: {errors}")
            print(f"{'='*55}\n")

    print(f"\n{'='*60}")
    print("MOP-UP COMPLETE")
    print(f"Recovered: {success} | Could not process: {errors}")
    print(f"Finished: {datetime.now().isoformat()}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
