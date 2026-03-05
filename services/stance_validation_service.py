#!/usr/bin/env python3
"""
Stance Classification Validation Service

Implements the Benoit et al. (2025) ensemble validation pattern:
After classifying a statement's stance, validate it against the party profile
and re-assess confidence. This validation pass catches misclassifications and
boosts accuracy from 74% (single-pass) to 70-80%+ (validated).

Key insight: Variance across calls signals ambiguity. By comparing the initial
classification against the party profile, we identify and flag problematic cases.
"""

import json
import google.genai as genai
import os
from typing import Dict, Any, Optional
from dataclasses import dataclass

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-2.5-flash')


@dataclass
class StanceClassification:
    """Result of stance classification with validation"""
    statement_text: str
    statement_topic: str
    initial_stance: str  # SUPPORT|OPPOSE|MIXED|NEUTRAL|UNCLEAR
    initial_confidence: str  # HIGH|MEDIUM|LOW
    validated_stance: str  # After profile check
    validated_confidence: str
    confidence_score: float  # 0.0-1.0
    consistency_with_profile: float  # 0.0-1.0, how well does it match party profile
    alternative_interpretation: Optional[str]
    is_ambiguous: bool  # Flag for potential ensemble recheck
    validation_notes: str


class StanceValidator:
    """Validate stance classifications against party profile"""

    def __init__(self, party_profile: Dict[str, Any]):
        """
        Initialize validator with a party profile.

        Args:
            party_profile: Output from PartyProfileExtractor
        """
        self.party_profile = party_profile
        self.party_name = party_profile.get("party_name", "Unknown")

    def validate_statement_stance(
        self, statement_text: str, topic: str, initial_stance: str
    ) -> StanceClassification:
        """
        Validate a single statement's stance against the party profile.

        This is a two-pass approach:
        1. Initial stance classification (done elsewhere)
        2. Validation against profile + confidence reasoning (THIS FUNCTION)

        Args:
            statement_text: The statement to classify
            topic: Policy topic it relates to
            initial_stance: Initial classification (SUPPORT|OPPOSE|MIXED|NEUTRAL|UNCLEAR)

        Returns:
            StanceClassification with validated stance, confidence, and flags
        """

        # Get the party's known position on this topic from profile
        topic_stance = self.party_profile.get("topic_profiles", {}).get(topic, {})
        known_position = topic_stance.get("position", "unknown")
        known_direction = topic_stance.get("direction", "unknown")

        # Create validation prompt
        validation_prompt = f"""
You are validating a political statement's stance classification.

PARTY PROFILE:
Party: {self.party_name}
Ideology: {self.party_profile.get('overall_ideology', 'unknown')}
Known position on "{topic}": {known_position}
Known direction: {known_direction}
Rhetorical tone: {self.party_profile.get('rhetorical_tone', 'unknown')}

STATEMENT TO VALIDATE:
"{statement_text}"

INITIAL CLASSIFICATION:
Stance: {initial_stance}

Your task:
1. Does this statement align with the party's known position on {topic}?
2. Is the initial stance classification correct?
3. How confident are you in the classification? (HIGH/MEDIUM/LOW)
4. What would it take for this statement to have a DIFFERENT stance?

Return ONLY valid JSON (no markdown):
{{
  "statement_excerpt": "short summary of statement",
  "topic": "{topic}",
  "initial_stance": "{initial_stance}",
  "validated_stance": "SUPPORT|OPPOSE|MIXED|NEUTRAL|UNCLEAR",
  "confidence_level": "HIGH|MEDIUM|LOW",
  "confidence_score": 0.85,
  "consistency_with_profile": 0.90,
  "reasoning": "why you agreed or disagreed with initial classification",
  "alternative_interpretation": "what would make this a different stance",
  "is_ambiguous": false,
  "validation_notes": "any edge cases or concerns"
}}
"""

        print(f"  Validating stance on {topic}...")
        response = model.generate_content(validation_prompt)

        try:
            response_text = response.text
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0]
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0]

            data = json.loads(response_text)

            # Convert to StanceClassification
            classification = StanceClassification(
                statement_text=statement_text,
                statement_topic=topic,
                initial_stance=initial_stance,
                initial_confidence="HIGH" if initial_stance != "UNCLEAR" else "MEDIUM",
                validated_stance=data.get("validated_stance", initial_stance),
                validated_confidence=data.get("confidence_level", "MEDIUM"),
                confidence_score=data.get("confidence_score", 0.5),
                consistency_with_profile=data.get("consistency_with_profile", 0.5),
                alternative_interpretation=data.get("alternative_interpretation"),
                is_ambiguous=data.get("is_ambiguous", False),
                validation_notes=data.get("validation_notes", ""),
            )

            return classification

        except json.JSONDecodeError as e:
            print(f"  ✗ Validation failed: {e}")
            # Return a conservative classification if validation fails
            return StanceClassification(
                statement_text=statement_text,
                statement_topic=topic,
                initial_stance=initial_stance,
                initial_confidence="MEDIUM",
                validated_stance="UNCLEAR",  # Default to unclear if we can't validate
                validated_confidence="LOW",
                confidence_score=0.3,
                consistency_with_profile=0.0,
                alternative_interpretation=None,
                is_ambiguous=True,
                validation_notes=f"Validation failed: {str(e)}",
            )

    def batch_validate_statements(
        self, statements: list[Dict[str, str]]
    ) -> list[StanceClassification]:
        """
        Validate multiple statements in sequence.

        Args:
            statements: List of {"text": str, "topic": str, "stance": str}

        Returns:
            List of StanceClassification results
        """
        results = []
        for i, stmt in enumerate(statements, 1):
            print(f"[{i}/{len(statements)}] Validating statement...")
            classification = self.validate_statement_stance(
                statement_text=stmt["text"],
                topic=stmt["topic"],
                initial_stance=stmt.get("stance", "UNCLEAR"),
            )
            results.append(classification)

        # Identify ambiguous items for potential ensemble recheck
        ambiguous_count = sum(1 for c in results if c.is_ambiguous)
        if ambiguous_count > 0:
            print(
                f"\n⚠️  {ambiguous_count}/{len(results)} statements flagged as ambiguous"
            )
            print("  These should be reviewed manually or re-classified in a second pass")

        return results

    def identify_contradictions(
        self, classifications: list[StanceClassification]
    ) -> list[Dict[str, Any]]:
        """
        Identify contradictions: statements from the same party on the same topic
        with opposing stances. This signals policy inconsistency or evolution.

        Args:
            classifications: List of StanceClassification results

        Returns:
            List of contradiction findings
        """
        contradictions = []

        # Group by topic
        by_topic = {}
        for c in classifications:
            if c.statement_topic not in by_topic:
                by_topic[c.statement_topic] = []
            by_topic[c.statement_topic].append(c)

        # Find opposing stances within same topic
        for topic, stances in by_topic.items():
            support_stances = [
                s for s in stances if s.validated_stance == "SUPPORT"
            ]
            oppose_stances = [
                s for s in stances if s.validated_stance == "OPPOSE"
            ]

            if support_stances and oppose_stances:
                contradictions.append(
                    {
                        "topic": topic,
                        "issue": f"Party {self.party_name} both supported AND opposed on {topic}",
                        "support_count": len(support_stances),
                        "oppose_count": len(oppose_stances),
                        "possible_explanation": "Policy evolution over time, OR different contexts, OR internal disagreement",
                        "requires_investigation": True,
                    }
                )

        return contradictions


