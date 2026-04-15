import httpx
import asyncio
import re
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class IBabsService:
    """Service for scraping agenda and meeting data directly from iBabs Public Portal"""
    
    BASE_URL = "https://rotterdamraad.bestuurlijkeinformatie.nl"
    LIST_MEETINGS_URL = f"{BASE_URL}/Agenda/RetrieveAgendasForYear"
    AGENDA_INDEX_URL = f"{BASE_URL}/Agenda/Index"
    DOCUMENT_LOAD_URL = f"{BASE_URL}/Document/LoadAgendaItemDocument"
    # /Calendar aggregates upcoming meetings across every `agendatypeId`
    # (raadsvergadering + commissies + stadsberaad + werkbezoek). Used by
    # `get_upcoming_meetings` for the 15-min refresh so new agendatypes in
    # raadsperiode 2026-2030 are picked up without a hardcoded type list.
    CALENDAR_URL = f"{BASE_URL}/Calendar"

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest"
        }

    async def get_meetings_for_year(self, year: int, agendatype_id: str = "100002367") -> List[Dict[str, Any]]:
        """Fetch list of meetings for a given year using the RetrieveAgendasForYear endpoint"""
        params = {
            "agendatypeId": agendatype_id,
            "year": str(year)
        }
        
        async with httpx.AsyncClient(timeout=30.0, headers=self.headers) as client:
            try:
                response = await client.get(self.LIST_MEETINGS_URL, params=params)
                response.raise_for_status()
                return self._parse_meeting_list(response.text, year)
            except Exception as e:
                logger.error(f"Error fetching meetings for year {year}: {e}")
                return []

    async def get_upcoming_meetings(self) -> List[Dict[str, Any]]:
        """Scrape /Calendar — returns every upcoming meeting across all agendatypes.

        The legacy `get_meetings_for_year(agendatype_id="100002367")` only
        polls one type and therefore misses stadsberaad / BWB-startberaad
        (agendatypeId=100199686) plus any new type raadsperiode 2026-2030
        introduces. This method is agendatype-agnostic: it parses the same
        cards the portal's public calendar renders, so new types are picked
        up the moment they appear.

        Returns the same shape as `get_meetings_for_year` so callers can
        swap implementations without further changes.
        """
        async with httpx.AsyncClient(timeout=30.0, headers=self.headers) as client:
            try:
                response = await client.get(self.CALENDAR_URL, follow_redirects=True)
                response.raise_for_status()
                return self._parse_calendar_page(response.text)
            except Exception as e:
                logger.error(f"Error fetching /Calendar: {e}")
                return []

    def _parse_calendar_page(self, html: str) -> List[Dict[str, Any]]:
        """Parse the /Calendar HTML into meeting dicts.

        Each meeting card surfaces committee, location, subtitle, date and
        time. The meeting UUID lives in the `/Agenda/Index/{id}` link; all
        other fields are derived from the card text.
        """
        soup = BeautifulSoup(html, 'html.parser')
        meetings: List[Dict[str, Any]] = []
        seen_ids: set = set()

        for link in soup.find_all('a', href=re.compile(r'/Agenda/Index/')):
            href = link['href']
            meeting_id = href.rsplit('/', 1)[-1]
            if not meeting_id or meeting_id in seen_ids:
                continue
            seen_ids.add(meeting_id)

            card = link.find_parent(['li', 'div', 'article']) or link
            card_text = card.get_text(separator=' | ', strip=True)
            parts = [p.strip() for p in card_text.split('|') if p.strip()]

            committee = parts[0] if parts else None
            location = None
            subtitle = None
            date_str = None
            time_str = None
            for part in parts[1:]:
                if part.startswith('(') and part.endswith(')') and not location:
                    location = part.strip('()')
                elif re.search(r'\b(maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag)\b', part, re.IGNORECASE) and not date_str:
                    date_str = part
                elif re.match(r'^\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}$', part) and not time_str:
                    time_str = part
                elif not subtitle and len(part) > 3 and '@' not in part:
                    subtitle = part

            start_date = None
            if date_str:
                start_dt = self._parse_ibabs_date(
                    f"{date_str}, {time_str.split('-')[0].strip()}" if time_str else date_str,
                    datetime.now().year,
                )
                if start_dt:
                    start_date = start_dt.isoformat()

            meetings.append({
                "id": meeting_id,
                "name": f"{committee} — {subtitle}" if subtitle and committee else (committee or subtitle or "Meeting"),
                "committee": committee,
                "location": location,
                "subtitle": subtitle,
                "start_date": start_date,
                "url": f"{self.BASE_URL}{href}",
            })

        return meetings

    async def get_meeting_agenda(self, meeting_id: str, resolve_references: bool = False) -> Dict[str, Any]:
        """Fetch agenda items and document links for a specific meeting.

        Every returned agenda item carries ``meeting_id`` and every document
        carries both ``meeting_id`` and ``agenda_item_id`` so downstream
        `storage.insert_agenda_item` / `storage.insert_document` can write
        without losing the parent reference. Missing either field caused the
        2026-04-15 Erik regression where UUID-meeting agenda items/documents
        were silently dropped by the `scheduled_refresh` Phase 2 sweep.
        """
        url = f"{self.AGENDA_INDEX_URL}/{meeting_id}"

        async with httpx.AsyncClient(timeout=30.0, headers={**self.headers, "X-Requested-With": ""}) as client:
            try:
                response = await client.get(url, follow_redirects=True)

                # Fallback: if numeric ID fails with 500/404, it might be an ORI ID that doesn't match iBabs GUID
                if (response.status_code >= 400) and meeting_id.isdigit():
                    logger.warning(f"Meeting ID {meeting_id} failed with {response.status_code}. This might be a numeric ID mismatch.")
                    # In a real scenario, we might want to search by date/committee here.
                    # For now, we'll just raise the error to be handled by the caller.

                response.raise_for_status()
                agenda_data = self._parse_agenda_page(response.text, meeting_id)
                
                # 2. Fetch Overzichtsitems for each agenda item (often AJAX loaded)
                ov_tasks = []
                order = []
                for item in agenda_data.get("agenda", []):
                    if item["id"] != "general":
                        ov_tasks.append(self._fetch_overzichtsitems(item["id"]))
                        order.append(item)
                
                if ov_tasks:
                    ov_results = await asyncio.gather(*ov_tasks)
                    for item, docs in zip(order, ov_results):
                        item["documents"].extend(docs)

                if resolve_references:
                    # Resolve Overzichtsitems to direct download URLs
                    tasks = []
                    docs_to_resolve = []
                    for item in agenda_data.get("agenda", []):
                        for doc in item.get("documents", []):
                            if doc.get("type") == "overzichtsitem" and doc.get("report_guid") and not doc.get("resolved"):
                                tasks.append(self.resolve_overzichtsitem(doc["report_guid"]))
                                docs_to_resolve.append(doc)
                    
                    if tasks:
                        results = await asyncio.gather(*tasks)
                        for doc, resolved_docs in zip(docs_to_resolve, results):
                            if resolved_docs:
                                # Find the parent item to add multiple documents
                                parent_item = None
                                for item in agenda_data.get("agenda", []):
                                    if doc in item["documents"]:
                                        parent_item = item
                                        break
                                
                                if parent_item:
                                    # Remove the original placeholder
                                    parent_item["documents"].remove(doc)
                                    # Add all resolved documents
                                    for rd in resolved_docs:
                                        new_doc = {
                                            "id": rd["id"],
                                            "name": f"{doc['name']} - {rd['name']}" if rd['name'] != "Document" else doc['name'],
                                            "url": f"{self.BASE_URL}/Document/View/{rd['id']}",
                                            "type": "overzichtsitem",
                                            "resolved": True,
                                            "report_guid": doc['report_guid']
                                        }
                                        parent_item["documents"].append(new_doc)
                
                return agenda_data
            except Exception as e:
                logger.error(f"Error fetching agenda for meeting {meeting_id}: {e}")
                return {}

    async def _fetch_overzichtsitems(self, item_id: str) -> List[Dict]:
        """Fetch Overzichtsitems for a specific agenda item via AJAX endpoint"""
        url = f"{self.BASE_URL}/Agenda/ListEntries/{item_id}?type=Overzichtsitems"
        async with httpx.AsyncClient(timeout=30.0, headers=self.headers) as client:
            try:
                response = await client.get(url, headers={"X-Requested-With": "XMLHttpRequest"})
                if response.status_code != 200: return []
                
                soup = BeautifulSoup(response.text, 'html.parser')
                rows = soup.find_all('tr')
                bb_pattern = re.compile(r'\b[0-9]{2}bb[0-9]{4,10}\b', re.IGNORECASE)
                
                docs = []
                for row in rows:
                    cells = row.find_all('td')
                    if len(cells) < 2: continue
                    
                    bb_number = None
                    for cell in cells:
                        text = cell.get_text(strip=True)
                        match = bb_pattern.search(text)
                        if match:
                            bb_number = match.group(0).lower()
                            break
                    
                    if bb_number:
                        doc_title = cells[2].get_text(strip=True) if len(cells) >= 3 else ""
                        if not doc_title or doc_title.lower() == bb_number.lower():
                            # Try to find a cell that isn't the BB-number
                            for cell in cells:
                                text = cell.get_text(strip=True)
                                if text and text.lower() != bb_number.lower() and len(text) > 10:
                                    doc_title = text
                                    break
                        if not doc_title: doc_title = f"Document referentie: {bb_number}"
                        item_url = row.get('data-url', '') or row.get('data-entry-id', '')
                        report_guid = item_url.split('/')[-1] if item_url else None
                        
                        docs.append({
                            "id": bb_number,
                            "name": f"[{bb_number.upper()}] {doc_title}",
                            "url": None,
                            "type": "overzichtsitem",
                            "report_guid": report_guid,
                            "resolved": False
                        })
                return docs
            except Exception as e:
                logger.error(f"Error fetching AJAX Overzichtsitems for {item_id}: {e}")
                return []

    async def resolve_overzichtsitem(self, report_guid: str) -> List[Dict[str, str]]:
        """Fetch the report item detail page and extract all documentIds for download"""
        # We try both Item and Details URLs as they often serve similar content
        urls = [
            f"{self.BASE_URL}/Reports/Item/{report_guid}",
            f"{self.BASE_URL}/Reports/Details/{report_guid}"
        ]
        
        found_docs = []
        async with httpx.AsyncClient(timeout=30.0, headers=self.headers) as client:
            for url in urls:
                try:
                    response = await client.get(url, follow_redirects=True)
                    if response.status_code != 200: continue
                    
                    soup = BeautifulSoup(response.text, 'html.parser')
                    # Find ALL links with data-document-id
                    links = soup.find_all('a', attrs={'data-document-id': True})
                    for link in links:
                        doc_id = link['data-document-id']
                        doc_name = link.get_text(strip=True) or "Document"
                        # Deduplicate by doc_id
                        if not any(d['id'] == doc_id for d in found_docs):
                            found_docs.append({
                                "id": doc_id,
                                "name": doc_name
                            })
                    
                    if found_docs: break # Stop if we found documents
                except Exception as e:
                    logger.error(f"Error resolving Overzichtsitem {report_guid} at {url}: {e}")
        
        return found_docs

    def _parse_meeting_list(self, html: str, year: int) -> List[Dict[str, Any]]:
        """Parse the HTML fragment returned by RetrieveAgendasForYear"""
        soup = BeautifulSoup(html, 'html.parser')
        meetings = []
        
        # Meetings are in <li> items with class agenda-link
        items = soup.find_all('li', class_='agenda-link')
        for item in items:
            link = item.find('a', href=re.compile(r'/Agenda/Index/'))
            if not link:
                continue
                
            href = link['href']
            meeting_id = href.split('/')[-1]
            
            title_elem = link.find('div', class_='agenda-link-title')
            subtitle_elem = link.find('div', class_='agenda-link-subtitle')
            
            name = title_elem.get_text(separator=' ', strip=True) if title_elem else "Unknown Meeting"
            subtitle = subtitle_elem.get_text(separator=' ', strip=True) if subtitle_elem else ""
            
            # Extract date from name or subtitle
            start_date = self._parse_ibabs_date(subtitle, year)
            if not start_date:
                start_date = self._parse_ibabs_date(name, year)
            
            meetings.append({
                "id": meeting_id,
                "name": name,
                "subtitle": subtitle,
                "start_date": start_date.isoformat() if start_date else None,
                "year": year,
                "url": f"{self.BASE_URL}{href}"
            })
            
        return meetings

    def _parse_ibabs_date(self, date_str: str, year: int) -> Optional[datetime]:
        """Parse iBabs Dutch date string to datetime object"""
        if not date_str:
            return None
            
        months = {
            'januari': 1, 'februari': 2, 'maart': 3, 'april': 4, 'mei': 5, 'juni': 6,
            'juli': 7, 'augustus': 8, 'september': 9, 'oktober': 10, 'november': 11, 'december': 12
        }
        
        try:
            # Example: "maandag 3 februari 2026, 18:00 - 18:30"
            match = re.search(r'(\d+)\s+([a-z]+)\s+(\d{4})(?:,\s+(\d{2}):(\d{2}))?', date_str.lower())
            if match:
                day, month_name, yr, hour, minute = match.groups()
                month = months.get(month_name, 1)
                h = int(hour) if hour else 0
                m = int(minute) if minute else 0
                return datetime(int(yr), month, int(day), h, m)
        except Exception:
            pass
        return None

    def _parse_agenda_page(self, html: str, meeting_id: str) -> Dict[str, Any]:
        """Parse the meeting index page for agenda items and documents"""
        soup = BeautifulSoup(html, 'html.parser')
        
        # Basic meeting info
        h1 = soup.find('h1')
        meeting_name = h1.get_text(strip=True) if h1 else "Unknown Meeting"
        
        h2 = soup.find('h2') # Often the date
        meeting_date_str = h2.get_text(strip=True) if h2 else None
        
        # Group by agenda items
        items_dict = {}

        # 1. Process standard document links (Bijlagen)
        doc_links = soup.find_all('a', href=re.compile(r'/Agenda/Document/'))
        for link in doc_links:
            href = link['href']
            doc_match = re.search(r'documentId=([a-f0-9-]+)', href)
            item_match = re.search(r'agendaItemId=([a-f0-9-]+)', href)
            
            if not doc_match: continue
                
            doc_id = doc_match.group(1)
            item_id = item_match.group(1) if item_match else "general"
            doc_name = link.get_text(strip=True) or "Document"
            
            if item_id not in items_dict:
                items_dict[item_id] = self._create_item_entry(link, item_id)
            
            download_url = f"{self.BASE_URL}/Document/LoadAgendaItemDocument/{doc_id}"
            if item_id != "general": download_url += f"?agendaItemId={item_id}"
            
            items_dict[item_id]["documents"].append({
                "id": doc_id, "name": doc_name, "url": download_url, "type": "bijlage"
            })

        # 2. Process Overzichtsitems (referenced documents via BB-numbers)
        # These are usually in tables. We look for rows that contain BB-numbers.
        rows = soup.find_all('tr')
        bb_pattern = re.compile(r'\b[0-9]{2}bb[0-9]{4,10}\b', re.IGNORECASE)
        
        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 2: continue
            
            # Check if any cell matches the BB-number pattern
            bb_number = None
            for cell in cells:
                text = cell.get_text(strip=True)
                match = bb_pattern.search(text)
                if match:
                    bb_number = match.group(0).lower()
                    break
            
            if bb_number:
                # Find the parent agenda item
                parent_panel = row.find_parent(class_='panel')
                item_id = "general"
                if parent_panel:
                    panel_id = parent_panel.get('id', '')
                    item_id = panel_id.replace('panel-agendaitem-', '').replace('agendaitem-', '')
                    
                    if not item_id or len(item_id) < 10:
                        first_link = parent_panel.find('a', href=re.compile(r'agendaItemId='))
                        if first_link:
                            m = re.search(r'agendaItemId=([a-f0-9-]+)', first_link['href'])
                            item_id = m.group(1) if m else "general"

                if item_id not in items_dict:
                    items_dict[item_id] = self._create_item_entry(row, item_id)

                # Use the third column as title if available, otherwise the cell text
                doc_title = cells[2].get_text(strip=True) if len(cells) >= 3 else ""
                if not doc_title or doc_title.lower() == bb_number.lower():
                    for cell in cells:
                        text = cell.get_text(strip=True)
                        if text and text.lower() != bb_number.lower() and len(text) > 10:
                            doc_title = text
                            break
                if not doc_title: doc_title = f"Document referentie: {bb_number}"

                # Append as a 'reference' document
                item_url = row.get('data-url', '')
                report_guid = item_url.split('/')[-1] if item_url else None

                items_dict[item_id]["documents"].append({
                    "id": bb_number,
                    "name": f"[{bb_number.upper()}] {doc_title}",
                    "url": None, # Needs resolution
                    "type": "overzichtsitem",
                    "report_guid": report_guid,
                    "resolved": False
                })

        agenda_list = list(items_dict.values())
        # Stamp parent references so downstream inserts don't drop the link.
        # insert_agenda_item requires `meeting_id`; insert_document writes
        # `meeting_id` + `agenda_item_id` into `document_assignments` (see
        # services/storage.py:411). Without these fields, Phase 2 calendar
        # sweep silently dropped every agenda item and document for UUID-
        # format raadsperiode 2026-2030 meetings (Erik — 2026-04-15).
        for item in agenda_list:
            item["meeting_id"] = meeting_id
            for doc in item.get("documents", []):
                doc.setdefault("meeting_id", meeting_id)
                if item["id"] != "general":
                    doc.setdefault("agenda_item_id", item["id"])

        return {
            "id": meeting_id,
            "name": meeting_name,
            "date_str": meeting_date_str,
            "agenda": agenda_list,
        }

    def _create_item_entry(self, element, item_id: str) -> Dict[str, Any]:
        """Helper to create a standard agenda item entry from a soup element."""
        parent_panel = element.find_parent(class_='panel')
        item_name = "Algemeen"
        if parent_panel:
            title_elem = parent_panel.find(class_='panel-title-label')
            if title_elem:
                item_name = title_elem.get_text(strip=True)
        
        return {
            "id": item_id,
            "name": item_name,
            "documents": []
        }

    async def close(self):
        pass
