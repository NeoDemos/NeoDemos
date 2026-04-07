"""
Generation-level metrics via Claude-as-Judge.
Orchestrates judge calls and aggregates results.
"""

from __future__ import annotations

from typing import Dict, Optional

from eval.judge.claude_judge import LLMJudge


def compute_generation_metrics(
    judge: LLMJudge,
    question_text: str,
    answer: str,
    context: str,
    gold_answer: Optional[str] = None,
    category: str = "",
) -> Dict[str, Dict]:
    """
    Run all applicable judge metrics for one question-answer pair.
    Returns a dict of metric_name -> {"score": int, "reasoning": str}.
    """
    return judge.evaluate_all(
        question=question_text,
        answer=answer,
        context=context,
        gold_answer=gold_answer,
        category=category,
    )
