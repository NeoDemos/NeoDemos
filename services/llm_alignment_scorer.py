#!/usr/bin/env python3
"""
LLM-Enhanced Alignment Scoring Service

Replaces simple keyword matching with sophisticated semantic analysis
using Google's Gemini Flash 3 model to evaluate alignment between
Rotterdam policies and party ideological positions.

This service provides semantic understanding rather than pattern matching.
"""

import json
import os
import re
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
import google.genai as genai


class LLMAlignmentScorer:
    """Use LLM for sophisticated semantic alignment scoring"""
    
    def __init__(self, party_name: str = "GroenLinks-PvdA"):
        """Initialize LLM scorer with party context"""
        self.party_name = party_name
        self.model_id = 'gemini-3-flash-preview'  # Match the model used in ai_service
        
        # Initialize Gemini client
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set in environment")
        
        # Create client directly with API key
        self.client = genai.Client(api_key=api_key)
    
    def score_alignment(
        self,
        party_position: str,
        party_core_values: List[str],
        rotterdam_policy: str,
        policy_area: str,
        historical_context: str = ""
    ) -> Dict[str, Any]:
        """
        Score alignment between a Rotterdam policy and a party's position
        using semantic analysis via LLM.
        
        Args:
            party_position: The party's stated position on this policy area
            party_core_values: The party's core ideological values
            rotterdam_policy: The Rotterdam policy to evaluate
            policy_area: The policy area category
            historical_context: Previously retrieved historical context for RAG
        
        Returns:
            Dict with score (0-1), reasoning, and recommendations
        """
        
        # Build the evaluation prompt
        prompt = self._build_evaluation_prompt(
            party_position,
            party_core_values,
            rotterdam_policy,
            policy_area,
            historical_context
        )
        
        try:
            # Call Gemini for semantic analysis
            # Note: google.genai.Client uses different parameter names
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt
            )
            
            # Parse the response
            result = self._parse_llm_response(response.text)
            return result
            
        except Exception as e:
            print(f"⚠ LLM scoring error: {e}")
            # Fallback to heuristic scoring if LLM fails
            return self._fallback_heuristic_score(
                party_position,
                rotterdam_policy,
                policy_area
            )
    
    def score_agenda_item(
        self,
        agenda_item_text: str,
        party_profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Score how well an entire agenda item aligns with party positions.
        
        Args:
            agenda_item_text: Full text of the agenda item
            party_profile: Party's position profile
        
        Returns:
            Comprehensive alignment assessment
        """
        
        core_values = party_profile.get('kernwaarden', [])
        positions = party_profile.get('posities', {})
        
        # First, categorize the agenda item
        category_result = self._categorize_agenda_item(agenda_item_text)
        policy_area = category_result['area']
        
        # Get the party's position on this area
        party_position_data = positions.get(policy_area, {})
        party_statement = party_position_data.get('uit_programma', '')
        
        if not party_statement:
            party_statement = party_position_data.get('samenvatting', f"No specific position on {policy_area}")
        
        # Retrieve historical context using RAG
        try:
            from services.rag_service import RAGService
            rag = RAGService()
            relevant_chunks = rag.retrieve_relevant_context(
                query_text=f"{policy_area} {agenda_item_text[:500]}",
                top_k=8
            )
            historical_context = rag.format_retrieved_context(relevant_chunks) if relevant_chunks else ""
        except Exception as e:
            print(f"RAG Retrieval error: {e}")
            historical_context = ""

        # Score the alignment
        alignment = self.score_alignment(
            party_position=party_statement,
            party_core_values=core_values,
            rotterdam_policy=agenda_item_text,
            policy_area=policy_area,
            historical_context=historical_context
        )
        
        # Generate recommendations
        recommendations = self._generate_recommendations(
            alignment=alignment,
            party_position=party_statement,
            policy_area=policy_area,
            core_values=core_values
        )
        
        return {
            'policy_area': policy_area,
            'alignment': alignment,
            'recommendations': recommendations,
            'category_confidence': category_result.get('confidence', 0.7)
        }
    
    def _build_evaluation_prompt(
        self,
        party_position: str,
        party_core_values: List[str],
        rotterdam_policy: str,
        policy_area: str,
        historical_context: str = ""
    ) -> str:
        """
        Build a sophisticated prompt for LLM alignment evaluation.
        
        The prompt guides the LLM to provide semantic analysis rather than
        simple pattern matching.
        """
        
        values_text = "\n".join([f"  - {v}" for v in party_core_values[:5]])
        
        prompt = f"""Je bent een expert op het gebied van Nederlandse lokale politiek en beleidsanalyse.

Jouw taak is om te evalueren hoe goed een Rotterdam-beleidsstuk aansluit bij de ideologische visie van {self.party_name}.

**KERNWAARDEN VAN {self.party_name.upper()}:**
{values_text}

**POSITIE VAN {self.party_name.upper()} OP BELEIDSGEBIED '{policy_area.upper()}':**
{party_position}

**ROTTERDAM-BELEIDSSTUK TER EVALUATIE:**
{rotterdam_policy}
"""
        
        if historical_context:
            prompt += f"\n**HISTORISCHE CONTEXT UIT DE GEMEENTERAADSNOTULEN:**\n{historical_context}\n"

        prompt += f"""
Voer nu een grondige semantische analyse uit met de focus op het partijperspectief:

1. **ALIGNMENT SCORE** (op schaal 0.0-1.0):
   - 0.0-0.2: Sterk in tegenspraak met de partij-ideologie
   - 0.2-0.4: Belangrijke verschillen, beperkte afstemming
   - 0.4-0.6: Gemengde afstemming, enkele overeenkomsten en verschillen
   - 0.6-0.8: Goede afstemming, enkele kritische punten
   - 0.8-1.0: Zeer sterke afstemming met kernwaarden

2. **HISTORISCHE CONTEXT & PARTIJ VISIE** (vul in onder 'analyse'): Beschrijf de context en visie voor dit onderwerp. 
   - BELANGRIJK: Herhaal NIET de algemene samenvatting van het document. Bouw daar juist op voort.
   - Hoe heeft {self.party_name} zich in het (recente) verleden uitgesproken over dit of soortgelijke onderwerpen?
   - Hoe past dit specifieke beleid binnen de grotere visie van de partij?

3. **SUGGESTIES**: Geef tastbare politieke munitie voor {self.party_name}.
   - **Vraag suggesties**: 2-3 scherpe, kritische vragen die het raadslid kan stellen tijdens het debat.
   - **Tegenvoorstel suggesties**: 1-2 concrete ideeën voor een motie of amendement om het beleid beter in lijn te brengen met de partijvisie. Wees specifiek (bijv. "Sla een brug tussen X en Y middels een motie").

Antwoord in JSON-formaat als volgt:
{{
  "score": <getal tussen 0.0 en 1.0>,
  "interpretatie": "<korte samenvatting van afstemming>",
  "analyse": "<historische context en diepgaande partij visie, vermijd herhaling van het voorstel zelf>",
  "positieve_punten": [<lijst van sterke afstemming punten waarbij het beleid de partijvisie volgt>],
  "kritische_punten": [<lijst van gebieden waar het beleid schuurt of tekortschiet>],
  "vraag_suggesties": [<lijst van 2-3 scherpe vragen voor het debat>],
  "tegenvoorstel_suggesties": [<lijst van 1-2 concrete ideeën voor moties of amendementen>]
}}
"""
        
        return prompt
    
    def _categorize_agenda_item(self, text: str) -> Dict[str, Any]:
        """
        Categorize an agenda item into a policy area using LLM.
        """
        
        prompt = f"""Categoriseer het volgende Rotterdam-agendapunt in één van deze beleidsgebieden:
- Klimaat
- Wonen
- Mobiliteit
- Onderwijs
- Zorg
- Economie
- Veiligheid
- Inclusiviteit
- Overig

AGENDAPUNT:
{text[:500]}

Antwoord ALLEEN met de categorie naam, gevolgd door een confidence score (0-1).
Format: CATEGORIE|0.9
"""
        
        try:
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt
            )
            
            # Parse response
            result = response.text.strip()
            if '|' in result:
                area, conf = result.split('|')
                return {
                    'area': area.strip(),
                    'confidence': float(conf.strip())
                }
            else:
                return {'area': result.strip(), 'confidence': 0.7}
                
        except Exception as e:
            print(f"⚠ Categorization error: {e}")
            return {'area': 'Overige', 'confidence': 0.5}
    
    def _parse_llm_response(self, response_text: str) -> Dict[str, Any]:
        """
        Parse the JSON response from LLM alignment evaluation.
        """
        
        try:
            # Extract JSON from response (LLM might add extra text)
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                data = json.loads(json_str)
                
                # Validate and normalize score
                score = float(data.get('score', 0.5))
                score = max(0.0, min(1.0, score))
                
                return {
                    'score': round(score, 2),
                    'interpretatie': data.get('interpretatie', 'Analyse beschikbaar'),
                    'analyse': data.get('analyse', ''),
                    'positieve_punten': data.get('positieve_punten', []),
                    'kritische_punten': data.get('kritische_punten', []),
                    'vraag_suggesties': data.get('vraag_suggesties', []),
                    'tegenvoorstel_suggesties': data.get('tegenvoorstel_suggesties', []),
                    'bron': 'LLM'
                }
            else:
                raise ValueError("No JSON found in response")
                
        except Exception as e:
            print(f"⚠ Response parsing error: {e}")
            # Return structured fallback
            return {
                'score': 0.5,
                'interpretatie': 'Analyse onvolledig',
                'analyse': response_text[:200],
                'positieve_punten': [],
                'kritische_punten': [],
                'vraag_suggesties': [],
                'tegenvoorstel_suggesties': [],
                'bron': 'fallback'
            }
    
    def _fallback_heuristic_score(
        self,
        party_position: str,
        rotterdam_policy: str,
        policy_area: str
    ) -> Dict[str, Any]:
        """
        Fallback to heuristic scoring if LLM fails.
        
        This maintains service reliability while LLM is down.
        """
        
        score = 0.5
        
        # Green keywords that align with GroenLinks-PvdA values
        green_keywords = [
            'duurzaam', 'groen', 'klimaat', 'milieu', 'sociaal',
            'inclusief', 'gelijkheid', 'arbeiders', 'werknemers',
            'publiek', 'voorzieningen', 'ondersteuning', 'zorg',
            'onderwijs', 'participatie', 'democratie', 'lokaal'
        ]
        
        # Keywords suggesting market-based approach (potential conflicts)
        market_keywords = [
            'privatisering', 'commercieel', 'marktwerking',
            'bezuinigingen', 'effiëntie', 'ondernemerschap',
            'deregulering', 'concurrentie'
        ]
        
        combined_text = (party_position + ' ' + rotterdam_policy).lower()
        
        # Count keyword matches
        green_matches = sum(1 for kw in green_keywords if kw in combined_text)
        market_matches = sum(1 for kw in market_keywords if kw in combined_text)
        
        # Adjust score based on keyword matches
        if green_matches > market_matches:
            score = 0.5 + (0.3 * (green_matches / (green_matches + market_matches + 1)))
        elif market_matches > green_matches:
            score = 0.5 - (0.3 * (market_matches / (green_matches + market_matches + 1)))
        
        score = max(0.0, min(1.0, score))
        
        return {
            'score': round(score, 2),
            'interpretatie': self._interpret_score(score),
            'analyse': f'Heuristische analyse gebaseerd op {green_matches} positieve en {market_matches} kritische indicatoren',
            'positieve_punten': [],
            'kritische_punten': [],
            'vraag_suggesties': [],
            'tegenvoorstel_suggesties': [],
            'bron': 'heuristic_fallback'
        }
    
    def _generate_recommendations(
        self,
        alignment: Dict[str, Any],
        party_position: str,
        policy_area: str,
        core_values: List[str]
    ) -> List[str]:
        """
        Generate recommendations for the party based on alignment analysis.
        """
        
        score = alignment.get('score', 0.5)
        
        # Prefer rich LLM recommendations if available
        llm_recommendations = alignment.get('aanbevelingen', [])
        if llm_recommendations:
            return llm_recommendations
            
        # Fallback to heuristics if LLM didn't provide any
        recommendations = []
        if score < 0.3:
            recommendations.append(
                f"Sterke bezwaar tegen dit beleid indienen; "
                f"wijst af van kernwaarden op {policy_area}"
            )
            recommendations.append(
                "Alternatieve beleidsvoorstel indienen dat beter aansluit "
                "bij kernwaarden"
            )
        elif score < 0.5:
            recommendations.append(
                f"Kritische vragen indienen; veel verbeterpunten nodig"
            )
            recommendations.append(
                "Amendementen voorstellen voor beter afstemming"
            )
        elif score < 0.7:
            recommendations.append(
                f"Steun onder voorwaarden; waarborgen nodig voor "
                f"kernwaarden op {policy_area}"
            )
            recommendations.append(
                "Controlemaatregelen voorstellen in implementatie"
            )
        else:
            recommendations.append(
                f"Volledige steun; stemt goed aan met {self.party_name}'s visie"
            )
            recommendations.append(
                "Actief promoten als voorbeeld van goed beleid"
            )
        
        return recommendations
    
    def _interpret_score(self, score: float) -> str:
        """Generate interpretation text for a numerical score"""
        
        if score > 0.8:
            return "Zeer sterke afstemming"
        elif score > 0.6:
            return "Goede afstemming"
        elif score > 0.4:
            return "Matige afstemming"
        elif score > 0.2:
            return "Lage afstemming"
        else:
            return "Zeer lage afstemming / Tegengesteld"
    
    def batch_score_policies(
        self,
        policies: List[Dict[str, str]],
        party_profile: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Score multiple policies efficiently.
        
        Args:
            policies: List of policy dicts with 'text' and 'area' keys
            party_profile: Party profile data
        
        Returns:
            List of scored policies
        """
        
        results = []
        for i, policy in enumerate(policies):
            print(f"  Scoring policy {i+1}/{len(policies)}...", end='\r')
            
            result = self.score_agenda_item(
                policy.get('text', ''),
                party_profile
            )
            result['policy'] = policy
            results.append(result)
        
        print(f"\n  ✓ Scored {len(results)} policies")
        
        return results
