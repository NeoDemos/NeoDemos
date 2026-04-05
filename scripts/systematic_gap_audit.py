import asyncio
import os
import httpx
import psycopg2
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.openraadsinformatie.nl/v1/elastic"
INDEX = "ori_rotterdam_20250629013104"

async def fetch_ori_zwcs_meetings(start_year: int = 2021, end_year: int = 2024):
    """Fetch all ZWCS meetings from ORI for the given period."""
    print(f"--- Fetching ORI ZWCS Meetings for {start_year}-{end_year} ---")
    
    query = {
        "query": {
            "bool": {
                "must": [
                    { "term": { "@type": "Meeting" } },
                    { "range": { "start_date": { "gte": f"{start_year}-01-01T00:00:00Z", "lte": f"{end_year}-12-31T23:59:59Z" } } },
                    { "match_phrase": { "name": "Commissie Zorg, Welzijn, Cultuur en Sport" } }
                ]
            }
        },
        "size": 1000,
        "sort": [{"start_date": "asc"}]
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{BASE_URL}/{INDEX}/_search", json=query)
        resp.raise_for_status()
        data = resp.json()
        return data.get("hits", {}).get("hits", [])

async def fetch_ori_meeting_details(meeting_id: str):
    """Fetch agenda items and their documents from ORI."""
    agenda_query = {
        "query": {
            "bool": {
                "must": [
                    { "term": { "@type": "AgendaItem" } },
                    { "term": { "parent": meeting_id } }
                ]
            }
        },
        "size": 100
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{BASE_URL}/{INDEX}/_search", json=agenda_query)
        if resp.status_code != 200: return []
        
        agenda_hits = resp.json().get("hits", {}).get("hits", [])
        all_docs = []
        for hit in agenda_hits:
            source = hit.get("_source", {})
            attachments = source.get("attachment", [])
            if isinstance(attachments, str): attachments = [attachments]
            all_docs.extend(attachments)
        
        if not all_docs: return []
        
        doc_query = {
            "query": {
                "bool": {
                    "must": [
                        { "terms": { "_id": all_docs } }
                    ]
                }
            },
            "size": 200
        }
        doc_resp = await client.post(f"{BASE_URL}/{INDEX}/_search", json=doc_query)
        if doc_resp.status_code != 200: return []
        return doc_resp.json().get("hits", {}).get("hits", [])

async def run_local_audit(ori_meetings):
    """Compare ORI data against local Postgres."""
    db_name = os.getenv("DB_NAME", "neodemos")
    conn = psycopg2.connect(dbname=db_name, user=os.getenv("DB_USER"), host=os.getenv("DB_HOST"))
    cur = conn.cursor()
    
    gap_report = {
        "missing_meetings": [],
        "missing_documents": [],
        "content_gaps": []
    }
    
    print(f"Auditing {len(ori_meetings)} meetings...")
    for hit in ori_meetings:
        mid = hit["_id"]
        source = hit["_source"]
        m_name = source.get("name")
        m_date = source.get("start_date")
        
        cur.execute("SELECT id FROM meetings WHERE id = %s OR (name = %s AND start_date = %s)", (mid, m_name, m_date))
        if not cur.fetchone():
            gap_report["missing_meetings"].append({"id": mid, "name": m_name, "date": m_date})
            
        ori_docs = await fetch_ori_meeting_details(mid)
        for d_hit in ori_docs:
            did = d_hit["_id"]
            d_source = d_hit["_source"]
            d_name = d_source.get("name")
            d_url = d_source.get("url") or d_source.get("original_url")
            
            # Use exact ID check first
            cur.execute("SELECT id, LENGTH(content) FROM documents WHERE id = %s", (did,))
            res = cur.fetchone()
            if not res:
                # Fuzzy check by URL suffix/ID in URL just in case
                cur.execute("SELECT id, LENGTH(content) FROM documents WHERE name = %s AND url ILIKE %s", (d_name, f"%{did[-8:]}%"))
                res = cur.fetchone()

            if not res:
                gap_report["missing_documents"].append({"id": did, "name": d_name, "url": d_url, "meeting": m_name})
            elif (res[1] or 0) < 500:
                gap_report["content_gaps"].append({"id": did, "name": d_name, "length": res[1], "url": d_url})
                
    cur.close()
    conn.close()
    return gap_report

async def main():
    zwcs_meetings = await fetch_ori_zwcs_meetings()
    print(f"Found {len(zwcs_meetings)} ZWCS meetings in ORI.")
    
    report = await run_local_audit(zwcs_meetings)
    
    print("\n" + "="*40)
    print("ZWCS SYSTEMATIC GAP AUDIT REPORT")
    print("="*40)
    print(f"Missing Meetings:   {len(report['missing_meetings'])}")
    print(f"Missing Documents:  {len(report['missing_documents'])}")
    print(f"OCR Candidates (<500 chars): {len(report['content_gaps'])}")
    print("="*40)
    
    if report['missing_documents']:
        print("\nSAMPLE MISSING DOCUMENTS:")
        for d in report['missing_documents'][:10]:
            print(f"- {d['name']} (ID: {d['id']})")
            
    if report['content_gaps']:
        print("\nSAMPLE OCR CANDIDATES:")
        for d in report['content_gaps'][:10]:
            print(f"- {d['name']} (ID: {d['id']}, Length: {d['length']})")

if __name__ == "__main__":
    asyncio.run(main())
