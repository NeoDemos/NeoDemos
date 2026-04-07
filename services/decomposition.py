"""
Sub-Query Decomposition — for multi_hop queries.

Architecture:
  DECOMPOSE:  Haiku splits question into 2-4 sub-queries
  RETRIEVE:   Parallel retrieval per sub-query (via RAGService)
  MERGE:      Deduplicate + rerank merged pool
  SYNTHESIZE: Sonnet produces answer with cross-source linking

API-only: Haiku for decomposition, Sonnet for synthesis. No local models.
"""

import os
import asyncio
import logging
from typing import List, Tuple, Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────

MAX_SUB_QUERIES = 4
DECOMPOSE_RETRY_ATTEMPTS = 2


# ── Decompose ─────────────────────────────────────────────────────────

_DECOMPOSE_PROMPT = """Je bent een expert op het gebied van de Rotterdamse gemeentepolitiek.

Splits de volgende complexe vraag in 2-4 eenvoudige deelvragen die elk apart beantwoord kunnen worden met zoekresultaten uit gemeenteraadsdocumenten.

Regels:
- Elke deelvraag moet zelfstandig doorzoekbaar zijn
- Maximaal 4 deelvragen
- In het Nederlands
- Geef antwoord als JSON array van strings

Vraag: {question}

Deelvragen (JSON array):"""


async def _decompose_query(question: str) -> List[str]:
    """
    Use Haiku to split a complex question into sub-queries.
    Returns list of sub-query strings, or [question] on failure.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        log.warning("No ANTHROPIC_API_KEY — returning original query")
        return [question]

    prompt = _DECOMPOSE_PROMPT.format(question=question)

    for attempt in range(DECOMPOSE_RETRY_ATTEMPTS):
        try:
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text.strip()
            # Strip markdown fences
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            import json
            sub_queries = json.loads(text)

            if isinstance(sub_queries, list) and 1 <= len(sub_queries) <= MAX_SUB_QUERIES:
                # Validate each is a non-empty string
                valid = [sq for sq in sub_queries if isinstance(sq, str) and sq.strip()]
                if valid:
                    log.info(f"Decomposed into {len(valid)} sub-queries")
                    return valid

            log.warning(f"Decomposition returned invalid format: {text[:100]}")

        except Exception as e:
            log.warning(f"Decomposition attempt {attempt+1} failed: {e}")

    # Fallback: return original question
    return [question]


# ── Synthesis ─────────────────────────────────────────────────────────

_SYNTHESIS_PROMPT = """Je bent een expert op het gebied van de Rotterdamse gemeentepolitiek.

Oorspronkelijke vraag: {question}

Om deze vraag te beantwoorden zijn de volgende deelvragen onderzocht:
{sub_queries_formatted}

Hieronder staan de bronnen die voor alle deelvragen gevonden zijn.

Instructies:
- Beantwoord de oorspronkelijke vraag door informatie uit de deelvragen te VERBINDEN
- Vermeld welke bron welk feit levert
- Maak expliciet de verbanden tussen feiten uit verschillende bronnen
- Verzin GEEN informatie die niet in de bronnen staat
- Als een deelvraag niet goed beantwoord kan worden, zeg dat eerlijk
- NEGEER bronnen die niet direct relevant zijn voor de oorspronkelijke vraag, ook al bevatten ze correcte feiten
- Begin je antwoord DIRECT met het relevante antwoord, niet met irrelevante data uit de bronnen

Bronnen:
{context}

Antwoord (in het Nederlands):"""


def _call_claude_synthesis(
    question: str,
    sub_queries: List[str],
    context: str,
) -> str:
    """
    Claude Sonnet synthesizes sub-query results into a cohesive answer.
    API-only.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return context[:3000]

    sq_formatted = "\n".join(f"  {i+1}. {sq}" for i, sq in enumerate(sub_queries))

    prompt = _SYNTHESIS_PROMPT.format(
        question=question,
        sub_queries_formatted=sq_formatted,
        context=context,
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as e:
        log.error(f"Claude synthesis failed: {e}")
        return context[:3000]


# ── Public API ────────────────────────────────────────────────────────

class MultiHopDecomposer:
    """
    Sub-query decomposition for multi_hop queries.
    API-only: Haiku decomposes, Sonnet synthesizes.
    """

    async def decompose_and_retrieve(
        self,
        question: str,
        rag_service,
        top_k_per_sub: int = 15,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Tuple[list, List[str]]:
        """
        Decompose question → parallel retrieval → merge & dedup.
        Returns (merged_chunks, sub_queries).
        """
        # Step 1: Decompose
        sub_queries = await _decompose_query(question)
        log.info(f"Sub-queries: {sub_queries}")

        # Step 2: Parallel retrieval per sub-query
        async def _retrieve_sub(sq: str):
            return await asyncio.to_thread(
                rag_service.retrieve_relevant_context,
                sq,
                None,  # query_embedding (will be computed)
                top_k_per_sub,
                True,  # fallback_to_keywords
                date_from,
                date_to,
                False,  # fast_mode — we want reranking
            )

        tasks = [_retrieve_sub(sq) for sq in sub_queries]
        results_per_sub = await asyncio.gather(*tasks)

        # Step 3: Merge & deduplicate
        seen_ids = set()
        merged = []
        for chunks in results_per_sub:
            for chunk in chunks:
                if chunk.chunk_id not in seen_ids:
                    seen_ids.add(chunk.chunk_id)
                    merged.append(chunk)

        log.info(f"Merged: {len(merged)} unique chunks from {len(sub_queries)} sub-queries")

        # Step 4: Rerank merged pool
        from services.rag_service import _reranker
        if _reranker and merged:
            try:
                documents = [c.content for c in merged]
                scores = _reranker.score_pairs(question, documents)
                for chunk, score in zip(merged, scores):
                    chunk.similarity_score = float(score)
                merged.sort(key=lambda x: x.similarity_score, reverse=True)
                # Keep top 25
                merged = merged[:25]
            except Exception as e:
                log.warning(f"Reranking merged pool failed: {e}")

        return merged, sub_queries

    def synthesize(
        self,
        question: str,
        sub_queries: List[str],
        chunks: list,
    ) -> str:
        """
        Produce final answer from merged chunks.
        Uses Claude Sonnet for cross-source synthesis.
        """
        if not chunks:
            return "Geen relevante bronnen gevonden voor deze vraag."

        # Format context
        parts = []
        for i, c in enumerate(chunks, 1):
            title = getattr(c, "title", "Onbekend")
            content = getattr(c, "content", str(c))
            score = getattr(c, "similarity_score", 0)
            parts.append(f"[Bron {i} — {title} (score: {score:.3f})]\n{content}")
        context = "\n\n---\n\n".join(parts)

        return _call_claude_synthesis(question, sub_queries, context)
