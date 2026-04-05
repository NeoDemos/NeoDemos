"""
Generates data/pipeline_state/chunking_work_plan.json

Categorises every document and existing chunk into one of:

  embed_directly    — chunk already in document_chunks, size ≤50K (ready for embedding)
  needs_resplit     — chunk already in document_chunks but >50K (must be deleted and
                       parent re-chunked; skipped by migrate_embeddings.py)
  ocr_first         — unchunked document, content <500 chars, has URL (OCR before chunk)
  chunk_atomic      — unchunked document, 500-1000 chars (single-chunk, no LLM needed)
  chunk_linear      — unchunked document, 1K-16K chars (Gemini 1-pass)
  chunk_hierarchical— unchunked document, >16K chars (multi-child Gemini split)

Usage:
    python scripts/build_chunking_work_plan.py
"""

import json
import os
import psycopg2
from datetime import datetime

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
OUT_FILE = "data/pipeline_state/chunking_work_plan.json"


def main():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    plan = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "summary": {},
        "needs_resplit": [],    # existing chunks >50K to delete + re-chunk
        "ocr_first": [],        # unchunked, <500 chars, has URL
        "chunk_atomic": [],     # unchunked, 500-1000 chars
        "chunk_linear": [],     # unchunked, 1K-16K
        "chunk_hierarchical": [],  # unchunked, >16K
    }

    # 1. Oversized existing chunks (>50K — skipped by migrate_embeddings.py)
    print("Scanning for oversized chunks (>50K)...")
    cur.execute("""
        SELECT dc.id, dc.document_id, dc.chunk_index, LENGTH(dc.content), dc.child_id
        FROM document_chunks dc
        WHERE LENGTH(dc.content) > 50000
        ORDER BY LENGTH(dc.content) DESC
    """)
    for row in cur.fetchall():
        plan["needs_resplit"].append({
            "chunk_id": row[0],
            "document_id": row[1],
            "chunk_index": row[2],
            "content_len": row[3],
            "child_id": row[4],
        })

    # 2. Unchunked documents
    print("Scanning unchunked documents...")
    cur.execute("""
        SELECT d.id, d.name, LENGTH(d.content), d.url, d.meeting_id
        FROM documents d
        WHERE NOT EXISTS (SELECT 1 FROM document_chunks dc WHERE dc.document_id = d.id)
        AND d.content IS NOT NULL AND LENGTH(d.content) > 10
        ORDER BY d.id
    """)
    for doc_id, name, clen, url, meeting_id in cur.fetchall():
        entry = {"id": doc_id, "name": (name or "")[:120], "content_len": clen}
        if clen < 500:
            entry["url"] = url
            plan["ocr_first"].append(entry)
        elif clen <= 1000:
            plan["chunk_atomic"].append(entry)
        elif clen <= 16000:
            plan["chunk_linear"].append(entry)
        else:
            plan["chunk_hierarchical"].append(entry)

    # 3. Summary counts (embed_directly is everything else in document_chunks)
    cur.execute("SELECT COUNT(*) FROM document_chunks WHERE LENGTH(content) <= 50000")
    embed_directly = cur.fetchone()[0]

    plan["summary"] = {
        "embed_directly": embed_directly,
        "needs_resplit": len(plan["needs_resplit"]),
        "ocr_first": len(plan["ocr_first"]),
        "chunk_atomic": len(plan["chunk_atomic"]),
        "chunk_linear": len(plan["chunk_linear"]),
        "chunk_hierarchical": len(plan["chunk_hierarchical"]),
        "total_unchunked_docs": (
            len(plan["ocr_first"]) + len(plan["chunk_atomic"]) +
            len(plan["chunk_linear"]) + len(plan["chunk_hierarchical"])
        ),
    }

    cur.close()
    conn.close()

    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)

    print(f"\nWork plan written to {OUT_FILE}")
    print(f"\n{'Category':<25} {'Count':>8}")
    print("-" * 35)
    for k, v in plan["summary"].items():
        print(f"{k:<25} {v:>8}")


if __name__ == "__main__":
    main()
