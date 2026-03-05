#!/usr/bin/env python3
"""
STAP 5: Eindrapport Generatie

Generates a comprehensive Dutch policy alignment report synthesizing:
- GL-PvdA's official programme promises
- Their actual behavior in city council
- Frequency patterns and consistency
- Key divergence areas with evidence

Uses Gemini Flash 3 Preview for high-quality policy analysis and reporting.
"""

import json
import google.genai as genai
from typing import Dict, Any
from datetime import datetime
import os

class FinalReportService:
    """Generate final comprehensive policy report"""
    
    def __init__(self):
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")
        
        self.client = genai.Client(api_key=api_key)
        self.model_id = 'gemini-3-flash-preview'
        
        # Load all data
        self.comparison_data = None
        self.glpvda_programme = None
        self.college_proposals = None
        self.glpvda_notulen = None
        self.college_implicit = None
        self.trends = None
    
    def load_data(self) -> bool:
        """Load all necessary data"""
        try:
            with open('data/pipeline/three_way_comparison_results.json', 'r') as f:
                self.comparison_data = json.load(f)
            
            with open('data/pipeline/groenlinks_pvda_detailed_proposals.json', 'r') as f:
                self.glpvda_programme = json.load(f)
            
            with open('data/pipeline/raadsvoorstel_2024_2025.json', 'r') as f:
                self.college_proposals = json.load(f)
            
            with open('data/pipeline/groenlinks_pvda_notulen_positions.json', 'r') as f:
                self.glpvda_notulen = json.load(f)
            
            with open('data/pipeline/college_bw_implicit_positions.json', 'r') as f:
                self.college_implicit = json.load(f)
            
            with open('data/pipeline/trend_analysis_results.json', 'r') as f:
                self.trends = json.load(f)
            
            return True
        except Exception as e:
            print(f"Fout bij laden data: {e}")
            return False
    
    def generate_report(self) -> Dict[str, Any]:
        """Generate final comprehensive report"""
        
        print(f"\n{'='*70}")
        print("STAP 5: EINDRAPPORT GENERATIE")
        print("Vergelijking: GroenLinks-PvdA Programma vs Praktijk")
        print(f"{'='*70}\n")
        
        # Load data
        print("[1/4] Loading all analysis data...")
        if not self.load_data():
            return {'error': 'Could not load data'}
        print("  ✓ Data loaded successfully")
        
        # Generate executive summary
        print("\n[2/4] Generating executive summary...")
        executive_summary = self._generate_executive_summary()
        
        # Generate detailed findings
        print("\n[3/4] Generating detailed findings...")
        detailed_findings = self._generate_detailed_findings()
        
        # Generate conclusions and recommendations
        print("\n[4/4] Generating conclusions and recommendations...")
        conclusions = self._generate_conclusions()
        
        # Compile report
        report = {
            'rapport_type': 'Vergelijking Programma vs Praktijk',
            'partij': 'GroenLinks-PvdA',
            'gemeente': 'Rotterdam',
            'generatie_datum': datetime.now().isoformat(),
            'taal': 'Nederlands',
            'samenvatting': executive_summary,
            'gedetailleerde_bevindingen': detailed_findings,
            'conclusies_aanbevelingen': conclusions,
            'metadata': {
                'totaal_beleidsgebieden': self.comparison_data['summary']['policy_areas_analyzed'],
                'gemiddelde_afstemming': self.comparison_data['statistical_summary']['overall_alignment_avg'],
                'layer1_afstemming': self.comparison_data['statistical_summary']['layer1_avg'],
                'layer2_afstemming': self.comparison_data['statistical_summary']['layer2_avg'],
                'layer3_afstemming': self.comparison_data['statistical_summary']['layer3_avg'],
            }
        }
        
        return report
    
    def _generate_executive_summary(self) -> str:
        """Generate executive summary using Gemini"""
        
        # Prepare context
        stats = self.comparison_data['statistical_summary']
        
        prompt = f"""Je bent een politieke analist voor de gemeente Rotterdam. Genereer een korte, professionele samenvatting (max 150 woorden) van de vergelijking tussen GroenLinks-PvdA's verkiezingsprogramma en hun werkelijke gedrag in de gemeenteraad.

Gegeven data:
- Totaal onderzochte beleidsgebieden: {stats['total_policy_areas']}
- Gemiddelde afstemming (0.0-1.0): {stats['overall_alignment_avg']}
- Layer 1 (Formeel - programma vs raadsvoorstellen): {stats['layer1_avg']}
- Layer 2 (Implicit - programma vs daadwerkelijk gedrag in notulen): {stats['layer2_avg']}
- Layer 3 (Trends - frequentiepatronen): {stats['layer3_avg']}
- Beleidsgebieden met hoge afstemming: {stats['high_alignment_areas']}
- Beleidsgebieden met lage afstemming (divergenties): {stats['low_alignment_areas']}

Schrijf in Nederlands. Wees objectief en voeg geen speculatie toe."""
        
        try:
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt
            )
            return response.text
        except Exception as e:
            print(f"  ⚠ Fout bij generatie samenvatting: {e}")
            return "Samenvatting kon niet worden gegenereerd."
    
    def _generate_detailed_findings(self) -> str:
        """Generate detailed findings by policy area"""
        
        # Get top divergences
        divergences = self.comparison_data.get('key_divergences', [])
        
        prompt = f"""Je bent een politieke analist. Genereer een gedetailleerde analyse van de volgende divergentiegebieden tussen GroenLinks-PvdA's programma en praktijk:

{json.dumps(divergences[:3], ensure_ascii=False, indent=2)}

Voor elk gebied: verklaar de divergentie en geef concrete voorbeelden. Schrijf in Nederlands. Maak het begrijpelijk voor beleidsmakers. Max 400 woorden."""
        
        try:
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt
            )
            return response.text
        except Exception as e:
            print(f"  ⚠ Fout bij generatie bevindingen: {e}")
            return "Gedetailleerde bevindingen konden niet worden gegenereerd."
    
    def _generate_conclusions(self) -> str:
        """Generate conclusions and recommendations"""
        
        stats = self.comparison_data['statistical_summary']
        
        prompt = f"""Je bent een politieke analist. Geef professionele conclusies en aanbevelingen gebaseerd op deze analyse van GroenLinks-PvdA in Rotterdam:

Statistieken:
- Gemiddelde afstemming programma vs praktijk: {stats['overall_alignment_avg']}
- Formele afstemming: {stats['layer1_avg']}
- Impliciete afstemming (daadwerkelijk gedrag): {stats['layer2_avg']}
- Frequentiepatronen consistent?: {'Ja' if stats['layer3_avg'] > 0.5 else 'Nee'}

Geef:
1. Korte samenvattende conclusie (3 punten max)
2. Aanbevelingen voor verbetering transparantie/consistentie
3. Aanbevelingen voor kiezer/journalist onderzoek

Schrijf in Nederlands. Wees constructief en objectief."""
        
        try:
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt
            )
            return response.text
        except Exception as e:
            print(f"  ⚠ Fout bij generatie conclusies: {e}")
            return "Conclusies konden niet worden gegenereerd."
    
    def save_report_html(self, report: Dict[str, Any], filename: str = 'Eindrapport_Programma_vs_Praktijk.html'):
        """Save report as HTML"""
        
        html = f"""<!DOCTYPE html>
<html lang="nl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{report['rapport_type']} - {report['partij']}</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 40px; line-height: 1.6; max-width: 900px; }}
        h1, h2 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
        .metadata {{ background: #ecf0f1; padding: 15px; border-radius: 5px; margin: 20px 0; }}
        .score {{ font-weight: bold; color: #e74c3c; }}
        .section {{ margin: 30px 0; }}
        .footer {{ margin-top: 40px; color: #7f8c8d; font-size: 0.9em; }}
    </style>
</head>
<body>
    <h1>{report['rapport_type']}</h1>
    <p><strong>Partij:</strong> {report['partij']}</p>
    <p><strong>Gemeente:</strong> {report['gemeente']}</p>
    <p><strong>Datum:</strong> {report['generatie_datum']}</p>
    
    <div class="metadata">
        <h3>Sleutelcijfers</h3>
        <p><strong>Beleidsgebieden onderzocht:</strong> {report['metadata']['totaal_beleidsgebieden']}</p>
        <p><strong>Gemiddelde afstemming:</strong> <span class="score">{report['metadata']['gemiddelde_afstemming']:.1%}</span></p>
        <p><strong>Layer 1 (Formeel):</strong> {report['metadata']['layer1_afstemming']:.1%}</p>
        <p><strong>Layer 2 (Implicit):</strong> {report['metadata']['layer2_afstemming']:.1%}</p>
        <p><strong>Layer 3 (Trends):</strong> {report['metadata']['layer3_afstemming']:.1%}</p>
    </div>
    
    <div class="section">
        <h2>Samenvatting</h2>
        <p>{report['samenvatting']}</p>
    </div>
    
    <div class="section">
        <h2>Gedetailleerde Bevindingen</h2>
        <p>{report['gedetailleerde_bevindingen']}</p>
    </div>
    
    <div class="section">
        <h2>Conclusies en Aanbevelingen</h2>
        <p>{report['conclusies_aanbevelingen']}</p>
    </div>
    
    <div class="footer">
        <p>Rapport gegenereerd door NeoDemos Policy Analysis System</p>
        <p>Analyse gebaseerd op GroenLinks-PvdA verkiezingsprogramma (2025), Rotterdam Gemeenteraad notulen, en College B&W voorstellen.</p>
    </div>
</body>
</html>"""
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(html)
        
        print(f"  ✓ HTML-rapport opgeslagen: {filename}")
    
    def save_report_json(self, report: Dict[str, Any], filename: str = 'Eindrapport_Programma_vs_Praktijk.json'):
        """Save report as JSON"""
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        print(f"  ✓ JSON-rapport opgeslagen: {filename}")
