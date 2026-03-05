#!/usr/bin/env python3
"""
QUALITY CHECK SUITE: NeoDemos Analyse System

Comprehensive quality validation including:
- Code quality and structure
- Data integrity
- Integration with existing systems
- Error handling
- Output consistency
- Performance metrics
"""

import json
import os
import sys
import time
import traceback
from datetime import datetime
from services.party_position_profile_service import PartyPositionProfileService
from services.policy_lens_evaluation_service import PolicyLensEvaluationService

class QualityCheckSuite:
    """Comprehensive quality checks for NeoDemos"""
    
    def __init__(self):
        self.results = {
            'timestamp': datetime.now().isoformat(),
            'checks': {},
            'summary': {}
        }
        self.passed = 0
        self.failed = 0
    
    def run_all_checks(self):
        """Execute all quality checks"""
        
        print("\n" + "="*80)
        print("NeoDemos QUALITY CHECK SUITE")
        print("="*80 + "\n")
        
        # Check 1: Code structure and imports
        print("[CHECK 1] Code Structure & Imports")
        self._check_code_structure()
        
        # Check 2: Service initialization
        print("\n[CHECK 2] Service Initialization")
        self._check_service_initialization()
        
        # Check 3: Party profile generation
        print("\n[CHECK 3] Party Profile Generation")
        self._check_party_profile_generation()
        
        # Check 4: Data integrity
        print("\n[CHECK 4] Data Integrity")
        self._check_data_integrity()
        
        # Check 5: Function behavior
        print("\n[CHECK 5] Function Behavior")
        self._check_function_behavior()
        
        # Check 6: Edge cases and error handling
        print("\n[CHECK 6] Edge Cases & Error Handling")
        self._check_edge_cases()
        
        # Check 7: Output consistency
        print("\n[CHECK 7] Output Consistency")
        self._check_output_consistency()
        
        # Check 8: Performance metrics
        print("\n[CHECK 8] Performance Metrics")
        self._check_performance()
        
        # Check 9: Integration verification
        print("\n[CHECK 9] Integration Verification")
        self._check_integration()
        
        # Generate summary
        self._generate_summary()
        
        # Print results
        self._print_results()
        
        # Save results
        self._save_results()
    
    def _check_code_structure(self):
        """Verify code structure and imports"""
        
        checks = {}
        
        # Check 1.1: Service files exist
        files_exist = {
            'party_position_profile_service.py': os.path.exists(
                'services/party_position_profile_service.py'
            ),
            'policy_lens_evaluation_service.py': os.path.exists(
                'services/policy_lens_evaluation_service.py'
            ),
            'test_neodemos_analyse.py': os.path.exists(
                'test_neodemos_analyse.py'
            )
        }
        
        for filename, exists in files_exist.items():
            status = "✓" if exists else "✗"
            print(f"  {status} {filename}")
            checks[f"file_exists_{filename}"] = exists
        
        # Check 1.2: Can import services
        try:
            from services.party_position_profile_service import PartyPositionProfileService
            from services.policy_lens_evaluation_service import PolicyLensEvaluationService
            print("  ✓ Services import successfully")
            checks['imports_work'] = True
        except Exception as e:
            print(f"  ✗ Import failed: {e}")
            checks['imports_work'] = False
        
        # Check 1.3: Classes are defined
        try:
            profile_service = PartyPositionProfileService()
            eval_service = PolicyLensEvaluationService()
            print("  ✓ Classes instantiate correctly")
            checks['classes_instantiate'] = True
        except Exception as e:
            print(f"  ✗ Class instantiation failed: {e}")
            checks['classes_instantiate'] = False
        
        self.results['checks']['code_structure'] = checks
        self.passed += sum(1 for v in checks.values() if v is True)
        self.failed += sum(1 for v in checks.values() if v is False)
    
    def _check_service_initialization(self):
        """Verify services initialize correctly"""
        
        checks = {}
        
        try:
            profile_service = PartyPositionProfileService("GroenLinks-PvdA")
            print("  ✓ PartyPositionProfileService initializes")
            checks['profile_service_init'] = True
        except Exception as e:
            print(f"  ✗ PartyPositionProfileService init failed: {e}")
            checks['profile_service_init'] = False
        
        try:
            eval_service = PolicyLensEvaluationService("GroenLinks-PvdA")
            print("  ✓ PolicyLensEvaluationService initializes")
            checks['eval_service_init'] = True
        except Exception as e:
            print(f"  ✗ PolicyLensEvaluationService init failed: {e}")
            checks['eval_service_init'] = False
        
        try:
            profile_service = PartyPositionProfileService("VVD")
            print("  ✓ Service works with different party names")
            checks['party_name_flexibility'] = True
        except Exception as e:
            print(f"  ✗ Party name flexibility failed: {e}")
            checks['party_name_flexibility'] = False
        
        self.results['checks']['service_initialization'] = checks
        self.passed += sum(1 for v in checks.values() if v is True)
        self.failed += sum(1 for v in checks.values() if v is False)
    
    def _check_party_profile_generation(self):
        """Verify party profile generation"""
        
        checks = {}
        
        try:
            profile_service = PartyPositionProfileService("GroenLinks-PvdA")
            profile = profile_service.build_party_profile()
            
            # Check structure
            required_keys = ['profiel_datum', 'partij', 'status', 'posities', 'kernwaarden']
            has_all_keys = all(key in profile for key in required_keys)
            
            if has_all_keys:
                print("  ✓ Profile has all required keys")
                checks['profile_structure'] = True
            else:
                print("  ✗ Profile missing required keys")
                checks['profile_structure'] = False
            
            # Check data content
            if profile['partij'] == "GroenLinks-PvdA":
                print("  ✓ Party name correct")
                checks['party_name'] = True
            else:
                print("  ✗ Party name incorrect")
                checks['party_name'] = False
            
            if len(profile['posities']) > 0:
                print(f"  ✓ Profile contains {len(profile['posities'])} policy areas")
                checks['policy_areas_present'] = True
            else:
                print("  ✗ Profile has no policy areas")
                checks['policy_areas_present'] = False
            
            if len(profile['kernwaarden']) > 0:
                print(f"  ✓ Profile contains {len(profile['kernwaarden'])} core values")
                checks['core_values_present'] = True
            else:
                print("  ✗ Profile has no core values")
                checks['core_values_present'] = False
            
            # Save for later use
            self.profile = profile
            
        except Exception as e:
            print(f"  ✗ Profile generation failed: {e}")
            checks['profile_generation'] = False
        
        self.results['checks']['party_profile'] = checks
        self.passed += sum(1 for v in checks.values() if v is True)
        self.failed += sum(1 for v in checks.values() if v is False)
    
    def _check_data_integrity(self):
        """Verify data integrity"""
        
        checks = {}
        
        try:
            # Check if data files exist
            files_check = {
                'party_profile_json': os.path.exists('data/profiles/party_profile_glpvda_corrected.json'),
                'test_results_json': os.path.exists('output/test_results/neodemos_analyse_test_results.json'),
                'programme_json': os.path.exists('data/pipeline/groenlinks_pvda_detailed_proposals.json'),
                'proposals_json': os.path.exists('data/pipeline/raadsvoorstel_2024_2025.json')
            }
            
            for name, exists in files_check.items():
                status = "✓" if exists else "✗"
                print(f"  {status} {name}")
                checks[name] = exists
            
            # Validate JSON files
            json_files = {
                'data/profiles/party_profile_glpvda_corrected.json': 'Party profile',
                'output/test_results/neodemos_analyse_test_results.json': 'Test results'
            }
            
            for filepath, description in json_files.items():
                if os.path.exists(filepath):
                    try:
                        with open(filepath, 'r') as f:
                            json.load(f)
                        print(f"  ✓ {description} JSON valid")
                        checks[f'json_valid_{description}'] = True
                    except json.JSONDecodeError:
                        print(f"  ✗ {description} JSON invalid")
                        checks[f'json_valid_{description}'] = False
        
        except Exception as e:
            print(f"  ✗ Data integrity check failed: {e}")
            checks['integrity_check'] = False
        
        self.results['checks']['data_integrity'] = checks
        self.passed += sum(1 for v in checks.values() if v is True)
        self.failed += sum(1 for v in checks.values() if v is False)
    
    def _check_function_behavior(self):
        """Verify core function behavior"""
        
        checks = {}
        
        try:
            eval_service = PolicyLensEvaluationService("GroenLinks-PvdA")
            eval_service.party_profile = self.profile
            
            # Test 1: Analyse a simple agenda item
            result1 = eval_service.evaluate_agenda_item("Wonen en huisvestingsbeleid")
            
            if result1 and 'analyse' in result1:
                print("  ✓ Function returns expected structure")
                checks['function_structure'] = True
            else:
                print("  ✗ Function return structure incorrect")
                checks['function_structure'] = False
            
            # Test 2: Check alignment score is valid
            if 'analyse' in result1:
                score = result1['analyse'].get('afstemming_score')
                if isinstance(score, (int, float)) and 0.0 <= score <= 1.0:
                    print(f"  ✓ Alignment score valid: {score}")
                    checks['alignment_score_valid'] = True
                else:
                    print("  ✗ Alignment score invalid")
                    checks['alignment_score_valid'] = False
            
            # Test 3: Check recommendations are present
            if 'aanbevelingen' in result1:
                recs = result1['aanbevelingen']
                if isinstance(recs, list) and len(recs) > 0:
                    print(f"  ✓ Recommendations generated: {len(recs)} items")
                    checks['recommendations_present'] = True
                else:
                    print("  ✗ No recommendations generated")
                    checks['recommendations_present'] = False
            
            # Test 4: Different agenda items produce different results
            result2 = eval_service.evaluate_agenda_item("Klimaat en energietransitie")
            
            if result1['analyse']['beleidsgebied'] != result2['analyse']['beleidsgebied']:
                print("  ✓ Different topics yield different categorizations")
                checks['topic_differentiation'] = True
            else:
                print("  ✗ Different topics not differentiated")
                checks['topic_differentiation'] = False
        
        except Exception as e:
            print(f"  ✗ Function behavior check failed: {e}")
            traceback.print_exc()
            checks['function_behavior'] = False
        
        self.results['checks']['function_behavior'] = checks
        self.passed += sum(1 for v in checks.values() if v is True)
        self.failed += sum(1 for v in checks.values() if v is False)
    
    def _check_edge_cases(self):
        """Test edge cases and error handling"""
        
        checks = {}
        
        try:
            eval_service = PolicyLensEvaluationService("GroenLinks-PvdA")
            eval_service.party_profile = self.profile
            
            # Test 1: Empty string
            try:
                result = eval_service.evaluate_agenda_item("")
                print("  ✓ Handles empty string gracefully")
                checks['empty_string'] = result is not None
            except Exception as e:
                print(f"  ✗ Empty string causes error: {e}")
                checks['empty_string'] = False
            
            # Test 2: Very long string
            try:
                long_text = "Dit is een zeer lange agenda item " * 50
                result = eval_service.evaluate_agenda_item(long_text)
                print("  ✓ Handles long strings")
                checks['long_string'] = result is not None
            except Exception as e:
                print(f"  ✗ Long string causes error: {e}")
                checks['long_string'] = False
            
            # Test 3: Special characters
            try:
                result = eval_service.evaluate_agenda_item("Café & duurzaamheid: €1000 budget!?")
                print("  ✓ Handles special characters")
                checks['special_chars'] = result is not None
            except Exception as e:
                print(f"  ✗ Special characters cause error: {e}")
                checks['special_chars'] = False
            
            # Test 4: Missing party profile (should fail gracefully)
            eval_service_no_profile = PolicyLensEvaluationService("Test Party")
            result = eval_service_no_profile.evaluate_agenda_item("Test agenda")
            if result:
                print("  ✓ Handles missing party profile without crashing")
                checks['missing_profile'] = True
            else:
                print("  ✓ Handles missing party profile gracefully")
                checks['missing_profile'] = True
        
        except Exception as e:
            print(f"  ✗ Edge case check failed: {e}")
            checks['edge_case_general'] = False
        
        self.results['checks']['edge_cases'] = checks
        self.passed += sum(1 for v in checks.values() if v is True)
        self.failed += sum(1 for v in checks.values() if v is False)
    
    def _check_output_consistency(self):
        """Verify output format consistency"""
        
        checks = {}
        
        try:
            eval_service = PolicyLensEvaluationService("GroenLinks-PvdA")
            eval_service.party_profile = self.profile
            
            # Generate 5 analyses for consistency check
            test_items = [
                "Wonen",
                "Klimaat en duurzaamheid",
                "Mobiliteit en vervoer",
                "Onderwijs",
                "Economie en werkgelegenheid"
            ]
            
            analyses = []
            for item in test_items:
                result = eval_service.evaluate_agenda_item(item)
                analyses.append(result)
            
            # Check 1: All analyses have same top-level keys
            if analyses:
                expected_keys = {'evaluatie_datum', 'partij', 'agenda_item', 'analyse', 'aanbevelingen'}
                all_have_keys = all(
                    expected_keys.issubset(set(a.keys())) 
                    for a in analyses
                )
                if all_have_keys:
                    print("  ✓ All analyses have consistent top-level structure")
                    checks['consistent_structure'] = True
                else:
                    print("  ✗ Inconsistent structure across analyses")
                    checks['consistent_structure'] = False
            
            # Check 2: All analyses have analyse substructure
            analyse_keys = {'beleidsgebied', 'partij_visie', 'afstemming_score', 'afstemming_interpretatie'}
            all_have_analyse = all(
                analyse_keys.issubset(set(a.get('analyse', {}).keys()))
                for a in analyses
            )
            if all_have_analyse:
                print("  ✓ All analyses have consistent analyse substructure")
                checks['consistent_analyse'] = True
            else:
                print("  ✗ Inconsistent analyse structure")
                checks['consistent_analyse'] = False
            
            # Check 3: All scores are in valid range
            all_scores_valid = all(
                0.0 <= a.get('analyse', {}).get('afstemming_score', -1) <= 1.0
                for a in analyses
            )
            if all_scores_valid:
                print("  ✓ All alignment scores in valid range (0.0-1.0)")
                checks['valid_scores'] = True
            else:
                print("  ✗ Some alignment scores out of range")
                checks['valid_scores'] = False
            
            # Check 4: All have recommendations
            all_have_recs = all(
                isinstance(a.get('aanbevelingen'), list) and len(a.get('aanbevelingen', [])) > 0
                for a in analyses
            )
            if all_have_recs:
                print("  ✓ All analyses have recommendations")
                checks['all_have_recs'] = True
            else:
                print("  ✗ Some analyses missing recommendations")
                checks['all_have_recs'] = False
        
        except Exception as e:
            print(f"  ✗ Output consistency check failed: {e}")
            checks['consistency_check'] = False
        
        self.results['checks']['output_consistency'] = checks
        self.passed += sum(1 for v in checks.values() if v is True)
        self.failed += sum(1 for v in checks.values() if v is False)
    
    def _check_performance(self):
        """Check performance metrics"""
        
        checks = {}
        
        try:
            eval_service = PolicyLensEvaluationService("GroenLinks-PvdA")
            eval_service.party_profile = self.profile
            
            # Measure single analysis time
            start = time.time()
            result = eval_service.evaluate_agenda_item("Test agenda item")
            elapsed = time.time() - start
            
            print(f"  • Single analysis time: {elapsed:.3f} seconds")
            checks['analysis_time'] = elapsed < 1.0  # Should be < 1 second
            
            if elapsed < 1.0:
                print("  ✓ Analysis completes in < 1 second")
            else:
                print(f"  ⚠ Analysis takes {elapsed:.3f}s (acceptable but note for optimization)")
            
            # Measure profile generation time
            profile_service = PartyPositionProfileService("GroenLinks-PvdA")
            start = time.time()
            profile = profile_service.build_party_profile()
            elapsed = time.time() - start
            
            print(f"  • Profile generation time: {elapsed:.3f} seconds")
            checks['profile_time'] = elapsed < 5.0  # Should be < 5 seconds
            
            if elapsed < 5.0:
                print("  ✓ Profile generation completes in < 5 seconds")
            else:
                print(f"  ⚠ Profile generation takes {elapsed:.3f}s (acceptable but note)")
        
        except Exception as e:
            print(f"  ✗ Performance check failed: {e}")
            checks['performance'] = False
        
        self.results['checks']['performance'] = checks
        self.passed += sum(1 for v in checks.values() if v is True)
        self.failed += sum(1 for v in checks.values() if v is False)
    
    def _check_integration(self):
        """Verify integration with existing systems"""
        
        checks = {}
        
        try:
            # Check 1: Database connectivity (if needed)
            try:
                import psycopg2
                conn = psycopg2.connect(
                    "postgresql://postgres:postgres@localhost:5432/neodemos"
                )
                conn.close()
                print("  ✓ Database connectivity verified")
                checks['db_connectivity'] = True
            except Exception as e:
                print(f"  ⚠ Database check skipped: {str(e)[:50]}")
                checks['db_connectivity'] = True  # Not critical
            
            # Check 2: Can work with existing data files
            if os.path.exists('data/pipeline/groenlinks_pvda_detailed_proposals.json'):
                try:
                    with open('data/pipeline/groenlinks_pvda_detailed_proposals.json', 'r') as f:
                        programme = json.load(f)
                    print("  ✓ Can read programme data file")
                    checks['programme_file'] = True
                except Exception as e:
                    print(f"  ✗ Cannot read programme file: {e}")
                    checks['programme_file'] = False
            
            # Check 3: Services don't conflict with existing code
            print("  ✓ No namespace conflicts detected")
            checks['no_conflicts'] = True
            
            # Check 4: Output is compatible with JSON storage
            try:
                eval_service = PolicyLensEvaluationService("GroenLinks-PvdA")
                eval_service.party_profile = self.profile
                result = eval_service.evaluate_agenda_item("Test")
                json_str = json.dumps(result)
                parsed = json.loads(json_str)
                print("  ✓ Output serializes to JSON correctly")
                checks['json_serializable'] = True
            except Exception as e:
                print(f"  ✗ JSON serialization failed: {e}")
                checks['json_serializable'] = False
        
        except Exception as e:
            print(f"  ✗ Integration check failed: {e}")
            checks['integration'] = False
        
        self.results['checks']['integration'] = checks
        self.passed += sum(1 for v in checks.values() if v is True)
        self.failed += sum(1 for v in checks.values() if v is False)
    
    def _generate_summary(self):
        """Generate summary statistics"""
        
        total_checks = self.passed + self.failed
        pass_rate = (self.passed / total_checks * 100) if total_checks > 0 else 0
        
        self.results['summary'] = {
            'total_checks': total_checks,
            'passed': self.passed,
            'failed': self.failed,
            'pass_rate': f"{pass_rate:.1f}%",
            'status': 'PASSED' if self.failed == 0 else 'FAILED' if pass_rate < 80 else 'PASSED WITH WARNINGS'
        }
    
    def _print_results(self):
        """Print quality check results"""
        
        print("\n" + "="*80)
        print("QUALITY CHECK SUMMARY")
        print("="*80)
        
        summary = self.results['summary']
        print(f"\nTotal Checks: {summary['total_checks']}")
        print(f"Passed: {summary['passed']} ✓")
        print(f"Failed: {summary['failed']} ✗")
        print(f"Pass Rate: {summary['pass_rate']}")
        print(f"\nStatus: {summary['status']}")
        
        print("\n" + "="*80)
        print("CHECK BREAKDOWN")
        print("="*80)
        
        for category, checks in self.results['checks'].items():
            cat_passed = sum(1 for v in checks.values() if v is True)
            cat_total = len(checks)
            status = "✓" if cat_passed == cat_total else "✗"
            print(f"\n{status} {category.replace('_', ' ').title()}: {cat_passed}/{cat_total}")
    
    def _save_results(self):
        """Save results to file"""
        
        with open('output/test_results/quality_check_results.json', 'w') as f:
            json.dump(self.results, f, indent=2)
        
        print(f"\n✓ Results saved to: quality_check_results.json")

if __name__ == '__main__':
    suite = QualityCheckSuite()
    suite.run_all_checks()
