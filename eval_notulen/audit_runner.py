"""
Virtual Notulen Audit Runner

Runs a complete, one-meeting-at-a-time audit covering:
  1. Transcript quality (NEER, speaker attribution, segment quality, agenda coverage)
  2. Hallucination check (LLM claim verification on sampled chunks)
  3. DB consistency (metadata, speakers, entity cross-reference)
  4. Chunk quality (length distribution, boilerplate, duplicates, coverage)

Runs entirely off PostgreSQL — no Qdrant required.
Embedding happens only after the audit passes, at promotion time.

Usage:
    python -m eval_notulen.audit_runner --list
    python -m eval_notulen.audit_runner --meeting-id <id>
    python -m eval_notulen.audit_runner --meeting-id <id> --samples 10
    python -m eval_notulen.audit_runner --meeting-id <id> --skip-llm
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/notulen_audit.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

from eval_notulen.config import NotulenAuditConfig, RUNS_DIR
from eval_notulen.metrics.transcript_quality import (
    run_all_transcript_quality,
    load_political_dictionary,
    flatten_segments,
)
from eval_notulen.metrics.db_consistency import (
    check_meeting_metadata_consistency,
    check_speaker_presence,
    check_entity_consistency_with_production,
)
from eval_notulen.metrics.chunk_quality import run_chunk_quality
from eval_notulen.reporter import generate_audit_report, save_audit_results, save_report


# ── Judge with notulen prompts ────────────────────────────────────────────────

def _patch_prompts():
    """Patch base prompts module with notulen-specific versions."""
    import eval.judge.prompts as base_prompts_module
    from eval_notulen.judge.prompts import PROMPTS as NOTULEN_PROMPTS
    base_prompts_module.PROMPTS["claim_verification"] = NOTULEN_PROMPTS["claim_verification"]
    base_prompts_module.PROMPTS["transcript_faithfulness"] = NOTULEN_PROMPTS["transcript_faithfulness"]
    base_prompts_module.PROMPTS["chunk_informativeness"] = NOTULEN_PROMPTS["chunk_informativeness"]


def _create_notulen_judge(config):
    """Create a judge instance for the configured backend."""
    from eval_notulen.config import NotulenAuditConfig
    _patch_prompts()

    backend = getattr(config, "judge_backend", "gemini")

    if backend == "claude":
        from eval.judge.claude_judge import ClaudeJudge
        return ClaudeJudge(model=config.anthropic_model)

    elif backend == "gemini":
        from eval.judge.claude_judge import GeminiJudge
        return GeminiJudge(model=config.gemini_model)

    elif backend == "local":
        return _create_local_judge(config.local_model)

    else:
        raise ValueError(f"Unknown judge backend: {backend}")


def _create_local_judge(model_path: str):
    """Create a judge backed by a local MLX model (Qwen2.5-7B-4bit etc.)."""
    from eval.judge.prompts import PROMPTS, SYSTEM
    from eval.judge.claude_judge import LLMJudge, _build_prompt, _parse_claims_response, _parse_json_score
    import time

    class LocalMLXJudge(LLMJudge):
        def __init__(self, model_path: str):
            import mlx_lm
            log.info(f"Loading local model: {model_path}")
            self.model, self.tokenizer = mlx_lm.load(model_path)
            self._generate = mlx_lm.generate
            self.backend_name = f"Local ({model_path.split('/')[-1]})"
            self._call_count = 0

        def _call(self, prompt: str, max_tokens: int = 1024) -> str:
            import mlx_lm
            messages = [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ]
            formatted = self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            if self._call_count > 0:
                time.sleep(0.1)
            self._call_count += 1
            return self._generate(self.model, self.tokenizer, prompt=formatted,
                                  max_tokens=max_tokens, verbose=False)

        def evaluate_metric(self, metric, question, answer, context="", gold_answer=""):
            prompt = _build_prompt(metric, question, answer, context, gold_answer)
            try:
                text = self._call(prompt, max_tokens=512)
                return _parse_json_score(text)
            except Exception as e:
                return {"score": 0, "reasoning": f"Local judge error: {e}"}

        def evaluate_claims(self, question, answer, context=""):
            prompt = _build_prompt("claim_verification", question, answer, context, "")
            try:
                text = self._call(prompt, max_tokens=1024)
                return _parse_claims_response(text)
            except Exception as e:
                return {"claims": [], "total_claims": 0, "supported": 0,
                        "unsupported": 0, "contradicted": 0,
                        "hallucination_rate": None, "reasoning": f"Local judge error: {e}"}

    return LocalMLXJudge(model_path)


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_staging_meeting(meeting_id: str, db_url: str) -> Dict:
    """Load meeting metadata, document list, and chunk count from staging."""
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn = psycopg2.connect(db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT id, name, start_date, committee, quality_score,
                   review_status, transcript_source, promoted_at
            FROM staging.meetings
            WHERE id = %s
        """, (meeting_id,))
        row = cur.fetchone()
        meeting = dict(row) if row else {}

        cur.execute("""
            SELECT id, name
            FROM staging.documents
            WHERE meeting_id = %s
            ORDER BY id
        """, (meeting_id,))
        documents = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT COUNT(*) AS cnt
            FROM staging.document_chunks dc
            JOIN staging.documents d ON dc.document_id = d.id
            WHERE d.meeting_id = %s
        """, (meeting_id,))
        chunk_count = cur.fetchone()["cnt"]

        cur.close()
        conn.close()

        return {"meeting": meeting, "documents": documents, "chunk_count": chunk_count}
    except Exception as e:
        log.error(f"Could not load staging meeting {meeting_id}: {e}")
        return {}


def _load_transcript_cache(meeting_id: str) -> Optional[Dict]:
    """Try to load the cached transcript JSON from the pipeline's staging cache."""
    cache_path = (
        PROJECT_ROOT / "output" / "transcripts" / "staging_cache" / f"{meeting_id}.json"
    )
    if cache_path.exists():
        try:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.warning(f"Could not load transcript cache for {meeting_id}: {e}")
    return None


