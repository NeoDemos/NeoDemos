"""
Component-level diagnostics computed from QueryTrace.
Identifies whether vector search, keyword search, RRF, or reranker
is the bottleneck (or the hero).
"""

from __future__ import annotations

from typing import Dict, Any

from eval.instrumentation.tracer import QueryTrace


def vector_keyword_contribution(trace: QueryTrace) -> Dict[str, Any]:
    """
    For each final chunk, determine if it came from:
    - vector search only
    - keyword search only
    - both (overlap)
    """
    vec_ids = trace.vector_chunk_ids
    kw_ids = trace.keyword_chunk_ids
    final_ids = trace.final_chunk_ids

    if not final_ids:
        return {
            "vector_only_pct": 0.0,
            "keyword_only_pct": 0.0,
            "both_pct": 0.0,
            "neither_pct": 0.0,
        }

    vector_only = 0
    keyword_only = 0
    both = 0
    neither = 0

    for cid in final_ids:
        in_vec = cid in vec_ids
        in_kw = cid in kw_ids
        if in_vec and in_kw:
            both += 1
        elif in_vec:
            vector_only += 1
        elif in_kw:
            keyword_only += 1
        else:
            neither += 1

    n = len(final_ids)
    return {
        "vector_only_pct": round(vector_only / n, 3),
        "keyword_only_pct": round(keyword_only / n, 3),
        "both_pct": round(both / n, 3),
        "neither_pct": round(neither / n, 3),
        "vector_only": vector_only,
        "keyword_only": keyword_only,
        "both": both,
        "total": n,
    }


def reranker_impact(trace: QueryTrace) -> Dict[str, Any]:
    """
    How much did the reranker change the ordering?
    - Mean absolute rank change
    - Count of promotions (moved up 5+) and demotions (moved down 5+)
    """
    if trace.reranker_skipped or not trace.reranker_results:
        return {"skipped": True}

    # Sort by reranker score descending to get post-rerank positions
    sorted_entries = sorted(
        trace.reranker_results, key=lambda e: e.reranker_score, reverse=True
    )
    post_rank = {e.chunk_id: i for i, e in enumerate(sorted_entries)}

    deltas = []
    promotions = 0
    demotions = 0

    for entry in trace.reranker_results:
        pre = entry.pre_rerank_position
        post = post_rank.get(entry.chunk_id, pre)
        delta = pre - post  # positive = promoted (moved up)
        deltas.append(abs(delta))
        if delta >= 5:
            promotions += 1
        elif delta <= -5:
            demotions += 1

    avg_delta = sum(deltas) / len(deltas) if deltas else 0.0

    return {
        "skipped": False,
        "avg_position_change": round(avg_delta, 2),
        "promotions_5plus": promotions,
        "demotions_5plus": demotions,
        "candidates_scored": len(trace.reranker_results),
    }


def rrf_effectiveness(trace: QueryTrace) -> Dict[str, Any]:
    """
    Did RRF fusion add value over single-source retrieval?
    Counts chunks in the final result that wouldn't appear in either
    vector-only or keyword-only top-k.
    """
    top_k = trace.config_snapshot.get("top_k", 10)
    vec_ids = trace.vector_chunk_ids
    kw_ids = trace.keyword_chunk_ids

    # Simulate single-source top-k
    vec_top = {r.chunk_id for r in trace.vector_results[:top_k]}
    kw_top = {r.chunk_id for r in trace.keyword_results[:top_k]}
    single_source_top = vec_top | kw_top

    # Final chunks unique to fusion
    final_ids = trace.final_chunk_ids
    unique_from_fusion = final_ids - single_source_top

    if not final_ids:
        return {"unique_from_fusion": 0, "unique_pct": 0.0}

    return {
        "unique_from_fusion": len(unique_from_fusion),
        "unique_pct": round(len(unique_from_fusion) / len(final_ids), 3),
        "vector_top_k_overlap": len(final_ids & vec_top),
        "keyword_top_k_overlap": len(final_ids & kw_top),
    }


def compute_component_metrics(trace: QueryTrace) -> Dict[str, Any]:
    """Compute all component metrics for a single trace."""
    return {
        "contribution": vector_keyword_contribution(trace),
        "reranker": reranker_impact(trace),
        "rrf": rrf_effectiveness(trace),
        "timings": trace.timings,
    }
