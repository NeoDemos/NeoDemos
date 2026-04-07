"""
Comparison tool: load two evaluation runs and diff scores.
Highlights regressions, improvements, and config changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Tuple

from eval.reporting.terminal_reporter import print_comparison


def load_run(runs_dir: str, run_id: str) -> Tuple[Dict, Dict, Dict]:
    """Load summary, scores, and config for a run."""
    run_path = Path(runs_dir) / run_id
    summary = json.loads((run_path / "summary.json").read_text(encoding="utf-8"))
    scores = json.loads((run_path / "scores.json").read_text(encoding="utf-8"))
    config = json.loads((run_path / "config.json").read_text(encoding="utf-8"))
    return summary, scores, config


def diff_configs(config_a: Dict, config_b: Dict) -> Dict[str, Tuple]:
    """Return keys where config changed between runs."""
    all_keys = set(config_a.keys()) | set(config_b.keys())
    changes = {}
    for k in all_keys:
        va = config_a.get(k)
        vb = config_b.get(k)
        if va != vb:
            changes[k] = (va, vb)
    return changes


def diff_scores(scores_a: list, scores_b: list, threshold: float = 1.0) -> Dict:
    """
    Compare per-question scores between two runs.
    Returns regressions (score dropped > threshold) and improvements.
    """
    map_a = {s["question_id"]: s for s in scores_a}
    map_b = {s["question_id"]: s for s in scores_b}

    regressions = []
    improvements = []

    all_ids = sorted(set(map_a.keys()) | set(map_b.keys()))

    for qid in all_ids:
        a = map_a.get(qid, {})
        b = map_b.get(qid, {})

        # Compare retrieval precision
        prec_a = a.get("retrieval_metrics", {}).get("context_precision", 0)
        prec_b = b.get("retrieval_metrics", {}).get("context_precision", 0)
        delta_prec = prec_b - prec_a

        # Compare generation scores
        gen_a = a.get("generation_metrics", {})
        gen_b = b.get("generation_metrics", {})

        deltas = {"precision_delta": round(delta_prec, 3)}
        for metric in ["answer_relevance", "faithfulness", "factual_correctness", "completeness"]:
            score_a = gen_a.get(metric, {}).get("score", 0) if gen_a else 0
            score_b = gen_b.get(metric, {}).get("score", 0) if gen_b else 0
            deltas[f"{metric}_delta"] = score_b - score_a

        entry = {
            "question_id": qid,
            "category": b.get("category", a.get("category", "?")),
            **deltas,
        }

        # Check for regression or improvement
        any_regression = any(v < -threshold for v in deltas.values())
        any_improvement = any(v > threshold for v in deltas.values())

        if any_regression:
            regressions.append(entry)
        if any_improvement:
            improvements.append(entry)

    return {
        "regressions": regressions,
        "improvements": improvements,
        "total_questions_compared": len(all_ids),
    }


def compare_runs(runs_dir: str, run_id_a: str, run_id_b: str):
    """Full comparison: load, diff, and print."""
    summary_a, scores_a, config_a = load_run(runs_dir, run_id_a)
    summary_b, scores_b, config_b = load_run(runs_dir, run_id_b)

    # Print config changes
    config_changes = diff_configs(config_a, config_b)
    if config_changes:
        print("\nConfig changes between runs:")
        for k, (va, vb) in config_changes.items():
            print(f"  {k}: {va} -> {vb}")

    # Print summary comparison
    print_comparison(summary_a, summary_b, run_id_a, run_id_b)

    # Print regressions/improvements
    score_diff = diff_scores(scores_a, scores_b)

    if score_diff["regressions"]:
        print(f"\nRegressions ({len(score_diff['regressions'])}):")
        for r in score_diff["regressions"]:
            print(f"  {r['question_id']} ({r['category']}): {r}")

    if score_diff["improvements"]:
        print(f"\nImprovements ({len(score_diff['improvements'])}):")
        for r in score_diff["improvements"]:
            print(f"  {r['question_id']} ({r['category']}): {r}")

    if not score_diff["regressions"] and not score_diff["improvements"]:
        print("\nNo significant score changes detected.")