class HighConfidenceAnalysisPipeline:
    """
    End-to-end pipeline for high-confidence stance analysis.

    Combines:
    1. Initial stance classification (from notulen/moties)
    2. Party profile context (from programme + historical statements)
    3. Validation pass (profile-aware check)
    4. Confidence scoring (0.0-1.0 with alternative interpretations)
    5. Ambiguity flagging (for ensemble recheck)

    Expected accuracy: 70-80%+ (vs 74% for single-pass LLM)
    """

    def __init__(self, party_profile: Dict[str, Any]):
        self.validator = StanceValidator(party_profile)
        self.party_profile = party_profile

    def analyze_statement(
        self, statement_text: str, topic: str
    ) -> StanceClassification:
        """
        Analyze a single statement with full validation pipeline.

        Args:
            statement_text: The statement to analyze
            topic: Policy topic it relates to

        Returns:
            StanceClassification with high confidence
        """

        # Step 1: Initial classification (would normally come from notulen extraction)
        # For this example, we do a quick initial classification
        initial_stance = self._initial_classification(statement_text, topic)

        # Step 2: Validation against profile
        classification = self.validator.validate_statement_stance(
            statement_text=statement_text, topic=topic, initial_stance=initial_stance
        )

        return classification

    def _initial_classification(self, statement_text: str, topic: str) -> str:
        """
        Quick initial stance classification.
        In production, this would come from the notulen extraction step.
        """

        prompt = f"""
Classify this statement's stance on "{topic}" in one word only.

Statement: "{statement_text}"

Return ONLY one of: SUPPORT, OPPOSE, MIXED, NEUTRAL, UNCLEAR
"""

        response = model.generate_content(prompt)
        stance = response.text.strip().upper()

        # Validate response
        valid_stances = ["SUPPORT", "OPPOSE", "MIXED", "NEUTRAL", "UNCLEAR"]
        return stance if stance in valid_stances else "UNCLEAR"


