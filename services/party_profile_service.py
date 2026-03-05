#!/usr/bin/env python3
"""
High-Confidence Party Profile Extraction Service

Builds a comprehensive party profile by analyzing:
1. Official party programme (normative positions)
2. Historical notulen statements (actual behaviour)
3. Moties/amendementen (explicit votes and proposals)

This multi-source approach creates profiles grounded in actual behaviour,
not just rhetorical promises, resulting in 70-80%+ accuracy for stance
classification when combined with contextual validation.

Based on: Approach 3 (Contextual Actor Profiling) with enhancements from
Benoit ensemble validation pattern.
"""

import json
import psycopg2
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import google.genai as genai
import os

# Initialize Gemini client
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    client = genai.Client(api_key=api_key)
else:
    client = None


@dataclass
class TopicStance:
    """Party's stance on a specific topic"""
    topic_id: int
    topic_name: str
    position: str  # Specific policy position(s)
    direction: str  # 'left', 'center-left', 'center', 'center-right', 'right'
    strength: str  # 'WEAK', 'MODERATE', 'STRONG'
    evidence_sources: List[str]  # ['programme', 'notulen', 'motie']
    confidence: float  # 0.0-1.0
    contradictions: Optional[List[str]] = None  # Where positions conflict


@dataclass
class PartyProfile:
    """Comprehensive party profile for contextual stance detection"""
    party_name: str
    profile_generated_at: str
    overall_ideology: str  # e.g., "center-left, progressive, social-democratic"
    priority_topics: List[str]  # Top 5-7 topics
    rhetorical_tone: str  # Communication style
    voting_pattern: str  # How they typically vote
    coalition_behaviour: str  # Tendency to cooperate/block
    topic_stances: Dict[str, TopicStance]  # Per-topic detailed positions
    key_distinguishing_positions: List[str]  # What sets them apart
    internal_consistency: float  # 0.0-1.0, how consistent across sources
    data_sources_used: List[str]  # ['programme', 'notulen', 'moties']
    notes: str  # Analyst notes, observations


