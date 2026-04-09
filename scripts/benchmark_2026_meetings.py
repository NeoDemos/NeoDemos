#!/usr/bin/env python3
"""
Benchmark script for the 11 test meetings from 2026.

Reads staging cache JSONs (raw + processed) and outputs a CSV with quality
metrics. Used as before/after comparison for pipeline hardening phases.

Usage:
    python scripts/benchmark_2026_meetings.py                # print table
    python scripts/benchmark_2026_meetings.py --csv out.csv   # save CSV
"""

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/neodemos")
CACHE_DIR = Path("output/transcripts/staging_cache")

GARBAGE_PATTERNS = re.compile(
    r'^(Even kijken|Jawel|Jazeker|Goedemiddag|Goedemorgen|Goedenavond|'
    r'collega|aanname|makka|Inspreker|Unknown|Spreker onbekend)$',
    re.IGNORECASE,
)

DISFLUENCY_RE = re.compile(r'\b(eh|uhm?|uh|hmm?)\b', re.IGNORECASE)


def analyze_meeting(mid: str):
    """Analyze a single meeting's transcript quality."""
    raw_path = CACHE_DIR / f"{mid}.json"
    proc_path = CACHE_DIR / f"{mid}_processed.json"

    if not raw_path.exists():
        return None

    with open(raw_path) as f:
        raw = json.load(f)

    # Use processed version if available, else raw
    use_path = proc_path if proc_path.exists() else raw_path
    with open(use_path) as f:
        data = json.load(f)

    raw_segments = raw.get("total_segments", 0)

    # Count final segments, speakers, parties, issues
    total_segs = 0
    with_speaker = 0
    with_party = 0
    garbage_speakers = set()
    incomplete = 0
    disfluency_count = 0
    ellipsis_count = 0

    for item in data.get("agenda_items", []):
        for seg in item.get("segments", []):
            total_segs += 1
            text = seg.get("text", "").strip()
            speaker = seg.get("speaker", "")
            party = seg.get("party", "")

            # Speaker attribution
            if speaker and speaker not in ("Unknown", "Spreker onbekend", ""):
                with_speaker += 1
                if GARBAGE_PATTERNS.match(speaker):
                    garbage_speakers.add(speaker)
                if party:
                    with_party += 1

            # Incomplete sentences
            if len(text) > 20 and text[-1] not in '.!?:;)"\'':
                incomplete += 1

            # Disfluencies
            if DISFLUENCY_RE.search(text):
                disfluency_count += 1

            # Ellipsis gaps
            if "\u2026" in text or "..." in text:
                ellipsis_count += 1

    n_items = len(data.get("agenda_items", []))
    retention = total_segs / raw_segments if raw_segments else 0
    speaker_rate = with_speaker / total_segs if total_segs else 0
    party_rate = with_party / with_speaker if with_speaker else 0
    incomplete_rate = incomplete / total_segs if total_segs else 0

    return {
        "meeting_id": mid,
        "date": raw.get("date", "?"),
        "committee": raw.get("meeting_name", "")[:45],
        "duration": raw.get("duration", "?"),
        "raw_segments": raw_segments,
        "final_segments": total_segs,
        "segment_retention": round(retention, 3),
        "speaker_attr_rate": round(speaker_rate, 3),
        "party_attr_rate": round(party_rate, 3),
        "garbage_speakers": ", ".join(sorted(garbage_speakers)) if garbage_speakers else "",
        "incomplete_sentences": incomplete,
        "incomplete_rate": round(incomplete_rate, 3),
        "disfluency_count": disfluency_count,
        "ellipsis_gaps": ellipsis_count,
        "agenda_items": n_items,
        "post_processed": data.get("post_processed", False),
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark 2026 test meetings")
    parser.add_argument("--csv", type=str, help="Output CSV file path")
    args = parser.parse_args()

    # Get 2026 meeting IDs from staging
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute(
        "SELECT id::text FROM staging.meetings "
        "WHERE EXTRACT(YEAR FROM start_date) = 2026 ORDER BY start_date"
    )
    meeting_ids = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()

    results = []
    for mid in meeting_ids:
        row = analyze_meeting(mid)
        if row:
            results.append(row)

    if not results:
        print("No meetings found.")
        return

    # Print table
    fields = list(results[0].keys())
    header_map = {
        "meeting_id": "ID",
        "date": "Date",
        "committee": "Committee",
        "duration": "Dur",
        "raw_segments": "Raw",
        "final_segments": "Final",
        "segment_retention": "Retain%",
        "speaker_attr_rate": "Spkr%",
        "party_attr_rate": "Party%",
        "garbage_speakers": "Garbage",
        "incomplete_sentences": "Incompl",
        "incomplete_rate": "Incompl%",
        "disfluency_count": "Disfl",
        "ellipsis_gaps": "Ellips",
        "agenda_items": "Items",
        "post_processed": "PP",
    }

    # Compact table for terminal
    print(f"{'#':>2} {'Date':<11} {'Raw':>4} {'Final':>5} {'Ret%':>5} "
          f"{'Spkr%':>5} {'Pty%':>5} {'Incmp':>5} {'Disfl':>5} {'Ellip':>5} "
          f"{'Items':>5} {'PP':>3} Garbage")
    print("-" * 100)

    for i, r in enumerate(results, 1):
        print(f"{i:>2} {r['date']:<11} {r['raw_segments']:>4} {r['final_segments']:>5} "
              f"{r['segment_retention']:>5.0%} {r['speaker_attr_rate']:>5.0%} "
              f"{r['party_attr_rate']:>5.0%} {r['incomplete_sentences']:>5} "
              f"{r['disfluency_count']:>5} {r['ellipsis_gaps']:>5} "
              f"{r['agenda_items']:>5} {'Y' if r['post_processed'] else 'N':>3} "
              f"{r['garbage_speakers']}")

    # Summary
    print("-" * 100)
    avg_ret = sum(r["segment_retention"] for r in results) / len(results)
    avg_spk = sum(r["speaker_attr_rate"] for r in results) / len(results)
    avg_pty = sum(r["party_attr_rate"] for r in results) / len(results)
    total_inc = sum(r["incomplete_sentences"] for r in results)
    total_dis = sum(r["disfluency_count"] for r in results)
    total_ell = sum(r["ellipsis_gaps"] for r in results)
    n_garbage = sum(1 for r in results if r["garbage_speakers"])
    print(f"   {'AVG/TOTAL':<11} {'':>4} {'':>5} {avg_ret:>5.0%} {avg_spk:>5.0%} "
          f"{avg_pty:>5.0%} {total_inc:>5} {total_dis:>5} {total_ell:>5} "
          f"{'':>5} {'':>3} {n_garbage} meetings w/ garbage")

    # CSV output
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(results)
        print(f"\nSaved to {args.csv}")


if __name__ == "__main__":
    main()