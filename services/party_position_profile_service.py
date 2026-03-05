#!/usr/bin/env python3
"""
CORRECTED ARCHITECTURE: Party Position Profile Service

Extracts complete party position & value profile from BOTH:
1. Party programme documents (formal stated positions)
2. Notulen (actual voiced opinions in council)

This combined source is then used to evaluate Rotterdam policies
through the party's ideological lens.

Dutch language throughout.
"""

import json
import psycopg2
import re
from typing import Dict, List, Any, Optional
from datetime import datetime
from collections import defaultdict

class PartyPositionProfileService:
    """Build comprehensive party position profile from programme + notulen"""
    
    def __init__(self, party_name: str = "GroenLinks-PvdA"):
        self.party_name = party_name
        self.conn = psycopg2.connect(
            "postgresql://postgres:postgres@localhost:5432/neodemos"
        )
        self.cursor = self.conn.cursor()
        self.party_pattern = r"(groenlinks|pvda|partij van de arbeid)"
    
    def build_party_profile(self) -> Dict[str, Any]:
        """
        Build complete party position profile from both sources.
        
        Returns:
        {
          "partij": "GroenLinks-PvdA",
          "status": "Opposition (2022-present)",
          "posities": {
            "wonen": {
              "uit_programma": "...",
              "uit_notulen": ["...", "..."],
              "kernwaarde": "...",
              "consistentie": "Consistent" / "Inconsistent"
            },
            ...
          }
        }
        """
        
        print(f"\n{'='*70}")
        print("PARTY POSITION PROFILE SERVICE")
        print(f"Partij: {self.party_name}")
        print(f"{'='*70}\n")
        
        result = {
            'profiel_datum': datetime.now().isoformat(),
            'partij': self.party_name,
            'status': 'Opposition (sinds maart 2022)',
            'posities': {},
            'kernwaarden': [],
            'samenvatting': {}
        }
        
        try:
            # Step 1: Load party programme positions
            print("[1/4] Loading party programme positions...")
            programme_positions = self._extract_programme_positions()
            print(f"  ✓ {len(programme_positions)} policy positions from programme")
            
            # Step 2: Load notulen positions
            print("\n[2/4] Extracting party positions from notulen...")
            notulen_positions = self._extract_notulen_positions()
            print(f"  ✓ {len(notulen_positions)} position references from notulen")
            
            # Step 3: Combine and match positions
            print("\n[3/4] Combining programme and notulen positions...")
            combined_positions = self._combine_positions(
                programme_positions, 
                notulen_positions
            )
            print(f"  ✓ {len(combined_positions)} unique policy areas identified")
            
            # Step 4: Extract core values
            print("\n[4/4] Identifying core party values...")
            core_values = self._extract_core_values(combined_positions)
            print(f"  ✓ {len(core_values)} core values identified")
            
            result['posities'] = combined_positions
            result['kernwaarden'] = core_values
            result['samenvatting'] = self._generate_summary(
                combined_positions, 
                core_values
            )
            
            return result
        
        except Exception as e:
            print(f"✗ Fout: {e}")
            import traceback
            traceback.print_exc()
            return result
    
    def _extract_programme_positions(self) -> Dict[str, Dict[str, Any]]:
        """Extract positions from party programme"""
        
        positions = defaultdict(dict)
        
        try:
            with open('data/pipeline/groenlinks_pvda_detailed_proposals.json', 'r') as f:
                programme_data = json.load(f)
            
            voorstellen = programme_data.get('voorstellen', {})
            
            # voorstellen is organized by policy area
            if isinstance(voorstellen, dict):
                for area, proposals in voorstellen.items():
                    if isinstance(proposals, list) and proposals:
                        # Synthesize positions from proposals
                        first_proposal = proposals[0]
                        if isinstance(first_proposal, dict):
                            positions[area] = {
                                'uit_programma': first_proposal.get('titel', ''),
                                'volledige_tekst': first_proposal.get('volledige_tekst', '')[:200],
                                'aantal_voorstellen': len(proposals),
                                'beleidsgebied': area,
                                'bron': 'verkiezingsprogramma_2025'
                            }
            
            return dict(positions)
        
        except FileNotFoundError:
            print("  ⚠ Programme file not found")
            return {}
        except Exception as e:
            print(f"  ⚠ Error extracting programme: {e}")
            return {}
    
    def _extract_notulen_positions(self) -> Dict[str, List[Dict[str, Any]]]:
        """Extract positions from notulen (council statements, motions, votes)"""
        
        positions = defaultdict(list)
        
        try:
            # Get all Rotterdam Gemeenteraad notulen with GL-PvdA content
            self.cursor.execute("""
                SELECT d.id, d.name, d.content, m.start_date
                FROM documents d
                INNER JOIN meetings m ON d.meeting_id = m.id
                INNER JOIN document_classifications dc ON d.id = dc.document_id
                WHERE m.name = 'Gemeenteraad'
                AND dc.is_notulen = TRUE
                AND d.content IS NOT NULL
                AND (d.content ILIKE '%groenlinks%' OR d.content ILIKE '%pvda%')
                ORDER BY m.start_date DESC
                LIMIT 50
            """)
            
            notulen_docs = self.cursor.fetchall()
            
            for doc_id, doc_name, content, meeting_date in notulen_docs:
                if not content:
                    continue
                
                # Extract different types of positions from notulen
                # 1. Statements
                statements = self._extract_statements(content)
                # 2. Motions
                motions = self._extract_motions(content)
                # 3. Questions
                questions = self._extract_questions(content)
                # 4. Voting positions
                votes = self._extract_votes(content)
                
                # Categorize by policy area
                for stmt in statements:
                    area = stmt.get('beleidsgebied', 'Overig')
                    positions[area].append({
                        'type': 'statement',
                        'tekst': stmt.get('tekst'),
                        'datum': meeting_date.isoformat() if meeting_date else None,
                        'bron': doc_id
                    })
                
                for motion in motions:
                    area = motion.get('beleidsgebied', 'Overig')
                    positions[area].append({
                        'type': 'motie',
                        'tekst': motion.get('tekst'),
                        'datum': meeting_date.isoformat() if meeting_date else None,
                        'bron': doc_id
                    })
                
                for question in questions:
                    area = question.get('beleidsgebied', 'Overig')
                    positions[area].append({
                        'type': 'vraag',
                        'tekst': question.get('tekst'),
                        'datum': meeting_date.isoformat() if meeting_date else None,
                        'bron': doc_id
                    })
                
                for vote in votes:
                    area = vote.get('beleidsgebied', 'Overig')
                    positions[area].append({
                        'type': 'stemming',
                        'richting': vote.get('richting'),
                        'datum': meeting_date.isoformat() if meeting_date else None,
                        'bron': doc_id
                    })
            
            return dict(positions)
        
        except Exception as e:
            print(f"  ⚠ Error extracting notulen: {e}")
            return {}
    
    def _extract_statements(self, content: str) -> List[Dict[str, Any]]:
        """Extract GL-PvdA statements from notulen content"""
        
        statements = []
        
        # Look for patterns like "(GroenLinks):" or "GL-fractie:"
        patterns = [
            r"\(GroenLinks[^\)]*\):\s*([^(\n]{50,})",
            r"\(PvdA[^\)]*\):\s*([^(\n]{50,})",
            r"GL-fractie.*?:\s*([^(\n]{50,})",
        ]
        
        for pattern in patterns:
            matches = re.finditer(pattern, content, re.IGNORECASE)
            for match in matches:
                stmt_text = match.group(1)[:150]
                area = self._categorize_policy_area(stmt_text)
                statements.append({
                    'tekst': stmt_text,
                    'beleidsgebied': area
                })
        
        return statements
    
    def _extract_motions(self, content: str) -> List[Dict[str, Any]]:
        """Extract GL-PvdA motions from notulen"""
        
        motions = []
        
        # Look for motion patterns mentioning GL-PvdA
        pattern = r"motie.*?(?:groenlinks|pvda)[^\n\.]{30,}(?:[.!?]|$)"
        
        matches = re.finditer(pattern, content, re.IGNORECASE)
        for match in matches:
            motion_text = match.group(0)[:150]
            area = self._categorize_policy_area(motion_text)
            motions.append({
                'tekst': motion_text,
                'beleidsgebied': area
            })
        
        return motions
    
    def _extract_questions(self, content: str) -> List[Dict[str, Any]]:
        """Extract GL-PvdA questions to College from notulen"""
        
        questions = []
        
        # Look for question patterns
        pattern = r"vraag.*?(?:groenlinks|pvda)[^\n\.]{30,}(?:[.!?]|$)"
        
        matches = re.finditer(pattern, content, re.IGNORECASE)
        for match in matches:
            question_text = match.group(0)[:150]
            area = self._categorize_policy_area(question_text)
            questions.append({
                'tekst': question_text,
                'beleidsgebied': area
            })
        
        return questions
    
    def _extract_votes(self, content: str) -> List[Dict[str, Any]]:
        """Extract GL-PvdA voting patterns from notulen"""
        
        votes = []
        
        # Look for voting records
        pattern = r"(voor|tegen|onthouden):\s*([^;\n]*(?:groenlinks|pvda)[^;\n]*)"
        
        matches = re.finditer(pattern, content, re.IGNORECASE)
        for match in matches:
            direction = match.group(1)
            context = match.group(2)[:100]
            area = self._categorize_policy_area(context)
            
            votes.append({
                'richting': direction.upper(),
                'context': context,
                'beleidsgebied': area
            })
        
        return votes
    
    def _categorize_policy_area(self, text: str) -> str:
        """Categorize text into policy area"""
        
        areas = {
            'wonen': ['huis', 'woning', 'huur', 'koop', 'leegstand', 'sociale'],
            'klimaat': ['klimaat', 'co2', 'duurzaam', 'energie', 'groen'],
            'mobiliteit': ['fiets', 'auto', 'verkeer', 'parkeer', 'openbaar vervoer'],
            'onderwijs': ['school', 'onderwijs', 'student', 'universiteit'],
            'zorg': ['zorg', 'gezondheid', 'jeugd', 'ouderen', 'wmo'],
            'economie': ['bedrijf', 'economie', 'werk', 'handel'],
            'veiligheid': ['veiligheid', 'politie', 'criminaliteit'],
            'inclusiviteit': ['inclusiviteit', 'discriminatie', 'migratie', 'gelijk'],
        }
        
        text_lower = text.lower()
        
        for area, keywords in areas.items():
            for keyword in keywords:
                if keyword in text_lower:
                    return area.capitalize()
        
        return 'Overig'
    
    def _combine_positions(
        self, 
        programme: Dict[str, Dict[str, Any]], 
        notulen: Dict[str, List[Dict[str, Any]]]
    ) -> Dict[str, Dict[str, Any]]:
        """Combine programme and notulen positions into unified profile"""
        
        combined = {}
        all_areas = set(list(programme.keys()) + list(notulen.keys()))
        
        for area in all_areas:
            combined[area] = {
                'beleidsgebied': area,
                'uit_programma': programme.get(area, {}).get('uit_programma', 'Niet expliciet'),
                'uit_notulen': notulen.get(area, []),
                'aantal_notulen_verwijzingen': len(notulen.get(area, [])),
                'kernwaarde': self._infer_core_value(area, programme.get(area, {})),
                'consistentie': self._assess_consistency(
                    programme.get(area, {}),
                    notulen.get(area, [])
                )
            }
        
        return combined
    
    def _infer_core_value(
        self, 
        area: str, 
        programme_info: Dict[str, Any]
    ) -> str:
        """Infer core party value from policy area"""
        
        area_lower = area.lower()
        
        values = {
            'wonen': 'Wonen als recht, niet als handelswaar',
            'klimaat': 'Ecologische duurzaamheid',
            'mobiliteit': 'Duurzaam & toegankelijk vervoer',
            'onderwijs': 'Gelijke kansen in onderwijs',
            'zorg': 'Universele gezondheidszorg',
            'economie': 'Rechtvaardige economie',
            'veiligheid': 'Veilighed voor iedereen',
            'inclusiviteit': 'Gelijke waardigheid voor allen',
        }
        
        return values.get(area_lower, 'Algemeen beleid')
    
    def _assess_consistency(
        self,
        programme_info: Dict[str, Any],
        notulen_positions: List[Dict[str, Any]]
    ) -> str:
        """Assess consistency between programme and actual notulen positions"""
        
        if not notulen_positions:
            return "Niet in notulen besproken"
        
        if programme_info and notulen_positions:
            # If party states position in programme AND voices it in council
            return "Consistent"
        elif not programme_info and notulen_positions:
            # If party voices position but didn't state it in programme
            return "Geëvolueerd/Toegevoegd"
        else:
            return "Inconsistent/Afwezig"
    
    def _extract_core_values(
        self, 
        positions: Dict[str, Dict[str, Any]]
    ) -> List[str]:
        """Extract overarching core values"""
        
        core_values = set()
        
        for area, position_info in positions.items():
            value = position_info.get('kernwaarde')
            if value and value != 'Algemeen beleid':
                core_values.add(value)
        
        return sorted(list(core_values))
    
    def _generate_summary(
        self,
        positions: Dict[str, Dict[str, Any]],
        core_values: List[str]
    ) -> Dict[str, Any]:
        """Generate summary of party profile"""
        
        return {
            'totaal_beleidsgebieden': len(positions),
            'kernwaarden': core_values,
            'beleidsgebieden_met_notulen': sum(
                1 for p in positions.values() 
                if p.get('aantal_notulen_verwijzingen', 0) > 0
            ),
            'consistentie_overall': self._calculate_overall_consistency(positions)
        }
    
    def _calculate_overall_consistency(
        self,
        positions: Dict[str, Dict[str, Any]]
    ) -> str:
        """Calculate overall consistency score"""
        
        if not positions:
            return "Onbekend"
        
        consistent = sum(
            1 for p in positions.values() 
            if p.get('consistentie') == 'Consistent'
        )
        
        ratio = consistent / len(positions)
        
        if ratio > 0.75:
            return "Hoog consistent"
        elif ratio > 0.5:
            return "Matig consistent"
        else:
            return "Laag consistent"
