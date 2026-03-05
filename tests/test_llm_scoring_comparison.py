#!/usr/bin/env python3
"""
Test and Compare LLM-Enhanced Alignment Scoring vs Heuristic Scoring

This test runs real Rotterdam agenda items through both the old heuristic
scoring and the new LLM-based semantic scoring to measure improvements.
"""

import json
import time
import os
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from services.policy_lens_evaluation_service import PolicyLensEvaluationService
from services.llm_alignment_scorer import LLMAlignmentScorer


class ScoringComparison:
    """Compare heuristic vs LLM scoring approaches"""
    
    def __init__(self):
        self.party_name = "GroenLinks-PvdA"
        self.results = {
            'timestamp': datetime.now().isoformat(),
            'party': self.party_name,
            'comparisons': [],
            'summary': {}
        }
    
    def load_test_cases(self) -> list:
        """Load real Rotterdam agenda items for testing"""
        
        test_cases = [
            {
                'title': 'Klimaatbeleid 2025-2030',
                'text': '''
                Agendapunt: Aanname Klimaatbeleid Rotterdam 2025-2030
                
                Het College stelt voor om het nieuwe klimaatbeleid aan te nemen,
                gericht op:
                - 50% CO2-reductie tegen 2030
                - Transitie naar hernieuwbare energie in stadsverwarmingsnetwerk
                - Groenste stad van Nederland
                - 100 hectare groenere openbare ruimte toevoegen
                - Electrificatie van gemeentelijk wagenpark tegen 2027
                - Stimulering duurzame mobiliteit en fietsinfrastructuur
                - Circulaire economie in bouw- en sloopafval
                
                Dit wordt gefinancierd via:
                - EU-subsidies (€2 miljoen)
                - Gemeente begroting (€1.5 miljoen)
                - Private investeringen (€3 miljoen)
                
                Nadelen volgens oppositie:
                - Te veel lasten voor ondernemers
                - Beperkte ondersteuning MKB
                - Transitiekosten voor werknemers onvoldoende opvangen
                '''
            },
            {
                'title': 'Herstructurering Nachtleven',
                'text': '''
                Agendapunt: Commercialisering nachtlevenszone Meent
                
                Het College proposeert:
                - Versoepeling horecavergunningen in centrumgebied
                - Privatisering van openbare terassen
                - Toename maximale bezetting bars/clubs van 500 naar 800
                - Deregulering geluidsvoorschriften
                - Exclusieve exploitatie aan twee grote horecabedrijven
                
                Voordelen:
                - Verwachte inkomsten €500k per jaar voor gemeente
                - Meer werkgelegenheid (geschat 200-300 banen)
                - Opwaardering openbare ruimte
                
                Gefinancierd door commerciële exploitant.
                '''
            },
            {
                'title': 'Woningbouwprogramma Sociaal',
                'text': '''
                Agendapunt: Uitvoering Sociaal Woningbouwprogramma 2025-2030
                
                Voorstel voor:
                - 2000 sociale huurwoningen (max €1000/maand)
                - 500 maatschappelijk vastgoed voor LOKB/hulpverlening
                - Stopzetting privatisering gemeentelijk woningbezit
                - Bezettingsbeleid: prioriteit laaginkomengroepen
                - Samenwerkingsverband Amsterdam-Rotterdam voor kennisdeling
                - Ondersteuning woningenmarkt kwetsbaren (daklozen, jongeren)
                
                Financiering:
                - Staatssteun 60%
                - Gemeentebegroting 40%
                - Betrokkenheid corporaties en volkshuisvesting
                '''
            },
            {
                'title': 'Bezuiniging Publieke Diensten',
                'text': '''
                Agendapunt: Optimalisering Gemeentelijke Organisatie
                
                College stelt voor:
                - Centralisatie gemeentelijke diensten (5 loketten → 1 mega-loket)
                - Sluiting 8 buurtcentra (besparing €2 miljoen)
                - Digitalisering alle gemeente-processen
                - Functioneringseisen medewerkers verhogen
                - Reducatie ambtelijk apparaat 20% (200-250 banen)
                - Outsourcing hulpverlening naar private contractoren
                - Privatisering parkeergarages
                
                Besparing: €8 miljoen structureel
                Gevolgen: Minder bereikbaarheid voor ouderen/hulpbehoevenden
                '''
            }
        ]
        
        return test_cases
    
    def load_party_profile(self) -> dict:
        """Load the party profile"""
        try:
            with open('data/profiles/party_profile_glpvda_corrected.json', 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            print("⚠ Party profile not found - using minimal test profile")
            return {
                'partij': 'GroenLinks-PvdA',
                'kernwaarden': [
                    'Duurzaamheid en klimaatactie',
                    'Sociale gelijkheid en inclusie',
                    'Publieke voorzieningen',
                    'Werknemersbescherming',
                    'Participatieve democratie'
                ],
                'posities': {}
            }
    
    def run_comparison_test(self) -> dict:
        """Run the full comparison test"""
        
        print("\n" + "="*80)
        print("LLM SCORING ENHANCEMENT - COMPARISON TEST")
        print("="*80 + "\n")
        
        # Load test data
        test_cases = self.load_test_cases()
        party_profile = self.load_party_profile()
        
        print(f"Party: {self.party_name}")
        print(f"Test cases: {len(test_cases)}")
        print(f"Party core values: {len(party_profile.get('kernwaarden', []))}\n")
        
        # Initialize services
        try:
            llm_scorer = LLMAlignmentScorer(party_name=self.party_name)
            print("✓ LLM scorer initialized\n")
        except Exception as e:
            print(f"✗ LLM scorer failed to initialize: {e}")
            print("  Proceeding with heuristic comparison only\n")
            llm_scorer = None
        
        policy_service = PolicyLensEvaluationService(party_name=self.party_name)
        policy_service.party_profile = party_profile
        
        # Run comparison on each test case
        for i, test_case in enumerate(test_cases, 1):
            print(f"\n[TEST {i}/{len(test_cases)}] {test_case['title']}")
            print("-" * 80)
            
            comparison = self._compare_single_agenda_item(
                test_case,
                party_profile,
                policy_service,
                llm_scorer
            )
            
            self.results['comparisons'].append(comparison)
            
            # Display results
            self._display_comparison_results(comparison)
        
        # Generate summary
        self._generate_summary()
        
        # Save results
        self._save_results()
        
        return self.results
    
    def _compare_single_agenda_item(
        self,
        test_case: dict,
        party_profile: dict,
        policy_service,
        llm_scorer
    ) -> dict:
        """Compare heuristic vs LLM scoring for a single agenda item"""
        
        comparison = {
            'title': test_case['title'],
            'heuristic': None,
            'llm': None,
            'difference': None,
            'quality_improvement': None,
            'timing': {}
        }
        
        # Get heuristic score
        start_time = time.time()
        heuristic_score = policy_service._heuristic_alignment_score(
            party_vision='',
            policy_text=test_case['text'],
            core_values=party_profile.get('kernwaarden', []),
            policy_area='Diverse'
        )
        heuristic_time = time.time() - start_time
        comparison['heuristic'] = heuristic_score
        comparison['timing']['heuristic_ms'] = round(heuristic_time * 1000, 2)
        
        # Get LLM score if available
        if llm_scorer:
            start_time = time.time()
            try:
                llm_score = llm_scorer.score_alignment(
                    party_position='Duurzaamheid, sociale inclusie, en publieke voorzieningen',
                    party_core_values=party_profile.get('kernwaarden', []),
                    rotterdam_policy=test_case['text'],
                    policy_area='Mixed'
                )
                llm_time = time.time() - start_time
                comparison['llm'] = llm_score
                comparison['timing']['llm_ms'] = round(llm_time * 1000, 2)
                
                # Calculate difference
                comparison['difference'] = round(
                    llm_score.get('score', 0.5) - heuristic_score.get('score', 0.5),
                    2
                )
                
                # Quality assessment
                comparison['quality_improvement'] = self._assess_quality_improvement(
                    heuristic_score,
                    llm_score
                )
                
            except Exception as e:
                print(f"  ⚠ LLM scoring failed: {e}")
                comparison['llm'] = {'error': str(e)}
        
        return comparison
    
    def _assess_quality_improvement(self, heuristic: dict, llm: dict) -> dict:
        """Assess quality improvements from LLM scoring"""
        
        improvements = {
            'has_reasoning': bool(llm.get('analyse')),
            'has_strong_points': len(llm.get('sterke_punten', [])) > 0,
            'has_weak_points': len(llm.get('kritische_punten', [])) > 0,
            'has_recommendations': len(llm.get('aanbevelingen', [])) > 0,
            'semantic_depth': 'detailed' if len(llm.get('analyse', '')) > 200 else 'brief'
        }
        
        improvement_score = sum([
            improvements['has_reasoning'],
            improvements['has_strong_points'],
            improvements['has_weak_points'],
            improvements['has_recommendations']
        ]) / 4.0
        
        improvements['overall_score'] = round(improvement_score, 2)
        
        return improvements
    
    def _display_comparison_results(self, comparison: dict):
        """Display results for a single comparison"""
        
        heuristic = comparison['heuristic']
        llm = comparison['llm']
        
        # Heuristic score
        print(f"\n  HEURISTIC SCORING:")
        print(f"    Score: {heuristic.get('score', 'N/A')}/1.0")
        print(f"    Interpretation: {heuristic.get('interpretatie', 'N/A')}")
        print(f"    Time: {comparison['timing'].get('heuristic_ms', 'N/A')} ms")
        
        # LLM score
        if llm and 'error' not in llm:
            print(f"\n  LLM-BASED SCORING:")
            print(f"    Score: {llm.get('score', 'N/A')}/1.0")
            print(f"    Interpretation: {llm.get('interpretatie', 'N/A')}")
            print(f"    Time: {comparison['timing'].get('llm_ms', 'N/A')} ms")
            
            # Analysis details
            if llm.get('analyse'):
                analysis_preview = llm['analyse'][:150] + "..." if len(llm['analyse']) > 150 else llm['analyse']
                print(f"    Analysis: {analysis_preview}")
            
            # Strong points
            if llm.get('sterke_punten'):
                print(f"    ✓ Strong points: {len(llm['sterke_punten'])} identified")
            
            # Critical points
            if llm.get('kritische_punten'):
                print(f"    ✗ Critical points: {len(llm['kritische_punten'])} identified")
            
            # Difference
            if comparison['difference'] is not None:
                diff = comparison['difference']
                symbol = "↑" if diff > 0 else "↓" if diff < 0 else "→"
                print(f"\n  DIFFERENCE: {symbol} {abs(diff):.2f} points")
            
            # Quality improvement
            if comparison['quality_improvement']:
                qa = comparison['quality_improvement']
                print(f"\n  QUALITY IMPROVEMENTS:")
                print(f"    Semantic depth: {qa['semantic_depth']}")
                print(f"    Analysis depth: {'Yes' if qa['has_reasoning'] else 'No'}")
                print(f"    Strong points identified: {'Yes' if qa['has_strong_points'] else 'No'}")
                print(f"    Critical points identified: {'Yes' if qa['has_weak_points'] else 'No'}")
                print(f"    Actionable recommendations: {'Yes' if qa['has_recommendations'] else 'No'}")
                print(f"    Overall quality improvement: {qa['overall_score']:.0%}")
        elif llm:
            print(f"\n  LLM-BASED SCORING: ERROR - {llm.get('error', 'Unknown error')}")
    
    def _generate_summary(self):
        """Generate summary statistics"""
        
        comparisons = self.results['comparisons']
        
        summary = {
            'total_tests': len(comparisons),
            'llm_tests_successful': len([c for c in comparisons if c['llm'] and 'error' not in c['llm']]),
            'average_heuristic_score': round(
                sum(c['heuristic']['score'] for c in comparisons) / len(comparisons),
                2
            ),
            'average_llm_score': round(
                sum(c['llm']['score'] for c in comparisons if c['llm'] and 'score' in c['llm']) /
                len([c for c in comparisons if c['llm'] and 'score' in c['llm']]) if any(
                    c['llm'] and 'score' in c['llm'] for c in comparisons
                ) else 0,
                2
            ),
            'average_score_difference': round(
                sum(c['difference'] for c in comparisons if c['difference'] is not None) /
                len([c for c in comparisons if c['difference'] is not None]) if any(
                    c['difference'] is not None for c in comparisons
                ) else 0,
                2
            ),
            'timing_comparison': {
                'avg_heuristic_ms': round(
                    sum(c['timing'].get('heuristic_ms', 0) for c in comparisons) / len(comparisons),
                    2
                ),
                'avg_llm_ms': round(
                    sum(c['timing'].get('llm_ms', 0) for c in comparisons if 'llm_ms' in c['timing']) /
                    len([c for c in comparisons if 'llm_ms' in c['timing']]) if any(
                        'llm_ms' in c['timing'] for c in comparisons
                    ) else 0,
                    2
                )
            },
            'quality_improvements': {}
        }
        
        # Aggregate quality improvements
        successful_llm_tests = [c for c in comparisons if c['quality_improvement']]
        if successful_llm_tests:
            summary['quality_improvements'] = {
                'avg_quality_score': round(
                    sum(c['quality_improvement']['overall_score'] for c in successful_llm_tests) /
                    len(successful_llm_tests),
                    2
                ),
                'tests_with_detailed_analysis': len([
                    c for c in successful_llm_tests
                    if c['quality_improvement']['semantic_depth'] == 'detailed'
                ]),
                'tests_with_strong_points': len([
                    c for c in successful_llm_tests
                    if c['quality_improvement']['has_strong_points']
                ]),
                'tests_with_weak_points': len([
                    c for c in successful_llm_tests
                    if c['quality_improvement']['has_weak_points']
                ]),
                'tests_with_recommendations': len([
                    c for c in successful_llm_tests
                    if c['quality_improvement']['has_recommendations']
                ])
            }
        
        self.results['summary'] = summary
    
    def _save_results(self):
        """Save results to JSON file"""
        
        filename = f'llm_scoring_comparison_results_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        
        with open(filename, 'w') as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False)
        
        print(f"\n✓ Results saved to {filename}")
    
    def print_summary(self):
        """Print the summary to console"""
        
        print("\n" + "="*80)
        print("SUMMARY STATISTICS")
        print("="*80 + "\n")
        
        summary = self.results['summary']
        
        print(f"Total tests: {summary['total_tests']}")
        print(f"LLM tests successful: {summary['llm_tests_successful']}/{summary['total_tests']}")
        print()
        
        print("ALIGNMENT SCORES:")
        print(f"  Average heuristic score: {summary['average_heuristic_score']}/1.0")
        print(f"  Average LLM score: {summary['average_llm_score']}/1.0")
        print(f"  Average difference: {summary['average_score_difference']:+.2f} points")
        print()
        
        print("PERFORMANCE:")
        print(f"  Average heuristic time: {summary['timing_comparison']['avg_heuristic_ms']} ms")
        print(f"  Average LLM time: {summary['timing_comparison']['avg_llm_ms']} ms")
        print()
        
        if summary['quality_improvements']:
            qa = summary['quality_improvements']
            print("QUALITY IMPROVEMENTS:")
            print(f"  Average quality score: {qa['avg_quality_score']:.0%}")
            print(f"  Tests with detailed analysis: {qa['tests_with_detailed_analysis']}/{summary['total_tests']}")
            print(f"  Tests with identified strong points: {qa['tests_with_strong_points']}/{summary['total_tests']}")
            print(f"  Tests with identified weak points: {qa['tests_with_weak_points']}/{summary['total_tests']}")
            print(f"  Tests with actionable recommendations: {qa['tests_with_recommendations']}/{summary['total_tests']}")


if __name__ == '__main__':
    import sys
    
    try:
        comparison = ScoringComparison()
        results = comparison.run_comparison_test()
        comparison.print_summary()
        
        sys.exit(0)
    except KeyboardInterrupt:
        print("\n\n✗ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
