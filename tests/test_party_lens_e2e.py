#!/usr/bin/env python3
"""
End-to-End Test: NeoDemos Party Lens Analysis

Tests the complete party lens evaluation pipeline with real Rotterdam data.
This verifies that the corrected data + LLM scoring works end-to-end.

Run from project root:
  python tests/test_party_lens_e2e.py
"""

import sys
import os
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor


class PartyLensE2ETest:
    """Test the full party lens evaluation pipeline"""
    
    def __init__(self):
        load_dotenv()
        self.db_config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': int(os.getenv('DB_PORT', '5432')),
            'dbname': os.getenv('DB_NAME', 'neodemos'),
            'user': os.getenv('DB_USER', 'postgres'),
            'password': os.getenv('DB_PASSWORD', 'postgres')
        }
        self.results = {
            'timestamp': datetime.now().isoformat(),
            'tests': [],
            'summary': {}
        }
    
    def _get_connection(self):
        return psycopg2.connect(**self.db_config)
    
    def run_tests(self):
        """Run all E2E tests"""
        
        print("\n" + "=" * 70)
        print("NeoDemos Party Lens - End-to-End Test")
        print("=" * 70 + "\n")
        
        # Test 1: Verify clean Rotterdam data
        print("[TEST 1/3] Verify clean Rotterdam notulen data...")
        test1 = self._test_data_quality()
        
        # Test 2: Load party profile
        print("\n[TEST 2/3] Load GroenLinks-PvdA party profile...")
        test2 = self._test_party_profile_loading()
        
        # Test 3: Evaluate sample agenda items through lens
        print("\n[TEST 3/3] Evaluate sample Rotterdam agenda items...")
        test3 = self._test_party_lens_evaluation()
        
        self.results['tests'] = [test1, test2, test3]
        self._generate_summary()
        self._print_results()
        
        return self.results
    
    def _test_data_quality(self):
        """Test 1: Verify clean Rotterdam data"""
        
        test = {
            'name': 'Data Quality Check',
            'status': 'pending',
            'checks': []
        }
        
        try:
            conn = self._get_connection()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            # Check 1: Count Rotterdam notulen
            cur.execute("""
                SELECT COUNT(*) as cnt FROM documents d
                JOIN meetings m ON d.meeting_id = m.id
                WHERE LOWER(d.name) LIKE '%notulen%'
            """)
            notulen_count = cur.fetchone()['cnt']
            test['checks'].append({
                'name': f'Rotterdam notulen linked: {notulen_count}',
                'pass': notulen_count > 0,
                'value': notulen_count
            })
            
            # Check 2: Verify content is not truncated
            cur.execute("""
                SELECT AVG(LENGTH(d.content)) as avg_length, 
                       MIN(LENGTH(d.content)) as min_length,
                       MAX(LENGTH(d.content)) as max_length
                FROM documents d
                JOIN meetings m ON d.meeting_id = m.id
                WHERE LOWER(d.name) LIKE '%notulen%'
            """)
            row = cur.fetchone()
            avg_length = row['avg_length']
            min_length = row['min_length']
            max_length = row['max_length']
            
            test['checks'].append({
                'name': f'Average content length: {int(avg_length):,} chars',
                'pass': avg_length > 50000,  # Should be > 50KB after unfetchful
                'value': int(avg_length)
            })
            
            test['checks'].append({
                'name': f'Max content length: {int(max_length):,} chars',
                'pass': max_length > 200000,  # Should be > 200KB
                'value': int(max_length)
            })
            
            # Check 3: GL-PvdA mentions
            cur.execute("""
                SELECT 
                    SUM(CASE WHEN content ILIKE '%groenlinks%' THEN 1 ELSE 0 END) as docs_with_gl,
                    SUM(CASE WHEN content ILIKE '%pvda%' THEN 1 ELSE 0 END) as docs_with_pvda
                FROM documents d
                JOIN meetings m ON d.meeting_id = m.id
                WHERE LOWER(d.name) LIKE '%notulen%'
            """)
            row = cur.fetchone()
            gl_docs = row['docs_with_gl'] or 0
            pvda_docs = row['docs_with_pvda'] or 0
            
            test['checks'].append({
                'name': f'Documents mentioning GroenLinks: {gl_docs}',
                'pass': gl_docs >= 3,
                'value': gl_docs
            })
            
            test['checks'].append({
                'name': f'Documents mentioning PvdA: {pvda_docs}',
                'pass': pvda_docs >= 3,
                'value': pvda_docs
            })
            
            test['status'] = 'pass' if all(c['pass'] for c in test['checks']) else 'fail'
            
            cur.close()
            conn.close()
            
            for check in test['checks']:
                marker = '✓' if check['pass'] else '✗'
                print(f"  {marker} {check['name']}")
            
        except Exception as e:
            test['status'] = 'error'
            test['error'] = str(e)
            print(f"  ✗ Error: {e}")
        
        return test
    
    def _test_party_profile_loading(self):
        """Test 2: Load party profile"""
        
        test = {
            'name': 'Party Profile Loading',
            'status': 'pending',
            'checks': []
        }
        
        try:
            from services.policy_lens_evaluation_service import PolicyLensEvaluationService
            
            # Load service
            service = PolicyLensEvaluationService(party_name='GroenLinks-PvdA')
            
            # Try to load profile (try multiple naming conventions)
            profile_paths = [
                'data/profiles/party_profile_glpvda_corrected.json',
                'data/profiles/party_profile_groenlinks_pvda.json',
                'data/profiles/party_profile_groenlinks-pvda.json',
                'data/profiles/party_profile_gl_pvda.json'
            ]
            profile_path = None
            for path in profile_paths:
                if os.path.exists(path):
                    profile_path = path
                    break
            if os.path.exists(profile_path):
                service.load_party_profile(profile_path)
                test['checks'].append({
                    'name': 'Party profile loaded',
                    'pass': True,
                    'file': profile_path
                })
                
                # Verify profile contents
                if service.party_profile:
                    test['checks'].append({
                        'name': f'Policy areas: {len(service.party_profile.get("posities", {}))}',
                        'pass': len(service.party_profile.get("posities", {})) > 0,
                        'value': len(service.party_profile.get("posities", {}))
                    })
                    
                    test['checks'].append({
                        'name': f'Core values: {len(service.party_profile.get("kernwaarden", []))}',
                        'pass': len(service.party_profile.get("kernwaarden", [])) > 0,
                        'value': len(service.party_profile.get("kernwaarden", []))
                    })
                else:
                    test['checks'].append({
                        'name': 'Profile content verification',
                        'pass': False,
                        'error': 'Profile is empty'
                    })
            else:
                test['checks'].append({
                    'name': 'Party profile file exists',
                    'pass': False,
                    'error': f'File not found: {profile_path}'
                })
            
            test['status'] = 'pass' if all(c.get('pass', False) for c in test['checks']) else 'fail'
            
            for check in test['checks']:
                marker = '✓' if check.get('pass', False) else '✗'
                msg = check['name']
                if 'value' in check:
                    msg += f" ({check['value']})"
                print(f"  {marker} {msg}")
            
        except Exception as e:
            test['status'] = 'error'
            test['error'] = str(e)
            print(f"  ✗ Error: {e}")
        
        return test
    
    def _test_party_lens_evaluation(self):
        """Test 3: Evaluate sample agenda items through party lens"""
        
        test = {
            'name': 'Party Lens Evaluation',
            'status': 'pending',
            'evaluations': []
        }
        
        try:
            from services.policy_lens_evaluation_service import PolicyLensEvaluationService
            
            # Get sample Rotterdam agenda items
            conn = self._get_connection()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            # Find substantive agenda items with documents
            cur.execute("""
                SELECT DISTINCT ai.id, ai.name, m.name as meeting_name, m.start_date
                FROM agenda_items ai
                JOIN meetings m ON ai.meeting_id = m.id
                JOIN documents d ON ai.id = d.agenda_item_id
                WHERE m.start_date > '2024-01-01'
                AND ai.name NOT ILIKE '%vaststelling%'
                AND ai.name NOT ILIKE '%ingekomen%'
                AND ai.name NOT ILIKE '%agenda%'
                AND d.content IS NOT NULL
                LIMIT 3
            """)
            
            sample_items = cur.fetchall()
            
            if not sample_items:
                test['evaluations'].append({
                    'item': 'Sample agenda items',
                    'pass': False,
                    'error': 'No suitable agenda items found'
                })
                test['status'] = 'fail'
            else:
                # Initialize lens service
                lens_service = PolicyLensEvaluationService(party_name='GroenLinks-PvdA')
                profile_paths = [
                    'data/profiles/party_profile_glpvda_corrected.json',
                    'data/profiles/party_profile_groenlinks_pvda.json',
                    'data/profiles/party_profile_groenlinks-pvda.json',
                    'data/profiles/party_profile_gl_pvda.json'
                ]
                profile_path = None
                for path in profile_paths:
                    if os.path.exists(path):
                        profile_path = path
                        break
                
                if os.path.exists(profile_path):
                    lens_service.load_party_profile(profile_path)
                    
                    print(f"  Found {len(sample_items)} sample agenda items")
                    
                    for item in sample_items:
                        print(f"    Evaluating: {item['name'][:60]}...")
                        
                        # Get all documents for this agenda item
                        cur.execute(
                            "SELECT content FROM documents WHERE agenda_item_id = %s AND content IS NOT NULL LIMIT 1",
                            (item['id'],)
                        )
                        doc_row = cur.fetchone()
                        
                        if doc_row:
                            agenda_text = f"{item['name']}\n\n{doc_row['content'][:2000]}"
                            
                            # Evaluate through lens
                            result = lens_service.evaluate_agenda_item(agenda_text)
                            
                            eval_result = {
                                'item': item['name'][:60],
                                'pass': result and result.get('analyse') is not None,
                                'has_score': result.get('analyse', {}).get('score') is not None if result else False,
                                'score': result.get('analyse', {}).get('score') if result else None,
                                'has_recommendations': len(result.get('aanbevelingen', [])) > 0 if result else False
                            }
                            
                            test['evaluations'].append(eval_result)
                            
                            if eval_result['pass']:
                                score_str = f"{eval_result['score']:.2f}" if eval_result['score'] is not None else "N/A"
                                print(f"      ✓ Evaluated (score: {score_str})")
                            else:
                                print(f"      ✗ Evaluation failed")
                        else:
                            test['evaluations'].append({
                                'item': item['name'][:60],
                                'pass': False,
                                'error': 'No documents found'
                            })
                else:
                    test['status'] = 'fail'
                    test['evaluations'].append({
                        'item': 'Party profile',
                        'pass': False,
                        'error': f'Profile not found: {profile_path}'
                    })
            
            test['status'] = 'pass' if all(e.get('pass', False) for e in test['evaluations']) else 'fail'
            cur.close()
            conn.close()
            
        except Exception as e:
            test['status'] = 'error'
            test['error'] = str(e)
            print(f"  ✗ Error: {e}")
        
        return test
    
    def _generate_summary(self):
        """Generate test summary"""
        
        total_tests = len(self.results['tests'])
        passed_tests = sum(1 for t in self.results['tests'] if t['status'] == 'pass')
        failed_tests = sum(1 for t in self.results['tests'] if t['status'] == 'fail')
        error_tests = sum(1 for t in self.results['tests'] if t['status'] == 'error')
        
        self.results['summary'] = {
            'total_tests': total_tests,
            'passed': passed_tests,
            'failed': failed_tests,
            'errors': error_tests,
            'pass_rate': f"{(passed_tests / total_tests * 100):.0f}%" if total_tests > 0 else "0%",
            'overall_status': 'pass' if failed_tests == 0 and error_tests == 0 else 'fail'
        }
    
    def _print_results(self):
        """Print test results"""
        
        summary = self.results['summary']
        
        print("\n" + "=" * 70)
        print("TEST SUMMARY")
        print("=" * 70)
        print(f"Total tests:   {summary['total_tests']}")
        print(f"Passed:        {summary['passed']}")
        print(f"Failed:        {summary['failed']}")
        print(f"Errors:        {summary['errors']}")
        print(f"Pass rate:     {summary['pass_rate']}")
        print(f"Status:        {'✓ PASS' if summary['overall_status'] == 'pass' else '✗ FAIL'}")
        print("=" * 70 + "\n")
        
        # Save results
        os.makedirs('output/test_results', exist_ok=True)
        with open('output/test_results/test_party_lens_e2e_results.json', 'w') as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False, default=str)
        
        print(f"Results saved to output/test_results/test_party_lens_e2e_results.json")
        
        return summary['overall_status'] == 'pass'


if __name__ == "__main__":
    tester = PartyLensE2ETest()
    passed = tester.run_tests()
    sys.exit(0 if passed else 1)
