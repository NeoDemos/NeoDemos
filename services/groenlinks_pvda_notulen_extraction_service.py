#!/usr/bin/env python3
"""
STAP 3C UITGEBREID: GroenLinks-PvdA Positie Extractie uit Notulen

Extracts ACTUAL GroenLinks-PvdA positions from notulen by analyzing:
- GL-PvdA statements during meetings
- GL-PvdA voting behavior and positions
- GL-PvdA proposals and amendments
- GL-PvdA questions and accountability demands
- GL-PvdA responses to College proposals

This is CRITICAL for comparing:
Programme promises (Step 3A) vs Notulen behavior (this service)
to show consistency or contradiction.

Treats GroenLinks-PvdA as ONE unified party.
"""

import json
import psycopg2
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from datetime import datetime
import re

@dataclass
class GLPvdAPosition:
    """GroenLinks-PvdA position extracted from notulen"""
    id: str
    beleidsterrein: str
    positie_omschrijving: str  # Dutch description
    activiteit_type: str  # "statement" / "stemming" / "voorstel" / "amendement" / "vraag"
    volledige_tekst: str  # Full quote from notulen
    bron_notule_id: str
    datum: str
    spreker: Optional[str]  # Name of GL-PvdA council member if identifiable
    context_type: str  # "initiatief" / "respons_college" / "motie" / "begrotings_vraag"
    sterkte: str  # "STERK" / "MATIG" / "ZWAK"
    confidence: float  # 0.0-1.0

