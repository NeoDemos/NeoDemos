"""
Committee Meeting Virtual Notulen Pipeline
============================================

Generates virtual notulen (minutes) from committee meeting videos by:
  1. Discovering committee meetings without official notulen
  2. Running the video-to-text pipeline (VTT/Whisper)
  3. Post-processing with two-pass LLM correction
  4. Computing quality scores
  5. Ingesting chunks into isolated staging schema (PostgreSQL only)

All data stays in staging until explicitly promoted to production.
Embedding into Qdrant happens at promotion time, not during ingestion
(audit-first architecture).

Usage:
    python -m pipeline.committee_notulen_pipeline --year 2026 --limit 5
    python -m pipeline.committee_notulen_pipeline --year 2026 --committee "BWB"
    python -m pipeline.committee_notulen_pipeline --reprocess <meeting_id>
"""

import json
import os
import sys
import time
import gc
import logging
import argparse
import uuid
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

import psycopg2

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.main_pipeline import run_pipeline
from pipeline.staging_ingestor import StagingIngestor
from pipeline.transcript_postprocessor import TranscriptPostProcessor
from pipeline.exceptions import (
    MeetingCancelledError,
    MeetingUnavailableError,
    WebcastCodeExtractionError,
)

from dotenv import load_dotenv
load_dotenv()

# Create logs dir before setting up file handler (prevents crash on import)
Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/committee_notulen_pipeline.log"),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("committee_notulen_pipeline")

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/neodemos")
STATE_FILE = Path("data/pipeline_state/committee_notulen_state.json")
# Transcript cache: raw JSON saved after download so crashes during post-processing
# or ingestion don't require re-downloading the video.
TRANSCRIPT_CACHE_DIR = Path("output/transcripts/staging_cache")
COOLDOWN_SECONDS = 5


COMMITTEE_ABBREVS = {
    "bestuur, organisatie, financiën en veiligheid": "BOFV",
    "mobiliteit, haven, economie en klimaat": "MHEK",
    "bouwen, wonen en buitenruimte": "BWB",
    "zorg, welzijn, cultuur en sport": "ZOCS",
    "werk & inkomen, onderwijs, samenleven": "WIOS",
    "werk en inkomen, onderwijs, samenleven": "WIOS",
    "onderzoek van de rekening": "COR",
}


def derive_committee(meeting_name: str) -> str:
    """Derive a committee abbreviation from the meeting name.

    Falls back to the full committee name if no abbreviation matches.
    """
    name_lower = (meeting_name or "").lower()
    for pattern, abbrev in COMMITTEE_ABBREVS.items():
        if pattern in name_lower:
            return abbrev
    # Subcommissies: keep as-is (e.g., "Subcommissie BOOR")
    if "subcommissie" in name_lower:
        for word in meeting_name.split():
            if word.isupper() and len(word) >= 3:
                return f"Sub-{word}"
    return ""


