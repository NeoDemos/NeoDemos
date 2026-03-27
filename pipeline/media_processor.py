"""
Phase 2: Media Processor (ffmpeg)
==================================
Downloads video and extracts:
  1. Bottom-20% cropped frames at 0.5 fps for OCR (speaker name detection)
  2. Audio-only WAV for Whisper transcription (fallback)

All temp files are managed and can be cleaned up after processing.

Usage:
    from pipeline.media_processor import MediaProcessor
    processor = MediaProcessor(temp_dir="/tmp/neodemos_pipeline")
    frames = processor.extract_ocr_frames(mp4_url, start_sec=345, end_sec=753)
    audio  = processor.extract_audio(mp4_url, start_sec=345, end_sec=753)
"""

import os
import subprocess
import tempfile
import shutil
import logging
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
from dataclasses import dataclass

import requests
from tqdm import tqdm

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://rotterdamraad.bestuurlijkeinformatie.nl/",
}


@dataclass
class FrameInfo:
    """Metadata for an extracted video frame."""
    path: str
    timestamp_seconds: float
    agenda_item_title: Optional[str] = None


class MediaProcessor:
    """Handles video downloading and ffmpeg-based extraction."""

    def __init__(self, temp_dir: Optional[str] = None):
        self.temp_dir = Path(temp_dir or tempfile.mkdtemp(prefix="neodemos_pipeline_"))
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self._downloaded_video: Optional[Path] = None
        logger.info(f"MediaProcessor temp dir: {self.temp_dir}")

        # Verify ffmpeg is available
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise RuntimeError("ffmpeg not found. Install with: brew install ffmpeg")

    # ── Public API ────────────────────────────────────────────────────

    def download_video(
        self,
        mp4_url: str,
        filename: str = "meeting.mp4",
        webcast_code: Optional[str] = None,
    ) -> Path:
        """
        Download the MP4 video to the temp directory.
        Uses a dynamic Referer to avoid 403 Forbidden from the CDN.
        Shows a progress bar for large files.
        """
        output_path = self.temp_dir / filename

        if output_path.exists():
            logger.info(f"Video already downloaded: {output_path}")
            self._downloaded_video = output_path
            return output_path

        # Build a Referer that matches the Royalcast player URL.
        # Without a matching Referer, some CDN endpoints return 403.
        headers = dict(HEADERS)
        if webcast_code:
            headers["Referer"] = f"https://sdk.companywebcast.com/sdk/player/?id={webcast_code}"

        logger.info(f"Downloading video: {mp4_url}")
        resp = requests.get(mp4_url, headers=headers, stream=True, timeout=300)
        resp.raise_for_status()

        total = int(resp.headers.get("content-length", 0))
        with open(output_path, "wb") as f:
            with tqdm(total=total, unit="B", unit_scale=True, desc="Downloading MP4") as pbar:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    pbar.update(len(chunk))

        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info(f"Downloaded {size_mb:.1f} MB to {output_path}")
        self._downloaded_video = output_path
        return output_path

    def extract_ocr_frames(
        self,
        video_path: Optional[Path] = None,
        start_sec: float = 0,
        end_sec: Optional[float] = None,
        fps: float = 0.5,
        crop_bottom_pct: float = 0.20,
        item_label: str = "segment",
    ) -> List[FrameInfo]:
        """
        Extract bottom-cropped frames from the video for OCR speaker detection.

        Args:
            video_path: Path to video file (uses downloaded if None)
            start_sec: Start time in seconds
            end_sec: End time in seconds (None = to end)
            fps: Frames per second to extract (default 0.5 = 1 frame every 2 sec)
            crop_bottom_pct: Fraction of frame height to keep from bottom (0.20 = bottom 20%)
            item_label: Label for output directory
        """
        video_path = video_path or self._downloaded_video
        if not video_path:
            raise ValueError("No video path or URL provided")
        is_url = str(video_path).startswith("http")
        if not is_url and not Path(video_path).exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        # Create output directory for frames
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in item_label)
        frames_dir = self.temp_dir / f"frames_{safe_label}"
        frames_dir.mkdir(parents=True, exist_ok=True)

        # Get video dimensions first
        width, height = self._get_video_dimensions(video_path)
        crop_h = int(height * crop_bottom_pct)
        crop_y = height - crop_h

        # Build ffmpeg command
        cmd = ["ffmpeg", "-y", "-nostdin"]
        
        if is_url:
            header_str = f"User-Agent: {HEADERS['User-Agent']}\r\nReferer: {HEADERS['Referer']}\r\n"
            cmd.extend(["-headers", header_str])

        # Time range
        if start_sec > 0:
            cmd.extend(["-ss", str(start_sec)])
        cmd.extend(["-i", str(video_path)])
        if end_sec is not None:
            duration = end_sec - start_sec
            cmd.extend(["-t", str(duration)])

        # Crop to bottom portion + set frame rate
        crop_filter = f"crop={width}:{crop_h}:0:{crop_y},fps={fps}"
        cmd.extend([
            "-vf", crop_filter,
            "-q:v", "2",  # High quality JPEG
            str(frames_dir / "frame_%06d.jpg"),
        ])

        logger.info(f"Extracting frames: {start_sec:.0f}s-{end_sec:.0f}s @ {fps}fps, "
                     f"crop bottom {crop_bottom_pct*100:.0f}%")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"ffmpeg error: {result.stderr[-500:]}")
            raise RuntimeError(f"ffmpeg frame extraction failed: {result.stderr[-200:]}")

        # Collect extracted frames with timestamps
        frames = []
        frame_files = sorted(frames_dir.glob("frame_*.jpg"))
        for idx, fpath in enumerate(frame_files):
            timestamp = start_sec + (idx / fps)
            frames.append(FrameInfo(
                path=str(fpath),
                timestamp_seconds=timestamp,
                agenda_item_title=item_label,
            ))

        logger.info(f"Extracted {len(frames)} frames for '{item_label}'")
        return frames

    def extract_audio(
        self,
        video_path: Optional[Path] = None,
        start_sec: float = 0,
        end_sec: Optional[float] = None,
        item_label: str = "segment",
    ) -> Path:
        """
        Extract audio from a video segment as WAV for Whisper transcription.

        Returns the path to the WAV file.
        """
        video_path = video_path or self._downloaded_video
        if not video_path:
            raise ValueError("No video path or URL provided")
        is_url = str(video_path).startswith("http")
        if not is_url and not Path(video_path).exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in item_label)
        audio_path = self.temp_dir / f"audio_{safe_label}.wav"

        cmd = ["ffmpeg", "-y", "-nostdin"]
        if start_sec > 0:
            cmd.extend(["-ss", str(start_sec)])
        cmd.extend(["-i", str(video_path)])
        if end_sec is not None:
            duration = end_sec - start_sec
            cmd.extend(["-t", str(duration)])

        cmd.extend([
            "-vn",              # No video
            "-acodec", "pcm_s16le",
            "-ar", "16000",     # 16kHz for Whisper
            "-ac", "1",         # Mono
            str(audio_path),
        ])

        logger.info(f"Extracting audio: {start_sec:.0f}s-{end_sec or 'end'}s")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg audio extraction failed: {result.stderr[-200:]}")

        logger.info(f"Audio saved: {audio_path} ({audio_path.stat().st_size / 1024:.0f} KB)")
        return audio_path

    def extract_video_clips(
        self,
        agenda_items: List[Dict],
        output_dir: Path,
        video_path: Optional[Path] = None,
    ) -> List[Path]:
        """
        Extract physical video clips for each agenda item using ffmpeg.
        Uses stream copying for speed.
        """
        video_path = video_path or self._downloaded_video
        if not video_path:
            raise ValueError("No video path or URL provided")
        
        output_dir.mkdir(parents=True, exist_ok=True)
        clip_paths = []

        logger.info(f"🎬 Splitting video into {len(agenda_items)} clips...")
        
        # Helper to convert "0:12:34" to seconds if needed
        def to_s(t):
            if isinstance(t, (int, float)): return t
            if not t: return 0
            parts = list(map(int, t.split(':')))
            if len(parts) == 3: return parts[0]*3600 + parts[1]*60 + parts[2]
            if len(parts) == 2: return parts[0]*60 + parts[1]
            return 0

        for item in agenda_items:
            title = item.get("title", "Unknown")
            start = to_s(item.get("start_time", 0))
            
            # Find end time (next item's start or end of video)
            # This is a bit tricky if segments aren't continuous, 
            # but usually we can find the max timestamp of segments.
            segments = item.get("segments", [])
            if not segments: continue
            
            end = max(s.get("end_time_seconds", start + 60) for s in segments)
            duration = end - start
            if duration <= 0: duration = 60 # Fallback

            safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)
            clip_name = f"agenda_{item.get('id', safe_title)}.mp4"
            clip_path = output_dir / clip_name

            cmd = [
                "ffmpeg", "-y", "-nostdin",
                "-ss", str(start),
                "-t", str(duration),
                "-i", str(video_path),
                "-c", "copy",      # Use stream copy (instant)
                "-avoid_negative_ts", "make_zero",
                str(clip_path)
            ]

            logger.info(f"  Cutting clip: {title} ({start:.0f}s -> {end:.0f}s)")
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                clip_paths.append(clip_path)
            else:
                logger.warning(f"  Failed to cut clip '{title}': {result.stderr[-100:]}")

        return clip_paths

    def download_mp3(self, mp3_url: str, filename: str = "meeting.mp3") -> Path:
        """Download the MP3 audio directly (faster than extracting from MP4)."""
        output_path = self.temp_dir / filename

        if output_path.exists():
            logger.info(f"MP3 already downloaded: {output_path}")
            return output_path

        logger.info(f"Downloading MP3: {mp3_url}")
        resp = requests.get(mp3_url, headers=HEADERS, stream=True, timeout=300)
        resp.raise_for_status()

        total = int(resp.headers.get("content-length", 0))
        with open(output_path, "wb") as f:
            with tqdm(total=total, unit="B", unit_scale=True, desc="Downloading MP3") as pbar:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    pbar.update(len(chunk))

        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info(f"Downloaded MP3: {size_mb:.1f} MB")
        return output_path

    def download_hls_video(self, hls_url: str, filename: str = "meeting_hls.mp4") -> Path:
        """
        Download full video from an HLS stream using ffmpeg.
        Required for OCR when MP4 is unavailable.
        """
        output_path = self.temp_dir / filename
        if output_path.exists():
            return output_path

        logger.info(f"Downloading FULL VIDEO from HLS stream: {hls_url}")
        
        header_str = f"User-Agent: {HEADERS['User-Agent']}\r\nReferer: {HEADERS['Referer']}\r\n"
        
        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-headers", header_str,
            "-i", hls_url,
            "-c", "copy",       # Stream copy instead of re-encoding
            "-bsf:a", "aac_adtstoasc", # Fix AAC headers for mp4 container
            str(output_path)
        ]

        log_path = self.temp_dir / f"{filename}.log"
        with open(log_path, "w") as log_file:
            result = subprocess.run(cmd, stdout=log_file, stderr=subprocess.STDOUT, text=True)

        if result.returncode != 0:
            with open(log_path, "r") as log_file:
                error_log = log_file.read()
            logger.error(f"HLS video download failed (URL: {hls_url}):\n{error_log[-1000:]}")
            raise RuntimeError(f"HLS video download failed: {error_log[-200:]}")

        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info(f"Downloaded HLS video: {size_mb:.1f} MB")
        self._downloaded_video = output_path
        return output_path

    def download_hls_audio(self, hls_url: str, filename: str = "meeting_hls.wav") -> Path:
        """
        Download audio from an HLS stream using ffmpeg.
        Useful when direct MP3 download is forbidden but HLS is accessible.
        """
        output_path = self.temp_dir / filename
        if output_path.exists():
            return output_path

        logger.info(f"Downloading audio from HLS stream: {hls_url}")
        
        # Build headers for ffmpeg
        header_str = f"User-Agent: {HEADERS['User-Agent']}\r\nReferer: {HEADERS['Referer']}\r\n"
        
        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-headers", header_str,
            "-i", hls_url,
            "-vn",              # No video
            "-acodec", "pcm_s16le",
            "-ar", "16000",     # 16kHz for Whisper
            "-ac", "1",         # Mono
            str(output_path)
        ]

        log_path = self.temp_dir / f"{filename}.log"
        with open(log_path, "w") as log_file:
            result = subprocess.run(cmd, stdout=log_file, stderr=subprocess.STDOUT, text=True)

        if result.returncode != 0:
            with open(log_path, "r") as log_file:
                error_log = log_file.read()
            logger.error(f"HLS download failed (URL: {hls_url}):\n{error_log[-1000:]}")
            raise RuntimeError(f"HLS audio download failed: {error_log[-200:]}")

        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info(f"Downloaded HLS audio: {size_mb:.1f} MB")
        return output_path

    # ── Cleanup ───────────────────────────────────────────────────────

    def cleanup(self):
        """Remove all temporary files."""
        if self.temp_dir.exists():
            size = sum(f.stat().st_size for f in self.temp_dir.rglob("*") if f.is_file())
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            logger.info(f"Cleaned up {size / (1024*1024):.1f} MB from {self.temp_dir}")

    def delete_video(self):
        """Delete just the downloaded video file (keep frames/audio)."""
        if self._downloaded_video and self._downloaded_video.exists():
            size = self._downloaded_video.stat().st_size / (1024 * 1024)
            self._downloaded_video.unlink()
            logger.info(f"Deleted video file ({size:.1f} MB)")
            self._downloaded_video = None

    # ── Private Helpers ───────────────────────────────────────────────

    def _get_video_dimensions(self, video_path: Path) -> Tuple[int, int]:
        """Get video width and height using ffprobe."""
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0:s=x",
            str(video_path),
        ]
        if str(video_path).startswith("http"):
            # Add headers for http streams
            cmd.insert(1, "-headers")
            cmd.insert(2, f"User-Agent: {HEADERS['User-Agent']}\r\nReferer: {HEADERS['Referer']}\r\n")
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # Default fallback dimensions
            logger.warning("Could not determine video dimensions, using 1920x1080")
            return 1920, 1080

        dims = result.stdout.strip().split("x")
        return int(dims[0]), int(dims[1])


# ── CLI for standalone testing ────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.media_processor <mp4_url> [start_sec] [end_sec]")
        sys.exit(1)

    mp4_url = sys.argv[1]
    start = float(sys.argv[2]) if len(sys.argv) > 2 else 0
    end = float(sys.argv[3]) if len(sys.argv) > 3 else 60  # First minute by default

    processor = MediaProcessor()
    try:
        video = processor.download_video(mp4_url)
        frames = processor.extract_ocr_frames(video, start_sec=start, end_sec=end, item_label="test")
        print(f"\nExtracted {len(frames)} frames")
        for f in frames[:3]:
            print(f"  {f.timestamp_seconds:.1f}s → {f.path}")
    finally:
        processor.cleanup()
