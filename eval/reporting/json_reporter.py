"""
JSON reporter: writes structured results for each evaluation run.

Output structure per run:
  eval/runs/{run_id}/
    config.json    — tunable parameters used
    traces.json    — full pipeline trace per question
    scores.json    — all metrics per question with judge reasoning
    summary.json   — aggregates per category + overall + component health
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any, Dict, List


def save_config(run_dir: Path, config: Dict[str, Any]):
    (run_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def save_traces(run_dir: Path, traces: List[Dict]):
    (run_dir / "traces.json").write_text(
        json.dumps(traces, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def save_scores(run_dir: Path, scores: List[Dict]):
    (run_dir / "scores.json").write_text(
        json.dumps(scores, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def compute_and_save_summary(
    run_dir: Path,
    results: List[Dict],
    config: Dict[str, Any],
) -> Dict:
    """Aggregate per-category and overall scores, save as summary.json."""
    by_category: Dict[str, List[Dict]] = {}
    for r in results:
        cat = r.get("category", "unknown")
        by_category.setdefault(cat, []).append(r)

    category_summaries = {}
    for cat, items in sorted(by_category.items()):
        category_summaries[cat] = _aggregate_category(items)

    overall = _aggregate_category(results)

    # Component health across all traces
    component_health = _aggregate_component_health(results)

    summary = {
        "run_id": run_dir.name,
        "total_questions": len(results),
        "overall": overall,
        "by_category": category_summaries,
        "component_health": component_health,
    }

    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _aggregate_category(items: List[Dict]) -> Dict:
    """Compute mean scores for a list of question results."""
    agg: Dict[str, List[float]] = {}

    for item in items:
        retrieval = item.get("retrieval_metrics", {})
        if "context_precision" in retrieval:
            agg.setdefault("context_precision", []).append(retrieval["context_precision"])

        generation = item.get("generation_metrics", {})
        for metric_name, metric_data in generation.items():
            if isinstance(metric_data, dict) and "score" in metric_data:
                agg.setdefault(metric_name, []).append(metric_data["score"])

        # Hallucination metrics
        hallucination = item.get("hallucination_metrics", {})
        cv = hallucination.get("claim_verification", {})
        if "hallucination_rate" in cv and cv["hallucination_rate"] >= 0:
            agg.setdefault("hallucination_rate", []).append(cv["hallucination_rate"])
        sa = hallucination.get("source_attribution", {})
        if isinstance(sa, dict) and "score" in sa:
            agg.setdefault("source_attribution", []).append(sa["score"])

    result = {"count": len(items)}
    for key, values in agg.items():
        result[key] = {
            "mean": round(statistics.mean(values), 2) if values else 0,
            "min": min(values) if values else 0,
            "max": max(values) if values else 0,
        }
    return result


def _aggregate_component_health(results: List[Dict]) -> Dict:
    """Aggregate component-level metrics across all questions."""
    vec_pcts = []
    kw_pcts = []
    both_pcts = []
    reranker_deltas = []
    rrf_unique_pcts = []
    total_ms_list = []

    for r in results:
        comp = r.get("component_metrics", {})

        contrib = comp.get("contribution", {})
        if "vector_only_pct" in contrib:
            vec_pcts.append(contrib["vector_only_pct"])
            kw_pcts.append(contrib["keyword_only_pct"])
            both_pcts.append(contrib["both_pct"])

        reranker = comp.get("reranker", {})
        if not reranker.get("skipped") and "avg_position_change" in reranker:
            reranker_deltas.append(reranker["avg_position_change"])

        rrf = comp.get("rrf", {})
        if "unique_pct" in rrf:
            rrf_unique_pcts.append(rrf["unique_pct"])

        timings = comp.get("timings", {})
        if "total_ms" in timings:
            total_ms_list.append(timings["total_ms"])

    def _safe_mean(lst):
        return round(statistics.mean(lst), 3) if lst else 0.0

    return {
        "avg_vector_only_pct": _safe_mean(vec_pcts),
        "avg_keyword_only_pct": _safe_mean(kw_pcts),
        "avg_both_pct": _safe_mean(both_pcts),
        "avg_reranker_position_change": _safe_mean(reranker_deltas),
        "avg_rrf_unique_pct": _safe_mean(rrf_unique_pcts),
        "avg_total_ms": _safe_mean(total_ms_list),
        "total_questions": len(results),
    }