class CommitteeNotulenPipeline:
    """Orchestrates the committee meeting virtual notulen pipeline."""

    def __init__(self, state_file: str = None, reset_state: bool = False):
        self.state_file = Path(state_file) if state_file else STATE_FILE
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        TRANSCRIPT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state(reset_state)
        self.ingestor = StagingIngestor(chunk_only=True)
        self.postprocessor = TranscriptPostProcessor()
        self.pipeline_run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    # ── State Management ─────────────────────────────────────────────────

    def _load_state(self, reset: bool) -> Dict:
        if self.state_file.exists() and not reset:
            with open(self.state_file, "r") as f:
                state = json.load(f)
            # Reset crashed meetings
            for m_id, info in state.get("meetings", {}).items():
                if info.get("status") == "in_progress":
                    logger.info(f"Resetting {m_id} from in_progress to failed (crash recovery)")
                    info["status"] = "failed"
                    info["last_error"] = "Interrupted by crash (recovered)"
            return state
        return {"meetings": {}}

    def _save_state(self):
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2, default=str)

    # ── Discovery ────────────────────────────────────────────────────────

    def discover_meetings(self, year: int = 2026, committee_filter: str = None,
                          limit: int = None) -> List[Dict]:
        """Find committee meetings without official notulen.

        Queries production DB for committee meetings and filters out those
        that already have notulen/verslagen or are already in staging.
        """
        logger.info(f"Discovering {year} committee meetings" +
                     (f" (filter: {committee_filter})" if committee_filter else "") + "...")

        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()

        try:
            # Find committee meetings without official notulen.
            # Use ibabs_url (synced from ORI) instead of constructing from id —
            # iBabs UUIDs rotate, so the source-of-truth column must be queried.
            query = """
                SELECT m.id, m.name, m.committee, m.start_date, m.ibabs_url
                FROM public.meetings m
                WHERE EXTRACT(YEAR FROM m.start_date) = %s
                  AND (m.committee ILIKE '%%Commissie%%'
                       OR m.name ILIKE '%%Commissie%%')
                  AND m.ibabs_url IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM public.documents d
                      WHERE d.meeting_id = m.id
                        AND (d.name ILIKE '%%notulen%%' OR d.name ILIKE '%%verslag%%')
                        AND d.content IS NOT NULL
                        AND length(d.content) > 500
                  )
                ORDER BY m.start_date DESC
            """
            cur.execute(query, (year,))
            rows = cur.fetchall()

            # Check what's already in staging
            try:
                cur.execute("SET search_path TO staging, public")
                cur.execute("""
                    SELECT meeting_id FROM pipeline_meeting_log
                    WHERE status IN ('completed', 'in_progress')
                """)
                staging_ids = {r[0] for r in cur.fetchall()}
                cur.execute("SET search_path TO public")
            except psycopg2.errors.UndefinedTable:
                conn.rollback()
                staging_ids = set()
                logger.warning("Staging schema not found. Run scripts/create_staging_schema.py first.")

        finally:
            cur.close()
            conn.close()

        # Filter and build meeting list
        meetings = []
        for row in rows:
            m_id = str(row[0])

            # Skip already processed in staging
            if m_id in staging_ids:
                continue

            # Skip already in local state as completed
            state_info = self.state.get("meetings", {}).get(m_id, {})
            if state_info.get("status") == "completed":
                continue

            # Apply committee filter if specified
            name = row[1] or ""
            committee = row[2] or ""
            if committee_filter:
                if committee_filter.lower() not in name.lower() and \
                   committee_filter.lower() not in committee.lower():
                    continue

            # Use the aligned ibabs_url from the DB (set by sync_ibabs_urls.py)
            # This is the current iBabs UUID, not our internal meeting ID.
            url = row[4]
            if not url:
                continue  # ibabs_url not yet aligned — skip
            meeting = {
                "id": m_id,
                "name": name,
                "committee": committee,
                "start_date": str(row[3]),
                "url": url,
            }
            meetings.append(meeting)

            # Add to state if not present
            if m_id not in self.state.get("meetings", {}):
                self.state.setdefault("meetings", {})[m_id] = {
                    "name": name,
                    "committee": committee,
                    "date": str(row[3]),
                    "url": url,
                    "status": "pending",
                    "attempts": 0,
                    "last_error": None,
                }

        self._save_state()

        if limit:
            meetings = meetings[:limit]

        logger.info(f"Discovery: {len(meetings)} meetings to process "
                     f"(filtered from {len(rows)} total committee meetings)")
        return meetings

    # ── Quality Scoring ──────────────────────────────────────────────────

    def compute_quality_score(self, transcript: Dict) -> Dict[str, Any]:
        """Compute automated quality metrics for a transcript."""
        all_segments = []
        for item in transcript.get("agenda_items", []):
            all_segments.extend(item.get("segments", []))

        total = len(all_segments)
        if total == 0:
            return {"score": 0.0, "status": "auto_rejected", "metrics": {}}

        # Metric: segment count (expect 50+ for a 2h meeting)
        seg_score = min(total / 50, 1.0)

        # Metric: speaker attribution rate
        with_speaker = sum(1 for s in all_segments
                          if s.get("speaker") and s["speaker"] not in ("Unknown", "Spreker onbekend", ""))
        speaker_score = with_speaker / total if total else 0

        # Metric: text density (avg chars per segment)
        avg_chars = sum(len(s.get("text", "")) for s in all_segments) / total if total else 0
        density_score = min(avg_chars / 200, 1.0)

        # Metric: average confidence
        confidences = [s.get("confidence", 1.0) for s in all_segments if s.get("text")]
        confidence_score = sum(confidences) / len(confidences) if confidences else 0

        # Metric: agenda coverage
        n_items = len(transcript.get("agenda_items", []))
        agenda_score = min(n_items / 3, 1.0)

        # Metric: completeness (detect VTT gaps and incomplete sentences)
        import re
        gap_patterns = re.compile(r'(\*\s*){3,}|(\.\s*){3,}|([A-Z]{2,}\s){3,}|\b(\w+)(\s+\4){2,}\b')
        incomplete_re = re.compile(r'^.{20,}[^.!?:;)"\s]$')
        gaps = sum(1 for s in all_segments if gap_patterns.search(s.get("text", "")))
        incomplete = sum(1 for s in all_segments if incomplete_re.match(s.get("text", "").strip()))
        completeness_score = 1.0 - min((gaps + incomplete) / total, 1.0) if total else 0

        # Weighted composite
        score = (
            0.10 * seg_score +
            0.30 * speaker_score +
            0.20 * density_score +
            0.15 * confidence_score +
            0.15 * agenda_score +
            0.10 * completeness_score
        )

        # Determine review status
        source = transcript.get("transcript_source", "unknown")
        if score >= 0.7 and source == "vtt":
            status = "auto_approved"
        elif score < 0.4:
            status = "auto_rejected"
        else:
            status = "pending"  # Whisper-only or mid-range always needs review

        metrics = {
            "score": round(score, 3),
            "segment_count": total,
            "seg_score": round(seg_score, 3),
            "speaker_score": round(speaker_score, 3),
            "density_score": round(density_score, 3),
            "confidence_score": round(confidence_score, 3),
            "agenda_score": round(agenda_score, 3),
            "completeness_score": round(completeness_score, 3),
            "gap_segments": gaps,
            "incomplete_segments": incomplete,
            "avg_chars_per_segment": round(avg_chars, 1),
            "transcript_source": source,
        }

        return {"score": round(score, 3), "status": status, "metrics": metrics}

    # ── Per-Meeting Processing ───────────────────────────────────────────

    def _process_meeting(self, meeting: Dict) -> bool:
        """Process a single committee meeting. Returns True on success."""
        m_id = meeting["id"]
        info = self.state["meetings"].get(m_id, {})
        info["status"] = "in_progress"
        info["attempts"] = info.get("attempts", 0) + 1
        self._save_state()

        # ── Log pipeline run ─────────────────────────────────────────
        self._log_meeting_start(m_id)

        try:
            # ── Step 1: Acquire transcript ────────────────────────────
            logger.info(f"  Step 1: Acquiring transcript from {meeting['url']}...")
            transcript = self._acquire_transcript(meeting)

            if not transcript or not transcript.get("agenda_items"):
                raise ValueError("Empty transcript — no agenda items produced")

            source = transcript.get("transcript_source", "unknown")
            logger.info(f"  Transcript acquired: {transcript.get('total_segments', 0)} segments "
                         f"(source: {source})")

            # ── Step 1.5: Agenda detection for single-item transcripts ─
            n_items = len(transcript.get("agenda_items", []))
            if n_items <= 1:
                from pipeline.agenda_detector import detect_and_split_agenda
                logger.info("  Step 1.5: Detecting agenda boundaries for single-item transcript...")
                transcript = detect_and_split_agenda(transcript)

            # ── Step 2: Post-process with LLM ────────────────────────
            logger.info("  Step 2: LLM post-processing (two-pass)...")
            transcript = self.postprocessor.process(transcript)

            # ── Step 2.5: Speaker enrichment + party fill ────────────
            from services.speaker_inference import SpeakerInferenceEnricher
            enricher = SpeakerInferenceEnricher()
            enricher.enrich(transcript)
            enricher.resolve_insprekers(transcript)
            enricher.fill_party_from_dictionary(transcript)
            enricher.filter_garbage_speakers(transcript)

            # ── Step 3: Quality scoring ──────────────────────────────
            logger.info("  Step 3: Computing quality score...")
            quality = self.compute_quality_score(transcript)
            logger.info(f"  Quality: {quality['score']:.3f} ({quality['status']})")

            # ── Step 4: Ingest into staging ──────────────────────────
            logger.info("  Step 4: Ingesting into staging...")
            committee = meeting.get("committee") or derive_committee(meeting["name"])
            self.ingestor.ensure_staging_meeting(
                meeting_id=m_id,
                name=meeting["name"],
                start_date=meeting.get("start_date"),
                committee=committee,
                transcript_source=source,
            )
            self.ingestor.ingest_transcript(transcript, heuristic=True, category="committee_transcript")
            self.ingestor.update_quality_score(m_id, quality["score"], quality["status"])

            # Clear transcript cache now that ingestion succeeded
            self._clear_transcript_cache(m_id)

            # ── Step 5: Update state ─────────────────────────────────
            info["status"] = "completed"
            info["last_error"] = None
            info["quality_score"] = quality["score"]
            info["review_status"] = quality["status"]
            info["transcript_source"] = source
            info["completed_at"] = datetime.now().isoformat()
            self._save_state()
            self._log_meeting_complete(m_id, source, quality)

            logger.info(f"  Done: {meeting['name']} (score: {quality['score']:.3f})")
            return True

        except (MeetingCancelledError, MeetingUnavailableError) as skip_err:
            info["status"] = "skipped"
            info["last_error"] = str(skip_err)
            self._save_state()
            self._log_meeting_error(m_id, str(skip_err), "skipped")
            logger.info(f"  Skipped: {skip_err}")
            return False

        except WebcastCodeExtractionError as wce:
            info["status"] = "skipped"
            info["last_error"] = str(wce)
            self._save_state()
            self._log_meeting_error(m_id, str(wce), "skipped")
            logger.warning(f"  No webcast code: {wce}")
            return False

        except Exception as e:
            info["status"] = "failed"
            info["last_error"] = str(e)
            self._save_state()
            self._log_meeting_error(m_id, str(e), "failed")
            logger.error(f"  Failed: {e}")
            return False

    def _transcript_cache_path(self, meeting_id: str) -> Path:
        return TRANSCRIPT_CACHE_DIR / f"{meeting_id}.json"

    def _load_transcript_cache(self, meeting_id: str) -> Optional[Dict]:
        """Load a previously downloaded transcript from disk cache."""
        cache_path = self._transcript_cache_path(meeting_id)
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    transcript = json.load(f)
                logger.info(f"  Loaded transcript from cache ({cache_path.name}) — skipping re-download")
                return transcript
            except Exception as e:
                logger.warning(f"  Cache file corrupt, will re-download: {e}")
                cache_path.unlink(missing_ok=True)
        return None

    def _save_transcript_cache(self, meeting_id: str, transcript: Dict):
        """Save a transcript to disk so crashes during post-processing don't require re-download."""
        cache_path = self._transcript_cache_path(meeting_id)
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(transcript, f, ensure_ascii=False, indent=2)
            logger.info(f"  Transcript cached to {cache_path.name}")
        except Exception as e:
            logger.warning(f"  Could not cache transcript: {e}")

    def _clear_transcript_cache(self, meeting_id: str):
        """Remove cache file after successful ingestion."""
        self._transcript_cache_path(meeting_id).unlink(missing_ok=True)

    def _acquire_transcript(self, meeting: Dict) -> Dict:
        """Run the video-to-text pipeline and get transcript JSON.

        Checks disk cache first — if the transcript was already downloaded in a
        previous (crashed) attempt, we skip the video download entirely.

        Uses a 3-tier fallback strategy:
          1. Heuristic (VTT + iBabs speakers, no API)
          2. Whisper fallback
          3. VTT-only (minimum)
        """
        m_id = meeting["id"]
        url = meeting["url"]

        # Check cache first — prevents re-downloading after a crash mid-processing
        cached = self._load_transcript_cache(m_id)
        if cached:
            return cached

        # Tier 1: Heuristic (VTT + iBabs)
        try:
            transcript = run_pipeline(
                ibabs_url=url,
                heuristic=True,
                no_ingest=True,  # Never ingest into production directly!
                numeric_id=m_id if m_id.isdigit() else None,
            )
            self._save_transcript_cache(m_id, transcript)
            return transcript
        except (MeetingCancelledError, MeetingUnavailableError, WebcastCodeExtractionError):
            raise  # Let caller handle skip-worthy errors
        except Exception as e:
            logger.warning(f"  Tier 1 (heuristic) failed: {e}. Trying Whisper...")

        # Tier 2: Whisper fallback
        try:
            transcript = run_pipeline(
                ibabs_url=url,
                heuristic=True,
                use_whisper=True,
                no_ingest=True,
                numeric_id=m_id if m_id.isdigit() else None,
            )
            self._save_transcript_cache(m_id, transcript)
            return transcript
        except Exception as e:
            logger.warning(f"  Tier 2 (Whisper) failed: {e}. Trying VTT-only...")

        # Tier 3: VTT-only (minimum)
        transcript = run_pipeline(
            ibabs_url=url,
            heuristic=True,
            vtt_only=True,
            no_ingest=True,
            numeric_id=m_id if m_id.isdigit() else None,
        )
        self._save_transcript_cache(m_id, transcript)
        return transcript

    # ── Pipeline Run Logging (staging DB) ────────────────────────────────

    def _init_pipeline_run(self, total_meetings: int):
        """Register this pipeline run in the staging database."""
        try:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            cur.execute("SET search_path TO staging, public")
            cur.execute("""
                INSERT INTO pipeline_runs (id, meetings_total, config)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET meetings_total = EXCLUDED.meetings_total
            """, (self.pipeline_run_id, total_meetings, json.dumps({"started": datetime.now().isoformat()})))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.warning(f"Could not log pipeline run: {e}")

    def _log_meeting_start(self, meeting_id: str):
        try:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            cur.execute("SET search_path TO staging, public")
            cur.execute("""
                INSERT INTO pipeline_meeting_log (pipeline_run_id, meeting_id, status, phase, started_at)
                VALUES (%s, %s, 'in_progress', 'acquisition', NOW())
                ON CONFLICT (pipeline_run_id, meeting_id) DO UPDATE SET
                    status = 'in_progress', phase = 'acquisition', started_at = NOW()
            """, (self.pipeline_run_id, meeting_id))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.debug(f"Could not log meeting start: {e}")

    def _log_meeting_complete(self, meeting_id: str, source: str, quality: Dict):
        try:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            cur.execute("SET search_path TO staging, public")
            cur.execute("""
                UPDATE pipeline_meeting_log SET
                    status = 'completed', phase = 'done',
                    transcript_source = %s,
                    quality_metrics = %s,
                    completed_at = NOW()
                WHERE pipeline_run_id = %s AND meeting_id = %s
            """, (source, json.dumps(quality), self.pipeline_run_id, meeting_id))
            # Update pipeline run counters
            cur.execute("""
                UPDATE pipeline_runs SET meetings_completed = meetings_completed + 1
                WHERE id = %s
            """, (self.pipeline_run_id,))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.debug(f"Could not log meeting complete: {e}")

    def _log_meeting_error(self, meeting_id: str, error: str, status: str):
        try:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            cur.execute("SET search_path TO staging, public")
            cur.execute("""
                UPDATE pipeline_meeting_log SET
                    status = %s, error_message = %s, completed_at = NOW()
                WHERE pipeline_run_id = %s AND meeting_id = %s
            """, (status, error[:500], self.pipeline_run_id, meeting_id))
            if status == "failed":
                cur.execute("""
                    UPDATE pipeline_runs SET meetings_failed = meetings_failed + 1
                    WHERE id = %s
                """, (self.pipeline_run_id,))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.debug(f"Could not log meeting error: {e}")

    def _finalize_pipeline_run(self):
        try:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            cur.execute("SET search_path TO staging, public")
            cur.execute("""
                UPDATE pipeline_runs SET status = 'completed', completed_at = NOW()
                WHERE id = %s
            """, (self.pipeline_run_id,))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.debug(f"Could not finalize pipeline run: {e}")

    # ── Main Run ─────────────────────────────────────────────────────────

    def run(self, meetings: List[Dict], limit: int = None):
        """Process a list of meetings sequentially."""
        if limit:
            meetings = meetings[:limit]

        logger.info(f"\nStarting pipeline run: {self.pipeline_run_id}")
        logger.info(f"Meetings to process: {len(meetings)}")
        self._init_pipeline_run(len(meetings))

        completed = 0
        failed = 0
        skipped = 0

        for i, meeting in enumerate(meetings, 1):
            logger.info(f"\n{'='*60}")
            logger.info(f"[{i}/{len(meetings)}] {meeting['name']} ({meeting.get('start_date', 'N/A')})")
            logger.info(f"{'='*60}")

            success = self._process_meeting(meeting)

            if success:
                completed += 1
            else:
                status = self.state["meetings"].get(meeting["id"], {}).get("status", "")
                if status == "skipped":
                    skipped += 1
                else:
                    failed += 1

            # Cooldown + memory cleanup
            gc.collect()
            try:
                import mlx.core as mx
                mx.metal.clear_cache()
            except Exception:
                pass
            time.sleep(COOLDOWN_SECONDS)

        self._finalize_pipeline_run()

        logger.info(f"\n{'='*60}")
        logger.info(f"Pipeline Run Complete: {self.pipeline_run_id}")
        logger.info(f"  Completed: {completed}")
        logger.info(f"  Failed:    {failed}")
        logger.info(f"  Skipped:   {skipped}")
        logger.info(f"{'='*60}")

    def reprocess_meeting(self, meeting_id: str, use_whisper: bool = False):
        """Reprocess a single meeting (e.g., after rejection)."""
        info = self.state.get("meetings", {}).get(meeting_id)
        if not info:
            logger.error(f"Meeting {meeting_id} not found in state")
            return

        # Reset state and clear cache to force re-download
        info["status"] = "pending"
        info["attempts"] = 0
        info["last_error"] = None
        self._clear_transcript_cache(meeting_id)
        self._save_state()

        meeting = {
            "id": meeting_id,
            "name": info["name"],
            "committee": info.get("committee", ""),
            "start_date": info.get("date", ""),
            "url": info["url"],
        }

        self._init_pipeline_run(1)
        self._process_meeting(meeting)
        self._finalize_pipeline_run()


