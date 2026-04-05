import os
import re
import json
import asyncio
from typing import Dict, Any, Optional, List
from collections import Counter

try:
    import google.genai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

import logging
logger = logging.getLogger(__name__)

class AIService:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        
        # FAIL-SAFE: Manually read .env if key is missing from environment
        if not self.api_key:
            try:
                # Use project absolute path to ensure recovery
                abs_env = "/Users/dennistak/Documents/Final Frontier/NeoDemos/.env"
                if os.path.exists(abs_env):
                    with open(abs_env, "r") as f:
                        for line in f:
                            if "GEMINI_API_KEY" in line and "=" in line:
                                self.api_key = line.split("=")[1].strip().strip("'").strip('"')
                                os.environ["GEMINI_API_KEY"] = self.api_key
                                break
            except:
                pass

        # Initialize Gemini API if available and key is set
        if GEMINI_AVAILABLE and self.api_key:
            self.client = genai.Client(api_key=self.api_key)
            self.model_name = 'gemini-2.5-flash-lite'
            self.embedding_model = 'gemini-embedding-001'  # Available embedding model
            self.use_llm = True
        else:
            self.use_llm = False
            if not GEMINI_AVAILABLE:
                print("Warning: google-genai not installed. Using fallback heuristic analysis.")

    async def analyze_agenda_item(self, item_name: str, documents: List[Dict[str, str]], party_vision: Optional[str] = None) -> Dict[str, Any]:
        """
        Deep analysis of a substantive agenda item with all its documents.
        Extracts meaningful insights from lengthy documents for busy city councillors.
        Uses Gemini Flash 3 for intelligent analysis.
        """
        
        if not documents:
            return {
                "summary": f"Geen documenten beschikbaar voor agendapunt: {item_name}",
                "key_points": [],
                "conflicts": [],
                "decision_points": [],
                "controversial_topics": [],
                "questions": [],
                "party_alignment": None
            }
        
        # Try LLM analysis first, fall back to heuristics if needed
        if self.use_llm:
            try:
                analysis = await self._analyze_with_gemini(item_name, documents, party_vision)
                return analysis
            except Exception as e:
                print(f"Gemini API error, falling back to heuristics: {str(e)}")
        
        # Fallback to heuristic analysis
        analysis = {
            "summary": self._create_executive_summary(item_name, documents),
            "key_points": self._extract_key_proposals(documents),
            "conflicts": self._detect_conflicts_deep(documents),
            "decision_points": self._extract_decision_points_deep(documents, item_name),
            "controversial_topics": self._detect_controversial_topics_deep(documents),
            "questions": self._generate_critical_questions(item_name, documents),
            "party_alignment": None
        }
        
        # Add party alignment if vision provided
        if party_vision:
            analysis["party_alignment"] = self._assess_party_alignment_deep(documents, party_vision)
        
        return analysis

    async def _analyze_with_gemini(self, item_name: str, documents: List[Dict[str, str]], party_vision: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
        """
        Use Gemini Flash 3 for intelligent deep analysis of agenda items.
        Provides nuanced understanding of proposals, conflicts, and implications.
        """
        
        # Prepare document content for analysis
        documents_text = self._prepare_documents_for_analysis(documents)
        
        # Extract sampling of document text for RAG query
        docs_sample_text = ' '.join([d.get('content', '')[:500] for d in documents])
        
        # Retrieve historical context from RAG
        try:
            from services.rag_service import RAGService
            rag = RAGService()
            relevant_chunks = rag.retrieve_relevant_context(
                query_text=f"{item_name} {docs_sample_text}",
                top_k=10,
                fast_mode=True,
                date_from=date_from,
                date_to=date_to,
            )
            historical_context = rag.format_retrieved_context(relevant_chunks) if relevant_chunks else ""
        except Exception as e:
            print(f"RAG Retrieval warning: {e}")
            historical_context = ""

        # Create comprehensive analysis prompt
        analysis_prompt = self._create_analysis_prompt(item_name, documents_text, party_vision, historical_context)
        
        try:
            # Call Gemini API
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=analysis_prompt
            )
            
            # Parse the response as JSON
            response_text = response.text
            
            # Try to extract JSON from the response
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                analysis_json = json.loads(json_match.group())
            else:
                # If no JSON found, create structured response from text
                analysis_json = self._parse_gemini_response(response_text)
            
            # Verify quotes in summary and context
            if "summary" in analysis_json:
                analysis_json["summary"] = self.verify_quotes(analysis_json["summary"], documents)
            if "historical_context" in analysis_json and analysis_json["historical_context"]:
                analysis_json["historical_context"] = self.verify_quotes(analysis_json["historical_context"], documents + (relevant_chunks if 'relevant_chunks' in locals() else []))
            
            return analysis_json
            
        except json.JSONDecodeError as e:
            print(f"Failed to parse Gemini response as JSON: {str(e)}")
            # Fall back to heuristic analysis
            raise Exception("Failed to parse LLM response")
    
    def _prepare_documents_for_analysis(self, documents: List[Dict[str, str]]) -> str:
        """Prepare documents for Gemini analysis with clear structure."""
        docs_text = []
        for i, doc in enumerate(documents, 1):
            name = doc.get('name', f'Document {i}')
            content = doc.get('content', '')
            docs_text.append(f"=== Document {i}: {name} ===\n{content}\n")
        
        return "\n".join(docs_text)
    
    def _create_analysis_prompt(self, item_name: str, documents_text: str, party_vision: Optional[str] = None, historical_context: str = "") -> str:
        """Create a detailed prompt for Gemini analysis."""
        
        prompt = f"""Je bent een expert analyse-assistent voor gemeenteraadsleden in Rotterdam. Analyseer dit agendapunt diepgaand.

AGENDAPUNT: {item_name}

DOCUMENTEN (volledig, niet ingekort):
{documents_text}
"""
        if historical_context:
            prompt += f"\nRELEVANTE HISTORISCHE CONTEXT UIT GEMEENTERAADSNOTULEN:\n{historical_context}\n"

        prompt += """
Analyseer het agendapunt en geef een JSON-response in het volgende format:
{
    "summary": "Bondige samenvatting (2-3 zinnen) van wat het agendapunt betreft en wat de hoofdpunten zijn",
    "key_points": ["Voorstel 1", "Voorstel 2", ...],
    "conflicts": ["Conflict 1: beschrijving", "Conflict 2: beschrijving"],
    "decision_points": ["Besluit 1: beschrijving", "Besluit 2: beschrijving"],
    "controversial_topics": ["Topic 1", "Topic 2"],
    "questions": ["Kritische vraag 1?", "Kritische vraag 2?"],
    "historical_context": "Korte samenvatting van wat in het verleden over dit onderwerp is besproken of besloten (gebaseerd op de RELEVANTE HISTORISCHE CONTEXT)",
    "party_alignment": null
}

RICHTLIJNEN:
1. **Key points**: Concrete voorstellen, financiële bedragen, doelstellingen, maatregelen
2. **Conflicts**: Substantiële meningsverschillen tussen documenten, budgetverschillen, tegengestelde aanbevelingen
3. **Decision points**: Wat moet de raad precies besluiten/goedkeuren?
4. **Controversial topics**: Onderwerpen die waarschijnlijk gevoelig/controversieel zijn (klimaat, woningbouw, verkeer, veiligheid, financiën, etc.)
5. **Questions**: Kritische vragen die raadsleden MOETEN stellen - niet oppervlakkig, maar gericht op de kern
6. **Historical context**: Gebruik de meegeleverde historische context om aan te geven wat eerder in debatten is gezegd. Focus op partijstandpunten (vooral de partij die wordt geanalyseerd). Is er consistentie of een draai? Als er geen historische context is, laat dit veld dan null.
7. Werk alleen met informatie uit de documenten en de historische context
8. Focus op substantiële inhoud, niet op procedure
9. Geef geen generieke vragen, maar specifieke vragen gebaseerd op de documenten"""
        
        if party_vision:
            prompt += f"""\n\nPARTIJVISIE:
{party_vision}

Voeg ook toe aan de JSON response:
"party_alignment": {{
    "score": 0-100,
    "alignment_level": "Hoog/Gemiddeld/Laag",
    "reasoning": "Korte uitleg"
}}"""
        
        return prompt
    
    def _parse_gemini_response(self, response_text: str) -> Dict[str, Any]:
        """
        Parse Gemini response into structured format if JSON parsing fails.
        This is a fallback for when the response isn't pure JSON.
        """
        # Try to extract structured information from the response
        analysis = {
            "summary": response_text[:300],
            "key_points": [],
            "conflicts": [],
            "decision_points": [],
            "controversial_topics": [],
            "questions": [],
            "party_alignment": None
        }
        
        # Try to extract bullet points/sections
        lines = response_text.split('\n')
        current_section = None
            
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            if 'key point' in line.lower() or 'voorstel' in line.lower():
                current_section = 'key_points'
            elif 'conflict' in line.lower():
                current_section = 'conflicts'
            elif 'decision' in line.lower() or 'besluit' in line.lower():
                current_section = 'decision_points'
            elif 'controversial' in line.lower() or 'gevoelig' in line.lower():
                current_section = 'controversial_topics'
            elif 'question' in line.lower() or 'vraag' in line.lower():
                current_section = 'questions'
            elif line.startswith('-') or line.startswith('•'):
                clean_line = line.lstrip('-•').strip()
                if current_section and clean_line:
                    if isinstance(analysis[current_section], list):
                        analysis[current_section].append(clean_line)
        
        return analysis

    def _create_executive_summary(self, item_name: str, documents: List[Dict[str, str]]) -> str:
        """Create a concise executive summary of the agenda item based on all documents."""
        
        all_content = "\n\n".join([d.get('content', '') for d in documents if d.get('content')])
        
        # Extract key sentences - look for sentences with action words
        sentences = re.split(r'[.!?]+', all_content)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 30]
        
        # Score sentences by importance
        importance_words = [
            'voorstel', 'plan', 'budget', 'investering', 'miljoen', 'project',
            'besluit', 'goedkeuring', 'advies', 'aanbeveling', 'wijziging',
            'vaststelling', 'vaststellen', 'instemming', 'accordeert'
        ]
        
        scored_sentences = []
        for sentence in sentences[:50]:  # Look at first 50 sentences
            score = sum(1 for word in importance_words if word in sentence.lower())
            if score > 0:
                scored_sentences.append((score, sentence))
        
        # Sort by importance and take top 2-3 sentences
        scored_sentences.sort(reverse=True, key=lambda x: x[0])
        summary_sentences = [s[1] for s in scored_sentences[:3]]
        
        if summary_sentences:
            return " ".join(summary_sentences)[:400]
        
        # Fallback: use first substantial paragraphs
        paragraphs = [p.strip() for p in all_content.split('\n\n') if len(p.strip()) > 50]
        return " ".join(paragraphs[:2])[:400]

    def _extract_key_proposals(self, documents: List[Dict[str, str]]) -> List[str]:
        """Extract key proposals, plans, and initiatives from documents."""
        
        all_content = "\n\n".join([d.get('content', '') for d in documents if d.get('content')])
        all_content_lower = all_content.lower()
        
        key_points = []
        
        # Look for concrete proposals and plans
        proposal_patterns = [
            (r'voorstel[:\s]+([^.]{20,120})', 'Voorstel'),
            (r'plan[:\s]+([^.]{20,120})', 'Plan'),
            (r'investering[:\s]*(?:van |in )?([^.]{20,80})', 'Investering'),
            (r'budget[:\s]*([^.]{20,80})', 'Budget'),
            (r'(?:zal|worden|geschat)\s+([^.]{30,100})', 'Activiteit'),
            (r'doelstelling[:\s]+([^.]{20,100})', 'Doelstelling'),
            (r'maatregel[:\s]+([^.]{20,100})', 'Maatregel'),
        ]
        
        found_proposals = set()
        for pattern, label in proposal_patterns:
            matches = re.findall(pattern, all_content, re.IGNORECASE)
            for match in matches[:2]:  # Take top 2 matches per pattern
                # Clean up the match
                clean_match = re.sub(r'\s+', ' ', match.strip())[:80]
                if len(clean_match) > 15 and clean_match not in found_proposals:
                    key_points.append(f"{label}: {clean_match}")
                    found_proposals.add(clean_match)
        
        # Look for financial figures
        money_pattern = r'(?:€|euro|miljoen|miljard|miljoen euro|miljard euro)\s*[\d.,\s]+'
        money_matches = re.findall(money_pattern, all_content, re.IGNORECASE)
        if money_matches:
            for amount in set(money_matches[:3]):
                key_points.append(f"Financiering: {amount.strip()}")
        
        # Look for percentages and statistics
        percentage_pattern = r'\d+\s*%'
        pct_matches = re.findall(percentage_pattern, all_content)
        if pct_matches:
            key_points.append(f"Statistieken: {', '.join(set(pct_matches[:3]))}")
        
        # Look for timelines
        timeline_pattern = r'(?:2024|2025|2026|jaar|maanden?)\s+(?:tot|t/m|-)\s*(?:2024|2025|2026|2027|[0-9]+\s*(?:jaar|maanden?))'
        timeline_matches = re.findall(timeline_pattern, all_content, re.IGNORECASE)
        if timeline_matches:
            key_points.append(f"Tijdschema: {', '.join(set(timeline_matches[:2]))}")
        
        return list(set(key_points))[:8]

    def _detect_conflicts_deep(self, documents: List[Dict[str, str]]) -> List[str]:
        """Detect substantial conflicts and differing perspectives between documents."""
        
        if len(documents) < 2:
            return []
        
        conflicts = []
        doc_contents = {d.get('name', f'Doc {i}'): d.get('content', '').lower() for i, d in enumerate(documents)}
        
        # Look for explicit disagreement language
        disagreement_words = [
            'tegen', 'bezwaar', 'kritiek', 'probleem', 'risico', 'gevaar', 'concern',
            'niet mee eens', 'tegengesteld', 'afwijzen', 'verwerpen', 'onjuist'
        ]
        
        # Look for opposing viewpoints across documents
        support_words = [
            'voor', 'steun', 'voorkeur', 'voorstander', 'akkoord', 'instemming',
            'positief', 'voordeel', 'mogelijkheid', 'kans', 'ondersteunt'
        ]
        
        # Check if some docs support and others criticize
        has_support = False
        has_criticism = False
        
        for doc_name, content in doc_contents.items():
            if any(word in content for word in support_words):
                has_support = True
            if any(word in content for word in disagreement_words):
                has_criticism = True
        
        if has_support and has_criticism:
            conflicts.append("Duidelijke verschil van mening: sommige documenten ondersteunen het voorstel, anderen hebben kritiek")
        
        # Look for specific disagreements in numbers/budgets
        budget_pattern = r'(?:€|euro|miljoen)\s*[\d.,\s]+'
        budgets_mentioned = []
        for doc_name, content in doc_contents.items():
            budgets = re.findall(budget_pattern, content, re.IGNORECASE)
            if budgets:
                budgets_mentioned.extend(budgets)
        
        if len(set(budgets_mentioned)) > 1:
            conflicts.append(f"Verschillende financiële aantallen gegeven: {', '.join(set(budgets_mentioned)[:3])}")
        
        # Look for documents with 'reactie', 'bezwaar', 'advies' - typically alternative views
        doc_types = {}
        for doc_name in doc_contents.keys():
            name_lower = doc_name.lower()
            if 'reactie' in name_lower:
                doc_types['reactie'] = True
            if 'bezwaar' in name_lower:
                doc_types['bezwaar'] = True
            if 'advies' in name_lower:
                doc_types['advies'] = True
            if 'inspreekbijdrage' in name_lower:
                doc_types['inspreekbijdrage'] = True
        
        if len(doc_types) > 1:
            conflict_desc = f"Meerdere perspectieven aanwezig: {', '.join(doc_types.keys())}"
            conflicts.append(conflict_desc)
        
        return list(set(conflicts))

    def _extract_decision_points_deep(self, documents: List[Dict[str, str]], item_name: str) -> List[str]:
        """Extract specific decision points and what exactly needs to be decided."""
        
        all_content = "\n\n".join([d.get('content', '') for d in documents if d.get('content')])
        all_content_lower = all_content.lower()
        
        decision_points = []
        
        # Look for explicit decision language
        decision_patterns = [
            (r'(\w+\s+){0,3}besluiten?[:\s]+([^.]{20,120})', 'Moet besluiten over'),
            (r'(\w+\s+){0,3}stemming[:\s]+([^.]{20,80})', 'Stemming nodig over'),
            (r'(\w+\s+){0,3}goedkeur(?:en|ing)[:\s]+([^.]{20,80})', 'Goedkeuring nodig voor'),
            (r'(\w+\s+){0,3}advies[:\s]+([^.]{20,80})', 'Advies gevraagd over'),
            (r'raad zal[:\s]+([^.]{20,100})', 'Raad zal'),
            (r'commissie zal[:\s]+([^.]{20,100})', 'Commissie zal'),
            (r'voorstel om[:\s]+([^.]{20,100})', 'Voorstel om'),
        ]
        
        for pattern, label in decision_patterns:
            matches = re.findall(pattern, all_content, re.IGNORECASE)
            for match in matches[:2]:
                # Handle both single and multi-group matches
                if isinstance(match, tuple):
                    decision_text = match[-1]  # Take last group
                else:
                    decision_text = match
                
                clean_text = re.sub(r'\s+', ' ', decision_text.strip())[:100]
                if len(clean_text) > 15:
                    decision_points.append(f"{label}: {clean_text}")
        
        # Look for "voor instemming" or similar
        if 'voor instemming' in all_content_lower:
            decision_points.append("Instemming van raad/commissie gevraagd")
        
        if 'adviesaanvraag' in all_content_lower:
            decision_points.append("Advies van commissie wordt ingewacht")
        
        # If no explicit decision points found, infer from item name
        if not decision_points:
            if 'voorstel' in item_name.lower() or 'plan' in item_name.lower():
                decision_points.append("Bestemming: Goedkeuring van het voorstel/plan")
            else:
                decision_points.append("Raadsbesluit op basis van deze agenda item")
        
        return list(set(decision_points))[:5]

    def _detect_controversial_topics_deep(self, documents: List[Dict[str, str]]) -> List[str]:
        """Identify potentially controversial topics based on document content."""
        
        all_content = "\n\n".join([d.get('content', '') for d in documents if d.get('content')])
        all_content_lower = all_content.lower()
        
        controversial = []
        
        # Define controversial topics with keywords
        controversial_topics = {
            'Klimaat & Duurzaamheid': ['klimaat', 'co2', 'emissie', 'fossiel', 'groen', 'duurzaam', 'energietransitie'],
            'Woningbouw & Huisvesting': ['woningen', 'bouwen', 'bouw', 'huisvesting', 'erfpacht', 'huurprijzen', 'huurtoeslag'],
            'Verkeer & Mobiliteit': ['verkeer', 'auto', 'fiets', 'openbaar vervoer', 'parkeer', 'straat', 'weg'],
            'Sociale Cohesie': ['migratie', 'integratie', 'diversiteit', 'segregatie', 'getto', 'armoede', 'onderwijs'],
            'Veiligheid': ['criminaliteit', 'politie', 'veiligheid', 'geweld', 'overlast', 'drugsbestrijding'],
            'Economie & Werkgelegenheid': ['werkgelegenheid', 'economie', 'bedrijven', 'ondernemers', 'banen', 'werkloosheid'],
            'Financiën & Bezuinigingen': ['bezuiniging', 'budget', 'miljoen', 'subsidie', 'krijgt', 'inkomsten'],
            'Gronden & Eigendom': ['gronden', 'pand', 'eigendom', 'vastgoed', 'perceel', 'exploitatie'],
        }
        
        for topic, keywords in controversial_topics.items():
            keyword_count = sum(1 for kw in keywords if kw in all_content_lower)
            if keyword_count >= 2:  # At least 2 keywords per topic
                controversial.append(topic)
        
        # Look for explicit controversy indicators
        if any(word in all_content_lower for word in ['bezwaar', 'tegengesteld', 'kritiek', 'concern']):
            if controversial:
                controversial[0] = f"⚠️ {controversial[0]} - Expliciete kritiek/bezwaren"
        
        # Check for budget/investment size that might be controversial
        large_budget_pattern = r'(?:€|euro)\s*[\d.,]+\s*(?:miljoen|miljard)'
        large_budgets = re.findall(large_budget_pattern, all_content, re.IGNORECASE)
        if large_budgets and 'Financiën & Bezuinigingen' not in controversial:
            controversial.append('Financiën & Bezuinigingen')
        
        return list(set(controversial))[:4]

    def _generate_critical_questions(self, item_name: str, documents: List[Dict[str, str]]) -> List[str]:
        """Generate critical questions that council members should ask."""
        
        all_content = "\n\n".join([d.get('content', '') for d in documents if d.get('content')])
        all_content_lower = all_content.lower()
        
        questions = []
        
        # Always ask about implementation and timeline
        if any(word in all_content_lower for word in ['plan', 'voorstel', 'project']):
            questions.append("Wat is het concrete implementatieplan en de timeline?")
            questions.append("Wie is verantwoordelijk voor de uitvoering?")
        
        # Budget questions if money mentioned
        budget_pattern = r'(?:€|euro|miljoen)\s*[\d.,\s]+'
        if re.search(budget_pattern, all_content):
            questions.append("Hoe is dit financieel duurzaam over meerdere jaren?")
            questions.append("Wat zijn de begrote versus werkelijke kosten van vergelijkbare projecten?")
            questions.append("Zijn er risicoschema's voor kostenoverloop?")
        
        # Impact and effectiveness questions
        questions.append("Wat zijn meetbare doelstellingen en hoe wordt voortgang gemonitord?")
        questions.append("Wat is de impact op verschillende groepen burgers?")
        
        # Stakeholder questions
        if any(word in all_content_lower for word in ['buurt', 'bewoner', 'stakeholder', 'betrokken']):
            questions.append("Zijn relevante stakeholders en buurtbewoners betrokken in het besluitvormingsproces?")
        
        # Environmental/social impact
        if any(word in all_content_lower for word in ['klimaat', 'milieu', 'duurzaam', 'groen']):
            questions.append("Wat zijn de milieueffecten en duurzaamheidsimplicaties?")
        
        if any(word in all_content_lower for word in ['armoede', 'sociaal', 'kwetsbaar', 'migratie']):
            questions.append("Hoe wordt sociale cohesie en inclusie bevorderd?")
        
        # Risk questions for conflicts detected
        if len(documents) > 1:
            questions.append("Wat zijn de belangrijkste bezwaren/kritiekpunten en hoe worden deze ondervangen?")
        
        # Remove duplicates and limit to 6 questions
        questions = list(set(questions))[:6]
        
        return questions

    def _assess_party_alignment_deep(self, documents: List[Dict[str, str]], party_vision: str) -> str:
        """
        Deep assessment of how the agenda item aligns with party vision.
        Foundation for political decision-making.
        """
        
        all_content = "\n\n".join([d.get('content', '') for d in documents if d.get('content')])
        all_content_lower = all_content.lower()
        
        vision_lower = party_vision.lower()
        vision_keywords = [w.strip() for w in vision_lower.split() if len(w) > 4]
        
        # Count keyword matches
        matches = sum(1 for keyword in vision_keywords if keyword in all_content_lower)
        alignment_score = (matches / max(len(vision_keywords), 1)) * 100
        
        # Check for explicit conflicts with party vision
        conflicting_terms = []
        if 'groen' in vision_lower and 'vervuiling' in all_content_lower:
            conflicting_terms.append('milieubelasting')
        if 'duurzaam' in vision_lower and 'fossiel' in all_content_lower:
            conflicting_terms.append('fossiele brandstoffen')
        
        if alignment_score > 70:
            alignment = "Hoog"
        elif alignment_score > 40:
            alignment = "Gemiddeld"
        else:
            alignment = "Laag"
        
        result = f"{alignment} - ({int(alignment_score)}% keyword overlap)"
        
        if conflicting_terms:
            result += f" [⚠️ Potentiële conflicten: {', '.join(conflicting_terms)}]"
        
        return result

    async def summarize_document(self, content: str, party_vision: Optional[str] = None) -> Dict[str, Any]:
        """
        Legacy method for single document summarization.
        """
        documents = [{"name": "Document", "content": content}]
        return await self.analyze_agenda_item("Item", documents, party_vision)
    
    async def generate_speech_draft(self, item_name: str, documents: List[Dict[str, str]], party_vision: str) -> str:
        """
        Generate a compelling speech draft (bijdrage) for a councillor.
        Focuses on rhetorical strength, logical flow, and party-alignment.
        """
        docs_text = self._prepare_documents_for_analysis(documents)
        
        prompt = f"""Je bent een strategisch adviseur voor een Rotterdamse fractie.
Schrijf een krachtige 'bijdrage' (speech) voor een raadslid over het volgende agendapunt:

AGENDAPUNT: {item_name}

BRONDOCUMENTEN:
{docs_text}

PARTIJVISIE:
{party_vision}

RICHTLIJNEN VOOR DE BIJDRAGE:
1. Begin met een sterke opening die de gedeelde waarden van de partij raakt.
2. Benoem de kern van het voorstel bondig.
3. Onderbouw de positie (voor/tegen/nuance) met minimaal 3 sterke argumenten uit de documenten.
4. Stel 1-2 scherpe, retorische vragen aan het college/de wethouder.
5. Eindig met een duidelijke conclusie of oproep tot actie.
6. Houd de toon professioneel, Rotterdams (direct), en politiek scherp.
7. Gebruik 'wij' als fractie.

GEWENSTE OUTPUT:
Exclusief de tekst van de bijdrage, geordend in logische paragrafen. Geen titels of metadata.
"""
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt
            )
            return response.text
        except Exception as e:
            logger.error(f"Speech generation failed: {e}")
            return "Fout bij het genereren van de bijdrage."

    def generate_embedding(self, text: str) -> Optional[List[float]]:
        """
        Generate a vector embedding for text.
        Prefers LocalAIService (MLX 4096-dim) to match indexed data.
        Falls back to Gemini (3072-dim) ONLY if local is unavailable.
        """
        # --- PREFER LOCAL MODEL (4096-dim) ---
        try:
            from services.local_ai_service import LocalAIService
            local_ai = LocalAIService()
            if local_ai.is_available():
                emb = local_ai.generate_embedding(text)
                if emb is not None:
                    print(f"DEBUG: Vector search using LOCAL embedding (dim={len(emb)})")
                    return emb
        except Exception as e:
            logger.warning(f"Local embedding failed, falling back to Gemini: {e}")

        if not self.use_llm:
            return None
        
        print("DEBUG: Vector search falling back to GEMINI embedding (dim=3072)")
        
        try:
            response = self.client.models.embed_content(
                model=self.embedding_model,
                contents=text
            )
            # gemini-embedding-001 returns embeddings as a list, get the first one
            if response.embeddings:
                return response.embeddings[0].values
            return None
        except Exception as e:
            print(f"Embedding error: {e}")
            return None

    async def extract_temporal_filters(self, query: str) -> Dict[str, Optional[str]]:
        """
        Detects temporal language in a query and returns structured date filters.
        Returns {"query": cleaned_query, "date_from": ..., "date_to": ...}.
        Falls back to no-op (original query, no dates) on any failure.
        """
        from datetime import date
        today = date.today().isoformat()

        # Fast path: skip LLM call if no temporal-looking words are present
        temporal_signals = [
            "vorig", "afgelopen", "sinds", "recent", "eerder", "laatste",
            "dit jaar", "vorige maand", "begin 20", "eind 20", "na 20",
            "voor 20", "in 20", "from 20", "since 20", "last year",
            "this year", "recent", "ago",
        ]
        has_temporal = any(s in query.lower() for s in temporal_signals)
        if not has_temporal:
            return {"query": query, "date_from": None, "date_to": None}

        if not self.use_llm:
            return {"query": query, "date_from": None, "date_to": None}

        prompt = f"""Vandaag is {today}. Analyseer deze zoekvraag en extraheer temporele filters.

Vraag: "{query}"

Als de vraag een tijdsperiode impliceert, geef dan:
- query: de vraag ZONDER temporele termen (behoud de inhoudelijke zoektermen)
- date_from: startdatum in ISO formaat (YYYY-MM-DD) of null
- date_to: einddatum in ISO formaat (YYYY-MM-DD) of null

Voorbeelden:
- "parkeerbeleid vorig jaar" → {{"query": "parkeerbeleid", "date_from": "2025-01-01", "date_to": "2025-12-31"}}
- "wat is er recent besloten over woningbouw" → {{"query": "besloten woningbouw", "date_from": "2026-01-01", "date_to": null}}
- "klimaatbeleid" → {{"query": "klimaatbeleid", "date_from": null, "date_to": null}}

Antwoord ALLEEN met een JSON object, geen uitleg."""

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
            )
            json_match = re.search(r'\{[\s\S]*?\}', response.text)
            if json_match:
                result = json.loads(json_match.group())
                return {
                    "query": result.get("query") or query,
                    "date_from": result.get("date_from"),
                    "date_to": result.get("date_to"),
                }
        except Exception as e:
            logger.debug(f"Temporal extraction failed (non-critical): {e}")

        return {"query": query, "date_from": None, "date_to": None}

    async def generate_parallel_queries(self, original_query: str) -> Dict[str, str]:
        """
        Uses a small model to rewrite the original query into 3 specialized dimensions.
        """
        prompt = f"""Rewrite the following municipal query into 3 specific search queries for different dimensions.
Original query: {original_query}

Return ONLY a JSON object with these keys:
- financial: Focus on budgets, millions, cost overruns, and funding.
- debate: Focus on opinions of specific parties, council member quotes, and controversies.
- fact: Focus on technical details, location specifics, regulations, and policy definitions.

Output format: {{"financial": "...", "debate": "...", "fact": "..."}}
"""
        try:
            response = self.client.models.generate_content(
                model=self.model_name, # Fast and cheap for rewriting
                contents=prompt
            )
            # Find JSON in response
            json_match = re.search(r'\{[\s\S]*\}', response.text)
            if json_match:
                return json.loads(json_match.group())
            return {
                "financial": f"{original_query} begroting",
                "debate": f"{original_query} debat",
                "fact": f"{original_query} beleid"
            }
        except Exception as e:
            print(f"Multi-query rewriting failed: {e}")
            return {
                "financial": f"{original_query} begroting",
                "debate": f"{original_query} debat",
                "fact": f"{original_query} beleid"
            }

    async def perform_deep_search(self, query: str, storage: Any, date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
        """
        Standard Deep Research search.
        """
        if not self.use_llm:
            return {"answer": "AI Search is momenteel niet beschikbaar.", "sources": []}

        enhanced_query = f"{query} standpunten politieke partijen college debat financiën"
        query_embedding = self.generate_embedding(enhanced_query)
        
        from services.rag_service import RAGService
        rag = RAGService()
        
        parallel_queries = await self.generate_parallel_queries(query)
        chunks = await rag.retrieve_parallel_context(
            query_text=query,
            query_embedding=query_embedding,
            distribution={"financial": 50, "debate": 80, "fact": 50, "vision": 20},
            overrides=parallel_queries,
            date_from=date_from,
            date_to=date_to,
            fast_mode=True,
        )
        
        if not chunks:
            return {"answer": "Geen relevante informatie gevonden in de notulen.", "sources": []}
            
        return await self._run_analytical_pipeline(query, chunks, storage, rag)

    async def _run_analytical_pipeline(self, query: str, context_source: Any, storage: Any, rag: Any, party: str = "GroenLinks-PvdA") -> Dict[str, Any]:
        """
        Unified 3-stage synthesis pipeline with 10-section PvdA Pivot.
        'context_source' can be a List[RetrievedChunk] (from search) or List[Dict] (from meeting item).
        """
        
        # --- STAGE 0: Context Preparation ---
        if not context_source:
            return {"answer": "Geen informatie beschikbaar om te analyseren.", "sources": []}
            
        is_direct_docs = isinstance(context_source, list) and len(context_source) > 0 and isinstance(context_source[0], dict)
        
        if is_direct_docs:
            # For meeting items, we already have the documents with content
            context_text = ""
            for i, doc in enumerate(context_source, 1):
                context_text += f"=== Document {i}: {doc.get('name', 'Onbekend')} ===\n{doc.get('content', '')}\n\n"
            verification_content = context_source
            ordered_sources = []
            for i, doc in enumerate(context_source, 1):
                # Strip leading IDs like [26bb000120] from title to prevent AI confusion
                clean_name = re.sub(r'^\[[a-zA-Z0-9]+\]\s*', '', doc.get('name', 'Onbekend'))
                ordered_sources.append({
                    "id": i, 
                    "name": clean_name,
                    "url": doc.get('url', '#')
                })
            chunks = []
        else:
            chunks = context_source
            
            # --- STAGE 0.5: Agentic Golden Source Discovery (Only for search) ---
            raw_text_sample = ""
            for c in chunks[:40]:
                raw_text_sample += str(getattr(c, 'title', '')) + "\n" + str(getattr(c, 'content', ''))[:500] + "\n\n"
                
            golden_prompt = f"""You are a legal municipal clerk.
Review the text below and identify if there are any EXPLICIT mentions of formal 'Golden Sources' that we need to find.
Specifically look for explicit references to:
- "Raadsvoorstel [Name/Subject]"
- "Motie [Name/Subject]"
- "Amendement [Name/Subject]"
- "Schriftelijke Vragen [Subject]"

If you find them, list their exact names/titles, one per line (e.g., 'Amendement Feyenoord City').
If none are explicitly referenced, output exactly 'NONE'.

QUERY: {query}
CONTEXT SAMPLE:
{raw_text_sample[:12000]}

OUTPUT (Only names, separated by newlines, or NONE):"""
            try:
                golden_response = self.client.models.generate_content(model=self.model_name, contents=golden_prompt)
                golden_sources_text = golden_response.text.strip()
                if golden_sources_text and "NONE" not in golden_sources_text.upper():
                    print(f"Agent identified Golden Sources to parallel fetch:\n{golden_sources_text}")
                    targeted_golden_chunks = await rag.retrieve_parallel_context(
                        query_text=golden_sources_text.replace('\n', ' '),
                        query_embedding=self.generate_embedding(golden_sources_text),
                        distribution={"fact": 20, "debate": 10, "financial": 0, "vision": 0},
                        fast_mode=True,
                    )
                    seen_ids = {c.chunk_id for c in chunks}
                    for gc in targeted_golden_chunks:
                        if gc.chunk_id not in seen_ids:
                            chunks.append(gc)
                            seen_ids.add(gc.chunk_id)
            except Exception as e:
                print(f"Golden Source Discovery failed: {e}")

            # 3. Fetch metadata for sources (Only for search chunks)
            doc_ids = list(set([str(c.document_id) for c in chunks]))
            docs_metadata = storage.get_documents_metadata(doc_ids)
            metadata_dict = {str(d['id']): d for d in docs_metadata}
            
            # Inject start_date and url into chunks for sorting and formatting
            for c in chunks:
                doc_meta = metadata_dict.get(str(c.document_id))
                if doc_meta:
                    c.start_date = doc_meta.get('start_date')
                    c.url = doc_meta.get('url')
            
            # Final hierarchical expansion for search
            context_text, ordered_sources, verification_content = rag.expand_to_hierarchical_context(chunks, storage)
            
            # Chronological sorting for search-based chunks
            chunks.sort(key=lambda x: getattr(x, 'start_date', '1970-01-01') or '1970-01-01')
            
        # STAGE 1: Information Extraction
        extraction_prompt = f"""You are an objective data extractor analyzing municipal documents.
Analyze the provided CONTEXT CHUNKS to extract:
1. A Chronological Timeline of events/budgets.
2. Core Conditions and Tensions (e.g., exact requirements, financial vs. commercial issues, reasons for failure).
3. Important verbatim quotes, strictly formatted with speaker names, parties, and dates.
CRITICAL: Check the source descriptions. Strongly distinguish between "Raadsleden" (Council members) and "Insprekers"/"Burgerbrieven" (Citizens). DO NOT treat citizen quotes as political viewpoints of the council.

VRAAG: {query}
HISTORISCHE CONTEXT:
{context_text}

OUTPUT (Be concise and factual):
**BELANGRIJK**: Gebruik [n] citaties na elke bewering.
"""
        
        # STAGE 2: Debate Mapping
        debate_prompt = f"""You are a political analyst mapping municipal debates in Rotterdam.
Analyze the provided CONTEXT CHUNKS to map the debate dynamics.
Identify:
1. Who was IN FAVOR (Voorstanders) and their core arguments.
2. Who was AGAINST (Tegenstanders/Kritisch) and their core arguments.
3. Relevant ideological positions from [VISION] chunks.
4. **BRON-ATTRIBUTIE**: Voor elk punt, noteer specifiek in welke Commissie (bijv. ZWCS, BWB, MO) of Raadsvergadering dit is besproken.

CRITICAL: Only map the positions of actual political parties and council members. 
- If a quote is from a citizen ("Inspreker"), label it [BURGERPERSPECTIEF].
- ALWAYS use the format: Naam (Partij), Commissie/Raad, Datum.

VRAAG: {query}
HISTORISCHE CONTEXT:
{context_text}

OUTPUT (Map the debate):
**BELANGRIJK**: Gebruik [n] citaties na elke bewering.
"""
        
        logger.info(f"--- STAGE 1+2: Parallel Extraction & Debate Map | Context Length: {len(context_text)} ---")
        try:
            import asyncio as _asyncio
            _loop = _asyncio.get_running_loop()
            ext_response, deb_response = await _asyncio.gather(
                _loop.run_in_executor(None, lambda: self.client.models.generate_content(model=self.model_name, contents=extraction_prompt)),
                _loop.run_in_executor(None, lambda: self.client.models.generate_content(model=self.model_name, contents=debate_prompt)),
            )
            extracted_facts = ext_response.text
            debate_map = deb_response.text
            logger.info(f"--- STAGE 2: Parallel extraction done. Facts length: {len(extracted_facts)} ---")
            
            # STAGE 3: Professional Dossier Synthesis (PvdA Pivot)
            synthesis_prompt = f"""Je bent een strategisch adviseur die een formeel 'Debat-voorbereidingsdossier' schrijft.
Gebruik de 'Extracted Facts' en 'Debate Map' om een gestructureerd rapport te maken in de officiële PvdA-standaardindeling.

### DE 10 VERPLICHTE SECTIES (Gebruik exact deze headers):

1. # 📅 Context & Vorige bespreking
Schrijf een feitelijke inleiding over de voorgeschiedenis van dit dossier.

2. # 📌 Argumenten & Hoofdpunten
Lijst van de belangrijkste inhoudelijke punten en argumenten uit de stukken.

3. # 🚩 Wat vindt {party} van dit onderwerp?
Analyseer hoe dit onderwerp aansluit bij de waarden en standpunten van {party}. 
**EIS**: Inclusief letterlijke citaten (verbatim) van raadsleden, commissieleden of eerdere voorstellen van {party} over dit specifieke onderwerp indien deze in de bronnen voorkomen.

4. # 📄 Wat houdt het voorstel precies in?
Feitelijke, technische samenvatting van het voorliggende besluit of rapport.

5. # 🚀 Vervolg
Wat zijn de procedurele volgende stappen? Wordt het besproken als debat, hamerstuk, of ter kennisname?

6. # 📈 Sterke punten
Positieve elementen en kansen die worden genoemd of passen bij {party}.

7. # 📉 Kritische punten
Diepere duik in kritieke details, risico's, technische mitsen en maren of elementen waar {party} kritisch op is.

8. # ❓ Vragen voor de Wethouder / Commissie
Lijst met scherpe, concrete vragen die gesteld moeten worden tijdens het debat.

9. # 🎤 Bijdrage / Spreektekst
Voorzitter,
Schrijf een tekstueel concept voor de inbreng in de raad/commissie over dit onderwerp. Begin direct na 'Voorzitter,'.

10. # 💰 Bijlage: Financieel Overzicht & Tijdlijn
PLAATS HIER DE TABEL. Gebruik kolommen: | Datum | Gebeurtenis | Bedrag | Bron |

### STRIKTE REGELS VOOR OUTPUT:
- **CITATIES**: Gebruik voor ELKE bewering PRECIES [n] (bijvoorbeeld [1, 3]) om naar de bron te verwijzen. Nooit een getal zonder haken!
- **GEEN INTERNE TAGS**: Gebruik NOOIT termen als [VISION], [FACT], [DEBATE], [CONTEXT] of andere interne metadata-labels in je antwoord. 
- **GEEN INSTRUCTIES**: Laat geen instructietekst tussen haakjes (zoals [Wat is de voorgeschiedenis...]) staan. Schrijf enkel de inhoud.
- **BRON-ATTRIBUTIE**: Noem ALTIJD de Commissie (bijv. ZWCS, BWB) of de Raadsvergadering bij elke datum of quote. Gebruik formaat: Naam (Partij), Commissie, Datum.
- **FINANCIEEL**: Wees extreem specifiek over bedragen. Als er "10 miljoen" staat, neem dit letterlijk over in sectie 10.
- **DIRECT STARTEN**: Begin direct met sectie 1. Geen inleidende zinnen.

VRAAG: {query}

EXTRACTED FACTS & TIMELINE:
{extracted_facts}

DEBATE MAP & POSITIONS:
{debate_map}
"""
            
            logger.info(f"--- STAGE 3: Synthesis | Prompt Length: {len(synthesis_prompt)} ---")
            final_response = self.client.models.generate_content(model=self.model_name, contents=synthesis_prompt)
            answer = final_response.text
            
            # 5. Verify quotes using the expanded context text
            # If direct docs, verification_content is the list of dicts
            verified_answer = self.verify_quotes(answer, verification_content)
            
            return {
                "answer": verified_answer,
                "sources": ordered_sources
            }
        except Exception as e:
            logger.error(f"Synthesis pipeline failed: {e}")
            
            # HEURISTIC FALLBACK: Assemble a basic dossier from available documents
            fallback_answer = f"# 📅 Context & Vorige bespreking (Heuristische Samenvatting)\n"
            fallback_answer += f"De AI-samenvatting is momenteel niet beschikbaar, maar hieronder volgt een overzicht van de gevonden documenten voor de vraag: '{query}'.\n\n"
            
            fallback_answer += "# 📌 Belangrijkste Documenten\n"
            sources_list = ordered_sources if 'ordered_sources' in locals() else []
            for i, src in enumerate(sources_list[:10], 1):
                clean_name = re.sub(r'^\[[a-zA-Z0-9]+\]\s*', '', src.get('name', 'Onbekend'))
                fallback_answer += f"{i}. **{clean_name}** ({src.get('start_date', 'Datum onbekend')})\n"
            
            fallback_answer += f"\n# 🚩 Wat vindt de fractie van dit onderwerp?\n"
            fallback_answer += "Zie de brondocumenten voor de specifieke standpunten en bijdragen van raadsleden.\n\n"
            
            fallback_answer += "# 📄 Detailoverzicht van Bronnen\n"
            fallback_answer += "Raadpleeg de gekoppelde documenten aan de rechterkant voor de volledige tekst en context.\n"
            
            return {
                "answer": fallback_answer,
                "sources": sources_list
            }

    def verify_quotes(self, text: str, source_data: List[Any]) -> str:
        """
        Extracts quotes from text and verifies them against source content.
        source_data can be a list of dicts (with 'content') or RetrievedChunk objects.
        """
        if not text:
            return text
            
        # Extract all quotes
        quotes = re.findall(r'"([^"]{10,})"', text)
        if not quotes:
            return text
            
        # Consolidate all source text
        if isinstance(source_data, str):
            full_source_text = source_data.lower()
        else:
            source_texts = []
            for item in source_data:
                if hasattr(item, 'content'):
                    source_texts.append(item.content.lower())
                elif isinstance(item, dict) and 'content' in item:
                    source_texts.append(item['content'].lower())
            full_source_text = " ".join(source_texts)
        
        verified_text = text
        for quote in quotes:
            # Clean quote for robust matching (ignore whitespace, tiny OCR differences)
            clean_quote = re.sub(r'\s+', ' ', quote.strip()).lower()
            
            # Simple direct match first
            found = False
            if clean_quote in full_source_text:
                found = True
            else:
                # Try fuzzy/normalized match (ignore punctuation)
                norm_quote = re.sub(r'[^\w\s]', '', clean_quote)
                norm_source = re.sub(r'[^\w\s]', '', full_source_text)
                if norm_quote in norm_source:
                    found = True
            
            if not found:
                # If not found, strip the quotes to indicate it's a summary/paraphrase
                # and remove the confusing [Geparafraseerd] tag as requested.
                verified_text = verified_text.replace(f'"{quote}"', quote)
            else:
                # If found, keep the quotes as intended.
                pass
                
        return verified_text

    async def perform_agentic_debate_prep(self, query: str, storage: Any, party: str = "GroenLinks-PvdA", date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
        """
        Phase 11: Agentic Debate Preparation Loop
        A multi-pass workflow that performs a gap analysis and structured synthesis.
        """
        if not self.client:
            return {"answer": "AI is niet geconfigureerd.", "sources": []}
            
        print(f"--- STARTING AGENTIC DEBATE PREP FOR: {query} ---")
        
        # --- PASS 1: Base Retrieval ---
        from services.rag_service import RAGService
        rag = RAGService()
        
        base_query = f"{query} politiek debat standpunten financiële dekking besluit"
        query_embedding = self.generate_embedding(base_query)
        
        print("Agent Step 1: Retrieving baseline context (Parallel Streams + Rewriting)")
        parallel_queries = await self.generate_parallel_queries(base_query)
        base_chunks = await rag.retrieve_parallel_context(
            query_text=base_query,
            query_embedding=query_embedding,
            distribution={"financial": 30, "debate": 45, "fact": 30, "vision": 15},
            overrides=parallel_queries,
            date_from=date_from,
            date_to=date_to,
            fast_mode=True,
        )
        
        if not base_chunks:
            return {"answer": "Geen startinformatie gevonden voor dit debat.", "sources": []}
            
        # Temporarily enrich with metadata
        doc_ids = list(set([str(c.document_id) for c in base_chunks]))
        docs_metadata = {str(d['id']): d for d in storage.get_documents_metadata(doc_ids)}
        for c in base_chunks:
            c.start_date = docs_metadata.get(str(c.document_id), {}).get('start_date') or '1970-01-01'
            c.url = docs_metadata.get(str(c.document_id), {}).get('url') or '#'
            
        base_chunks.sort(key=lambda x: x.start_date)
        base_context, _, _ = rag.expand_to_hierarchical_context(base_chunks, storage)
        
        # --- PASS 2: Gap Analysis (LLM Call 1) ---
        gap_prompt = f"""Je bereidt een gemeenteraadsdebat in Rotterdam voor over: "{query}".
Hier is de initiële gevonden context:
{base_context[:15000]}... [ingekort]

TAAK: Welke CRUCIALE informatie ontbreekt er nog om dit debat goed te voeren? Denk aan:
- Specifieke tegenstemmers in een eerdere fase.
- Het excuus van het college voor een budgetoverschrijding.
- De exacte alternatieve dekking die is voorgesteld.

Schrijf als antwoord ENKEL EN ALLEEN één zoekopdracht (max 15 woorden) die we in de archieven moeten opzoeken om deze blinde vlek aan te vullen. Geef GEEN uitleg, alleen de zoektermen."""
        
        print("Agent Step 2: Running Gap Analysis...")
        try:
            gap_response = self.client.models.generate_content(
                model=self.model_name,
                contents=gap_prompt
            )
            targeted_search_query = gap_response.text.strip().replace('"', '')
            print(f"Agent identified missing info. New search query: '{targeted_search_query}'")
        except Exception as e:
            print(f"Gap analysis failed: {e}")
            targeted_search_query = f"{query} kritiek oppositie tekort"

        # --- PASS 3: Targeted Retrieval ---
        print("Agent Step 3: Executing targeted search (Parallel Streams)")
        targeted_embedding = self.generate_embedding(targeted_search_query)
        targeted_chunks = await rag.retrieve_parallel_context(
            query_text=targeted_search_query,
            query_embedding=targeted_embedding,
            distribution={"financial": 15, "debate": 25, "fact": 15, "vision": 5},
            date_from=date_from,
            date_to=date_to,
            fast_mode=True,
        )
        
        # Merge chunks uniquely based on chunk_id
        seen_chunks = {c.chunk_id for c in base_chunks}
        for tc in targeted_chunks:
            if tc.chunk_id not in seen_chunks:
                base_chunks.append(tc)
                seen_chunks.add(tc.chunk_id)

        logger.info(f"Agent Step 4: Final synthesis with {len(base_chunks)} total chunks")
        
        # Call the unified synthesis pipeline
        return await self._run_analytical_pipeline(query, base_chunks, storage, rag, party=party)

    async def perform_agentic_meeting_analysis(self, item_name: str, documents: List[Dict[str, str]], party: str = "GroenLinks-PvdA") -> Dict[str, Any]:
        """
        New agentic pipeline specifically for individual meeting agenda items.
        Bypasses RAG retrieval since documents are already provided.
        """
        if not self.client:
            return {"answer": "AI is niet geconfigureerd.", "sources": []}
            
        logger.info(f"--- STARTING AGENTIC MEETING ANALYSIS FOR: {item_name} ---")
        
        from services.rag_service import RAGService
        rag = RAGService()
        
        # We treat the documents as the fixed context source
        # Stage 3 will generate the 10-section report
        return await self._run_analytical_pipeline(item_name, documents, None, rag, party=party)

    async def _stream_synthesis_chunks(self, prompt: str):
        """
        Runs Gemini generate_content_stream in a thread and yields text chunks
        asynchronously via an asyncio.Queue so the event loop stays responsive.
        """
        import asyncio
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _producer():
            try:
                for chunk in self.client.models.generate_content_stream(
                    model=self.model_name, contents=prompt
                ):
                    if chunk.text:
                        loop.call_soon_threadsafe(queue.put_nowait, chunk.text)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(None, _producer)
        while True:
            text = await queue.get()
            if text is None:
                break
            yield text

    async def stream_agentic_meeting_analysis(
        self, item_name: str, documents: List[Dict[str, str]], party: str = "GroenLinks-PvdA"
    ):
        """
        Streaming version of perform_agentic_meeting_analysis for SSE delivery.
        Yields dicts with keys: type ("status"|"chunk"|"done"|"error"), plus payload.
        Stages 1+2 run in parallel via asyncio.gather; stage 3 streams token-by-token.
        """
        import asyncio

        if not self.client:
            yield {"type": "error", "message": "AI is niet geconfigureerd."}
            return

        if not documents:
            yield {"type": "error", "message": f"Geen documenten beschikbaar voor: {item_name}"}
            return

        yield {"type": "status", "message": "Documenten voorbereiden..."}

        # Stage 0: context preparation (no LLM)
        context_text = ""
        for i, doc in enumerate(documents, 1):
            context_text += f"=== Document {i}: {doc.get('name', 'Onbekend')} ===\n{doc.get('content', '')}\n\n"

        ordered_sources = []
        for i, doc in enumerate(documents, 1):
            clean_name = re.sub(r'^\[[a-zA-Z0-9]+\]\s*', '', doc.get('name', 'Onbekend'))
            ordered_sources.append({"id": i, "name": clean_name, "url": doc.get('url', '#')})

        extraction_prompt = f"""You are an objective data extractor analyzing municipal documents.
Analyze the provided CONTEXT CHUNKS to extract:
1. A Chronological Timeline of events/budgets.
2. Core Conditions and Tensions (e.g., exact requirements, financial vs. commercial issues, reasons for failure).
3. Important verbatim quotes, strictly formatted with speaker names, parties, and dates.
CRITICAL: Check the source descriptions. Strongly distinguish between "Raadsleden" (Council members) and "Insprekers"/"Burgerbrieven" (Citizens). DO NOT treat citizen quotes as political viewpoints of the council.

VRAAG: {item_name}
HISTORISCHE CONTEXT:
{context_text}

OUTPUT (Be concise and factual):
**BELANGRIJK**: Gebruik [n] citaties na elke bewering.
"""

        debate_prompt = f"""You are a political analyst mapping municipal debates in Rotterdam.
Analyze the provided CONTEXT CHUNKS to map the debate dynamics.
Identify:
1. Who was IN FAVOR (Voorstanders) and their core arguments.
2. Who was AGAINST (Tegenstanders/Kritisch) and their core arguments.
3. Relevant ideological positions from [VISION] chunks.
4. **BRON-ATTRIBUTIE**: Voor elk punt, noteer specifiek in welke Commissie (bijv. ZWCS, BWB, MO) of Raadsvergadering dit is besproken.

CRITICAL: Only map the positions of actual political parties and council members.
- If a quote is from a citizen ("Inspreker"), label it [BURGERPERSPECTIEF].
- ALWAYS use the format: Naam (Partij), Commissie/Raad, Datum.

VRAAG: {item_name}
HISTORISCHE CONTEXT:
{context_text}

OUTPUT (Map the debate):
**BELANGRIJK**: Gebruik [n] citaties na elke bewering.
"""

        # Stages 1+2: parallel (independent, both need context_text only)
        yield {"type": "status", "message": "Feiten extraheren & debat kaart (1+2/3)..."}
        loop = asyncio.get_running_loop()
        try:
            ext_response, deb_response = await asyncio.gather(
                loop.run_in_executor(None, lambda: self.client.models.generate_content(model=self.model_name, contents=extraction_prompt)),
                loop.run_in_executor(None, lambda: self.client.models.generate_content(model=self.model_name, contents=debate_prompt)),
            )
            extracted_facts = ext_response.text
            debate_map = deb_response.text
        except Exception as e:
            logger.error(f"Streaming stages 1+2 failed: {e}")
            yield {"type": "error", "message": f"Analyse mislukt: {str(e)}"}
            return

        synthesis_prompt = f"""Je bent een strategisch adviseur die een formeel 'Debat-voorbereidingsdossier' schrijft.
Gebruik de 'Extracted Facts' en 'Debate Map' om een gestructureerd rapport te maken in de officiële PvdA-standaardindeling.

### DE 10 VERPLICHTE SECTIES (Gebruik exact deze headers):

1. # 📅 Context & Vorige bespreking
Schrijf een feitelijke inleiding over de voorgeschiedenis van dit dossier.

2. # 📌 Argumenten & Hoofdpunten
Lijst van de belangrijkste inhoudelijke punten en argumenten uit de stukken.

3. # 🚩 Wat vindt {party} van dit onderwerp?
Analyseer hoe dit onderwerp aansluit bij de waarden en standpunten van {party}.
**EIS**: Inclusief letterlijke citaten (verbatim) van raadsleden, commissieleden of eerdere voorstellen van {party} over dit specifieke onderwerp indien deze in de bronnen voorkomen.

4. # 📄 Wat houdt het voorstel precies in?
Feitelijke, technische samenvatting van het voorliggende besluit of rapport.

5. # 🚀 Vervolg
Wat zijn de procedurele volgende stappen? Wordt het besproken als debat, hamerstuk, of ter kennisname?

6. # 📈 Sterke punten
Positieve elementen en kansen die worden genoemd of passen bij {party}.

7. # 📉 Kritische punten
Diepere duik in kritieke details, risico's, technische mitsen en maren of elementen waar {party} kritisch op is.

8. # ❓ Vragen voor de Wethouder / Commissie
Lijst met scherpe, concrete vragen die gesteld moeten worden tijdens het debat.

9. # 🎤 Bijdrage / Spreektekst
Voorzitter,
Schrijf een tekstueel concept voor de inbreng in de raad/commissie over dit onderwerp. Begin direct na 'Voorzitter,'.

10. # 💰 Bijlage: Financieel Overzicht & Tijdlijn
PLAATS HIER DE TABEL. Gebruik kolommen: | Datum | Gebeurtenis | Bedrag | Bron |

### STRIKTE REGELS VOOR OUTPUT:
- **CITATIES**: Gebruik voor ELKE bewering PRECIES [n] (bijvoorbeeld [1, 3]) om naar de bron te verwijzen. Nooit een getal zonder haken!
- **GEEN INTERNE TAGS**: Gebruik NOOIT termen als [VISION], [FACT], [DEBATE], [CONTEXT] of andere interne metadata-labels in je antwoord.
- **GEEN INSTRUCTIES**: Laat geen instructietekst tussen haakjes (zoals [Wat is de voorgeschiedenis...]) staan. Schrijf enkel de inhoud.
- **BRON-ATTRIBUTIE**: Noem ALTIJD de Commissie (bijv. ZWCS, BWB) of de Raadsvergadering bij elke datum of quote. Gebruik formaat: Naam (Partij), Commissie, Datum.
- **FINANCIEEL**: Wees extreem specifiek over bedragen. Als er "10 miljoen" staat, neem dit letterlijk over in sectie 10.
- **DIRECT STARTEN**: Begin direct met sectie 1. Geen inleidende zinnen.

VRAAG: {item_name}

EXTRACTED FACTS & TIMELINE:
{extracted_facts}

DEBATE MAP & POSITIONS:
{debate_map}
"""

        # Stage 3: streaming synthesis
        yield {"type": "status", "message": "Rapport schrijven (3/3)..."}
        try:
            async for text_chunk in self._stream_synthesis_chunks(synthesis_prompt):
                yield {"type": "chunk", "text": text_chunk}
        except Exception as e:
            logger.error(f"Streaming stage 3 failed: {e}")
            yield {"type": "error", "message": f"Synthese mislukt: {str(e)}"}
            return

        yield {"type": "done", "sources": ordered_sources}

