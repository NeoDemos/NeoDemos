#!/usr/bin/env python3
"""Batch-ingest all available transcripts into staging (chunk_only, no embedding)."""

import json
import glob
import os
import sys
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import psycopg2

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/neodemos")


def main():
    from pipeline.staging_ingestor import StagingIngestor
    from pipeline.transcript_postprocessor import TranscriptPostProcessor
    from pipeline.committee_notulen_pipeline import derive_committee, CommitteeNotulenPipeline

    # Already-staged
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("SELECT id FROM staging.meetings")
    staged = {str(r[0]) for r in cur.fetchall()}
    cur.close()
    conn.close()

    pipeline = CommitteeNotulenPipeline.__new__(CommitteeNotulenPipeline)
    ingestor = StagingIngestor(db_url=DB_URL, chunk_only=True)
    postprocessor = TranscriptPostProcessor()

    # Find candidates
    candidates = []
    for f in sorted(glob.glob("output/transcripts/gemeenterotterdam_*.json")):
        try:
            with open(f) as fh:
                t = json.load(fh)
            mid = t.get("meeting_id")
            if not mid or mid in staged:
                continue
            items = t.get("agenda_items", [])
            total_segs = sum(len(i.get("segments", [])) for i in items)
            if total_segs > 50:
                candidates.append((f, mid, t))
        except Exception:
            pass

    print(f"Ingesting {len(candidates)} meetings...")
    ok = 0
    fail = 0

    for i, (fpath, mid, transcript) in enumerate(candidates, 1):
        name = transcript.get("meeting_name", "")
        date = transcript.get("date", "")
        source = transcript.get("transcript_source", "vtt")
        committee = derive_committee(name)

        try:
            cache = f"output/transcripts/staging_cache/{mid}.json"
            if not os.path.exists(cache):
                shutil.copy(fpath, cache)

            # Post-process before ingestion (pre-clean + 2-pass LLM)
            transcript = postprocessor.process(transcript)

            ingestor.ensure_staging_meeting(
                meeting_id=mid, name=name, start_date=date,
                committee=committee, transcript_source=source,
            )

            quality = CommitteeNotulenPipeline.compute_quality_score(pipeline, transcript)
            ingestor.ingest_transcript(transcript, category="committee_transcript")
            ingestor.update_quality_score(mid, quality["score"], quality["status"])
            ok += 1

            if i % 20 == 0:
                print(f"  [{i}/{len(candidates)}] {ok} ok, {fail} failed")
        except Exception as e:
            fail += 1
            print(f"  FAIL [{i}] {name[:40]}: {e}")

    print(f"\nDone: {ok} ingested, {fail} failed")


if __name__ == "__main__":
    main()
