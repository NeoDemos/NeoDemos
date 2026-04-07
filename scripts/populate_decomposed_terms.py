"""
Populate decomposed_terms column for Dutch compound word BM25 matching.

Processes all document_chunks, decompounding long words using the OpenTaal
lexicon + domain terms. Stores decomposed parts in a text column that gets
added to the tsvector for BM25 search.

This is the definitive fix for the Dutch compound word problem:
- "leegstandsbelasting" → stores "leegstand belasting" in decomposed_terms
- Searching for "leegstand" now matches via the decomposed terms

Usage:
    python scripts/populate_decomposed_terms.py              # Full run
    python scripts/populate_decomposed_terms.py --limit 1000 # Test
    python scripts/populate_decomposed_terms.py --update-tsvector  # Also rebuild tsvector
"""

import sys
import time
import argparse
import logging
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_batch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.dutch_decompound import get_decompounder, decompound_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
BATCH_SIZE = 500


def populate(limit: int = None):
    """Populate decomposed_terms for all chunks."""
    log.info("Loading decompounder...")
    dc = get_decompounder()
    log.info(f"Lexicon: {len(dc.lexicon):,} words")

    conn = psycopg2.connect(DB_URL)

    processed = 0
    updated = 0
    t0 = time.time()
    last_id = 0
    total_limit = limit

    while True:
        # Keyset pagination: fast even on large tables
        read_cur = conn.cursor()
        sql = "SELECT id, content FROM document_chunks WHERE decomposed_terms IS NULL AND id > %s ORDER BY id LIMIT %s"
        fetch_size = min(BATCH_SIZE, total_limit - processed) if total_limit else BATCH_SIZE
        if fetch_size <= 0:
            break
        read_cur.execute(sql, (last_id, fetch_size))
        rows = read_cur.fetchall()
        read_cur.close()

        if not rows:
            break

        batch = []
        for chunk_id, content in rows:
            terms = decompound_text(content or "", min_word_len=8)
            batch.append((terms or "", chunk_id))
            processed += 1
            if terms:
                updated += 1
            last_id = chunk_id

        write_cur = conn.cursor()
        execute_batch(write_cur, "UPDATE document_chunks SET decomposed_terms = %s WHERE id = %s", batch)
        conn.commit()
        write_cur.close()

        elapsed = time.time() - t0
        rate = processed / elapsed if elapsed > 0 else 0
        log.info(f"Processed: {processed:,} | Updated: {updated:,} | Rate: {rate:.0f}/s | Last ID: {last_id}")

    elapsed = time.time() - t0
    log.info(f"Done: {processed:,} processed, {updated:,} with decomposed terms in {elapsed:.0f}s")

    # Note: text_search is a GENERATED column defined as:
    #   to_tsvector('dutch', title || ' ' || content || ' ' || decomposed_terms)
    # So it auto-updates whenever decomposed_terms is written — no manual update needed.

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    populate(limit=args.limit)
