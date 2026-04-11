"""
Pipeline Orchestrator — Committee Meeting Transcription
========================================================
CLI entry point that orchestrates all phases:
  1. Scrape metadata + VTT from Royalcast
  2. Download video + extract OCR frames
  3. Run OCR for speaker names + Whisper (fallback)
  4. Align & output final JSON

Usage:
    python -m pipeline.main_pipeline gemeenterotterdam_20260107_1
    python -m pipeline.main_pipeline gemeenterotterdam_20260107_1 --vtt-only
    python -m pipeline.main_pipeline gemeenterotterdam_20260107_1 --output /path/to/output.json
"""

import json
import sys
import os
import logging
import argparse
import hashlib
import gc
from pathlib import Path
from datetime import datetime
from typing import Optional

from pipeline.scraper import RoyalcastScraper, MeetingMetadata, VTTSegment
from services.open_raad import OpenRaadService
from pipeline.media_processor import MediaProcessor
from pipeline.extractor import (
    SpeakerDetector,
    WhisperTranscriber,
    TranscriptAligner,
    TranscriptSegment,
    _format_time,
)
from pipeline.exceptions import (
    MeetingCancelledError,
    MeetingUnavailableError,
    WebcastCodeExtractionError,
)
from pipeline.normalization import EntityNormalizer
from services.scraper import ScraperService
import asyncio

logger = logging.getLogger(__name__)


def run_async(coro):
    """Helper to run async code from a sync or async context."""
    try:
        loop = asyncio.get_running_loop()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)