class GLPvdANotulenExtractionService:
    """Extract actual GL-PvdA positions from meeting minutes"""
    
    def __init__(self):
        self.conn = psycopg2.connect(
            "postgresql://postgres:postgres@localhost:5432/neodemos"
        )
        self.cursor = self.conn.cursor()
        # Pattern to find GL-PvdA references (unified party)
        self.party_pattern = r"(groenlinks|pvda|partij van de arbeid)"
    
    def extract_positions_from_notulen(self) -> Dict[str, Any]:
        """
        Extract actual GroenLinks-PvdA positions from notulen by analyzing:
        1. Direct statements by GL-PvdA members
        2. Voting records
        3. Proposals and amendments submitted
        4. Questions posed to College
        5. Responses to College initiatives
        """
        
        print(f"\n{'='*70}")
        print("STAP 3C UITGEBREID: GROENLINKS-PVDA POSITIE EXTRACTIE")
        print("Uit Rotterdam Gemeenteraad Notulen")
        print(f"{'='*70}\n")
        
        results = {
            'extractie_datum': datetime.now().isoformat(),
            'type': 'groenlinks_pvda_notulen_extraction',
            'partij': 'GroenLinks-PvdA (unified)',
            'posities': [],
            'samenvatting': {}
        }
        
        try:
            with self.conn as conn:
                with conn.cursor() as cur:
                    # Get Rotterdam Gemeenteraad notulen
                    print("[1/5] Loading Rotterdam Gemeenteraad notulen...")
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
                    
                    # Analyze each notule for GL-PvdA positions
                    print("\n[2/5] Extracting GL-PvdA statements...")
                    statements = self._extract_statements(notulen_docs)
                    print(f"  ✓ {len(statements)} statements extracted")
                    
                    print("\n[3/5] Extracting GL-PvdA voting behavior...")
                    votes = self._extract_voting_behavior(notulen_docs)
                    print(f"  ✓ {len(votes)} voting positions extracted")
                    
                    print("\n[4/5] Extracting GL-PvdA proposals/amendments...")
                    proposals = self._extract_proposals_amendments(notulen_docs)
                    print(f"  ✓ {len(proposals)} proposals/amendments extracted")
                    
                    # Combine all positions
                    all_positions = statements + votes + proposals
                    results['posities'] = all_positions
                    
                    print(f"\n[5/5] Compiling summary...")
                    results['samenvatting'] = self._generate_summary(all_positions)
            
            return results
        
        except Exception as e:
            print(f"✗ Fout: {e}")
            import traceback
            traceback.print_exc()
            return results
    
    def _extract_statements(self, notulen_docs) -> List[Dict[str, Any]]:
        """Extract GL-PvdA statements during meetings"""
        
        statements = []
        
        for doc_id, doc_name, content, meeting_date in notulen_docs:
            if not content or not re.search(self.party_pattern, content, re.IGNORECASE):
                continue
            
            # Find sections with GL-PvdA member names or statements
            # Look for patterns like "Xyza (GroenLinks):" or "(PvdA)" followed by text
            patterns = [
                r"\(GroenLinks[^\)]*\):\s*([^(\n]{50,})",
                r"\(PvdA[^\)]*\):\s*([^(\n]{50,})",
                r"Partij van de Arbeid:\s*([^(\n]{50,})",
                r"raadslid.*?groenlinks.*?zegt?.*?(?::|\-)\s*([^(\n]{50,})",
            ]
            
            for pattern in patterns:
                matches = re.finditer(pattern, content, re.IGNORECASE)
                for match in matches:
                    statement_text = match.group(1)[:400]  # Longer text for better context
                    # Look for policy keywords in statement and surrounding context
                    context_start = max(0, match.start() - 200)
                    context_end = min(len(content), match.end() + 200)
                    extended_context = content[context_start:context_end]
                    
                    policy_area = self._extract_policy_area(extended_context)
                    
                    statement = {
                        'id': f"{doc_id}_stmt_{len(statements)}",
                        'beleidsterrein': policy_area,
                        'positie_omschrijving': statement_text[:100],
                        'activiteit_type': 'statement',
                        'volledige_tekst': statement_text,
                        'bron_notule_id': doc_id,
                        'datum': meeting_date.isoformat() if meeting_date else '',
                        'spreker': self._extract_speaker(match.group(0)),
                        'context_type': self._infer_context_type(statement_text),
                        'sterkte': 'STERK',
                        'confidence': 0.80
                    }
                    statements.append(statement)
        
        return statements
    
    def _extract_voting_behavior(self, notulen_docs) -> List[Dict[str, Any]]:
        """Extract GL-PvdA voting positions"""
        
        votes = []
        
        for doc_id, doc_name, content, meeting_date in notulen_docs:
            if not content or not re.search(self.party_pattern, content, re.IGNORECASE):
                continue
            
            # Look for voting records mentioning GL or PvdA
            # Patterns: "voor: GroenLinks, PvdA, ..." or "tegen: VVD; abstain: GL"
            patterns = [
                r"(voor|tegen|onthouden):\s*([^;\n]*(?:groenlinks|pvda|partij van de arbeid)[^;\n]*)",
                r"(groenlinks|pvda|partij van de arbeid).*?(stemt? voor|stemt? tegen|onthoudt? zich)",
            ]
            
            for pattern in patterns:
                matches = re.finditer(pattern, content, re.IGNORECASE)
                for match in matches:
                    vote_text = match.group(0)[:200]
                    policy_area = self._extract_policy_area(vote_text)
                    vote_direction = self._extract_vote_direction(vote_text)
                    
                    vote = {
                        'id': f"{doc_id}_vote_{len(votes)}",
                        'beleidsterrein': policy_area,
                        'positie_omschrijving': f"GL-PvdA stemt {vote_direction}",
                        'activiteit_type': 'stemming',
                        'volledige_tekst': vote_text,
                        'bron_notule_id': doc_id,
                        'datum': meeting_date.isoformat() if meeting_date else '',
                        'spreker': None,
                        'context_type': 'stemming',
                        'sterkte': 'STERK',
                        'confidence': 0.85
                    }
                    votes.append(vote)
        
        return votes
    
    def _extract_proposals_amendments(self, notulen_docs) -> List[Dict[str, Any]]:
        """Extract GL-PvdA proposals and amendments"""
        
        proposals = []
        
        for doc_id, doc_name, content, meeting_date in notulen_docs:
            if not content or not re.search(self.party_pattern, content, re.IGNORECASE):
                continue
            
            # Look for motions, amendments submitted by GL-PvdA
            patterns = [
                r"(?:motie|amendement)\s+.*?(?:groenlinks|pvda)[^.!?]{50,}(?:[.!?])",
                r"(?:voorstel|voorstel).*?(?:groenlinks|pvda).*?:\s*([^(\n]{50,})",
            ]
            
            for pattern in patterns:
                matches = re.finditer(pattern, content, re.IGNORECASE)
                for match in matches:
                    proposal_text = match.group(0)[:250]
                    policy_area = self._extract_policy_area(proposal_text)
                    
                    proposal = {
                        'id': f"{doc_id}_prop_{len(proposals)}",
                        'beleidsterrein': policy_area,
                        'positie_omschrijving': proposal_text[:80],
                        'activiteit_type': 'voorstel' if 'voorstel' in proposal_text.lower() else 'amendement',
                        'volledige_tekst': proposal_text,
                        'bron_notule_id': doc_id,
                        'datum': meeting_date.isoformat() if meeting_date else '',
                        'spreker': self._extract_speaker(proposal_text),
                        'context_type': 'initiatief',
                        'sterkte': 'STERK',
                        'confidence': 0.75
                    }
                    proposals.append(proposal)
        
        return proposals
    
    def _extract_policy_area(self, text: str) -> str:
        """Extract policy area from text"""
        
        # Extended keywords for better detection
        areas = {
            'wonen': 'Wonen',
            'huishouden': 'Wonen',
            'woningen': 'Wonen',
            'huurders': 'Wonen',
            'klimaat': 'Klimaat',
            'duurzaamheid': 'Klimaat',
            'co2': 'Klimaat',
            'energietransitie': 'Klimaat',
            'mobiliteit': 'Mobiliteit',
            'fiets': 'Mobiliteit',
            'verkeer': 'Mobiliteit',
            'autoverkeer': 'Mobiliteit',
            'parkeerplek': 'Mobiliteit',
            'onderwijs': 'Onderwijs',
            'school': 'Onderwijs',
            'kinderopvang': 'Onderwijs',
            'zorg': 'Zorg',
            'gezondheidszorg': 'Zorg',
            'jeugdhulp': 'Zorg',
            'economie': 'Economie',
            'bedrijven': 'Economie',
            'werkgelegenheid': 'Werk & Inkomen',
            'veiligheid': 'Veiligheid',
            'politie': 'Veiligheid',
            'criminaliteit': 'Veiligheid',
            'cultuur': 'Cultuur',
            'kunst': 'Cultuur',
            'museum': 'Cultuur',
            'milieu': 'Milieu',
            'milieuvervuiling': 'Milieu',
            'inkomen': 'Werk & Inkomen',
            'armoedebestrijding': 'Armoedebestrijding',
            'armoede': 'Armoedebestrijding',
            'participatie': 'Inclusiviteit',
            'migratie': 'Inclusiviteit',
            'discriminatie': 'Inclusiviteit',
            'diversiteit': 'Inclusiviteit',
            'gelijk': 'Inclusiviteit',
        }
        
        text_lower = text.lower()
        for keyword, area in areas.items():
            if keyword in text_lower:
                return area
        
        return 'Overig'
    
    def _extract_speaker(self, text: str) -> Optional[str]:
        """Try to extract speaker name"""
        
        # Look for names in parentheses like "(Jan Jansen, GroenLinks)"
        match = re.search(r"([A-Z][a-z]+\s+[A-Z][a-z]+)\s*\((?:Groen|PvdA)", text)
        if match:
            return match.group(1)
        
        return None
    
    def _extract_vote_direction(self, text: str) -> str:
        """Extract vote direction from text"""
        
        text_lower = text.lower()
        if 'voor' in text_lower or 'ja' in text_lower:
            return 'VOOR'
        elif 'tegen' in text_lower or 'nee' in text_lower:
            return 'TEGEN'
        elif 'onthoud' in text_lower:
            return 'ONTHOUDEN'
        
        return 'ONBEKEND'
    
    def _infer_context_type(self, text: str) -> str:
        """Infer context type (own proposal vs response to College)"""
        
        text_lower = text.lower()
        if any(word in text_lower for word in ['college', 'wethouder', 'reageert', 'antwoord']):
            return 'respons_college'
        elif any(word in text_lower for word in ['motie', 'amendement', 'voorstel']):
            return 'initiatief'
        elif any(word in text_lower for word in ['begroting', 'budget', 'miljoen']):
            return 'begrotings_vraag'
        
        return 'overig'
    
    def _generate_summary(self, positions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate summary of GL-PvdA positions"""
        
        summary = {
            'totaal_posities': len(positions),
            'per_activiteit_type': {},
            'per_beleidsterrein': {},
            'per_sterkte': {}
        }
        
        for pos in positions:
            # Count by activity type
            atype = pos['activiteit_type']
            summary['per_activiteit_type'][atype] = summary['per_activiteit_type'].get(atype, 0) + 1
            
            # Count by policy area
            area = pos['beleidsterrein']
            summary['per_beleidsterrein'][area] = summary['per_beleidsterrein'].get(area, 0) + 1
            
            # Count by strength
            strength = pos['sterkte']
            summary['per_sterkte'][strength] = summary['per_sterkte'].get(strength, 0) + 1
        
        return summary

