"""
Unified Summarizer — WS6

Tiered document summarization with post-hoc source-spans verification.
Every generated summary either passes the verifier (all sentences map to
a retrieved chunk via reranker score) or gets marked partial — no silent
hallucinations.

Tiers (by document size, auto-classified):
  skip     : < 500 chars total content → no summary needed
  excerpt  : 500–3,000 chars → first 2-3 sentences verbatim, zero LLM cost,
             verified=True by definition (the "summary" IS the source text)
  direct   : 3,000–30K effective chars → all chunks fit in context, single
             Gemini Flash call + verifier
  extract  : > 30K effective chars → reranker selects top-25 diverse chunks
             (MMR), single Gemini Flash call + verifier on selected chunks

Modes (v0.2.0):
  'short' : 2-3 sentence exec summary (tiered as above)
  'long'  : multi-paragraph via MapReduceSynthesizer (not tiered)

Batch support:
  `build_batch_prompts()` returns (prompt, verify_chunks) pairs keyed by
  a caller-provided ID. The nightly 06b script feeds these to the Gemini
  Batch API (50% cost) and then calls `verify_and_build_result()` on each
  response. On-demand callers use `summarize_async()` which does single
  calls as before.

Modes deferred to v0.3.0: 'themes', 'structured', 'comparison'.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional, Tuple

from services.source_span_verifier import (
    SourceSpanVerifier,
    VerifiedSentence,
    VerificationResult,
    DEFAULT_THRESHOLD,
    split_sentences,
)

logger = logging.getLogger(__name__)

SummaryMode = Literal["short", "long"]
_V020_MODES: set = {"short", "long"}
_DEFERRED_MODES: set = {"themes", "structured", "comparison"}

# Safety cap for mode='long' verification (MapReduce sees all chunks).
MAX_VERIFY_CHUNKS: int = 50

# ── Tier thresholds ──────────────────────────────────────────────────────

TIER_SKIP_MAX_CHARS: int = 500
TIER_EXCERPT_MAX_CHARS: int = 3_000
TIER_DIRECT_MAX_CHARS: int = 30_000
# Above TIER_DIRECT_MAX_CHARS → "extract" tier

# Max sentences for excerpt tier.
EXCERPT_MAX_SENTENCES: int = 3

Tier = Literal["skip", "excerpt", "direct", "extract"]


@dataclass
class Citation:
    """A source citation attached to a verified summary."""

    chunk_id: Any
    document_id: Optional[str] = None
    title: Optional[str] = None
    rerank_score: float = 0.0


@dataclass
class SummaryResult:
    """Result of `Summarizer.summarize` — matches the WS6 handoff shape."""

    text: str
    sentences: List[VerifiedSentence] = field(default_factory=list)
    verified: bool = True
    stripped_count: int = 0
    total_sentences: int = 0
    sources: List[Citation] = field(default_factory=list)
    mode: str = "short"
    tier: str = ""
    latency_ms: int = 0
    strip_ratio: float = 0.0


# ── Gemini short-summary prompt ─────────────────────────────────────────

_SHORT_PROMPT_TEMPLATE = (
    "Je bent een expert op het gebied van de Rotterdamse gemeentepolitiek.\n\n"
    "Hieronder staan {n_chunks} fragmenten uit één raadsdocument. "
    "Schrijf een KORTE Nederlandse samenvatting van 2-3 zinnen die de kern weergeeft: "
    "wat wordt voorgesteld/besproken, welk bedrag of welke beslissing is relevant, "
    "en (indien van toepassing) welke partij of welke commissie het initieert.\n\n"
    "REGELS:\n"
    "1. Schrijf ALLEEN wat letterlijk uit de fragmenten blijkt — geen aannames.\n"
    "2. Maximaal 3 zinnen.\n"
    "3. Noem concrete cijfers, namen en datums als die in de fragmenten staan.\n"
    "4. Geen meta-commentaar (\"dit document gaat over...\"); begin direct met de inhoud.\n\n"
    "Fragmenten:\n{context}\n\n"
    "Samenvatting (2-3 zinnen, Nederlands):"
)

# Variant for extract tier: let the LLM know these are representative
# samples, not the full document.
_SHORT_PROMPT_EXTRACT_TEMPLATE = (
    "Je bent een expert op het gebied van de Rotterdamse gemeentepolitiek.\n\n"
    "Hieronder staan {n_chunks} GESELECTEERDE fragmenten uit een lang raadsdocument "
    "(het volledige document bevat veel meer fragmenten). Deze fragmenten zijn "
    "geselecteerd als de meest representatieve delen van het document.\n\n"
    "Schrijf een KORTE Nederlandse samenvatting van 2-3 zinnen die de kern weergeeft: "
    "wat wordt voorgesteld/besproken, welk bedrag of welke beslissing is relevant, "
    "en (indien van toepassing) welke partij of welke commissie het initieert.\n\n"
    "REGELS:\n"
    "1. Schrijf ALLEEN wat letterlijk uit de fragmenten blijkt — geen aannames.\n"
    "2. Maximaal 3 zinnen.\n"
    "3. Noem concrete cijfers, namen en datums als die in de fragmenten staan.\n"
    "4. Geen meta-commentaar (\"dit document gaat over...\"); begin direct met de inhoud.\n\n"
    "Fragmenten:\n{context}\n\n"
    "Samenvatting (2-3 zinnen, Nederlands):"
)


def _build_context(
    chunks: List[Any], max_chars: int = 30_000,
) -> Tuple[str, List[Any]]:
    """Concatenate chunk contents with lightweight headers, capped at max_chars.

    Returns (context_string, included_chunks) so the caller can pass only the
    chunks the LLM actually saw to the verifier.
    """
    parts: List[str] = []
    included: List[Any] = []
    total = 0
    for i, c in enumerate(chunks, 1):
        content = getattr(c, "content", "") or ""
        if not content.strip():
            continue
        title = getattr(c, "title", "") or ""
        header = f"[Fragment {i}" + (f" — {title}" if title else "") + "]"
        piece = f"{header}\n{content}"
        if total + len(piece) > max_chars:
            break
        parts.append(piece)
        included.append(c)
        total += len(piece)
    return "\n\n---\n\n".join(parts), included


def _total_content_chars(chunks: List[Any]) -> int:
    """Sum of content char lengths across all chunks."""
    return sum(len(getattr(c, "content", "") or "") for c in chunks)


# ── Summarizer ───────────────────────────────────────────────────────────

class Summarizer:
    """
    Tiered source-spans-verified summarizer.

    On-demand callers use `summarize_async()` (single Gemini call).
    Batch callers use `classify_tier()` + `build_prompt()` + `verify_and_build_result()`.
    """

    def __init__(
        self,
        verifier: Optional[SourceSpanVerifier] = None,
        chunk_selector: Optional[Any] = None,
        threshold: float = DEFAULT_THRESHOLD,
        gemini_model: str = "gemini-2.5-flash-lite",
    ):
        self._verifier = verifier
        self._chunk_selector = chunk_selector
        self.threshold = threshold
        self.gemini_model = gemini_model

    # ── Tier classification ────────────────────────────────────────────

    @staticmethod
    def classify_tier(chunks: List[Any]) -> Tier:
        """Classify a document's chunk set into a processing tier."""
        total = _total_content_chars(chunks)
        if total < TIER_SKIP_MAX_CHARS:
            return "skip"
        if total < TIER_EXCERPT_MAX_CHARS:
            return "excerpt"
        if total <= TIER_DIRECT_MAX_CHARS:
            return "direct"
        return "extract"

    # ── Excerpt tier (no LLM call) ─────────────────────────────────────

    @staticmethod
    def build_excerpt(chunks: List[Any]) -> SummaryResult:
        """Build a summary from the first 2-3 sentences of the content.

        No LLM call, no reranker call. verified=True because the summary
        text is a verbatim excerpt from the source — it's grounded by
        definition.
        """
        # Concatenate all chunk content (it's < 3K chars by tier gate).
        full_text = " ".join(
            (getattr(c, "content", "") or "").strip()
            for c in chunks
            if (getattr(c, "content", "") or "").strip()
        )
        if not full_text:
            return SummaryResult(text="", verified=False, tier="excerpt")

        sentences = split_sentences(full_text)
        kept = sentences[:EXCERPT_MAX_SENTENCES]
        excerpt = " ".join(kept)

        return SummaryResult(
            text=excerpt,
            sentences=[
                VerifiedSentence(text=s, rerank_score=1.0, kept=True)
                for s in kept
            ],
            verified=True,
            stripped_count=0,
            total_sentences=len(kept),
            sources=[],
            mode="short",
            tier="excerpt",
            latency_ms=0,
            strip_ratio=0.0,
        )

    # ── Chunk selection for extract tier ───────────────────────────────

    def _get_chunk_selector(self):
        if self._chunk_selector is None:
            from services.chunk_selector import ChunkSelector
            self._chunk_selector = ChunkSelector()
        return self._chunk_selector

    def select_chunks(self, chunks: List[Any], top_k: int = 25) -> List[Any]:
        """Select diverse representative chunks for the extract tier."""
        selector = self._get_chunk_selector()
        return selector.select(chunks, top_k=top_k)

    # ── Prompt building (for batch API callers) ────────────────────────

    def build_prompt(
        self, chunks: List[Any], mode: SummaryMode = "short",
    ) -> Tuple[str, List[Any]]:
        """Build the LLM prompt and return (prompt, verify_chunks).

        Used by the nightly batch script: it collects prompts, submits them
        to the Gemini Batch API, then calls `verify_and_build_result` on
        each response.

        Handles tier classification internally: if the tier is "extract",
        chunks are pre-selected via the reranker before building the prompt.

        Raises ValueError if tier is "skip" or "excerpt" (those don't need
        an LLM call — handle them before calling this method).
        """
        tier = self.classify_tier(chunks)
        if tier == "skip":
            raise ValueError("Skip-tier docs don't need a prompt. Handle before calling build_prompt.")
        if tier == "excerpt":
            raise ValueError("Excerpt-tier docs don't need a prompt. Use build_excerpt() instead.")

        if tier == "extract":
            chunks = self.select_chunks(chunks)
            template = _SHORT_PROMPT_EXTRACT_TEMPLATE
        else:
            template = _SHORT_PROMPT_TEMPLATE

        context, included_chunks = _build_context(chunks)
        if not context:
            raise ValueError("No usable chunk content after building context.")

        prompt = template.format(
            n_chunks=len(included_chunks),
            context=context,
        )
        return prompt, included_chunks

    # ── Verification + result building (for batch callers) ─────────────

    def verify_and_build_result(
        self,
        raw_text: str,
        verify_chunks: List[Any],
        mode: SummaryMode = "short",
        tier: str = "",
        latency_ms: int = 0,
    ) -> SummaryResult:
        """Run source-span verification on raw LLM output and build a SummaryResult.

        Used after retrieving batch results. `verify_chunks` should be the
        same list returned by `build_prompt()`.
        """
        if not raw_text or not raw_text.strip():
            return SummaryResult(
                text="", verified=False, mode=mode, tier=tier,
                latency_ms=latency_ms,
            )

        verification = self._run_verifier(raw_text, verify_chunks)
        sources = self._build_sources(verification, verify_chunks)

        return SummaryResult(
            text=verification.text,
            sentences=verification.sentences,
            verified=verification.verified,
            stripped_count=verification.stripped_count,
            total_sentences=verification.total_sentences,
            sources=sources,
            mode=mode,
            tier=tier,
            latency_ms=latency_ms,
            strip_ratio=verification.strip_ratio,
        )

    # ── On-demand public API ───────────────────────────────────────────

    def summarize(
        self,
        chunks: List[Any],
        mode: SummaryMode = "short",
        max_tokens: int = 1500,
        enforce_source_spans: bool = True,
        question: Optional[str] = None,
    ) -> SummaryResult:
        """Synchronous wrapper for summarize_async."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            raise RuntimeError(
                "Summarizer.summarize called from inside a running event loop; "
                "use await Summarizer.summarize_async(...) instead."
            )
        return asyncio.run(
            self.summarize_async(
                chunks=chunks,
                mode=mode,
                max_tokens=max_tokens,
                enforce_source_spans=enforce_source_spans,
                question=question,
            )
        )

    async def summarize_async(
        self,
        chunks: List[Any],
        mode: SummaryMode = "short",
        max_tokens: int = 1500,
        enforce_source_spans: bool = True,
        question: Optional[str] = None,
    ) -> SummaryResult:
        """Async on-demand entrypoint (single Gemini call, immediate result)."""
        started = time.monotonic()

        if mode in _DEFERRED_MODES:
            raise NotImplementedError(
                f"mode='{mode}' is deferred to v0.3.0 per WS6 handoff. "
                f"v0.2.0 supports: {sorted(_V020_MODES)}"
            )
        if mode not in _V020_MODES:
            raise ValueError(f"Unknown mode '{mode}'. Valid: {sorted(_V020_MODES)}")

        if not chunks:
            return SummaryResult(text="", verified=False, mode=mode, tier="skip")

        # ── mode='short': tiered routing ───────────────────────────────
        if mode == "short":
            tier = self.classify_tier(chunks)

            if tier == "skip":
                return SummaryResult(text="", verified=False, mode=mode, tier=tier)

            if tier == "excerpt":
                result = self.build_excerpt(chunks)
                result.latency_ms = int((time.monotonic() - started) * 1000)
                return result

            # direct or extract — need LLM call
            raw_text, verify_chunks = await self._generate_short(chunks, tier, max_tokens)

        # ── mode='long': MapReduce (no tiering) ───────────────────────
        else:
            tier = "direct"
            raw_text = await self._generate_long(
                question or "Geef een uitgebreide samenvatting van dit document.",
                chunks,
            )
            verify_chunks = chunks[:MAX_VERIFY_CHUNKS]

        if not raw_text or not raw_text.strip():
            return SummaryResult(
                text="", verified=False, mode=mode, tier=tier,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        if not enforce_source_spans:
            return SummaryResult(
                text=raw_text.strip(), verified=False, mode=mode, tier=tier,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        result = self.verify_and_build_result(
            raw_text, verify_chunks, mode=mode, tier=tier,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    # ── Generation: short (tiered) ─────────────────────────────────────

    async def _generate_short(
        self, chunks: List[Any], tier: Tier, max_tokens: int,
    ) -> Tuple[str, List[Any]]:
        """Generate a short summary. Routes through tier logic.

        For "extract" tier: selects diverse chunks first via reranker.
        For "direct" tier: uses all chunks (they fit in context).

        Returns (text, verify_chunks).
        """
        if tier == "extract":
            selected = self.select_chunks(chunks)
            template = _SHORT_PROMPT_EXTRACT_TEMPLATE
        else:
            selected = chunks
            template = _SHORT_PROMPT_TEMPLATE

        context, included_chunks = _build_context(selected)
        if not context:
            return "", []

        prompt = template.format(
            n_chunks=len(included_chunks),
            context=context,
        )

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.warning("summarizer: GEMINI_API_KEY missing")
            return "", []

        try:
            import google.genai as genai  # type: ignore
        except ImportError:
            logger.warning("summarizer: google-genai not installed")
            return "", []

        def _call() -> str:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=self.gemini_model,
                contents=prompt,
            )
            return (response.text or "").strip()

        try:
            text = await asyncio.to_thread(_call)
            return text, included_chunks
        except Exception as e:
            logger.error(f"summarizer: Gemini short call failed: {e}")
            return "", []

    # ── Generation: long (delegates to MapReduceSynthesizer) ───────────

    async def _generate_long(self, question: str, chunks: List[Any]) -> str:
        """Delegate to the existing MapReduceSynthesizer."""
        try:
            from services.synthesis import MapReduceSynthesizer
        except ImportError as e:
            logger.error(f"summarizer: MapReduceSynthesizer unavailable: {e}")
            return ""
        try:
            synth = MapReduceSynthesizer()
            return await synth.synthesize(question=question, chunks=chunks)
        except Exception as e:
            logger.error(f"summarizer: MapReduceSynthesizer failed: {e}")
            return ""

    # ── Verification + citation plumbing ───────────────────────────────

    def _get_verifier(self) -> SourceSpanVerifier:
        if self._verifier is None:
            self._verifier = SourceSpanVerifier(threshold=self.threshold)
        return self._verifier

    def _run_verifier(self, text: str, chunks: List[Any]) -> VerificationResult:
        verifier = self._get_verifier()
        return verifier.verify(text, chunks)

    @staticmethod
    def _build_sources(
        verification: VerificationResult,
        chunks: List[Any],
    ) -> List[Citation]:
        """Collect unique citation chunks in the order they were first cited."""
        by_chunk_id: dict = {}
        for c in chunks:
            cid = getattr(c, "chunk_id", None)
            if cid is None:
                continue
            by_chunk_id[cid] = c

        seen: set = set()
        citations: List[Citation] = []
        for vs in verification.sentences:
            if not vs.kept:
                continue
            if vs.citation_chunk_id is None:
                continue
            if vs.citation_chunk_id in seen:
                continue
            seen.add(vs.citation_chunk_id)
            src = by_chunk_id.get(vs.citation_chunk_id)
            citations.append(
                Citation(
                    chunk_id=vs.citation_chunk_id,
                    document_id=getattr(src, "document_id", None) if src else None,
                    title=getattr(src, "title", None) if src else None,
                    rerank_score=vs.rerank_score,
                )
            )
        return citations
