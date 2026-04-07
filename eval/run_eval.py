"""
NeoDemos RAG Evaluation Pipeline — CLI Entry Point

Usage:
    python -m eval.run_eval --run-id "v1-baseline"
    python -m eval.run_eval --skip-generation --run-id "v1-retrieval-only"
    python -m eval.run_eval --category temporal --run-id "v1-temporal"
    python -m eval.run_eval --fast --run-id "v1-fast"
    python -m eval.run_eval --compare-with "v1-baseline" --run-id "v2-tuned"
    python -m eval.run_eval --top-k 15 --run-id "v1-topk15"
    python -m eval.run_eval --questions eval/data/questions_legacy.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load .env before any service imports
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from eval.config import EvalConfig
from eval.instrumentation.rag_wrapper import create_instrumented_rag
from eval.metrics.retrieval import compute_retrieval_metrics
from eval.metrics.component import compute_component_metrics
from eval.reporting import json_reporter
from eval.reporting.terminal_reporter import print_summary, print_question_details


def load_questions(path: str, category_filter: str = "") -> list:
    """Load and optionally filter questions by category."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if category_filter:
        data = [q for q in data if q.get("category") == category_filter]
    return data


def format_context(chunks: list) -> str:
    """Format retrieved chunks as readable context for the judge."""
    parts = []
    for i, chunk in enumerate(chunks, 1):
        title = getattr(chunk, "title", "Onbekend")
        content = getattr(chunk, "content", "")
        score = getattr(chunk, "similarity_score", 0)
        parts.append(
            f"[Bron {i} — {title} (score: {score:.3f})]\n{content}"
        )
    return "\n\n---\n\n".join(parts)


def generate_answer_with_gemini(
    question_text: str,
    context: str,
) -> str:
    """
    Generate an answer using the existing Gemini-based AI service.
    Falls back to context-only summary if Gemini is unavailable.
    """
    try:
        from services.ai_service import AIService
        import asyncio

        ai = AIService()
        if not ai.use_llm:
            return _simple_context_answer(context)

        prompt = (
            f"Beantwoord de volgende vraag op basis van de meegeleverde context.\n\n"
            f"Vraag: {question_text}\n\n"
            f"Context:\n{context}\n\n"
            f"Antwoord (in het Nederlands, gebaseerd op de context):"
        )

        loop = asyncio.new_event_loop()
        response = loop.run_until_complete(
            ai._call_gemini(prompt)
        )
        loop.close()
        return response or _simple_context_answer(context)

    except Exception as e:
        print(f"  [warn] Gemini generation failed: {e}")
        return _simple_context_answer(context)


def _simple_context_answer(context: str) -> str:
    """Fallback: return a structured summary of the context."""
    return f"[Geen LLM beschikbaar — Ruwe context weergegeven]\n\n{context[:3000]}"


_CATEGORY_INSTRUCTIONS = {
    "party_stance": (
        "De vraag gaat over het standpunt van een specifieke partij. "
        "Noem UITSLUITEND standpunten, citaten of acties die in de bronnen expliciet aan die partij worden toegeschreven. "
        "Als een bron geen duidelijk partijstandpunt noemt, zeg dat dan eerlijk. "
        "Verzin of interpoleer geen standpunten op basis van algemene politieke kennis."
    ),
    "broad_aggregation": (
        "De vraag vraagt om een VOLLEDIG overzicht. "
        "Noem ALLE afzonderlijke uitdagingen, kansen of aspecten die in de bronnen worden behandeld — ook als ze klein lijken. "
        "Structureer je antwoord met kopjes of opsommingstekens zodat elk aspect zichtbaar is. "
        "Meld ook hoeveel unieke bronnen je hebt gebruikt."
    ),
    "balanced_view": (
        "De vraag vraagt expliciet om ZOWEL positieve als negatieve punten. "
        "Zorg dat je antwoord beide perspectieven evenwichtig behandelt. "
        "Gebruik aparte secties voor voor- en nadelen."
    ),
    "multi_hop": (
        "De vraag vereist het combineren van informatie uit MEERDERE bronnen. "
        "Verbind expliciet de relevante feiten uit verschillende bronnen. "
        "Noem welke bron welk feit levert."
    ),
    "temporal": (
        "De vraag is tijdgebonden. Vermeld uitsluitend informatie uit de gevraagde periode. "
        "Als een bron buiten die periode valt, negeer die dan."
    ),
}


