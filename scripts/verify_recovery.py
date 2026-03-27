import os
import sys
import psycopg2
import logging
import json

# Ensure we can import from the root directory
sys.path.append(os.getcwd())

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_recovery")

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"

def verify_tagging(category="committee_transcript_test"):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    logger.info(f"Verifying data for category: {category}")
    
    # Check meetings
    cur.execute("SELECT id, name, start_date FROM meetings WHERE category = %s", (category,))
    meetings = cur.fetchall()
    logger.info(f"Found {len(meetings)} meetings tagged with {category}")
    for m in meetings:
        logger.info(f"  - Meeting: {m[1]} (ID: {m[0]}, Date: {m[2]})")
        
    # Check documents
    cur.execute("SELECT id, name, meeting_id FROM documents WHERE category = %s", (category,))
    docs = cur.fetchall()
    logger.info(f"Found {len(docs)} documents tagged with {category}")
    for d in docs:
        logger.info(f"  - Document: {d[1]} (ID: {d[0]}, Meeting ID: {d[2]})")
        
    conn.close()

if __name__ == "__main__":
    verify_tagging()
