# OpenRaadsinformatie Integration Guide for NeoDemos

## Quick Reference

### API Endpoint
```
https://api.openraadsinformatie.nl/v1/elastic/_search
```

### Rotterdam Data Index
- Current: `ori_rotterdam_20250629013104`
- Pattern: `ori_rotterdam_YYYYMMDDHHMMSSS`
- **Important**: Index is date-stamped and changes periodically

### Authentication
None required - public API

### Rate Limits
Not explicitly documented, but recommend:
- Max 100 results per query
- Batch queries with time-based pagination
- Cache results locally (TTL: 6 hours)

---

## Data Model

### Document Types

#### Meeting
```python
{
    "@type": "Meeting",
    "id": "6074539",
    "name": "Commissie Energietransitie",
    "start_date": "2024-11-15T19:00:00Z",
    "committee": "6065859",
    "status": "EventConfirmed",
    "location": "Rotterdam Blaaktoren",
    "agenda": ["6074540", "6074542", "..."]  # IDs of AgendaItems
}
```

#### AgendaItem
```python
{
    "@type": "AgendaItem",
    "id": "6074540",
    "name": "1. Opening en mededelingen",
    "position": 1,
    "parent": "6074539",  # Meeting ID
    "attachment": ["6074541", "6074543"],  # Document IDs
    "decision_type": "Voorstel"
}
```

#### Document (Attachment)
```python
{
    "@type": "Document",
    "id": "6074541",
    "name": "Bijlage 1 - Verslag vorige vergadering",
    "original_url": "https://api1.ibabs.eu/publicdownload.aspx?...",
    "file_name": "Bijlage 1.pdf",
    "content_type": "application/pdf",
    "text": [
        "Page 1 text...",
        "Page 2 text..."
    ],
    "size_in_bytes": 1024000
}
```

---

## Essential Queries

### 1. Get Recent Meetings (Last 30 days)
```python
import httpx
from datetime import datetime, timedelta

async def get_recent_meetings():
    thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).isoformat() + "Z"
    
    query = {
        "query": {
            "bool": {
                "must": [
                    {"term": {"_index": "ori_rotterdam_20250629013104"}},
                    {"term": {"@type": "Meeting"}},
                    {"range": {"start_date": {"gte": thirty_days_ago}}}
                ]
            }
        },
        "size": 100,
        "sort": [{"start_date": "desc"}]
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openraadsinformatie.nl/v1/elastic/_search",
            json=query
        )
        return response.json()["hits"]["hits"]
```

### 2. Get Meeting Details (Agenda + Documents)
```python
async def get_meeting_with_agenda(meeting_id: str):
    # Step 1: Get the meeting
    meeting_query = {
        "query": {
            "bool": {
                "must": [
                    {"term": {"_index": "ori_rotterdam_20250629013104"}},
                    {"term": {"_id": meeting_id}}
                ]
            }
        }
    }
    
    # Step 2: Get all agenda items for this meeting
    agenda_query = {
        "query": {
            "bool": {
                "must": [
                    {"term": {"_index": "ori_rotterdam_20250629013104"}},
                    {"term": {"@type": "AgendaItem"}},
                    {"term": {"parent": meeting_id}}
                ]
            }
        },
        "size": 100,
        "sort": [{"position": "asc"}]
    }
    
    # Step 3: For each agenda item, fetch attached documents
    # (See next query)
```

### 3. Get Documents for Agenda Item
```python
async def get_documents_for_item(agenda_item_id: str):
    # Option A: Get from agenda item's attachment field
    agenda_query = {
        "query": {
            "bool": {
                "must": [
                    {"term": {"_index": "ori_rotterdam_20250629013104"}},
                    {"term": {"_id": agenda_item_id}}
                ]
            }
        }
    }
    
    # Get attachment IDs from response, then fetch each:
    doc_query = {
        "query": {
            "bool": {
                "must": [
                    {"term": {"_index": "ori_rotterdam_20250629013104"}},
                    {"terms": {"_id": attachment_ids}}
                ]
            }
        }
    }
```

### 4. Full-Text Search (Party Alignment Check)
```python
async def search_by_keyword(keyword: str, days_back: int = 30):
    date_threshold = (datetime.utcnow() - timedelta(days=days_back)).isoformat() + "Z"
    
    query = {
        "query": {
            "bool": {
                "must": [
                    {"term": {"_index": "ori_rotterdam_20250629013104"}},
                    {"match": {"text": keyword}},
                    {"range": {"start_date": {"gte": date_threshold}}}
                ]
            }
        },
        "size": 50,
        "sort": [{"start_date": "desc"}]
    }
    
    # Returns all documents (meetings, agenda items, attachments) matching keyword
```

### 5. Get All Committees
```python
async def get_committees():
    query = {
        "query": {
            "bool": {
                "must": [
                    {"term": {"_index": "ori_rotterdam_20250629013104"}},
                    {"term": {"@type": "Committee"}}
                ]
            }
        },
        "size": 50
    }
```

---

## Critical Issues & Solutions

