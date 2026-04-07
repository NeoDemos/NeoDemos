"""
Rich terminal reporter for RAG evaluation results.

Three sections:
1. Category summary table (aggregate scores, color-coded)
2. Per-question breakdown (failures expanded)
3. Component health diagnostics
"""

from __future__ import annotations

from typing import Dict, List, Any

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


def _score_color(score: float, max_score: float = 5.0) -> str:
    """Return rich color name based on score threshold."""
    ratio = score / max_score if max_score > 0 else 0
    if ratio >= 0.8:
        return "green"
    elif ratio >= 0.6:
        return "yellow"
    return "red"


def _precision_color(p: float) -> str:
    if p >= 0.8:
        return "green"
    elif p >= 0.6:
        return "yellow"
    return "red"


def _fmt_score(val: float, max_val: float = 5.0) -> str:
    """Format score with color markup."""
    color = _score_color(val, max_val)
    return f"[{color}]{val:.1f}[/{color}]/{max_val:.0f}"


def _fmt_pct(val: float) -> str:
    color = _precision_color(val)
    return f"[{color}]{val:.2f}[/{color}]"


def print_summary(summary: Dict, run_id: str = ""):
    """Print the full evaluation report to terminal."""
    if not RICH_AVAILABLE:
        _print_fallback(summary, run_id)
        return

    console = Console()
    console.print()

    # --- Header ---
    title = f"NeoDemos RAG Evaluation — {run_id}" if run_id else "NeoDemos RAG Evaluation"
    console.print(Panel(
        f"[bold]{title}[/bold]\n"
        f"{summary.get('total_questions', 0)} questions evaluated",
        style="blue",
    ))

    # --- Section 1: Category Summary ---
    _print_category_table(console, summary)

    # --- Section 2: Component Health ---
    _print_component_health(console, summary.get("component_health", {}))

    console.print()


def print_question_details(console_or_none, results: List[Dict], threshold: float = 3.0):
    """Print per-question details, expanding failures."""
    if not RICH_AVAILABLE:
        return

    has_hal = any(r.get("hallucination_metrics") for r in results)

    console = console_or_none or Console()
    table = Table(title="Per-Question Details", show_lines=True)
    table.add_column("Status", width=6)
    table.add_column("ID", width=8)
    table.add_column("Category", width=20)
    table.add_column("Precision", width=10)
    table.add_column("Relevance", width=10)
    table.add_column("Faithful", width=10)
    if has_hal:
        table.add_column("Hal. Rate", width=10)
    table.add_column("Time (ms)", width=10)
    table.add_column("Notes", max_width=40)

    for r in results:
        ret = r.get("retrieval_metrics", {})
        gen = r.get("generation_metrics", {})
        comp = r.get("component_metrics", {})
        hal = r.get("hallucination_metrics", {})
        precision = ret.get("context_precision", 0)
        relevance = gen.get("answer_relevance", {}).get("score", 0) if gen else 0
        faithful = gen.get("faithfulness", {}).get("score", 0) if gen else 0
        total_ms = comp.get("timings", {}).get("total_ms", 0)

        cv = hal.get("claim_verification", {})
        hal_rate = cv.get("hallucination_rate", -1) if cv else -1

        # Determine status — hallucination rate overrides other scores
        if has_hal and hal_rate > 0.1:
            status = "[red bold]UNSAFE[/red bold]"
        else:
            scores = [s for s in [precision * 5, relevance, faithful] if s > 0]
            avg = sum(scores) / len(scores) if scores else 0
            if avg >= 4.0:
                status = "[green]PASS[/green]"
            elif avg >= threshold:
                status = "[yellow]WARN[/yellow]"
            else:
                status = "[red]FAIL[/red]"

        # Build notes
        notes = ""
        if has_hal and cv.get("most_dangerous_claim"):
            notes = cv["most_dangerous_claim"][:60]
        else:
            contrib = comp.get("contribution", {})
            if contrib.get("vector_only_pct", 0) > 0.8:
                notes = "Keyword search contributed little"
            elif contrib.get("keyword_only_pct", 0) > 0.8:
                notes = "Vector search contributed little"
            if gen and not gen.get("answer_relevance"):
                notes = "Generation skipped"

        row = [
            status,
            r.get("question_id", "?"),
            r.get("category", "?"),
            _fmt_pct(precision),
            _fmt_score(relevance) if gen else "[dim]N/A[/dim]",
            _fmt_score(faithful) if gen else "[dim]N/A[/dim]",
        ]
        if has_hal:
            row.append(_fmt_hal_rate(hal_rate))
        row.extend([f"{total_ms:.0f}", notes])

        table.add_row(*row)

    console.print(table)


def _hallucination_color(rate: float) -> str:
    """Color for hallucination rate — lower is better (inverted from scores)."""
    if rate <= 0.0:
        return "green"
    elif rate <= 0.1:
        return "yellow"
    return "red"


def _fmt_hal_rate(val: float) -> str:
    """Format hallucination rate as percentage with color."""
    if val < 0:
        return "[dim]—[/dim]"
    color = _hallucination_color(val)
    return f"[{color}]{val:.0%}[/{color}]"


