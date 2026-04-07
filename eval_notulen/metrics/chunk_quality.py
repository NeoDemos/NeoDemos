"""
Chunk Quality Metrics for Virtual Notulen

Verifies that the chunks stored in staging.document_chunks are well-formed
and ready for embedding. Runs entirely off PostgreSQL — no Qdrant needed.

Checks:
  - Length distribution (tiny / short / medium / long buckets)
  - Empty and near-empty chunks
  - Boilerplate-only chunks (just a heading, no substantive content)
  - Agenda item coverage (every agenda item has at least one chunk)
  - Duplicate detection (exact-match within this meeting's chunks)
  - Chunk type distribution (atomic / quote / recursive / structural)
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List


# ── Length buckets ────────────────────────────────────────────────────────────

BUCKET_TINY   = 50    # < 50 chars  — almost certainly useless
BUCKET_SHORT  = 200   # 50-200      — may be a heading or list item
BUCKET_MEDIUM = 2000  # 200-2000    — good RAG chunk size
BUCKET_LONG   = 5000  # 2000-5000   — acceptable but chunking may be too coarse
                      # > 5000      — too long, likely a chunking failure


def compute_length_distribution(chunks: List[Dict]) -> Dict:
    """Bucket chunks by character length and flag outliers."""
    if not chunks:
        return {}

    lengths = [len(c.get("content") or "") for c in chunks]
    total = len(lengths)

    buckets = Counter()
    for n in lengths:
        if n < BUCKET_TINY:
            buckets["tiny (<50)"] += 1
        elif n < BUCKET_SHORT:
            buckets["short (50-200)"] += 1
        elif n < BUCKET_MEDIUM:
            buckets["medium (200-2000)"] += 1
        elif n < BUCKET_LONG:
            buckets["long (2000-5000)"] += 1
        else:
            buckets["very_long (>5000)"] += 1

    empty_count = sum(1 for c in chunks if not (c.get("content") or "").strip())

    return {
        "total_chunks": total,
        "empty_chunks": empty_count,
        "buckets": dict(buckets),
        "min_chars": min(lengths),
        "max_chars": max(lengths),
        "avg_chars": round(sum(lengths) / total, 1),
        "median_chars": sorted(lengths)[total // 2],
        "tiny_rate": round(buckets["tiny (<50)"] / total, 4),
        "oversized_rate": round(buckets["very_long (>5000)"] / total, 4),
    }


# ── Boilerplate detection ─────────────────────────────────────────────────────

# Patterns that indicate a chunk contains only procedural/heading content
_BOILERPLATE_PATTERNS = [
    r"^\s*\d+[\.\)]\s*$",                     # Just a number
    r"^\s*[-–—]\s*$",                          # Just a dash
    r"^\s*(opening|sluiting|rondvraag|pauze)\s*$",  # Single procedural word
    r"^\s*agendapunt\s+\d+\s*$",               # "Agendapunt 3"
    r"^\s*(ja|nee|dank u|dank je)\s*\.?\s*$",  # Single acknowledgement
]
_BOILERPLATE_RE = re.compile("|".join(_BOILERPLATE_PATTERNS), re.IGNORECASE)

# Minimum ratio of alphabetic characters to total chars for substantive content
_MIN_ALPHA_RATIO = 0.5
# Minimum distinct word count for a useful chunk
_MIN_DISTINCT_WORDS = 8


def _is_boilerplate(text: str) -> bool:
    """Return True if the chunk text is likely boilerplate/procedural filler."""
    stripped = text.strip()
    if not stripped:
        return True
    if len(stripped) < BUCKET_TINY:
        return True
    if _BOILERPLATE_RE.match(stripped):
        return True
    # Low alphabetic content (e.g. mostly numbers/punctuation)
    alpha_chars = sum(1 for c in stripped if c.isalpha())
    if len(stripped) > 0 and alpha_chars / len(stripped) < _MIN_ALPHA_RATIO:
        return True
    # Too few distinct words
    words = set(w.lower() for w in re.findall(r"\b\w{3,}\b", stripped))
    if len(words) < _MIN_DISTINCT_WORDS:
        return True
    return False


def compute_boilerplate_rate(chunks: List[Dict]) -> Dict:
    """Identify boilerplate-only chunks that will add noise to the index."""
    if not chunks:
        return {}

    boilerplate_chunks = [
        {"id": c.get("id"), "title": c.get("title"), "preview": (c.get("content") or "")[:100]}
        for c in chunks
        if _is_boilerplate(c.get("content") or "")
    ]
    total = len(chunks)

    return {
        "total_chunks": total,
        "boilerplate_count": len(boilerplate_chunks),
        "boilerplate_rate": round(len(boilerplate_chunks) / total, 4),
        "boilerplate_examples": boilerplate_chunks[:5],
    }


# ── Agenda item coverage ──────────────────────────────────────────────────────

def compute_agenda_coverage_from_chunks(chunks: List[Dict], db_url: str,
                                         meeting_id: str) -> Dict:
    """
    Check that every staging document (= agenda item transcript) has at least
    one chunk. Documents with zero chunks indicate an ingestion failure.
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn = psycopg2.connect(db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT d.id, d.name,
                   COUNT(dc.id) AS chunk_count
            FROM staging.documents d
            LEFT JOIN staging.document_chunks dc ON dc.document_id = d.id
            WHERE d.meeting_id = %s
            GROUP BY d.id, d.name
            ORDER BY d.name
        """, (meeting_id,))
        docs = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
    except Exception as e:
        return {"status": "error", "message": str(e)}

    empty_docs = [d for d in docs if d["chunk_count"] == 0]

    return {
        "total_documents": len(docs),
        "documents_with_chunks": len(docs) - len(empty_docs),
        "empty_documents": len(empty_docs),
        "coverage_rate": round((len(docs) - len(empty_docs)) / len(docs), 4) if docs else 0.0,
        "documents": [{"name": d["name"], "chunks": d["chunk_count"]} for d in docs],
        "empty_document_names": [d["name"] for d in empty_docs],
    }


# ── Duplicate detection ───────────────────────────────────────────────────────

def compute_duplicate_rate(chunks: List[Dict]) -> Dict:
    """
    Detect exact-duplicate chunk content within this meeting.
    Near-duplicates are not checked (too expensive without embeddings).
    """
    if not chunks:
        return {}

    content_counts: Counter = Counter()
    for c in chunks:
        content = (c.get("content") or "").strip()
        if content:
            content_counts[content] += 1

    duplicates = {text: count for text, count in content_counts.items() if count > 1}
    duplicate_chunk_count = sum(count - 1 for count in duplicates.values())

    examples = [
        {"preview": text[:120], "count": count}
        for text, count in sorted(duplicates.items(), key=lambda x: -x[1])[:3]
    ]

    return {
        "total_chunks": len(chunks),
        "duplicate_groups": len(duplicates),
        "duplicate_chunk_count": duplicate_chunk_count,
        "duplicate_rate": round(duplicate_chunk_count / len(chunks), 4) if chunks else 0.0,
        "examples": examples,
    }


# ── Chunk type distribution ───────────────────────────────────────────────────

def compute_chunk_type_distribution(chunks: List[Dict]) -> Dict:
    """Count chunks by chunk_type (atomic, quote, recursive, structural)."""
    if not chunks:
        return {}

    type_counts: Counter = Counter(c.get("chunk_type") or "unknown" for c in chunks)
    total = len(chunks)

    return {
        "total": total,
        "distribution": {k: {"count": v, "rate": round(v / total, 4)}
                         for k, v in type_counts.most_common()},
    }


# ── Combined runner ───────────────────────────────────────────────────────────

def run_chunk_quality(chunks: List[Dict], db_url: str, meeting_id: str) -> Dict:
    """
    Run all chunk quality checks and return a combined result dict.

    Args:
        chunks: All chunks for this meeting from staging.document_chunks.
        db_url: PostgreSQL connection string.
        meeting_id: Staging meeting ID.
    """
    return {
        "length_distribution": compute_length_distribution(chunks),
        "boilerplate": compute_boilerplate_rate(chunks),
        "agenda_coverage": compute_agenda_coverage_from_chunks(chunks, db_url, meeting_id),
        "duplicates": compute_duplicate_rate(chunks),
        "chunk_types": compute_chunk_type_distribution(chunks),
    }
