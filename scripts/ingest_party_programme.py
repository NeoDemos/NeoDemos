#!/usr/bin/env python3
"""
Ingest Party Programme PDF
Extracts and stores party programme content for policy analysis.
Handles PDF extraction, section identification, and token-efficient storage.
"""

import sys
import os
import psycopg2
from pypdf import PdfReader
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class ProgrammeIngestionService:
    """Service for ingesting party programmes"""
    
    def __init__(self):
        self.stats = {
            'programmes_ingested': 0,
            'sections_identified': 0,
            'errors': []
        }
    
    def ingest_programme(self, file_path: str, party_name: str, election_year: int):
        """
        Ingest a party programme PDF
        
        Args:
            file_path: Path to the PDF file
            party_name: Name of the party (e.g., "GroenLinks-PvdA")
            election_year: Election year (e.g., 2025)
        """
        try:
            print(f"\n{'='*70}")
            print(f"Party Programme Ingestion Service")
            print(f"{'='*70}\n")
            
            file_path_obj = Path(file_path)
            if not file_path_obj.exists():
                print(f"✗ File not found: {file_path}")
                return self.stats
            
            print(f"[1/3] Extracting PDF content...")
            pdf_content = self._extract_pdf_text(file_path)
            
            if not pdf_content:
                print(f"✗ Could not extract text from PDF")
                return self.stats
            
            print(f"✓ Extracted {len(pdf_content):,} characters\n")
            
            # Store in database
            print(f"[2/3] Storing programme in database...")
            programme_id = self._store_programme(
                party_name=party_name,
                election_year=election_year,
                file_path=str(file_path),
                file_name=file_path_obj.name,
                pdf_content=pdf_content
            )
            
            if programme_id:
                print(f"✓ Programme stored (ID: {programme_id})\n")
                
                # Identify sections
                print(f"[3/3] Identifying document sections...")
                sections = self._identify_sections(pdf_content)
                print(f"✓ Identified {len(sections)} major sections\n")
                
                self.stats['programmes_ingested'] += 1
                self.stats['sections_identified'] = len(sections)
                
                # Print section summary
                self._print_section_summary(sections)
            
            return self.stats
        
        except Exception as e:
            error_msg = f"Fatal error: {str(e)}"
            self.stats['errors'].append(error_msg)
            print(f"\n✗ {error_msg}")
            return self.stats
    
    def _extract_pdf_text(self, file_path: str) -> str:
        """Extract text from PDF file"""
        try:
            text = []
            with open(file_path, 'rb') as f:
                pdf_reader = PdfReader(f)
                print(f"  PDF has {len(pdf_reader.pages)} pages")
                
                for page_num, page in enumerate(pdf_reader.pages):
                    try:
                        page_text = page.extract_text()
                        if page_text:
                            text.append(page_text)
                    except Exception as e:
                        self.stats['errors'].append(f"Error extracting page {page_num + 1}: {str(e)}")
            
            return '\n'.join(text)
        
        except Exception as e:
            self.stats['errors'].append(f"PDF extraction error: {str(e)}")
            return ""
    
    def _identify_sections(self, content: str) -> list:
        """
        Identify major sections in the programme based on formatting and keywords.
        Returns list of (section_name, start_char, end_char, content_preview)
        """
        sections = []
        
        # Common Dutch policy section keywords
        section_keywords = [
            'klimaat', 'duurzaam', 'energie',
            'wonen', 'huisvesting', 'huizen',
            'mobiliteit', 'vervoer', 'fiets', 'auto',
            'onderwijs', 'school',
            'zorg', 'gezondheid',
            'werk', 'werkgelegenheid', 'economie',
            'veiligheid', 'criminaliteit',
            'cultuur', 'kunst',
            'sport', 'recreatie',
            'milieu', 'natuur', 'groen',
            'democratie', 'participatie', 'inspraak',
            'diversity', 'inclusie',
            'armoede', 'schuld',
            'gemeentes',  'bestuur'
        ]
        
        lines = content.split('\n')
        current_section = None
        section_start = 0
        
        for i, line in enumerate(lines):
            stripped = line.strip().lower()
            
            # Check if line looks like a section header
            # (short lines, all caps or title case, not indented much)
            if len(stripped) > 5 and len(stripped) < 100:
                # Check for keywords
                for keyword in section_keywords:
                    if keyword in stripped:
                        # Found a potential section
                        if current_section:
                            sections.append({
                                'name': current_section,
                                'line': section_start,
                                'length': i - section_start
                            })
                        
                        current_section = stripped.title()
                        section_start = i
                        break
        
        # Add final section
        if current_section:
            sections.append({
                'name': current_section,
                'line': section_start,
                'length': len(lines) - section_start
            })
        
        return sections
    
    def _store_programme(self, party_name: str, election_year: int, 
                        file_path: str, file_name: str, pdf_content: str) -> int:
        """Store programme in database"""
        try:
            conn = psycopg2.connect(
                "postgresql://postgres:postgres@localhost:5432/neodemos"
            )
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO party_programmes 
                (party_name, election_year, file_path, file_name, pdf_content, extraction_status)
                VALUES (%s, %s, %s, %s, %s, 'extracted')
                ON CONFLICT (party_name, election_year) DO UPDATE SET
                    pdf_content = EXCLUDED.pdf_content,
                    extraction_status = 'extracted'
                RETURNING id;
            """, (party_name, election_year, file_path, file_name, pdf_content))
            
            programme_id = cursor.fetchone()[0]
            conn.commit()
            cursor.close()
            conn.close()
            
            return programme_id
        
        except Exception as e:
            error_msg = f"Database error: {str(e)}"
            self.stats['errors'].append(error_msg)
            return None
    
    def _print_section_summary(self, sections: list):
        """Print summary of identified sections"""
        print("Identified Sections:")
        print("="*70)
        for section in sections[:20]:  # Show first 20
            print(f"  • {section['name']:40} ({section['length']:3} lines)")
        if len(sections) > 20:
            print(f"  ... and {len(sections) - 20} more sections")
        print()

async def main():
    """Main entry point"""
    service = ProgrammeIngestionService()
    
    # Define the GroenLinks-PvdA 2025 programme
    programme_path = "/Users/dennistak/Documents/Final Frontier/Rotterdam Stemwijzer AI/Programmas/Verkiezingsprogramma-2025-glpvda_PRINT-DEF-DEF-DEF.pdf"
    
    stats = service.ingest_programme(
        file_path=programme_path,
        party_name="GroenLinks-PvdA",
        election_year=2025
    )
    
    if stats['errors']:
        print(f"Completed with {len(stats['errors'])} errors")
    
    sys.exit(0 if not stats['errors'] else 1)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
