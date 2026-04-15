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
    async def fetch_docs_by_name_pattern(
        self,
        name_patterns: list[str],
        year: int | None = None,
        from_offset: int = 0,
        page_size: int = 500,
    ) -> tuple[list[Dict[str, Any]], int]:
        """Paginated fetch of MediaObject docs matching one or more name patterns.

        Used by WS11b to ingest schriftelijke vragen, initiatiefnotities, etc.

        Returns (docs, total_hits) — caller paginates by incrementing from_offset
        by page_size until len(docs) < page_size or from_offset >= total_hits.
        """
        index = await self.ensure_index()

        should_clauses = [{"match": {"name": p}} for p in name_patterns]
        must_clauses: list[dict] = [{"term": {"@type": "MediaObject"}}]
        if year:
            must_clauses.append({
                "range": {
                    "last_discussed_at": {
                        "gte": f"{year}-01-01",
                        "lte": f"{year}-12-31",
                    }
                }
            })

        query = {
            "size": page_size,
            "from": from_offset,
            "query": {
                "bool": {
                    "must": must_clauses,
                    "should": should_clauses,
                    "minimum_should_match": 1,
                }
            },
            "_source": [
                "@id", "name", "url", "original_url",
                "last_discussed_at", "text", "content_type",
                "size_in_bytes", "was_generated_by",
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self.BASE_URL}/{index}/_search", json=query
                )
                resp.raise_for_status()
                data = resp.json()
                total = data.get("hits", {}).get("total", {})
                total_hits = total.get("value", 0) if isinstance(total, dict) else int(total)
                docs = []
                for hit in data.get("hits", {}).get("hits", []):
                    src = hit.get("_source", {})
                    text_parts = src.get("text", [])
                    if isinstance(text_parts, list):
                        full_text = "\n\n".join(t for t in text_parts if t)
                    else:
                        full_text = text_parts or ""
                    docs.append({
                        "ori_id": src.get("@id") or hit.get("_id"),
                        "name": src.get("name") or "",
                        "url": src.get("original_url") or src.get("url") or "",
                        "last_discussed_at": src.get("last_discussed_at"),
                        "text": full_text,
                        "content_type": src.get("content_type") or "",
                        "was_generated_by": src.get("was_generated_by"),
                    })
                return docs, total_hits
        except Exception as e:
            print(f"Error in fetch_docs_by_name_pattern (year={year}, offset={from_offset}): {e}")
            return [], 0

    async def _find_mediaobject_for_report(
        self,
        client: httpx.AsyncClient,
        index: str,
        report_name: str,
    ) -> Optional[Dict[str, Any]]:
        """Look up the MediaObject paired to an ORI Report by name.

        Uses the same exact-match logic as ws4_backfill: strip the
        "[NNbbNNNNNN] " prefix from MediaObject names, then require
        exactly one match to avoid ambiguous merges.
        """
        query = {
            "size": 3,
            "query": {
                "bool": {
                    "must": [
                        {"match_phrase": {"name": report_name}},
                        {"term": {"@type": "MediaObject"}},
                    ]
                }
            },
            "_source": ["@id", "name", "url", "original_url", "last_discussed_at", "text"],
        }
        try:
            resp = await client.post(f"{self.BASE_URL}/{index}/_search", json=query, timeout=30.0)
            resp.raise_for_status()
        except Exception:
            return None

        hits = resp.json().get("hits", {}).get("hits", [])
        if not hits:
            return None

        # Prefer an exact tail-match (strip "[NNbbNNNNNN] " prefix)
        exact = []
        for h in hits:
            src = h.get("_source", {})
            nm = (src.get("name") or "").strip()
            if nm.startswith("[") and "]" in nm:
                tail = nm.split("]", 1)[1].lstrip()
            else:
                tail = nm
            if tail == report_name:
                exact.append(src)

        if len(exact) == 1:
            return exact[0]
        if len(hits) == 1:
            return hits[0].get("_source") or {}
        return None

    async def fetch_docs_by_classification(
        self,
        classification_value: str,
        from_offset: int = 0,
        page_size: int = 500,
    ) -> tuple[list[Dict[str, Any]], int]:
        """Paginated fetch of Report-type docs by ORI classification field.

        E.g. classification_value='Raadsvragen' fetches formally classified
        schriftelijke vragen reports.  Complements fetch_docs_by_name_pattern.

        For each Report that has no text, we also look up the paired
        MediaObject (by name match) to capture content + URL — preventing
        the stub-no-URL pattern that previously blocked retrieval.

        Returns (docs, total_hits).
        """
        index = await self.ensure_index()

        query = {
            "size": page_size,
            "from": from_offset,
            "query": {"term": {"classification.keyword": classification_value}},
            "sort": [{"start_date": {"order": "asc"}}],
            "_source": [
                "@id", "name", "classification", "start_date",
                "description", "attachment", "has_organization_name",
                "was_generated_by",
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self.BASE_URL}/{index}/_search", json=query
                )
                resp.raise_for_status()
                data = resp.json()
                total = data.get("hits", {}).get("total", {})
                total_hits = total.get("value", 0) if isinstance(total, dict) else int(total)
                docs = []
                for hit in data.get("hits", {}).get("hits", []):
                    src = hit.get("_source", {})
                    name = src.get("name") or ""
                    text = src.get("description") or ""
                    url = ""

                    # Report records carry no text/url — look up the paired MediaObject
                    if not text and name:
                        media = await self._find_mediaobject_for_report(client, index, name)
                        if media:
                            text_parts = media.get("text", [])
                            if isinstance(text_parts, list):
                                text = "\n\n".join(t for t in text_parts if t)
                            else:
                                text = text_parts or ""
                            url = media.get("original_url") or media.get("url") or ""

                    docs.append({
                        "ori_id": src.get("@id") or hit.get("_id"),
                        "name": name,
                        "url": url,
                        "last_discussed_at": src.get("start_date"),
                        "text": text,
                        "content_type": "application/pdf",
                        "was_generated_by": src.get("was_generated_by"),
                        "attachment_ids": src.get("attachment") or [],
                        "classification": src.get("classification"),
                    })
                return docs, total_hits
        except Exception as e:
            print(f"Error in fetch_docs_by_classification ({classification_value}, offset={from_offset}): {e}")
            return [], 0

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
