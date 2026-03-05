#!/usr/bin/env python3
"""
Proposal-Level Extraction Service

Extracts specific policy proposals from GroenLinks-PvdA programme with full details:
- Policy text (exact)
- Budget (if stated)
- Timeline (if stated)
- Target level (city/province/national)
- Implementation mechanism
- Related proposals

Maintains Dutch language throughout for precision.
"""

import json
import psycopg2
import os
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import google.genai as genai

# Initialize Gemini client
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    client = genai.Client(api_key=api_key)
else:
    client = None

@dataclass
class Proposal:
    """Single policy proposal with full details"""
    titel: str                    # Proposal title (Dutch)
    beleidsterrein: str          # Policy area (e.g., "Klimaat", "Wonen")
    volledige_tekst: str         # Full proposal text (Dutch)
    begroting: Optional[str]     # Budget (if stated)
    timeline: Optional[str]      # Timeline (if stated)
    doelniveau: str              # "gemeente" / "provincie" / "landelijk"
    implementatiemechanisme: str # How it will be implemented
    stakeholders: List[str]      # Key actors needed
    gerelateerde_voorstellen: List[str]  # Related proposals
    bron_pagina: Optional[int]   # Source page in programme
    bron_paragraaf: Optional[str] # Source section/paragraph
    
@dataclass
class ProposalDatabase:
    """Complete proposal database from programme"""
    partij_naam: str
    programma_titel: str
    extractiedatum: str
    aantal_voorstellen: int
    voorstellen: Dict[str, List[Proposal]]  # Organized by policy area
    totaal_budget: Optional[str]  # If summable

class ProposalExtractor:
    """Extract proposal-level details from party programme"""
    
    def __init__(self):
        self.conn = psycopg2.connect(
            "postgresql://postgres:postgres@localhost:5432/neodemos"
        )
        self.cursor = self.conn.cursor()
    
    def extract_all_proposals(self, party_name: str) -> ProposalDatabase:
        """
        Extract all detailed proposals from party programme.
        Maintains Dutch language throughout.
        """
        print(f"\n{'='*70}")
        print(f"EXTRACTEN VAN GEDETAILLEERDE VOORSTELLEN: {party_name}")
        print(f"{'='*70}\n")
        
        # Get programme from database
        self.cursor.execute(
            "SELECT pdf_content FROM party_programmes WHERE party_name = %s",
            (party_name,)
        )
        result = self.cursor.fetchone()
        
        if not result:
            raise ValueError(f"Programma niet gevonden voor {party_name}")
        
        programme_text = result[0]
        
        # Extract proposals using Gemini
        print("[1/2] Extracting proposals from programme...")
        proposals = self._extract_proposals_from_text(programme_text, party_name)
        
        # Organize by policy area
        print("[2/2] Organizing proposals by policy area...")
        organized = self._organize_by_policy_area(proposals)
        
        # Count budget if possible
        total_budget = self._calculate_total_budget(proposals)
        
        database = ProposalDatabase(
            partij_naam=party_name,
            programma_titel="Verkiezingsprogramma 2025-2030",
            extractiedatum=datetime.now().isoformat(),
            aantal_voorstellen=len(proposals),
            voorstellen=organized,
            totaal_budget=total_budget
        )
        
        print(f"\n✓ {len(proposals)} voorstellen geëxtraheerd")
        print(f"\nBeleidsterreinen vertegenwoordigd:")
        for area, proposals_in_area in organized.items():
            print(f"  - {area}: {len(proposals_in_area)} voorstellen")
        
        return database
    
    def _extract_proposals_from_text(self, text: str, party_name: str) -> List[Proposal]:
        """Extract individual proposals using Gemini"""
        
        if not client:
            raise RuntimeError("GEMINI_API_KEY not set")
        
        extraction_prompt = f"""
Je bent een expert in Nederlandse politieke programma's en beleid.

Analyseer het volgende {party_name} verkiezingsprogramma en extraheer ALLE concrete beleidsvoorstellen.

Voor elk voorstel, lever deze gegevens in Nederlands:
1. titel: Korte titel van het voorstel
2. beleidsterrein: Categorie (bijv. Klimaat, Wonen, Onderwijs, etc.)
3. volledige_tekst: De exacte tekst van het voorstel uit het programma
4. begroting: Budget (bijv. "€500 miljoen") - null als niet genoemd
5. timeline: Tijdlijn (bijv. "2025-2030") - null als niet genoemd
6. doelniveau: "gemeente" / "provincie" / "landelijk"
7. implementatiemechanisme: HOE wordt het uitgevoerd?
8. stakeholders: Lijst van betrokken partijen
9. gerelateerde_voorstellen: Andere voorstellen die hiermee verbonden zijn

Antwoord ALLEEN in geldige JSON format, met array van voorstellen:

{{
  "voorstellen": [
    {{
      "titel": "...",
      "beleidsterrein": "...",
      "volledige_tekst": "...",
      "begroting": "..." or null,
      "timeline": "...",
      "doelniveau": "...",
      "implementatiemechanisme": "...",
      "stakeholders": ["...", "..."],
      "gerelateerde_voorstellen": ["...", "..."],
      "bron_pagina": null,
      "bron_paragraaf": null
    }}
  ]
}}

PROGRAMMA TEKST:
{text[:100000]}
"""
        
        print("  Calling Gemini Flash 3 for proposal extraction...")
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=extraction_prompt
        )
        
        try:
            # Parse JSON from response
            response_text = response.text
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0]
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0]
            
            data = json.loads(response_text)
            proposals = [Proposal(**p) for p in data.get("voorstellen", [])]
            
            print(f"  ✓ {len(proposals)} voorstellen geëxtraheerd")
            return proposals
        
        except json.JSONDecodeError as e:
            print(f"  ✗ JSON parsing error: {e}")
            return []
    
    def _organize_by_policy_area(self, proposals: List[Proposal]) -> Dict[str, List[Proposal]]:
        """Organize proposals by policy area"""
        organized = {}
        for proposal in proposals:
            area = proposal.beleidsterrein
            if area not in organized:
                organized[area] = []
            organized[area].append(proposal)
        return organized
    
    def _calculate_total_budget(self, proposals: List[Proposal]) -> Optional[str]:
        """Try to sum total budget from all proposals"""
        total = 0
        for proposal in proposals:
            if proposal.begroting and "miljoen" in proposal.begroting.lower():
                try:
                    amount = float(proposal.begroting.lower().replace("€", "").replace("miljoen", "").strip())
                    total += amount
                except ValueError:
                    pass
        
        if total > 0:
            return f"€{total:.0f} miljoen"
        return None
