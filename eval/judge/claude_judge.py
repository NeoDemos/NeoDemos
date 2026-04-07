"""
LLM-as-Judge for RAG evaluation.

Supports multiple backends:
  - Claude (Anthropic API) — preferred, strongest reasoning
  - Gemini (Google GenAI) — fallback, already available in project
  - Any OpenAI-compatible API

Evaluates each metric via a separate, focused prompt to avoid score conflation.
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, Optional

from eval.judge.prompts import PROMPTS, SYSTEM


def create_judge(backend: Optional[str] = None, model: Optional[str] = None) -> "LLMJudge":
    """
    Factory: auto-detect best available judge backend.

    Priority: ANTHROPIC_API_KEY → GEMINI_API_KEY → error.
    """
    if backend is None:
        if os.getenv("ANTHROPIC_API_KEY"):
            backend = "claude"
        elif os.getenv("GEMINI_API_KEY"):
            backend = "gemini"
        else:
            raise ValueError(
                "No judge API key found. Set ANTHROPIC_API_KEY or GEMINI_API_KEY."
            )

    if backend == "claude":
        return ClaudeJudge(model=model or "claude-sonnet-4-20250514")
    elif backend == "gemini":
        return GeminiJudge(model=model or "gemini-2.5-flash")
    else:
        raise ValueError(f"Unknown judge backend: {backend}")


class LLMJudge:
    """Base class for LLM-as-Judge implementations."""

    def evaluate_metric(
        self,
        metric: str,
        question: str,
        answer: str,
        context: str = "",
        gold_answer: str = "",
    ) -> Dict:
        raise NotImplementedError

    def evaluate_claims(
        self,
        question: str,
        answer: str,
        context: str = "",
    ) -> Dict:
        """
        Claim-level verification: decompose answer into claims, verify each.
        Returns structured result with per-claim verdicts and hallucination_rate.
        """
        raise NotImplementedError

    def evaluate_all(
        self,
        question: str,
        answer: str,
        context: str = "",
        gold_answer: Optional[str] = None,
        category: str = "",
    ) -> Dict[str, Dict]:
        """
        Evaluate all applicable metrics for a question-answer pair.
        Completeness is only scored for broad_aggregation, balanced_view, multi_hop.
        """
        results = {}

        results["answer_relevance"] = self.evaluate_metric(
            "answer_relevance", question, answer,
        )
        results["faithfulness"] = self.evaluate_metric(
            "faithfulness", question, answer, context=context,
        )
        if gold_answer:
            results["factual_correctness"] = self.evaluate_metric(
                "factual_correctness", question, answer, gold_answer=gold_answer,
            )
        if category in ("broad_aggregation", "balanced_view", "multi_hop"):
            results["completeness"] = self.evaluate_metric(
                "completeness", question, answer, context=context,
            )
        return results


def _build_prompt(metric: str, question: str, answer: str,
                  context: str, gold_answer: str) -> str:
    template = PROMPTS.get(metric)
    if template is None:
        raise ValueError(f"Unknown metric: {metric}. Available: {list(PROMPTS.keys())}")
    return template.format(
        question=question,
        answer=answer,
        context=context or "Geen context aangeleverd.",
        gold_answer=gold_answer or "Niet beschikbaar — beoordeel op eigen kennis.",
    )


def _parse_json_score(text: str) -> Dict:
    """Parse JSON score from LLM response, handling markdown fences."""
    clean = _strip_markdown_fences(text)
    parsed = json.loads(clean)
    return {
        "score": int(parsed.get("score", 0)),
        "reasoning": parsed.get("reasoning", ""),
    }


def _parse_claims_response(text: str) -> Dict:
    """Parse structured claim verification response from LLM."""
    clean = _strip_markdown_fences(text)

    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError:
        # LLM sometimes produces malformed JSON with unescaped quotes/newlines
        # in Dutch text. Try to fix common issues:
        import re
        # Replace literal newlines inside JSON strings
        fixed = re.sub(r'(?<=": ")(.*?)(?="[,\}])', lambda m: m.group(0).replace('\n', ' '), clean, flags=re.DOTALL)
        try:
            parsed = json.loads(fixed)
        except json.JSONDecodeError:
            # Last resort: extract what we can with regex
            supported = len(re.findall(r'"verdict"\s*:\s*"supported"', clean))
            unsupported = len(re.findall(r'"verdict"\s*:\s*"unsupported"', clean))
            contradicted = len(re.findall(r'"verdict"\s*:\s*"contradicted"', clean))
            total = supported + unsupported + contradicted
            return {
                "claims": [],
                "total_claims": total,
                "supported": supported,
                "unsupported": unsupported,
                "contradicted": contradicted,
                "hallucination_rate": round((unsupported + contradicted) / total, 2) if total > 0 else 0.0,
                "most_dangerous_claim": None,
                "reasoning": "Parsed via regex fallback (malformed JSON from judge)",
            }

    claims = parsed.get("claims", [])
    total = parsed.get("total_claims", len(claims))
    supported = parsed.get("supported", 0)
    unsupported = parsed.get("unsupported", 0)
    contradicted = parsed.get("contradicted", 0)

    # Recompute hallucination rate for safety (don't trust LLM arithmetic)
    if total > 0:
        hallucination_rate = round((unsupported + contradicted) / total, 2)
    else:
        hallucination_rate = 0.0

    return {
        "claims": claims,
        "total_claims": total,
        "supported": supported,
        "unsupported": unsupported,
        "contradicted": contradicted,
        "hallucination_rate": hallucination_rate,
        "most_dangerous_claim": parsed.get("most_dangerous_claim"),
        "reasoning": parsed.get("reasoning", ""),
    }


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences from LLM JSON responses."""
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[-1]
    if clean.endswith("```"):
        clean = clean.rsplit("```", 1)[0]
    return clean.strip()


