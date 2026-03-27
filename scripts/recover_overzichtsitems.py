import asyncio
import os
import sys
import logging
import httpx
import psycopg2
from pypdf import PdfReader
import io

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.ibabs_service import IBabsService
from pipeline.ingestion import SmartIngestor

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"

async def recover_missing_docs():
    ibabs = IBabsService()
    ingestor = SmartIngestor(db_url=DB_URL)
    
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    # 1. Find all meetings in 2024, 2025 and 2026
    log("Fetching meetings for 2024, 2025 and 2026...")
    cur.execute("SELECT id, name, start_date FROM meetings WHERE start_date >= '2024-01-01' ORDER BY start_date DESC")
    meetings = cur.fetchall()
    log(f"Found {len(meetings)} meetings to check.")
    
    for m_id, m_name, m_date in meetings:
        log(f"\nChecking meeting: {m_name} ({m_date}) [ID: {m_id}]")
        
        try:
            # 2. Get agenda with resolved references
            agenda_data = await ibabs.get_meeting_agenda(m_id, resolve_references=True)
            
            for item in agenda_data.get("agenda", []):
                for doc in item.get("documents", []):
                    if doc.get("type") == "overzichtsitem" and doc.get("resolved"):
                        bb_number = doc['id']
                        doc_url = doc['url']
                        doc_name = doc['name']
                        
                        # 3. Check if already ingested
                        cur.execute("SELECT id FROM documents WHERE id = %s", (bb_number,))
                        if cur.fetchone():
                            log(f"  - Document {bb_number} already exists. Skipping.")
                            continue
                            
                        # 4. Download and Ingest
                        log(f"  + Recovering Missing Doc: {doc_name} ({bb_number})")
                        content = await download_and_extract(doc_url)
                        
                        if content:
                            ingestor.ingest_document(
                                doc_id=bb_number,
                                doc_name=doc_name,
                                content=content,
                                meeting_id=m_id,
                                metadata={"type": "recovery", "bb_number": bb_number}
                            )
                            log(f"    ✓ Ingested {len(content)} chars.")
                        else:
                            log(f"    ✗ Failed to extract content for {bb_number}")
                            
        except Exception as e:
            logger.error(f"Error processing meeting {m_id}: {e}")
            
    cur.close()
    conn.close()

async def download_and_extract(url: str) -> str:
    """Download PDF and use pypdf for extraction"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.get(url, headers=headers, follow_redirects=True)
            response.raise_for_status()
            
            # Simple pypdf extraction
            reader = PdfReader(io.BytesIO(response.content))
            text = ""
            for page in reader.pages:
                text += (page.extract_text() or "") + "\n"
            
            return text.strip()
        except Exception as e:
            logger.error(f"Download/Extract error: {e}")
            return ""

def log(msg: str):
    print(msg, flush=True)

if __name__ == "__main__":
    asyncio.run(recover_missing_docs())
