import os
import requests
import psycopg2
from dotenv import load_dotenv

load_dotenv()

# We need the ingestion services to process the content
# Since we want to use the existing RAG/Storage logic, we'll try to import them
# If not possible, we'll implement a simple PDF-to-Text-to-DB logic for this repair.

def ingest_document_by_ori_id(ori_id):
    """Surgically fetches a document from ORI and pushes it into NeoDemos."""
    print(f"--- ATTEMPTING REPAIR FOR ORI ID: {ori_id} ---")
    search_url = f"https://api.openraadsinformatie.nl/v1/elastic/_search"
    query = {"query": {"bool": {"must": [{"match": {"_id": ori_id}}]}}}
    
    try:
        resp = requests.post(search_url, json=query)
        if resp.status_code != 200:
            print(f"ORI lookup failed: {resp.status_code}")
            return
        
        data = resp.json()
        hits = data.get('hits', {}).get('hits', [])
        if not hits:
            print(f"No hits in ORI for {ori_id}")
            return
            
        source = hits[0]['_source']
        pdf_url = source.get('url')
        name = source.get('name', 'Missing Document Name')
        text_content = source.get('text', '') # ORI sometimes has the OCR'd text already
        
        if not text_content and pdf_url:
            print(f"No text in ORI metadata for {ori_id}. Would need to fetch and OCR PDF: {pdf_url}")
            # For this repair, we'll use ORI's 'text' if available. 
            # If not, we'll mark it for deep OCR.
            return

        print(f"Found content in ORI ({len(text_content)} chars). Injecting into local DB...")
        
        db_name = os.getenv("DB_NAME", "neodemos")
        conn = psycopg2.connect(dbname=db_name, user=os.getenv("DB_USER"), host=os.getenv("DB_HOST"))
        cur = conn.cursor()
        
        # Check if we already have it
        cur.execute("SELECT id FROM documents WHERE id = %s", (ori_id,))
        if cur.fetchone():
            print(f"Document {ori_id} already exists. Updating content...")
            cur.execute("UPDATE documents SET content = %s, name = %s WHERE id = %s", (text_content, name, ori_id))
        else:
            # We need a meeting_id. We'll search for the parent or use a 'legacy' bucket.
            parent_id = source.get('parent', 'legacy_recovery')
            cur.execute("INSERT INTO documents (id, name, content, meeting_id, url) VALUES (%s, %s, %s, %s, %s)", 
                        (ori_id, name, text_content, parent_id, pdf_url))
        
        conn.commit()
        print(f"Successfully repaired document {ori_id}")
        cur.close()
        conn.close()
        
    except Exception as e:
        print(f"Failed to ingest {ori_id}: {e}")

if __name__ == "__main__":
    # The specific documents identifying the gap
    target_ids = ['6126099', '6119188', '6119187'] 
    for tid in target_ids:
        ingest_document_by_ori_id(tid)
