import psycopg2
from psycopg2.extras import RealDictCursor
import os
from typing import List, Dict, Any, Optional
from datetime import datetime
from contextlib import contextmanager

class StorageService:
    """PostgreSQL-backed storage service for NeoDemos"""
    
    def __init__(self, connection_string: Optional[str] = None):
        """Initialize storage service with PostgreSQL connection"""
        if connection_string is None:
            # Build from environment or use default
            host = os.getenv("DB_HOST", "localhost")
            port = os.getenv("DB_PORT", "5432")
            database = os.getenv("DB_NAME", "neodemos")
            user = os.getenv("DB_USER", "postgres")
            password = os.getenv("DB_PASSWORD", "postgres")
            
            connection_string = f"postgresql://{user}:{password}@{host}:{port}/{database}"
        
        self.connection_string = connection_string
        self._verify_connection()
    
    def _verify_connection(self):
        """Verify PostgreSQL connection works"""
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT version()")
        except Exception as e:
            raise RuntimeError(f"Failed to connect to PostgreSQL: {e}")
    
    @contextmanager
    def _get_connection(self):
        """Context manager for database connections"""
        conn = psycopg2.connect(self.connection_string)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
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

    
    def get_meeting_details(self, meeting_id: str) -> Optional[Dict[str, Any]]:
        """Get meeting with all agenda items and documents"""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get meeting
                cur.execute('SELECT * FROM meetings WHERE id = %s', (meeting_id,))
                meeting_row = cur.fetchone()
                
                if not meeting_row:
                    return None
                
                meeting = dict(meeting_row)
                
                # Convert datetime to string for Jinja2
                if meeting.get('start_date') and hasattr(meeting['start_date'], 'isoformat'):
                    meeting['start_date'] = meeting['start_date'].isoformat()
                
                # Get agenda items
                cur.execute('SELECT * FROM agenda_items WHERE meeting_id = %s', (meeting_id,))
                agenda_rows = cur.fetchall()
                meeting['agenda'] = []
                
                for agenda_row in agenda_rows:
                    item = dict(agenda_row)
                    
                    # Get documents for this agenda item
                    cur.execute(
                        'SELECT * FROM documents WHERE agenda_item_id = %s',
                        (item['id'],)
                    )
                    item['documents'] = [dict(doc) for doc in cur.fetchall()]
                    meeting['agenda'].append(item)
                
                # Post-process agenda items to merge 'Betrekken bij' items into their parent items
                parent_items = []
                child_items = []
                for item in meeting['agenda']:
                    if item.get('name', '').strip().lower().startswith('betrekken bij'):
                        child_items.append(item)
                    else:
                        parent_items.append(item)
                
                # Try to attach children to parents
                for child in child_items:
                    c_name_lower = child.get('name', '').strip().lower()
                    target = c_name_lower.replace('betrekken bij', '', 1).strip()
                    
                    merged = False
                    if target:
                        for parent in parent_items:
                            p_name_lower = parent.get('name', '').lower()
                            # Check if target is a significant substring of parent, or vice versa
                            if target in p_name_lower or p_name_lower in target:
                                parent['documents'].extend(child.get('documents', []))
                                merged = True
                                break
                    
                    if not merged:
                        parent_items.append(child)
                
                meeting['agenda'] = parent_items
                
                return meeting
    
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
        """Insert or update a document"""
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
                    
                    cur.execute("""
                        INSERT INTO documents (id, agenda_item_id, meeting_id, name, url, content, summary_json)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            name = EXCLUDED.name,
                            url = EXCLUDED.url,
                            content = EXCLUDED.content,
                            summary_json = EXCLUDED.summary_json
                    """, (
                        document_data['id'],
                        document_data['agenda_item_id'],
                        document_data['meeting_id'],
                        document_data.get('name'),
                        document_data.get('url'),
                        content,
                        summary_json
                    ))
                    return True
        except Exception as e:
            print(f"Error inserting document: {e}")
            return False
    
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
