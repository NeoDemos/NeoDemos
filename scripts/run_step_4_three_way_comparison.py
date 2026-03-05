#!/usr/bin/env python3
"""
STAP 4 EXECUTIE: Drie-weg Vergelijking

Synthesizes all three layers (formal, implicit, trends) into a comprehensive
comparison with alignment scores and divergence identification.
"""

import json
import sys
from services.three_way_comparison_service import ThreeWayComparisonService

def main():
    print("\n" + "="*70)
    print("STAP 4: DRIE-WEG VERGELIJKING EXECUTIE")
    print("="*70)
    
    service = ThreeWayComparisonService()
    
    # Perform comparison
    results = service.perform_comparison()
    
    if 'error' in results:
        print(f"\n✗ {results['error']}")
        return
    
    # Save results to JSON
    output_file = 'data/pipeline/three_way_comparison_results.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n✓ Resultaten opgeslagen in: {output_file}")
    
    # Print summary
    summary = results.get('summary', {})
    print(f"\n--- SAMENVATTING ---")
    print(f"\nBeleidsgebieden Geanalyseerd: {summary.get('policy_areas_analyzed', 0)}")
    print(f"Gemiddelde Afstemming: {summary.get('policy_areas_analyzed', 0)}")
    
    print(f"\nHoogste Afstemming Gebieden:")
    for area, alignment in summary.get('highest_alignment_areas', []):
        print(f"  - {area}: {alignment['overall_alignment']}")
    
    print(f"\nLaagste Afstemming Gebieden (Divergenties):")
    for area, alignment in summary.get('lowest_alignment_areas', []):
        print(f"  - {area}: {alignment['overall_alignment']}")
    
    # Print statistical summary
    stats = results.get('statistical_summary', {})
    print(f"\n--- STATISTISCHE SAMENVATTING ---")
    print(f"Gemiddelde Overall Afstemming: {stats.get('overall_alignment_avg', 0)}")
    print(f"Layer 1 (Formeel) Gemiddelde: {stats.get('layer1_avg', 0)}")
    print(f"Layer 2 (Implicit) Gemiddelde: {stats.get('layer2_avg', 0)}")
    print(f"Layer 3 (Trends) Gemiddelde: {stats.get('layer3_avg', 0)}")
    print(f"Hoge Afstemming Gebieden: {stats.get('high_alignment_areas', 0)}")
    print(f"Lage Afstemming Gebieden: {stats.get('low_alignment_areas', 0)}")
    
    # Print key divergences
    print(f"\n--- SLEUTEL DIVERGENTIES ---")
    for div in results.get('key_divergences', [])[:3]:
        print(f"\n{div['policy_area']} (Afstemming: {div['overall_alignment']})")
        for specific in div.get('specific_divergences', []):
            print(f"  - {specific}")
    
    print(f"\n{'='*70}")
    print("STAP 4: DRIE-WEG VERGELIJKING VOLTOOID")
    print(f"{'='*70}\n")

if __name__ == '__main__':
    main()
