"""
Audit Report Generator for Virtual Notulen

Produces two output formats per audit run:
  - audit_{timestamp}.json  — machine-readable, for trend tracking and automation
  - report_{timestamp}.md   — human-readable, for the manual review workflow

Saved to: eval_notulen/runs/{meeting_id}/
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

RUNS_DIR = Path(__file__).resolve().parent / "runs"


# ── Persistence ───────────────────────────────────────────────────────────────

def save_audit_results(
    meeting_id: str, results: Dict, runs_dir: Optional[Path] = None
) -> Path:
    """Write JSON results to eval_notulen/runs/{meeting_id}/audit_{ts}.json."""
    if runs_dir is None:
        runs_dir = RUNS_DIR
    run_dir = runs_dir / meeting_id
    run_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = run_dir / f"audit_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    return path


def save_report(
    meeting_id: str, report: str, runs_dir: Optional[Path] = None
) -> Path:
    """Write markdown report to eval_notulen/runs/{meeting_id}/report_{ts}.md."""
    if runs_dir is None:
        runs_dir = RUNS_DIR
    run_dir = runs_dir / meeting_id
    run_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = run_dir / f"report_{ts}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    return path


# ── Formatting helpers ────────────────────────────────────────────────────────

def _pct(value, decimals: int = 1) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{decimals}%}"


def _score(value, out_of: int = 5) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}/{out_of}"


def _rate(value) -> str:
    if value is None:
        return "N/A"
    return f"{value:.4f}"


# ── Report generation ─────────────────────────────────────────────────────────

def generate_audit_report(results: Dict) -> str:
    """Generate a human-readable markdown audit report from audit results."""
    meeting_id = results.get("meeting_id", "unknown")
    info = results.get("meeting_info", {})
    verdict = results.get("verdict", {})

    lines = [
        "# Virtual Notulen — Audit Report",
        "",
        f"**Meeting:** {info.get('name', meeting_id)}",
        f"**Date:** {str(info.get('start_date', ''))[:10] or 'unknown'}",
        f"**Committee:** {info.get('committee', 'unknown')}",
        f"**Transcript source:** {info.get('transcript_source', 'unknown')}",
        f"**Chunks indexed:** {info.get('chunk_count', 0)}",
        f"**Pipeline quality score:** {info.get('quality_score', 'N/A')}",
        f"**Audit timestamp:** {results.get('audit_timestamp', '')[:19]}",
        "",
        "---",
        "",
        f"## Verdict: {verdict.get('recommendation', 'UNKNOWN')}",
        "",
    ]

    if verdict.get("issues"):
        lines.append("### Issues — must fix before promotion")
        for issue in verdict["issues"]:
            lines.append(f"- **{issue}**")
        lines.append("")

    if verdict.get("warnings"):
        lines.append("### Warnings — review recommended")
        for warning in verdict["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    if verdict.get("scores"):
        lines.append("### Score summary")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        for key, val in sorted(verdict["scores"].items()):
            lines.append(f"| {key} | {val} |")
        lines.append("")

    # ── 1. Transcript quality ──────────────────────────────────────────────
    lines += ["---", "", "## 1. Transcript Quality", ""]
    quality = results.get("transcript_quality", {})

    if isinstance(quality, dict) and "status" not in quality:
        seg_q = quality.get("segment_quality", {})
        speaker_attr = quality.get("speaker_attribution", {})
        speaker_div = quality.get("speaker_diversity", {})
        neer = quality.get("neer", {})
        agenda = quality.get("agenda_coverage", {})

        lines.append("### Segments")
        lines.append(f"- Total segments: {seg_q.get('total_segments', 0):,}")
        lines.append(f"- Total words: {seg_q.get('total_words', 0):,}")
        lines.append(f"- Avg words/segment: {seg_q.get('avg_words_per_segment', 0):.1f}")
        lines.append(f"- Short segment rate (<10 words): {_pct(seg_q.get('short_segment_rate'))}")
        if seg_q.get("avg_confidence") is not None:
            lines.append(f"- Avg Whisper confidence: {seg_q['avg_confidence']:.3f}")
        if seg_q.get("low_confidence_rate") is not None:
            lines.append(f"- Low confidence rate (<0.5): {_pct(seg_q.get('low_confidence_rate'))}")
        lines.append("")

        lines.append("### Speaker Attribution")
        lines.append(f"- Attribution rate: {_pct(speaker_attr.get('attribution_rate'))}")
        lines.append(f"- Party attribution rate: {_pct(speaker_attr.get('party_attribution_rate'))}")
        lines.append(
            f"- Attributed: {speaker_attr.get('attributed_segments', 0)} / "
            f"{speaker_attr.get('total_segments', 0)} segments"
        )
        lines.append("")

        lines.append("### Speaker Diversity")
        lines.append(f"- Unique speakers: {speaker_div.get('unique_speakers', 0)}")
        lines.append(f"- Unique parties: {speaker_div.get('unique_parties', 0)}")
        lines.append(f"- Monologue rate (top speaker): {_pct(speaker_div.get('monologue_rate'))}")
        if speaker_div.get("top_speaker"):
            lines.append(
                f"- Top speaker: {speaker_div['top_speaker']} "
                f"({speaker_div.get('top_speaker_segment_count', 0)} segments)"
            )
        if speaker_div.get("parties"):
            party_list = ", ".join(
                f"{p} ({n})" for p, n in speaker_div["parties"].items()
            )
            lines.append(f"- Parties: {party_list}")
        lines.append("")

        if neer.get("total_occurrences", 0) > 0:
            lines.append("### Named Entity Error Rate (NEER)")
            lines.append(f"- NEER: {_rate(neer.get('neer'))} (lower = better)")
            lines.append(f"- Entity occurrences checked: {neer.get('total_occurrences', 0)}")
            lines.append(f"- Correctly spelled: {neer.get('correct_count', 0)}")
            lines.append(f"- Misspelled: {neer.get('error_count', 0)}")
            if neer.get("error_examples"):
                lines.append("- Misspelling examples:")
                for ex in neer["error_examples"][:5]:
                    lines.append(f"  - `{ex['wrong']}` → should be `{ex['correct']}`")
            lines.append("")
        else:
            lines.append("### Named Entity Error Rate (NEER)")
            lines.append("- *No known entity occurrences found in transcript*")
            lines.append("")

        lines.append("### Agenda Coverage")
        lines.append(f"- Coverage: {_pct(agenda.get('coverage_rate'))}")
        lines.append(
            f"- Items with content: {agenda.get('items_with_content', 0)} / "
            f"{agenda.get('agenda_items_total', 0)}"
        )
        if agenda.get("items"):
            lines.append("- Agenda items:")
            for item in agenda["items"][:8]:
                lines.append(f"  - {item['title']} ({item['segment_count']} segments)")
        lines.append("")
    else:
        lines.append(f"*{quality.get('message', 'Quality metrics not available')}*\n")

    # ── 2. DB consistency ──────────────────────────────────────────────────
    lines += ["---", "", "## 2. DB Consistency", ""]
    db = results.get("db_consistency", {})

    metadata = db.get("metadata", {})
    lines.append("### Metadata vs. production.meetings")
    if metadata.get("status") == "error":
        lines.append(f"- Error: {metadata.get('message')}")
    elif metadata.get("status") == "not_found":
        lines.append("- Meeting not found in staging")
    elif metadata.get("issues"):
        for issue in metadata["issues"]:
            lines.append(f"- ⚠ {issue}")
    else:
        lines.append("- ✓ No metadata discrepancies")
        if metadata.get("production") is None:
            lines.append("  *(Meeting will be inserted on first promotion)*")
    lines.append("")

    speaker_presence = db.get("speaker_presence", {})
    if speaker_presence:
        lines.append("### Speaker Presence Check")
        lines.append(f"- Unique speakers: {speaker_presence.get('total_unique_speakers', 0)}")
        lines.append(f"- Recognition rate: {_pct(speaker_presence.get('recognition_rate'))}")

        recognized = speaker_presence.get("recognized", [])
        if recognized:
            lines.append("- Recognized speakers:")
            for s in recognized[:10]:
                badge = s.get("recognized_as", "")
                parties = ", ".join(s.get("parties", []))
                lines.append(
                    f"  - {s['name']} ({s['segment_count']} segs)"
                    f"{' [' + badge + ']' if badge else ''}"
                    f"{' (' + parties + ')' if parties else ''}"
                )

        unrecognized = speaker_presence.get("unrecognized", [])
        if unrecognized:
            lines.append("- Unrecognized speakers (review):")
            for s in unrecognized[:5]:
                lines.append(f"  - **{s['name']}** ({s['segment_count']} segments)")
        lines.append("")

    entity = db.get("entity_consistency", {})
    if entity.get("status") == "ok":
        lines.append("### Entity Cross-Reference with Production")
        lines.append(f"- Committee: {entity.get('committee_id')}")
        lines.append(f"- Overlap rate: {_pct(entity.get('overlap_rate'))}")
        lines.append(f"- Staging-only entities: {entity.get('staging_only_entities_count', 0)}")
        suspicious = entity.get("suspicious_staging_only", [])
        if suspicious:
            lines.append(f"- Suspicious staging-only names: {', '.join(suspicious[:10])}")
        lines.append(f"- *{entity.get('note', '')}*")
        lines.append("")
    elif entity.get("status") == "skipped":
        lines.append(f"### Entity Cross-Reference\n*{entity.get('message')}*\n")

    # ── 3. Hallucination check ─────────────────────────────────────────────
    lines += ["---", "", "## 3. Hallucination Check (LLM)", ""]
    hall = results.get("hallucination_check", {})

    if hall.get("status") == "skipped":
        lines.append(f"*{hall.get('message', 'Skipped')}*\n")
    elif hall.get("status") == "error":
        lines.append(f"*Error: {hall.get('message')}*\n")
    else:
        avg_rate = hall.get("avg_hallucination_rate")
        safe = hall.get("safe_for_councillors")
        n = hall.get("n_samples", 0)

        lines.append(f"- Chunks sampled: {n}")
        if avg_rate is not None:
            lines.append(f"- Avg hallucination rate: {_pct(avg_rate)}")
            safety_str = "✓ SAFE" if safe else "⚠ REVIEW NEEDED"
            lines.append(f"- Safe for councillors: {safety_str}")

        if hall.get("hallucination_types_found"):
            lines.append(f"- Hallucination types: {', '.join(hall['hallucination_types_found'])}")

        dangerous = hall.get("dangerous_claims", [])
        if dangerous:
            lines.append("\n### Dangerous Claims (require immediate review)")
            for dc in dangerous:
                lines.append(f"- Chunk {dc.get('chunk_id')}: *{dc.get('claim', '')}*")

        lines.append("")
        lines.append("### Per-Chunk Results")
        for cr in hall.get("chunk_results", [])[:5]:
            cv = cr.get("claim_verification", {})
            tf = cr.get("transcript_faithfulness", {})
            hall_rate = cv.get("hallucination_rate")
            faith_score = tf.get("score")
            title = cr.get("chunk_title") or f"Chunk {cr.get('chunk_id')}"
            lines.append(
                f"- **{title}** — hallucination {_pct(hall_rate)}"
                f"{f', faithfulness {faith_score}/5' if faith_score is not None else ''}"
            )
            if cv.get("most_dangerous_claim"):
                lines.append(f"  - ⚠ {cv['most_dangerous_claim'][:150]}")
        lines.append("")

    # ── 4. Chunk quality ───────────────────────────────────────────────────
    lines += ["---", "", "## 4. Chunk Quality", ""]
    cq = results.get("chunk_quality", {})

    ld = cq.get("length_distribution", {})
    if ld:
        lines.append("### Length Distribution")
        lines.append(f"- Total chunks: {ld.get('total_chunks', 0)}")
        lines.append(f"- Empty chunks: {ld.get('empty_chunks', 0)}")
        lines.append(f"- Min / Avg / Max chars: "
                     f"{ld.get('min_chars', 0)} / {ld.get('avg_chars', 0):.0f} / {ld.get('max_chars', 0)}")
        if ld.get("buckets"):
            for bucket, count in sorted(ld["buckets"].items()):
                pct = _pct(count / ld["total_chunks"]) if ld.get("total_chunks") else "N/A"
                lines.append(f"  - {bucket}: {count} ({pct})")
        lines.append("")

    bp = cq.get("boilerplate", {})
    if bp:
        lines.append("### Boilerplate")
        lines.append(f"- Boilerplate chunks: {bp.get('boilerplate_count', 0)} "
                     f"({_pct(bp.get('boilerplate_rate'))})")
        if bp.get("boilerplate_examples"):
            lines.append("- Examples:")
            for ex in bp["boilerplate_examples"][:3]:
                lines.append(f"  - `{ex.get('preview', '')[:80]}`")
        lines.append("")

    dupes = cq.get("duplicates", {})
    if dupes:
        lines.append("### Duplicates")
        lines.append(f"- Duplicate groups: {dupes.get('duplicate_groups', 0)}")
        lines.append(f"- Duplicate chunks: {dupes.get('duplicate_chunk_count', 0)} "
                     f"({_pct(dupes.get('duplicate_rate'))})")
        if dupes.get("examples"):
            for ex in dupes["examples"][:2]:
                lines.append(f"  - `{ex['preview'][:80]}` (×{ex['count']})")
        lines.append("")

    agenda_cov = cq.get("agenda_coverage", {})
    if agenda_cov.get("status") != "error" and agenda_cov:
        lines.append("### Document Coverage")
        lines.append(f"- Documents: {agenda_cov.get('documents_with_chunks', 0)} / "
                     f"{agenda_cov.get('total_documents', 0)} have chunks")
        if agenda_cov.get("empty_document_names"):
            lines.append("- Documents with no chunks:")
            for name in agenda_cov["empty_document_names"]:
                lines.append(f"  - {name}")
        if agenda_cov.get("documents"):
            lines.append("- Per-document chunk counts:")
            for doc in agenda_cov["documents"]:
                lines.append(f"  - {doc['name'][:70]}: {doc['chunks']} chunks")
        lines.append("")

    ct = cq.get("chunk_types", {})
    if ct.get("distribution"):
        lines.append("### Chunk Types")
        for ctype, info in ct["distribution"].items():
            lines.append(f"  - {ctype}: {info['count']} ({_pct(info['rate'])})")
        lines.append("")

    # ── EU AI Act metadata ─────────────────────────────────────────────────
    lines += [
        "---", "",
        "## EU AI Act Transparency Metadata (Art. 13 & 50)",
        "",
        "| Field | Value |",
        "|-------|-------|",
        "| System type | AI-generated meeting minutes (virtual notulen) |",
        f"| Source material | Video recording + "
        f"{'VTT subtitles' if info.get('transcript_source') == 'vtt' else 'Whisper ASR'} |",
        "| Post-processing | Two-pass LLM correction (Gemini Flash Lite) |",
        f"| Human review status | {verdict.get('recommendation', 'Pending')} |",
        f"| Human review required | "
        f"{'Yes — Whisper source' if info.get('transcript_source') == 'whisper' else 'Recommended before formal use'} |",
        f"| Audit date | {results.get('audit_timestamp', '')[:10]} |",
        f"| Meeting ID | {meeting_id} |",
        "",
    ]

    return "\n".join(lines)