# ---------------------------------------------------------------------------
# Claude backend
# ---------------------------------------------------------------------------

class ClaudeJudge(LLMJudge):
    """Scores via Anthropic Claude API."""

    def __init__(self, model: str = "claude-sonnet-4-20250514"):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self._call_count = 0
        self.backend_name = f"Claude ({model})"

    def evaluate_metric(self, metric, question, answer, context="", gold_answer=""):
        prompt = _build_prompt(metric, question, answer, context, gold_answer)

        if self._call_count > 0:
            time.sleep(0.3)
        self._call_count += 1

        try:
            import anthropic
            response = self.client.messages.create(
                model=self.model,
                max_tokens=512,
                temperature=0.0,
                system=SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            return _parse_json_score(response.content[0].text)
        except (json.JSONDecodeError, KeyError) as e:
            return {"score": 0, "reasoning": f"Parse error: {e}"}
        except Exception as e:
            return {"score": 0, "reasoning": f"API error: {e}"}

    def evaluate_claims(self, question, answer, context=""):
        prompt = _build_prompt("claim_verification", question, answer, context, "")

        if self._call_count > 0:
            time.sleep(0.3)
        self._call_count += 1

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,  # Claims response is larger than simple scores
                temperature=0.0,
                system=SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            return _parse_claims_response(response.content[0].text)
        except (json.JSONDecodeError, KeyError) as e:
            return {"claims": [], "total_claims": 0, "supported": 0,
                    "unsupported": 0, "contradicted": 0,
                    "hallucination_rate": None,
                    "parsing_error": str(e),
                    "reasoning": f"Parse error: {e}"}
        except Exception as e:
            return {"claims": [], "total_claims": 0, "supported": 0,
                    "unsupported": 0, "contradicted": 0,
                    "hallucination_rate": None,
                    "reasoning": f"API error: {e}"}


# ---------------------------------------------------------------------------
# Gemini backend
# ---------------------------------------------------------------------------

class GeminiJudge(LLMJudge):
    """Scores via Google Gemini API."""

    def __init__(self, model: str = "gemini-2.5-flash"):
        import google.genai as genai
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = model
        self._call_count = 0
        self.backend_name = f"Gemini ({model})"

    def evaluate_metric(self, metric, question, answer, context="", gold_answer=""):
        prompt = f"{SYSTEM}\n\n{_build_prompt(metric, question, answer, context, gold_answer)}"

        if self._call_count > 0:
            time.sleep(0.3)
        self._call_count += 1

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config={"temperature": 0.0, "response_mime_type": "application/json"},
            )
            return _parse_json_score(response.text)
        except (json.JSONDecodeError, KeyError) as e:
            return {"score": 0, "reasoning": f"Parse error: {e}"}
        except Exception as e:
            return {"score": 0, "reasoning": f"API error: {e}"}

    def evaluate_claims(self, question, answer, context=""):
        prompt = f"{SYSTEM}\n\n{_build_prompt('claim_verification', question, answer, context, '')}"

        if self._call_count > 0:
            time.sleep(0.3)
        self._call_count += 1

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config={"temperature": 0.0, "response_mime_type": "application/json"},
            )
            return _parse_claims_response(response.text)
        except (json.JSONDecodeError, KeyError) as e:
            return {"claims": [], "total_claims": 0, "supported": 0,
                    "unsupported": 0, "contradicted": 0,
                    "hallucination_rate": None,
                    "parsing_error": str(e),
                    "reasoning": f"Parse error: {e}"}
        except Exception as e:
            return {"claims": [], "total_claims": 0, "supported": 0,
                    "unsupported": 0, "contradicted": 0,
                    "hallucination_rate": None,
                    "reasoning": f"API error: {e}"}
