"""
build_queue.py
──────────────
Creates the `chunking_queue` table and populates it with every document
not yet in `chunking_metadata`. Idempotent — safe to re-run.

Ordering: Large docs first (most expensive → get started early for max throughput)
Priority: 1 = large (>100k chars), 2 = medium, 3 = small
"""

import psycopg2

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"

def main():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    # Create queue table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chunking_queue (
            id              SERIAL PRIMARY KEY,
            document_id     TEXT NOT NULL UNIQUE,
            status          TEXT NOT NULL DEFAULT 'pending',
            priority        INT  NOT NULL DEFAULT 2,
            claimed_by      INT,
            claimed_at      TIMESTAMPTZ,
            completed_at    TIMESTAMPTZ,
            error_message   TEXT,
            created_at      TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_queue_status ON chunking_queue(status, priority, id)")
    conn.commit()
    print("✓ chunking_queue table ready")

    # Insert all un-chunked documents
    cur.execute("""
        INSERT INTO chunking_queue (document_id, priority)
        SELECT d.id,
               CASE
                   WHEN length(d.content) < 20000  THEN 1
                   WHEN length(d.content) < 100000 THEN 2
                   ELSE 3
               END as priority
        FROM documents d
        WHERE d.content IS NOT NULL
          AND length(d.content) >= 50
          AND d.id NOT IN (SELECT document_id FROM chunking_metadata)
        ON CONFLICT (document_id) DO UPDATE SET status = 'pending' WHERE chunking_queue.status = 'in_progress'
    """)
    inserted = cur.rowcount
    conn.commit()
    print(f"✓ Inserted {inserted} documents into queue")

    cur.execute("SELECT status, count(*) FROM chunking_queue GROUP BY status ORDER BY status")
    rows = cur.fetchall()
    print("\nQueue summary:")
    for status, count in rows:
        print(f"  {status}: {count}")

    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