def _call_gemini_direct(question_text: str, context: str, category: str = "") -> str:
    """Call Gemini API directly for answer generation, with category-aware instructions."""
    try:
        import google.genai as genai
        import os

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return _simple_context_answer(context)

        client = genai.Client(api_key=api_key)

        extra = _CATEGORY_INSTRUCTIONS.get(category, "")
        category_line = f"\n\nExtra instructie: {extra}" if extra else ""

        prompt = (
            f"Je bent een expert op het gebied van de Rotterdamse gemeentepolitiek. "
            f"Beantwoord de volgende vraag uitsluitend op basis van de meegeleverde bronnen."
            f"{category_line}\n\n"
            f"Vraag: {question_text}\n\n"
            f"Bronnen:\n{context}\n\n"
            f"Antwoord (in het Nederlands):"
        )

        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash-lite",
                    contents=prompt,
                )
                if response.text:
                    return response.text
            except Exception as e:
                if attempt == 2:
                    raise
                import time as _time
                print(f"  [warn] Gemini attempt {attempt+1} failed: {e} — retrying...")
                _time.sleep(2)
        return _simple_context_answer(context)

    except Exception as e:
        print(f"  [warn] Gemini generation failed: {e}")
        return _simple_context_answer(context)


def run_evaluation(config: EvalConfig):
    """Main evaluation loop."""
    print(f"\n{'='*60}")
    print(f"  NeoDemos RAG Evaluation Pipeline")
    print(f"  Run ID: {config.run_id}")
    print(f"  Mode: {'retrieval-only' if config.skip_generation else 'full (retrieval + generation)'}")
    print(f"  Fast mode: {config.fast_mode}")
    print(f"  Top-k: {config.top_k}")
    print(f"{'='*60}\n")

    # Load questions
    questions = load_questions(config.questions_path, config.category_filter)
    if not questions:
        print("No questions found. Check your questions file and category filter.")
        return
    print(f"Loaded {len(questions)} questions from {config.questions_path}")

    # Initialize instrumented RAG
    print("Initializing RAG service (embedding model + Qdrant + PostgreSQL)...")
    rag = create_instrumented_rag(db_url=config.db_url)
    print("RAG service ready.\n")

    # Initialize judge if needed
    judge = None
    if not config.skip_generation:
        try:
            from eval.judge.claude_judge import create_judge
            judge = create_judge()
            print(f"Judge initialized: {judge.backend_name}")
        except Exception as e:
            print(f"[warn] Could not initialize judge: {e}")
            print("Falling back to retrieval-only mode.")
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

        print(f"\n[{i}/{len(questions)}] {qid} ({category})")
        print(f"  Q: {text[:80]}{'...' if len(text) > 80 else ''}")

        # --- Retrieval with trace ---
        chunks, trace = rag.retrieve_with_trace(
            question_id=qid,
            query_text=text,
            top_k=config.top_k,
            date_from=date_from,
            date_to=date_to,
            fast_mode=config.fast_mode,
            score_threshold=config.score_threshold,
            reranker_threshold=config.reranker_threshold,
        )
        all_traces.append(trace.to_dict())

        print(f"  Retrieved: {len(chunks)} chunks in {trace.total_ms:.0f}ms")
        print(f"  Vector: {len(trace.vector_results)} | Keyword: {len(trace.keyword_results)} | RRF: {len(trace.rrf_results)}")

        # --- Compute retrieval metrics ---
        retrieval_metrics = compute_retrieval_metrics(trace, date_from, date_to)
        component_metrics = compute_component_metrics(trace)
        print(f"  Precision: {retrieval_metrics['context_precision']:.2f}")

        # --- Generation metrics ---
        generation_metrics = {}
        answer = ""
        context = ""
        if not config.skip_generation and judge and chunks:
            context = format_context(chunks)

            # Generate answer via Gemini
            print(f"  Generating answer (Gemini)...")
            answer = _call_gemini_direct(text, context, category=category)
            print(f"  Answer: {answer[:100]}{'...' if len(answer) > 100 else ''}")

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

        # --- Hallucination metrics (claim-level verification) ---
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
            rate = cv.get("hallucination_rate")
            safe = is_safe_for_councillors(cv)
            print(f"  Claims: {cv.get('total_claims', 0)} total, "
                  f"{cv.get('supported', 0)} supported, "
                  f"{cv.get('unsupported', 0)} unsupported, "
                  f"{cv.get('contradicted', 0)} contradicted")
            if rate is not None:
                print(f"  Hallucination rate: {rate:.0%} — {'SAFE' if safe else 'UNSAFE'} for councillors")
            else:
                print(f"  Hallucination rate: PARSE ERROR (claims could not be extracted)")
            if cv.get("most_dangerous_claim"):
                print(f"  DANGEROUS: {cv['most_dangerous_claim'][:120]}")

            # DB-backed verification: check claims against actual chunk text
            claims = cv.get("claims", [])
            if claims:
                print(f"  DB verification (checking claims against PostgreSQL)...")
                from eval.metrics.db_verifier import verify_claims_against_db, compute_db_verification_summary
                verify_claims_against_db(claims, db_url=config.db_url)
                db_summary = compute_db_verification_summary(claims)
                hallucination_metrics["db_verification"] = db_summary
                print(f"  DB: {db_summary['db_confirmed']} confirmed, "
                      f"{db_summary['db_denied']} not found, "
                      f"{db_summary['db_unknown']} unknown")

            sa = hallucination_metrics.get("source_attribution", {})
            if sa:
                print(f"  Source attribution: {sa.get('score', '?')}/5")

        # --- Assemble result ---
        result = {
            "question_id": qid,
            "question_text": text,
            "category": category,
            "answer": answer[:2000] if answer else "",
            "retrieval_metrics": retrieval_metrics,
            "component_metrics": component_metrics,
            "generation_metrics": generation_metrics,
            "hallucination_metrics": hallucination_metrics,
            "chunks_retrieved": len(chunks),
            "total_ms": trace.total_ms,
        }
        all_results.append(result)

    elapsed = time.perf_counter() - t_start
    print(f"\n{'='*60}")
    print(f"  Evaluation complete: {len(all_results)} questions in {elapsed:.1f}s")
    print(f"{'='*60}")

    # --- Save results ---
    run_dir = config.run_dir()
    json_reporter.save_config(run_dir, config.snapshot())
    json_reporter.save_traces(run_dir, all_traces)
    json_reporter.save_scores(run_dir, all_results)
    summary = json_reporter.compute_and_save_summary(run_dir, all_results, config.snapshot())

    print(f"\nResults saved to: {run_dir}")

    # --- Terminal report ---
    print_summary(summary, config.run_id)

    try:
        from rich.console import Console
        console = Console()
        print_question_details(console, all_results)
    except ImportError:
        pass

    # --- Comparison mode ---
    if config.compare_with:
        print(f"\nComparing with: {config.compare_with}")
        from eval.reporting.comparator import compare_runs
        compare_runs(config.runs_dir, config.compare_with, config.run_id)


