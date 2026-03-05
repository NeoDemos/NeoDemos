#!/usr/bin/env python3
"""
STAP 3D EXECUTIE: Trend Analyse

Analyzes frequency patterns and consistency to identify sustained vs situational
policy conflicts between GL-PvdA positions and College B&W positions.
"""

import json
import sys
from services.trend_analysis_service import TrendAnalysisService

def main():
    print("\n" + "="*70)
    print("STAP 3D: TREND ANALYSE EXECUTIE")
    print("="*70)
    
    service = TrendAnalysisService()
    
    # Analyze trends
    results = service.analyze_trends()
    
    # Save results to JSON
    output_file = 'data/pipeline/trend_analysis_results.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n✓ Resultaten opgeslagen in: {output_file}")
    
    # Print summary
    if results['samenvatting']:
        print(f"\n--- SAMENVATTING ---")
        print(f"\nGL-PvdA Top Beleidsgebieden:")
        for item in results['samenvatting'].get('glpvda_top_areas', []):
            print(f"  - {item['area']}: {item['frequency']} posities")
        
        print(f"\nCollege Top Beleidsgebieden:")
        for item in results['samenvatting'].get('college_top_areas', []):
            print(f"  - {item['area']}: {item['frequency']} posities")
        
        print(f"\nHoogste Divergentie Gebieden:")
        for item in results['samenvatting'].get('highest_divergence_areas', []):
            print(f"  - {item['area']}: {item['divergence']} (0.0=identiek, 1.0=tegengesteld)")
        
        print(f"\nTotaal Unieke Beleidsgebieden: {results['samenvatting'].get('total_unique_areas', 0)}")
    
    print(f"\n{'='*70}")
    print("STAP 3D: TREND ANALYSE VOLTOOID")
    print(f"{'='*70}\n")

if __name__ == '__main__':
    main()
