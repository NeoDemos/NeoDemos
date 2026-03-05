#!/usr/bin/env python3
"""
Data Validation Script for NeoDemos
Checks database integrity and data completeness
"""

import sys
import os
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.storage import StorageService

class DataValidator:
    """Validates NeoDemos database integrity"""
    
    def __init__(self):
        self.storage = StorageService()
        self.issues = []
        self.warnings = []
    
    def run_all_checks(self) -> bool:
        """Run all validation checks"""
        print("\n" + "="*60)
        print("NeoDemos Data Validation Report")
        print("="*60 + "\n")
        
        # Run individual checks
        self._check_basic_counts()
        self._check_orphaned_documents()
        self._check_missing_content()
        self._check_document_integrity()
        self._check_meeting_dates()
        self._check_agenda_item_counts()
        
        # Print results
        self._print_results()
        
        return len(self.issues) == 0
    
    def _check_basic_counts(self):
        """Check basic record counts"""
        print("[1/6] Checking record counts...")
        stats = self.storage.validate_data()
        
        print(f"  Meetings:          {stats['meetings']}")
        print(f"  Agenda items:      {stats['agenda_items']}")
        print(f"  Documents:         {stats['documents']}")
        
        if stats['meetings'] == 0:
            self.issues.append("No meetings found in database")
        if stats['agenda_items'] == 0:
            self.issues.append("No agenda items found in database")
        if stats['documents'] == 0:
            self.issues.append("No documents found in database")
        
        # Check ratios
        if stats['meetings'] > 0:
            avg_items_per_meeting = stats['agenda_items'] / stats['meetings']
            avg_docs_per_item = stats['documents'] / stats['agenda_items'] if stats['agenda_items'] > 0 else 0
            
            print(f"  Avg items/meeting: {avg_items_per_meeting:.1f}")
            print(f"  Avg docs/item:     {avg_docs_per_item:.1f}")
            
            if avg_items_per_meeting < 3:
                self.warnings.append(f"Low average agenda items per meeting: {avg_items_per_meeting:.1f}")
    
    def _check_orphaned_documents(self):
        """Check for documents without valid agenda items"""
        print("\n[2/6] Checking for orphaned documents...")
        stats = self.storage.validate_data()
        
        if stats['orphaned_documents'] > 0:
            self.issues.append(f"Found {stats['orphaned_documents']} orphaned documents")
            print(f"  ✗ {stats['orphaned_documents']} orphaned documents")
        else:
            print(f"  ✓ No orphaned documents")
    
    def _check_missing_content(self):
        """Check for documents without extracted content"""
        print("\n[3/6] Checking for missing document content...")
        stats = self.storage.validate_data()
        
        if stats['documents_missing_content'] > 0:
            ratio = (stats['documents_missing_content'] / stats['documents']) * 100
            self.warnings.append(
                f"Found {stats['documents_missing_content']} documents without content ({ratio:.1f}%)"
            )
            print(f"  ⚠️  {stats['documents_missing_content']} docs missing content ({ratio:.1f}%)")
        else:
            print(f"  ✓ All documents have content")
    
    def _check_document_integrity(self):
        """Check document size distribution"""
        print("\n[4/6] Checking document integrity...")
        
        try:
            with self.storage._get_connection() as conn:
                with conn.cursor() as cur:
                    # Check document size distribution
                    cur.execute("""
                        SELECT 
                            COUNT(*) as total,
                            COUNT(CASE WHEN LENGTH(content) > 0 AND LENGTH(content) < 100 THEN 1 END) as tiny,
                            COUNT(CASE WHEN LENGTH(content) >= 100 AND LENGTH(content) < 1000 THEN 1 END) as small,
                            COUNT(CASE WHEN LENGTH(content) >= 1000 AND LENGTH(content) < 10000 THEN 1 END) as medium,
                            COUNT(CASE WHEN LENGTH(content) >= 10000 THEN 1 END) as large,
                            MIN(LENGTH(content)) as min_size,
                            MAX(LENGTH(content)) as max_size,
                            ROUND(AVG(LENGTH(content))::numeric, 0) as avg_size
                        FROM documents
                    """)
                    result = cur.fetchone()
                    
                    if result:
                        total, tiny, small, medium, large, min_size, max_size, avg_size = result
                        print(f"  Total documents:   {total}")
                        print(f"  Size distribution:")
                        print(f"    Tiny (<100 B):    {tiny}")
                        print(f"    Small (<1 KB):    {small}")
                        print(f"    Medium (<10 KB):  {medium}")
                        print(f"    Large (≥10 KB):   {large}")
                        print(f"  Size range:        {min_size} - {max_size} bytes")
                        print(f"  Avg size:          {avg_size} bytes")
                        
                        if tiny > 0:
                            ratio = (tiny / total) * 100
                            if ratio > 10:
                                self.warnings.append(f"Many tiny documents ({ratio:.1f}%)")
        
        except Exception as e:
            self.warnings.append(f"Could not check document sizes: {e}")
    
    def _check_meeting_dates(self):
        """Check meeting date ranges"""
        print("\n[5/6] Checking meeting date ranges...")
        
        try:
            with self.storage._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT 
                            MIN(start_date) as earliest,
                            MAX(start_date) as latest,
                            COUNT(*) as total
                        FROM meetings
                        WHERE start_date IS NOT NULL
                    """)
                    result = cur.fetchone()
                    
                    if result and result[0]:
                        earliest, latest, total = result
                        print(f"  Date range:        {earliest.date()} to {latest.date()}")
                        print(f"  Meetings in range: {total}")
        
        except Exception as e:
            self.warnings.append(f"Could not check meeting dates: {e}")
    
    def _check_agenda_item_counts(self):
        """Check agenda items per meeting"""
        print("\n[6/6] Checking agenda item distribution...")
        
        try:
            with self.storage._get_connection() as conn:
                with conn.cursor() as cur:
                    # Find meetings with no agenda items
                    cur.execute("""
                        SELECT COUNT(*) FROM meetings m
                        WHERE NOT EXISTS (
                            SELECT 1 FROM agenda_items a WHERE a.meeting_id = m.id
                        )
                    """)
                    no_agenda = cur.fetchone()[0]
                    
                    if no_agenda > 0:
                        self.warnings.append(f"Found {no_agenda} meetings without agenda items")
                        print(f"  ⚠️  {no_agenda} meetings without agenda items")
                    else:
                        print(f"  ✓ All meetings have agenda items")
        
        except Exception as e:
            self.warnings.append(f"Could not check agenda items: {e}")
    
    def _print_results(self):
        """Print validation results"""
        print("\n" + "="*60)
        
        if self.issues:
            print("❌ VALIDATION FAILED")
            print("\nCritical Issues:")
            for i, issue in enumerate(self.issues, 1):
                print(f"  {i}. {issue}")
        else:
            print("✅ VALIDATION PASSED")
        
        if self.warnings:
            print(f"\n⚠️  Warnings ({len(self.warnings)}):")
            for i, warning in enumerate(self.warnings, 1):
                print(f"  {i}. {warning}")
        
        print("="*60 + "\n")

def main():
    """Main entry point"""
    load_dotenv()
    validator = DataValidator()
    success = validator.run_all_checks()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
