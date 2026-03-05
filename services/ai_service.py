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

class AIService:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        
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

    async def _analyze_with_gemini(self, item_name: str, documents: List[Dict[str, str]], party_vision: Optional[str] = None) -> Dict[str, Any]:
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
                top_k=10
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
    
    def generate_embedding(self, text: str) -> Optional[List[float]]:
        """
        Generate a vector embedding for text using Gemini embedding API.
        Returns a list of floats (3072 dimensions for gemini-embedding-001).
        Returns None if embedding fails or LLM not available.
        """
        if not self.use_llm:
            return None
        
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
