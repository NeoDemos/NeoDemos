#!/usr/bin/env python3
"""
STEP 3A: Extract Detailed Policy Proposals from GroenLinks-PvdA Programme

Extracts specific, detailed proposals from the 2025 election programme:
- Exact proposal text (Dutch)
- Budget allocations
- Timeline
- Implementation mechanism
- Related proposals
- Confidence levels

Uses Gemini Flash 3 for optimal reasoning about complex policy.
"""

import os
import sys
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.proposal_extraction_service import ProposalExtractor
from dataclasses import asdict

def main():
    """Extract proposals and save results"""
    
    # Load API key from .env
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        with open(".env") as f:
            for line in f:
                if line.startswith("GEMINI_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    os.environ["GEMINI_API_KEY"] = api_key
                    break
    
    if not api_key:
        print("✗ GEMINI_API_KEY not found")
        sys.exit(1)
    
    print(f"\n{'='*70}")
    print("STAP 3A: EXTRACTIE GEDETAILLEERDE VOORSTELLEN")
    print("GroenLinks-PvdA Verkiezingsprogramma 2025-2030")
    print(f"{'='*70}\n")
    
    print(f"Model: Gemini Flash 3 (gemini-3-flash-preview)")
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        extractor = ProposalExtractor()
        
        # Extract all proposals
        database = extractor.extract_all_proposals("GroenLinks-PvdA")
        
        # Display summary
        print("\n" + "="*70)
        print("✓ EXTRACTIE VOLTOOID")
        print("="*70)
        
        db_dict = asdict(database)
        
        print(f"\nPartij: {db_dict['partij_naam']}")
        print(f"Programma: {db_dict['programma_titel']}")
        print(f"Totaal voorstellen: {db_dict['aantal_voorstellen']}")
        print(f"Totale begroting: {db_dict['totaal_budget'] or 'Niet bepaald'}")
        
        print(f"\nVoorstellen per beleidsterrein:")
        for area, proposals in db_dict['voorstellen'].items():
            print(f"  {area}: {len(proposals)} voorstellen")
        
        # Save to JSON
        output_file = "data/pipeline/groenlinks_pvda_detailed_proposals.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(db_dict, f, indent=2, default=str, ensure_ascii=False)
        
        print(f"\n✓ Voorstellen opgeslagen in: {output_file}")
        
        # Show sample proposals
        print("\nVoorbeelden van geëxtraheerde voorstellen:")
        print("-" * 70)
        
        proposal_count = 0
        for area, proposals in db_dict['voorstellen'].items():
            if proposal_count >= 3:
                break
            for proposal in proposals[:1]:
                proposal_count += 1
                print(f"\n{proposal_count}. {proposal['titel']}")
                print(f"   Terrein: {proposal['beleidsterrein']}")
                print(f"   Doelniveau: {proposal['doelniveau']}")
                print(f"   Begroting: {proposal['begroting'] or 'Niet vermeld'}")
                print(f"   Timeline: {proposal['timeline'] or 'Niet vermeld'}")
                print(f"   Tekst: {proposal['volledige_tekst'][:100]}...")
        
        print(f"\nEind time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return True
    
    except Exception as e:
        print(f"\n✗ Fout: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
