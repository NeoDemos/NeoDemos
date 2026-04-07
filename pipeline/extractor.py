"""
Phase 3+4: AI Extractor — OCR, Whisper, and Alignment
=======================================================
1. macOS Vision OCR for speaker name/party detection from video frames
2. mlx-whisper for fallback transcription (when VTT is unavailable)
3. Alignment: merges OCR speaker names with VTT/Whisper transcript segments

Usage:
    from pipeline.extractor import SpeakerDetector, WhisperTranscriber, TranscriptAligner
"""

import re
import json
import logging
import gc
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict

logger = logging.getLogger(__name__)


# ── Data Classes ──────────────────────────────────────────────────────

@dataclass
class DetectedSpeaker:
    """A speaker name detected via OCR at a specific timestamp."""
    name: str
    party: Optional[str]
    timestamp_seconds: float
    confidence: float = 0.0
    raw_text: str = ""


@dataclass
class TranscriptSegment:
    """A single segment of the final aligned transcript."""
    speaker: Optional[str]
    party: Optional[str]
    text: str
    start_seconds: float
    end_seconds: float
    confidence: float = 1.0  # Default 1.0 (for VTT which has no confidence)
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    start_formatted: str = ""
    end_formatted: str = ""

    def __post_init__(self):
        if not self.start_formatted:
            self.start_formatted = _format_time(self.start_seconds)
        if not self.end_formatted:
            self.end_formatted = _format_time(self.end_seconds)


@dataclass
class AgendaTranscript:
    """Complete transcript for one agenda item."""
    title: str
    start_time: str
    end_time: str
    segments: List[TranscriptSegment] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Speaker Detector (macOS Vision OCR) ───────────────────────────────

class SpeakerDetector:
    """
    Uses macOS Vision framework to detect speaker names and party labels
    from the lower-third overlay in video frames.
    """

    def __init__(self):
        self._vision_available = False
        try:
            import Vision
            import Quartz
            self._vision_available = True
            logger.info("macOS Vision framework loaded successfully")
        except ImportError:
            logger.warning(
                "macOS Vision framework not available. "
                "Install: pip install pyobjc-framework-Vision pyobjc-framework-Quartz"
            )

    def detect_speakers_from_frames(
        self, frame_paths: List[str], timestamps: List[float]
    ) -> List[DetectedSpeaker]:
        """
        Run OCR on a list of frame images and extract speaker names.

        Args:
            frame_paths: Paths to JPEG frame images (bottom-cropped)
            timestamps: Corresponding timestamps for each frame

        Returns:
            List of DetectedSpeaker objects
        """
        if not self._vision_available:
            logger.warning("Vision OCR not available; returning empty speaker list")
            return []

        speakers = []
        seen_at_timestamp = {}

        for path, ts in zip(frame_paths, timestamps):
            try:
                texts = self._ocr_frame(path)
                if texts:
                    parsed = self._parse_speaker_text(texts)
                    if parsed:
                        name, party = parsed
                        # Deduplicate: skip if same name detected within 5 seconds
                        key = name.lower().strip()
                        if key in seen_at_timestamp and abs(ts - seen_at_timestamp[key]) < 5.0:
                            continue
                        seen_at_timestamp[key] = ts
                        speakers.append(DetectedSpeaker(
                            name=name,
                            party=party,
                            timestamp_seconds=ts,
                            raw_text=" | ".join(texts),
                        ))
            except Exception as e:
                logger.debug(f"OCR failed for {path}: {e}")

        logger.info(f"Detected {len(speakers)} speaker changes across {len(frame_paths)} frames")
        return speakers

    def _ocr_frame(self, image_path: str) -> List[str]:
        """Run macOS Vision OCR on a single image, return detected text lines."""
        import Vision
        import Quartz
        import objc

        with objc.autorelease_pool():
            # Load image
            image_url = Quartz.CFURLCreateWithFileSystemPath(
                None, image_path, Quartz.kCFURLPOSIXPathStyle, False
            )
            ci_image = Quartz.CIImage.imageWithContentsOfURL_(image_url)
            if ci_image is None:
                return []
    
            # Create CGImage from CIImage
            context = Quartz.CIContext.contextWithOptions_(None)
            extent = ci_image.extent()
            cg_image = context.createCGImage_fromRect_(ci_image, extent)
            if cg_image is None:
                return []
    
            # Create text recognition request
            request = Vision.VNRecognizeTextRequest.alloc().init()
            request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
            request.setRecognitionLanguages_(["nl", "en"])
            request.setUsesLanguageCorrection_(True)
    
            # Perform request
            handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
                cg_image, None
            )
            success = handler.performRequests_error_([request], None)
    
            if not success[0]:
                return []
    
            # Extract text
            results = request.results()
            texts = []
            for obs in results or []:
                candidate = obs.topCandidates_(1)
                if candidate:
                    text = candidate[0].string()
                    conf = candidate[0].confidence()
                    if conf > 0.3:  # Minimum confidence
                        texts.append(text)
    
            return texts

    def _parse_speaker_text(self, texts: List[str]) -> Optional[Tuple[str, Optional[str]]]:
        """
        Parse OCR text to extract (speaker_name, party).

        The lower-third overlay typically shows:
            Line 1: "Speaker Name"
            Line 2: "Party Name" (optional)

        Common patterns:
            "J. de Vries" / "GroenLinks-PvdA"
            "Wethouder Bonte"
            "Voorzitter De Langen"
        """
        if not texts:
            return None

        # Filter out noise (very short, numeric, or common non-name text)
        noise_patterns = [
            r'^\d+$',  # Pure numbers
            r'^[A-Z]{2,}$',  # Likely a station ID
            r'rotterdam',  # Background text
            r'commissie',
            r'raadzaal',
            r'live',
            r'^\s*$',
        ]

        clean_texts = []
        for t in texts:
            t = t.strip()
            if len(t) < 2 or len(t) > 80:
                continue
            if any(re.search(p, t, re.IGNORECASE) for p in noise_patterns):
                continue
            clean_texts.append(t)

        if not clean_texts:
            return None

        # Known Dutch political parties for matching
        parties = {
            "groenlinks", "pvda", "groenlinks-pvda", "vvd", "d66", "cda",
            "pvv", "sp", "christenunie", "denk", "leefbaar rotterdam",
            "50plus", "volt", "bij1", "partij voor de dieren", "sgp",
            "nida", "forum voor democratie", "ja21",
        }

        name = None
        party = None

        for t in clean_texts:
            t_lower = t.lower().strip()
            # Check if this line is a party name
            if any(p in t_lower for p in parties):
                party = t.strip()
            elif name is None:
                # First non-party text is likely the name
                name = t.strip()

        if name:
            return (name, party)
        return None


