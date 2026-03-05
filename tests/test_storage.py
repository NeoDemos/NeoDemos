"""
Unit tests for StorageService
Tests database operations with PostgreSQL
"""

import pytest
import os
from dotenv import load_dotenv

# Load environment
load_dotenv()

from services.storage import StorageService

@pytest.fixture
def storage():
    """Create a StorageService instance"""
    return StorageService()

class TestStorageBasics:
    """Basic storage service tests"""
    
    def test_connection(self, storage):
        """Test PostgreSQL connection"""
        assert storage is not None
        
    def test_get_meetings(self, storage):
        """Test fetching meetings"""
        meetings = storage.get_meetings(limit=5)
        assert isinstance(meetings, list)
        assert len(meetings) > 0
        assert 'id' in meetings[0]
        assert 'name' in meetings[0]
    
    def test_meeting_details(self, storage):
        """Test fetching meeting details"""
        meetings = storage.get_meetings(limit=1)
        if meetings:
            meeting_id = meetings[0]['id']
            meeting = storage.get_meeting_details(meeting_id)
            assert meeting is not None
            assert meeting.get('id') == meeting_id
            assert 'agenda' in meeting
    
    def test_data_validation(self, storage):
        """Test data validation"""
        stats = storage.validate_data()
        assert isinstance(stats, dict)
        assert 'meetings' in stats
        assert 'agenda_items' in stats
        assert 'documents' in stats
        assert 'orphaned_documents' in stats
        assert stats['orphaned_documents'] == 0  # No orphaned docs
    
    def test_datetime_conversion(self, storage):
        """Test that datetimes are converted to strings"""
        meetings = storage.get_meetings(limit=5)
        for meeting in meetings:
            if meeting.get('start_date'):
                # Should be string, not datetime object
                assert isinstance(meeting['start_date'], str)
                # Should be ISO format
                assert 'T' in meeting['start_date']
    
    def test_last_ingestion_date(self, storage):
        """Test getting last ingestion date"""
        date = storage.get_last_ingestion_date()
        # May be None if no ingestion logged yet, but should not error
        assert date is None or isinstance(date, str)

class TestSubstantiveItems:
    """Test substantive item filtering"""
    
    def test_is_substantive_filters_procedures(self, storage):
        """Test that procedural items are filtered correctly"""
        # Test items that should NOT be substantive
        assert not storage.is_substantive_item({'number': '1.0', 'name': 'Opening'})
        assert not storage.is_substantive_item({'number': '1.5', 'name': 'Vaststellen van de agenda'})
        assert not storage.is_substantive_item({'name': 'Ingekomen stukken'})
    
    def test_is_substantive_includes_proposals(self, storage):
        """Test that substantive items are kept"""
        assert storage.is_substantive_item({'number': '1.0', 'name': 'Regeling van werkzaamheden'})
        assert storage.is_substantive_item({'number': '2.0', 'name': 'Budget proposal'})
        assert storage.is_substantive_item({'number': '3.5', 'name': 'Something substantive'})

class TestDocumentStorage:
    """Test document storage and retrieval"""
    
    def test_document_exists(self, storage):
        """Test document existence check"""
        # Get a real document ID
        meeting = storage.get_meeting_details(
            storage.get_meetings(limit=1)[0]['id']
        )
        
        if meeting and meeting.get('agenda'):
            for item in meeting['agenda']:
                if item.get('documents'):
                    doc_id = item['documents'][0]['id']
                    assert storage.document_exists(doc_id)
                    break
    
    def test_document_content_not_empty(self, storage):
        """Test that documents have content"""
        with storage._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, LENGTH(content) as content_length
                    FROM documents
                    WHERE content IS NOT NULL
                    LIMIT 10
                """)
                docs = cur.fetchall()
                for doc in docs:
                    assert doc[1] > 0  # Content length should be > 0

class TestDataIntegrity:
    """Test data integrity and relationships"""
    
    def test_no_orphaned_agenda_items(self, storage):
        """Test that all agenda items have valid meetings"""
        with storage._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM agenda_items a
                    WHERE NOT EXISTS (
                        SELECT 1 FROM meetings m WHERE m.id = a.meeting_id
                    )
                """)
                orphaned_count = cur.fetchone()[0]
                assert orphaned_count == 0
    
    def test_no_orphaned_documents(self, storage):
        """Test that all documents have valid agenda items"""
        with storage._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM documents d
                    WHERE NOT EXISTS (
                        SELECT 1 FROM agenda_items a WHERE a.id = d.agenda_item_id
                    )
                """)
                orphaned_count = cur.fetchone()[0]
                assert orphaned_count == 0

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