def _get_all_chunks(meeting_id: str, db_url: str) -> List[Dict]:
    """Fetch all chunks for a meeting — used for chunk quality checks."""
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn = psycopg2.connect(db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT dc.id, dc.content, dc.title, dc.chunk_type, dc.chunk_index,
                   d.id AS document_id, d.name AS document_name
            FROM staging.document_chunks dc
            JOIN staging.documents d ON dc.document_id = d.id
            WHERE d.meeting_id = %s
            ORDER BY dc.chunk_index
        """, (meeting_id,))
        chunks = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return chunks
    except Exception as e:
        log.error(f"Could not get all chunks: {e}")
        return []


def _get_sample_chunks(meeting_id: str, db_url: str, n: int = 10) -> List[Dict]:
    """Fetch n representative chunks from staging.document_chunks."""
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn = psycopg2.connect(db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        # Sample spread across the meeting: first, middle, and last chunks
        cur.execute("""
            SELECT dc.id, dc.content, dc.title, dc.chunk_type, dc.chunk_index,
                   d.name AS document_name
            FROM staging.document_chunks dc
            JOIN staging.documents d ON dc.document_id = d.id
            WHERE d.meeting_id = %s AND dc.content IS NOT NULL AND length(dc.content) > 100
            ORDER BY dc.chunk_index
        """, (meeting_id,))
        all_chunks = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()

        if len(all_chunks) <= n:
            return all_chunks

        # Sample evenly: first, spread, last
        step = len(all_chunks) // n
        return [all_chunks[i * step] for i in range(n)]
    except Exception as e:
        log.error(f"Could not get sample chunks: {e}")
        return []


# ── LLM hallucination check ───────────────────────────────────────────────────

def _run_hallucination_check(
    meeting_id: str,
    db_url: str,
    judge,
    n_samples: int = 5,
    max_hallucination_rate: float = 0.05,
) -> Dict:
    """
    Sample n chunks and run claim-level hallucination verification.

    The transcript cache is used as ground-truth context. If no cache is
    available, each chunk is verified against itself (weaker but still catches
    fabricated patterns internal to the chunk).
    """
    chunks = _get_sample_chunks(meeting_id, db_url, n=n_samples)
    transcript = _load_transcript_cache(meeting_id)

    if not chunks:
        return {"status": "skipped", "message": "No chunks available for evaluation"}

    # Build a per-document-name index of transcript segments so each chunk can
    # be compared against the segments from its own agenda item — not the whole
    # transcript. This avoids false positives from cross-section comparisons.
    #
    # Build a speaker → party lookup first so party appears CONSISTENTLY in the
    # context. Party attribution is only 32.8% in raw segments; without this,
    # the judge sees "(PvdA)" in a chunk but not in the context and flags it.
    speaker_party_lookup: Dict[str, str] = {}
    if transcript:
        for item in transcript.get("agenda_items", []):
            for seg in item.get("segments", []):
                sp = (seg.get("speaker") or "").strip()
                pt = (seg.get("party") or "").strip()
                if sp and pt and sp not in speaker_party_lookup:
                    speaker_party_lookup[sp] = pt

    agenda_context: Dict[str, str] = {}  # doc_name → segment text
    if transcript:
        for item in transcript.get("agenda_items", []):
            segs = item.get("segments", [])
            if not segs:
                continue
            lines = []
            for seg in segs:
                speaker = (seg.get("speaker") or "Onbekend").strip()
                party = (seg.get("party") or speaker_party_lookup.get(speaker, "")).strip()
                speaker_str = f"{speaker} ({party})" if party else speaker
                text = (seg.get("text") or "").strip()
                if text:
                    lines.append(f"[{speaker_str}]: {text}")
            if lines:
                agenda_context[item.get("title", "")] = "\n".join(lines)

    # Fallback: flat context from first 30 segments if agenda matching fails
    flat_fallback_context = ""
    if transcript:
        all_segs = flatten_segments(transcript)[:30]
        fallback_lines = []
        for s in all_segs:
            text = (s.get("text") or "").strip()
            if not text:
                continue
            sp = (s.get("speaker") or "Onbekend").strip()
            pt = (s.get("party") or speaker_party_lookup.get(sp, "")).strip()
            sp_str = f"{sp} ({pt})" if pt else sp
            fallback_lines.append(f"[{sp_str}]: {text}")
        flat_fallback_context = "\n".join(fallback_lines)

    chunk_results = []
    all_rates = []

    for chunk in chunks:
        text = chunk.get("content", "").strip()
        if len(text) < 50:
            continue

        # Match chunk to its agenda item context.
        # Priority: document_name (strip "Transcript: " prefix) > chunk_title > partial > flat fallback
        chunk_title = chunk.get("title") or ""
        doc_name = (chunk.get("document_name") or "").removeprefix("Transcript: ").strip()

        context = None
        for candidate in [doc_name, chunk_title]:
            if not candidate or context:
                continue
            context = agenda_context.get(candidate)
            if not context:
                for agenda_title, agenda_text in agenda_context.items():
                    if agenda_title and (
                        agenda_title.lower() in candidate.lower()
                        or candidate.lower() in agenda_title.lower()
                    ):
                        context = agenda_text
                        break

        if not context:
            context = flat_fallback_context if flat_fallback_context else text

        # Claim verification
        try:
            claims_result = judge.evaluate_claims(
                question=(
                    "Controleer alle feitelijke beweringen in dit fragment op "
                    "basis van het originele transcript."
                ),
                answer=text,
                context=context,
            )
        except Exception as e:
            log.warning(f"Claim verification failed for chunk {chunk.get('id')}: {e}")
            claims_result = {"status": "error", "message": str(e)}

        chunk_result = {
            "chunk_id": chunk.get("id"),
            "chunk_title": chunk.get("title"),
            "chunk_index": chunk.get("chunk_index"),
            "content_preview": text[:200],
            "claim_verification": claims_result,
        }
        chunk_results.append(chunk_result)

        rate = claims_result.get("hallucination_rate")
        if isinstance(rate, (int, float)):
            all_rates.append(float(rate))

        time.sleep(0.3)  # Rate limit between calls

    avg_rate = round(sum(all_rates) / len(all_rates), 4) if all_rates else None
    safe = avg_rate is not None and avg_rate <= max_hallucination_rate

    # Aggregate hallucination types found across all chunks
    all_hallucination_types: set = set()
    dangerous_claims = []
    for cr in chunk_results:
        cv = cr.get("claim_verification", {})
        for t in cv.get("hallucination_types_found", []):
            all_hallucination_types.add(t)
        most_dangerous = cv.get("most_dangerous_claim")
        if most_dangerous:
            dangerous_claims.append({
                "chunk_id": cr.get("chunk_id"),
                "claim": most_dangerous,
            })

    return {
        "status": "ok",
        "n_samples": len(chunk_results),
        "avg_hallucination_rate": avg_rate,
        "safe_for_councillors": safe,
        "hallucination_types_found": sorted(all_hallucination_types),
        "dangerous_claims": dangerous_claims,
        "chunk_results": chunk_results,
    }


# ── Verdict computation ───────────────────────────────────────────────────────

def _compute_verdict(results: Dict, config: NotulenAuditConfig) -> Dict:
    """
    Derive an overall APPROVE / PENDING / REJECT recommendation from all audit results.

    Priority: REJECT blocks promotion immediately. PENDING requires manual review.
    APPROVE only if all thresholds pass AND source is VTT.
    """
    issues = []      # Must fix before promotion
    warnings = []    # Review recommended but not blocking
    scores = {}

    # Hallucination check
    hall = results.get("hallucination_check", {})
    avg_rate = hall.get("avg_hallucination_rate")
    if avg_rate is not None:
        scores["hallucination_rate"] = avg_rate
        if avg_rate > 0.10:
            issues.append(f"High hallucination rate: {avg_rate:.1%} (threshold ≤10%)")
        elif avg_rate > config.max_hallucination_rate:
            warnings.append(f"Moderate hallucination rate: {avg_rate:.1%} (threshold {config.max_hallucination_rate:.0%})")

    for dc in hall.get("dangerous_claims", []):
        warnings.append(f"Dangerous claim in chunk {dc.get('chunk_id')}: {dc.get('claim', '')[:120]}")

    # Speaker attribution
    quality = results.get("transcript_quality", {})
    speaker_attr = quality.get("speaker_attribution", {})
    attr_rate = speaker_attr.get("attribution_rate")
    if attr_rate is not None:
        scores["speaker_attribution_rate"] = attr_rate
        if attr_rate < 0.50:
            issues.append(f"Very low speaker attribution: {attr_rate:.0%} (threshold ≥50%)")
        elif attr_rate < config.min_speaker_attribution_rate:
            warnings.append(f"Low speaker attribution: {attr_rate:.0%} (target {config.min_speaker_attribution_rate:.0%})")

    # NEER — only warn, not block (transcription errors are expected)
    neer = quality.get("neer", {})
    if neer.get("neer") is not None:
        scores["neer"] = neer["neer"]
        if neer["neer"] > 0.10:
            warnings.append(f"High NEER (many entity misspellings): {neer['neer']:.1%}")

    # DB consistency
    db = results.get("db_consistency", {})
    for issue in db.get("metadata", {}).get("issues", []):
        warnings.append(f"Metadata: {issue}")

    # Chunk quality
    cq = results.get("chunk_quality", {})
    ld = cq.get("length_distribution", {})
    if ld.get("empty_chunks", 0) > 0:
        issues.append(f"Chunk quality: {ld['empty_chunks']} empty chunks")
    if ld.get("tiny_rate", 0) > 0.15:
        warnings.append(f"Chunk quality: {ld['tiny_rate']:.0%} of chunks are tiny (<50 chars)")
    if ld.get("oversized_rate", 0) > 0.05:
        warnings.append(f"Chunk quality: {ld['oversized_rate']:.0%} of chunks exceed 5000 chars")

    bp = cq.get("boilerplate", {})
    if bp.get("boilerplate_rate", 0) > 0.20:
        warnings.append(f"Chunk quality: {bp['boilerplate_rate']:.0%} boilerplate chunks")

    dupes = cq.get("duplicates", {})
    if dupes.get("duplicate_chunk_count", 0) > 0:
        issues.append(f"Chunk quality: {dupes['duplicate_chunk_count']} duplicate chunks")

    agenda_cov = cq.get("agenda_coverage", {})
    if agenda_cov.get("empty_documents", 0) > 0:
        issues.append(
            f"Chunk quality: {agenda_cov['empty_documents']} documents have zero chunks "
            f"({', '.join(agenda_cov.get('empty_document_names', [])[:3])})"
        )

    # Pipeline quality score
    meeting_info = results.get("meeting_info", {})
    pipeline_score = meeting_info.get("quality_score")
    if pipeline_score is not None:
        scores["pipeline_quality_score"] = pipeline_score
        if pipeline_score < 0.40:
            issues.append(f"Very low pipeline quality score: {pipeline_score:.2f}")

    # Determine recommendation
    transcript_source = meeting_info.get("transcript_source", "")
    if issues:
        recommendation = "REJECT — fix issues before promotion"
    elif transcript_source == "whisper":
        recommendation = "PENDING — Whisper source always requires manual review"
    elif warnings:
        recommendation = "PENDING — warnings present, manual review recommended"
    elif pipeline_score is not None and pipeline_score < config.min_quality_score:
        recommendation = f"PENDING — pipeline quality {pipeline_score:.2f} < threshold {config.min_quality_score}"
    else:
        recommendation = "APPROVE — all thresholds met, safe to promote"

    return {
        "recommendation": recommendation,
        "issues": issues,
        "warnings": warnings,
        "scores": scores,
    }


# ── Main auditor class ────────────────────────────────────────────────────────

class NotulenAuditor:
    """Orchestrates a full audit of a single virtual notulen meeting."""

    def __init__(self, config: NotulenAuditConfig = None):
        self.config = config or NotulenAuditConfig()
        self.dictionary = load_political_dictionary(self.config.lexicon_path)
        self.judge = None

    def _init_judge(self):
        if self.judge is not None:
            return
        try:
            self.judge = _create_notulen_judge(self.config)
            log.info(f"Judge initialized: {self.judge.backend_name}")
        except Exception as e:
            log.warning(f"LLM judge unavailable ({e}). LLM steps will be skipped.")

    def run_audit(self, meeting_id: str, n_hallucination_samples: int = 5,
                  skip_llm: bool = False) -> Dict:
        """
        Run a complete audit for one meeting. Returns a JSON-serializable result dict.
        """
        cfg = self.config
        log.info("=" * 60)
        log.info(f"  Virtual Notulen Audit")
        log.info(f"  Meeting ID: {meeting_id}")
        log.info("=" * 60)

        audit = {
            "meeting_id": meeting_id,
            "audit_timestamp": datetime.now().isoformat(),
            "config": cfg.snapshot(),
        }

        # ── Step 1: Load staging data ──────────────────────────────────────
        log.info("Step 1/5: Loading staging data")
        staging = _load_staging_meeting(meeting_id, cfg.db_url)
        if not staging.get("meeting"):
            audit["error"] = f"Meeting {meeting_id} not found in staging"
            return audit

        m = staging["meeting"]
        audit["meeting_info"] = {
            "name": m.get("name"),
            "start_date": str(m.get("start_date") or ""),
            "committee": m.get("committee"),
            "quality_score": m.get("quality_score"),
            "review_status": m.get("review_status"),
            "transcript_source": m.get("transcript_source"),
            "promoted_at": str(m.get("promoted_at") or ""),
            "chunk_count": staging.get("chunk_count", 0),
            "document_count": len(staging.get("documents", [])),
        }
        log.info(f"  Name: {audit['meeting_info']['name']}")
        log.info(f"  Source: {audit['meeting_info']['transcript_source']}  "
                 f"Chunks: {audit['meeting_info']['chunk_count']}")

        # ── Step 2: Transcript quality ─────────────────────────────────────
        log.info("Step 2/5: Transcript quality metrics")
        transcript = _load_transcript_cache(meeting_id)
        if transcript:
            quality = run_all_transcript_quality(transcript, self.dictionary)
            audit["transcript_quality"] = quality
            attr = quality["speaker_attribution"]["attribution_rate"]
            seg_count = quality["segment_quality"].get("total_segments", 0)
            log.info(f"  Segments: {seg_count}  Speaker attribution: {attr:.0%}")
            if quality["neer"]["neer"] is not None:
                log.info(f"  NEER: {quality['neer']['neer']:.3f}")
        else:
            audit["transcript_quality"] = {
                "status": "skipped",
                "message": "Transcript cache not found — run pipeline first or re-download",
            }
            log.warning("  No transcript cache found — quality metrics skipped")

        # ── Step 3: DB consistency ─────────────────────────────────────────
        log.info("Step 3/5: DB consistency check")
        chunks_sample = _get_sample_chunks(meeting_id, cfg.db_url, n=20)
        metadata_check = check_meeting_metadata_consistency(meeting_id, cfg.db_url)
        entity_check = check_entity_consistency_with_production(
            meeting_id, chunks_sample, cfg.db_url
        )
        speaker_check: Dict = {}
        if transcript:
            all_segs = flatten_segments(transcript)
            speaker_check = check_speaker_presence(all_segs, self.dictionary)

        audit["db_consistency"] = {
            "metadata": metadata_check,
            "entity_consistency": entity_check,
            "speaker_presence": speaker_check,
        }

        if metadata_check.get("issues"):
            log.warning(f"  Metadata issues: {metadata_check['issues']}")
        else:
            log.info("  Metadata: OK")
        if speaker_check.get("recognition_rate") is not None:
            log.info(f"  Speaker recognition: {speaker_check['recognition_rate']:.0%}")

        # ── Step 4: Chunk quality ──────────────────────────────────────────
        log.info("Step 4/5: Chunk quality check")
        all_chunks = _get_all_chunks(meeting_id, cfg.db_url)
        chunk_quality = run_chunk_quality(all_chunks, cfg.db_url, meeting_id)
        audit["chunk_quality"] = chunk_quality

        ld = chunk_quality.get("length_distribution", {})
        bp = chunk_quality.get("boilerplate", {})
        dupes = chunk_quality.get("duplicates", {})
        log.info(f"  Chunks: {ld.get('total_chunks', 0)}  "
                 f"tiny: {ld.get('tiny_rate', 0):.0%}  "
                 f"boilerplate: {bp.get('boilerplate_rate', 0):.0%}  "
                 f"dupes: {dupes.get('duplicate_chunk_count', 0)}")

        # ── Step 5: LLM hallucination check ───────────────────────────────
        log.info(f"Step 5/5: Hallucination check (n={n_hallucination_samples})")
        if skip_llm:
            audit["hallucination_check"] = {"status": "skipped", "message": "--skip-llm flag set"}
            log.info("  Skipped (--skip-llm)")
        else:
            self._init_judge()
            if self.judge:
                hall = _run_hallucination_check(
                    meeting_id, cfg.db_url, self.judge,
                    n_samples=n_hallucination_samples,
                    max_hallucination_rate=cfg.max_hallucination_rate,
                )
                audit["hallucination_check"] = hall
                avg_r = hall.get("avg_hallucination_rate")
                if avg_r is not None:
                    safe = hall.get("safe_for_councillors")
                    log.info(f"  Avg hallucination rate: {avg_r:.1%}  "
                             f"{'SAFE' if safe else 'REVIEW NEEDED'}")
            else:
                audit["hallucination_check"] = {
                    "status": "skipped", "message": "No LLM judge available"
                }

        # ── Verdict ────────────────────────────────────────────────────────
        audit["verdict"] = _compute_verdict(audit, cfg)
        log.info(f"Verdict: {audit['verdict']['recommendation']}")
        log.info("=" * 60)

        return audit


# ── CLI helpers ───────────────────────────────────────────────────────────────

def list_staging_meetings(db_url: str):
    """Print all staging meetings in a formatted table."""
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn = psycopg2.connect(db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, name, start_date, committee, quality_score,
                   review_status, transcript_source, promoted_at
            FROM staging.meetings
            ORDER BY start_date DESC NULLS LAST
        """)
        meetings = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()

        if not meetings:
            print("No meetings found in staging.")
            return

        header = (
            f"{'ID':<38} {'Date':<12} {'Score':<7} "
            f"{'Status':<22} {'Source':<10} {'Name'}"
        )
        print("\n" + header)
        print("-" * 110)

        STATUS_COLORS = {
            "approved": "\033[92m",
            "auto_approved": "\033[92m",
            "rejected": "\033[91m",
            "auto_rejected": "\033[91m",
            "pending": "\033[93m",
        }
        RESET = "\033[0m"

        for m in meetings:
            status = str(m.get("review_status") or "")
            color = STATUS_COLORS.get(status, "")
            score = f"{m['quality_score']:.2f}" if m.get("quality_score") is not None else "-"
            date = str(m.get("start_date") or "")[:10]
            promoted = " [promoted]" if m.get("promoted_at") else ""
            name = str(m.get("name") or "")[:55]
            print(
                f"{str(m['id']):<38} {date:<12} {score:<7} "
                f"{color}{status:<22}{RESET} {str(m.get('transcript_source') or ''):<10} "
                f"{name}{promoted}"
            )
    except Exception as e:
        print(f"Error listing meetings: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Virtual Notulen Audit Runner — one meeting at a time"
    )
    parser.add_argument("--meeting-id", help="Meeting ID to audit")
    parser.add_argument("--list", action="store_true", help="List all staging meetings")
    parser.add_argument(
        "--samples", type=int, default=5,
        help="Number of chunks to sample for hallucination check (default: 5)"
    )
    parser.add_argument(
        "--skip-llm", action="store_true",
        help="Skip LLM-based checks (hallucination, RAG quality) for a fast structural audit"
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory for audit results (default: eval_notulen/runs/<meeting_id>)"
    )
    args = parser.parse_args()

    config = NotulenAuditConfig()
    runs_dir = Path(args.output_dir) if args.output_dir else RUNS_DIR

    if args.list:
        list_staging_meetings(config.db_url)
    elif args.meeting_id:
        auditor = NotulenAuditor(config)
        results = auditor.run_audit(
            args.meeting_id,
            n_hallucination_samples=args.samples,
            skip_llm=args.skip_llm,
        )

        json_path = save_audit_results(args.meeting_id, results, runs_dir)
        report = generate_audit_report(results)
        report_path = save_report(args.meeting_id, report, runs_dir)

        print("\n" + "=" * 60)
        print(report)
        print("=" * 60)
        print(f"\nJSON results → {json_path}")
        print(f"Markdown report → {report_path}")
    else:
        parser.print_help()
