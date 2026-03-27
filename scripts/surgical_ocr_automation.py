import os
import json
import sqlite3
import psycopg2
import subprocess

# DB Config
DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
IMAGE_DIR = "data/audio_recovery/ocr_snapshots"

def detect_speaker_changes(meeting_id, transcript_json):
    """
    Analyzes the Whisper JSON (with timestamps) to find all speaker segment starts.
    """
    segments = transcript_json.get('segments', [])
    change_markers = []
    
    last_speaker = None
    for seg in segments:
        speaker = seg.get('speaker', 'Unknown')
        if speaker != last_speaker:
            change_markers.append({
                'timestamp': seg['start'],
                'speaker_id': speaker
            })
            last_speaker = speaker
            
    return change_markers

def extract_surgical_frames(meeting_id, markers):
    """
    Pulls a frame for each speaker change to feed the OCR.
    """
    for marker in markers:
        ts = marker['timestamp']
        output = os.path.join(IMAGE_DIR, f"{meeting_id}_{int(ts)}.jpg")
        
        # Pull 1 frame exactly at the change
        subprocess.run([
            "ffmpeg", "-y", "-ss", str(ts + 1.0), # +1s to avoid transition flicker
            "-i", "hls_url_placeholder", 
            "-frames:v", "1", "-q:v", "2", output
        ], capture_output=True)
        
    print(f"✅ Extracted {len(markers)} surgical frames for {meeting_id}.")

if __name__ == "__main__":
    # This logic will be integrated into the main recovery worker
    pass
