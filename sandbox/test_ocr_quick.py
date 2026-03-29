import logging
from pathlib import Path
from pipeline.media_processor import MediaProcessor
from pipeline.extractor import SpeakerDetector

logging.basicConfig(level=logging.INFO)

mp4_url = "https://sdk.companywebcast.com/43c307a0-59c7-4608-a1b0-c384a19712b9/mp4/bb_nl.mp4"
processor = MediaProcessor()

try:
    # Just download a tiny 60-second slice starting at 10 minutes in
    # Actually wait, `download_video` downloads the whole thing.
    # To test quickly, we can just use extract_ocr_frames which accepts start_sec/end_sec
    # BUT wait, extract_ocr_frames uses ffmpeg on the downloaded file.
    # Let's use the URL directly with ffmpeg!
    frames = processor.extract_ocr_frames(video_path=mp4_url, start_sec=1200, end_sec=1300, fps=0.5, item_label="test_slice")
    
    detector = SpeakerDetector()
    speakers = detector.detect_speakers_from_frames(
        [f.path for f in frames], 
        [f.timestamp_seconds for f in frames]
    )
    
    print("\n--- DETECTED SPEAKERS ---")
    for s in speakers:
        print(f"[{s.timestamp_seconds}s] {s.name} ({s.party}) - Raw: {s.raw_text}")

finally:
    processor.cleanup()