# ── Whisper Transcriber (MLX, fallback) ───────────────────────────────

class WhisperTranscriber:
    """
    Fallback transcription using mlx-whisper on Apple Silicon.
    Only used when WebVTT is unavailable or garbled.
    """

    def __init__(self, model_name: str = "mlx-community/whisper-large-v3-turbo",
                 use_vad: bool = True):
        self.model_name = model_name
        self._model_loaded = False
        self._temp_dir = Path("/tmp/neodemos_whisper")
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self.use_vad = use_vad

        # Load initial_prompt from Rotterdam political dictionary
        self.initial_prompt = self._load_initial_prompt()

    def _load_initial_prompt(self) -> str:
        """Load the Whisper initial_prompt from the political dictionary."""
        dict_path = Path(__file__).parent.parent / "data" / "lexicons" / "rotterdam_political_dictionary.json"
        try:
            with open(dict_path, "r", encoding="utf-8") as f:
                dictionary = json.load(f)
            return dictionary.get("whisper_initial_prompt", "")
        except Exception as e:
            logger.warning(f"Could not load political dictionary for Whisper prompt: {e}")
            return ""

    def _preprocess_vad(self, audio_path: str) -> str:
        """Use Silero VAD to strip silence, reducing hallucinations on long recordings.

        Returns path to VAD-processed audio, or original path if VAD unavailable.
        """
        try:
            import torch
            import torchaudio
        except ImportError:
            logger.debug("torch/torchaudio not available, skipping VAD preprocessing")
            return audio_path

        try:
            model, utils = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                trust_repo=True
            )
            (get_speech_timestamps, _, read_audio, _, _) = utils

            wav = read_audio(audio_path, sampling_rate=16000)
            speech_timestamps = get_speech_timestamps(wav, model, sampling_rate=16000)

            if not speech_timestamps:
                logger.warning("VAD found no speech in audio")
                return audio_path

            # Concatenate speech segments with small padding
            segments = []
            for ts in speech_timestamps:
                start = max(0, ts['start'] - 1600)  # 100ms padding
                end = min(len(wav), ts['end'] + 1600)
                segments.append(wav[start:end])

            if segments:
                speech_only = torch.cat(segments)
                vad_path = self._temp_dir / "vad_processed.wav"
                torchaudio.save(str(vad_path), speech_only.unsqueeze(0), 16000)
                silence_removed = len(wav) - len(speech_only)
                logger.info(
                    f"VAD: removed {silence_removed / 16000:.1f}s silence "
                    f"({len(speech_timestamps)} speech segments)"
                )
                return str(vad_path)

        except Exception as e:
            logger.warning(f"VAD preprocessing failed, using original audio: {e}")

        return audio_path

    def transcribe(self, audio_path: str, language: str = "nl") -> List[TranscriptSegment]:
        """
        Transcribe an audio file using mlx-whisper.

        Returns list of TranscriptSegment (without speaker info — needs OCR alignment).
        """
        try:
            import mlx_whisper
        except ImportError:
            logger.error("mlx-whisper not installed. Install: pip install mlx-whisper")
            return []

        import subprocess
        
        # Check audio length using ffprobe
        try:
            cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path]
            duration = float(subprocess.check_output(cmd).decode().strip())
        except Exception as e:
            logger.warning(f"Could not determine audio duration, proceeding without chunking: {e}")
            duration = 0

        # Optional VAD preprocessing to strip silence (reduces hallucinations)
        if self.use_vad and duration > 300:
            audio_path = self._preprocess_vad(audio_path)

        # If longer than 15 minutes, chunk it
        if duration > 900:
            logger.info(f"Audio is {duration/60:.1f}m long. Using chunked transcription to save RAM.")
            return self._transcribe_chunked(audio_path, duration, language)

        logger.info(f"Transcribing with mlx-whisper: {audio_path}")
        gc.collect()

        transcribe_kwargs = dict(
            path_or_hf_repo=self.model_name,
            language=language,
            word_timestamps=True,
        )
        if self.initial_prompt:
            transcribe_kwargs["initial_prompt"] = self.initial_prompt

        result = mlx_whisper.transcribe(
            audio_path,
            **transcribe_kwargs,
        )

        segments = []
        for seg in result.get("segments", []):
            # Calculate a heuristic 0-1 confidence from avg_logprob
            # Whisper logprobs are typically in range [-2, 0]
            # -1.0 is ~36% confidence, -0.5 is ~60%, -0.2 is ~80%, 0 is 100%
            import math
            logprob = seg.get("avg_logprob", 0)
            conf = math.exp(max(min(logprob, 0), -3)) # Cap at -3 (~5%)
            
            segments.append(TranscriptSegment(
                speaker=None,
                party=None,
                text=seg["text"].strip(),
                start_seconds=seg["start"],
                end_seconds=seg["end"],
                confidence=conf,
                avg_logprob=logprob,
                no_speech_prob=seg.get("no_speech_prob", 0)
            ))
        return segments

    def _transcribe_chunked(self, audio_path: str, duration: float, language: str) -> List[TranscriptSegment]:
        import mlx_whisper
        import subprocess
        
        chunk_size = 600  # 10 minutes
        all_segments = []
        
        for start in range(0, int(duration), chunk_size):
            chunk_file = self._temp_dir / f"chunk_{start}.wav"
            logger.info(f"Processing chunk {start//60}-{(start+chunk_size)//60} min...")
            
            # Extract chunk using ffmpeg
            cmd = [
                "ffmpeg", "-y", "-nostdin", "-ss", str(start), "-t", str(chunk_size),
                "-i", audio_path, "-ac", "1", "-ar", "16000", str(chunk_file)
            ]
            subprocess.run(cmd, capture_output=True)
            
            if not chunk_file.exists():
                continue
                
            gc.collect()
            transcribe_kwargs = dict(
                path_or_hf_repo=self.model_name,
                language=language,
                word_timestamps=True,
            )
            if self.initial_prompt:
                transcribe_kwargs["initial_prompt"] = self.initial_prompt

            result = mlx_whisper.transcribe(
                str(chunk_file),
                **transcribe_kwargs,
            )
            
            for seg in result.get("segments", []):
                import math
                logprob = seg.get("avg_logprob", 0)
                conf = math.exp(max(min(logprob, 0), -3))
                
                all_segments.append(TranscriptSegment(
                    speaker=None,
                    party=None,
                    text=seg["text"].strip(),
                    start_seconds=seg["start"] + start,
                    end_seconds=seg["end"] + start,
                    confidence=conf,
                    avg_logprob=logprob,
                    no_speech_prob=seg.get("no_speech_prob", 0)
                ))
            
            # Cleanup chunk
            chunk_file.unlink(missing_ok=True)
            del result
            gc.collect()
            import mlx.core as mx
            mx.metal.clear_cache()
            
        return all_segments
        
        # Explicitly clear result and trigger GC
        del result
        gc.collect()
        import mlx.core as mx
        mx.metal.clear_cache()
        
        return segments


