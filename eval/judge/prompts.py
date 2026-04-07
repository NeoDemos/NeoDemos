"""
Dutch-language judge prompt templates for RAG evaluation.

Each metric gets its own prompt to avoid score conflation.
All prompts enforce JSON output: {"score": 0-5, "reasoning": "..."}
"""

SYSTEM = (
    "Je bent een strenge, onpartijdige AI-beoordelaar voor een RAG-systeem "
    "over de Rotterdamse gemeentepolitiek. Je geeft altijd eerlijke scores "
    "op basis van de meegeleverde gegevens. Antwoord uitsluitend in valide JSON."
)

ANSWER_RELEVANCE = """\
Beoordeel hoe goed het antwoord de vraag beantwoordt.

## Vraag
{question}

## Antwoord
{answer}

## Scorebereik (0-5)
- 5: Het antwoord beantwoordt de vraag volledig en direct, zonder onnodige informatie.
- 4: Het antwoord beantwoordt de vraag grotendeels, met minimale afdwaling.
- 3: Het antwoord raakt het onderwerp maar mist belangrijke aspecten of bevat veel irrelevante info.
- 2: Het antwoord is slechts gedeeltelijk relevant.
- 1: Het antwoord raakt het onderwerp nauwelijks.
- 0: Het antwoord gaat volledig langs de vraag heen, of er is geen antwoord.

Antwoord in JSON: {{"score": <0-5>, "reasoning": "<korte toelichting in het Nederlands>"}}
"""

FAITHFULNESS = """\
Beoordeel of het antwoord uitsluitend gebaseerd is op de meegeleverde context.

## Vraag
{question}

## Context (aangeleverd door het RAG-systeem)
{context}

## Antwoord
{answer}

## Scorebereik (0-5)
- 5: Elke bewering in het antwoord is direct terug te vinden in de context.
- 4: Vrijwel alle beweringen zijn terug te vinden; hooguit 1 minor detail ontbreekt.
- 3: Het merendeel is gebaseerd op de context, maar er zijn enkele onverifieerbare claims.
- 2: Significant deel van het antwoord bevat informatie die niet in de context staat.
- 1: Het meeste is niet gebaseerd op de context.
- 0: Het antwoord is volledig verzonnen (hallucinatie) of bevat geen relatie tot de context.

BELANGRIJK: Als het antwoord een bewering bevat die NIET in de context voorkomt, verlaag de
faithfulness score — ongeacht of de bewering feitelijk klopt. Een RAG-systeem moet zich
baseren op zijn bronnen, niet op externe kennis.

Als het antwoord eerlijk zegt "deze informatie is niet beschikbaar in de bronnen", scoor dan
HOOG op faithfulness (dat is eerlijk gedrag).

Antwoord in JSON: {{"score": <0-5>, "reasoning": "<korte toelichting in het Nederlands>"}}
"""

FACTUAL_CORRECTNESS = """\
Beoordeel de feitelijke juistheid van het antwoord.

## Vraag
{question}

## Antwoord
{answer}

## Referentie-antwoord (Gold Standard)
{gold_answer}

## Scorebereik (0-5)
- 5: Alle feiten kloppen en komen overeen met het referentie-antwoord.
- 4: Vrijwel alles klopt; hooguit een klein detail wijkt af.
- 3: De hoofdlijnen kloppen, maar er zijn enkele feitelijke onnauwkeurigheden.
- 2: Er zijn significante feitelijke fouten.
- 1: Het merendeel van de feiten is onjuist.
- 0: Het antwoord is volledig feitelijk onjuist.

Gebruik het referentie-antwoord als absolute bron van waarheid. Als er geen referentie-antwoord
is, gebruik dan je eigen kennis van de Rotterdamse gemeentepolitiek, maar wees terughoudend
met lage scores als je zelf niet zeker bent.

Antwoord in JSON: {{"score": <0-5>, "reasoning": "<korte toelichting in het Nederlands>"}}
"""

COMPLETENESS = """\
Beoordeel hoe volledig het antwoord is — dekt het alle relevante aspecten?
Dit is vooral belangrijk voor overzichtsvragen en vragen die om meerdere perspectieven vragen.

## Vraag
{question}

## Context (aangeleverd door het RAG-systeem)
{context}

## Antwoord
{answer}

## Scorebereik (0-5)
- 5: Het antwoord behandelt alle relevante aspecten die in de context te vinden zijn.
- 4: Het antwoord dekt de meeste aspecten, met slechts 1 minor aspect gemist.
- 3: Het antwoord dekt de hoofdlijnen maar mist meerdere relevante aspecten.
- 2: Het antwoord is oppervlakkig en mist de helft of meer van de relevante informatie.
- 1: Het antwoord noemt slechts 1 aspect terwijl er meerdere zijn.
- 0: Het antwoord is leeg of volledig onvolledig.

Let vooral op:
- Worden zowel positieve als negatieve kanten benoemd (indien gevraagd)?
- Worden meerdere betrokken partijen/actoren genoemd?
- Worden financiële, beleidsmatige en praktische aspecten behandeld?

Antwoord in JSON: {{"score": <0-5>, "reasoning": "<korte toelichting in het Nederlands>"}}
"""

