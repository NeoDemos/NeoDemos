"""
NeoDemos RAG Evaluation Pipeline — v3 Architecture

Usage:
    python -m eval_v3.run_eval --run-id "v3-first"
    python -m eval_v3.run_eval --run-id "v3-first" --compare-with "v4-topk15-prompts"
    python -m eval_v3.run_eval --no-router --run-id "v3-ablation-no-router"
    python -m eval_v3.run_eval --no-map-reduce --run-id "v3-ablation-no-mr"
    python -m eval_v3.run_eval --force-strategy standard --run-id "v3-standard-only"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from eval_v3.config import V3EvalConfig
from eval_v3.instrumentation.rag_wrapper_v3 import create_v3_rag
from eval.metrics.retrieval import compute_retrieval_metrics
from eval.metrics.component import compute_component_metrics
from eval.reporting import json_reporter
from eval.reporting.terminal_reporter import print_summary, print_question_details

# Reuse v2 generation and judging
from eval.run_eval import (
    load_questions,
    format_context,
    _call_gemini_direct,
    _CATEGORY_INSTRUCTIONS,
)


def run_evaluation(config: V3EvalConfig):
    """Main v3 evaluation loop with query routing."""
    print(f"\n{'='*60}")
    print(f"  NeoDemos RAG Evaluation Pipeline — v3 Architecture")
    print(f"  Run ID: {config.run_id}")
    print(f"  Router: {'ON' if config.enable_router else 'OFF'}")
    print(f"  Map-reduce: {'ON' if config.enable_map_reduce else 'OFF'}")
    print(f"  Decomposition: {'ON' if config.enable_decomposition else 'OFF'}")
    print(f"  Force strategy: {config.force_strategy or 'auto'}")
    print(f"{'='*60}\n")

    # Load questions (same benchmark as v2)
    questions = load_questions(config.questions_path, config.category_filter)
    if not questions:
        print("No questions found.")
        return
    print(f"Loaded {len(questions)} questions from {config.questions_path}")

    # Initialize v3 RAG
    print("Initializing v3 RAG service (router + synthesizer + decomposer)...")
    rag = create_v3_rag(
        db_url=config.db_url,
        enable_router=config.enable_router,
        enable_map_reduce=config.enable_map_reduce,
        enable_decomposition=config.enable_decomposition,
    )
    print("v3 RAG service ready.\n")

    # Initialize judge
    judge = None
    if not config.skip_generation:
        try:
            from eval.judge.claude_judge import create_judge
            judge = create_judge()
            print(f"Judge initialized: {judge.backend_name}")
        except Exception as e:
            print(f"[warn] Could not initialize judge: {e}")
            config.skip_generation = True

    # Run evaluation
    all_results = []
    all_traces = []
    t_start = time.perf_counter()

    for i, q in enumerate(questions, 1):
        qid = q["id"]
        text = q["text"]
        category = q.get("category", "unknown")
        date_from = q.get("metadata", {}).get("date_from")
        date_to = q.get("metadata", {}).get("date_to")
        q_metadata = q.get("metadata", {})

        print(f"\n[{i}/{len(questions)}] {qid} ({category})")
        print(f"  Q: {text[:80]}{'...' if len(text) > 80 else ''}")

        # ── v3 Retrieval with routing ──
        chunks, trace, route, generated_answer = rag.retrieve_with_trace(
            question_id=qid,
            query_text=text,
            top_k=config.top_k,
            date_from=date_from,
            date_to=date_to,
            fast_mode=config.fast_mode,
            score_threshold=config.score_threshold,
            reranker_threshold=config.reranker_threshold,
            metadata=q_metadata,
            force_strategy=config.force_strategy,
        )
        all_traces.append(trace.to_dict())

        print(f"  Strategy: {route.strategy} (type={route.query_type}, confidence={route.confidence:.2f})")
        print(f"  Retrieved: {len(chunks)} chunks in {trace.timings.get('total_ms', 0):.0f}ms")

        # ── Compute retrieval metrics ──
        retrieval_metrics = compute_retrieval_metrics(trace, date_from, date_to)
        component_metrics = compute_component_metrics(trace)
        print(f"  Precision: {retrieval_metrics['context_precision']:.2f}")

        # ── Generation ──
        generation_metrics = {}
        answer = generated_answer  # May already be set by map_reduce or sub_query
        context = ""

        if not config.skip_generation and judge and chunks:
            context = format_context(chunks)

            # If answer wasn't produced by the strategy, use Gemini (standard path)
            if not answer:
                # Use Claude Sonnet for party_stance, Gemini for others
                if category == "party_stance":
                    answer = _call_claude_for_party_stance(text, context)
                else:
                    print(f"  Generating answer (Gemini)...")
                    answer = _call_gemini_direct(text, context, category=category)

            print(f"  Answer ({route.strategy}): {answer[:100]}{'...' if len(answer) > 100 else ''}")

            # Judge with Claude
            print(f"  Judging with Claude...")
            from eval.metrics.generation import compute_generation_metrics
            generation_metrics = compute_generation_metrics(
                judge=judge,
                question_text=text,
                answer=answer,
                context=context,
                gold_answer=q.get("gold_answer"),
                category=category,
            )

            for metric, data in generation_metrics.items():
                print(f"  {metric}: {data['score']}/5")

        # ── Hallucination metrics ──
        hallucination_metrics = {}
        if config.hallucination_mode and judge and answer and chunks:
            if not context:
                context = format_context(chunks)
            print(f"  Claim verification (hallucination check)...")
            from eval.metrics.hallucination import compute_hallucination_metrics, is_safe_for_councillors
            hallucination_metrics = compute_hallucination_metrics(
                judge=judge,
                question_text=text,
                answer=answer,
                context=context,
                category=category,
            )

            cv = hallucination_metrics.get("claim_verification", {})
            rate = cv.get("hallucination_rate", -1)
            safe = is_safe_for_councillors(cv)
            print(f"  Hallucination rate: {rate:.0%} — {'SAFE' if safe else 'UNSAFE'}")

            # DB verification
            claims = cv.get("claims", [])
            if claims:
                from eval.metrics.db_verifier import verify_claims_against_db, compute_db_verification_summary
                verify_claims_against_db(claims, db_url=config.db_url)
                db_summary = compute_db_verification_summary(claims)
                hallucination_metrics["db_verification"] = db_summary

        # ── Assemble result ──
        result = {
            "question_id": qid,
            "question_text": text,
            "category": category,
            "answer": answer[:2000] if answer else "",
            "route": route.to_dict(),
            "retrieval_metrics": retrieval_metrics,
            "component_metrics": component_metrics,
            "generation_metrics": generation_metrics,
            "hallucination_metrics": hallucination_metrics,
            "chunks_retrieved": len(chunks),
            "total_ms": trace.timings.get("total_ms", 0),
        }
        all_results.append(result)

    elapsed = time.perf_counter() - t_start
    print(f"\n{'='*60}")
    print(f"  v3 Evaluation complete: {len(all_results)} questions in {elapsed:.1f}s")
    print(f"{'='*60}")

    # ── Save results ──
    run_dir = config.run_dir()
    json_reporter.save_config(run_dir, config.snapshot())
    json_reporter.save_traces(run_dir, all_traces)
    json_reporter.save_scores(run_dir, all_results)
    summary = json_reporter.compute_and_save_summary(run_dir, all_results, config.snapshot())

    print(f"\nResults saved to: {run_dir}")

    # Terminal report
    print_summary(summary, config.run_id)
    try:
        from rich.console import Console
        console = Console()
        print_question_details(console, all_results)
    except ImportError:
        pass

    # Comparison
    if config.compare_with:
        print(f"\nComparing with: {config.compare_with}")
        # Check both v2 and v3 run dirs
        from eval.reporting.comparator import compare_runs
        v2_runs = str(PROJECT_ROOT / "eval" / "runs")
        v3_runs = config.runs_dir
        # Try v3 first, then v2
        for runs_dir in [v3_runs, v2_runs]:
            compare_dir = Path(runs_dir) / config.compare_with
            if compare_dir.exists():
                compare_runs(runs_dir, config.compare_with, config.run_id)
                break
        else:
            print(f"  [warn] Comparison run '{config.compare_with}' not found in v2 or v3 dirs")


def _call_claude_for_party_stance(question: str, context: str) -> str:
    """
    Use Claude Sonnet for party_stance generation (higher faithfulness).
    API-only.
    """
    import os
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _call_gemini_direct(question, context, category="party_stance")

    prompt = (
        f"Je bent een expert op het gebied van de Rotterdamse gemeentepolitiek.\n\n"
        f"Beantwoord de volgende vraag uitsluitend op basis van de meegeleverde bronnen.\n\n"
        f"Extra instructie: {_CATEGORY_INSTRUCTIONS['party_stance']}\n\n"
        f"Vraag: {question}\n\n"
        f"Bronnen:\n{context}\n\n"
        f"Antwoord (in het Nederlands):"
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as e:
        print(f"  [warn] Claude party_stance generation failed: {e}")
        return _call_gemini_direct(question, context, category="party_stance")


def main():
    parser = argparse.ArgumentParser(
        description="NeoDemos RAG Evaluation Pipeline — v3 Architecture",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--run-id", default="", help="Label for this run")
    parser.add_argument("--questions", default="", help="Path to questions JSON")
    parser.add_argument("--category", default="", help="Filter by category")
    parser.add_argument("--skip-generation", action="store_true")
    parser.add_argument("--fast", action="store_true", help="Skip reranker")
    parser.add_argument("--top-k", type=int, default=10, help="Base top-k (router may override)")
    parser.add_argument("--compare-with", default="", help="Run ID to compare against")
    parser.add_argument("--reranker-threshold", type=float, default=-2.0)
    parser.add_argument("--model", default="claude-sonnet-4-20250514")
    parser.add_argument("--hallucination", action="store_true")

    # v3-specific flags
    parser.add_argument("--no-router", action="store_true", help="Disable query router")
    parser.add_argument("--no-map-reduce", action="store_true", help="Disable map-reduce synthesis")
    parser.add_argument("--no-decomposition", action="store_true", help="Disable sub-query decomposition")
    parser.add_argument("--force-strategy", default="", choices=["", "standard", "party_filtered", "map_reduce", "sub_query"])

    args = parser.parse_args()

    config = V3EvalConfig(
        run_id=args.run_id,
        category_filter=args.category,
        skip_generation=args.skip_generation,
        fast_mode=args.fast,
        top_k=args.top_k,
        compare_with=args.compare_with,
        anthropic_model=args.model,
        hallucination_mode=args.hallucination,
        reranker_threshold=args.reranker_threshold,
        enable_router=not args.no_router,
        enable_map_reduce=not args.no_map_reduce,
        enable_decomposition=not args.no_decomposition,
        force_strategy=args.force_strategy,
    )

    if config.hallucination_mode and config.skip_generation:
        print("[warn] --hallucination requires generation. Disabling --skip-generation.")
        config.skip_generation = False

    if args.questions:
        config.questions_path = args.questions

    run_evaluation(config)


if __name__ == "__main__":
    main()