# ── CLI Entry Point ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Committee Meeting Virtual Notulen Pipeline"
    )
    parser.add_argument("--year", type=int, default=2026, help="Year to process")
    parser.add_argument("--limit", type=int, help="Limit number of meetings")
    parser.add_argument("--committee", type=str, help="Filter by committee name (e.g., 'BWB')")
    parser.add_argument("--reset", action="store_true", help="Reset pipeline state")
    parser.add_argument("--reprocess", type=str, help="Reprocess a specific meeting ID")
    parser.add_argument("--state-file", type=str, help="Custom state file path")
    parser.add_argument("--discover-only", action="store_true", help="Only discover meetings, don't process")

    args = parser.parse_args()

    # Ensure logs directory exists
    Path("logs").mkdir(exist_ok=True)

    pipeline = CommitteeNotulenPipeline(
        state_file=args.state_file,
        reset_state=args.reset,
    )

    if args.reprocess:
        logger.info(f"Reprocessing meeting: {args.reprocess}")
        pipeline.reprocess_meeting(args.reprocess)
        return

    meetings = pipeline.discover_meetings(
        year=args.year,
        committee_filter=args.committee,
        limit=args.limit,
    )

    if args.discover_only:
        print(f"\nDiscovered {len(meetings)} meetings:")
        for m in meetings:
            print(f"  {m['start_date'][:10]}  {m['name']}")
        return

    if not meetings:
        logger.info("No meetings to process.")
        return

    pipeline.run(meetings, limit=args.limit)


if __name__ == "__main__":
    main()
