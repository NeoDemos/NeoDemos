import os
import requests
import psycopg2
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DB_URL") or "postgresql://postgres:postgres@localhost:5432/neodemos"

def fetch_and_ingest(ori_id):
    """Fetch a single MediaObject from ORI and ingest into Postgres."""
    u = f"https://api.openraadsinformatie.nl/v1/elastic/ori_rotterdam_20250629013104/_search"
    query = {"query": {"term": {"_id": ori_id}}}
    try:
        resp = requests.post(u, json=query)
        hits = resp.json().get('hits', {}).get('hits', [])
        if not hits:
            # print(f"  [MISS] {ori_id} not found in ORI.")
            return None
        
        hit = hits[0]['_source']
        name = hit.get('name', 'Untitled ORI Doc')
        url = hit.get('original_url', hit.get('url', ''))
        # Concatenate all page contents for the 'Yolk'
        content = ""
        for page in hit.get('text_pages', []):
            content += page.get('text', '') + "\n\n"
        
        # Ingest into Postgres
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        
        # UPSERT into documents
        cur.execute("""
            INSERT INTO documents (id, name, url, content)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                content = EXCLUDED.content,
                url = EXCLUDED.url,
                name = EXCLUDED.name
            RETURNING id
        """, (ori_id, name, url, content))
        
        doc_id = cur.fetchone()[0]
        
        # 2. Add to chunking_queue
        cur.execute("""
            INSERT INTO chunking_queue (document_id, status)
            VALUES (%s, 'pending')
            ON CONFLICT (document_id) DO UPDATE SET status = 'pending'
        """, (doc_id,))
        
        conn.commit()
        cur.close()
        conn.close()
        return doc_id
    except Exception as e:
        print(f"  [ERROR] {ori_id}: {e}")
        return None

if __name__ == "__main__":
    targets = [
        "6126099", # Bestuursopdracht Zorg Primary Letter
        "6110375", # Adviesmodel Cultuur 
        "6106672", # Re-ingesting this shell
        "6105526", # Re-ingesting this shell
        "6105489", # Adviesmodel Cultuur Kwartiermaker
        "6105490", # Adviesmodel Cultuur Openbaarheid
        "6105491", # Tijdslijnen Cultuurplan
        "6102057", # Trevvel Problems Contract
        "6102060", # Tel je zegeningen Research
        "6102883", # Begroting Bijlage 2
        "6102939", # Begroting Bijlage (Duplicate check)
    ]
    
    print(f"--- SURGICAL ORI RECOVERY (11 ZWCS Documents) ---")
    success_count = 0
    for tid in tqdm(targets, desc="Fetching"):
        did = fetch_and_ingest(tid)
        if did:
            success_count += 1
    
    print(f"\n✅ SUCCESS: Ingested {success_count} ZWCS primary documents.")