def main():
    """Example usage of the validation service"""

    # Example party profile (would come from PartyProfileExtractor)
    example_profile = {
        "party_name": "GroenLinks-PvdA",
        "overall_ideology": "center-left, progressive",
        "topic_profiles": {
            "Klimaat & Milieu": {
                "position": "Strong climate action, zero-emission zone by 2030",
                "direction": "left",
                "strength": "STRONG",
            },
            "Bouwen & Wonen": {
                "position": "Affordable housing, social housing expansion",
                "direction": "center-left",
                "strength": "STRONG",
            },
        },
        "rhetorical_tone": "idealistic, progressive, community-focused",
    }

    # Example statements to classify
    example_statements = [
        {
            "text": "We must implement stricter emissions standards for industries.",
            "topic": "Klimaat & Milieu",
            "stance": "SUPPORT",
        },
        {
            "text": "Market-based solutions are sufficient for climate goals.",
            "topic": "Klimaat & Milieu",
            "stance": "OPPOSE",
        },
        {
            "text": "Housing costs are too high; we need to build more affordable units.",
            "topic": "Bouwen & Wonen",
            "stance": "SUPPORT",
        },
    ]

    # Run validation pipeline
    validator = StanceValidator(example_profile)

    print("=" * 70)
    print("HIGH-CONFIDENCE STANCE VALIDATION PIPELINE")
    print("=" * 70)

    results = validator.batch_validate_statements(example_statements)

    # Display results
    for i, result in enumerate(results, 1):
        print(f"\n[{i}] {result.statement_topic}")
        print(f"    Statement: {result.statement_text[:80]}...")
        print(f"    Initial: {result.initial_stance}")
        print(f"    Validated: {result.validated_stance}")
        print(f"    Confidence: {result.validated_confidence} ({result.confidence_score:.0%})")
        print(f"    Consistency w/ profile: {result.consistency_with_profile:.0%}")
        if result.is_ambiguous:
            print(f"    ⚠️  AMBIGUOUS - FLAG FOR REVIEW")
        if result.alternative_interpretation:
            print(f"    Alternative: {result.alternative_interpretation}")

    # Check for contradictions
    print("\n" + "=" * 70)
    print("CONTRADICTION CHECK")
    print("=" * 70)
    contradictions = validator.identify_contradictions(results)
    if contradictions:
        for c in contradictions:
            print(f"\n⚠️  {c['issue']}")
            print(f"    Support: {c['support_count']}, Oppose: {c['oppose_count']}")
    else:
        print("\n✓ No contradictions detected")


if __name__ == "__main__":
    main()
