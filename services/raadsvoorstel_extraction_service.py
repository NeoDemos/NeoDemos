#!/usr/bin/env python3
"""
Raadsvoorstel (City Council Proposal) Extraction Service

Extracts College B&W proposals (raadsvoorstel) from meeting documents.
Also extracts initiatiefvoorstel and Wethouder responses.

Maintains Dutch language throughout.
"""

import json
import psycopg2
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime

@dataclass
class Raadsvoorstel:
    """Single city council proposal"""
    id: str
    titel: str                    # Proposal title
    beleidsterrein: str          # Policy area
    soort: str                   # "raadsvoorstel" / "initiatiefvoorstel"
    indiener: str                # Who submitted (College B&W or Council member)
    datum: str                   # Date submitted
    volledige_tekst: str         # Full proposal text
    begroting: Optional[str]     # Budget if stated
    related_meeting_id: str      # Meeting where discussed
    related_meeting_date: str    # Date of meeting
    wethouder_respons: Optional[str]  # Alderman's response (if initiatiefvoorstel)
    uitkomst: Optional[str]      # Outcome (aangenomen/verworpen/ingetrokken)

class RaadsvoorstelExtractor:
    """Extract proposals from city council database"""
    
    def __init__(self):
        self.conn = psycopg2.connect(
            "postgresql://postgres:postgres@localhost:5432/neodemos"
        )
        self.cursor = self.conn.cursor()
    
    def extract_all_raadsvoorstel(self) -> List[Raadsvoorstel]:
        """Extract all raadsvoorstel from database"""
        
        print(f"\n{'='*70}")
        print("EXTRACTEN VAN RAADSVOORSTEL UIT GEMEENTERAAD")
        print(f"{'='*70}\n")
        
        # Find all documents that are raadsvoorstel
        print("[1/3] Searching for raadsvoorstel...")
        self.cursor.execute("""
            SELECT d.id, d.name, d.content, m.id as meeting_id, m.start_date
            FROM documents d
            INNER JOIN meetings m ON d.meeting_id = m.id
            WHERE m.name = 'Gemeenteraad'
            AND (d.name ILIKE '%raadsvoorstel%' 
                 OR d.name ILIKE '%initiatiefvoorstel%'
                 OR d.content ILIKE '%raadsvoorstel%')
            ORDER BY m.start_date DESC
        """)
        
        docs = self.cursor.fetchall()
        print(f"  ✓ Found {len(docs)} documents mentioning raadsvoorstel")
        
        raadsvoorstel_list = []
        
        print("\n[2/3] Parsing raadsvoorstel...")
        for doc_id, doc_name, content, meeting_id, meeting_date in docs:
            if not content:
                continue
            
            # Parse the document
            proposal = self._parse_raadsvoorstel(
                doc_id, doc_name, content, meeting_id, meeting_date
            )
            
            if proposal:
                raadsvoorstel_list.append(proposal)
        
        print(f"  ✓ Parsed {len(raadsvoorstel_list)} raadsvoorstel")
        
        print(f"\n[3/3] Statistics:")
        college_voorstel = [r for r in raadsvoorstel_list if r.soort == "raadsvoorstel"]
        init_voorstel = [r for r in raadsvoorstel_list if r.soort == "initiatiefvoorstel"]
        print(f"  - College B&W raadsvoorstel: {len(college_voorstel)}")
        print(f"  - Initiatiefvoorstel: {len(init_voorstel)}")
        
        return raadsvoorstel_list
    
    def _parse_raadsvoorstel(self, doc_id: str, name: str, content: str, 
                            meeting_id: str, meeting_date) -> Optional[Raadsvoorstel]:
        """Parse individual raadsvoorstel from document"""
        
        # Determine type
        if "initiatiefvoorstel" in name.lower() or "initiatiefvoorstel" in content[:500].lower():
            soort = "initiatiefvoorstel"
            indiener = "Raadslid/fractie"  # Would need to parse further
        else:
            soort = "raadsvoorstel"
            indiener = "College B&W"
        
        # Extract title (usually first substantive line)
        titel = name.replace("[", "").replace("]", "").strip()
        
        # Try to extract policy area from content
        beleidsterrein = self._extract_policy_area(content)
        
        # Try to extract budget
        begroting = self._extract_budget(content)
        
        try:
            proposal = Raadsvoorstel(
                id=doc_id,
                titel=titel,
                beleidsterrein=beleidsterrein,
                soort=soort,
                indiener=indiener,
                datum=meeting_date.isoformat() if meeting_date else "",
                volledige_tekst=content[:2000],  # First 2000 chars as summary
                begroting=begroting,
                related_meeting_id=meeting_id,
                related_meeting_date=meeting_date.isoformat() if meeting_date else "",
                wethouder_respons=None,  # Would need more parsing
                uitkomst=None  # Would need to track through meetings
            )
            return proposal
        except Exception as e:
            print(f"  ⚠️  Error parsing {name}: {e}")
            return None
    
    def _extract_policy_area(self, content: str) -> str:
        """Extract policy area from content"""
        policy_areas = [
            "Klimaat", "Wonen", "Onderwijs", "Zorg", "Economie", 
            "Mobiliteit", "Veiligheid", "Cultuur", "Milieu"
        ]
        
        for area in policy_areas:
            if area.lower() in content.lower():
                return area
        
        return "Overig"
    
    def _extract_budget(self, content: str) -> Optional[str]:
        """Extract budget if mentioned"""
        import re
        
        # Look for budget patterns: "€5 miljoen", "€500.000"
        match = re.search(r'€\s*[\d.,]+\s*(miljoen|duizend|€)?', content, re.IGNORECASE)
        if match:
            return match.group(0)
        
        return None
