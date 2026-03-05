#!/usr/bin/env python3
"""
STAP 3B: Extractie Raadsvoorstel & Initiatiefvoorstel

Extracts formal city council proposals from the database:
- Raadsvoorstel (College B&W proposals)
- Initiatiefvoorstel (Council member proposals)
- Tracks budgets, outcomes, Wethouder responses

All in Dutch for precision and credibility.
"""

import os
import sys
import json
import psycopg2
from typing import List, Dict, Any
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.storage import StorageService

class RaadsvoorstelExtractor:
    """Extract formal council proposals from database"""
    
    def __init__(self):
        self.storage = StorageService()
    
    def extract_all_raadsvoorstel(self) -> Dict[str, Any]:
        """Extract all raadsvoorstel and initiatiefvoorstel"""
        
        print(f"\n{'='*70}")
        print("STAP 3B: EXTRACTIE RAADSVOORSTEL & INITIATIEFVOORSTEL")
        print(f"{'='*70}\n")
        
        results = {
            'extractie_datum': datetime.now().isoformat(),
            'raadsvoorstel_college': [],
            'initiatiefvoorstel': [],
            'totaal': 0
        }
        
        try:
            with self.storage._get_connection() as conn:
                with conn.cursor() as cur:
                    # Extract raadsvoorstel mentioning policy topics
                    print("[1/3] Extracting College B&W raadsvoorstel...")
                    college_proposals = self._extract_college_raadsvoorstel(cur)
                    results['raadsvoorstel_college'] = college_proposals
                    print(f"  ✓ {len(college_proposals)} raadsvoorstel gevonden")
                    
                    # Extract initiatiefvoorstel
                    print("\n[2/3] Extracting Council member initiatiefvoorstel...")
                    init_proposals = self._extract_initiatiefvoorstel(cur)
                    results['initiatiefvoorstel'] = init_proposals
                    print(f"  ✓ {len(init_proposals)} initiatiefvoorstel gevonden")
                    
                    # Statistics
                    print("\n[3/3] Compiling statistics...")
                    results['totaal'] = len(college_proposals) + len(init_proposals)
                    
                    # By policy area
                    areas = {}
                    for prop in college_proposals + init_proposals:
                        area = prop.get('beleidsterrein', 'Overig')
                        areas[area] = areas.get(area, 0) + 1
                    results['per_beleidsterrein'] = areas
                    
                    print(f"  ✓ Totaal: {results['totaal']} voorstellen")
            
            return results
        
        except Exception as e:
            print(f"✗ Fout: {e}")
            import traceback
            traceback.print_exc()
            return results
    
    def _extract_college_raadsvoorstel(self, cur) -> List[Dict[str, Any]]:
        """Extract College B&W proposals (raadsvoorstel)"""
        
        proposals = []
        
        # Find documents that are raadsvoorstel
        cur.execute("""
            SELECT d.id, d.name, d.content, m.start_date, m.id as meeting_id
            FROM documents d
            INNER JOIN meetings m ON d.meeting_id = m.id
            WHERE m.name = 'Gemeenteraad'
            AND (d.name ILIKE '%raadsvoorstel%' 
                 AND d.name NOT ILIKE '%initiatiefvoorstel%')
            ORDER BY m.start_date DESC
            LIMIT 30
        """)
        
        for doc_id, name, content, meeting_date, meeting_id in cur.fetchall():
            if not content:
                continue
            
            proposal = {
                'id': doc_id,
                'type': 'raadsvoorstel',
                'titel': name.replace('[', '').replace(']', '').strip(),
                'indiener': 'College B&W',
                'datum': meeting_date.isoformat() if meeting_date else '',
                'beleidsterrein': self._extract_policy_area(content),
                'inhoud_preview': content[:500],
                'meeting_id': meeting_id,
                'inhoud_lengte': len(content),
                'bevat_begroting': 'miljoen' in content.lower() or '€' in content
            }
            proposals.append(proposal)
        
        return proposals
    
    def _extract_initiatiefvoorstel(self, cur) -> List[Dict[str, Any]]:
        """Extract Council member proposals (initiatiefvoorstel)"""
        
        proposals = []
        
        # Find documents that are initiatiefvoorstel
        cur.execute("""
            SELECT d.id, d.name, d.content, m.start_date, m.id as meeting_id
            FROM documents d
            INNER JOIN meetings m ON d.meeting_id = m.id
            WHERE m.name = 'Gemeenteraad'
            AND d.name ILIKE '%initiatiefvoorstel%'
            ORDER BY m.start_date DESC
            LIMIT 30
        """)
        
        for doc_id, name, content, meeting_date, meeting_id in cur.fetchall():
            if not content:
                continue
            
            # Try to extract indiener from content
            indiener = self._extract_indiener(content)
            
            proposal = {
                'id': doc_id,
                'type': 'initiatiefvoorstel',
                'titel': name.replace('[', '').replace(']', '').strip(),
                'indiener': indiener or 'Onbekend raadslid/fractie',
                'datum': meeting_date.isoformat() if meeting_date else '',
                'beleidsterrein': self._extract_policy_area(content),
                'inhoud_preview': content[:500],
                'meeting_id': meeting_id,
                'inhoud_lengte': len(content),
                'bevat_begroting': 'miljoen' in content.lower() or '€' in content
            }
            proposals.append(proposal)
        
        return proposals
    
    def _extract_policy_area(self, content: str) -> str:
        """Extract policy area from content"""
        
        policy_areas = {
            'klimaat': 'Klimaat',
            'wonen': 'Wonen',
            'mobiliteit': 'Mobiliteit',
            'onderwijs': 'Onderwijs',
            'zorg': 'Zorg',
            'economie': 'Economie',
            'veiligheid': 'Veiligheid',
            'cultuur': 'Cultuur',
            'milieu': 'Milieu',
            'inkomen': 'Werk & Inkomen',
            'armoedebestrijding': 'Armoedebestrijding',
        }
        
        content_lower = content.lower()
        for keyword, area in policy_areas.items():
            if keyword in content_lower:
                return area
        
        return 'Overig'
    
    def _extract_indiener(self, content: str) -> str:
        """Try to extract who submitted the proposal"""
        
        # Look for common framing
        if 'initiatiefvoorstel' in content.lower():
            # Check for party names
            for party in ['groenlinks', 'pvda', 'vvd', 'd66', 'sp', 'cda']:
                if party in content.lower():
                    return party.upper()
        
        return None

def main():
    """Main execution"""
    
    extractor = RaadsvoorstelExtractor()
    results = extractor.extract_all_raadsvoorstel()
    
    # Display results
    print("\n" + "="*70)
    print("✓ EXTRACTIE VOLTOOID")
    print("="*70)
    
    print(f"\nRaadsvoorstel (College B&W): {len(results['raadsvoorstel_college'])}")
    print(f"Initiatiefvoorstel (Raad): {len(results['initiatiefvoorstel'])}")
    print(f"Totaal: {results['totaal']}")
    
    print(f"\nPer beleidsterrein:")
    for area, count in sorted(results['per_beleidsterrein'].items()):
        print(f"  {area}: {count}")
    
    # Save results
    output_file = "data/pipeline/raadsvoorstel_2024_2025.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str, ensure_ascii=False)
    
    print(f"\n✓ Resultaten opgeslagen in: {output_file}")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
