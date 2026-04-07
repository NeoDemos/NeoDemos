"""
Extended judge prompts for virtual notulen audit.

Extends the base eval/judge/prompts.py with meeting-minutes-specific checks:
  - Extended claim verification with hallucination_type classification
  - Transcript faithfulness (AI notulen vs original transcript)
  - Vote attribution accuracy
  - Speaker presence verification

All base prompts are re-exported so callers only need to import from here.
"""

from eval.judge.prompts import (
    SYSTEM,
    ANSWER_RELEVANCE,
    FAITHFULNESS,
    COMPLETENESS,
    FACTUAL_CORRECTNESS,
    SOURCE_ATTRIBUTION,
)

# ── Extended claim verification with hallucination type ──────────────────────

CLAIM_VERIFICATION_EXTENDED = """\
Je bent een factchecker voor vergadernotulen van de Rotterdamse gemeenteraad.
Dit zijn VIRTUELE notulen gegenereerd door AI vanuit video-opnames.
Elke foutieve bewering — met name over stemgedrag, standpunten of citaten — kan gevaarlijk zijn
voor raadsleden die op basis hiervan besluiten nemen.

Je taak: ontleed het fragment in individuele feitelijke beweringen en controleer ELKE bewering
tegen de meegeleverde context (het originele transcriptfragment).

## Onderzoeksfocus
{question}

## Context (origineel transcript — dit is de bron van waarheid)
{context}

## Te controleren tekst (AI-gegenereerde virtual notulen)
{answer}

## Instructies

Stap 1: Ontleed de te controleren tekst in afzonderlijke feitelijke beweringen.
        Elke bewering bevat één verifieerbaar feit.
        Negeer vage of subjectieve uitspraken.

Stap 2: Controleer ELKE bewering. Classificeer als:
        - "supported": directe basis in het transcript
        - "unsupported": niet aantoonbaar in het transcript (mogelijk correct, niet verifieerbaar)
        - "contradicted": het transcript spreekt de bewering expliciet tegen

Stap 3: Wijs voor elke NIET-supported bewering een hallucinatie-type toe:
        - "vote_attribution"   — onjuiste stemtoeschrijving (bijv. "D66 stemde voor" zonder basis)
        - "speaker_presence"   — spreker wordt geciteerd die niet sprak
        - "false_consensus"    — suggereert unanimiteit die er niet was
        - "amount_error"       — financieel bedrag, percentage of statistiek klopt niet
        - "date_displacement"  — datum of tijdsperiode onjuist
        - "party_position"     — standpunt toegeschreven aan partij die het niet innam
        - "fabricated_quote"   — citaat dat niet in het transcript staat
        - "other"              — andere hallucinatie

Stap 4: NIET als hallucinatie aanmerken:
        - Fonetische spelvarianten van namen (bijv. "Morkoets" voor "Morkoç",
          "Joskoen" voor "Coşkun") — dit zijn ASR-transcriptiefouten, geen fabricaties.
        - Feitelijke beweringen die een SPREKER maakte tijdens de vergadering en die
          letterlijk in de context staan, ook al zijn ze inhoudelijk onjuist. De notulen
          geven weer wat er GEZEGD is, niet of het waar is.
        - Partijlidmaatschap dat in de sprekerprefix staat (bijv. "[Naam (VVD)]:")
          — dit is afkomstig uit de officiële vergadermetadata, niet gegenereerd.

Stap 5: Let EXTRA op gevaarlijke patronen:
        - Specifieke getallen die NIET in de context staan
        - Toeschrijving aan personen/partijen die NIET in de context staan
        - Beweringen over wat partijen "vinden" of "vonden" zonder bronverwijzing
        - Details die klinken als algemene kennis maar niet uit het transcript komen

Antwoord in JSON:
{{
    "claims": [
        {{
            "claim": "<de bewering>",
            "verdict": "supported|unsupported|contradicted",
            "hallucination_type": "<type of null als supported>",
            "evidence": "<relevante passage uit context, of 'niet gevonden'>"
        }}
    ],
    "total_claims": <aantal>,
    "supported": <aantal>,
    "unsupported": <aantal>,
    "contradicted": <aantal>,
    "hallucination_rate": <(unsupported + contradicted) / total_claims, afgerond op 2 decimalen>,
    "most_dangerous_claim": "<de bewering met het hoogste risico voor een raadslid, of null>",
    "hallucination_types_found": ["<type1>", "<type2>"],
    "reasoning": "<korte samenvatting van de bevindingen>"
}}
"""

# ── Transcript faithfulness ───────────────────────────────────────────────────

