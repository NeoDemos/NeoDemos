import httpx
from typing import List, Dict, Any, Optional
from datetime import datetime

class OpenRaadService:
    BASE_URL = "https://api.openraadsinformatie.nl/v1/elastic"
    
    def __init__(self):
        self._index = None
        self._last_index_check = None

    async def ensure_index(self):
        """Fetch the latest Rotterdam index if not already cached/stale."""
        now = datetime.now()
        if self._index and self._last_index_check and (now - self._last_index_check).days < 1:
            return self._index

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.BASE_URL}/_cat/indices?format=json")
                resp.raise_for_status()
                indices = resp.json()
                
                # Filter for Rotterdam-related indices
                rotterdam_indices = [
                    idx["index"] for idx in indices 
                    if "rotterdam" in idx["index"].lower() and idx["index"].startswith("ori_")
                ]
                
                if not rotterdam_indices:
                    # Fallback to a known good index if discovery fails
                    self._index = "ori_rotterdam_20250629013104"
                else:
                    # Use the lexicographically latest index (usually the most recent)
                    self._index = sorted(rotterdam_indices)[-1]
                
                self._last_index_check = now
                print(f"Using OpenRaad index: {self._index}")
                return self._index
        except Exception as e:
            print(f"Error discovering index: {e}")
            self._index = "ori_rotterdam_20250629013104"
            return self._index

    async def get_meetings(self, start_date: str = "2026-01-01", end_date: str = "2026-12-31") -> List[Dict[str, Any]]:
        index = await self.ensure_index()
        query = {
            "query": {
                "bool": {
                    "must": [
                        { "term": { "_index": index } },
                        { "term": { "@type": "Meeting" } },
                        { "range": { "start_date": { "gte": f"{start_date}T00:00:00Z", "lte": f"{end_date}T23:59:59Z" } } }
                    ]
                }
            },
            "size": 500,
            "sort": [{"start_date": "asc"}]
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(f"{self.BASE_URL}/_search", json=query)
                response.raise_for_status()
                data = response.json()
                
                meetings = []
                for hit in data.get("hits", {}).get("hits", []):
                    source = hit.get("_source", {})
                    meetings.append({
                        "id": hit.get("_id"),
                        "name": source.get("name"),
                        "start_date": source.get("start_date"),
                        "committee": source.get("committee"),
                        "location": source.get("location")
                    })
                return meetings
        except Exception as e:
            print(f"Error in get_meetings: {e}")
            return []

    async def get_meeting_details(self, meeting_id: str) -> Dict[str, Any]:
        """Fetch agenda items and documents for a specific meeting."""
        index = await self.ensure_index()
        # Query for the meeting itself
        query = {
            "query": {
                "bool": {
                    "must": [
                        { "term": { "_index": index } },
                        { "term": { "_id": meeting_id } }
                    ]
                }
            }
        }
        
        # Query for AgendaItems associated with this meeting
        agenda_query = {
            "query": {
                "bool": {
                    "must": [
                        { "term": { "_index": index } },
                        { "term": { "@type": "AgendaItem" } },
                        { "term": { "parent": meeting_id } }
                    ]
                }
            },
            "size": 100,
            "sort": [{"position": "asc"}]
        }

        try:
            async with httpx.AsyncClient() as client:
                # Get meeting basic info
                resp = await client.post(f"{self.BASE_URL}/_search", json=query)
                resp.raise_for_status()
                meeting_hit = resp.json().get("hits", {}).get("hits", [])
                if not meeting_hit:
                    return {}
                meeting = meeting_hit[0].get("_source", {})
                meeting['id'] = meeting_id

                # Get agenda items
                resp = await client.post(f"{self.BASE_URL}/_search", json=agenda_query)
                resp.raise_for_status()
                agenda_hits = resp.json().get("hits", {}).get("hits", [])
                
                meeting['agenda'] = []
                for hit in agenda_hits:
                    source = hit.get("_source", {})
                    item = {
                        "id": hit.get("_id"),
                        "number": source.get("position"), # Using position as number
                        "name": source.get("name") or source.get("title"),
                        "documents": []
                    }
                    
                    # Attachments are IDs. Can be either a string or a list
                    attachment_raw = source.get("attachment", [])
                    # Normalize to list: convert string to list, keep list as-is
                    if isinstance(attachment_raw, str):
                        attachment_ids = [attachment_raw]
                    elif isinstance(attachment_raw, list):
                        attachment_ids = attachment_raw
                    else:
                        attachment_ids = []
                    
                    if attachment_ids:
                        doc_query = {
                            "query": {
                                "bool": {
                                    "must": [
                                        { "term": { "_index": index } },
                                        { "terms": { "_id": attachment_ids } }
                                    ]
                                }
                            }
                        }
                        doc_resp = await client.post(f"{self.BASE_URL}/_search", json=doc_query)
                        if doc_resp.status_code == 200:
                            doc_hits = doc_resp.json().get("hits", {}).get("hits", [])
                            for doc_hit in doc_hits:
                                doc_source = doc_hit.get("_source", {})
                                item['documents'].append({
                                    "id": doc_hit.get("_id"),
                                    "name": doc_source.get("name") or doc_source.get("title") or "Unnamed Document",
                                    "url": doc_source.get("original_url") or doc_source.get("url")
                                })
                    
                    meeting['agenda'].append(item)
                
                return meeting
        except Exception as e:
            print(f"Error in get_meeting_details for {meeting_id}: {e}")
            return {}
    
    async def get_meetings_by_date(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """Get meetings between two datetime objects"""
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        return await self.get_meetings(start_date=start_str, end_date=end_str)
    
    async def get_documents_by_type(self, doc_type: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """
        Search for documents by type (e.g., 'notulen' for meeting minutes)
        Returns a list of documents matching the criteria
        """
        index = await self.ensure_index()
        query = {
            "query": {
                "bool": {
                    "must": [
                        { "term": { "_index": index } },
                        { "match": { "document_type": doc_type } },
                        { "range": { "date": { "gte": f"{start_date}T00:00:00Z", "lte": f"{end_date}T23:59:59Z" } } }
                    ]
                }
            },
            "size": 200,
            "sort": [{"date": "asc"}]
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(f"{self.BASE_URL}/_search", json=query)
                response.raise_for_status()
                data = response.json()
                
                documents = []
                for hit in data.get("hits", {}).get("hits", []):
                    source = hit.get("_source", {})
                    documents.append({
                        "id": hit.get("_id"),
                        "name": source.get("name") or source.get("title"),
                        "url": source.get("original_url") or source.get("url"),
                        "document_type": source.get("document_type"),
                        "date": source.get("date")
                    })
                return documents
        except Exception as e:
            print(f"Error in get_documents_by_type: {e}")
            return []
    async def get_document_by_identifier(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Search for a specific document by its municipal identifier (e.g. BB-number)"""
        index = await self.ensure_index()
        query = {
            "query": {
                "bool": {
                    "must": [
                        { "term": { "_index": index } },
                        { "match": { "identifier": identifier } }
                    ]
                }
            },
            "size": 1
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(f"{self.BASE_URL}/_search", json=query)
                response.raise_for_status()
                data = response.json()
                
                hits = data.get("hits", {}).get("hits", [])
                if not hits:
                    return None
                
                source = hits[0].get("_source", {})
                return {
                    "id": hits[0].get("_id"),
                    "name": source.get("name") or source.get("title"),
                    "url": source.get("original_url") or source.get("url"),
                    "document_type": source.get("document_type"),
                    "date": source.get("date")
                }
        except Exception as e:
            print(f"Error in get_document_by_identifier for {identifier}: {e}")
            return None
