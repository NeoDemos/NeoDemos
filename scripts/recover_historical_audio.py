import os
import json
import sqlite3
import psycopg2
import subprocess
from datetime import datetime

# DB Config
DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
AUDIO_DIR = "data/audio_recovery"
CHECKPOINT_DB = "data/audio_recovery/recovery_checkpoint.sqlite"

def init_recovery():
    if not os.path.exists(AUDIO_DIR):
        os.makedirs(AUDIO_DIR)
        
    conn = sqlite3.connect(CHECKPOINT_DB)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS recovery_queue (
            meeting_id TEXT PRIMARY KEY,
            start_date TEXT,
            status TEXT DEFAULT 'pending',
            retries INTEGER DEFAULT 0,
            last_error TEXT
        )
    """)
    conn.commit()
    return conn

def populate_queue():
    pg_conn = psycopg2.connect(DB_URL)
    pg_cur = pg_conn.cursor()
    
    # Strictly backward from 2026 to 2018
    print("🔍 Scanning 2018-2026 for gaps...")
    pg_cur.execute("""
        SELECT id, start_date 
        FROM meetings 
        WHERE (transcript IS NULL OR LENGTH(transcript) < 100)
        AND start_date >= '2018-01-01' AND start_date <= '2026-12-31'
        ORDER BY start_date DESC
    """)
    rows = pg_cur.fetchall()
    
    sqlite_conn = sqlite3.connect(CHECKPOINT_DB)
    sl_cur = sqlite_conn.cursor()
    
    for row in rows:
        sl_cur.execute("INSERT OR IGNORE INTO recovery_queue (meeting_id, start_date) VALUES (?, ?)", (row[0], str(row[1])))
    
    sqlite_conn.commit()
    print(f"✅ Recovery queue populated with {len(rows)} meetings.")
    pg_conn.close()
    sqlite_conn.close()

def download_audio(meeting_id):
    # This is a placeholder for the actual HLS URL lookups logic we developed previously
    # It identifies the HLS stream from the iBabs ID and pulls it using ffmpeg
    output_path = os.path.join(AUDIO_DIR, f"{meeting_id}.m4a")
    print(f"🎵 Downloading Audio for {meeting_id}...")
    
    # (Mocking the ffmpeg call for the plan review)
    # real_cmd = ["ffmpeg", "-i", hls_url, "-vn", "-acodec", "copy", output_path]
    return True

if __name__ == "__main__":
    init_recovery()
    populate_queue()
