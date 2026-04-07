"""
Retrieval-level metrics computed from QueryTrace — no LLM calls needed.

Inspired by RAGAS context_precision / context_recall and TruLens context_relevance.
"""

from __future__ import annotations

import math
from typing import Dict, Optional

from eval.instrumentation.tracer import QueryTrace


def context_precision(trace: QueryTrace) -> float:
    """
    Fraction of final chunks with a positive reranker score (> 0.0).
    If reranker was skipped, uses vector similarity > score_threshold.
    Returns 0.0-1.0.
    """
    if not trace.final_chunks:
        return 0.0

    if trace.reranker_skipped:
        threshold = trace.config_snapshot.get("score_threshold", 0.15)
        relevant = sum(1 for c in trace.final_chunks if c.score > threshold)
    else:
        # Jina v3 reranker: positive score = relevant, > 0.0 is a good threshold
        relevant = sum(1 for c in trace.final_chunks if c.score > 0.0)

    return relevant / len(trace.final_chunks)


def retrieval_diversity(trace: QueryTrace) -> Dict[str, float]:
    """
    How diverse are the retrieved chunks?
    Returns document diversity ratio and Shannon entropy over document IDs.
    """
    if not trace.final_chunks:
        return {"document_diversity": 0.0, "entropy": 0.0}

    doc_ids = [c.metadata.get("document_id") for c in trace.final_chunks]
    n = len(doc_ids)
    unique = len(set(doc_ids))

    # Shannon entropy
    freq: Dict[str, int] = {}
    for d in doc_ids:
        freq[d] = freq.get(d, 0) + 1

    entropy = 0.0
    for count in freq.values():
        p = count / n
        if p > 0:
            entropy -= p * math.log2(p)

    return {
        "document_diversity": unique / n,
        "unique_documents": unique,
        "total_chunks": n,
        "entropy": round(entropy, 3),
    }


def temporal_accuracy(
    trace: QueryTrace,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, float]:
    """
    For date-filtered queries: what fraction of final chunks fall within range?
    Chunks without start_date are counted as 'unknown'.
    """
    if not date_from and not date_to:
        return {"applicable": False, "in_range_ratio": 1.0}

    if not trace.final_chunks:
        return {"applicable": True, "in_range_ratio": 0.0, "total": 0}

    # We check start_date from the original RetrievedChunk metadata
    # The trace stores content_preview but we can check via vector/keyword results
    in_range = 0
    unknown = 0
    out_of_range = 0

    # Build a lookup from chunk_id -> start_date from vector + keyword results
    # (final_chunks only have metadata.document_id, not start_date)
    # We'll use what's available
    for chunk in trace.final_chunks:
        # The StageResult metadata doesn't carry start_date yet;
        # this is a known limitation — we count all as 'unknown' for now
        # and rely on the full trace data for deeper analysis.
        unknown += 1

    total = len(trace.final_chunks)
    return {
        "applicable": True,
        "in_range_ratio": in_range / total if total > 0 else 0.0,
        "in_range": in_range,
        "out_of_range": out_of_range,
        "unknown_date": unknown,
        "total": total,
    }


def compute_retrieval_metrics(
    trace: QueryTrace,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, any]:
    """Compute all retrieval metrics for a single trace."""
    return {
        "context_precision": round(context_precision(trace), 3),
        "diversity": retrieval_diversity(trace),
        "temporal": temporal_accuracy(trace, date_from, date_to),
    }
