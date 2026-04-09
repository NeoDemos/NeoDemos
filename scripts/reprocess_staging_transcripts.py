#!/usr/bin/env python3
"""
Batch-reprocess staging transcripts through VTT pre-cleaning + 2-pass LLM post-processing.

Loads raw transcripts from staging_cache, runs the full post-processing pipeline,
and re-ingests into staging (upsert — safe to run multiple times).

Usage:
    # Dry-run: show what would be reprocessed
    python scripts/reprocess_staging_transcripts.py --dry-run

    # Reprocess all 2026 meetings
    python scripts/reprocess_staging_transcripts.py --year 2026

    # Reprocess specific meeting
    python scripts/reprocess_staging_transcripts.py --meeting-id <uuid>

    # Reprocess all un-processed meetings (post_processed=False in cache)
    python scripts/reprocess_staging_transcripts.py --unprocessed
"""

import argparse
import json
import os
import sys
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import psycopg2

def _build_db_url():
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    user = os.getenv("DB_USER", "postgres")
    pw = os.getenv("DB_PASSWORD", "postgres")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "neodemos")
    return f"postgresql://{user}:{pw}@{host}:{port}/{name}"

DB_URL = _build_db_url()
CACHE_DIR = Path("output/transcripts/staging_cache")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def get_staging_meetings(year=None, meeting_id=None, unprocessed=False):
    """Fetch staging meetings, optionally filtered."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    if meeting_id:
        cur.execute(
            "SELECT id, name, start_date, committee, review_status, quality_score "
            "FROM staging.meetings WHERE id::text = %s",
            (meeting_id,)
        )
    elif year:
        cur.execute(
            "SELECT id, name, start_date, committee, review_status, quality_score "
            "FROM staging.meetings WHERE EXTRACT(YEAR FROM start_date) = %s "
            "ORDER BY start_date",
            (year,)
        )
    else:
        cur.execute(
            "SELECT id, name, start_date, committee, review_status, quality_score "
            "FROM staging.meetings ORDER BY start_date"
        )

    meetings = []
    for row in cur.fetchall():
        mid = str(row[0])
        cache_path = CACHE_DIR / f"{mid}.json"
        if not cache_path.exists():
            logger.warning(f"  No cache file for {mid} ({row[1][:40]}), skipping")
            continue

        # Check if already post-processed
        with open(cache_path) as f:
            data = json.load(f)

        if unprocessed and data.get("post_processed"):
            continue

        meetings.append({
            "id": mid,
            "name": row[1],
            "start_date": row[2],
            "committee": row[3],
            "review_status": row[4],
            "quality_score": row[5],
            "cache_path": str(cache_path),
            "already_processed": data.get("post_processed", False),
        })

    cur.close()
    conn.close()
    return meetings


def reprocess_meeting(mid, cache_path, dry_run=False):
    """Reprocess a single meeting transcript."""
    from pipeline.transcript_postprocessor import TranscriptPostProcessor
    from pipeline.staging_ingestor import StagingIngestor
    from pipeline.committee_notulen_pipeline import CommitteeNotulenPipeline

    # Load raw transcript from cache
    with open(cache_path) as f:
        transcript = json.load(f)

    name = transcript.get("meeting_name", "?")
    date = transcript.get("date", "?")
    segs = transcript.get("total_segments", 0)
    logger.info(f"  Loading: {name[:50]} ({date}), {segs} segments")

    if dry_run:
        return True

    # Run post-processing (pre-clean + 2-pass LLM)
    processor = TranscriptPostProcessor()
    processed = processor.process(transcript)

    # Speaker enrichment + party fill + garbage filter
    from services.speaker_inference import SpeakerInferenceEnricher
    enricher = SpeakerInferenceEnricher()
    enricher.enrich(processed)
    enricher.resolve_insprekers(processed)
    enricher.fill_party_from_dictionary(processed)
    enricher.filter_garbage_speakers(processed)

    # Save post-processed cache alongside raw
    processed_path = cache_path.replace(".json", "_processed.json")
    with open(processed_path, "w", encoding="utf-8") as f:
        json.dump(processed, f, ensure_ascii=False, indent=2)
    logger.info(f"  Saved processed cache: {processed_path}")

    # Re-score quality
    pipeline = CommitteeNotulenPipeline.__new__(CommitteeNotulenPipeline)
    quality = CommitteeNotulenPipeline.compute_quality_score(pipeline, processed)
    logger.info(f"  New quality: {quality['score']:.3f} ({quality['status']})")

    # Re-ingest into staging (upsert)
    ingestor = StagingIngestor(db_url=DB_URL, chunk_only=True)
    committee = transcript.get("committee") or processed.get("committee", "")
    source = transcript.get("transcript_source", "vtt")

    ingestor.ensure_staging_meeting(
        meeting_id=mid,
        name=transcript.get("meeting_name", ""),
        start_date=transcript.get("date"),
        committee=committee,
        transcript_source=source,
    )
    ingestor.ingest_transcript(processed, category="committee_transcript")
    ingestor.update_quality_score(mid, quality["score"], quality["status"])

    logger.info(f"  Re-ingested with quality {quality['score']:.3f}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Reprocess staging transcripts with LLM post-processing")
    parser.add_argument("--year", type=int, help="Filter by year")
    parser.add_argument("--meeting-id", type=str, help="Reprocess a specific meeting")
    parser.add_argument("--unprocessed", action="store_true", help="Only reprocess meetings not yet post-processed")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be reprocessed without doing it")
    args = parser.parse_args()

    meetings = get_staging_meetings(
        year=args.year,
        meeting_id=args.meeting_id,
        unprocessed=args.unprocessed,
    )

    if not meetings:
        logger.info("No meetings to reprocess.")
        return

    # Summary
    total_chars = 0
    for m in meetings:
        with open(m["cache_path"]) as f:
            data = json.load(f)
        chars = sum(
            len(seg.get("text", ""))
            for item in data.get("agenda_items", [])
            for seg in item.get("segments", [])
        )
        total_chars += chars

    est_tokens = total_chars // 4
    est_cost = (est_tokens * 2 * 0.075 + est_tokens * 1.8 * 0.30) / 1_000_000

    logger.info(f"{'DRY RUN — ' if args.dry_run else ''}Reprocessing {len(meetings)} meetings")
    logger.info(f"  Total content: {total_chars:,} chars ≈ {est_tokens:,} tokens")
    logger.info(f"  Estimated cost: ${est_cost:.2f} (gemini-2.5-flash-lite)")
    logger.info("")

    ok = 0
    fail = 0
    for i, m in enumerate(meetings, 1):
        pp_flag = " [already processed]" if m["already_processed"] else ""
        logger.info(f"[{i}/{len(meetings)}] {m['id'][:8]} — {m['name'][:50]} "
                     f"(score={m['quality_score']}){pp_flag}")
        try:
            reprocess_meeting(m["id"], m["cache_path"], dry_run=args.dry_run)
            ok += 1
        except Exception as e:
            fail += 1
            logger.error(f"  FAILED: {e}")

    logger.info(f"\nDone: {ok} ok, {fail} failed")


if __name__ == "__main__":
    main()
