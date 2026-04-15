from psycopg2.extras import RealDictCursor
from typing import List, Dict, Any, Optional
from datetime import datetime
from contextlib import contextmanager

from services.db_pool import get_connection as _pool_get_connection

class StorageService:
    """PostgreSQL-backed storage service for NeoDemos"""

    def __init__(self, connection_string: Optional[str] = None):  # noqa: ARG002 — kept for API compat
        """Initialize storage service with PostgreSQL connection"""
        self._verify_connection()

    def _verify_connection(self):
        """Verify PostgreSQL connection works"""
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT version()")
        except Exception as e:
            raise RuntimeError(f"Failed to connect to PostgreSQL: {e}")

    def _clean_name(self, name: Optional[str]) -> Optional[str]:
        """Remove legacy artifacts like 'zzz ' prefix and raw numeric IDs."""
        if not name:
            return name
        if name.startswith('zzz '):
            name = name[4:]
        # Suppress raw numeric IDs from Open Raadsinformatie (e.g. "6065857")
        if name.strip().isdigit():
            return None
        return name

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections (backed by shared pool)"""
        with _pool_get_connection() as conn:
            yield conn
    
    def get_meetings(self, limit: int = 50, year: int = None, offset: int = 0) -> List[Dict[str, Any]]:
        """Get meetings sorted by date descending, optionally filtered by year."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if year:
                    cur.execute("""
                        SELECT * FROM meetings
                        WHERE EXTRACT(YEAR FROM start_date) = %s
                        ORDER BY start_date DESC
                        LIMIT %s OFFSET %s
                    """, (year, limit, offset))
                else:
                    cur.execute("""
                        SELECT * FROM meetings
                        ORDER BY start_date DESC
                        LIMIT %s OFFSET %s
                    """, (limit, offset))
                meetings = []
                for row in cur.fetchall():
                    meeting = dict(row)
                    meeting['name'] = self._clean_name(meeting.get('name'))
                    meeting['committee'] = self._clean_name(meeting.get('committee'))
                    if meeting.get('start_date') and hasattr(meeting['start_date'], 'isoformat'):
                        meeting['start_date'] = meeting['start_date'].isoformat()
                    meetings.append(meeting)
                return meetings

    def get_meeting_years(self) -> List[int]:
        """Get all distinct years that have meetings, sorted descending."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT EXTRACT(YEAR FROM start_date)::int as year
                    FROM meetings
                    WHERE start_date IS NOT NULL
                    ORDER BY year DESC
                """)
                return [row[0] for row in cur.fetchall()]

    
    def get_meeting_details(
        self,
        meeting_id: str,
        municipality: str = 'rotterdam',
    ) -> Optional[Dict[str, Any]]:
        """Get meeting with all agenda items and documents.

        Args:
            meeting_id: primary key of the meeting row.
            municipality: gemeente slug for forward-compat (WS13 will add
                          WHERE m.municipality = municipality once the column exists).
                          TODO(WS13): enforce municipality scope here.
        """
        del municipality  # forward-compat param; meetings table has no municipality column yet (WS13)
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get meeting
                cur.execute('SELECT * FROM meetings WHERE id = %s', (meeting_id,))
                meeting_row = cur.fetchone()
                
                if not meeting_row:
                    return None
                
                meeting = dict(meeting_row)
                meeting['name'] = self._clean_name(meeting.get('name'))
                meeting['committee'] = self._clean_name(meeting.get('committee'))
                
                # Convert datetime to string for Jinja2
                if meeting.get('start_date') and hasattr(meeting['start_date'], 'isoformat'):
                    meeting['start_date'] = meeting['start_date'].isoformat()
                
                # Get agenda items - STRICT ORDERING IS CRITICAL FOR GROUPING
                cur.execute('SELECT * FROM agenda_items WHERE meeting_id = %s ORDER BY id ASC', (meeting_id,))
                agenda_rows = cur.fetchall()
                meeting['agenda'] = []
                
                for agenda_row in agenda_rows:
                    item = dict(agenda_row)
                    
                    # Get documents for this agenda item via junction table.
                    # DISTINCT guards against duplicate junction rows (WS14 C1 hotfix).
                    cur.execute(
                        '''
                        SELECT DISTINCT ON (d.id) d.*
                        FROM documents d
                        JOIN document_assignments da ON d.id = da.document_id
                        WHERE da.agenda_item_id = %s
                        ORDER BY d.id, d.name
                        ''',
                        (item['id'],)
                    )
                    item['documents'] = [dict(doc) for doc in cur.fetchall()]
                    meeting['agenda'].append(item)
                
                # Step 1: Initialize all items and identify Leads
                all_items = []
                leads = []
                sub_candidates = []
                
                for i, item in enumerate(meeting['agenda']):
                    item_num = item.get('number')
                    item['sub_items'] = []
                    item['_idx'] = i # Store original position for distance calculation
                    
                    is_lead = False
                    if item_num:
                        s_num = str(item_num).strip()
                        if s_num and s_num.lower() not in ('none', 'null', ''):
                            is_lead = True
                    
                    if is_lead:
                        leads.append(item)
                    else:
                        sub_candidates.append(item)
                    all_items.append(item)

                # Step 2: "Best Fit" grouping logic
                def get_significant_keywords(s):
                    dutch_stop_words = {'de', 'van', 'het', 'een', 'en', 'in', 'is', 'dat', 'op', 'met', 'voor', 'bij', 'daarbij', 'om', 'te', 'die', 'als', 'wat', 'ter', 'door', 'tot', 'aan'}
                    return {w.strip('.,()') for w in s.lower().split() if len(w) > 3 and w.strip('.,()') not in dutch_stop_words}

                final_agenda = []
                assigned_ids = set()
                
                # First pass: Semantic matching (Keyword overlap with distance penalty)
                for candidate in sub_candidates:
                    c_id = candidate['id']
                    c_words = get_significant_keywords(candidate.get('name', ''))
                    
                    if not c_words:
                        continue
                        
                    best_parent = None
                    max_score = 0
                    
                    for lead in leads:
                        l_words = get_significant_keywords(lead.get('name', ''))
                        overlap = len(c_words.intersection(l_words))
                        distance = abs(lead['_idx'] - candidate['_idx'])
                        
                        # Distance-aware scoring
                        # If it's a weak match (only 1 word), reject it if it's too far (> 3 items away)
                        score = overlap
                        if overlap == 1 and distance > 3:
                            score = 0
                        
                        if score > max_score:
                            max_score = score
                            best_parent = lead
                        elif score == max_score and score > 0:
                            # Tie-breaker: pick the closest parent
                            current_best_distance = abs(best_parent['_idx'] - candidate['_idx'])
                            if distance < current_best_distance:
                                best_parent = lead
                    
                    if best_parent and max_score >= 1:
                        best_parent['sub_items'].append(candidate)
                        assigned_ids.add(c_id)

                # Second pass: Positional fallback
                current_lead = None
                for item in all_items:
                    if item in leads:
                        current_lead = item
                        final_agenda.append(item)
                    else:
                        if item['id'] in assigned_ids:
                            continue # Already nested semantically
                        
                        if current_lead:
                            current_lead['sub_items'].append(item)
                        else:
                            final_agenda.append(item)
                
                meeting['agenda'] = final_agenda
                return meeting

    def get_agenda_item_with_sub_documents(self, agenda_item_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch an agenda item and all documents for it AND its sub-items.
        Returns a dict with 'name', 'meeting_name', and 'documents' (List of {name, content}).
        """
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Get the item and its meeting_id
                cur.execute("SELECT name, meeting_id FROM agenda_items WHERE id = %s", (agenda_item_id,))
                item_row = cur.fetchone()
                if not item_row:
                    return None
                
                meeting_id = item_row['meeting_id']
                
                # 2. Use existing logic to get the full meeting with correctly grouped items
                # (This ensures the lead/sub relationship is identical to what the user sees in the UI)
                meeting = self.get_meeting_details(meeting_id)
                if not meeting:
                    return None
                
                # 3. Find our item in the agenda (could be a lead or a sub)
                target_item = None
                all_docs = []
                
                # Search top-level items
                for item in meeting['agenda']:
                    if str(item['id']) == str(agenda_item_id):
                        target_item = item
                        break
                    # Search sub-items
                    for sub in item.get('sub_items', []):
                        if str(sub['id']) == str(agenda_item_id):
                            target_item = sub
                            break
                    if target_item:
                        break
                
                if not target_item:
                    return None
                    
                # 4. Collect documents from the item itself
                all_docs.extend(target_item.get('documents', []))
                
                # 5. IF it's a lead item, also collect documents from all its nested sub-items
                for sub in target_item.get('sub_items', []):
                    all_docs.extend(sub.get('documents', []))
                    
                return {
                    "name": target_item['name'],
                    "meeting_name": meeting['name'],
                    "documents": all_docs
                }
    
    def is_substantive_item(self, item: dict, meeting_name: str = '') -> bool:
        """
        Determine if an agenda item is substantive (should be analyzed)
        vs procedural (should be skipped).
        """
        name = item.get('name', '').lower()
        number = item.get('number')
        
        # Check if it's a COR meeting (Commissie tot onderzoek van de Rekening)
        is_cor = 'commissie tot onderzoek van de rekening' in meeting_name.lower() or 'cor' in meeting_name.lower().split()
        
        # Skip ingekomen stukken
        if 'ingekomen stukken' in name:
            return False
        
        # Skip items numbered 1.0-1.99 UNLESS they are substantive proposals or it's a COR meeting
        if number:
            try:
                num_float = float(str(number).split('.')[0])
                if num_float == 1.0:
                    if is_cor:
                        pass # COR 1.x items are substantive
                    else:
                        # Check for substantive keywords in 1.x items
                        substantive_keywords = [
                            'regeling van werkzaamheden',
                            'verkenning',
                            'voorstel',
                            'beleid',
                            'strategie'
                        ]
                        if not any(kw in name for kw in substantive_keywords):
                            return False
            except (ValueError, IndexError):
                pass
        
        # Skip "Vaststellen van de agenda" items
        if 'vaststellen van de agenda' in name:
            return False
        
        # All other items are substantive
        return True
    
    def insert_meeting(self, meeting_data: Dict[str, Any]) -> bool:
        """Insert or update a meeting"""
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO meetings (id, name, start_date, committee, location, organization_id)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            name = EXCLUDED.name,
                            start_date = EXCLUDED.start_date,
                            committee = EXCLUDED.committee,
                            location = EXCLUDED.location,
                            organization_id = EXCLUDED.organization_id,
                            last_updated = CURRENT_TIMESTAMP
                    """, (
                        meeting_data['id'],
                        meeting_data['name'],
                        meeting_data.get('start_date'),
                        meeting_data.get('committee'),
                        meeting_data.get('location'),
                        meeting_data.get('organization_id')
                    ))
                    return True
        except Exception as e:
            print(f"Error inserting meeting: {e}")
            return False
    
    def insert_agenda_item(self, agenda_item_data: Dict[str, Any]) -> bool:
        """Insert or update an agenda item"""
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO agenda_items (id, meeting_id, number, name)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            number = EXCLUDED.number,
                            name = EXCLUDED.name
                    """, (
                        agenda_item_data['id'],
                        agenda_item_data['meeting_id'],
                        agenda_item_data.get('number'),
                        agenda_item_data.get('name')
                    ))
                    return True
        except Exception as e:
            print(f"Error inserting agenda item: {e}")
            return False
    
    def insert_document(self, document_data: Dict[str, Any]) -> bool:
        """Insert or update a document.

        Accepts optional fields added in migration 0006:
          - municipality (str, default 'rotterdam')
          - source       (str, e.g. 'ori', 'ibabs', 'scraper', 'manual')
          - doc_classification (str, civic type e.g. 'schriftelijke_vraag')
          - category     (str, e.g. 'municipal_doc', 'meeting')
        """
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    # Clean NUL characters from content
                    content = document_data.get('content', '')
                    if content:
                        content = content.replace('\x00', '')

                    summary_json = document_data.get('summary_json', '')
                    if summary_json:
                        summary_json = summary_json.replace('\x00', '')

                    municipality = document_data.get('municipality', 'rotterdam') or 'rotterdam'
                    source = document_data.get('source')
                    doc_classification = document_data.get('doc_classification')
                    category = document_data.get('category')

                    cur.execute("""
                        INSERT INTO documents
                            (id, name, meeting_id, content, summary_json, url,
                             municipality, source, doc_classification, category)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            name = EXCLUDED.name,
                            url = COALESCE(EXCLUDED.url, documents.url),
                            content = CASE
                                WHEN LENGTH(EXCLUDED.content) > LENGTH(COALESCE(documents.content, ''))
                                THEN EXCLUDED.content
                                ELSE COALESCE(documents.content, EXCLUDED.content)
                            END,
                            summary_json = COALESCE(EXCLUDED.summary_json, documents.summary_json),
                            municipality = COALESCE(EXCLUDED.municipality, documents.municipality),
                            source = COALESCE(EXCLUDED.source, documents.source),
                            doc_classification = COALESCE(EXCLUDED.doc_classification, documents.doc_classification),
                            category = COALESCE(EXCLUDED.category, documents.category)
                    """, (
                        document_data['id'],
                        document_data.get('name'),
                        document_data.get('meeting_id'),  # Keep for legacy; assignments is primary
                        content,
                        summary_json,
                        document_data.get('url'),
                        municipality,
                        source,
                        doc_classification,
                        category,
                    ))

                    # Ensure assignment exists
                    if document_data.get('meeting_id') or document_data.get('agenda_item_id'):
                        cur.execute("""
                            INSERT INTO document_assignments (document_id, meeting_id, agenda_item_id)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (document_id, meeting_id, agenda_item_id) DO NOTHING
                        """, (
                            document_data['id'],
                            document_data.get('meeting_id'),
                            document_data.get('agenda_item_id')
                        ))
                    return True
        except Exception as e:
            print(f"Error inserting document: {e}")
            return False
    
    def ensure_document_assignment(self, document_id: str, meeting_id: Optional[str],
                                   agenda_item_id: Optional[str]) -> bool:
        """Ensure a (document, meeting, agenda_item) link exists.

        The upsert path in `insert_document` only writes `document_assignments`
        when a NEW row is inserted; when the same `document_id` is re-asserted
        for a different meeting (overzichtsitem reused across agendas, or any
        doc that was first ingested under another meeting_id), the assignment
        row is never written and the calendar UI shows zero bijlagen for the
        new meeting — exactly the 2026-04-15 Erik regression for the
        `f9b8b1c0` raadsvergadering where 13 of 17 docs were present in
        `documents` but absent from `document_assignments`.
        """
        if not (meeting_id or agenda_item_id):
            return False
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO document_assignments (document_id, meeting_id, agenda_item_id)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (document_id, meeting_id, agenda_item_id) DO NOTHING
                    """, (document_id, meeting_id, agenda_item_id))
                    return True
        except Exception as e:
            print(f"Error ensuring document assignment: {e}")
            return False

    def get_document_content_length(self, doc_id: str) -> int:
        """Return LENGTH(content) for a document, or 0 if row missing.

        Used by the refresh service to decide whether to retry OCR on a
        stub (document row exists with empty/short content).
        """
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT LENGTH(COALESCE(content, '')) FROM documents WHERE id = %s",
                        (doc_id,),
                    )
                    row = cur.fetchone()
                    return int(row[0]) if row else 0
        except Exception as e:
            print(f"Error reading document content length for {doc_id}: {e}")
            return 0

    def document_exists(self, doc_id: str) -> bool:
        """Check if a document already exists"""
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM documents WHERE id = %s", (doc_id,))
                    return cur.fetchone() is not None
        except Exception:
            return False
    
    def log_ingestion(self, date_range_start: str, date_range_end: str, 
                     meetings_found: int, meetings_inserted: int, 
                     meetings_updated: int, documents_downloaded: int, 
                     errors: Optional[str] = None):
        """Log an ingestion operation"""
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO ingestion_log 
                        (date_range_start, date_range_end, meetings_found, meetings_inserted, 
                         meetings_updated, documents_downloaded, errors)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        date_range_start,
                        date_range_end,
                        meetings_found,
                        meetings_inserted,
                        meetings_updated,
                        documents_downloaded,
                        errors
                    ))
        except Exception as e:
            print(f"Error logging ingestion: {e}")
    
    def get_last_ingestion_date(self) -> Optional[str]:
        """Get the date of the last ingestion"""
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT date_range_end FROM ingestion_log 
                        ORDER BY run_date DESC 
                        LIMIT 1
                    """)
                    result = cur.fetchone()
                    return result[0].isoformat() if result else None
        except Exception:
            return None
    
    def validate_data(self) -> Dict[str, Any]:
        """Validate database integrity"""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                # Count records
                cur.execute("SELECT COUNT(*) FROM meetings")
                meetings_count = cur.fetchone()[0]
                
                cur.execute("SELECT COUNT(*) FROM agenda_items")
                agenda_items_count = cur.fetchone()[0]
                
                cur.execute("SELECT COUNT(*) FROM documents")
                documents_count = cur.fetchone()[0]
                
                # Check for orphaned documents
                cur.execute("""
                    SELECT COUNT(*) FROM documents 
                    WHERE agenda_item_id NOT IN (SELECT id FROM agenda_items)
                """)
                orphaned_docs = cur.fetchone()[0]
                
                # Check for missing content
                cur.execute("""
                    SELECT COUNT(*) FROM documents 
                    WHERE content IS NULL OR content = ''
                """)
                missing_content = cur.fetchone()[0]
                
                return {
                    'meetings': meetings_count,
                    'agenda_items': agenda_items_count,
                    'documents': documents_count,
                    'orphaned_documents': orphaned_docs,
                    'documents_missing_content': missing_content,
                    'status': 'PASS' if orphaned_docs == 0 else 'FAIL'
                }

    def get_documents_metadata(self, doc_ids: List[str]) -> List[Dict[str, Any]]:
        """Retrieve names and URLs for a list of document IDs"""
        if not doc_ids:
            return []
        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT d.id, d.name, d.url, m.start_date 
                        FROM documents d
                        JOIN document_assignments da ON d.id = da.document_id
                        LEFT JOIN meetings m ON da.meeting_id = m.id
                        WHERE d.id = ANY(%s)
                        """,
                        (doc_ids,)
                    )
                    
                    results = []
                    for row in cur.fetchall():
                        doc = dict(row)
                        if doc.get('start_date') and hasattr(doc['start_date'], 'isoformat'):
                            doc['start_date'] = doc['start_date'].isoformat()
                        results.append(doc)
                    return results
        except Exception as e:
            print(f"Error retrieving document metadata: {e}")
            return []

    def get_meetings_filtered(
        self,
        year: Optional[int] = None,
        committee: Optional[str] = None,
        search: Optional[str] = None,
        has_docs: Optional[bool] = None,
        limit: int = 500,
        municipality: str = 'rotterdam',
    ) -> List[Dict[str, Any]]:
        """Get meetings with agenda_item_count and doc_count, with optional filters.

        Returns meetings ordered by start_date DESC.  Each row includes:
        - agenda_item_count  (int)
        - doc_count          (int)   -- total docs (bijlage + annotatie + other)
        - bijlage_count      (int)   -- docs where doc_classification = 'bijlage'
        - annotatie_count    (int)   -- docs where doc_classification = 'annotatie'
        - other_count        (int)   -- remaining / unclassified docs
        - first 5 agenda-item names as ``agenda_preview`` (list[str])

        Args:
            has_docs: if True, only return meetings that have at least one document.
                      if None (default), return all meetings.
            search: matches against meeting name, committee AND agenda item names.
            municipality: gemeente slug to scope results (default 'rotterdam').
                          NOTE: meetings table has no municipality column yet (WS13).
                          This parameter is accepted for forward-compat but the WHERE
                          clause is not applied until the column exists.
                          TODO(WS13): add WHERE m.municipality = municipality
                          once migration adds the column.
        """
        del municipality  # forward-compat param; meetings table has no municipality column yet (WS13)
        from services.calendar_labels import normalize_and_dedupe

        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                conditions: list[str] = []
                params: list = []

                if year:
                    conditions.append("EXTRACT(YEAR FROM m.start_date) = %s")
                    params.append(year)

                if committee:
                    conditions.append("m.committee ILIKE %s")
                    params.append(f"%{committee}%")

                if search:
                    # Search meeting name, committee, AND agenda item names
                    conditions.append("""(
                        m.name ILIKE %s
                        OR m.committee ILIKE %s
                        OR EXISTS (
                            SELECT 1 FROM agenda_items ai2
                            WHERE ai2.meeting_id = m.id
                            AND ai2.name ILIKE %s
                        )
                    )""")
                    params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

                where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

                # C3: split doc_count into bijlage/annotatie/other.
                # C2: the document_assignments join now catches rows linked at
                # agenda-item level only (no meeting_id set) via a correlated
                # subquery. This is belt-and-braces: the B1 backfill script will
                # ensure meeting_id is always populated, but the OR guard means
                # any agenda-item-only junction rows are still counted correctly.
                #
                # We JOIN documents (d) to get doc_classification for the FILTER
                # expressions in C3. The DISTINCT on da.document_id is preserved
                # inside each FILTER by using COUNT(DISTINCT ...).
                #
                # has_docs filter applied as HAVING clause after aggregation.
                having = "HAVING COUNT(DISTINCT da.document_id) > 0" if has_docs else ""

                params.append(limit)

                cur.execute(f"""
                    SELECT
                        m.id,
                        m.name,
                        m.start_date,
                        m.committee,
                        m.location,
                        COUNT(DISTINCT ai.id) AS agenda_item_count,
                        COUNT(DISTINCT da.document_id) AS doc_count,
                        COUNT(DISTINCT da.document_id)
                            FILTER (WHERE d.doc_classification = 'bijlage')
                            AS bijlage_count,
                        COUNT(DISTINCT da.document_id)
                            FILTER (WHERE d.doc_classification = 'annotatie')
                            AS annotatie_count,
                        COUNT(DISTINCT da.document_id)
                            FILTER (WHERE d.doc_classification NOT IN ('bijlage', 'annotatie')
                                      OR d.doc_classification IS NULL)
                            AS other_count
                    FROM meetings m
                    LEFT JOIN agenda_items ai ON ai.meeting_id = m.id
                    -- C2: match junction rows linked directly OR via agenda item
                    LEFT JOIN document_assignments da
                        ON da.meeting_id = m.id
                        OR da.agenda_item_id IN (
                            SELECT id FROM agenda_items WHERE meeting_id = m.id
                        )
                    LEFT JOIN documents d ON d.id = da.document_id
                    {where}
                    GROUP BY m.id, m.name, m.start_date, m.committee, m.location
                    {having}
                    ORDER BY m.start_date DESC
                    LIMIT %s
                """, params)

                meetings = []
                meeting_ids = []
                for row in cur.fetchall():
                    meeting = dict(row)
                    meeting['name'] = self._clean_name(meeting.get('name'))
                    meeting['committee'] = self._clean_name(meeting.get('committee'))
                    if meeting.get('start_date') and hasattr(meeting['start_date'], 'isoformat'):
                        meeting['start_date'] = meeting['start_date'].isoformat()
                    # Ensure int types (psycopg2 may return Decimal for COUNT)
                    for count_col in ('doc_count', 'bijlage_count', 'annotatie_count', 'other_count', 'agenda_item_count'):
                        meeting[count_col] = int(meeting.get(count_col) or 0)
                    meeting['agenda_preview'] = []
                    meetings.append(meeting)
                    meeting_ids.append(meeting['id'])

                # Fetch first 5 agenda items per meeting for preview
                if meeting_ids:
                    cur.execute("""
                        SELECT meeting_id, name
                        FROM (
                            SELECT meeting_id, name,
                                   ROW_NUMBER() OVER (PARTITION BY meeting_id ORDER BY id) AS rn
                            FROM agenda_items
                            WHERE meeting_id = ANY(%s) AND name IS NOT NULL
                        ) sub
                        WHERE rn <= 5
                        ORDER BY meeting_id, rn
                    """, (meeting_ids,))
                    previews: Dict[str, list] = {}
                    for row in cur.fetchall():
                        mid = row['meeting_id']
                        previews.setdefault(mid, []).append(row['name'])
                    for meeting in meetings:
                        meeting['agenda_preview'] = previews.get(meeting['id'], [])

                # C6: normalize weekday-prefixed names and soft-dedup display duplicates
                return normalize_and_dedupe(meetings)

    def get_distinct_committees(self) -> List[str]:
        """Return all distinct committee names, sorted alphabetically."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT committee
                    FROM meetings
                    WHERE committee IS NOT NULL AND committee != ''
                    ORDER BY committee
                """)
                raw = [row[0] for row in cur.fetchall()]
                return [self._clean_name(c) for c in raw if c]

    def get_document_full_content(self, doc_id: str) -> Optional[str]:
        """Fetch the raw text content of a document by its ID."""
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT content FROM documents WHERE id = %s", (doc_id,))
                    row = cur.fetchone()
                    return row[0] if row else None
        except Exception as e:
            print(f"Error fetching document content: {e}")
            return None