def run_pipeline(
    webcast_code: str = None,
    output_path: Optional[str] = None,
    vtt_only: bool = False,
    use_whisper: bool = False,
    temp_dir: Optional[str] = None,
    ibabs_url: Optional[str] = None,
    no_normalize: bool = False,
    no_ingest: bool = False,
    heuristic: bool = False,
    split_video: bool = False,
    numeric_id: Optional[str] = None,
    download_only: bool = False,
    category: str = "committee_transcript",
    whisper_model: Optional[str] = None,
) -> dict:
    """
    Run the full transcription pipeline for a single committee meeting.

    Args:
        webcast_code: Royalcast webcast code (e.g., "gemeenterotterdam_20260107_1")
        output_path: Path for the output JSON file (default: ./output/{code}.json)
        vtt_only: If True, skip video download and OCR (VTT transcript only)
        use_whisper: If True, use mlx-whisper even when VTT is available
        temp_dir: Temporary directory for media files

    Returns:
        The final meeting transcript as a dict
    """
    logger.info(f"{'='*60}")
    logger.info(f"Pipeline Start: {webcast_code or ibabs_url}")
    logger.info(f"Mode: {'VTT-only' if vtt_only else 'Full (VTT + OCR)'}")
    logger.info(f"{'='*60}")

    scraper = RoyalcastScraper()
    normalizer = EntityNormalizer() if not no_normalize else None
    processor = None

    try:
        # ── Phase 1: Metadata & VTT ──────────────────────────────────
        logger.info("\n📡 Phase 1: Fetching metadata & transcript...")

        # If it's a URL or missing, extract the code
        # NOTE: MeetingCancelledError / MeetingUnavailableError are intentionally
        # NOT caught here — they must propagate so the orchestrator can mark the
        # meeting as 'skipped' rather than 'failed'.
        if ibabs_url and not webcast_code:
            logger.info(f"Extracting webcast code from iBabs: {ibabs_url}")
            try:
                webcast_code = scraper.extract_webcast_code_from_ibabs(ibabs_url)
            except (MeetingCancelledError, MeetingUnavailableError):
                raise  # propagate — orchestrator handles these
            except WebcastCodeExtractionError:
                raise  # propagate so it's recorded correctly
            except Exception as e:
                logger.warning(f"⚠️  Unexpected error extracting webcast code: {e}")
                webcast_code = None

        if webcast_code and webcast_code.startswith("http"):
            ibabs_url = webcast_code
            try:
                webcast_code = scraper.extract_webcast_code_from_ibabs(webcast_code)
            except (MeetingCancelledError, MeetingUnavailableError):
                raise
            except Exception as e:
                logger.warning(f"⚠️  Could not extract webcast code from URL: {e}")
                webcast_code = None

        if not webcast_code and not ibabs_url:
            raise ValueError("webcast_code or ibabs_url must be provided")

        if webcast_code:
            logger.info(f"Using webcast code: {webcast_code}")
            metadata = scraper.fetch_meeting_metadata(webcast_code)
        else:
            logger.warning("⚠️  Proceeding without webcast code (will rely on PDF/Scraping fallback)")
            
            # Extract GUID from ibabs_url if possible (e.g. .../Index/guid)
            extracted_uuid = "unknown"
            if ibabs_url:
                parts = ibabs_url.rstrip("/").split("/")
                if len(parts[-1]) > 30 and "-" in parts[-1]:
                    extracted_uuid = parts[-1]
                else:
                    extracted_uuid = hashlib.md5(ibabs_url.encode()).hexdigest()

            metadata = MeetingMetadata(
                webcast_code="unknown",
                uuid=extracted_uuid,
                label="Unresolved Meeting",
                start_time="",
                duration="00:00:00",
                duration_seconds=0.0,
                vtt_available=False
            )

            # Check for ConnectLive VTT directly
            if extracted_uuid != "unknown" and "-" in extracted_uuid and len(extracted_uuid) > 30:
                logger.info(f"Checking for hidden ConnectLive VTT: {extracted_uuid}")
                vtt_url = f"https://connectlive.ibabs.eu/Player/File/{extracted_uuid}/-subtitle.vtt"
                try:
                    vtt_resp = scraper.session.head(vtt_url, timeout=10)
                    if vtt_resp.status_code == 200:
                        metadata.vtt_available = True
                        logger.info("✅ ConnectLive VTT found and enabled!")
                except Exception as e:
                    logger.debug("VTT availability check failed: %s", e)

        # Fetch VTT transcript
        vtt_segments = []
        if metadata.vtt_available:
            vtt_segments = scraper.fetch_vtt(metadata.uuid)
            metadata.vtt_segments = vtt_segments
            logger.info(f"✅ VTT: {len(vtt_segments)} segments loaded")
        else:
            logger.warning("⚠️  No VTT available — will need Whisper transcription")

        # Fetch agenda timestamps
        agenda_timestamps = scraper.fetch_agenda_timestamps(metadata)
        metadata.agenda_timestamps = agenda_timestamps
        if agenda_timestamps:
            logger.info(f"✅ Agenda: {len(agenda_timestamps)} items with timestamps")
        else:
            logger.info("ℹ️  No agenda timestamps from events API")

        # ── Phase 1.1: Scrape iBabs Speakers ──────────────────────────
        ibabs_speakers = []
        if ibabs_url:
            logger.info("🎙️  Phase 1.1: Scraping speakers from iBabs...")
            ibabs_speakers = scraper.scrape_ibabs_speakers(ibabs_url)
            metadata.speaker_segments = ibabs_speakers
            if ibabs_speakers:
                logger.info(f"✅ iBabs Speakers: {len(ibabs_speakers)} segments found")
                
                # If agenda timestamps are missing, infer them from speakers
                if not agenda_timestamps:
                    logger.info("🔄 Inferring agenda items from iBabs speaker metadata...")
                    agenda_timestamps = TranscriptAligner.infer_agenda_from_speakers(ibabs_speakers)
                    metadata.agenda_timestamps = agenda_timestamps
                    if agenda_timestamps:
                        logger.info(f"✅ Inferred Agenda: {len(agenda_timestamps)} items")
                
                # Normalize iBabs speakers
                if normalizer and ibabs_speakers:
                    logger.info("⚖️  Normalizing iBabs speakers...")
                    normalizer.normalize_segments(ibabs_speakers)
            else:
                logger.warning("⚠️  No speakers found on iBabs page")

        # ── Phase 2+3: Video Processing (if not VTT-only and no iBabs data) ──
        detected_speakers = []

        # We skip video download/OCR if iBabs speaker data is already found, 
        # unless specifically requested or VTT-only is false but we want double verification
        skip_video = vtt_only or (len(ibabs_speakers) > 0)
        video_url = metadata.mp4_url or metadata.hls_path
        
        if not skip_video and video_url:
            logger.info("\n🎬 Phase 2: Downloading video & extracting frames...")
            processor = MediaProcessor(temp_dir=temp_dir)

            # Download video (may fail on archived Royalcast)
            try:
                if video_url.endswith('.m3u8'):
                    video_path = processor.download_hls_video(video_url)
                else:
                    video_path = processor.download_video(video_url)
            except Exception as e:
                logger.warning(f"⚠️  Video download failed: {e}. Attempting direct 'Surgical OCR' from URL...")
                video_path = None

            # NEW: Allow OCR directly from URL if local download failed/skipped
            target_path = video_path or video_url
            
            if target_path:
                # Extract frames for OCR across the full meeting
                all_frames = processor.extract_ocr_frames(
                    video_path=target_path,
                    start_sec=0,
                    end_sec=metadata.duration_seconds,
                    fps=0.5,
                    item_label="full_meeting",
                )

                # Delete video immediately after frame extraction
                processor.delete_video()
                logger.info("🗑️  Video file deleted (frames retained for OCR)")

                # ── Phase 3: OCR Speaker Detection ───────────────────────
                logger.info("\n🔍 Phase 3: Running OCR for speaker detection...")
                detector = SpeakerDetector()
                detected_speakers = detector.detect_speakers_from_frames(
                    frame_paths=[f.path for f in all_frames],
                    timestamps=[f.timestamp_seconds for f in all_frames],
                )
                logger.info(f"✅ OCR: Detected {len(detected_speakers)} speaker changes")

                # Normalize detected speakers
                if normalizer and detected_speakers:
                    logger.info("⚖️  Normalizing detected speakers...")
                    normalizer.normalize_segments(detected_speakers)
                
                # GC after memory-heavy OCR
                gc.collect()
            else:
                logger.warning("⚠️  Skipping OCR Phase (no video available)")

        elif not vtt_only and not video_url:
            logger.warning("⚠️  No Video URL (MP4/HLS) available — skipping video processing")

        # ── Whisper Fallback (Archive Strategy) ───────────────────────
        whisper_segments = []
        # If we need Whisper (use_whisper requested or no VTT available),
        # we try to get audio from MP3 or HLS
        if (use_whisper or not vtt_segments):
            if metadata.mp3_url or metadata.hls_path:
                logger.info("\n🎤 Running mlx-whisper transcription (fallback)...")
                if processor is None:
                    processor = MediaProcessor(temp_dir=temp_dir)
                
                audio_path = None
                try:
                    if metadata.mp3_url:
                        audio_path = processor.download_mp3(metadata.mp3_url)
                except Exception as e:
                    logger.warning(f"⚠️  Direct MP3 download failed: {e}. Trying HLS...")

                if not audio_path and metadata.hls_path:
                    try:
                        audio_path = processor.download_hls_audio(metadata.hls_path)
                    except Exception as e:
                        logger.error(f"❌ HLS audio download failed: {e}")

                if audio_path:
                    if download_only:
                        logger.info(f"✅ Download Only mode active. Audio saved to: {audio_path}")
                        # Move out of temp dir to a permanent downloads folder
                        downloads_dir = Path("downloads")
                        downloads_dir.mkdir(exist_ok=True)
                        dest_path = downloads_dir / f"meeting_{metadata.uuid}_{metadata.start_time.split('T')[0] if metadata.start_time else 'unknown'}.mp4"
                        
                        # Just a simple copy/move
                        import shutil
                        shutil.copy2(audio_path, dest_path)
                        logger.info(f"💾 Saved permanent audio to: {dest_path}")
                        
                        # Return early
                        return {"status": "success", "message": "download_only", "audio_path": str(dest_path)}

                    transcriber = WhisperTranscriber(model_name=whisper_model) if whisper_model else WhisperTranscriber()
                    whisper_segments = transcriber.transcribe(str(audio_path))
                    logger.info(f"✅ Whisper: {len(whisper_segments)} segments")
                    
                    # Convert Whisper TranscriptSegments to VTTSegments for alignment compatibility
                    vtt_segments = [
                        VTTSegment(
                            start_seconds=s.start_seconds,
                            end_seconds=s.end_seconds,
                            speaker_label=None,
                            text=s.text
                        ) for s in whisper_segments
                    ]
                    metadata.vtt_available = True
                    metadata.vtt_segments = vtt_segments
                else:
                    logger.error("❌ No audio source (MP3/HLS) available for Whisper!")
            else:
                logger.warning("⚠️  No audio source available — skipping Whisper")

        # ── Phase 1.2: PDF Fallback (for ConnectLive/Missing Transcripts) ──
        if not vtt_segments and not whisper_segments:
            logger.info("📄 Phase 1.2: No video transcript found. Attempting PDF (Notulen) fallback...")
            pdf_url = None
            
            # 1. Try finding PDF via ORI API if we have a numeric ID
            if numeric_id:
                logger.info(f"Searching ORI API for documents (ID: {numeric_id})...")
                raad_service = OpenRaadService()
                meeting_details = run_async(raad_service.get_meeting_details(numeric_id))
                for item in meeting_details.get('agenda', []):
                    for doc in item.get('documents', []):
                        doc_name = doc.get('name', '').lower()
                        if 'notulen' in doc_name or 'besluitenlijst' in doc_name:
                            pdf_url = doc.get('url')
                            logger.info(f"✅ Found Notulen PDF via ORI: {doc_name} -> {pdf_url}")
                            break
                    if pdf_url: break

            # 2. Try scraping iBabs if ORI failed or wasn't available
            if not pdf_url and ibabs_url:
                pdf_url = scraper.fetch_notulen_pdf_url(ibabs_url)

            if pdf_url:
                from pipeline.pdf_processor import PDFScraper
                pdf_scraper = PDFScraper()
                pdf_text = run_async(pdf_scraper.extract_text_from_url(pdf_url))
                if pdf_text:
                    logger.info(f"✅ PDF: Extracted {len(pdf_text)} characters from {pdf_url}")
                    # Create a single large VTT segment for the whole duration
                    # or several segments if text is huge
                    vtt_segments = [VTTSegment(
                        start_seconds=0,
                        end_seconds=metadata.duration_seconds or 3600*3, # Default 3h if unknown
                        speaker_label="pdf_source",
                        text=pdf_text
                    )]
                    metadata.vtt_available = True
                    metadata.vtt_segments = vtt_segments
                else:
                    logger.warning("⚠️  PDF found but text extraction failed.")
            else:
                logger.warning("⚠️  No Notulen PDF found on iBabs page.")

        # ── Phase 4: Alignment & Output ──────────────────────────────
        logger.info("\n🔗 Phase 4: Aligning speakers with transcript...")

        # Use VTT segments as primary, Whisper as fallback
        if vtt_segments:
            if ibabs_speakers:
                logger.info("Using iBabs metadata for speaker alignment")
                # Special case: if we have a single large PDF segment, 
                # we don't 'align' text to speakers, we just keep the PDF text as a whole
                # and let the ingestion handle it.
                if vtt_segments[0].speaker_label == "pdf_source":
                    logger.info("PDF source detected: skipping per-segment timestamp alignment")
                    aligned_segments = vtt_segments
                else:
                    aligned_segments = TranscriptAligner.align_speakers_with_ibabs(
                        vtt_segments=vtt_segments,
                        ibabs_speakers=ibabs_speakers,
                    )
            else:
                logger.info("Using OCR for speaker alignment")
                aligned_segments = TranscriptAligner.align_speakers_with_vtt(
                    vtt_segments=vtt_segments,
                    detected_speakers=detected_speakers,
                )
        elif whisper_segments:
            if ibabs_speakers:
                logger.info("Using iBabs metadata for Whisper alignment")
                aligned_segments = TranscriptAligner.align_speakers_with_ibabs(
                    vtt_segments=whisper_segments,
                    ibabs_speakers=ibabs_speakers,
                )
            else:
                logger.info("Using OCR for Whisper alignment")
                aligned_segments = TranscriptAligner.align_speakers_with_vtt(
                    vtt_segments=[],  # No VTT labels to map
                    detected_speakers=detected_speakers,
                )
            # Fallback: just use Whisper segments without speaker info
            if not aligned_segments:
                aligned_segments = whisper_segments
        else:
            logger.error("❌ No transcript source available!")
            aligned_segments = []

        # Split by agenda items
        agenda_transcripts = TranscriptAligner.split_by_agenda(
            aligned_segments, agenda_timestamps
        )

        # Build final output
        output = {
            "meeting_id": metadata.uuid,
            "webcast_code": metadata.webcast_code,
            "meeting_name": metadata.label,
            "date": metadata.start_time[:10] if metadata.start_time else None,
            "duration": metadata.duration,
            "total_segments": len(aligned_segments),
            "total_speakers_detected": len(detected_speakers) or len(ibabs_speakers),
            "speakers": list(set(
                s.name for s in ibabs_speakers
            )) if ibabs_speakers else (list(set(
                d.name for d in detected_speakers
            )) if detected_speakers else []),
            "transcript_source": "vtt" if vtt_segments else ("whisper" if whisper_segments else "none"),
            "speaker_source": "ibabs" if ibabs_speakers else ("ocr" if detected_speakers else "none"),
            "processed_at": datetime.now(tz=None).isoformat(),
            "agenda_items": [item.to_dict() for item in agenda_transcripts],
        }

        # ── Save Output ──────────────────────────────────────────────
        if output_path is None:
            output_dir = Path("output/transcripts")
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(output_dir / f"{webcast_code}.json")

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        # ── Phase 5: RAG Ingestion ───────────────────────────────────
        if not no_ingest:
            from pipeline.ingestion import SmartIngestor
            ingestor = SmartIngestor()
            ingestor.ingest_transcript(output, heuristic=heuristic, category=category)
            logger.info(f"✅ RAG Ingestion complete (Category: {category})")
            
            # Final GC after ingestion
            gc.collect()

        # ── Phase 6: Video Assets ────────────────────────────────────
        if split_video and video_url:
            logger.info("\n🎬 Phase 6: Extracting video assets per agenda item...")
            processor = MediaProcessor(temp_dir=temp_dir)
            
            # Ensure video is downloaded if not already here
            if video_url.endswith('.m3u8'):
                video_path = processor.download_hls_video(video_url)
            else:
                video_path = processor.download_video(video_url)
            
            assets_dir = Path("output/assets") / webcast_code
            processor.extract_video_clips(output.get("agenda_items", []), assets_dir, video_path)
            
            # Cleanup large video file but keep clips
            processor.delete_video()
            logger.info(f"✅ Video assets saved to {assets_dir}")

        return output

    finally:
        # ── Cleanup ──────────────────────────────────────────────────
        if processor:
            processor.cleanup()
            logger.info("🗑️  All temporary files cleaned up")

        logger.info(f"\n{'='*60}")
        logger.info("Pipeline Complete")
        logger.info(f"{'='*60}")