# ── Transcript Aligner ────────────────────────────────────────────────

class TranscriptAligner:
    """
    Merges speaker detections (iBabs HTML or OCR) with VTT/Whisper transcript segments
    based on timestamp overlap.
    """

    @staticmethod
    def align_speakers_with_ibabs(
        vtt_segments: list,  # List[VTTSegment] from scraper
        ibabs_speakers: list, # List[SpeakerSegment] from scraper
    ) -> List[TranscriptSegment]:
        """
        Map VTT segments to speakers using iBabs-sourced speaker metadata.
        iBabs data is generally the 'ground truth' for who is speaking and when.
        """
        if not vtt_segments:
            return []
        if not ibabs_speakers:
            # Fallback to anonymous segments
            return [TranscriptSegment(
                speaker=None, party=None, text=seg.text, 
                start_seconds=seg.start_seconds, end_seconds=seg.end_seconds
            ) for seg in vtt_segments]

        aligned = []
        for seg in vtt_segments:
            # Find the speaker whose time range covers this VTT segment
            # We look for the speaker that was active at the START of the VTT segment
            speaker = None
            
            # Since ibabs_speakers is sorted, we can be efficient, but a simple loop is fine for ~50-100 speakers
            for ib in ibabs_speakers:
                # If VTT segment starts within the iBabs speaker's range
                # Or if the iBabs speaker range is within the VTT segment (unlikely but possible)
                if ib.start_seconds <= seg.start_seconds < ib.end_seconds:
                    speaker = ib
                    break
            
            # Fallback: if no exact match, find the one with the most overlap
            if not speaker:
                max_overlap = 0
                for ib in ibabs_speakers:
                    overlap = min(seg.end_seconds, ib.end_seconds) - max(seg.start_seconds, ib.start_seconds)
                    if overlap > max_overlap:
                        max_overlap = overlap
                        speaker = ib
                
                # Only use if overlap is significant (e.g. > 1s)
                if max_overlap < 1.0:
                    speaker = None

            aligned.append(TranscriptSegment(
                speaker=speaker.name if speaker else None,
                party=speaker.party if speaker else None,
                text=seg.text,
                start_seconds=seg.start_seconds,
                end_seconds=seg.end_seconds,
            ))

        # Merge consecutive segments from the same speaker
        return TranscriptAligner._merge_consecutive(aligned)

    @staticmethod
    def align_speakers_with_vtt(
        vtt_segments: list,  # List[VTTSegment] from scraper
        detected_speakers: List[DetectedSpeaker],
    ) -> List[TranscriptSegment]:
        """
        Assign real speaker names (from OCR) to VTT segments (which have
        anonymous labels like 'spk1').

        Strategy:
        1. Build a mapping: spk_label -> real_name using temporal proximity
        2. Apply mapping to all VTT segments
        3. Merge consecutive segments from the same speaker
        """
        if not vtt_segments:
            return []

        # Step 1: Build speaker label -> real name mapping
        label_to_name: Dict[str, Tuple[str, Optional[str]]] = {}

        for det in detected_speakers:
            # Find the VTT segment closest in time to this OCR detection
            best_seg = None
            best_dist = float("inf")
            for seg in vtt_segments:
                dist = abs(seg.start_seconds - det.timestamp_seconds)
                if dist < best_dist:
                    best_dist = dist
                    best_seg = seg

            if best_seg and best_seg.speaker_label and best_dist < 10.0:
                label = best_seg.speaker_label
                # Update mapping (later detections override earlier ones for same label)
                if label not in label_to_name:
                    label_to_name[label] = (det.name, det.party)
                    logger.info(f"Mapped {label} → {det.name} ({det.party}) "
                                f"at {det.timestamp_seconds:.0f}s")

        # Also track label changes over time (same label might be reused
        # for different speakers across the meeting)
        speaker_timeline: List[Tuple[float, str, Optional[str]]] = []
        for det in sorted(detected_speakers, key=lambda d: d.timestamp_seconds):
            speaker_timeline.append((det.timestamp_seconds, det.name, det.party))

        # Step 2: Assign names to VTT segments
        aligned = []
        for seg in vtt_segments:
            speaker_name = None
            speaker_party = None

            # First try: lookup from the label mapping
            if seg.speaker_label and seg.speaker_label in label_to_name:
                speaker_name, speaker_party = label_to_name[seg.speaker_label]

            # If timeline-based detection gives a closer match, use that
            if speaker_timeline:
                closest = min(speaker_timeline,
                              key=lambda t: abs(t[0] - seg.start_seconds))
                if abs(closest[0] - seg.start_seconds) < 30.0:
                    speaker_name = closest[1]
                    speaker_party = closest[2]

            aligned.append(TranscriptSegment(
                speaker=speaker_name,
                party=speaker_party,
                text=seg.text,
                start_seconds=seg.start_seconds,
                end_seconds=seg.end_seconds,
            ))

        # Step 3: Merge consecutive segments from the same speaker
        merged = TranscriptAligner._merge_consecutive(aligned)
        return merged

    @staticmethod
    def infer_agenda_from_speakers(
        ibabs_speakers: list, # List[SpeakerSegment]
    ) -> list: # List[AgendaTimestamp]
        """
        If official agenda timestamps are missing, infer them from the titles
        associated with speaker segments.
        """
        from pipeline.scraper import AgendaTimestamp
        
        timestamps = []
        seen_titles = {}
        
        for speaker in ibabs_speakers:
            if speaker.agenda_item_title:
                title = speaker.agenda_item_title
                start = speaker.start_seconds
                
                # If we haven't seen this title, or if this segment starts earlier
                if title not in seen_titles:
                    seen_titles[title] = start
                    timestamps.append(AgendaTimestamp(title=title, start_seconds=start))
                else:
                    # Update to earlier start if found (though iBabs is usually chronological)
                    if start < seen_titles[title]:
                        seen_titles[title] = start
                        for ts in timestamps:
                            if ts.title == title:
                                ts.start_seconds = start
        
        # Sort by time
        timestamps.sort(key=lambda x: x.start_seconds)
        
        # Fill in end times
        for i, ts in enumerate(timestamps):
            if i + 1 < len(timestamps):
                ts.end_seconds = timestamps[i + 1].start_seconds
            # Last one will keep None or be filled by meeting duration elsewhere
            
        return timestamps

    @staticmethod
    def split_by_agenda(
        segments: List[TranscriptSegment],
        agenda_timestamps: list,  # List[AgendaTimestamp]
    ) -> List[AgendaTranscript]:
        """
        Split aligned transcript segments into agenda item groups.
        """
        if not agenda_timestamps:
            # No agenda info — return everything as one item
            return [AgendaTranscript(
                title="Full Meeting",
                start_time=_format_time(segments[0].start_seconds) if segments else "0:00:00",
                end_time=_format_time(segments[-1].end_seconds) if segments else "0:00:00",
                segments=segments,
            )]

        items = []
        for ts in agenda_timestamps:
            item_segments = [
                s for s in segments
                if ts.start_seconds <= s.start_seconds < (ts.end_seconds or float("inf"))
            ]
            items.append(AgendaTranscript(
                title=ts.title,
                start_time=_format_time(ts.start_seconds),
                end_time=_format_time(ts.end_seconds) if ts.end_seconds else "end",
                segments=item_segments,
            ))

        return items

    @staticmethod
    def _merge_consecutive(segments: List[TranscriptSegment]) -> List[TranscriptSegment]:
        """Merge consecutive segments that have the same speaker."""
        if not segments:
            return []

        merged = [segments[0]]
        for seg in segments[1:]:
            prev = merged[-1]
            # Merge if same speaker and time gap < 2 seconds
            if (prev.speaker == seg.speaker
                    and seg.start_seconds - prev.end_seconds < 2.0):
                merged[-1] = TranscriptSegment(
                    speaker=prev.speaker,
                    party=prev.party,
                    text=prev.text + " " + seg.text,
                    start_seconds=prev.start_seconds,
                    end_seconds=seg.end_seconds,
                )
            else:
                merged.append(seg)

        return merged


# ── Helpers ───────────────────────────────────────────────────────────

def _format_time(seconds: float) -> str:
    """Format seconds to H:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h}:{m:02d}:{s:02d}"
