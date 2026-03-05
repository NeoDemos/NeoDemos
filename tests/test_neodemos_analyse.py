#!/usr/bin/env python3
"""
TEST: NeoDemos Analyse Function

Tests the 'neodemos analyse' function on a real Rotterdam
Gemeenteraad meeting agenda item, evaluating it through
GroenLinks-PvdA's ideological lens.
"""

import json
from services.party_position_profile_service import PartyPositionProfileService
from services.policy_lens_evaluation_service import PolicyLensEvaluationService

def test_neodemos_analyse_on_agenda_item():
    """Test the complete neodemos analyse flow on a meeting agenda item"""
    
    print("\n" + "="*70)
    print("NeoDemos ANALYSE TEST")
    print("Testing analysis on meeting agenda item through party lens")
    print("="*70)
    
    # Test agenda items from Rotterdam Gemeenteraad
    test_items = [
        "Raadsvoorstel: Implementatie klimaatneutraal bouwbeleid 2025-2030",
        "Motie: Invoering verplichte betaalbare huurwoningen in nieuwbouw",
        "Agendapunt: Evaluatie openbaar vervoer en mobiliteitstransitie",
        "Raadsvoorstel: Begrotingskader 2026 - afdeling economische zaken"
    ]
    
    party_name = "GroenLinks-PvdA"
    
    # Step 1: Build party profile
    print("\n[STEP 1] Building party position profile...")
    print("-" * 70)
    
    profile_service = PartyPositionProfileService(party_name)
    party_profile = profile_service.build_party_profile()
    
    # Save party profile
    with open('data/profiles/party_profile_glpvda_corrected.json', 'w') as f:
        json.dump(party_profile, f, indent=2, ensure_ascii=False)
    
    print(f"\n✓ Party profile created with {len(party_profile.get('posities', {}))} policy areas")
    print(f"✓ Core values: {party_profile.get('kernwaarden', [])}")
    
    # Step 2: Test analyse function on each agenda item
    print("\n[STEP 2] Testing analyse function on agenda items...")
    print("-" * 70)
    
    evaluation_service = PolicyLensEvaluationService(party_name)
    evaluation_service.party_profile = party_profile
    
    analyses = {}
    
    for i, agenda_item in enumerate(test_items, 1):
        print(f"\n[TEST {i}] Analyzing: {agenda_item}")
        print(f"{"─"*70}")
        
        # Run the analyse function
        analysis = evaluation_service.evaluate_agenda_item(agenda_item)
        
        # Store results
        analyses[f"agenda_item_{i}"] = analysis
        
        # Print results
        print(f"\nRESULTS:")
        print(f"  Beleidsgebied: {analysis.get('analyse', {}).get('beleidsgebied', 'Onbekend')}")
        print(f"  Partij visie: {analysis.get('analyse', {}).get('partij_visie', 'Onbekend')}")
        print(f"  Afstemming score: {analysis.get('analyse', {}).get('afstemming_score', 'N/A')}")
        print(f"  Interpretatie: {analysis.get('analyse', {}).get('afstemming_interpretatie', 'N/A')}")
        
        print(f"\nAANBEVELINGEN:")
        for rec in analysis.get('aanbevelingen', []):
            print(f"  • {rec}")
    
    # Step 3: Save test results
    print("\n[STEP 3] Saving test results...")
    print("-" * 70)
    
    test_results = {
        'test_datum': party_profile.get('profiel_datum'),
        'partij': party_name,
        'test_items_count': len(test_items),
        'analyses': analyses
    }
    
    with open('output/test_results/neodemos_analyse_test_results.json', 'w') as f:
        json.dump(test_results, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Test results saved to: neodemos_analyse_test_results.json")
    
    # Step 4: Generate summary report
    print("\n[STEP 4] Generating test summary...")
    print("-" * 70)
    
    summary = generate_test_summary(analyses)
    
    print("\nTEST SUMMARY:")
    print(f"  Total items tested: {summary['total_items']}")
    print(f"  Average alignment score: {summary['avg_alignment']:.2f}")
    print(f"  Highest alignment: {summary['highest_alignment']} ({summary['highest_area']})")
    print(f"  Lowest alignment: {summary['lowest_alignment']} ({summary['lowest_area']})")
    
    print(f"\nPolicy areas covered:")
    for area in summary['areas_covered']:
        print(f"  • {area}")
    
    # Step 5: Validation check
    print("\n[STEP 5] Validation check...")
    print("-" * 70)
    
    validation = validate_test_results(analyses, party_profile)
    
    if validation['is_valid']:
        print("\n✓ TEST PASSED - Analysis functions correctly")
        print(f"\nValidation checks:")
        for check, result in validation['checks'].items():
            status = "✓" if result else "✗"
            print(f"  {status} {check}")
    else:
        print("\n✗ TEST FAILED - Issues detected")
        print(f"\nValidation checks:")
        for check, result in validation['checks'].items():
            status = "✓" if result else "✗"
            print(f"  {status} {check}")
    
    print("\n" + "="*70)
    print("NeoDemos ANALYSE TEST COMPLETE")
    print("="*70 + "\n")
    
    return {
        'party_profile': party_profile,
        'analyses': analyses,
        'validation': validation,
        'summary': summary
    }

def generate_test_summary(analyses: dict) -> dict:
    """Generate summary of test results"""
    
    if not analyses:
        return {
            'total_items': 0,
            'avg_alignment': 0.0,
            'highest_alignment': 0.0,
            'lowest_alignment': 1.0,
            'highest_area': 'N/A',
            'lowest_area': 'N/A',
            'areas_covered': []
        }
    
    scores = []
    areas = set()
    
    for item_data in analyses.values():
        score = item_data.get('analyse', {}).get('afstemming_score', 0)
        scores.append(score)
        area = item_data.get('analyse', {}).get('beleidsgebied', 'Onbekend')
        areas.add(area)
    
    avg_score = sum(scores) / len(scores) if scores else 0.5
    
    # Find highest and lowest
    highest_idx = scores.index(max(scores)) if scores else 0
    lowest_idx = scores.index(min(scores)) if scores else 0
    
    highest_item = list(analyses.values())[highest_idx]
    lowest_item = list(analyses.values())[lowest_idx]
    
    return {
        'total_items': len(analyses),
        'avg_alignment': avg_score,
        'highest_alignment': max(scores) if scores else 0,
        'lowest_alignment': min(scores) if scores else 1,
        'highest_area': highest_item.get('analyse', {}).get('beleidsgebied', 'N/A'),
        'lowest_area': lowest_item.get('analyse', {}).get('beleidsgebied', 'N/A'),
        'areas_covered': sorted(list(areas))
    }

def validate_test_results(analyses: dict, party_profile: dict) -> dict:
    """Validate that test results are correct and complete"""
    
    checks = {
        'Analyses created': len(analyses) > 0,
        'All items have policy area': all(
            a.get('analyse', {}).get('beleidsgebied') 
            for a in analyses.values()
        ),
        'All items have alignment score': all(
            isinstance(a.get('analyse', {}).get('afstemming_score'), (int, float))
            for a in analyses.values()
        ),
        'All items have recommendations': all(
            len(a.get('aanbevelingen', [])) > 0
            for a in analyses.values()
        ),
        'Party profile has positions': len(party_profile.get('posities', {})) > 0,
        'Party profile has core values': len(party_profile.get('kernwaarden', [])) > 0,
        'Alignment scores are valid': all(
            0.0 <= a.get('analyse', {}).get('afstemming_score', 0.5) <= 1.0
            for a in analyses.values()
        )
    }
    
    is_valid = all(checks.values())
    
    return {
        'is_valid': is_valid,
        'checks': checks
    }

if __name__ == '__main__':
    results = test_neodemos_analyse_on_agenda_item()
    
    if results['validation']['is_valid']:
        print("\n✓✓✓ NeoDemos ANALYSE FUNCTION IS WORKING CORRECTLY ✓✓✓")
    else:
        print("\n✗✗✗ NeoDemos ANALYSE FUNCTION HAS ISSUES ✗✗✗")
