#!/usr/bin/env python3
"""
Enrich speaker attribution for zero-attribution staging meetings.

Reads transcript cache, runs SpeakerInferenceEnricher, re-ingests chunks
into staging with speaker prefixes. Meetings that reach ≥50% attribution
are promoted from 'rejected' to 'pending' for re-audit.

Usage:
    python scripts/enrich_speaker_attribution.py           # dry-run
    python scripts/enrich_speaker_attribution.py --apply   # write to staging DB
    python scripts/enrich_speaker_attribution.py --apply --meeting-id <uuid>
"""

import os
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

import psycopg2
from psycopg2.extras import RealDictCursor

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/neodemos")


def get_rejected_meetings(db_url: str):
    conn = psycopg2.connect(db_url)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SET search_path TO staging, public")
    cur.execute("""
        SELECT id, name, committee, start_date, transcript_source
        FROM meetings WHERE review_status = 'rejected'
        ORDER BY start_date DESC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close(); conn.close()
    return rows


def re_ingest_transcript(transcript: dict, db_url: str):
    """Re-ingest chunks for this meeting with updated speaker attribution."""
    from pipeline.staging_ingestor import StagingIngestor
    ingestor = StagingIngestor(db_url=db_url, chunk_only=True)
    ingestor.ingest_transcript(transcript, category="committee_transcript")


def update_review_status(meeting_id: str, status: str, db_url: str):
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SET search_path TO staging, public")
    cur.execute("UPDATE meetings SET review_status = %s WHERE id = %s", (status, meeting_id))
    conn.commit()
    cur.close(); conn.close()


def main():
    parser = argparse.ArgumentParser(description="Enrich speaker attribution for rejected meetings")
    parser.add_argument("--apply", action="store_true", help="Write enriched transcripts to staging DB")
    parser.add_argument("--meeting-id", help="Process a single meeting only")
    parser.add_argument("--min-attribution", type=float, default=0.50,
                        help="Min attribution rate to un-reject (default: 0.50)")
    parser.add_argument("--min-date", type=str, default=None,
                        help="Only process meetings on or after this date (YYYY-MM-DD), e.g. 2022-01-01")
    args = parser.parse_args()

    from services.speaker_inference import SpeakerInferenceEnricher
    enricher = SpeakerInferenceEnricher()

    cache_dir = Path("output/transcripts/staging_cache")

    if args.meeting_id:
        meetings = [{"id": args.meeting_id, "name": args.meeting_id}]
    else:
        meetings = get_rejected_meetings(DB_URL)

    if args.min_date:
        meetings = [m for m in meetings if str(m.get("start_date", "") or "")[:10] >= args.min_date]

    print(f"Rejected meetings to process: {len(meetings)}")
    if not args.apply:
        print("DRY RUN — use --apply to write changes\n")

    recovered = 0
    still_poor = 0
    no_cache = 0

    for m in meetings:
        mid = str(m["id"])
        name = (m.get("name") or "")[:55]
        cache = cache_dir / f"{mid}.json"

        if not cache.exists():
            print(f"  [NO CACHE ] {name}")
            no_cache += 1
            continue

        with open(cache) as f:
            transcript = json.load(f)

        enricher.enrich(transcript)
        stats = enricher.last_stats
        rate = stats.get("attribution_rate_after", 0.0)
        cues = stats.get("cues_found", 0)

        status = "RECOVERED" if rate >= args.min_attribution else "POOR"
        print(f"  [{status:<9}] {rate:>5.1%} attr  {cues:>3} cues  {name}")

        if args.apply and rate >= args.min_attribution:
            # Save enriched transcript back to cache
            with open(cache, "w") as f:
                json.dump(transcript, f, ensure_ascii=False, default=str)

            # Re-ingest chunks with speaker prefixes
            try:
                re_ingest_transcript(transcript, DB_URL)
                update_review_status(mid, "pending", DB_URL)
                recovered += 1
            except Exception as e:
                print(f"    ERROR re-ingesting {mid[:8]}: {e}")
        elif rate >= args.min_attribution:
            recovered += 1  # would be recovered
        else:
            still_poor += 1

    print(f"\n{'Applied' if args.apply else 'Would recover'}: {recovered}/{len(meetings)}")
    print(f"Still poor attribution: {still_poor}")
    print(f"No transcript cache: {no_cache}")
    if args.apply and recovered:
        print(f"\nNext step: re-audit recovered meetings:")
        print(f"  python scripts/batch_audit_staging.py --status pending --llm-per-committee 0")


if __name__ == "__main__":
    main()
