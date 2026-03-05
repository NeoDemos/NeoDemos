#!/usr/bin/env python3
"""
STAP 3C: Notulen Position Inference Execution

Analyzes Rotterdam Gemeenteraad notulen to infer implicit College B&W positions
by examining:
- Wethouder responses to GroenLinks-PvdA initiatives
- Voting patterns and behavior
- Budget allocation signals
- Explicit policy direction statements

Treats GroenLinks-PvdA as ONE unified party (formele fusie juni 2026).
"""

import os
import sys
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.notulen_position_inference_service import NotulenPositionInferenceService

def main():
    """Execute position inference"""
    
    print(f"\n{'='*70}")
    print("STAP 3C: NOTULEN POSITIE INFERENTIE")
    print("College B&W Impliciete Standpunten uit Raadsnotulen")
    print(f"{'='*70}\n")
    
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("Party analyzed: GroenLinks-PvdA (unified)")
    print("Contrasted with: College B&W\n")
    
    try:
        service = NotulenPositionInferenceService()
        
        # Run inference
        results = service.infer_positions_from_notulen()
        
        # Display summary
        print("\n" + "="*70)
        print("✓ INFERENTIE VOLTOOID")
        print("="*70)
        
        total = results['samenvatting']['totaal_posities']
        print(f"\nTotaal inferente posities: {total}")
        
        print(f"\nPer bewijs type:")
        for btype, count in results['samenvatting']['per_bewijs_type'].items():
            print(f"  - {btype}: {count}")
        
        print(f"\nPer sterkte:")
        for strength, count in results['samenvatting']['per_sterkte'].items():
            print(f"  - {strength}: {count}")
        
        print(f"\nBeleidsterreinen vertegenwoordigd:")
        for area in sorted(results['samenvatting']['beleidsterreinen']):
            count = results['per_beleidsterrein'].get(area, 0)
            print(f"  - {area}: {count} inferenties")
        
        # Save results
        output_file = "data/pipeline/college_bw_implicit_positions.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str, ensure_ascii=False)
        
        print(f"\n✓ Resultaten opgeslagen in: {output_file}")
        
        # Show sample inferences
        if results['posities']:
            print("\nVoorbeelden van inferente posities:")
            print("-" * 70)
            for i, pos in enumerate(results['posities'][:3], 1):
                print(f"\n{i}. {pos['beleidsterrein']} - {pos['bewijs_type']}")
                print(f"   Positie: {pos['positie_omschrijving']}")
                print(f"   Sterkte: {pos['sterkte']} (confidence: {pos['confidence']:.0%})")
                print(f"   Context: \"{pos['context_preview'][:80]}...\"")
        
        print(f"\nEnd time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return True
    
    except Exception as e:
        print(f"\n✗ Fout: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
