#!/usr/bin/env python3
"""
STAP 4: Drie-weg Vergelijking

Synthesizes all three layers of analysis:
1. Layer 1 (Formal): GL-PvdA programme promises vs College formal proposals
2. Layer 2 (Implicit): GL-PvdA actual positions from notulen vs College behavior
3. Layer 3 (Trends): Frequency patterns showing sustained vs situational emphasis

Creates comprehensive alignment scores and identifies key divergence points.
"""

import json
from typing import Dict, List, Any, Tuple
from datetime import datetime
from collections import defaultdict

class ThreeWayComparisonService:
    """Compare GL-PvdA positions across all three layers"""
    
    def __init__(self):
        self.glpvda_programme = {}
        self.college_proposals = {}
        self.glpvda_notulen = {}
        self.college_implicit = {}
        self.trend_data = {}
    
    def load_all_data(self) -> bool:
        """Load all data files"""
        try:
            with open('data/pipeline/groenlinks_pvda_detailed_proposals.json', 'r') as f:
                self.glpvda_programme = json.load(f)
            
            with open('data/pipeline/raadsvoorstel_2024_2025.json', 'r') as f:
                self.college_proposals = json.load(f)
            
            with open('data/pipeline/groenlinks_pvda_notulen_positions.json', 'r') as f:
                self.glpvda_notulen = json.load(f)
            
            with open('data/pipeline/college_bw_implicit_positions.json', 'r') as f:
                self.college_implicit = json.load(f)
            
            with open('data/pipeline/trend_analysis_results.json', 'r') as f:
                self.trend_data = json.load(f)
            
            return True
        except Exception as e:
            print(f"Fout bij laden data: {e}")
            return False
    
    def perform_comparison(self) -> Dict[str, Any]:
        """Execute three-way comparison"""
        
        print(f"\n{'='*70}")
        print("STAP 4: DRIE-WEG VERGELIJKING")
        print("Synthesizing all three layers of analysis")
        print(f"{'='*70}\n")
        
        # Load data
        print("[1/5] Loading all data sources...")
        if not self.load_all_data():
            return {'error': 'Could not load data'}
        print("  ✓ All data loaded successfully")
        
        # Extract key policy areas
        print("\n[2/5] Extracting policy area coverage...")
        policy_areas = self._extract_policy_areas()
        
        # Build comprehensive profiles
        print(f"\n[3/5] Building layer profiles for {len(policy_areas)} policy areas...")
        layer_profiles = self._build_layer_profiles(policy_areas)
        
        # Compute alignment scores
        print("\n[4/5] Computing alignment scores...")
        alignments = self._compute_alignments(layer_profiles)
        
        # Identify key divergences
        print("\n[5/5] Identifying divergence patterns...")
        divergences = self._identify_divergences(layer_profiles, alignments)
        
        # Compile results
        results = {
            'comparison_date': datetime.now().isoformat(),
            'type': 'three_way_comparison',
            'summary': {
                'policy_areas_analyzed': len(policy_areas),
                'total_divergence_score': round(sum(a['overall_alignment'] for a in alignments.values()) / len(alignments), 3) if alignments else 0,
                'highest_alignment_areas': sorted(alignments.items(), key=lambda x: x[1]['overall_alignment'], reverse=True)[:3],
                'lowest_alignment_areas': sorted(alignments.items(), key=lambda x: x[1]['overall_alignment'])[:3]
            },
            'policy_area_profiles': layer_profiles,
            'alignment_scores': alignments,
            'key_divergences': divergences,
            'statistical_summary': self._generate_statistical_summary(layer_profiles, alignments)
        }
        
        return results
    
    def _extract_policy_areas(self) -> set:
        """Extract all unique policy areas mentioned"""
        
        areas = set()
        
        # From GL-PvdA programme (dict structure)
        voorstellen = self.glpvda_programme.get('voorstellen', {})
        if isinstance(voorstellen, dict):
            areas.update(voorstellen.keys())
        else:
            for prop in voorstellen:
                if isinstance(prop, dict) and 'beleidsterrein' in prop:
                    areas.add(prop['beleidsterrein'])
        
        # From College proposals
        voorstellen = self.college_proposals.get('voorstellen', {})
        if isinstance(voorstellen, dict):
            areas.update(voorstellen.keys())
        else:
            for prop in voorstellen:
                if isinstance(prop, dict) and 'beleidsterrein' in prop:
                    areas.add(prop['beleidsterrein'])
        
        # From GL-PvdA notulen
        for pos in self.glpvda_notulen.get('posities', []):
            if 'beleidsterrein' in pos:
                areas.add(pos['beleidsterrein'])
        
        # From College implicit
        for pos in self.college_implicit.get('posities', []):
            if 'beleidsterrein' in pos:
                areas.add(pos['beleidsterrein'])
        
        return areas
    
    def _build_layer_profiles(self, policy_areas: set) -> Dict[str, Dict[str, Any]]:
        """Build comprehensive profile for each policy area across layers"""
        
        profiles = {}
        
        for area in policy_areas:
            profiles[area] = {
                'area': area,
                'layer1_formal': self._get_formal_layer_profile(area),
                'layer2_implicit': self._get_implicit_layer_profile(area),
                'layer3_trends': self._get_trend_layer_profile(area),
            }
        
        return profiles
    
    def _get_formal_layer_profile(self, area: str) -> Dict[str, Any]:
        """Get formal layer (programme vs proposals) profile"""
        
        # GL-PvdA programme is dict keyed by area
        glpvda_voorstellen = self.glpvda_programme.get('voorstellen', {})
        if isinstance(glpvda_voorstellen, dict):
            glpvda_count = len(glpvda_voorstellen.get(area, []))
        else:
            glpvda_count = sum(1 for p in glpvda_voorstellen if isinstance(p, dict) and p.get('beleidsterrein') == area)
        
        # College proposals is dict keyed by area
        college_voorstellen = self.college_proposals.get('voorstellen', {})
        if isinstance(college_voorstellen, dict):
            college_count = len(college_voorstellen.get(area, []))
        else:
            college_count = sum(1 for p in college_voorstellen if isinstance(p, dict) and p.get('beleidsterrein') == area)
        
        return {
            'glpvda_programme_proposals': glpvda_count,
            'college_formal_proposals': college_count,
            'covered': glpvda_count > 0 or college_count > 0
        }
    
    def _get_implicit_layer_profile(self, area: str) -> Dict[str, Any]:
        """Get implicit layer (actual behavior) profile"""
        
        glpvda_count = sum(1 for p in self.glpvda_notulen.get('posities', []) 
                          if p.get('beleidsterrein') == area)
        college_count = sum(1 for p in self.college_implicit.get('posities', []) 
                           if p.get('beleidsterrein') == area)
        
        glpvda_avg_confidence = self._compute_avg_confidence(
            [p for p in self.glpvda_notulen.get('posities', []) if p.get('beleidsterrein') == area]
        )
        college_avg_confidence = self._compute_avg_confidence(
            [p for p in self.college_implicit.get('posities', []) if p.get('beleidsterrein') == area]
        )
        
        return {
            'glpvda_notulen_positions': glpvda_count,
            'college_implicit_positions': college_count,
            'glpvda_avg_confidence': round(glpvda_avg_confidence, 2),
            'college_avg_confidence': round(college_avg_confidence, 2),
            'covered': glpvda_count > 0 or college_count > 0
        }
    
    def _get_trend_layer_profile(self, area: str) -> Dict[str, Any]:
        """Get trend layer profile"""
        
        divergence_data = self.trend_data.get('divergence_patterns', {}).get(area, {})
        
        return {
            'glpvda_frequency': divergence_data.get('gl_frequency', 0),
            'college_frequency': divergence_data.get('college_frequency', 0),
            'divergence_score': divergence_data.get('divergence_score', 0.0),
            'interpretation': divergence_data.get('interpretation', 'N/A')
        }
    
    def _compute_alignments(self, profiles: Dict[str, Dict]) -> Dict[str, Dict[str, Any]]:
        """Compute alignment scores for each policy area"""
        
        alignments = {}
        
        for area, profile in profiles.items():
            formal = profile['layer1_formal']
            implicit = profile['layer2_implicit']
            trends = profile['layer3_trends']
            
            # Compute overall alignment (inverse of divergence)
            # Weighted: Layer 1 (30%), Layer 2 (40%), Layer 3 (30%)
            
            # Layer 1: presence of both GL and College proposals
            l1_alignment = self._compute_layer_alignment(
                formal['glpvda_programme_proposals'],
                formal['college_formal_proposals'],
                max_score=1.0
            )
            
            # Layer 2: position confidence and presence
            l2_alignment = self._compute_layer_alignment(
                implicit['glpvda_notulen_positions'],
                implicit['college_implicit_positions'],
                max_score=1.0
            ) * min(implicit['glpvda_avg_confidence'], implicit['college_avg_confidence'])
            
            # Layer 3: inverse of divergence score (1.0 - divergence)
            l3_alignment = 1.0 - trends['divergence_score']
            
            # Weighted overall alignment (higher = more aligned)
            overall = (l1_alignment * 0.3 + l2_alignment * 0.4 + l3_alignment * 0.3)
            
            alignments[area] = {
                'layer1_alignment': round(l1_alignment, 2),
                'layer2_alignment': round(l2_alignment, 2),
                'layer3_alignment': round(l3_alignment, 2),
                'overall_alignment': round(overall, 2),
                'interpretation': self._interpret_alignment(overall)
            }
        
        return alignments
    
    def _compute_layer_alignment(self, glpvda_count: int, college_count: int, max_score: float = 1.0) -> float:
        """Compute alignment for a layer based on counts"""
        
        total = glpvda_count + college_count
        if total == 0:
            return 0.0
        
        # Alignment increases when counts are similar
        ratio = min(glpvda_count, college_count) / max(glpvda_count, college_count) if max(glpvda_count, college_count) > 0 else 0
        return ratio * max_score
    
    def _compute_avg_confidence(self, positions: List[Dict]) -> float:
        """Compute average confidence score"""
        
        if not positions:
            return 0.0
        
        return sum(p.get('confidence', 0.0) for p in positions) / len(positions)
    
    def _interpret_alignment(self, score: float) -> str:
        """Interpret alignment score"""
        
        if score >= 0.75:
            return "HOGE AFSTEMMING: Sterke overlap"
        elif score >= 0.5:
            return "MATIGE AFSTEMMING: Enige samenhang"
        elif score >= 0.25:
            return "LAGE AFSTEMMING: Weinig overlap"
        else:
            return "GEEN AFSTEMMING: Geen relatie"
    
    def _identify_divergences(self, profiles: Dict[str, Dict], alignments: Dict[str, Dict]) -> List[Dict[str, Any]]:
        """Identify key divergence patterns"""
        
        divergences = []
        
        # Sort by lowest alignment
        sorted_areas = sorted(alignments.items(), key=lambda x: x[1]['overall_alignment'])
        
        for area, alignment in sorted_areas[:5]:  # Top 5 divergences
            profile = profiles[area]
            
            divergence = {
                'policy_area': area,
                'overall_alignment': alignment['overall_alignment'],
                'layer_breakdown': {
                    'layer1_formal': alignment['layer1_alignment'],
                    'layer2_implicit': alignment['layer2_alignment'],
                    'layer3_trends': alignment['layer3_alignment']
                },
                'specific_divergences': []
            }
            
            # Identify specific divergence patterns
            formal = profile['layer1_formal']
            implicit = profile['layer2_implicit']
            trends = profile['layer3_trends']
            
            if formal['glpvda_programme_proposals'] > 0 and formal['college_formal_proposals'] == 0:
                divergence['specific_divergences'].append(
                    "GL-PvdA heeft programmavoorstel, College geen formeel voorstel"
                )
            
            if implicit['glpvda_notulen_positions'] == 0 and formal['glpvda_programme_proposals'] > 0:
                divergence['specific_divergences'].append(
                    "GL-PvdA heeft programmavoorstel maar niet in notulen genoemd"
                )
            
            if trends['divergence_score'] > 0.7:
                divergence['specific_divergences'].append(
                    f"Hoge frequentieverschil: GL {trends['glpvda_frequency']} vs College {trends['college_frequency']}"
                )
            
            divergences.append(divergence)
        
        return divergences
    
    def _generate_statistical_summary(self, profiles: Dict[str, Dict], alignments: Dict[str, Dict]) -> Dict[str, Any]:
        """Generate statistical summary"""
        
        alignment_scores = [a['overall_alignment'] for a in alignments.values()]
        layer1_scores = [a['layer1_alignment'] for a in alignments.values()]
        layer2_scores = [a['layer2_alignment'] for a in alignments.values()]
        layer3_scores = [a['layer3_alignment'] for a in alignments.values()]
        
        return {
            'overall_alignment_avg': round(sum(alignment_scores) / len(alignment_scores), 3) if alignment_scores else 0,
            'layer1_avg': round(sum(layer1_scores) / len(layer1_scores), 3) if layer1_scores else 0,
            'layer2_avg': round(sum(layer2_scores) / len(layer2_scores), 3) if layer2_scores else 0,
            'layer3_avg': round(sum(layer3_scores) / len(layer3_scores), 3) if layer3_scores else 0,
            'total_policy_areas': len(profiles),
            'high_alignment_areas': sum(1 for s in alignment_scores if s >= 0.75),
            'low_alignment_areas': sum(1 for s in alignment_scores if s < 0.25)
        }
