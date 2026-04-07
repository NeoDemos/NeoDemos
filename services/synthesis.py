"""
Map-Reduce Synthesizer — parallel generation for broad_aggregation queries.

Architecture:
  MAP:    Split chunks into groups of 4-5, parallel Gemini API calls for mini-summaries
  REDUCE: Claude Sonnet synthesizes all mini-summaries into structured answer

API-only: Gemini API for map, Anthropic API for reduce. No local models.
"""

import os
import asyncio
import logging
from typing import List, Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────

MAP_GROUP_SIZE = 5       # Chunks per Gemini call
MAP_MAX_PARALLEL = 5     # Max concurrent Gemini calls
MAP_RETRY_ATTEMPTS = 3
MAP_RETRY_DELAY_S = 2
INTER_CALL_DELAY_S = 0.5  # Rate limit protection


# ── Gemini API helper ─────────────────────────────────────────────────

async def _call_gemini_map(question: str, chunk_texts: List[str], group_idx: int) -> str:
    """
    Call Gemini API for a single map step (summarize a group of chunks).
    Returns mini-summary or empty string on failure.
    """
    import google.genai as genai

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return ""

    context = "\n\n---\n\n".join(chunk_texts)

    prompt = (
        f"Je bent een expert op het gebied van de Rotterdamse gemeentepolitiek.\n\n"
        f"Vraag: {question}\n\n"
        f"Hieronder staan {len(chunk_texts)} bronnen. "
        f"Vat de relevante informatie samen die helpt bij het beantwoorden van de vraag. "
        f"Noem specifieke feiten, cijfers, namen en datums. "
        f"Als een bron niet relevant is, sla deze dan over.\n\n"
        f"Bronnen:\n{context}\n\n"
        f"Samenvatting:"
    )

    client = genai.Client(api_key=api_key)

    for attempt in range(MAP_RETRY_ATTEMPTS):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt,
            )
            if response.text:
                return response.text
        except Exception as e:
            if attempt == MAP_RETRY_ATTEMPTS - 1:
                log.warning(f"Gemini map group {group_idx} failed after {MAP_RETRY_ATTEMPTS} attempts: {e}")
                return ""
            await asyncio.sleep(MAP_RETRY_DELAY_S * (attempt + 1))

    return ""


# ── Claude Reduce ─────────────────────────────────────────────────────

def _call_claude_reduce(question: str, summaries: List[str], category: str = "") -> str:
    """
    Claude Sonnet synthesizes mini-summaries into a final structured answer.
    API-only.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        log.warning("No ANTHROPIC_API_KEY — returning concatenated summaries")
        return "\n\n".join(summaries)

    numbered = "\n\n".join(
        f"[Deelsamenvatting {i+1}]\n{s}" for i, s in enumerate(summaries) if s.strip()
    )

    prompt = (
        f"Je bent een expert op het gebied van de Rotterdamse gemeentepolitiek.\n\n"
        f"Vraag: {question}\n\n"
        f"Hieronder staan {len(summaries)} deelsamenvatingen, elk gebaseerd op een "
        f"groep bronnen uit gemeenteraadsdocumenten.\n\n"
        f"Syntheseer deze tot een COMPLEET en GESTRUCTUREERD antwoord:\n"
        f"- Gebruik kopjes of opsommingstekens\n"
        f"- Noem ALLE unieke aspecten/uitdagingen/feiten — verwijder duplicaten\n"
        f"- Behoud specifieke cijfers, datums en namen\n"
        f"- Vermeld ALLEEN informatie uit de deelsamenvatingen, verzin niets\n"
        f"- Meld hoeveel unieke aspecten je hebt gevonden\n\n"
        f"Deelsamenvatingen:\n{numbered}\n\n"
        f"Antwoord (in het Nederlands):"
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
        log.error(f"Claude reduce failed: {e}")
        return "\n\n".join(summaries)


# ── Public API ────────────────────────────────────────────────────────

class MapReduceSynthesizer:
    """
    Map-reduce generation for broad_aggregation queries.
    API-only: Gemini for map, Claude for reduce.
    """

    async def synthesize(
        self,
        question: str,
        chunks: list,
        category: str = "",
    ) -> str:
        """
        Split chunks into groups, parallel Gemini summaries, Claude synthesis.
        Returns final answer string.
        """
        if not chunks:
            return "Geen bronnen gevonden om te analyseren."

        # Extract text from chunks
        texts = []
        for c in chunks:
            content = getattr(c, "content", str(c))
            title = getattr(c, "title", "")
            score = getattr(c, "similarity_score", 0)
            texts.append(f"[{title} (score: {score:.3f})]\n{content}")

        # Split into groups
        groups = [
            texts[i:i + MAP_GROUP_SIZE]
            for i in range(0, len(texts), MAP_GROUP_SIZE)
        ]
        log.info(f"Map-reduce: {len(chunks)} chunks → {len(groups)} groups")

        # MAP phase: parallel Gemini calls with rate limiting
        semaphore = asyncio.Semaphore(MAP_MAX_PARALLEL)

        async def rate_limited_map(group_texts, idx):
            async with semaphore:
                if idx > 0:
                    await asyncio.sleep(INTER_CALL_DELAY_S)
                return await _call_gemini_map(question, group_texts, idx)

        tasks = [rate_limited_map(g, i) for i, g in enumerate(groups)]
        summaries = await asyncio.gather(*tasks)

        # Filter empty summaries
        valid_summaries = [s for s in summaries if s and s.strip()]
        log.info(f"Map phase: {len(valid_summaries)}/{len(groups)} successful summaries")

        if not valid_summaries:
            return "Kon geen samenvatting genereren uit de bronnen."

        # If only a few summaries, skip reduce — just concatenate
        if len(valid_summaries) <= 2:
            return "\n\n".join(valid_summaries)

        # REDUCE phase: Claude synthesis
        log.info("Reduce phase: Claude Sonnet synthesis...")
        result = _call_claude_reduce(question, valid_summaries, category)
        return result
