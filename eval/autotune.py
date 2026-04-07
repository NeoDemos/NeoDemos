"""
Autoresearch-inspired iterative optimization for RAG retrieval.

Inspired by Karpathy's autoresearch: define a single metric, run experiments
with controlled parameter changes, compare results, keep improvements.

Usage:
    python -m eval.autotune --budget 5 --metric context_precision
    python -m eval.autotune --budget 3 --metric faithfulness --category temporal
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from eval.config import EvalConfig
from eval.run_eval import run_evaluation
from eval.reporting.comparator import load_run, diff_scores


# ---------------------------------------------------------------------------
# Experiment definitions: what to try when a category underperforms
# ---------------------------------------------------------------------------

EXPERIMENTS = [
    {
        "name": "increase-top-k-15",
        "description": "Increase retrieval pool to top_k=15 for broader coverage",
        "changes": {"top_k": 15},
        "targets": ["broad_aggregation", "balanced_view", "multi_hop"],
    },
    {
        "name": "increase-top-k-20",
        "description": "Increase retrieval pool to top_k=20 for maximum coverage",
        "changes": {"top_k": 20},
        "targets": ["broad_aggregation", "balanced_view", "multi_hop"],
    },
    {
        "name": "lower-score-threshold",
        "description": "Lower vector similarity threshold to 0.10 for more recall",
        "changes": {"score_threshold": 0.10},
        "targets": ["informal_opinion", "absence", "acronym_abbreviation"],
    },
    {
        "name": "fast-mode-comparison",
        "description": "Skip reranker to measure its contribution",
        "changes": {"fast_mode": True},
        "targets": ["all"],
    },
    {
        "name": "higher-top-k-lower-threshold",
        "description": "Combine more results with lower threshold",
        "changes": {"top_k": 15, "score_threshold": 0.10},
        "targets": ["all"],
    },
]


def identify_weakest_categories(summary: Dict, threshold: float = 0.7) -> List[str]:
    """Find categories where context_precision is below threshold."""
    weak = []
    for cat, data in summary.get("by_category", {}).items():
        prec = data.get("context_precision", {}).get("mean", 0)
        if prec < threshold:
            weak.append((cat, prec))
    return sorted(weak, key=lambda x: x[1])


def select_experiments(weak_categories: List[tuple]) -> List[Dict]:
    """Select experiments that target the weakest categories."""
    weak_names = {cat for cat, _ in weak_categories}
    selected = []

    for exp in EXPERIMENTS:
        targets = exp["targets"]
        if "all" in targets or any(t in weak_names for t in targets):
            selected.append(exp)

    return selected


def run_experiment(
    baseline_config: Dict,
    experiment: Dict,
    questions_path: str,
    category_filter: str = "",
) -> Optional[Dict]:
    """Run a single experiment and return its summary."""
    run_id = f"autotune_{experiment['name']}_{datetime.now().strftime('%H%M%S')}"

    config = EvalConfig(
        run_id=run_id,
        questions_path=questions_path,
        category_filter=category_filter,
        skip_generation=True,  # Retrieval-only for speed during tuning
    )

    # Apply experiment changes
    for key, value in experiment["changes"].items():
        if hasattr(config, key):
            setattr(config, key, value)

    print(f"\n{'='*60}")
    print(f"  EXPERIMENT: {experiment['name']}")
    print(f"  {experiment['description']}")
    print(f"  Changes: {experiment['changes']}")
    print(f"{'='*60}")

    try:
        run_evaluation(config)
        summary, _, _ = load_run(str(config.runs_dir), run_id)
        return {"run_id": run_id, "experiment": experiment, "summary": summary}
    except Exception as e:
        print(f"  FAILED: {e}")
        return None


def autotune(
    budget: int = 5,
    target_metric: str = "context_precision",
    questions_path: str = "",
    category_filter: str = "",
    baseline_run_id: str = "",
):
    """
    Autoresearch-inspired optimization loop.

    1. Run baseline (or load existing)
    2. Identify weakest categories
    3. Select experiments targeting those categories
    4. Run up to `budget` experiments
    5. Report best configuration found
    """
    questions_path = questions_path or str(
        Path(__file__).parent / "data" / "questions.json"
    )
    runs_dir = str(Path(__file__).parent / "runs")
    experiment_log = []

    print(f"\n{'#'*60}")
    print(f"  AUTOTUNE — Target: {target_metric}")
    print(f"  Budget: {budget} experiments")
    print(f"{'#'*60}")

    # --- Step 1: Baseline ---
    if baseline_run_id:
        print(f"\nLoading existing baseline: {baseline_run_id}")
        baseline_summary, _, baseline_config = load_run(runs_dir, baseline_run_id)
    else:
        print("\nRunning baseline...")
        baseline_config_obj = EvalConfig(
            run_id=f"autotune_baseline_{datetime.now().strftime('%H%M%S')}",
            questions_path=questions_path,
            category_filter=category_filter,
            skip_generation=True,
        )
        run_evaluation(baseline_config_obj)
        baseline_summary, _, baseline_config = load_run(
            runs_dir, baseline_config_obj.run_id
        )
        baseline_run_id = baseline_config_obj.run_id

    baseline_score = baseline_summary.get("overall", {}).get(
        target_metric, {}
    ).get("mean", 0)
    print(f"\nBaseline {target_metric}: {baseline_score:.3f}")

    # --- Step 2: Identify weaknesses ---
    weak = identify_weakest_categories(baseline_summary)
    if weak:
        print(f"\nWeakest categories:")
        for cat, score in weak:
            print(f"  {cat}: {score:.3f}")
    else:
        print("\nAll categories above threshold — trying general improvements.")

    # --- Step 3: Select and run experiments ---
    experiments = select_experiments(weak)[:budget]
    print(f"\nSelected {len(experiments)} experiments (budget: {budget})")

    best_run_id = baseline_run_id
    best_score = baseline_score
    best_experiment = None

    for i, exp in enumerate(experiments, 1):
        print(f"\n--- Experiment {i}/{len(experiments)} ---")
        result = run_experiment(
            baseline_config, exp, questions_path, category_filter,
        )

        if result is None:
            continue

        exp_score = result["summary"].get("overall", {}).get(
            target_metric, {}
        ).get("mean", 0)

        delta = exp_score - baseline_score
        status = "IMPROVEMENT" if delta > 0 else ("REGRESSION" if delta < 0 else "NO CHANGE")

        experiment_log.append({
            "experiment": exp["name"],
            "changes": exp["changes"],
            "score": exp_score,
            "delta": round(delta, 4),
            "status": status,
            "run_id": result["run_id"],
        })

        print(f"\n  Result: {target_metric} = {exp_score:.3f} ({delta:+.3f}) — {status}")

        if exp_score > best_score:
            best_score = exp_score
            best_run_id = result["run_id"]
            best_experiment = exp

    # --- Step 4: Report ---
    print(f"\n{'#'*60}")
    print(f"  AUTOTUNE COMPLETE")
    print(f"{'#'*60}")
    print(f"\nBaseline: {baseline_score:.3f}")
    print(f"Best:     {best_score:.3f} ({best_score - baseline_score:+.3f})")

    if best_experiment:
        print(f"Winner:   {best_experiment['name']}")
        print(f"Changes:  {best_experiment['changes']}")
    else:
        print("Winner:   Baseline (no improvement found)")

    print(f"\nExperiment log:")
    for entry in experiment_log:
        print(f"  {entry['experiment']}: {entry['score']:.3f} ({entry['delta']:+.3f}) — {entry['status']}")

    # Save experiment log
    log_path = Path(runs_dir) / f"autotune_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    log_path.write_text(json.dumps({
        "baseline_run_id": baseline_run_id,
        "baseline_score": baseline_score,
        "best_run_id": best_run_id,
        "best_score": best_score,
        "best_experiment": best_experiment,
        "target_metric": target_metric,
        "experiments": experiment_log,
    }, indent=2, ensure_ascii=False))
    print(f"\nLog saved: {log_path}")

    return best_run_id, best_score


def main():
    parser = argparse.ArgumentParser(description="Autoresearch-inspired RAG optimization")
    parser.add_argument("--budget", type=int, default=5, help="Max experiments to run")
    parser.add_argument("--metric", default="context_precision", help="Metric to optimize")
    parser.add_argument("--category", default="", help="Focus on specific category")
    parser.add_argument("--baseline", default="", help="Existing baseline run_id to compare against")
    parser.add_argument("--questions", default="", help="Questions file path")

    args = parser.parse_args()
    autotune(
        budget=args.budget,
        target_metric=args.metric,
        category_filter=args.category,
        baseline_run_id=args.baseline,
        questions_path=args.questions,
    )


if __name__ == "__main__":
    main()
