import os, sys, sqlite3, psycopg2, subprocess
DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
AUDIO_DIR = "data/audio_recovery"
CHECKPOINT_DB = "data/audio_recovery/recovery_checkpoint.sqlite"

def get_hls_url(meeting_id):
    pg_conn = psycopg2.connect(DB_URL)
    pg_cur = pg_conn.cursor()
    pg_cur.execute("SELECT video_url FROM meetings WHERE id = %s", (meeting_id,))
    res = pg_cur.fetchone()
    pg_conn.close()
    return res[0] if res and res[0] else None

def process_queue():
    sl_conn = sqlite3.connect(CHECKPOINT_DB)
    sl_cur = sl_conn.cursor()
    sl_cur.execute("SELECT meeting_id FROM recovery_queue WHERE status = 'pending' ORDER BY start_date DESC")
    rows = sl_cur.fetchall()
    
    print(f"🚀 Starting Mass Extraction for {len(rows)} meetings...")
    for (m_id,) in rows:
        hls = get_hls_url(m_id)
        if not hls or not hls.startswith('http'):
            sl_cur.execute("UPDATE recovery_queue SET status = 'failed', last_error = 'Invalid URL' WHERE meeting_id = ?", (m_id,))
            sl_conn.commit()
            continue
            
        output = os.path.join(AUDIO_DIR, f"{m_id}.m4a")
        if os.path.exists(output):
             sl_cur.execute("UPDATE recovery_queue SET status = 'completed' WHERE meeting_id = ?", (m_id,))
             sl_conn.commit()
             continue

        print(f"🎵 Extracting: {m_id}...")
        try:
            # -vn = no video, -acodec copy = ultra fast (no re-encoding)
            subprocess.run(["ffmpeg", "-y", "-nostdin", "-i", hls, "-vn", "-acodec", "copy", output], check=True, capture_output=True, timeout=1200)
            sl_cur.execute("UPDATE recovery_queue SET status = 'completed' WHERE meeting_id = ?", (m_id,))
        except Exception as e:
            sl_cur.execute("UPDATE recovery_queue SET status = 'failed', last_error = ? WHERE meeting_id = ?", (str(e), m_id))
        sl_conn.commit()

if __name__ == '__main__': process_queue()