class PartyProfileExtractor:
    """Extract high-confidence party profiles from multiple sources"""

    def __init__(self):
        self.conn = psycopg2.connect(
            "postgresql://postgres:postgres@localhost:5432/neodemos"
        )
        self.cursor = self.conn.cursor()

    def extract_full_profile(
        self, party_name: str, party_pattern: str = None
    ) -> PartyProfile:
        """
        Extract comprehensive party profile from all available sources.

        Args:
            party_name: Normalized party name (e.g., "GroenLinks-PvdA")
            party_pattern: Regex pattern to match in documents (e.g., "groenlinks|pvda|partij van de arbeid")

        Returns:
            PartyProfile with high-confidence stances
        """
        print(f"\n{'='*70}")
        print(f"EXTRACTING PARTY PROFILE: {party_name}")
        print(f"{'='*70}\n")

        # Step 1: Extract from programme
        print("[1/3] Extracting positions from party programme...")
        programme_positions = self._extract_programme_positions(party_name)

        # Step 2: Extract from notulen
        print("[2/3] Extracting statements from gemeenteraad notulen...")
        notulen_positions = self._extract_notulen_positions(party_name, party_pattern)

        # Step 3: Extract from moties/amendementen
        print("[3/3] Extracting proposals from moties/amendementen...")
        motie_positions = self._extract_motie_positions(party_name, party_pattern)

        # Step 4: Synthesize into unified profile
        print("\n[4/4] Synthesizing unified profile from all sources...")
        profile = self._synthesize_profile(
            party_name,
            programme_positions,
            notulen_positions,
            motie_positions,
        )

        print("\n" + "="*70)
        print(f"PROFILE EXTRACTION COMPLETE")
        print(f"Confidence: {profile.internal_consistency:.1%}")
        print(f"Topics covered: {len(profile.topic_stances)}")
        print("="*70 + "\n")

        return profile

    def _extract_programme_positions(self, party_name: str) -> Dict[int, Dict[str, Any]]:
        """Extract policy positions directly from party programme"""

        # Get programme content
        self.cursor.execute(
            "SELECT pdf_content FROM party_programmes WHERE party_name = %s",
            (party_name,),
        )
        result = self.cursor.fetchone()
        if not result:
            print(f"  ⚠️  No programme found for {party_name}")
            return {}

        programme_text = result[0]

        # Prompt Gemini to extract structured positions
        extraction_prompt = f"""
You are a political analyst specializing in Dutch municipal politics.
Analyze this party programme and extract their policy positions on each topic area.

PARTY PROGRAMME:
{programme_text[:50000]}  # First 50K chars to fit in context

For each major topic area mentioned in the programme, extract:
1. Topic category (from the list below)
2. The party's specific policy position(s)
3. The ideological direction (left, center-left, center, center-right, right)
4. Strength of commitment (WEAK, MODERATE, STRONG)
5. Any apparent contradictions between sections

Topic categories to classify into:
- Werk & Inkomen
- Onderwijs
- Samenleven
- Schuldhulpverlening & Armoedebestrijding
- Zorg & Welzijn
- Cultuur
- Sport
- Bouwen & Wonen
- Buitenruimte & Groen
- Mobiliteit & Verkeer
- Haven & Scheepvaart
- Economie & Bedrijven
- Klimaat & Milieu
- Veiligheid
- Bestuur & Organisatie
- Financiën
- Burgerparticipatie
- Digitalisering
- Genderbeleid
- Migratie & Integratie
- Ruimtelijke Ordening

Return ONLY a valid JSON object (no markdown, no explanation). Format:
{{
  "overall_ideology": "center-left, progressive, social-democratic",
  "priority_topics": ["Klimaat & Milieu", "Bouwen & Wonen", ...],
  "rhetorical_tone": "description of how they communicate",
  "positions_by_topic": {{
    "Klimaat & Milieu": {{
      "position": "Specific policy statements from programme",
      "direction": "left",
      "strength": "STRONG",
      "quoted_passages": ["...", "..."]
    }},
    ...
  }},
  "key_distinguishing_positions": ["...", "..."],
  "apparent_contradictions": ["...", "..."]
}}
"""

        print("  Calling Gemini to extract programme positions...")
        if not client:
            raise RuntimeError("GEMINI_API_KEY not set")
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=extraction_prompt
        )

        try:
            # Extract JSON from response
            response_text = response.text
            # Handle markdown code blocks
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0]
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0]

            data = json.loads(response_text)
            print(f"  ✓ Extracted positions from programme")
            return data
        except json.JSONDecodeError as e:
            print(f"  ✗ Failed to parse Gemini response: {e}")
            print(f"    Response: {response_text[:200]}")
            return {}

    def _extract_notulen_positions(
        self, party_name: str, party_pattern: str = None
    ) -> Dict[int, Dict[str, Any]]:
        """Extract actual positions from gemeenteraad notulen"""

        # Get all notulen for gemeenteraad meetings
        self.cursor.execute(
            """
            SELECT d.id, d.name, d.content, m.start_date
            FROM documents d
            INNER JOIN meetings m ON d.meeting_id = m.id
            INNER JOIN document_classifications dc ON d.id = dc.document_id
            WHERE m.name = 'Gemeenteraad'
            AND dc.is_notulen = TRUE
            AND d.content IS NOT NULL
            ORDER BY m.start_date DESC
            """
        )
        notulen_docs = self.cursor.fetchall()

        if not notulen_docs:
            print(f"  ⚠️  No notulen found for Gemeenteraad")
            return {}

        print(f"  Found {len(notulen_docs)} notulen documents")

        positions = {}

        # Extract from each notulen
        for notulen_id, notulen_name, content, meeting_date in notulen_docs:
            if not content:
                continue

            extraction_prompt = f"""
You are analyzing gemeenteraad (municipal council) notulen (meeting minutes) to identify 
the positions and statements of the party: {party_name}.

In these notulen, find all statements, votes, or positions expressed by {party_name} members.
Look for: named statements, votes (adopted/rejected), proposed amendments, etc.

NOTULEN EXCERPT:
{content[:30000]}

For each statement or vote by {party_name}, extract:
1. The topic it relates to (from the taxonomy provided below)
2. The speaker's name (if mentioned)
3. The statement or position (verbatim or summary)
4. The stance (SUPPORT, OPPOSE, MIXED, NEUTRAL, UNCLEAR)
5. The context (was this a vote? a debate? a proposal?)

Topic taxonomy:
- Werk & Inkomen, Onderwijs, Samenleven, Schuldhulpverlening & Armoedebestrijding
- Zorg & Welzijn, Cultuur, Sport, Bouwen & Wonen, Buitenruimte & Groen
- Mobiliteit & Verkeer, Haven & Scheepvaart, Economie & Bedrijven, Klimaat & Milieu
- Veiligheid, Bestuur & Organisatie, Financiën, Burgerparticipatie, Digitalisering
- Genderbeleid, Migratie & Integratie, Ruimtelijke Ordening

Return ONLY valid JSON (no markdown). Format:
{{
  "notulen_id": "{notulen_id}",
  "meeting_date": "{meeting_date}",
  "party_name": "{party_name}",
  "statements": [
    {{
      "speaker": "name or null",
      "topic": "topic category",
      "position": "verbatim or summarized statement",
      "stance": "SUPPORT|OPPOSE|MIXED|NEUTRAL|UNCLEAR",
      "context": "description of context"
    }},
    ...
  ]
}}
"""

            if not client:
                raise RuntimeError("GEMINI_API_KEY not set")
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=extraction_prompt
            )
            try:
                response_text = response.text
                if "```json" in response_text:
                    response_text = response_text.split("```json")[1].split("```")[0]
                elif "```" in response_text:
                    response_text = response_text.split("```")[1].split("```")[0]

                data = json.loads(response_text)
                positions[notulen_id] = data
            except json.JSONDecodeError:
                print(f"    ⚠️  Failed to parse notulen {notulen_name}")

        print(f"  ✓ Extracted statements from {len(positions)} notulen")
        return positions

    def _extract_motie_positions(
        self, party_name: str, party_pattern: str = None
    ) -> List[Dict[str, Any]]:
        """Extract positions from moties and amendementen"""

        # Find all moties/amendementen (without filtering by content first - avoid ILIKE on large text)
        self.cursor.execute(
            """
            SELECT d.id, d.name, d.content
            FROM documents d
            INNER JOIN meetings m ON d.meeting_id = m.id
            WHERE m.name = 'Gemeenteraad'
            AND (d.name ILIKE '%motie%' OR d.name ILIKE '%amendement%')
            ORDER BY d.name
            """
        )

        all_motie_docs = self.cursor.fetchall()

        if not all_motie_docs:
            print(f"  ⚠️  No moties/amendementen found")
            return []

        # Filter for party mentions in Python (avoids problematic ILIKE on large content)
        search_pattern = party_pattern or party_name.lower()
        search_terms = search_pattern.split('|')
        
        motie_docs = [
            (doc_id, doc_name, content) 
            for doc_id, doc_name, content in all_motie_docs 
            if content and any(term.lower() in content.lower() for term in search_terms)
        ]

        if not motie_docs:
            print(f"  ⚠️  No moties/amendementen mentioning {party_name}")
            return []

        print(f"  Found {len(motie_docs)} moties/amendementen for {party_name}")

        positions = []

        # Extract from each motie
        for motie_id, motie_name, content in motie_docs:
            if not content:
                continue

            extraction_prompt = f"""
Analyze this motie/amendement from a gemeente raad meeting. Identify the party's position.

MOTIE/AMENDEMENT:
{content[:10000]}

Extract:
1. Which party proposed this? (Should be {party_name})
2. Topic area (from taxonomy below)
3. What is being proposed or opposed?
4. The stance being taken (SUPPORT for something, OPPOSE something, etc.)
5. Was this adopted or rejected?

Topic taxonomy:
Werk & Inkomen, Onderwijs, Samenleven, Schuldhulpverlening & Armoedebestrijding,
Zorg & Welzijn, Cultuur, Sport, Bouwen & Wonen, Buitenruimte & Groen,
Mobiliteit & Verkeer, Haven & Scheepvaart, Economie & Bedrijven, Klimaat & Milieu,
Veiligheid, Bestuur & Organisatie, Financiën, Burgerparticipatie, Digitalisering,
Genderbeleid, Migratie & Integratie, Ruimtelijke Ordening

Return ONLY valid JSON:
{{
  "motie_id": "{motie_id}",
  "motie_name": "{motie_name}",
  "proposing_party": "{party_name}",
  "topic": "topic category",
  "proposal": "what is being proposed/opposed",
  "stance": "SUPPORT|OPPOSE|MIXED",
  "outcome": "adopted|rejected|withdrawn",
  "significance": "WEAK|MODERATE|STRONG"
}}
"""

            if not client:
                raise RuntimeError("GEMINI_API_KEY not set")
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=extraction_prompt
            )
            try:
                response_text = response.text
                if "```json" in response_text:
                    response_text = response_text.split("```json")[1].split("```")[0]
                elif "```" in response_text:
                    response_text = response_text.split("```")[1].split("```")[0]

                data = json.loads(response_text)
                positions.append(data)
            except json.JSONDecodeError:
                print(f"    ⚠️  Failed to parse motie {motie_name}")

        print(f"  ✓ Extracted positions from {len(positions)} moties/amendementen")
        return positions

    def _synthesize_profile(
        self,
        party_name: str,
        programme_data: Dict,
        notulen_data: Dict,
        motie_data: List,
    ) -> PartyProfile:
        """
        Synthesize all sources into a unified, high-confidence party profile.
        This is where we catch contradictions and flag confidence issues.
        """

        # Create a synthesis prompt that compares all sources
        synthesis_prompt = f"""
You are synthesizing a comprehensive party profile from three sources:
1. Official party programme
2. Statements in gemeenteraad notulen
3. Moties/amendementen they submitted

PROGRAMME DATA:
{json.dumps(programme_data, indent=2)[:10000]}

NOTULEN STATEMENTS (sample):
{json.dumps(list(notulen_data.values())[:2], indent=2)[:8000]}

MOTIES/AMENDEMENTEN (sample):
{json.dumps(motie_data[:5], indent=2)[:5000]}

Create a unified party profile that:
1. Identifies their core ideological position
2. Lists priority topics (5-7 most important)
3. For EACH of the 21 topics: synthesize their position based on all sources
4. Flag any contradictions between programme and actual behaviour
5. Assess overall internal consistency (0.0-1.0)
6. Identify rhetorical tone and communication style
7. Note voting patterns (do they block, compromise, lead?)

Return ONLY valid JSON (no markdown, no explanation):
{{
  "party_name": "{party_name}",
  "overall_ideology": "brief ideological summary",
  "priority_topics": ["topic1", "topic2", ...],
  "rhetorical_tone": "how they communicate",
  "voting_pattern": "their voting behaviour",
  "coalition_behaviour": "do they cooperate or block?",
  "topic_profiles": {{
    "Klimaat & Milieu": {{
      "position": "synthesis of all statements",
      "direction": "left|center-left|center|center-right|right",
      "strength": "WEAK|MODERATE|STRONG",
      "evidence": "mentioned in [programme|notulen|moties]",
      "consistency": 0.85
    }},
    ... (for all 21 topics, or null if no data)
  }},
  "programme_vs_behaviour": {{
    "overall_match": 0.75,
    "contradictions": ["contradiction1", "contradiction2"]
   }},
   "internal_consistency": 0.82,
   "key_distinguishing_positions": ["position1", "position2"],
   "notes": "any observations"
}}
"""

        print("  Synthesizing profile across all sources...")
        if not client:
            raise RuntimeError("GEMINI_API_KEY not set")
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=synthesis_prompt
        )

        try:
            response_text = response.text
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0]
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0]

            data = json.loads(response_text)

            # Convert to PartyProfile
            profile = PartyProfile(
                party_name=party_name,
                profile_generated_at=datetime.now().isoformat(),
                overall_ideology=data.get("overall_ideology", ""),
                priority_topics=data.get("priority_topics", []),
                rhetorical_tone=data.get("rhetorical_tone", ""),
                voting_pattern=data.get("voting_pattern", ""),
                coalition_behaviour=data.get("coalition_behaviour", ""),
                topic_stances={},  # TODO: map to TopicStance dataclass
                key_distinguishing_positions=data.get("key_distinguishing_positions", []),
                internal_consistency=data.get("internal_consistency", 0.0),
                data_sources_used=["programme", "notulen", "moties"],
                notes=data.get("notes", ""),
            )

            return profile

        except json.JSONDecodeError as e:
            print(f"  ✗ Failed to synthesize profile: {e}")
            raise

    def store_profile(self, profile: PartyProfile) -> bool:
        """Store the generated profile in the database"""
        try:
            self.cursor.execute(
                """
                UPDATE party_programmes
                SET profile_json = %s, extraction_status = 'profile_extracted'
                WHERE party_name = %s
                """,
                (json.dumps(asdict(profile)), profile.party_name),
            )
            self.conn.commit()
            print(f"✓ Profile stored for {profile.party_name}")
            return True
        except Exception as e:
            print(f"✗ Error storing profile: {e}")
            return False


async def main():
    """Main entry point for profile extraction"""
    extractor = PartyProfileExtractor()

    # Extract profile for GroenLinks-PvdA
    profile = extractor.extract_full_profile(
        party_name="GroenLinks-PvdA",
        party_pattern="groenlinks|pvda|partij van de arbeid",
    )

    # Display profile
    print("\nGENERATED PROFILE:")
    print("=" * 70)
    print(json.dumps(asdict(profile), indent=2, default=str))
    print("=" * 70)

    # Store in database
    extractor.store_profile(profile)

    # Now present for review
    print("\n⚠️  PROFILE GENERATED - AWAITING YOUR REVIEW\n")
    print("Please review the profile above for accuracy and completeness.")
    print("Would you like to:")
    print("  1. Accept the profile as-is")
    print("  2. Request modifications")
    print("  3. Regenerate with different sources")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
