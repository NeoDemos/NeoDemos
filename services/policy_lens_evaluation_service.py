#!/usr/bin/env python3
"""
Policy Lens Evaluation Service

Evaluates Rotterdam's actual policies THROUGH a chosen party's 
ideological framework and stated values.

This is the core of NeoDemos: "through the lens of the party of your choice"
"""

import json
import google.genai as genai
from typing import Dict, List, Any, Optional
from datetime import datetime
import os
import re
from .llm_alignment_scorer import LLMAlignmentScorer

class PolicyLensEvaluationService:
    """Evaluate Rotterdam policies through party's ideological lens"""
    
    def __init__(self, party_name: str = "GroenLinks-PvdA"):
        self.party_name = party_name
        self.party_profile = {}
        self.rotterdam_policies = {}
        
        # Initialize Gemini for policy analysis
        api_key = os.getenv('GEMINI_API_KEY')
        if api_key:
            self.client = genai.Client(api_key=api_key)
            self.model_id = 'gemini-3-flash-preview'
            # Initialize LLM alignment scorer for enhanced semantic analysis
            try:
                self.llm_scorer = LLMAlignmentScorer(party_name=party_name)
                self.use_llm_scoring = True
                print(f"✓ LLM alignment scorer initialized for {party_name}")
            except Exception as e:
                print(f"⚠ LLM scorer initialization failed: {e}")
                self.llm_scorer = None
                self.use_llm_scoring = False
        else:
            self.client = None
            self.llm_scorer = None
            self.use_llm_scoring = False
            print("⚠ GEMINI_API_KEY not set - LLM analysis disabled")
    
    def load_party_profile(self, profile_file: str) -> bool:
        """Load party position profile from JSON"""
        try:
            with open(profile_file, 'r') as f:
                self.party_profile = json.load(f)
            return True
        except FileNotFoundError:
            print(f"Partijprofiel bestand niet gevonden: {profile_file}")
            return False
    
    def load_rotterdam_policies(self, policy_file: str) -> bool:
        """Load Rotterdam policies from JSON"""
        try:
            with open(policy_file, 'r') as f:
                self.rotterdam_policies = json.load(f)
            return True
        except FileNotFoundError:
            print(f"Rotterdam beleid bestand niet gevonden: {policy_file}")
            return False
    
    def evaluate_policies_through_lens(self) -> Dict[str, Any]:
        """
        Evaluate Rotterdam policies through the party's ideological lens
        """
        
        print(f"\n{'='*70}")
        print(f"ROTTERDAM BELEID EVALUATIE")
        print(f"Vanuit het perspectief van: {self.party_name}")
        print(f"{'='*70}\n")
        
        if not self.party_profile:
            print("✗ Partijprofiel niet geladen")
            return {}
        
        result = {
            'evaluatie_datum': datetime.now().isoformat(),
            'partij': self.party_name,
            'rotterdam_perspectief': {},
            'samenvatting': {}
        }
        
        try:
            # Extract party's core values
            print("[1/3] Extracting party values...")
            core_values = self.party_profile.get('kernwaarden', [])
            positions = self.party_profile.get('posities', {})
            print(f"  ✓ {len(core_values)} kernwaarden identified")
            
            # Evaluate each policy through the lens
            print("\n[2/3] Evaluating Rotterdam policies through party lens...")
            
            # Get policies from raadsvoorstel file
            try:
                with open('data/pipeline/raadsvoorstel_2024_2025.json', 'r') as f:
                    policy_data = json.load(f)
                policies = policy_data.get('voorstellen', {})
            except:
                policies = {}
            
            evaluations = {}
            policy_count = 0
            
            # Evaluate each policy area
            for area in positions.keys():
                party_position = positions.get(area, {})
                
                # Find related Rotterdam policies
                area_policies = policies.get(area, []) if isinstance(policies, dict) else []
                
                evaluation = {
                    'beleidsgebied': area,
                    'partij_visie': party_position.get('uit_programma', 'Niet expliciet'),
                    'rotterdam_beleid': self._summarize_area_policies(area_policies),
                    'afstemming': self._assess_alignment(
                        party_position,
                        area_policies,
                        core_values
                    ),
                    'partij_respons': self._assess_party_response(party_position),
                    'gaten': self._identify_gaps(
                        party_position,
                        area_policies
                    )
                }
                
                evaluations[area] = evaluation
                policy_count += 1
            
            print(f"  ✓ {policy_count} beleidsgebieden geëvalueerd")
            
            # Step 3: Summarize overall alignment
            print("\n[3/3] Calculating overall alignment...")
            
            result['rotterdam_perspectief'] = evaluations
            result['samenvatting'] = self._generate_overall_summary(evaluations)
            
            print(f"  ✓ Overall alignment: {result['samenvatting'].get('totale_afstemming', 'N/A')}")
            
            return result
        
        except Exception as e:
            print(f"✗ Fout: {e}")
            import traceback
            traceback.print_exc()
            return result
    
    def _summarize_area_policies(self, policies: List[Any]) -> str:
        """Summarize Rotterdam policies for an area"""
        
        if not policies:
            return "Geen formele voorstellen gevonden"
        
        if not isinstance(policies, list):
            return "Beleid aanwezig"
        
        summaries = []
        for policy in policies[:3]:  # First 3 policies
            if isinstance(policy, dict):
                title = policy.get('titel', 'Onbekend')
                summaries.append(title[:80])
        
        summary_text = "; ".join(summaries)
        return summary_text if summary_text else "Beleid aanwezig"
    
    def _assess_alignment(
        self,
        party_position: Dict[str, Any],
        area_policies: List[Any],
        core_values: List[str]
    ) -> Dict[str, Any]:
        """
        Assess how well Rotterdam policies align with party's vision.
        
        Uses LLM semantic analysis when available, falls back to heuristics.
        """
        
        party_vision = party_position.get('uit_programma', '')
        policy_text = str(area_policies) if area_policies else ""
        policy_area = party_position.get('beleidsgebied', 'Onbekend')
        
        # Try LLM scoring first if available
        if self.use_llm_scoring and self.llm_scorer and party_vision:
            try:
                alignment = self.llm_scorer.score_alignment(
                    party_position=party_vision,
                    party_core_values=core_values,
                    rotterdam_policy=policy_text,
                    policy_area=policy_area
                )
                
                # Add description
                alignment['beschrijving'] = (
                    f"Rotterdam's beleid {alignment.get('interpretatie', 'neutraal').lower()} "
                    f"met {self.party_name}'s visie"
                )
                return alignment
                
            except Exception as e:
                print(f"  ⚠ LLM scoring failed: {e}, falling back to heuristics")
        
        # Fallback to heuristic scoring
        return self._heuristic_alignment_score(
            party_vision,
            policy_text,
            core_values,
            policy_area
        )
    
    def _heuristic_alignment_score(
        self,
        party_vision: str,
        policy_text: str,
        core_values: List[str],
        policy_area: str
    ) -> Dict[str, Any]:
        """
        Fallback heuristic alignment scoring when LLM is unavailable.
        
        This maintains service reliability while using keyword matching.
        """
        
        alignment_score = 0.5  # Default middle position
        interpretation = "Neutraal"
        
        # Green keywords that align with GroenLinks-PvdA values
        green_keywords = [
            'duurzaam', 'groen', 'klimaat', 'milieu', 'sociaal',
            'inclusief', 'gelijkheid', 'arbeiders', 'werknemers',
            'publiek', 'voorzieningen', 'ondersteuning', 'zorg'
        ]
        
        # Keywords suggesting market-based approach (potential conflicts)
        market_keywords = [
            'markt', 'privatisering', 'commercieel', 'bezuinigingen',
            'effiëntie', 'deregulering', 'concurrentie'
        ]
        
        combined_text = (party_vision + ' ' + policy_text).lower()
        
        # Check for alignment indicators
        if any(keyword in combined_text for keyword in green_keywords):
            alignment_score += 0.2
        
        if any(keyword in combined_text for keyword in market_keywords):
            alignment_score -= 0.2
        
        # Clamp between 0 and 1
        alignment_score = max(0.0, min(1.0, alignment_score))
        
        if alignment_score > 0.7:
            interpretation = "Hoge afstemming"
        elif alignment_score > 0.4:
            interpretation = "Matige afstemming"
        elif alignment_score > 0.2:
            interpretation = "Lage afstemming"
        else:
            interpretation = "Zeer lage afstemming / Tegengesteld"
        
        return {
            'score': round(alignment_score, 2),
            'interpretatie': interpretation,
            'beschrijving': f"Rotterdam's beleid {interpretation.lower()} met {self.party_name}'s visie",
            'bron': 'heuristic'
        }
    
    def _assess_party_response(self, party_position: Dict[str, Any]) -> Dict[str, Any]:
        """Assess what the party actually did in response to policies"""
        
        notulen_refs = party_position.get('uit_notulen', [])
        
        response = {
            'aantal_moties': 0,
            'aantal_vragen': 0,
            'aantal_amendementen': 0,
            'totaal_activiteiten': len(notulen_refs),
            'samenvatting': 'Geen geregistreerde activiteit'
        }
        
        if notulen_refs:
            for ref in notulen_refs:
                ref_type = ref.get('type', '')
                if ref_type == 'motie':
                    response['aantal_moties'] += 1
                elif ref_type == 'vraag':
                    response['aantal_vragen'] += 1
                elif ref_type == 'amendement':
                    response['aantal_amendementen'] += 1
            
            total = response['totaal_activiteiten']
            if total > 0:
                response['samenvatting'] = f"{self.party_name} heeft {total} actieve positie(s) ingenomen"
        
        return response
    
    def _identify_gaps(
        self,
        party_position: Dict[str, Any],
        area_policies: List[Any]
    ) -> List[str]:
        """Identify gaps between party vision and Rotterdam policies"""
        
        gaps = []
        
        party_vision = party_position.get('uit_programma', '')
        consistency = party_position.get('consistentie', '')
        
        # Gap 1: Programme not in notulen
        if party_vision and not party_position.get('uit_notulen'):
            gaps.append(
                f"Programmabelofte niet geuit in raadsnotulen"
            )
        
        # Gap 2: No Rotterdam policies addressing the area
        if not area_policies and party_vision:
            gaps.append(
                f"Rotterdam heeft geen formele voorstellen in dit beleidsgebied"
            )
        
        # Gap 3: Inconsistency between programme and behavior
        if consistency and 'inconsistent' in consistency.lower():
            gaps.append(
                f"Inconsistentie tussen programma en werkelijk gedrag"
            )
        
        return gaps if gaps else ["Geen significante gaten geïdentificeerd"]
    
    def _generate_overall_summary(
        self,
        evaluations: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Generate summary of overall alignment"""
        
        if not evaluations:
            return {'totale_afstemming': 0.0, 'toelichting': 'Geen evaluaties beschikbaar'}
        
        alignment_scores = [
            e.get('afstemming', {}).get('score', 0.5)
            for e in evaluations.values()
        ]
        
        avg_alignment = sum(alignment_scores) / len(alignment_scores) if alignment_scores else 0.5
        
        high_alignment_areas = [
            area for area, eval in evaluations.items()
            if eval.get('afstemming', {}).get('score', 0) > 0.7
        ]
        
        low_alignment_areas = [
            area for area, eval in evaluations.items()
            if eval.get('afstemming', {}).get('score', 1) < 0.3
        ]
        
        total_party_activity = sum(
            e.get('partij_respons', {}).get('totaal_activiteiten', 0)
            for e in evaluations.values()
        )
        
        return {
            'totale_afstemming': round(avg_alignment, 2),
            'aantal_gebieden': len(evaluations),
            'hoge_afstemming_gebieden': high_alignment_areas,
            'lage_afstemming_gebieden': low_alignment_areas,
            'totale_partij_activiteiten': total_party_activity,
            'interpretatie': self._interpret_overall_alignment(avg_alignment)
        }
    
    def _interpret_overall_alignment(self, score: float) -> str:
        """Interpret overall alignment score"""
        
        if score > 0.7:
            return f"{self.party_name}'s ideologische visie stemt goed overeen met Rotterdam's praktische beleid"
        elif score > 0.5:
            return f"Matige afstemming tussen {self.party_name}'s visie en Rotterdam's beleid"
        elif score > 0.3:
            return f"Aanzienlijke verschillen tussen {self.party_name}'s ideologie en Rotterdam's praktijk"
        else:
            return f"{self.party_name}'s visie staat haaks op veel van Rotterdam's beleid"
    
    def evaluate_agenda_item(self, agenda_item_text: str) -> Dict[str, Any]:
        """
        Evaluate a specific meeting agenda item through the party lens
        
        This is the function to test!
        """
        
        print(f"\n{'='*70}")
        print("AGENDA ITEM EVALUATIE")
        print(f"Partij perspectief: {self.party_name}")
        print(f"{'='*70}\n")
        
        if not self.party_profile:
            print("✗ Partijprofiel niet geladen")
            return {}
        
        result = {
            'evaluatie_datum': datetime.now().isoformat(),
            'partij': self.party_name,
            'agenda_item': agenda_item_text[:100],
            'analyse': {},
            'aanbevelingen': []
        }
        
        try:
            # Check if LLM Scorer is available for rich analysis
            if self.use_llm_scoring and self.llm_scorer:
                print("[1/2] Using sophisticated LLM Scorer for deep semantic analysis...")
                llm_result = self.llm_scorer.score_agenda_item(agenda_item_text, self.party_profile)
                
                alignment = llm_result.get('alignment', {})
                policy_area = llm_result.get('policy_area', 'Onbekend')
                party_position_data = self.party_profile.get('posities', {}).get(policy_area, {})
                partij_visie = party_position_data.get('kernwaarde', 'Algemeen beleid')
                
                print(f"  ✓ Beleidsgebied: {policy_area}")
                print(f"  ✓ Alignment score: {alignment.get('score', 0.5)}")
                
                # Assemble the result exactly how the frontend expects it
                result['analyse'] = {
                    'beleidsgebied': policy_area,
                    'partij_visie': partij_visie if partij_visie != 'Onbekend' else 'Algemeen beleid',
                    'gedetailleerde_analyse': alignment.get('analyse', ''),
                    'afstemming_score': alignment.get('score', 0.5),
                    'afstemming_interpretatie': alignment.get('interpretatie', 'Geen interpretatie beschikbaar'),
                    'positieve_punten': alignment.get('positieve_punten', []),
                    'kritische_punten': alignment.get('kritische_punten', []),
                    'vraag_suggesties': alignment.get('vraag_suggesties', []),
                    'tegenvoorstel_suggesties': alignment.get('tegenvoorstel_suggesties', [])
                }
                
                # Use recommendations from LLM result (which now prioritizes LLM-generated ones)
                recs = llm_result.get('recommendations', [])
                
                # Filter out generic high-level observations that the LLM might still produce
                generic_phrases = ["sluit aan bij", "wijkt af van", "ideologische positie", "kernwaarde"]
                recs = [r for r in recs if not any(phrase in r.lower() for phrase in generic_phrases)]
                
                if not recs:
                    recs = self._generate_party_recommendations(agenda_item_text, party_position_data, alignment)
                
                # Remove duplicates while preserving order
                seen = set()
                result['aanbevelingen'] = [x for x in recs if not (x in seen or seen.add(x))]
                
                print(f"  ✓ {len(result['aanbevelingen'])} recommendations generated")
                return result
                
            # Fallback routine if LLM Scorer is not available
            # Step 1: Categorize agenda item
            print("[1/4] Categorizing agenda item...")
            policy_area = self._categorize_agenda_item(agenda_item_text)
            print(f"  ✓ Beleidsgebied: {policy_area}")
            
            # Step 2: Extract party position on this area
            print("\n[2/4] Extracting party position on this area...")
            party_position = self.party_profile.get('posities', {}).get(policy_area, {})
            print(f"  ✓ Party position: {party_position.get('kernwaarde', 'Algemeen beleid')}")
            
            # Step 3: Assess alignment with party's values
            print("\n[3/4] Assessing agenda item alignment...")
            alignment = self._assess_agenda_alignment(agenda_item_text, party_position)
            print(f"  ✓ Alignment score: {alignment.get('score', 'N/A')}")
            
            # Step 4: Generate recommendations
            print("\n[4/4] Generating party-aligned recommendations...")
            recommendations = self._generate_party_recommendations(
                agenda_item_text,
                party_position,
                alignment
            )
            print(f"  ✓ {len(recommendations)} recommendations generated")
            
            result['analyse'] = {
                'beleidsgebied': policy_area,
                'partij_visie': party_position.get('kernwaarde', 'Algemeen beleid'),
                'gedetailleerde_analyse': '',
                'afstemming_score': alignment.get('score', 0.5),
                'afstemming_interpretatie': alignment.get('interpretatie', 'Onbekend'),
                'sterke_punten': [],
                'kritische_punten': []
            }
            result['aanbevelingen'] = recommendations
            
            return result
        
        except Exception as e:
            print(f"✗ Fout: {e}")
            import traceback
            traceback.print_exc()
            return result
    
    def _categorize_agenda_item(self, text: str) -> str:
        """Categorize agenda item into policy area"""
        
        categories = {
            'Wonen': ['woning', 'huur', 'bouw', 'stedelijk', 'leegstand'],
            'Klimaat': ['klimaat', 'duurzaam', 'energie', 'groen', 'co2'],
            'Mobiliteit': ['verkeer', 'fiets', 'auto', 'openbaar vervoer', 'parkeer'],
            'Onderwijs': ['school', 'onderwijs', 'student', 'jeugd'],
            'Zorg': ['zorg', 'gezondheid', 'ouderen', 'jeugdzorg', 'wmo'],
            'Economie': ['economie', 'bedrijf', 'werk', 'handel', 'vestiging'],
            'Inclusiviteit': ['inclusief', 'gelijk', 'migratie', 'discriminatie', 'diversiteit'],
            'Veiligheid': ['veiligheid', 'politie', 'criminaliteit', 'handhaving'],
            'Overig': ['overig', 'algemeen', 'diversen']
        }
        
        text_lower = text.lower()
        
        for category, keywords in categories.items():
            for keyword in keywords:
                if keyword in text_lower:
                    return category
        
        return 'Overig'
    
    def _assess_agenda_alignment(
        self,
        agenda_text: str,
        party_position: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Assess how well agenda item aligns with party's values using LLM"""
        
        # If LLM is available, use semantic scoring
        if self.use_llm_scoring and self.client:
            try:
                return self._assess_alignment_with_llm(agenda_text, party_position)
            except Exception as e:
                print(f"LLM scoring failed, falling back to heuristic: {e}")
                return self._assess_alignment_heuristic(agenda_text, party_position)
        else:
            return self._assess_alignment_heuristic(agenda_text, party_position)
    
    def _assess_alignment_with_llm(
        self,
        agenda_text: str,
        party_position: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Use Gemini 3 Flash for semantic alignment assessment"""
        
        party_vision = party_position.get('uit_programma', 'Geen expliciete positie')
        policy_area = party_position.get('beleidsgebied', 'Onbekend')
        core_value = party_position.get('kernwaarde', 'Algemeen beleid')
        
        prompt = f"""Je bent een expert in Nederlandse stadsbestuur en politieke ideologie.

PARTIJ: {self.party_name}
BELEIDSGEBIED: {policy_area}
KERNWAARDE: {core_value}
PARTIJ POSITIE: {party_vision}

AGENDAPUNT (volledig - GEEN TRUNCATIE):
{agenda_text}

Evalueer hoe goed dit agendapunt aansluit bij {self.party_name}'s ideologie en kernwaarde.

Antwoord ALLEEN met deze JSON (geen extra tekst):
{{
    "score": 0.0-1.0,
    "interpretatie": "Korte Nederlandse samenvatting van afstemming (max 15 woorden)"
}}"""
        
        try:
            response = self.client.models.generate_content(
                model="gemini-3-flash-preview",
                contents=prompt
            )
            
            response_text = response.text.strip()
            # Handle potential markdown formatting
            if response_text.startswith('```'):
                response_text = response_text.split('```')[1].strip()
                if response_text.startswith('json'):
                    response_text = response_text[4:].strip()
            
            data = json.loads(response_text)
            
            return {
                'score': round(float(data.get('score', 0.5)), 2),
                'interpretatie': str(data.get('interpretatie', 'Evaluatie incomplete'))[:100]
            }
        except Exception as e:
            print(f"LLM evaluation error: {e}")
            return self._assess_alignment_heuristic(agenda_text, party_position)
    
    def _assess_alignment_heuristic(
        self,
        agenda_text: str,
        party_position: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Fallback heuristic-based keyword matching"""
        
        # Default moderate alignment
        score = 0.5
        interpretation = "Matig relevant"
        
        # Check if party has stated position on this area
        party_vision = party_position.get('uit_programma', '').lower()
        
        if party_vision:
            # Simple keyword matching
            agenda_lower = agenda_text.lower()
            
            # Count matching keywords
            matches = 0
            for word in party_vision.split():
                if len(word) > 3 and word in agenda_lower:
                    matches += 1
            
            if matches > 3:
                score = 0.8
                interpretation = "Zeer relevant voor partij"
            elif matches > 0:
                score = 0.6
                interpretation = "Relevant voor partij"
            else:
                score = 0.3
                interpretation = "Minder relevant voor partij"
        else:
            score = 0.4
            interpretation = "Partij heeft geen expliciete positie"
        
        return {
            'score': round(score, 2),
            'interpretatie': interpretation
        }
    
    def _generate_party_recommendations(
        self,
        agenda_text: str,
        party_position: Dict[str, Any],
        alignment: Dict[str, Any]
    ) -> List[str]:
        """Generate recommendations based on party's values"""
        
        recommendations = []
        
        core_value = party_position.get('kernwaarde', 'Algemeen beleid')
        if not core_value or core_value == 'Onbekend':
            core_value = 'Algemeen beleid'
            
        consistency = party_position.get('consistentie', '')
        
        # Recommendation 1: Based on party values - only if it's a specific recommendation (action)
        # However, the user wants this out of recommendations if it's just an observation.
        # So we skip adding it to 'recommendations' list here and let it be in analysis instead.
        
        # Recommendation 2: Check party activity
        activities = party_position.get('uit_notulen', [])
        if activities:
            recommendations.append(
                f"{self.party_name} heeft {len(activities)} positie(s) op dit gebied geuit in de raad"
            )
        
        # Recommendation 3: Consistency check
        if 'inconsistent' in consistency.lower():
            recommendations.append(
                "Let op: Mogelijke inconsistentie tussen programma en werkelijk gedrag"
            )
        
        
        return recommendations