TRANSCRIPT_FAITHFULNESS = """\
Je controleert of een AI-gegenereerd vergaderverslag (virtual notulen) trouw is aan het
originele transcriptfragment.

Dit is KRITIEK: virtual notulen worden gebruikt door Rotterdamse gemeenteraadsleden.
Een gefabriceerde uitspraak of een omgedraaide stemverhouding kan tot politieke schade leiden.

## Origineel transcript-fragment (bron van waarheid)
{context}

## AI-gegenereerd vergaderverslag-fragment
{answer}

## Controleer specifiek:
1. Zijn uitspraken toegeschreven aan dezelfde spreker als in het origineel?
2. Zijn stemverhoudingen en besluiten correct overgenomen?
3. Zijn er zinnen die NIET terug te voeren zijn op het transcript?
4. Is er politiek relevante informatie WEGGELATEN?
5. Zijn financiële bedragen, data of percentages correct?

## Scorebereik (0–5)
- 5: Nauwkeurige samenvatting — geen fabricaties, geen betekenisverranderende omissies.
- 4: Vrijwel nauwkeurig; hooguit 1 kleine parafrase die de betekenis niet verandert.
- 3: Grotendeels correct maar 1–2 inhoudelijke afwijkingen.
- 2: Meerdere inhoudelijke afwijkingen of fabricaties.
- 1: Significante fabricaties of onjuistheden.
- 0: De notulen zijn niet trouw aan het transcript.

Antwoord in JSON:
{{
    "score": <0-5>,
    "reasoning": "<toelichting in het Nederlands>",
    "fabricated_content": "<wat er gefabriceerd is, of null>",
    "omitted_content": "<wat er ontbreekt dat politiek relevant is, of null>"
}}
"""

# ── Vote attribution check ────────────────────────────────────────────────────

VOTE_ATTRIBUTION_CHECK = """\
Je controleert stemgedrag en besluiten in een AI-gegenereerd vergaderverslag.

## Fragment uit virtual notulen
{answer}

## Bijbehorend origineel transcript
{context}

Identificeer ALLE besluitvormingsmomenten (stemmingen, moties, amendementen, besluiten)
in beide teksten.

Voor elk besluit: stemde het verslag EXACT overeen met het transcript?
Let op:
- Voor/tegen verhoudingen
- Welke partijen voor/tegen stemden
- Of een motie/amendement werd aangenomen of verworpen
- Unanime besluiten vs. verdeelde stemmingen

Antwoord in JSON:
{{
    "votes_in_notulen": [
        {{
            "description": "<beschrijving van het besluit>",
            "outcome_notulen": "<aangenomen/verworpen/onbekend>",
            "parties_for": ["<partij1>"],
            "parties_against": ["<partij1>"],
            "found_in_transcript": true,
            "consistent": true,
            "inconsistency": "<beschrijving of null>"
        }}
    ],
    "total_votes": <aantal>,
    "consistent_votes": <aantal>,
    "vote_accuracy_rate": <consistent / total, of null als geen stemmingen>,
    "critical_errors": ["<beschrijving van ernstige fouten>"],
    "reasoning": "<samenvatting>"
}}
"""

# ── Chunk informativeness (for RAG quality) ───────────────────────────────────

CHUNK_INFORMATIVENESS = """\
Beoordeel of dit tekstfragment uit een vergaderverslag voldoende informatief is
om nuttig te zijn bij het beantwoorden van vragen over de vergadering.

## Vraag (typische zoekvraag over een commissievergadering)
{question}

## Tekstfragment (uit virtual notulen)
{answer}

## Scorebereik (0–5)
- 5: Fragment bevat specifieke, verifieerbare informatie die de vraag direct kan beantwoorden.
- 4: Fragment is relevant en informatief, al is het antwoord niet volledig hierin.
- 3: Fragment bevat enige relevante informatie maar is beperkt.
- 2: Fragment is slechts oppervlakkig relevant.
- 1: Fragment bevat nauwelijks bruikbare informatie.
- 0: Fragment is niet relevant of is louter proceduretekst zonder inhoud.

Antwoord in JSON: {{"score": <0-5>, "reasoning": "<korte toelichting in het Nederlands>"}}
"""

# ── Consolidated prompts map ──────────────────────────────────────────────────

PROMPTS = {
    "answer_relevance": ANSWER_RELEVANCE,
    "faithfulness": FAITHFULNESS,
    "completeness": COMPLETENESS,
    "factual_correctness": FACTUAL_CORRECTNESS,
    "source_attribution": SOURCE_ATTRIBUTION,
    # Overrides and extensions
    "claim_verification": CLAIM_VERIFICATION_EXTENDED,
    "transcript_faithfulness": TRANSCRIPT_FAITHFULNESS,
    "vote_attribution_check": VOTE_ATTRIBUTION_CHECK,
    "chunk_informativeness": CHUNK_INFORMATIVENESS,
}
