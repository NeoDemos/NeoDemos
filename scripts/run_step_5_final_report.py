#!/usr/bin/env python3
"""
STAP 5 EXECUTIE: Eindrapport Generatie

Generates a comprehensive Dutch language report on GroenLinks-PvdA's
programme promises vs actual practice in Rotterdam city council.

Uses Gemini Flash 3 for high-quality policy analysis and synthesis.
"""

import json
import sys
from services.final_report_service import FinalReportService

def main():
    print("\n" + "="*70)
    print("STAP 5: EINDRAPPORT GENERATIE")
    print("Vergelijking: GroenLinks-PvdA Programma vs Praktijk")
    print("="*70)
    
    service = FinalReportService()
    
    # Generate comprehensive report
    report = service.generate_report()
    
    if 'error' in report:
        print(f"\n✗ {report['error']}")
        return
    
    # Save in both JSON and HTML formats
    service.save_report_json(report)
    service.save_report_html(report)
    
    # Print summary to console
    print(f"\n--- RAPPORT SAMENVATTING ---\n")
    print(f"Partij: {report['partij']}")
    print(f"Gemeente: {report['gemeente']}")
    print(f"Beleidsgebieden onderzocht: {report['metadata']['totaal_beleidsgebieden']}")
    print(f"Gemiddelde afstemming programma vs praktijk: {report['metadata']['gemiddelde_afstemming']:.1%}")
    print(f"\nLayer-specificatie afstemming:")
    print(f"  Layer 1 (Formeel - programma vs raadsvoorstellen): {report['metadata']['layer1_afstemming']:.1%}")
    print(f"  Layer 2 (Implicit - programma vs notulen gedrag): {report['metadata']['layer2_afstemming']:.1%}")
    print(f"  Layer 3 (Trends - frequentiepatronen): {report['metadata']['layer3_afstemming']:.1%}")
    
    print(f"\n--- SAMENVATTING ---")
    print(report['samenvatting'][:300] + "...")
    
    print(f"\n{'='*70}")
    print("STAP 5: EINDRAPPORT VOLTOOID")
    print("Bestanden gegenereerd:")
    print("  - Eindrapport_Programma_vs_Praktijk.json")
    print("  - Eindrapport_Programma_vs_Praktijk.html")
    print(f"{'='*70}\n")

if __name__ == '__main__':
    main()
