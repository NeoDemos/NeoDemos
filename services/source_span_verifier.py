"""
Source-Spans-Only Verifier — WS6

Two-tier post-hoc verification that every sentence in an LLM-generated
summary is factually grounded in the source chunks.

Tier 1 — Semantic match (Jina v3 reranker):
    Score >= THRESHOLD (0.4): VERIFIED — sentence semantically matches a chunk.
    Score < FLOOR (0.15): HALLUCINATION — no semantic overlap at all, strip.

Tier 2 — Factual grounding (for borderline scores 0.15–0.39):
    The reranker says "weak match" — but is this a paraphrase (same facts,
    different words) or a factual hallucination (invented facts)?

    Extract "fact tokens" from the sentence: numbers, dates, proper nouns.
    Check whether each token appears in ANY source chunk text. If ≥ 80% of
    fact tokens are grounded → PARAPHRASE (keep, it's just a rewrite).
    Otherwise → HALLUCINATION (the LLM introduced novel facts).

This solves the false-positive problem where the verifier strips legitimate
paraphrases that score 0.3–0.39 on the reranker. Government document
summarization requires formal Dutch rewording — the verifier must allow
that while still catching invented numbers, names, and dates.

Public surface:

    verifier = SourceSpanVerifier()
    result = verifier.verify(summary_text, chunks)
    # result.sentences[i].classification:
    #   "verified"      — reranker score >= threshold
    #   "paraphrase"    — borderline score but facts grounded
    #   "hallucination" — facts not grounded or no semantic match
    #   "too_short"     — sentence too short to score
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Protocol, Set

logger = logging.getLogger(__name__)


# ── Configuration ────────────────────────────────────────────────────────

DEFAULT_THRESHOLD: float = 0.4

# Below this score, don't even bother with the fact-check — it's noise.
BORDERLINE_FLOOR: float = 0.15

# If more than this fraction of sentences get stripped, mark the whole
# summary partial (verified=False).
MAX_STRIP_RATIO: float = 0.30

# Minimum sentence length (chars) to attempt scoring.
MIN_SENTENCE_CHARS: int = 12

# Fact-grounding: what fraction of extracted fact tokens must appear in
# source chunks for a borderline sentence to be kept as "paraphrase".
FACT_GROUNDING_RATIO: float = 0.80


# ── Reranker / Chunk protocols ───────────────────────────────────────────

class RerankerProtocol(Protocol):
    def score_pairs(self, query: str, documents: List[str]) -> List[float]: ...


class ChunkProtocol(Protocol):
    chunk_id: Any
    content: str


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass
class VerifiedSentence:
    """A sentence from a generated summary, scored against source chunks."""

    text: str
    rerank_score: float
    citation_chunk_id: Optional[Any] = None
    kept: bool = True
    classification: str = ""  # "verified", "paraphrase", "hallucination", "too_short"
    # For diagnostics (logged in the 50-doc test):
    fact_tokens_total: int = 0
    fact_tokens_grounded: int = 0


@dataclass
class VerificationResult:
    """Outcome of verifying a summary text against its source chunks."""

    text: str
    sentences: List[VerifiedSentence] = field(default_factory=list)
    verified: bool = True
    stripped_count: int = 0
    total_sentences: int = 0
    threshold: float = DEFAULT_THRESHOLD
    latency_ms: int = 0

    @property
    def strip_ratio(self) -> float:
        if self.total_sentences == 0:
            return 0.0
        return self.stripped_count / self.total_sentences


# ── Dutch sentence tokenization ─────────────────────────────────────────

_DUTCH_ABBREVS = {
    "bijv", "bv", "nl", "o.a", "o.a.", "m.a.w", "d.w.z", "i.p.v", "t.o.v",
    "enz", "etc", "a.u.b", "z.o.z", "jl", "b.v", "vgl", "art", "lid",
    "dhr", "mw", "drs", "dr", "ir", "mr", "ing", "prof", "sr", "jr",
    "ca", "m.i", "i.v.m", "i.h.b", "t.a.v", "t.e.m", "m.b.t", "n.v.t",
    "ivm", "iom", "tbv", "tav",
}


def _looks_like_abbrev(token: str) -> bool:
    lowered = token.lower().rstrip(".")
    return lowered in _DUTCH_ABBREVS


def _regex_sentence_split(text: str) -> List[str]:
    text = text.strip()
    if not text:
        return []
    text = re.sub(r"\s+", " ", text)

    sentences: List[str] = []
    buf: List[str] = []
    tokens = text.split(" ")

    for i, tok in enumerate(tokens):
        buf.append(tok)
        if not tok:
            continue
        last_char = tok[-1]
        if last_char in ".?!":
            stripped = tok.rstrip(".?!)")
            if _looks_like_abbrev(stripped):
                continue
            if i + 1 < len(tokens):
                nxt = tokens[i + 1]
                if not nxt:
                    continue
                first = nxt[0]
                if not (first.isupper() or first.isdigit() or first in "„\"'"):
                    continue
            sentences.append(" ".join(buf).strip())
            buf = []

    if buf:
        tail = " ".join(buf).strip()
        if tail:
            sentences.append(tail)

    return [s for s in sentences if s]


def _nltk_sentence_split(text: str) -> Optional[List[str]]:
    try:
        import nltk
        from nltk.tokenize import sent_tokenize
    except ImportError:
        return None
    try:
        return sent_tokenize(text, language="dutch")
    except LookupError:
        try:
            nltk.download("punkt", quiet=True)
            nltk.download("punkt_tab", quiet=True)
            return sent_tokenize(text, language="dutch")
        except Exception:
            return None
    except Exception as e:
        logger.warning(f"nltk sent_tokenize failed, falling back: {e}")
        return None


def split_sentences(text: str) -> List[str]:
    """Split Dutch text into sentences (nltk punkt → regex fallback)."""
    if not text or not text.strip():
        return []
    via_nltk = _nltk_sentence_split(text)
    if via_nltk is not None:
        return [s.strip() for s in via_nltk if s and s.strip()]
    return _regex_sentence_split(text)


# ── Fact-token extraction ────────────────────────────────────────────────

# Dutch months for date extraction.
_NL_MONTHS = (
    "januari", "februari", "maart", "april", "mei", "juni",
    "juli", "augustus", "september", "oktober", "november", "december",
)
_NL_MONTHS_SET = set(_NL_MONTHS)

# Patterns that match fact-bearing tokens.
_NUMBER_RE = re.compile(r"\d[\d.,]*\d|\d+")
_DATE_RE = re.compile(
    r"\d{1,2}\s+(?:" + "|".join(_NL_MONTHS) + r")\s+\d{4}",
    re.IGNORECASE,
)
# Common Dutch function words / stopwords to exclude from proper-noun extraction.
_NL_STOPWORDS: Set[str] = {
    "de", "het", "een", "van", "in", "op", "voor", "met", "door", "aan",
    "uit", "over", "bij", "naar", "tot", "als", "om", "dat", "die", "dit",
    "deze", "er", "ook", "maar", "nog", "wel", "niet", "geen", "wat",
    "dan", "meer", "zeer", "alle", "andere", "wordt", "worden", "werd",
    "zijn", "was", "heeft", "hebben", "kan", "moet", "zal", "zou",
    "na", "te", "is", "en", "of", "zo",
}


def _extract_fact_tokens(sentence: str) -> Set[str]:
    """Extract fact-bearing tokens: numbers, dates, proper nouns.

    Returns a set of lowercased strings. An empty set means no verifiable
    facts were found — the sentence is likely a connector or opinion
    ("Dit is een belangrijk besluit").
    """
    facts: Set[str] = set()

    # Numbers (amounts, years, percentages).
    for m in _NUMBER_RE.finditer(sentence):
        facts.add(m.group().lower())

    # Dates ("15 maart 2024").
    for m in _DATE_RE.finditer(sentence):
        facts.add(m.group().lower())

    # Proper nouns: words starting with uppercase that are NOT at the
    # start of a sentence and NOT Dutch stopwords. Catches names of
    # people (Aboutaleb), places (Rotterdam-Zuid), organizations (GRJR).
    words = sentence.split()
    for i, w in enumerate(words):
        if i == 0:
            continue  # first word is always capitalized
        # Strip punctuation for matching.
        clean = re.sub(r"[^\w-]", "", w)
        if not clean:
            continue
        if clean[0].isupper() and clean.lower() not in _NL_STOPWORDS:
            facts.add(clean.lower())

    return facts


def _check_facts_grounded(
    fact_tokens: Set[str],
    chunk_texts: List[str],
) -> tuple[int, int]:
    """Check how many fact tokens appear in ANY source chunk.

    Returns (grounded_count, total_count).
    """
    if not fact_tokens:
        return 0, 0

    # Build a single lowercased corpus from all chunk texts for fast lookup.
    corpus = " ".join(chunk_texts).lower()

    grounded = 0
    for token in fact_tokens:
        if token in corpus:
            grounded += 1

    return grounded, len(fact_tokens)


# ── The verifier ─────────────────────────────────────────────────────────

class SourceSpanVerifier:
    """
    Two-tier post-hoc verifier.

    Tier 1: Jina v3 reranker scores each sentence against source chunks.
    Tier 2: For borderline scores (FLOOR ≤ score < THRESHOLD), extract
    fact tokens and check grounding. Paraphrases are kept; hallucinations
    with novel facts are stripped.
    """

    def __init__(
        self,
        reranker: Optional[RerankerProtocol] = None,
        threshold: float = DEFAULT_THRESHOLD,
        borderline_floor: float = BORDERLINE_FLOOR,
        max_strip_ratio: float = MAX_STRIP_RATIO,
        fact_grounding_ratio: float = FACT_GROUNDING_RATIO,
    ):
        self._reranker = reranker
        self.threshold = threshold
        self.borderline_floor = borderline_floor
        self.max_strip_ratio = max_strip_ratio
        self.fact_grounding_ratio = fact_grounding_ratio

    def _get_reranker(self) -> RerankerProtocol:
        if self._reranker is None:
            from services.reranker import create_reranker
            self._reranker = create_reranker()
        return self._reranker

    def verify(
        self,
        summary_text: str,
        chunks: List[ChunkProtocol],
        threshold: Optional[float] = None,
    ) -> VerificationResult:
        """Verify `summary_text` against `chunks`.

        Returns a VerificationResult with per-sentence classification:
        "verified", "paraphrase", "hallucination", or "too_short".
        """
        started = time.monotonic()
        active_threshold = self.threshold if threshold is None else threshold

        sentences = split_sentences(summary_text)
        if not sentences:
            return VerificationResult(
                text=summary_text, sentences=[], verified=False,
                stripped_count=0, total_sentences=0,
                threshold=active_threshold,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        if not chunks:
            return VerificationResult(
                text=summary_text,
                sentences=[
                    VerifiedSentence(text=s, rerank_score=0.0, kept=False,
                                     classification="hallucination")
                    for s in sentences
                ],
                verified=False,
                stripped_count=len(sentences),
                total_sentences=len(sentences),
                threshold=active_threshold,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        chunk_texts = [getattr(c, "content", "") or "" for c in chunks]
        scoring_pairs = [(i, t) for i, t in enumerate(chunk_texts) if t.strip()]
        if not scoring_pairs:
            return VerificationResult(
                text=summary_text,
                sentences=[
                    VerifiedSentence(text=s, rerank_score=0.0, kept=False,
                                     classification="hallucination")
                    for s in sentences
                ],
                verified=False,
                stripped_count=len(sentences),
                total_sentences=len(sentences),
                threshold=active_threshold,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        indices_in_chunks = [i for i, _ in scoring_pairs]
        texts_for_rerank = [t for _, t in scoring_pairs]

        try:
            reranker = self._get_reranker()
        except Exception as e:
            logger.error(f"verifier: reranker unavailable ({e}); returning unverified")
            return VerificationResult(
                text=summary_text,
                sentences=[
                    VerifiedSentence(text=s, rerank_score=0.0, kept=True,
                                     classification="verified")
                    for s in sentences
                ],
                verified=False, stripped_count=0,
                total_sentences=len(sentences),
                threshold=active_threshold,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        verified_sentences: List[VerifiedSentence] = []
        for sent in sentences:
            if len(sent) < MIN_SENTENCE_CHARS:
                verified_sentences.append(
                    VerifiedSentence(text=sent, rerank_score=0.0, kept=True,
                                     classification="too_short")
                )
                continue

            try:
                scores = reranker.score_pairs(sent, texts_for_rerank)
            except Exception as e:
                logger.warning(f"verifier: reranker call failed ({e}); stripping")
                verified_sentences.append(
                    VerifiedSentence(text=sent, rerank_score=0.0, kept=False,
                                     classification="hallucination")
                )
                continue

            if not scores:
                verified_sentences.append(
                    VerifiedSentence(text=sent, rerank_score=0.0, kept=False,
                                     classification="hallucination")
                )
                continue

            best_local_idx = max(range(len(scores)), key=lambda k: scores[k])
            best_score = float(scores[best_local_idx])
            best_chunk_idx = indices_in_chunks[best_local_idx]
            best_chunk = chunks[best_chunk_idx]
            citation = getattr(best_chunk, "chunk_id", None)

            # ── Tier 1: high reranker score → verified ──────────────
            if best_score >= active_threshold:
                verified_sentences.append(
                    VerifiedSentence(
                        text=sent, rerank_score=best_score,
                        citation_chunk_id=citation, kept=True,
                        classification="verified",
                    )
                )
                continue

            # ── Below borderline floor → definite hallucination ─────
            if best_score < self.borderline_floor:
                verified_sentences.append(
                    VerifiedSentence(
                        text=sent, rerank_score=best_score,
                        citation_chunk_id=None, kept=False,
                        classification="hallucination",
                    )
                )
                continue

            # ── Tier 2: borderline (0.15–0.39) → fact-grounding check
            fact_tokens = _extract_fact_tokens(sent)

            if not fact_tokens:
                # No verifiable facts (connector sentence like "Dit is
                # van belang voor de stad."). The reranker says weak match
                # and there are no facts to check — give benefit of doubt
                # since it's likely a transitional sentence, not a
                # hallucinated claim.
                verified_sentences.append(
                    VerifiedSentence(
                        text=sent, rerank_score=best_score,
                        citation_chunk_id=citation, kept=True,
                        classification="paraphrase",
                        fact_tokens_total=0, fact_tokens_grounded=0,
                    )
                )
                continue

            grounded, total = _check_facts_grounded(fact_tokens, chunk_texts)
            ratio = grounded / total if total else 0.0

            if ratio >= self.fact_grounding_ratio:
                # Facts are present in source chunks → paraphrase, keep.
                verified_sentences.append(
                    VerifiedSentence(
                        text=sent, rerank_score=best_score,
                        citation_chunk_id=citation, kept=True,
                        classification="paraphrase",
                        fact_tokens_total=total,
                        fact_tokens_grounded=grounded,
                    )
                )
            else:
                # Novel facts not in source chunks → hallucination, strip.
                verified_sentences.append(
                    VerifiedSentence(
                        text=sent, rerank_score=best_score,
                        citation_chunk_id=None, kept=False,
                        classification="hallucination",
                        fact_tokens_total=total,
                        fact_tokens_grounded=grounded,
                    )
                )

        kept = [vs for vs in verified_sentences if vs.kept]
        stripped_count = len(verified_sentences) - len(kept)
        strip_ratio = stripped_count / max(1, len(verified_sentences))
        rebuilt_text = " ".join(vs.text for vs in kept).strip()

        if not rebuilt_text:
            rebuilt_text = summary_text.strip()
            verified_flag = False
        else:
            verified_flag = strip_ratio <= self.max_strip_ratio

        latency_ms = int((time.monotonic() - started) * 1000)

        # Detailed classification log.
        classifications = {}
        for vs in verified_sentences:
            classifications[vs.classification] = classifications.get(vs.classification, 0) + 1
        logger.info(
            "verifier: sentences=%d stripped=%d ratio=%.2f verified=%s "
            "classifications=%s latency_ms=%d",
            len(verified_sentences), stripped_count, strip_ratio,
            verified_flag, classifications, latency_ms,
        )

        return VerificationResult(
            text=rebuilt_text,
            sentences=verified_sentences,
            verified=verified_flag,
            stripped_count=stripped_count,
            total_sentences=len(verified_sentences),
            threshold=active_threshold,
            latency_ms=latency_ms,
        )
