"""
CRAG (Corrective RAG) Relevance Filter — post-retrieval quality check.

After sub-query retrieval, evaluates each chunk's relevance to the ORIGINAL
question (not just the sub-query). Discards chunks that are factually correct
but contextually irrelevant.

Uses Haiku for fast, cheap relevance scoring. API-only.
"""

import os
import asyncio
import logging
from typing import List, Tuple

import anthropic
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

_RELEVANCE_PROMPT = """Beoordeel of het volgende tekstfragment DIRECT relevant is voor het beantwoorden van de vraag.

Vraag: {question}

Fragment (eerste 500 tekens):
{chunk_preview}

Antwoord met alleen "JA" of "NEE".
- JA: het fragment bevat informatie die direct helpt bij het beantwoorden van de vraag
- NEE: het fragment bevat misschien correcte feiten, maar deze zijn niet relevant voor de vraag"""


async def filter_chunks_by_relevance(
    question: str,
    chunks: list,
    min_relevant: int = 5,
    max_concurrent: int = 10,
) -> Tuple[list, int]:
    """
    Filter chunks by relevance to the original question using Haiku.

    Returns:
        (filtered_chunks, num_removed)
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or len(chunks) <= min_relevant:
        return chunks, 0

    client = anthropic.Anthropic(api_key=api_key)
    semaphore = asyncio.Semaphore(max_concurrent)

    async def check_relevance(chunk, idx: int) -> Tuple[int, bool]:
        async with semaphore:
            content = getattr(chunk, "content", str(chunk))
            preview = content[:500]

            prompt = _RELEVANCE_PROMPT.format(
                question=question,
                chunk_preview=preview,
            )

            try:
                response = await asyncio.to_thread(
                    client.messages.create,
                    model="claude-haiku-4-5-20251001",
                    max_tokens=10,
                    temperature=0.0,
                    messages=[{"role": "user", "content": prompt}],
                )
                answer = response.content[0].text.strip().upper()
                return idx, answer.startswith("JA")
            except Exception as e:
                log.warning(f"CRAG filter error on chunk {idx}: {e}")
                return idx, True  # Keep on error

    # Score all chunks in parallel
    tasks = [check_relevance(c, i) for i, c in enumerate(chunks)]
    results = await asyncio.gather(*tasks)

    # Separate relevant and irrelevant
    relevant_indices = {idx for idx, is_relevant in results if is_relevant}
    filtered = [c for i, c in enumerate(chunks) if i in relevant_indices]

    # Safety: always keep at least min_relevant chunks
    if len(filtered) < min_relevant:
        # Add back top-scored irrelevant chunks by similarity_score
        irrelevant = [(i, c) for i, c in enumerate(chunks) if i not in relevant_indices]
        irrelevant.sort(key=lambda x: getattr(x[1], "similarity_score", 0), reverse=True)
        for idx, chunk in irrelevant:
            if len(filtered) >= min_relevant:
                break
            filtered.append(chunk)

    removed = len(chunks) - len(filtered)
    log.info(f"CRAG filter: {len(chunks)} → {len(filtered)} chunks ({removed} removed as irrelevant)")
    return filtered, removed
