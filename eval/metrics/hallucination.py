"""
Hallucination detection metrics for RAG evaluation.

Designed for high-trust environments (city councillors) where a single
fabricated claim can destroy trust in the entire system.

Two approaches:
  1. Claim verification: decompose answer into claims, verify each against context
  2. Source attribution: check that party/person attributions are correct

Both require an LLM judge call (more expensive than retrieval metrics, but
essential for trust-critical applications).
"""

from __future__ import annotations

import json
from typing import Dict, Optional

from eval.judge.claude_judge import LLMJudge


def compute_hallucination_metrics(
    judge: LLMJudge,
    question_text: str,
    answer: str,
    context: str,
    category: str = "",
) -> Dict[str, Dict]:
    """
    Run hallucination-focused metrics on a question-answer pair.

    Returns:
        {
            "claim_verification": {
                "claims": [...],
                "total_claims": int,
                "supported": int,
                "unsupported": int,
                "contradicted": int,
                "hallucination_rate": float,
                "most_dangerous_claim": str | None,
                "reasoning": str,
            },
            "source_attribution": {
                "score": int (0-5),
                "reasoning": str,
            },
        }
    """
    results = {}

    # Claim verification — always run for hallucination questions
    claims_result = judge.evaluate_claims(
        question=question_text,
        answer=answer,
        context=context,
    )
    results["claim_verification"] = claims_result

    # Source attribution — run for attribution-sensitive categories
    attribution_categories = {
        "hallucination_attribution",
        "hallucination_conflation",
        "party_stance",
        "balanced_view",
        "multi_hop",
    }
    if category in attribution_categories or category.startswith("hallucination_"):
        results["source_attribution"] = judge.evaluate_metric(
            "source_attribution",
            question_text,
            answer,
            context=context,
        )

    return results


def hallucination_rate_from_claims(claims_result: Dict) -> Optional[float]:
    """Extract hallucination rate from claim verification result. None = parse error."""
    if not claims_result or "hallucination_rate" not in claims_result:
        return None
    rate = claims_result["hallucination_rate"]
    if rate is None:
        return None
    return float(rate)


def count_dangerous_claims(claims_result: Dict) -> int:
    """Count unsupported + contradicted claims."""
    if not claims_result or "claims" not in claims_result:
        return 0
    return sum(
        1 for c in claims_result.get("claims", [])
        if c.get("verdict") in ("unsupported", "contradicted")
    )


def is_safe_for_councillors(claims_result: Dict, max_hallucination_rate: float = 0.1) -> bool:
    """
    Conservative safety check: is this answer safe to show to a city councillor?

    Default threshold: max 10% of claims can be unsupported/contradicted.
    For production, consider 0.0 (zero tolerance).
    """
    rate = hallucination_rate_from_claims(claims_result)
    if rate is None:
        return True  # Parse error = unknown, don't flag as unsafe
    return rate <= max_hallucination_rate