### Issue 1: Hardcoded Index Name
**Problem**: Index is date-stamped, changes periodically → breaks code

**Solution**:
```python
async def get_latest_rotterdam_index():
    """Fetch list of indices and return latest Rotterdam one"""
    async with httpx.AsyncClient() as client:
        # Get indices from _cat/indices endpoint
        response = await client.get(
            "https://api.openraadsinformatie.nl/v1/elastic/_cat/indices?format=json"
        )
        indices = response.json()
        
        rotterdam_indices = [
            idx["index"] for idx in indices 
            if "rotterdam" in idx["index"] and idx["index"].startswith("ori_")
        ]
        
        # Sort and get latest
        latest = sorted(rotterdam_indices)[-1]
        return latest
```

Add to initialization and update weekly:
```python
class OpenRaadService:
    def __init__(self):
        self.INDEX = None
        self.index_updated = datetime.utcnow()
    
    async def ensure_latest_index(self):
        # Refresh index once per day
        if (datetime.utcnow() - self.index_updated).days >= 1:
            self.INDEX = await get_latest_rotterdam_index()
            self.index_updated = datetime.utcnow()
            print(f"Updated to index: {self.INDEX}")
```

### Issue 2: Missing Voting Records
**Problem**: No "stemmingsverslagen" (voting records) in ORI

**Solutions**:
1. Extract decision intent from decision documents
2. Track document amendments/versions
3. Note limitation in UI: "Decision outcomes shown; detailed voting records not available in public API"

### Issue 3: Text Extraction Quality
**Problem**: PDFs with complex formatting lose structure

**Solution**:
```python
def post_process_text(text_list):
    """Clean extracted text"""
    full_text = "\n".join(text_list)
    
    # Remove page numbers, headers, footers
    lines = full_text.split("\n")
    cleaned = [
        line for line in lines
        if not line.strip().startswith(("page", "pagina", "---"))
        and len(line.strip()) > 2
    ]
    
    return "\n".join(cleaned)
```

### Issue 4: Handling Duplicates & Amendments
**Problem**: Same document might appear multiple times, with different versions

**Solution**:
```python
def deduplicate_documents(documents):
    """Keep latest version of each document"""
    seen = {}
    
    for doc in sorted(documents, key=lambda d: d.get("updated", ""), reverse=True):
        base_name = doc["name"].split(" (")[0]  # Strip version suffix
        if base_name not in seen:
            seen[base_name] = doc
    
    return list(seen.values())
```

---

## Recommended Update Schedule

### Daily
- Query new meetings from last 24 hours
- Sync to database
- Cache local copies

### Weekly
- Check for index name changes
- Re-fetch and update all recent meetings (last 30 days)
- Detect amended documents

### Monthly
- Full re-index (refresh entire database)
- Cleanup old cache
- Verify data consistency

---

## Monitoring & Alerts

### Health Check Query
```python
async def health_check():
    """Verify API is responding"""
    query = {
        "query": {"match_all": {}},
        "size": 1
    }
    
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(
                "https://api.openraadsinformatie.nl/v1/elastic/_search",
                json=query,
                timeout=5
            )
        return response.status_code == 200
    except:
        return False
```

### Logging Template
```python
import logging

logger = logging.getLogger("NeoDemos.ORI")

# Log queries
logger.info(f"Fetching meetings from {start_date} to {end_date}")

# Log errors
logger.error(f"Failed to fetch agenda for meeting {meeting_id}: {error}")

# Log index changes
logger.warning(f"Index changed from {old_index} to {new_index}")
```

---

## Performance Tips

1. **Batch requests**: Fetch up to 100 documents per query
2. **Cache aggressively**: Store results locally with 6-hour TTL
3. **Async operations**: Use asyncio for parallel queries
4. **Pagination**: Use size/offset for large result sets
5. **Sorting**: Pre-sort by date on server side

---

## Testing

### Unit Test Example
```python
import pytest

@pytest.mark.asyncio
async def test_get_recent_meetings():
    service = OpenRaadService()
    meetings = await service.get_meetings(
        start_date="2024-01-01",
        end_date="2024-01-31"
    )
    assert len(meetings) > 0
    assert all("name" in m and "start_date" in m for m in meetings)

@pytest.mark.asyncio
async def test_get_meeting_details():
    service = OpenRaadService()
    meeting_id = "6074539"
    details = await service.get_meeting_details(meeting_id)
    assert details["id"] == meeting_id
    assert "agenda" in details
    assert len(details["agenda"]) > 0
```

---

## Resources

- **OpenRaadsinformatie Homepage**: https://openraadsinformatie.nl/
- **Rotterdam Council Data**: https://rotterdam.openraadsinformatie.nl/
- **Elasticsearch Query DSL**: https://www.elastic.co/guide/en/elasticsearch/reference/current/query-dsl.html
- **NeoDemos Project**: Local implementation

---

## Contact & Support

For questions about:
- **NeoDemos integration**: Check main.py, services/open_raad.py
- **ORI API**: See OpenRaadsinformatie documentation
- **Data issues**: Report to ORI maintainers