def _print_category_table(console: "Console", summary: Dict):
    # Check if any category has hallucination data
    has_hal = any(
        "hallucination_rate" in data
        for data in summary.get("by_category", {}).values()
    )

    table = Table(title="Category Summary", show_lines=True)
    table.add_column("Category", style="bold", width=22)
    table.add_column("n", justify="right", width=4)
    table.add_column("Precision", justify="right", width=12)
    table.add_column("Relevance", justify="right", width=12)
    table.add_column("Faithful", justify="right", width=12)
    table.add_column("Correct", justify="right", width=12)
    table.add_column("Complete", justify="right", width=12)
    if has_hal:
        table.add_column("Hal. Rate", justify="right", width=10)
        table.add_column("Src Attr", justify="right", width=10)

    for cat, data in sorted(summary.get("by_category", {}).items()):
        n = data.get("count", 0)
        prec = data.get("context_precision", {}).get("mean", 0)
        rel = data.get("answer_relevance", {}).get("mean", 0)
        faith = data.get("faithfulness", {}).get("mean", 0)
        corr = data.get("factual_correctness", {}).get("mean", 0)
        comp = data.get("completeness", {}).get("mean", 0)

        row = [
            cat,
            str(n),
            _fmt_pct(prec) if prec else "[dim]—[/dim]",
            _fmt_score(rel) if rel else "[dim]—[/dim]",
            _fmt_score(faith) if faith else "[dim]—[/dim]",
            _fmt_score(corr) if corr else "[dim]—[/dim]",
            _fmt_score(comp) if comp else "[dim]—[/dim]",
        ]
        if has_hal:
            hal = data.get("hallucination_rate", {}).get("mean", -1)
            sa = data.get("source_attribution", {}).get("mean", 0)
            row.append(_fmt_hal_rate(hal))
            row.append(_fmt_score(sa) if sa else "[dim]—[/dim]")

        table.add_row(*row)

    # Overall row
    overall = summary.get("overall", {})
    overall_row = [
        "[bold]OVERALL[/bold]",
        str(overall.get("count", 0)),
        _fmt_pct(overall.get("context_precision", {}).get("mean", 0)),
        _fmt_score(overall.get("answer_relevance", {}).get("mean", 0)),
        _fmt_score(overall.get("faithfulness", {}).get("mean", 0)),
        _fmt_score(overall.get("factual_correctness", {}).get("mean", 0)),
        _fmt_score(overall.get("completeness", {}).get("mean", 0)),
    ]
    if has_hal:
        hal = overall.get("hallucination_rate", {}).get("mean", -1)
        sa = overall.get("source_attribution", {}).get("mean", 0)
        overall_row.append(_fmt_hal_rate(hal))
        overall_row.append(_fmt_score(sa) if sa else "[dim]—[/dim]")

    table.add_row(*overall_row, style="bold")

    console.print(table)


def _print_component_health(console: "Console", health: Dict):
    if not health:
        return

    lines = [
        f"  Vector-only contribution:  {health.get('avg_vector_only_pct', 0):.1%}",
        f"  Keyword-only contribution: {health.get('avg_keyword_only_pct', 0):.1%}",
        f"  Both (overlap):            {health.get('avg_both_pct', 0):.1%}",
        f"  RRF unique additions:      {health.get('avg_rrf_unique_pct', 0):.1%}",
        f"  Reranker avg rank change:  {health.get('avg_reranker_position_change', 0):.1f}",
        f"  Avg retrieval time:        {health.get('avg_total_ms', 0):.0f} ms",
    ]

    console.print(Panel("\n".join(lines), title="Component Health", style="cyan"))


def _print_fallback(summary: Dict, run_id: str):
    """Fallback when rich is not installed."""
    print(f"\n=== NeoDemos RAG Evaluation — {run_id} ===")
    print(f"Questions: {summary.get('total_questions', 0)}\n")

    overall = summary.get("overall", {})
    for key, data in overall.items():
        if isinstance(data, dict) and "mean" in data:
            print(f"  {key}: {data['mean']:.2f}")

    print("\nInstall 'rich' for colored terminal output: pip install rich")


def print_comparison(run_a: Dict, run_b: Dict, label_a: str, label_b: str):
    """Print side-by-side comparison of two run summaries."""
    if not RICH_AVAILABLE:
        print(f"Comparison: {label_a} vs {label_b}")
        print("Install 'rich' for formatted comparison.")
        return

    console = Console()
    console.print(Panel(
        f"[bold]Comparison: {label_a} vs {label_b}[/bold]",
        style="magenta",
    ))

    table = Table(title="Score Deltas", show_lines=True)
    table.add_column("Category", style="bold", width=22)
    table.add_column(f"Precision ({label_a})", justify="right", width=14)
    table.add_column(f"Precision ({label_b})", justify="right", width=14)
    table.add_column("Delta", justify="right", width=10)

    cats_a = run_a.get("by_category", {})
    cats_b = run_b.get("by_category", {})
    all_cats = sorted(set(cats_a.keys()) | set(cats_b.keys()))

    for cat in all_cats:
        prec_a = cats_a.get(cat, {}).get("context_precision", {}).get("mean", 0)
        prec_b = cats_b.get(cat, {}).get("context_precision", {}).get("mean", 0)
        delta = prec_b - prec_a
        delta_color = "green" if delta > 0 else ("red" if delta < 0 else "dim")
        delta_str = f"[{delta_color}]{delta:+.2f}[/{delta_color}]"

        table.add_row(cat, f"{prec_a:.2f}", f"{prec_b:.2f}", delta_str)

    console.print(table)
