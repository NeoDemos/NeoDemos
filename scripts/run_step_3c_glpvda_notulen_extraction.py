#!/usr/bin/env python3
"""
STAP 3C UITGEBREID EXECUTIE: GroenLinks-PvdA Notulen Positie Extractie

Extracts actual GroenLinks-PvdA positions from Rotterdam Gemeenteraad notulen
to compare with their official programme promises (from Step 3A).

This completes the "Implicit Layer" of the three-layer analysis.
"""

import json
import sys
from services.groenlinks_pvda_notulen_extraction_service import GLPvdANotulenExtractionService

def main():
    print("\n" + "="*70)
    print("STAP 3C UITGEBREID: GROENLINKS-PVDA NOTULEN EXTRACTIE")
    print("="*70)
    
    service = GLPvdANotulenExtractionService()
    
    # Extract GL-PvdA positions from notulen
    results = service.extract_positions_from_notulen()
    
    # Save results to JSON
    output_file = 'data/pipeline/groenlinks_pvda_notulen_positions.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n✓ Resultaten opgeslagen in: {output_file}")
    print(f"  Totale posities: {results['samenvatting'].get('totaal_posities', 0)}")
    
    # Print summary
    if results['samenvatting']:
        print(f"\n--- SAMENVATTING ---")
        print(f"Per activiteit type:")
        for atype, count in results['samenvatting'].get('per_activiteit_type', {}).items():
            print(f"  - {atype}: {count}")
        
        print(f"\nPer beleidsterrein:")
        for area, count in results['samenvatting'].get('per_beleidsterrein', {}).items():
            print(f"  - {area}: {count}")
        
        print(f"\nPer sterkte:")
        for strength, count in results['samenvatting'].get('per_sterkte', {}).items():
            print(f"  - {strength}: {count}")
    
    print(f"\n{'='*70}")
    print("STAP 3C UITGEBREID: VOLTOOID")
    print(f"{'='*70}\n")

if __name__ == '__main__':
    main()