CLAIM_VERIFICATION = """\
Je bent een factchecker voor een RAG-systeem dat wordt gebruikt door Rotterdamse gemeenteraadsleden.
Elke foutieve bewering kan het vertrouwen in het systeem vernietigen.

Je taak: ontleed het antwoord in individuele feitelijke beweringen en controleer ELKE bewering
tegen de meegeleverde context.

## Vraag
{question}

## Context (aangeleverd door het RAG-systeem)
{context}

## Antwoord (gegenereerd door het systeem)
{answer}

## Instructies

Stap 1: Ontleed het antwoord in afzonderlijke feitelijke beweringen.
        Elke bewering moet één verifieerbaar feit bevatten.
        Negeer vage of subjectieve uitspraken (bijv. "dit is een belangrijk onderwerp").

Stap 2: Controleer ELKE bewering tegen de context. Classificeer als:
        - "supported": de bewering is direct terug te vinden in de context
        - "unsupported": de bewering staat NIET in de context (mogelijk correct, maar niet verifieerbaar)
        - "contradicted": de context spreekt de bewering expliciet tegen

Stap 3: Let EXTRA op deze gevaarlijke hallucinatie-patronen:
        - Specifieke getallen (euro's, percentages, aantallen) die niet in de context staan
        - Toeschrijving aan specifieke personen of partijen die niet in de context worden genoemd
        - Datums of tijdsperioden die niet overeenkomen met de context
        - Beweringen over wat partijen "vinden" of "vonden" zonder bronverwijzing in de context
        - Details die klinken alsof ze uit algemene kennis komen in plaats van uit de bronnen

Antwoord in JSON:
{{
    "claims": [
        {{
            "claim": "<de bewering>",
            "verdict": "supported|unsupported|contradicted",
            "evidence": "<relevante passage uit de context, of 'niet gevonden'>"
        }}
    ],
    "total_claims": <aantal>,
    "supported": <aantal>,
    "unsupported": <aantal>,
    "contradicted": <aantal>,
    "hallucination_rate": <unsupported + contradicted / total_claims, afgerond op 2 decimalen>,
    "most_dangerous_claim": "<de bewering met het hoogste risico voor verkeerd gebruik door een raadslid, of null>",
    "reasoning": "<korte samenvatting van de bevindingen>"
}}
"""

SOURCE_ATTRIBUTION = """\
Beoordeel of het antwoord correcte bronverwijzingen bevat.
In de context van de Rotterdamse gemeenteraad is het cruciaal dat uitspraken correct worden
toegeschreven aan de juiste persoon, partij of document.

## Vraag
{question}

## Context (aangeleverd door het RAG-systeem)
{context}

## Antwoord
{answer}

## Controleer specifiek:
1. Worden uitspraken correct toegeschreven aan de juiste partij/persoon?
2. Worden standpunten niet verwisseld tussen partijen?
3. Worden datums/perioden correct gekoppeld aan de juiste gebeurtenissen?
4. Wordt er onderscheid gemaakt tussen wat in de bronnen staat en wat het systeem zelf concludeert?

## Scorebereik (0-5)
- 5: Alle toeschrijvingen zijn correct en verifieerbaar in de context.
- 4: Vrijwel alle toeschrijvingen kloppen; 1 minor fout.
- 3: De meeste toeschrijvingen kloppen maar er zijn enkele fouten.
- 2: Meerdere verkeerde toeschrijvingen — onbetrouwbaar voor raadsleden.
- 1: De meeste toeschrijvingen zijn fout of niet verifieerbaar.
- 0: Geen enkele toeschrijving is correct, of het antwoord bevat geen bronverwijzingen terwijl die nodig zijn.

Antwoord in JSON: {{"score": <0-5>, "reasoning": "<korte toelichting in het Nederlands>"}}
"""

# Map metric names to templates
PROMPTS = {
    "answer_relevance": ANSWER_RELEVANCE,
    "faithfulness": FAITHFULNESS,
    "factual_correctness": FACTUAL_CORRECTNESS,
    "completeness": COMPLETENESS,
    "claim_verification": CLAIM_VERIFICATION,
    "source_attribution": SOURCE_ATTRIBUTION,
}
