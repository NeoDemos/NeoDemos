#!/usr/bin/env python3
"""
STAP 3D: Trend Analyse - Patroonfrequentie Analyse

Analyzes frequency patterns of GL-PvdA vs College B&W positions across policy areas
to identify sustained vs situational conflicts.

Compares:
1. How often GL-PvdA mentions each policy area (from notulen)
2. How often College responds to GL-PvdA on each topic
3. Pattern consistency over time (sustained vs one-time)
4. Alignment/divergence scores per policy area
"""

import json
import psycopg2
from typing import Dict, List, Any
from collections import defaultdict
from datetime import datetime

class TrendAnalysisService:
    """Analyze frequency patterns and trends in policy positions"""
    
    def __init__(self):
        self.conn = psycopg2.connect(
            "postgresql://postgres:postgres@localhost:5432/neodemos"
        )
        self.cursor = self.conn.cursor()
    
    def analyze_trends(self) -> Dict[str, Any]:
        """
        Analyze frequency patterns showing sustained vs situational conflicts
        """
        
        print(f"\n{'='*70}")
        print("STAP 3D: TREND ANALYSE")
        print("Patroonfrequentie en Consistentie Analyse")
        print(f"{'='*70}\n")
        
        results = {
            'analyse_datum': datetime.now().isoformat(),
            'type': 'trend_analysis',
            'policy_area_frequencies': {},
            'temporal_patterns': {},
            'divergence_patterns': {},
            'samenvatting': {}
        }
        
        try:
            # Load GL-PvdA positions from JSON
            with open('data/pipeline/groenlinks_pvda_notulen_positions.json', 'r') as f:
                glpvda_data = json.load(f)
            
            # Load College positions from JSON
            with open('data/pipeline/college_bw_implicit_positions.json', 'r') as f:
                college_data = json.load(f)
            
            glpvda_positions = glpvda_data.get('posities', [])
            college_positions = college_data.get('posities', [])
            
            print(f"[1/4] Analyzing GL-PvdA frequency patterns...")
            glpvda_freq = self._analyze_frequency(glpvda_positions, 'GL-PvdA')
            
            print(f"[2/4] Analyzing College frequency patterns...")
            college_freq = self._analyze_frequency(college_positions, 'College')
            
            print(f"[3/4] Computing temporal patterns...")
            temporal = self._analyze_temporal_patterns(glpvda_positions, college_positions)
            
            print(f"[4/4] Computing divergence patterns...")
            divergence = self._compute_divergence_patterns(glpvda_freq, college_freq)
            
            results['policy_area_frequencies'] = {
                'groenlinks_pvda': glpvda_freq,
                'college_bw': college_freq
            }
            results['temporal_patterns'] = temporal
            results['divergence_patterns'] = divergence
            results['samenvatting'] = self._generate_summary(results)
            
            return results
        
        except Exception as e:
            print(f"✗ Fout: {e}")
            import traceback
            traceback.print_exc()
            return results
    
    def _analyze_frequency(self, positions: List[Dict[str, Any]], source: str) -> Dict[str, Any]:
        """Analyze frequency of positions by policy area"""
        
        freq = defaultdict(lambda: {
            'totaal': 0,
            'statements': 0,
            'votes': 0,
            'proposals': 0,
            'amendments': 0,
            'responses': 0,
            'initiatives': 0,
            'avg_confidence': 0.0,
            'confidence_scores': []
        })
        
        for pos in positions:
            area = pos.get('beleidsterrein', 'Overig')
            atype = pos.get('activiteit_type', 'onbekend')
            context = pos.get('context_type', 'overig')
            confidence = pos.get('confidence', 0.0)
            
            freq[area]['totaal'] += 1
            
            if atype == 'statement':
                freq[area]['statements'] += 1
            elif atype == 'stemming':
                freq[area]['votes'] += 1
            elif atype == 'voorstel':
                freq[area]['proposals'] += 1
            elif atype == 'amendement':
                freq[area]['amendments'] += 1
            
            if context == 'respons_college':
                freq[area]['responses'] += 1
            elif context == 'initiatief':
                freq[area]['initiatives'] += 1
            
            freq[area]['confidence_scores'].append(confidence)
        
        # Calculate average confidence
        for area in freq:
            if freq[area]['confidence_scores']:
                freq[area]['avg_confidence'] = sum(freq[area]['confidence_scores']) / len(freq[area]['confidence_scores'])
            del freq[area]['confidence_scores']  # Remove raw scores from output
        
        # Sort by frequency
        sorted_freq = dict(sorted(freq.items(), key=lambda x: x[1]['totaal'], reverse=True))
        
        print(f"  ✓ {source}: {sum(f['totaal'] for f in freq.values())} posities across {len(freq)} policy areas")
        
        return sorted_freq
    
    def _analyze_temporal_patterns(self, glpvda_pos: List[Dict], college_pos: List[Dict]) -> Dict[str, Any]:
        """Analyze temporal patterns - sustained vs situational"""
        
        # Group by date and policy area
        glpvda_by_date = defaultdict(lambda: defaultdict(int))
        college_by_date = defaultdict(lambda: defaultdict(int))
        
        for pos in glpvda_pos:
            date = pos.get('datum', 'onbekend')[:7]  # YYYY-MM
            area = pos.get('beleidsterrein', 'Overig')
            glpvda_by_date[date][area] += 1
        
        for pos in college_pos:
            date = pos.get('datum', 'onbekend')[:7]  # YYYY-MM
            area = pos.get('beleidsterrein', 'Overig')
            college_by_date[date][area] += 1
        
        # Identify sustained topics (appearing in multiple months)
        sustained_topics = {}
        for area in set([a for d in glpvda_by_date.values() for a in d.keys()]):
            months_mentioned = len([d for d in glpvda_by_date.values() if area in d])
            if months_mentioned > 1:  # Sustained if mentioned in 2+ months
                sustained_topics[area] = {
                    'months_mentioned': months_mentioned,
                    'frequency': sum(d.get(area, 0) for d in glpvda_by_date.values()),
                    'type': 'SUSTAINED'
                }
            else:
                sustained_topics[area] = {
                    'months_mentioned': months_mentioned,
                    'frequency': sum(d.get(area, 0) for d in glpvda_by_date.values()),
                    'type': 'SITUATIONAL'
                }
        
        return {
            'sustained_vs_situational': sustained_topics,
            'total_time_periods': len(glpvda_by_date)
        }
    
    def _compute_divergence_patterns(self, glpvda_freq: Dict, college_freq: Dict) -> Dict[str, Any]:
        """Compute where GL-PvdA and College have different emphasis"""
        
        divergence = {}
        all_areas = set(list(glpvda_freq.keys()) + list(college_freq.keys()))
        
        for area in all_areas:
            gl_freq = glpvda_freq.get(area, {}).get('totaal', 0)
            coll_freq = college_freq.get(area, {}).get('totaal', 0)
            
            total = gl_freq + coll_freq
            if total == 0:
                continue
            
            # Divergence score: how much do they differ in their focus?
            # 0.0 = same emphasis, 1.0 = completely opposite
            gl_ratio = gl_freq / total if total > 0 else 0
            coll_ratio = coll_freq / total if total > 0 else 0
            
            divergence_score = abs(gl_ratio - coll_ratio)
            
            divergence[area] = {
                'gl_frequency': gl_freq,
                'college_frequency': coll_freq,
                'gl_ratio': round(gl_ratio, 2),
                'college_ratio': round(coll_ratio, 2),
                'divergence_score': round(divergence_score, 2),
                'interpretation': self._interpret_divergence(divergence_score, gl_freq, coll_freq)
            }
        
        # Sort by divergence score (highest first)
        sorted_divergence = dict(sorted(divergence.items(), key=lambda x: x[1]['divergence_score'], reverse=True))
        
        return sorted_divergence
    
    def _interpret_divergence(self, score: float, gl_freq: int, coll_freq: int) -> str:
        """Interpret divergence score"""
        
        if score < 0.2:
            return "GEBALANCEERD: Vergelijkbare aandacht"
        elif score < 0.4:
            return "MATIG VERSCHIL: Enige voorkeur verschil"
        elif score < 0.6:
            return "DUIDELIJK VERSCHIL: Verschillende prioriteiten"
        else:
            if gl_freq > coll_freq:
                return "HOOG VERSCHIL: GL-PvdA veel meer aandacht"
            else:
                return "HOOG VERSCHIL: College veel meer aandacht"
    
    def _generate_summary(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Generate summary of trends"""
        
        glpvda_freq = results['policy_area_frequencies'].get('groenlinks_pvda', {})
        college_freq = results['policy_area_frequencies'].get('college_bw', {})
        divergence = results['divergence_patterns']
        
        # Top policy areas for each
        glpvda_top = sorted(glpvda_freq.items(), key=lambda x: x[1]['totaal'], reverse=True)[:3]
        college_top = sorted(college_freq.items(), key=lambda x: x[1]['totaal'], reverse=True)[:3]
        
        # Highest divergence areas
        divergence_top = sorted(divergence.items(), key=lambda x: x[1]['divergence_score'], reverse=True)[:3]
        
        return {
            'glpvda_top_areas': [{'area': area, 'frequency': freq['totaal']} for area, freq in glpvda_top],
            'college_top_areas': [{'area': area, 'frequency': freq['totaal']} for area, freq in college_top],
            'highest_divergence_areas': [{'area': area, 'divergence': div['divergence_score']} for area, div in divergence_top],
            'total_unique_areas': len(set(list(glpvda_freq.keys()) + list(college_freq.keys())))
        }