# ── CLI Entry Point ───────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="NeoDemos Video Pipeline — Committee Meeting Transcription",
    )
    parser.add_argument(
        "webcast_code",
        help="Royalcast webcast code (e.g., gemeenterotterdam_20260107_1) or iBabs URL",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output JSON path (default: output/transcripts/{code}.json)",
    )
    parser.add_argument(
        "--vtt-only",
        action="store_true",
        help="Skip video download and OCR; use VTT transcript only",
    )
    parser.add_argument(
        "--whisper",
        action="store_true",
        help="Force mlx-whisper transcription even with VTT available",
    )
    parser.add_argument(
        "--temp-dir",
        help="Temporary directory for media files",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--no-normalize",
        action="store_true",
        help="Skip speaker/party entity normalization",
    )
    parser.add_argument(
        "--no-ingest",
        action="store_true",
        help="Skip RAG ingestion/chunking",
    )
    parser.add_argument(
        "--heuristic",
        action="store_true",
        help="Use high-speed heuristic chunking instead of Gemini",
    )
    parser.add_argument(
        "--split-video",
        action="store_true",
        help="Extract physical video clips per agenda item",
    )
    parser.add_argument(
        "--numeric-id",
        help="Numeric ORI meeting ID for document discovery",
    )
    parser.add_argument(
        "--whisper-model",
        help="Specific MLX Whisper model name (e.g. mlx-community/whisper-tiny-mlx)",
    )
    parser.add_argument(
        "--category",
        default="committee_transcript",
        help="Category tag for ingestion",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    result = run_pipeline(
        webcast_code=args.webcast_code,
        output_path=args.output,
        vtt_only=args.vtt_only,
        use_whisper=args.whisper,
        temp_dir=args.temp_dir,
        no_normalize=args.no_normalize,
        no_ingest=args.no_ingest,
        heuristic=args.heuristic,
        split_video=args.split_video,
        numeric_id=args.numeric_id if hasattr(args, 'numeric_id') else None,
        whisper_model=args.whisper_model if hasattr(args, 'whisper_model') else None,
        category=args.category if hasattr(args, 'category') else "committee_transcript"
    )

    # Print summary
    print(f"\n📊 Summary:")
    print(f"   Meeting:  {result['meeting_name']}")
    print(f"   Date:     {result['date']}")
    print(f"   Duration: {result['duration']}")
    print(f"   Segments: {result['total_segments']}")
    print(f"   Speakers: {', '.join(result['speakers']) if result['speakers'] else 'N/A (VTT-only)'}")
    print(f"   Source:   {result['transcript_source']}")


if __name__ == "__main__":
    main()