def main():
    parser = argparse.ArgumentParser(
        description="NeoDemos RAG Evaluation Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--run-id", default="", help="Label for this run (auto-generated if empty)")
    parser.add_argument("--questions", default="", help="Path to questions JSON file")
    parser.add_argument("--category", default="", help="Only evaluate questions in this category")
    parser.add_argument("--skip-generation", action="store_true", help="Skip LLM answer generation and judging")
    parser.add_argument("--fast", action="store_true", help="Skip CrossEncoder reranker (like MCP mode)")
    parser.add_argument("--top-k", type=int, default=10, help="Number of chunks to retrieve")
    parser.add_argument("--compare-with", default="", help="Run ID to compare against")
    parser.add_argument("--reranker-threshold", type=float, default=-2.0,
                        help="Min reranker score to keep a chunk (Jina v3: 0.0=relevant, -0.1=noise boundary)")
    parser.add_argument("--model", default="claude-sonnet-4-20250514", help="Claude model for judging")
    parser.add_argument("--hallucination", action="store_true",
                        help="Run claim-level hallucination verification (requires generation)")

    args = parser.parse_args()

    config = EvalConfig(
        run_id=args.run_id,
        category_filter=args.category,
        skip_generation=args.skip_generation,
        fast_mode=args.fast,
        top_k=args.top_k,
        compare_with=args.compare_with,
        anthropic_model=args.model,
        hallucination_mode=args.hallucination,
        reranker_threshold=args.reranker_threshold,
    )

    # Hallucination mode requires generation
    if config.hallucination_mode and config.skip_generation:
        print("[warn] --hallucination requires generation. Disabling --skip-generation.")
        config.skip_generation = False

    if args.questions:
        config.questions_path = args.questions

    run_evaluation(config)


if __name__ == "__main__":
    main()
