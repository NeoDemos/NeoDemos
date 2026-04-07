#!/usr/bin/env python3
"""
STAP 3C: Notulen Position Inference Service

Extracts IMPLICIT positions of College B&W from notulen:
- How Wethouders respond to GroenLinks-PvdA initiatives
- Voting behavior and patterns
- Budget allocation priorities
- Stated policy directions in meetings

Treats GroenLinks-PvdA as ONE unified party throughout.

Key principle: Actual behavior (notulen) reveals real priorities
better than formal proposals alone.
"""

import json
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from datetime import datetime
import re

from services.db_pool import get_connection

@dataclass
class NotulenPosition:
    """Implicit position inferred from notulen"""
    id: str
    beleidsterrein: str
    positie_omschrijving: str  # Dutch description of inferred position
    bewijs_type: str  # "wethouder_respons" / "stemgedrag" / "begroting" / "uitspraak"
    bron_notule_id: str
    datum: str
    context_preview: str  # Quote or context
    sterkte: str  # "STERK" / "MATIG" / "ZWAK"
    confidence: float  # 0.0-1.0
    
class NotulenPositionInferenceService:
    """Infer College B&W positions from meeting minutes"""

    def __init__(self):
        # Pattern to find GroenLinks-PvdA references (unified party)
        self.party_pattern = r"(groenlinks|pvda|partij van de arbeid)"
    
    def infer_positions_from_notulen(self) -> Dict[str, Any]:
        """
        Infer College B&W positions from notulen by analyzing:
        1. Wethouder responses to GL-PvdA initiatives
        2. Voting records
        3. Budget allocations
        4. Explicit statements about policy direction
        """
        
        print(f"\n{'='*70}")
        print("STAP 3C: INFERENTIE IMPLICIETE COLLEGE-POSITIES UIT NOTULEN")
        print(f"{'='*70}\n")
        
        results = {
            'extractie_datum': datetime.now().isoformat(),
            'type': 'notulen_position_inference',
            'actor': 'College B&W',
            'tegenover': 'GroenLinks-PvdA',
            'posities': [],
            'samenvatting': {}
        }
        
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Get Rotterdam Gemeenteraad notulen
                    print("[1/4] Loading Rotterdam Gemeenteraad notulen...")
                    cur.execute("""
                        SELECT d.id, d.name, d.content, m.start_date
                        FROM documents d
                        INNER JOIN meetings m ON d.meeting_id = m.id
                        INNER JOIN document_classifications dc ON d.id = dc.document_id
                        WHERE m.name = 'Gemeenteraad'
                        AND dc.is_notulen = TRUE
                        AND d.content IS NOT NULL
                        ORDER BY m.start_date DESC
                    """)

                    notulen_docs = cur.fetchall()
                    print(f"  ✓ {len(notulen_docs)} notulen loaded")

                    # Analyze each notule
                    print("\n[2/4] Analyzing notulen for implicit positions...")
                    all_positions = []

                    for doc_id, doc_name, content, meeting_date in notulen_docs:
                        if not content:
                            continue

                        # Find GL-PvdA mentions
                        if not re.search(self.party_pattern, content, re.IGNORECASE):
                            continue

                        # Infer positions from this notule
                        positions = self._infer_from_single_notule(
                            doc_id, doc_name, content, meeting_date
                        )
                        all_positions.extend(positions)

                    results['posities'] = all_positions
                    print(f"  ✓ {len(all_positions)} positie-inferenties afgeleid")

                    # Organize by policy area
                    print("\n[3/4] Organizing by policy area...")
                    by_area = {}
                    for pos in all_positions:
                        area = pos.get('beleidsterrein', 'Overig')
                        if area not in by_area:
                            by_area[area] = []
                        by_area[area].append(pos)

                    results['per_beleidsterrein'] = {
                        area: len(positions) for area, positions in by_area.items()
                    }

                    # Generate summary
                    print("\n[4/4] Generating summary...")
                    results['samenvatting'] = self._generate_summary(all_positions)

            return results
        
        except Exception as e:
            print(f"✗ Fout: {e}")
            import traceback
            traceback.print_exc()
            return results
    
    def _infer_from_single_notule(
        self, 
        doc_id: str, 
        doc_name: str, 
        content: str, 
        meeting_date
    ) -> List[Dict[str, Any]]:
        """Infer positions from a single notule"""
        
        positions = []
        
        # Extract policy areas mentioned with GL-PvdA
        for area in ['wonen', 'klimaat', 'mobiliteit', 'onderwijs', 'zorg', 'economie', 'veiligheid']:
            # Check if both area and party mentioned in same context
            if area.lower() in content.lower() and re.search(self.party_pattern, content, re.IGNORECASE):
                
                # Find evidence type
                evidence_type = self._determine_evidence_type(content, area)
                
                if evidence_type:
                    # Extract context snippet
                    context = self._extract_context_snippet(content, area, 200)
                    
                    # Infer position
                    position_desc = self._infer_position_description(content, area)
                    
                    position = {
                        'id': f"{doc_id}_{area}",
                        'beleidsterrein': area.capitalize(),
                        'positie_omschrijving': position_desc,
                        'bewijs_type': evidence_type,
                        'bron_notule_id': doc_id,
                        'datum': meeting_date.isoformat() if meeting_date else '',
                        'context_preview': context[:150],
                        'sterkte': self._assess_strength(context),
                        'confidence': 0.65  # Inferences from notulen are moderate confidence
                    }
                    positions.append(position)
        
        return positions
    
    def _determine_evidence_type(self, content: str, area: str) -> Optional[str]:
        """Determine what type of evidence indicates the position"""
        
        # Look for response keywords
        if any(word in content.lower() for word in ['wethouder', 'reactie', 'antwoord', 'zegt', 'stelt']):
            return 'wethouder_respons'
        
        # Look for voting keywords
        if any(word in content.lower() for word in ['stemming', 'voor', 'tegen', 'onthouden', 'aangenomen', 'verworpen']):
            return 'stemgedrag'
        
        # Look for budget keywords
        if any(word in content.lower() for word in ['budget', 'miljoen', '€', 'begroting', 'geld']):
            return 'begroting'
        
        # Default: explicit statement
        return 'uitspraak'
    
    def _extract_context_snippet(self, content: str, area: str, max_length: int) -> str:
        """Extract relevant snippet around area mention"""
        
        # Find area mention
        pattern = rf"(\w{{0,50}}\b{area}\w{{0,50}})"
        matches = re.finditer(pattern, content, re.IGNORECASE)
        
        for match in matches:
            start = max(0, match.start() - 50)
            end = min(len(content), match.end() + 150)
            snippet = content[start:end]
            if len(snippet) > 20:
                return snippet
        
        return content[:max_length]
    
    def _infer_position_description(self, content: str, area: str) -> str:
        """Create Dutch description of inferred position"""
        
        # Simple heuristics for inferring position from language
        if 'markt' in content.lower():
            return f"College kiest voor marktmechanismen in {area}"
        elif 'investeringen' in content.lower() or 'budget' in content.lower():
            return f"College investeert in {area}"
        elif 'duurzaam' in content.lower() or 'groen' in content.lower():
            return f"College benadrukt duurzaamheid in {area}"
        elif 'tegen' in content.lower() or 'bezwaar' in content.lower():
            return f"College heeft reserveringen over GL-PvdA voorstellen in {area}"
        else:
            return f"College positie inferred op {area}"
    
    def _assess_strength(self, context: str) -> str:
        """Assess strength of evidence"""
        
        length = len(context)
        if length > 200:
            return "STERK"
        elif length > 100:
            return "MATIG"
        else:
            return "ZWAK"
    
    def _generate_summary(self, positions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate summary of inferred positions"""
        
        summary = {
            'totaal_posities': len(positions),
            'per_bewijs_type': {},
            'per_sterkte': {},
            'beleidsterreinen': set()
        }
        
        for pos in positions:
            # Count by evidence type
            etype = pos['bewijs_type']
            summary['per_bewijs_type'][etype] = summary['per_bewijs_type'].get(etype, 0) + 1
            
            # Count by strength
            strength = pos['sterkte']
            summary['per_sterkte'][strength] = summary['per_sterkte'].get(strength, 0) + 1
            
            # Collect areas
            summary['beleidsterreinen'].add(pos['beleidsterrein'])
        
        summary['beleidsterreinen'] = list(summary['beleidsterreinen'])
        return summary

