#!/usr/bin/env python3
"""
Proposal Comparison Service

Compares:
1. GroenLinks proposals vs College B&W raadsvoorstel
2. GroenLinks proposals vs Council initiatiefvoorstel
3. Tracks outcomes and policy divergence

Maintains Dutch language throughout.
Uses Gemini Flash 3 for nuanced comparison (when available).
"""

import json
import os
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import google.genai as genai

api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key) if api_key else None

@dataclass
class ComparisonResult:
    """Result of comparing two proposals"""
    groenlinks_voorstel_id: str
    groenlinks_titel: str
    raadsvoorstel_id: str
    raadsvoorstel_titel: str
    alignment_type: str  # "aligned" / "conflicting" / "complementary" / "unrelated"
    alignment_score: float  # 0.0 (opposite) to 1.0 (identical)
    values_alignment: float  # How well do underlying values match
    proposal_alignment: float  # How well do concrete proposals match
    verschil_detail: str  # Dutch description of differences
    college_respons: Optional[str]  # How College B&W responded
    confidence: float  # How confident in the comparison
    notities: str

class ProposalComparator:
    """Compare proposals across sources"""
    
    def compare_groenlinks_vs_raadsvoorstel(
        self,
        gl_proposals: List[Dict[str, Any]],
        raadsvoorstel_list: List[Dict[str, Any]]
    ) -> List[ComparisonResult]:
        """
        Compare GroenLinks programme proposals with actual raadsvoorstel
        """
        
        print(f"\n{'='*70}")
        print("VERGELIJKING: GROENLINKS PROGRAMMA VS RAADSVOORSTEL")
        print(f"{'='*70}\n")
        
        if not client:
            print("✗ GEMINI_API_KEY not set - using fallback comparison")
            return self._fallback_comparison(gl_proposals, raadsvoorstel_list)
        
        print(f"[1/2] Analyzing {len(gl_proposals)} GroenLinks proposals...")
        print(f"[2/2] Comparing against {len(raadsvoorstel_list)} raadsvoorstel...")
        
        comparisons = []
        
        # For each GroenLinks proposal, find related raadsvoorstel
        for gl in gl_proposals[:5]:  # Start with first 5 for speed
            related = self._find_related_raadsvoorstel(gl, raadsvoorstel_list)
            
            if related:
                for rv in related:
                    comparison = self._compare_pair(gl, rv)
                    comparisons.append(comparison)
        
        print(f"\n✓ Generated {len(comparisons)} comparisons")
        return comparisons
    
    def _find_related_raadsvoorstel(
        self, 
        gl_proposal: Dict[str, Any],
        raadsvoorstel_list: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Find raadsvoorstel that relate to the GroenLinks proposal"""
        
        gl_area = gl_proposal.get("beleidsterrein", "")
        related = []
        
        for rv in raadsvoorstel_list:
            rv_area = rv.get("beleidsterrein", "")
            if gl_area.lower() in rv_area.lower() or rv_area.lower() in gl_area.lower():
                related.append(rv)
        
        return related[:3]  # Max 3 related proposals per GL proposal
    
    def _compare_pair(
        self,
        gl_proposal: Dict[str, Any],
        raadsvoorstel: Dict[str, Any]
    ) -> ComparisonResult:
        """Compare a single pair of proposals"""
        
        if not client:
            return self._fallback_pair_comparison(gl_proposal, raadsvoorstel)
        
        comparison_prompt = f"""
Je bent een expert in Nederlandse stadsbestuurskunde. Vergelijk twee voorstellen diepgaand.

GROENLINKS VOORSTEL:
Titel: {gl_proposal.get('titel', 'N/A')}
Beleidsterrein: {gl_proposal.get('beleidsterrein', 'N/A')}
Voorstel: {gl_proposal.get('volledige_tekst', 'N/A')[:500]}
Begroting: {gl_proposal.get('begroting', 'Niet vermeld')}
Timeline: {gl_proposal.get('timeline', 'Niet vermeld')}

RAADSVOORSTEL (College B&W):
Titel: {raadsvoorstel.get('titel', 'N/A')}
Beleidsterrein: {raadsvoorstel.get('beleidsterrein', 'N/A')}
Voorstel: {raadsvoorstel.get('volledige_tekst', 'N/A')[:500]}
Begroting: {raadsvoorstel.get('begroting', 'Niet vermeld')}

Lever je analyse in Nederlands JSON format:

{{
  "alignment_type": "aligned" / "conflicting" / "complementary" / "unrelated",
  "alignment_score": 0.0-1.0,
  "values_alignment": 0.0-1.0,
  "proposal_alignment": 0.0-1.0,
  "verschil_detail": "Samenvatting van de verschillen in Nederlands",
  "college_respons": "Verwachte College-reactie op dit verschil",
  "confidence": 0.0-1.0,
  "notities": "Aanvullende observaties"
}}
"""
        
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=comparison_prompt
            )
            
            response_text = response.text
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0]
            
            data = json.loads(response_text)
            
            return ComparisonResult(
                groenlinks_voorstel_id=gl_proposal.get("id", ""),
                groenlinks_titel=gl_proposal.get("titel", ""),
                raadsvoorstel_id=raadsvoorstel.get("id", ""),
                raadsvoorstel_titel=raadsvoorstel.get("titel", ""),
                alignment_type=data.get("alignment_type", "unrelated"),
                alignment_score=data.get("alignment_score", 0.0),
                values_alignment=data.get("values_alignment", 0.0),
                proposal_alignment=data.get("proposal_alignment", 0.0),
                verschil_detail=data.get("verschil_detail", ""),
                college_respons=data.get("college_respons"),
                confidence=data.get("confidence", 0.0),
                notities=data.get("notities", "")
            )
        
        except Exception as e:
            print(f"  ⚠️  Error comparing: {e}")
            return self._fallback_pair_comparison(gl_proposal, raadsvoorstel)
    
    def _fallback_comparison(
        self,
        gl_proposals: List[Dict[str, Any]],
        raadsvoorstel_list: List[Dict[str, Any]]
    ) -> List[ComparisonResult]:
        """Fallback: simple area-based comparison"""
        
        comparisons = []
        
        for gl in gl_proposals[:5]:
            for rv in raadsvoorstel_list:
                if gl.get("beleidsterrein") == rv.get("beleidsterrein"):
                    comparisons.append(
                        ComparisonResult(
                            groenlinks_voorstel_id=gl.get("id", ""),
                            groenlinks_titel=gl.get("titel", ""),
                            raadsvoorstel_id=rv.get("id", ""),
                            raadsvoorstel_titel=rv.get("titel", ""),
                            alignment_type="same_area",
                            alignment_score=0.5,
                            values_alignment=0.5,
                            proposal_alignment=0.5,
                            verschil_detail="Basis gelijkenis op beleidsgebied",
                            college_respons=None,
                            confidence=0.3,
                            notities="Fallback comparison - volledige analyse nodig"
                        )
                    )
        
        return comparisons
    
    def _fallback_pair_comparison(
        self,
        gl_proposal: Dict[str, Any],
        raadsvoorstel: Dict[str, Any]
    ) -> ComparisonResult:
        """Fallback: simple pair comparison"""
        
        return ComparisonResult(
            groenlinks_voorstel_id=gl_proposal.get("id", ""),
            groenlinks_titel=gl_proposal.get("titel", ""),
            raadsvoorstel_id=raadsvoorstel.get("id", ""),
            raadsvoorstel_titel=raadsvoorstel.get("titel", ""),
            alignment_type="unrelated",
            alignment_score=0.0,
            values_alignment=0.0,
            proposal_alignment=0.0,
            verschil_detail="Fallback comparison nodig",
            college_respons=None,
            confidence=0.0,
            notities="API niet beschikbaar"
        )
