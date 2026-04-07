#!/usr/bin/env python3
"""
Batch audit all staging meetings.

Phase 1: Structural audit (--skip-llm) on ALL meetings — fast, no API calls.
Phase 2: Full LLM hallucination check on N meetings per committee.

Produces a summary CSV at eval_notulen/runs/batch_summary.csv
"""

import os
import sys
import csv
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import psycopg2
from psycopg2.extras import RealDictCursor

from eval_notulen.config import NotulenAuditConfig, RUNS_DIR
from eval_notulen.audit_runner import NotulenAuditor
from eval_notulen.reporter import generate_audit_report, save_audit_results, save_report

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/neodemos")


def get_staging_meetings(status_filter=None):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SET search_path TO staging, public")

    query = "SELECT id, name, committee, start_date, quality_score, review_status, transcript_source FROM meetings"
    params = []
    if status_filter:
        query += " WHERE review_status = %s"
        params.append(status_filter)
    query += " ORDER BY start_date DESC"

    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def main():
    parser = argparse.ArgumentParser(description="Batch audit staging meetings")
    parser.add_argument("--llm-per-committee", type=int, default=2,
                        help="Number of meetings per committee to run full LLM audit (default: 2)")
    parser.add_argument("--llm-samples", type=int, default=5,
                        help="Chunk samples per LLM hallucination check (default: 5)")
    parser.add_argument("--skip-structural", action="store_true",
                        help="Skip structural audits (only run LLM phase)")
    parser.add_argument("--status", type=str, default=None,
                        help="Filter by review_status (e.g., auto_approved)")
    parser.add_argument("--judge", choices=["claude", "gemini", "local"], default="gemini",
                        help="LLM judge backend: gemini (default, cheapest), claude, or local (Qwen MLX)")
    args = parser.parse_args()

    meetings = get_staging_meetings(args.status)
    print(f"Found {len(meetings)} staging meetings")

    config = NotulenAuditConfig()
    config.judge_backend = args.judge
    auditor = NotulenAuditor(config)
    summary_rows = []

    # ── Phase 1: Structural audit (all meetings, no LLM) ────────────────
    if not args.skip_structural:
        print(f"\n{'='*60}")
        print(f"Phase 1: Structural audit ({len(meetings)} meetings, --skip-llm)")
        print(f"{'='*60}\n")

        for i, m in enumerate(meetings, 1):
            mid = str(m["id"])
            name = (m["name"] or "")[:50]
            committee = m["committee"] or "other"

            try:
                results = auditor.run_audit(mid, skip_llm=True)
                verdict = results.get("verdict", {})
                rec = verdict.get("recommendation", "UNKNOWN")

                # Save results
                save_audit_results(mid, results, RUNS_DIR)
                report = generate_audit_report(results)
                save_report(mid, report, RUNS_DIR)

                # Collect summary
                scores = verdict.get("scores", {})
                row = {
                    "meeting_id": mid,
                    "name": m["name"],
                    "committee": committee,
                    "date": str(m["start_date"] or "")[:10],
                    "source": m["transcript_source"],
                    "pipeline_score": m["quality_score"],
                    "speaker_attr": scores.get("speaker_attribution_rate"),
                    "neer": scores.get("neer"),
                    "chunk_count": results.get("meeting_info", {}).get("chunk_count", 0),
                    "issues": len(verdict.get("issues", [])),
                    "warnings": len(verdict.get("warnings", [])),
                    "structural_verdict": rec.split(" — ")[0] if " — " in rec else rec,
                    "hallucination_rate": None,
                    "llm_verdict": None,
                }
                summary_rows.append(row)

                status = "ISSUE" if row["issues"] > 0 else "OK"
                if i % 25 == 0 or i == len(meetings):
                    print(f"  [{i}/{len(meetings)}] {status} | {committee:>8} | {name}")

            except Exception as e:
                print(f"  [{i}/{len(meetings)}] ERROR {mid[:8]}...: {e}")
                summary_rows.append({
                    "meeting_id": mid, "name": m["name"], "committee": committee,
                    "date": str(m["start_date"] or "")[:10], "source": m["transcript_source"],
                    "pipeline_score": m["quality_score"], "structural_verdict": "ERROR",
                    "issues": 0, "warnings": 0, "chunk_count": 0,
                    "speaker_attr": None, "neer": None,
                    "hallucination_rate": None, "llm_verdict": None,
                })

    # ── Phase 2: LLM hallucination check (sample per committee) ─────────
    if args.llm_per_committee > 0:
        # Group by committee, pick N auto_approved meetings with most chunks
        from collections import defaultdict
        by_committee = defaultdict(list)
        for row in summary_rows or [{"meeting_id": str(m["id"]), "committee": m["committee"] or "other",
                                      "chunk_count": 0, **{k: None for k in ["name","date","source","pipeline_score",
                                      "speaker_attr","neer","issues","warnings","structural_verdict",
                                      "hallucination_rate","llm_verdict"]}} for m in meetings]:
            by_committee[row.get("committee", "other")].append(row)

        # If we skipped structural, load meetings directly
        if args.skip_structural:
            for m in meetings:
                mid = str(m["id"])
                committee = m["committee"] or "other"
                by_committee[committee].append({"meeting_id": mid, "committee": committee,
                    "chunk_count": 999, "name": m["name"]})

        llm_targets = []
        for committee, rows in sorted(by_committee.items()):
            # Pick top N by chunk count (more chunks = more substantive meeting)
            sorted_rows = sorted(rows, key=lambda r: r.get("chunk_count", 0) or 0, reverse=True)
            for row in sorted_rows[:args.llm_per_committee]:
                llm_targets.append(row)

        print(f"\n{'='*60}")
        print(f"Phase 2: LLM hallucination check ({len(llm_targets)} meetings, {args.llm_samples} samples each)")
        print(f"{'='*60}\n")

        for i, target in enumerate(llm_targets, 1):
            mid = target["meeting_id"]
            committee = target.get("committee", "?")
            name = (target.get("name") or "")[:50]

            try:
                results = auditor.run_audit(mid, n_hallucination_samples=args.llm_samples, skip_llm=False)
                verdict = results.get("verdict", {})
                rec = verdict.get("recommendation", "UNKNOWN")
                hall = results.get("hallucination_check", {})
                avg_rate = hall.get("avg_hallucination_rate")

                save_audit_results(mid, results, RUNS_DIR)
                report = generate_audit_report(results)
                save_report(mid, report, RUNS_DIR)

                # Update summary row
                for row in summary_rows:
                    if row["meeting_id"] == mid:
                        row["hallucination_rate"] = avg_rate
                        row["llm_verdict"] = rec.split(" — ")[0] if " — " in rec else rec
                        break

                safe = "SAFE" if hall.get("safe_for_councillors") else "REVIEW"
                rate_str = f"{avg_rate:.1%}" if avg_rate is not None else "N/A"
                print(f"  [{i}/{len(llm_targets)}] {rate_str:>6} {safe:<6} | {committee:>8} | {name}")

            except Exception as e:
                print(f"  [{i}/{len(llm_targets)}] ERROR {mid[:8]}...: {e}")

    # ── Write summary CSV ────────────────────────────────────────────────
    csv_path = RUNS_DIR / f"batch_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if summary_rows:
        fieldnames = list(summary_rows[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(summary_rows)
        print(f"\nSummary CSV → {csv_path}")

    # ── Print summary stats ──────────────────────────────────────────────
    if summary_rows:
        total = len(summary_rows)
        with_issues = sum(1 for r in summary_rows if r.get("issues", 0) > 0)
        llm_done = [r for r in summary_rows if r.get("hallucination_rate") is not None]
        avg_hall = sum(r["hallucination_rate"] for r in llm_done) / len(llm_done) if llm_done else None

        print(f"\n{'='*60}")
        print(f"Batch Audit Summary")
        print(f"{'='*60}")
        print(f"  Total meetings:       {total}")
        print(f"  Structural issues:    {with_issues}")
        print(f"  LLM audited:          {len(llm_done)}")
        if avg_hall is not None:
            print(f"  Avg hallucination:    {avg_hall:.1%}")
        print(f"  Reports:              {RUNS_DIR}/")
        print(f"  Summary CSV:          {csv_path}")


if __name__ == "__main__":
    main()
