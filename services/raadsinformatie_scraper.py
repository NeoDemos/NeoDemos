import httpx
import asyncio
import re
from bs4 import BeautifulSoup
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

class RaadsinformatieScraperService:
    """Service for scraping data from rotterdam.raadsinformatie.nl"""
    
    BASE_URL = "https://rotterdam.raadsinformatie.nl"
    # The real search endpoint requires the organisation filter (726 = Rotterdam)
    SEARCH_URL = f"{BASE_URL}/zoeken/result"
    ORGANISATION_ID = "726"
    
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/html, */*; q=0.01",
        "Referer": f"{BASE_URL}/zoeken?keywords=notulen"
    }
    
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=60.0, headers=self.HEADERS)
        
    async def search_documents(self, keywords: str, page: int = 1, limit: int = 25) -> List[Dict[str, Any]]:
        """Search for documents using the AJAX endpoint (with required org filter)"""
        params = {
            "keywords": keywords,
            "limit": limit,
            "document_type": "",
            "search": "send",
            "filter[organisations][]": self.ORGANISATION_ID,
            "page": page
        }
        try:
            response = await self.client.get(self.SEARCH_URL, params=params)
            response.raise_for_status()
            return self._parse_search_results(response.text)
        except Exception as e:
            logger.error(f"Error searching documents (page {page}): {e}")
            return []
            
    def _parse_search_results(self, html: str) -> List[Dict[str, Any]]:
        """Parse the HTML fragment returned by the search API"""
        soup = BeautifulSoup(html, 'html.parser')
        results = []
        
        # Results are in <li class="result-item"> elements
        items = soup.find_all('li', class_='result-item')
        for item in items:
            # Get document link (title/a element)
            title_span = item.find('span', class_='title')
            if not title_span:
                continue
            title_link = title_span.find('a')
            if not title_link:
                continue
                
            doc_url = title_link.get('href', '')
            doc_name = title_link.text.strip()
            
            # The meeting link is in a <span class="type"> element with an <a href="/vergadering/...">
            meeting_id = None
            meeting_name = None
            meeting_date = None
            
            type_spans = item.find_all('span', class_='type')
            for span in type_spans:
                # Find the meeting/event link
                meeting_link = span.find('a', href=re.compile(r'/vergadering/\d+$'))
                if meeting_link:
                    href = meeting_link.get('href', '')
                    match = re.search(r'/vergadering/(\d+)$', href)
                    if match:
                        meeting_id = match.group(1)
                        meeting_name = meeting_link.text.strip()
                
                # Find the date
                date_elem = span.find('span', class_='date')
                if date_elem:
                    meeting_date = date_elem.text.strip()
                
            if not meeting_id:
                continue
                
            results.append({
                "doc_name": doc_name,
                "doc_url": doc_url if doc_url.startswith('http') else f"{self.BASE_URL}{doc_url}",
                "meeting_id": meeting_id,
                "meeting_date": meeting_date,
                "meeting_name": meeting_name
            })
            
        logger.info(f"Parsed {len(results)} results from search page")
        return results

    async def get_meeting_details(self, meeting_id: str, meeting_name: str = None, meeting_date: str = None) -> Dict[str, Any]:
        """Fetch details of a specific meeting page, including agenda items and document links"""
        url = f"{self.BASE_URL}/vergadering/{meeting_id}"
        try:
            response = await self.client.get(url, headers={
                **self.HEADERS, 
                "Referer": self.BASE_URL,
                "X-Requested-With": ""  # Full page request, not AJAX
            })
            response.raise_for_status()
            return self._parse_meeting_page(response.text, meeting_id, meeting_name, meeting_date)
        except Exception as e:
            logger.error(f"Error fetching meeting details for {meeting_id}: {e}")
            return {}

    def _parse_meeting_page(self, html: str, meeting_id: str, meeting_name: str = None, meeting_date: str = None) -> Dict[str, Any]:
        """Parse a meeting/vergadering page for agenda items and documents"""
        soup = BeautifulSoup(html, 'html.parser')
        
        # Get meeting title from header if not provided
        if not meeting_name:
            h1 = soup.find('h1')
            meeting_name = h1.text.strip() if h1 else "Unknown Meeting"
        
        # Parse date from page if not provided
        if not meeting_date:
            date_elem = soup.find('span', class_='date') or soup.find('time')
            meeting_date = date_elem.text.strip() if date_elem else None
        
        # Find agenda items — usually in a list
        agenda = []
        agenda_section = soup.find('ul', id='agenda') or soup.find('ul', class_='agenda')
        if not agenda_section:
            # Try alternative selectors
            agenda_section = soup.find('div', class_='agenda-items') or soup.find('main')
        
        if agenda_section:
            agenda_items_els = agenda_section.find_all('li', id=re.compile(r'^ai_')) 
            if not agenda_items_els:
                # Try finding agenda items by anchor IDs
                agenda_items_els = soup.find_all('li', id=re.compile(r'^ai_'))

            for item_el in agenda_items_els:
                item_id_attr = item_el.get('id', '')
                item_id = f"{meeting_id}_{item_id_attr}"
                
                title_elem = item_el.find(['h2', 'h3', 'span', 'div'], class_=re.compile(r'title|name|label'))
                item_name = title_elem.text.strip() if title_elem else item_el.text.strip()[:100]
                
                # Find doc links in this item
                documents = []
                for doc_link in item_el.find_all('a', href=re.compile(r'/document/')):
                    doc_href = doc_link.get('href', '')
                    documents.append({
                        "name": doc_link.text.strip() or "Document",
                        "url": doc_href if doc_href.startswith('http') else f"{self.BASE_URL}{doc_href}"
                    })
                
                agenda.append({
                    "id": item_id,
                    "name": item_name,
                    "documents": documents
                })
        
        return {
            "id": meeting_id,
            "name": meeting_name,
            "meeting_date": meeting_date,
            "agenda": agenda
        }

    async def get_direct_document_text_url(self, document_url: str) -> str:
        """
        Try to extract direct PDF download URL from a document viewer URL.
        The raadsinformatie doc viewer is at /document/{id}/{version}.
        Direct PDF is often at /document/{id}/{version}/download_pdf or _with_annexes.
        """
        if '/document/' in document_url:
            return document_url + "/download_pdf"
        return document_url
    
    async def close(self):
        await self.client.aclose()
