"""
MCP Tool Registry — single source of truth for NeoDemos v0.2.0 MCP tool metadata.

Every tool registered on the NeoDemos MCP server (see `mcp_server_v3.py`) must
have a corresponding ToolSpec in REGISTRY. The ai_description follows the
FactSet rule: descriptions are written for AI consumption, not human docs, and
every entry MUST list positive and negative use cases. The registry is the
basis for:

  - OpenAPI export (docs/api/mcp_openapi.json)
  - Tool-collision detection (services/mcp_tool_uniqueness.py)
  - Parameter validation (Layer 2 defense-in-depth)
  - /public/mcp endpoint allow-list

The registry does NOT embed current-roster data or behavioral instructions
("call this proactively"); those belong in the get_neodemos_context() primer
tool per WS4 discipline.
"""

from dataclasses import dataclass, field
from typing import Literal, Optional


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ToolExample:
    """One example invocation of a tool, used in both docs and eval harnesses."""

    description: str
    input: dict
    expected_output_shape: str


@dataclass
class ToolSpec:
    """Metadata for a single MCP tool. Immutable at runtime."""

    name: str
    summary: str
    ai_description: str
    module: str = "mcp_server_v3"
    scopes: list[str] = field(default_factory=lambda: ["mcp", "search"])
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)
    latency_p50_ms: int = 0  # 0 == not yet measured
    cost_per_call_usd: float = 0.0
    stability: Literal["stable", "experimental", "deprecated"] = "stable"
    added_in_version: str = "0.1.0"
    public: bool = True  # eligible for /public/mcp (no user scoping)
    examples: list[ToolExample] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Shared schema fragments
# ---------------------------------------------------------------------------


_MARKDOWN_OUTPUT: dict = {
    "type": "string",
    "description": "Markdown-formatted response",
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


REGISTRY: dict[str, ToolSpec] = {
    # ---------------------------------------------------------------------
    # 1. zoek_raadshistorie
    # ---------------------------------------------------------------------
    "zoek_raadshistorie": ToolSpec(
        name="zoek_raadshistorie",
        summary="[DEFAULT SEARCH] Hybride BM25+vector zoekmachine over het volledige corpus met reranking.",
        ai_description=(
            "[DEFAULT SEARCH] De standaard zoekmachine wanneer geen andere gespecialiseerde tool "
            "past. Combineert BM25 full-text-search (dual-dictionary dutch+simple) met Qwen3-8B "
            "vector-retrieval en Jina v3 reranking. Doorzoekt het VOLLEDIGE corpus: notulen, "
            "raadsbrieven, commissiestukken, raadsvoorstellen — alle documenttypen.\n"
            "\n"
            "Gebruik deze tool wanneer:\n"
            "- De vraag inhoudelijk is ('wat is er besloten over X', 'hoe denkt de raad over Y') "
            "  en geen andere tool specifieker is.\n"
            "- Je citeerbare fragmenten nodig hebt met context, datum, en partij-tag.\n"
            "\n"
            "Gebruik deze tool NIET wanneer:\n"
            "- Euro-bedragen, begrotingstabellen of budgetjaren → `zoek_financieel`.\n"
            "- Stemuitslag/uitkomst van moties of amendementen → `zoek_moties`.\n"
            "- Breed overzicht met 40-80 korte previews → `scan_breed`.\n"
            "- Eén bekende persoon met rolwisseling → `zoek_uitspraken_op_rol`.\n"
            "- Letterlijke partij-citaten → `zoek_uitspraken`.\n"
            "\n"
            "Retourneert: markdown met max 20 unieke-document fragmenten (titel, datum, partij, "
            "score, document_id, bronlink)."
        ),
        input_schema={
            "type": "object",
            "required": ["vraag"],
            "properties": {
                "vraag": {
                    "type": "string",
                    "description": "Zoekterm of vraag in het Nederlands (temporele termen uit de tekst halen).",
                },
                "datum_van": {
                    "type": ["string", "null"],
                    "description": "Startdatum in ISO formaat (JJJJ-MM-DD).",
                },
                "datum_tot": {
                    "type": ["string", "null"],
                    "description": "Einddatum in ISO formaat (JJJJ-MM-DD).",
                },
                "partij": {
                    "type": ["string", "null"],
                    "description": "Partijnaam, bijv. 'VVD', 'PvdA', 'Leefbaar Rotterdam'.",
                },
                "max_resultaten": {
                    "type": "integer",
                    "description": "Aantal resultaten.",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 10,
                },
            },
        },
        output_schema=_MARKDOWN_OUTPUT,
        examples=[
            ToolExample(
                description="Positief: inhoudelijk onderwerp met datumfilter.",
                input={
                    "vraag": "fietsinfrastructuur oost",
                    "datum_van": "2023-01-01",
                    "datum_tot": "2024-12-31",
                    "max_resultaten": 8,
                },
                expected_output_shape="Markdown met tot 8 fragmenten, elk met datum, partij, score, document_id.",
            ),
            ToolExample(
                description="Negatief: begrotingsvraag — gebruik `zoek_financieel` in plaats daarvan.",
                input={"vraag": "begroting jeugdzorg 2024"},
                expected_output_shape="Deze tool retourneert wel resultaten maar mist table_json boost en budget_year — verkies `zoek_financieel`.",
            ),
        ],
    ),
    # ---------------------------------------------------------------------
    # 2. zoek_financieel
    # ---------------------------------------------------------------------
    "zoek_financieel": ToolSpec(
        name="zoek_financieel",
        summary="Financiele retrieval met tabel-boost en fiscaal-jaar filter (budget_year).",
        ai_description=(
            "Zoekt begrotingen, jaarstukken, budgetmutaties en subsidiedata. Boost tabelchunks "
            "(table_json) boven tekstchunks, en kan filteren op fiscaal jaar via `budget_year`. "
            "Rendert gevonden tabellen als gestructureerde markdown tables.\n"
            "\n"
            "Gebruik deze tool wanneer:\n"
            "- De vraag over kosten, budgetten, bezuinigingen, subsidies of begrotingsposten gaat.\n"
            "- Je een specifiek fiscaal jaar bedoelt: 'wat is de begrotingsruimte voor 2025' "
            "  → gebruik `budget_year=2025`. Let op het verschil met publicatiedatum: de "
            "  Begroting 2025 wordt in oktober 2024 ingediend (publicatiedatum) maar beschrijft "
            "  fiscaal jaar 2025 (budget_year). Voor 'welke begrotingsdocumenten werden in "
            "  oktober 2024 gepubliceerd' gebruik `datum_van='2024-10-01'` in plaats van "
            "  `budget_year`.\n"
            "\n"
            "Gebruik deze tool NIET wanneer:\n"
            "- De vraag algemeen-beleidsmatig is zonder financiele component — gebruik "
            "  `zoek_raadshistorie`.\n"
            "- Je een specifieke motie over een bezuiniging wilt ophalen met stemuitkomst — "
            "  gebruik `zoek_moties`.\n"
            "- Je de volledige tekst van een bekend begrotingsdocument wilt lezen — gebruik "
            "  `lees_fragment` met de document_id en passeer de originele query voor in-doc "
            "  re-rank.\n"
            "\n"
            "Retourneert: markdown met tabel-boosted chunks (max 20), inclusief gerenderde "
            "markdown tables waar table_json aanwezig is en de bronvermelding."
        ),
        input_schema={
            "type": "object",
            "required": ["onderwerp"],
            "properties": {
                "onderwerp": {
                    "type": "string",
                    "description": "Financieel onderwerp, bijv. 'jeugdzorg begroting'.",
                },
                "datum_van": {
                    "type": ["string", "null"],
                    "description": "Startdatum (publicatiedatum) in ISO formaat.",
                },
                "datum_tot": {
                    "type": ["string", "null"],
                    "description": "Einddatum (publicatiedatum) in ISO formaat.",
                },
                "budget_year": {
                    "type": ["integer", "null"],
                    "description": "Fiscaal doeljaar van het budget (staging.financial_documents.budget_years).",
                },
                "max_resultaten": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 12,
                },
            },
        },
        output_schema=_MARKDOWN_OUTPUT,
        examples=[
            ToolExample(
                description="Positief: budget_year pint het fiscaal jaar vast, onafhankelijk van publicatiedatum.",
                input={"onderwerp": "jeugdzorg", "budget_year": 2024},
                expected_output_shape="Markdown met financiele chunks van documenten waarvan budget_years 2024 bevat; tabellen gerenderd.",
            ),
            ToolExample(
                description="Negatief: algemene beleidsvraag zonder cijfers — verkies `zoek_raadshistorie`.",
                input={"onderwerp": "mening college over wonen"},
                expected_output_shape="Deze tool kan resultaten geven maar tabel-boost is ongewenst voor een debat-vraag.",
            ),
        ],
    ),
    # ---------------------------------------------------------------------
    # 3. zoek_uitspraken
    # ---------------------------------------------------------------------
    "zoek_uitspraken": ToolSpec(
        name="zoek_uitspraken",
        summary="[PARTY-TOPIC QUOTES] Haalt letterlijke citaten uit debatten op, gefilterd op fractienaam.",
        ai_description=(
            "[PARTY-TOPIC QUOTES] Haalt letterlijke citaten en debat-uitspraken op. Wanneer "
            "`partij_of_raadslid` een bekende fractienaam is (SP, VVD, GroenLinks-PvdA...) wordt "
            "gefilterd via Qdrant vector-payload; voor persoonsnamen wordt de naam als keyword "
            "toegevoegd. Input = onderwerp + optionele fractie. Output = debatfragmenten.\n"
            "\n"
            "Gebruik deze tool wanneer:\n"
            "- 'Wat zei fractie X over onderwerp Y?' — de typische use-case.\n"
            "- Letterlijke citaten uit plenaire of commissiedebatten nodig zijn.\n"
            "\n"
            "Gebruik deze tool NIET wanneer:\n"
            "- De persoon van rol is gewisseld (bijv. raadslid naar wethouder) en je periode-awareness "
            "  nodig hebt — gebruik `zoek_uitspraken_op_rol` (resolveert rolperiodes automatisch).\n"
            "- Je het volledige partijstandpunt op een beleidsgebied wilt inclusief profieldata — "
            "  gebruik `haal_partijstandpunt_op`.\n"
            "- Je specifiek moties of amendementen van een partij zoekt — gebruik `zoek_moties` "
            "  met de `partij` parameter.\n"
            "\n"
            "Retourneert: markdown met tot 20 gedededupeerde citaten, elk met spreker/partij, "
            "datum, score en document_id."
        ),
        input_schema={
            "type": "object",
            "required": ["onderwerp"],
            "properties": {
                "onderwerp": {"type": "string"},
                "partij_of_raadslid": {
                    "type": ["string", "null"],
                    "description": "Partijnaam (gefilterd via payload) of naam raadslid (als keyword).",
                },
                "datum_van": {"type": ["string", "null"]},
                "datum_tot": {"type": ["string", "null"]},
                "max_resultaten": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 10,
                },
            },
        },
        output_schema=_MARKDOWN_OUTPUT,
        examples=[
            ToolExample(
                description="Positief: partij-filter via bekende partijnaam.",
                input={"onderwerp": "warmtebedrijf", "partij_of_raadslid": "SP"},
                expected_output_shape="Markdown met SP-fragmenten uit debatten over het warmtebedrijf.",
            ),
            ToolExample(
                description="Negatief: persoon met rolwisseling — gebruik `zoek_uitspraken_op_rol`.",
                input={"onderwerp": "klimaat", "partij_of_raadslid": "Buijt"},
                expected_output_shape="Deze tool mist periode-awareness voor rolwisselingen; verkies `zoek_uitspraken_op_rol`.",
            ),
        ],
    ),
    # ---------------------------------------------------------------------
    # 4. haal_vergadering_op
    # ---------------------------------------------------------------------
    "haal_vergadering_op": ToolSpec(
        name="haal_vergadering_op",
        summary="Eén specifieke vergadering ophalen met volledige agenda + documenten (per id of exacte datum).",
        ai_description=(
            "Geeft de VOLLEDIGE AGENDA van exact ÉÉN raads- of commissievergadering: alle "
            "agendapunten (met geneste sub-items), de commissie-naam, en ALLE bijbehorende "
            "documenten (moties, raadsvoorstellen, brieven) inline onder elk agendapunt plus "
            "een totaallijst onderaan. Input is een `vergadering_id` OF een exacte datum "
            "`JJJJ-MM-DD`.\n"
            "\n"
            "Gebruik deze tool wanneer:\n"
            "- De gebruiker de agenda of bijlagen van een BEKENDE vergaderdatum wil inzien.\n"
            "- Je vanuit een eerder zoekresultaat (dat een `vergadering_id` noemde) de volledige "
            "  context wilt ophalen.\n"
            "\n"
            "Gebruik deze tool NIET wanneer:\n"
            "- De gebruiker een OVERZICHT van meerdere vergaderingen in een periode wil — gebruik "
            "  `lijst_vergaderingen` (geeft een kalenderlijst per jaar/commissie).\n"
            "- Je documentinhoud wilt lezen — gebruik `lees_fragment` met het document_id uit "
            "  het resultaat.\n"
            "\n"
            "Retourneert: markdown met vergadernaam, datum, commissie, geneste agendapunten "
            "en een documentenlijst."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "vergadering_id": {"type": ["string", "null"]},
                "datum": {
                    "type": ["string", "null"],
                    "description": "ISO datum JJJJ-MM-DD (exclusief met vergadering_id).",
                },
            },
        },
        output_schema=_MARKDOWN_OUTPUT,
        examples=[
            ToolExample(
                description="Positief: lookup by date.",
                input={"datum": "2024-06-13"},
                expected_output_shape="Markdown met agenda en documenten van de raadsvergadering op 2024-06-13.",
            ),
            ToolExample(
                description="Negatief: gebruiker wil meerdere vergaderingen in een periode — gebruik `lijst_vergaderingen`.",
                input={"datum": "2024"},
                expected_output_shape="Deze tool verwacht een volledige datum; gebruik `lijst_vergaderingen(jaar=2024)` voor een lijst.",
            ),
        ],
    ),
    # ---------------------------------------------------------------------
    # 5. lijst_vergaderingen
    # ---------------------------------------------------------------------
    "lijst_vergaderingen": ToolSpec(
        name="lijst_vergaderingen",
        summary="Kalenderoverzicht: meerdere vergaderingen per jaar/commissie als compacte tabel.",
        ai_description=(
            "Retourneert een KALENDEROVERZICHT van MEERDERE vergaderingen als een compacte "
            "markdown-tabel (datum, naam, commissie, vergadering_id). Filtert op `jaar` (integer) "
            "en/of `commissie` (gedeeltelijke naam-match). Bevat GEEN agenda of documenten — "
            "alleen een planningsoverzicht.\n"
            "\n"
            "Gebruik deze tool wanneer:\n"
            "- De gebruiker wil weten HOEVEEL en WANNEER vergaderingen in een jaar of commissie "
            "  hebben plaatsgevonden (kalender-achtige vraag).\n"
            "- Je vergadering_ids nodig hebt om er vervolgens één mee op te halen via "
            "  `haal_vergadering_op`.\n"
            "\n"
            "Gebruik deze tool NIET wanneer:\n"
            "- De gebruiker de agenda of documenten van één bekende vergaderdatum wil — gebruik "
            "  `haal_vergadering_op`.\n"
            "- De vraag inhoudelijk is ('wat werd er besproken over X') — gebruik "
            "  `zoek_raadshistorie` of `scan_breed`.\n"
            "\n"
            "Retourneert: markdown-tabel met max 100 rijen (datum, naam, commissie, "
            "vergadering_id)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "jaar": {"type": ["integer", "null"]},
                "commissie": {"type": ["string", "null"]},
                "max_resultaten": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 25,
                },
            },
        },
        output_schema=_MARKDOWN_OUTPUT,
        examples=[
            ToolExample(
                description="Positief: alle vergaderingen van de commissie MPOF in 2023.",
                input={"jaar": 2023, "commissie": "MPOF"},
                expected_output_shape="Markdown-tabel met vergaderingen van de commissie MPOF in 2023.",
            ),
            ToolExample(
                description="Negatief: inhoudelijke vraag — gebruik `zoek_raadshistorie`.",
                input={"commissie": "wonen"},
                expected_output_shape="Deze tool retourneert alleen metadata van vergaderingen, niet de inhoud.",
            ),
        ],
    ),
    # ---------------------------------------------------------------------
    # 6. tijdlijn_besluitvorming
    # ---------------------------------------------------------------------
    "tijdlijn_besluitvorming": ToolSpec(
        name="tijdlijn_besluitvorming",
        summary="Chronologische tijdlijn van raadsfragmenten over een onderwerp, gegroepeerd per jaar.",
        ai_description=(
            "Bouwt een chronologische tijdlijn van discussies en besluitvorming over een onderwerp. "
            "Dedupliceert op document_id, lost ontbrekende datums op via de vergaderdatum en "
            "groepeert fragmenten per jaar. Filtert chunks met score < 0.2.\n"
            "\n"
            "Gebruik deze tool wanneer:\n"
            "- De gebruiker wil zien hoe een beleidsdossier zich over tijd heeft ontwikkeld.\n"
            "- Je een evolutie wilt tonen (meerdere jaren, per jaar max 5 fragmenten getoond).\n"
            "\n"
            "Gebruik deze tool NIET wanneer:\n"
            "- De gebruiker alleen de meest recente discussie zoekt — gebruik `zoek_raadshistorie` "
            "  met `datum_van`.\n"
            "- Je alleen stemuitkomsten over tijd wilt zien — gebruik `zoek_moties` met "
            "  `datum_van`/`datum_tot`.\n"
            "- De vraag een breed overzicht vraagt zonder temporele ordening — gebruik "
            "  `scan_breed`.\n"
            "\n"
            "Retourneert: markdown met jaartal-secties, elk met tot 5 fragmenten (titel, snippet, "
            "score, document_id)."
        ),
        input_schema={
            "type": "object",
            "required": ["onderwerp"],
            "properties": {
                "onderwerp": {"type": "string"},
                "datum_van": {"type": ["string", "null"]},
                "datum_tot": {"type": ["string", "null"]},
            },
        },
        output_schema=_MARKDOWN_OUTPUT,
        examples=[
            ToolExample(
                description="Positief: evolutie van een dossier over meerdere jaren.",
                input={
                    "onderwerp": "warmtebedrijf",
                    "datum_van": "2018-01-01",
                    "datum_tot": "2024-12-31",
                },
                expected_output_shape="Markdown met jaartal-secties en fragmenten over het warmtebedrijf-dossier.",
            ),
            ToolExample(
                description="Negatief: gebruiker wil alleen het laatste debat — gebruik `zoek_raadshistorie`.",
                input={"onderwerp": "meest recente begrotingsdebat"},
                expected_output_shape="Deze tool levert een tijdlijn; voor alleen het recentste fragment is `zoek_raadshistorie` efficienter.",
            ),
        ],
    ),
    # ---------------------------------------------------------------------
    # 7. analyseer_agendapunt
    # ---------------------------------------------------------------------
    "analyseer_agendapunt": ToolSpec(
        name="analyseer_agendapunt",
        summary="Bundelt documentinhoud, partijprofiel en historische context voor een agendapunt.",
        ai_description=(
            "Verzamelt alle informatie die nodig is voor analyse van een specifiek agendapunt: "
            "de volledige documentinhoud van subdocumenten, het statische partijprofiel en "
            "historische RAG-context via Jina v3 reranking.\n"
            "\n"
            "Gebruik deze tool wanneer:\n"
            "- Je een agendapunt_id hebt en een volledige lensbriefing wilt opstellen.\n"
            "- De gebruiker vraagt om een analyse vanuit een specifiek partijperspectief.\n"
            "\n"
            "Gebruik deze tool NIET wanneer:\n"
            "- Je geen agendapunt_id hebt — gebruik eerst `haal_vergadering_op` of "
            "  `lijst_vergaderingen` om een id te vinden.\n"
            "- De vraag alleen om het partijstandpunt gaat — gebruik `haal_partijstandpunt_op`.\n"
            "- Je alleen de volledige tekst van een enkel document wilt — gebruik `lees_fragment`.\n"
            "\n"
            "Retourneert: markdown met agendapunt-header, documentinhoud (tot 4 docs, 4000 tekens "
            "per doc), partijprofiel-samenvatting en 6 historische RAG-fragmenten."
        ),
        input_schema={
            "type": "object",
            "required": ["agendapunt_id"],
            "properties": {
                "agendapunt_id": {"type": "string"},
                "partij": {
                    "type": "string",
                    "default": "GroenLinks-PvdA",
                    "description": "Partijnaam voor de lenssessie.",
                },
            },
        },
        output_schema=_MARKDOWN_OUTPUT,
        examples=[
            ToolExample(
                description="Positief: briefing op een concreet agendapunt.",
                input={"agendapunt_id": "abc-123", "partij": "GroenLinks-PvdA"},
                expected_output_shape="Markdown briefing met documentinhoud, partijprofiel en historische context.",
            ),
            ToolExample(
                description="Negatief: gebruiker heeft alleen een onderwerp, geen agendapunt_id.",
                input={"agendapunt_id": "wonen"},
                expected_output_shape="Tool geeft 'niet gevonden'; gebruik eerst `haal_vergadering_op` of `zoek_raadshistorie`.",
            ),
        ],
    ),
    # ---------------------------------------------------------------------
    # 8. haal_partijstandpunt_op
    # ---------------------------------------------------------------------
    "haal_partijstandpunt_op": ToolSpec(
        name="haal_partijstandpunt_op",
        summary="Combineert statisch partijprofiel met partij-gefilterde RAG voor een beleidsgebied.",
        ai_description=(
            "Haalt het statische profiel van een partij op voor een beleidsgebied (kernwaarde, "
            "programmapunt, consistentie, notulenverwijzingen) en vult dit aan met partij-gefilterde "
            "RAG-retrieval uit de notulen.\n"
            "\n"
            "Gebruik deze tool wanneer:\n"
            "- De vraag luidt: 'wat is het standpunt van partij X over onderwerp Y'.\n"
            "- Je zowel programmapunten als recente notulen-citaten wilt combineren.\n"
            "\n"
            "Gebruik deze tool NIET wanneer:\n"
            "- Je alleen losse citaten zonder profielcontext zoekt — gebruik `zoek_uitspraken` "
            "  met `partij_of_raadslid`.\n"
            "- De vraag over een individuele persoon met rolwisseling gaat — gebruik "
            "  `zoek_uitspraken_op_rol`.\n"
            "- Je een motie van een partij zoekt — gebruik `zoek_moties` met de `partij` "
            "  parameter.\n"
            "\n"
            "Retourneert: markdown met profielentries (tot 5) en tot 5 aanvullende RAG-fragmenten "
            "gefilterd op de partij."
        ),
        input_schema={
            "type": "object",
            "required": ["beleidsgebied"],
            "properties": {
                "beleidsgebied": {"type": "string"},
                "partij": {
                    "type": "string",
                    "default": "GroenLinks-PvdA",
                },
            },
        },
        output_schema=_MARKDOWN_OUTPUT,
        examples=[
            ToolExample(
                description="Positief: volledig standpunt + context.",
                input={"beleidsgebied": "Wonen", "partij": "PvdA"},
                expected_output_shape="Markdown met profielsamenvatting en RAG-citaten van PvdA over wonen.",
            ),
            ToolExample(
                description="Negatief: alleen losse citaten — verkies `zoek_uitspraken`.",
                input={"beleidsgebied": "citaten van VVD tijdens laatste debat", "partij": "VVD"},
                expected_output_shape="Deze tool is gericht op beleidsgebieden, niet op losse debatcitaten.",
            ),
        ],
    ),
    # ---------------------------------------------------------------------
    # 9. zoek_moties
    # ---------------------------------------------------------------------
    "zoek_moties": ToolSpec(
        name="zoek_moties",
        summary="Directe SQL-zoekopdracht op moties, amendementen en initiatiefvoorstellen met uitkomst en indieners.",
        ai_description=(
            "Doorzoekt moties, amendementen en initiatiefvoorstellen via directe SQL op "
            "documentnaam EN content. Retourneert de geparseerde uitkomst "
            "(aangenomen/verworpen/ingetrokken), stemverhouding, indieners en de eerste 1500 "
            "tekens van de motietekst. Dit is de beste bron voor stemmingsvragen.\n"
            "\n"
            "Gebruik deze tool wanneer:\n"
            "- De vraag over stemgedrag, verworpen voorstellen of stemverhoudingen gaat.\n"
            "- Je moties of amendementen van een specifieke partij of indiener zoekt.\n"
            "- De gebruiker 'motie', 'amendement' of 'initiatiefvoorstel' noemt, ook voor "
            "  single-word topic queries (content wordt altijd meegezocht).\n"
            "\n"
            "Gebruik deze tool NIET wanneer:\n"
            "- De vraag gaat om het bredere debat of de context rond een onderwerp, niet om "
            "  stemuitkomsten — gebruik `zoek_raadshistorie` of `zoek_uitspraken`.\n"
            "- Je financiele cijfers uit begrotingstabellen nodig hebt — gebruik "
            "  `zoek_financieel`.\n"
            "- Je de volledige tekst van een gevonden motie wilt lezen — gebruik "
            "  `lees_fragment` met de document_id.\n"
            "\n"
            "Retourneert: markdown met per resultaat documentnaam, datum, uitkomst, "
            "stemverhouding (indien beschikbaar), indieners, preview (tot 1500 tekens), "
            "document_id en bronlink."
        ),
        input_schema={
            "type": "object",
            "required": ["onderwerp"],
            "properties": {
                "onderwerp": {"type": "string"},
                "uitkomst": {
                    "type": ["string", "null"],
                    "description": "Filter op uitkomst: 'verworpen', 'aangenomen', 'ingetrokken'.",
                },
                "datum_van": {"type": ["string", "null"]},
                "datum_tot": {"type": ["string", "null"]},
                "partij": {"type": ["string", "null"]},
                "indiener": {
                    "type": ["string", "null"],
                    "description": "Naam van de indiener/raadslid; initialen worden gestript.",
                },
                "max_resultaten": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 80,
                    "default": 20,
                },
            },
        },
        output_schema=_MARKDOWN_OUTPUT,
        examples=[
            ToolExample(
                description="Positief: verworpen moties over een onderwerp binnen periode.",
                input={
                    "onderwerp": "leegstand",
                    "uitkomst": "verworpen",
                    "datum_van": "2022-01-01",
                },
                expected_output_shape="Markdown met verworpen moties/initiatiefvoorstellen over leegstand sinds 2022.",
            ),
            ToolExample(
                description="Negatief: gebruiker wil debat-citaten, niet stemuitkomsten.",
                input={"onderwerp": "wat zei PvdA over wonen"},
                expected_output_shape="Deze tool retourneert alleen moties/amendementen; gebruik `zoek_uitspraken` voor debat-citaten.",
            ),
        ],
    ),
    # ---------------------------------------------------------------------
    # 10. scan_breed
    # ---------------------------------------------------------------------
    "scan_breed": ToolSpec(
        name="scan_breed",
        summary="INDEX-SCAN: breed overzicht met tot 80 korte previews als tabel (titel + 100 tekens).",
        ai_description=(
            "INDEX-SCAN over het corpus: retourneert tot 80 treffers als een compacte "
            "markdown-TABEL met per rij datum, titel, partij, score en document_id. Elke rij "
            "bevat een snippet van max 100 tekens — net genoeg om relevantie te beoordelen, "
            "NIET genoeg om te citeren. Dit is de discovery-stap bij breed onderzoek.\n"
            "\n"
            "Gebruik deze tool wanneer:\n"
            "- De onderzoeksvraag breed is en je niet van tevoren weet welke stukken relevant zijn.\n"
            "- Je in één call tot 80 treffers wilt inventariseren i.p.v. meerdere smalle zoektools "
            "  te kettingen.\n"
            "\n"
            "Gebruik deze tool NIET wanneer:\n"
            "- Je een citeerbaar fragment nodig hebt — de snippets zijn te kort; gebruik "
            "  `zoek_raadshistorie` (volledige context per hit).\n"
            "- Je een chronologische tijdlijn wilt — gebruik `tijdlijn_besluitvorming`.\n"
            "- Je alleen moties/amendementen zoekt — gebruik `zoek_moties` (structured metadata).\n"
            "\n"
            "Retourneert: markdown-tabel (datum, titel, partij, score, document_id, snippet). "
            "Sluit af met een hint dat `lees_fragment` volledige tekst kan ophalen."
        ),
        input_schema={
            "type": "object",
            "required": ["vraag"],
            "properties": {
                "vraag": {"type": "string"},
                "datum_van": {"type": ["string", "null"]},
                "datum_tot": {"type": ["string", "null"]},
                "partij": {"type": ["string", "null"]},
                "max_resultaten": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 80,
                    "default": 40,
                },
            },
        },
        output_schema=_MARKDOWN_OUTPUT,
        examples=[
            ToolExample(
                description="Positief: breed onderzoek aftrappen.",
                input={"vraag": "haven duurzaamheid", "max_resultaten": 40},
                expected_output_shape="Markdown-tabel met tot 40 hits rond haven+duurzaamheid, te gebruiken als index voor vervolgtools.",
            ),
            ToolExample(
                description="Negatief: concrete vraag die volledige citaten vereist — verkies `zoek_raadshistorie`.",
                input={"vraag": "wat was het exacte standpunt van wethouder X op 2024-06-13"},
                expected_output_shape="Previews zijn te kort voor een citaat-antwoord; gebruik `zoek_raadshistorie` of `lees_fragment`.",
            ),
        ],
    ),
    # ---------------------------------------------------------------------
    # 11. lees_fragment
    # ---------------------------------------------------------------------
    "lees_fragment": ToolSpec(
        name="lees_fragment",
        summary="DEEP READ: volledige tekst van fragmenten uit één document (met optionele query-rerank).",
        ai_description=(
            "DEEP READ van één enkel document: haalt de VOLLEDIGE tekst van fragmenten op "
            "uit een document waarvan je het `document_id` al kent. Dit is de follow-up-stap "
            "na een zoektool die een document_id opleverde. Geef ALTIJD de originele `query` "
            "mee als je dit document via een topic-zoektocht vond — dan worden de fragmenten "
            "via Jina v3 herrangschikt zodat het relevante gedeelte bovenaan staat. Zonder "
            "query: fragmenten in chunk-index volgorde (kan betekenen dat het relevante stuk "
            "begraven wordt onder samenvattingsparagrafen).\n"
            "\n"
            "Gebruik deze tool wanneer:\n"
            "- Je al een `document_id` hebt en de VOLLEDIGE inhoud wilt lezen om te citeren.\n"
            "- Je via een topic-zoekopdracht een document vond: geef ALTIJD dezelfde query mee.\n"
            "\n"
            "Gebruik deze tool NIET wanneer:\n"
            "- Je nog geen `document_id` hebt — gebruik eerst `zoek_raadshistorie`, `zoek_moties`, "
            "  `zoek_financieel` of `scan_breed`.\n"
            "- Je GERELATEERDE stukken zoekt bij dit document — gebruik `zoek_gerelateerd`.\n"
            "\n"
            "Retourneert: markdown met kop (documentnaam, vergadering, datum, bronlink) en "
            "tot `max_fragmenten` fragmenten inclusief gerenderde tabellen."
        ),
        input_schema={
            "type": "object",
            "required": ["document_id"],
            "properties": {
                "document_id": {"type": "string"},
                "max_fragmenten": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 5,
                },
                "query": {
                    "type": ["string", "null"],
                    "description": "Optionele topic-query; activeert Jina v3 in-doc re-rank.",
                },
            },
        },
        output_schema=_MARKDOWN_OUTPUT,
        examples=[
            ToolExample(
                description="Positief: document gevonden via zoekopdracht, query meegeven voor re-rank.",
                input={
                    "document_id": "fin_jaarstukken_2019",
                    "query": "Middelland venstertijden",
                    "max_fragmenten": 5,
                },
                expected_output_shape="Markdown met de 5 meest relevante fragmenten voor 'Middelland venstertijden' binnen fin_jaarstukken_2019.",
            ),
            ToolExample(
                description="Negatief: zonder document_id — gebruik eerst een zoektool.",
                input={"document_id": "wonen"},
                expected_output_shape="Geen fragmenten gevonden; gebruik eerst `zoek_raadshistorie` of `scan_breed`.",
            ),
        ],
    ),
    # ---------------------------------------------------------------------
    # 12. zoek_gerelateerd
    # ---------------------------------------------------------------------
    "zoek_gerelateerd": ToolSpec(
        name="zoek_gerelateerd",
        summary="Vindt documenten gerelateerd aan een gegeven document via meeting, trefwoord of afdoeningsvoorstel.",
        ai_description=(
            "Zoekt documenten die gerelateerd zijn aan een gegeven `document_id` via drie "
            "strategieën: (1) andere documenten uit dezelfde vergadering, (2) trefwoord-match op "
            "de gestripte titel, en (3) afdoeningsvoorstellen die verwijzen naar het brondocument.\n"
            "\n"
            "Gebruik deze tool wanneer:\n"
            "- Je vanuit een motie het bijbehorende debat wilt vinden, of andersom.\n"
            "- Je vanuit een raadsbrief de opvolgende moties of afdoeningsvoorstellen wilt lokaliseren.\n"
            "\n"
            "Gebruik deze tool NIET wanneer:\n"
            "- Je het volledige inhoudsverloop over een onderwerp wilt zien — gebruik "
            "  `tijdlijn_besluitvorming`.\n"
            "- Je nog geen `document_id` hebt — gebruik eerst een zoektool.\n"
            "- Je de volledige tekst van het brondocument wilt — gebruik `lees_fragment`.\n"
            "\n"
            "Retourneert: markdown-tabel met tot 30 gerelateerde documenten (datum, relatie-type, "
            "naam, uitkomst-tag waar van toepassing, document_id)."
        ),
        input_schema={
            "type": "object",
            "required": ["document_id"],
            "properties": {
                "document_id": {"type": "string"},
                "max_resultaten": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 30,
                    "default": 10,
                },
            },
        },
        output_schema=_MARKDOWN_OUTPUT,
        examples=[
            ToolExample(
                description="Positief: vanuit een motie opvolgende afdoeningsstukken vinden.",
                input={"document_id": "motie_warmtebedrijf_2023", "max_resultaten": 10},
                expected_output_shape="Markdown-tabel met stukken uit dezelfde vergadering en afdoeningsvoorstellen.",
            ),
            ToolExample(
                description="Negatief: zonder document_id.",
                input={"document_id": "onbekend"},
                expected_output_shape="Tool retourneert 'niet gevonden'; gebruik eerst een zoektool om een geldige id te krijgen.",
            ),
        ],
    ),
    # ---------------------------------------------------------------------
    # 13. zoek_uitspraken_op_rol
    # ---------------------------------------------------------------------
    "zoek_uitspraken_op_rol": ToolSpec(
        name="zoek_uitspraken_op_rol",
        summary="Rol-bewuste utterance-zoekopdracht die de juiste datumperiode afleidt uit raadslid_rollen.",
        ai_description=(
            "[PERSON-ROLE-TIMELINE] Database-lookup in `raadslid_rollen` bepaalt automatisch de "
            "start- en einddatum van een persoon's rol (raadslid, wethouder, commissielid). "
            "Vereist een persoonsnaam als primaire input — NIET een fractie of onderwerp alleen. "
            "Verwerkt naamvarianten ('D.P.A. Tak', 'Dennis Tak', 'Tak') als equivalent. "
            "Toont de volledige rolhistorie als context in de response-header.\n"
            "\n"
            "Gebruik deze tool wanneer:\n"
            "- Iemand van functie is gewisseld en je alleen uitspraken uit één ambtstermijn wilt.\n"
            "- Je de CV/rolhistorie van een raadslid of bestuurder wilt opvragen.\n"
            "\n"
            "Gebruik deze tool NIET wanneer:\n"
            "- De vraag gaat over een fractie (VVD, SP...) — gebruik `zoek_uitspraken` met "
            "  `partij_of_raadslid`.\n"
            "- Je programma/profiel van een partij wilt — gebruik `haal_partijstandpunt_op`.\n"
            "- Je moties van een indiener zoekt — gebruik `zoek_moties` met `indiener`.\n"
            "\n"
            "Retourneert: markdown met rolperiode-tabel, ambtsduur-filter, en tot 20 "
            "hergerankte fragmenten met bronvermelding."
        ),
        input_schema={
            "type": "object",
            "required": ["naam", "onderwerp"],
            "properties": {
                "naam": {"type": "string"},
                "onderwerp": {"type": "string"},
                "rol": {
                    "type": ["string", "null"],
                    "description": "Specifieke rol: 'raadslid', 'wethouder', 'commissielid'.",
                },
                "datum_van": {
                    "type": ["string", "null"],
                    "description": "Overschrijft de automatisch afgeleide startdatum.",
                },
                "datum_tot": {
                    "type": ["string", "null"],
                    "description": "Overschrijft de automatisch afgeleide einddatum.",
                },
                "max_resultaten": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 10,
                },
            },
        },
        output_schema=_MARKDOWN_OUTPUT,
        examples=[
            ToolExample(
                description="Positief: uitspraken van Buijt als wethouder over klimaat, periode wordt automatisch afgeleid.",
                input={"naam": "Buijt", "onderwerp": "klimaat", "rol": "wethouder"},
                expected_output_shape="Markdown met rolhistorie, periode-filter en uitspraken binnen Buijt's wethouderschap.",
            ),
            ToolExample(
                description="Negatief: geen rolwisseling relevant — gebruik `zoek_uitspraken`.",
                input={"naam": "Pastors", "onderwerp": "veiligheid"},
                expected_output_shape="Werkt, maar voor personen zonder rolwisseling is `zoek_uitspraken` directer.",
            ),
        ],
    ),

    # ---------------------------------------------------------------------
    # 14. vat_document_samen (WS6, live 2026-04-16 — backfill complete,
    # 28K+ verified summaries in DB; mode='short' serves from cache <50ms)
    # ---------------------------------------------------------------------
    "vat_document_samen": ToolSpec(
        name="vat_document_samen",
        summary="Source-spans-only summarization van een specifiek document met provenance-verification.",
        ai_description=(
            "Genereert een samenvatting van één document. Alle zinnen in de samenvatting "
            "worden geverifieerd tegen de bron-spans — ongeverifiërbare zinnen krijgen een "
            "`⚠️ partial` markering. Werkt in twee modes: `short` (key points) en `long` "
            "(paragraaf-samenvatting met citaten).\n"
            "\n"
            "Gebruik deze tool wanneer:\n"
            "- Je één specifiek document wilt samenvatten en betrouwbare citaten nodig hebt.\n"
            "- De vraag expliciet om een samenvatting vraagt — niet om losse fragmenten.\n"
            "\n"
            "Gebruik deze tool NIET wanneer:\n"
            "- Je meerdere documenten tegelijk wilt samenvatten — gebruik `scan_breed` + eigen synthese.\n"
            "- Je ruwe fragmenten nodig hebt — gebruik `lees_fragment`.\n"
            "\n"
            "Retourneert: JSON met `summary`, `mode`, `verified_sentences`, `unverified_sentences`, "
            "en `citations`."
        ),
        input_schema={
            "type": "object",
            "required": ["document_id"],
            "properties": {
                "document_id": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["short", "long"],
                    "default": "short",
                },
            },
        },
        output_schema={"type": "string", "description": "JSON-encoded summary payload"},
        stability="experimental",
        added_in_version="0.2.0",
        examples=[
            ToolExample(
                description="Positief: korte samenvatting van een motie.",
                input={"document_id": "6115020", "mode": "short"},
                expected_output_shape="JSON met 3-5 key points, alle geverifieerd tegen bronspans.",
            ),
            ToolExample(
                description="Negatief: topic-overzicht — gebruik `scan_breed` eerst.",
                input={"document_id": "x", "mode": "long"},
                expected_output_shape="Werkt, maar niet geschikt voor onderzoeksvragen waar je de juiste doc_id nog niet kent.",
            ),
        ],
    ),

    # ---------------------------------------------------------------------
    # 15. get_neodemos_context — context primer (WS4 2026-04-11)
    # ---------------------------------------------------------------------
    "get_neodemos_context": ToolSpec(
        name="get_neodemos_context",
        summary="Context primer: gemeenten, document-types, huidige wethouders, coalition-history, aanbevolen tool-sequences. Roep dit FIRST aan.",
        ai_description=(
            "Retourneert een structured primer met de beschikbare gemeenten, document-types, "
            "de huidige zittende wethouders van Rotterdam (uit `raadslid_rollen`), de coalition-history "
            "per college-periode, known limitations, en recommended tool sequences voor veelvoorkomende "
            "vraagpatronen. Cheap to call (<50ms). Cached op server-niveau als de DB bereikbaar is.\n"
            "\n"
            "Gebruik deze tool wanneer:\n"
            "- Je een nieuwe sessie start — dit geeft je de ground-truth voor rol/tenure/coalition in plaats "
            "  van dat je uit trainingsdata moet gokken.\n"
            "- De vraag gaat over een historische stemming — check `coalition_history` voor de compositie "
            "  op dát moment (GroenLinks/PvdA waren in 2018 coalitiepartij, niet oppositie).\n"
            "- Je niet zeker weet welke tool sequence past — zie `recommended_tool_sequences`.\n"
            "\n"
            "Gebruik deze tool NIET wanneer:\n"
            "- De vraag al duidelijk in één retrieval tool past en je de context al kent.\n"
            "- Je al in dezelfde sessie de context hebt opgehaald (het verandert niet binnen één sessie).\n"
            "\n"
            "Retourneert: markdown met secties voor gemeenten, document-types, raadssamenstelling, "
            "college-history, limitations, en recommended tool-sequences."
        ),
        input_schema={
            "type": "object",
            "required": [],
            "properties": {},
        },
        output_schema=_MARKDOWN_OUTPUT,
        added_in_version="0.2.0",
        examples=[
            ToolExample(
                description="Positief: eerste call in een nieuwe sessie.",
                input={},
                expected_output_shape="Markdown met alle primer-secties + huidige wethouders uit raadslid_rollen.",
            ),
            ToolExample(
                description="Negatief: al eerder opgehaald in dezelfde sessie — primer is stabiel.",
                input={},
                expected_output_shape="Zelfde output; geen reden om te herhalen.",
            ),
        ],
    ),
    # -----------------------------------------------------------------
    # 16. vraag_begrotingsregel  (WS2 — Trustworthy Financial Analysis)
    # -----------------------------------------------------------------
    "vraag_begrotingsregel": ToolSpec(
        name="vraag_begrotingsregel",
        summary="Haalt exacte begrotingsregels op uit de gestructureerde financial_lines tabel.",
        ai_description=(
            "Haalt exacte begrotingsregels op uit de gestructureerde financial_lines tabel.\n\n"
            "Use this when:\n"
            "- De gebruiker vraagt naar een specifiek bedrag, begrotingsregel, of financieel gegeven\n"
            "- De vraag bevat een programma, jaar, en/of gemeente\n\n"
            "Do NOT use when:\n"
            "- De vraag is narratief/kwalitatief ('waarom is het budget gestegen?') → gebruik zoek_financieel\n"
            "- De gebruiker vraagt om een toelichting of context → gebruik zoek_financieel\n\n"
            "Returns: Exacte bedragen met SHA256 verificatietokens. "
            "Bedragen zijn byte-identiek aan de bron-PDF."
        ),
        stability="experimental",
        added_in_version="0.2.0",
        input_schema={
            "type": "object",
            "properties": {
                "gemeente": {"type": "string"},
                "jaar": {"type": "integer"},
                "programma": {"type": "string"},
                "sub_programma": {"type": "string"},
                "include_gr_derived": {"type": "boolean", "default": False},
            },
            "required": ["gemeente", "jaar", "programma"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "matches": {"type": "array"},
                "total": {"type": "integer"},
            },
        },
        examples=[
            ToolExample(
                description="Exacte lasten voor programma Veilig, 2026",
                input={"gemeente": "rotterdam", "jaar": 2026, "programma": "Veilig"},
                expected_output_shape='{"matches": [{"programma": "Veilig", "bedrag_eur": "82400000.00", ...}], "total": 1}',
            ),
            ToolExample(
                description="GRJR-derived jeugdhulp share",
                input={"gemeente": "rotterdam", "jaar": 2023, "programma": "jeugdhulp", "include_gr_derived": True},
                expected_output_shape='{"matches": [...scope=gemeente..., ...scope=derived_share...], "total": 2}',
            ),
        ],
    ),
    # -----------------------------------------------------------------
    # 17. vergelijk_begrotingsjaren  (WS2 — Trustworthy Financial Analysis)
    # -----------------------------------------------------------------
    "vergelijk_begrotingsjaren": ToolSpec(
        name="vergelijk_begrotingsjaren",
        summary="Vergelijkt begrotingsregels over meerdere jaren voor een programma.",
        ai_description=(
            "Vergelijkt begrotingsregels over meerdere jaren voor een programma.\n\n"
            "Use this when:\n"
            "- De gebruiker vraagt naar trends, ontwikkeling, of vergelijking over jaren\n"
            "- 'Hoe is het budget veranderd?', 'Wat is de trend?'\n\n"
            "Do NOT use when:\n"
            "- De vraag gaat over een enkel jaar → gebruik vraag_begrotingsregel\n\n"
            "Returns: Tijdreeks met delta_abs en delta_pct, "
            "geaggregeerd op IV3 taakveld voor consistentie."
        ),
        stability="experimental",
        added_in_version="0.2.0",
        input_schema={
            "type": "object",
            "properties": {
                "gemeente": {"type": "string"},
                "programma": {"type": "string"},
                "jaren": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["gemeente", "programma", "jaren"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "programma": {"type": "string"},
                "iv3_taakveld": {"type": "string"},
                "series": {"type": "array"},
                "source_documents": {"type": "array"},
            },
        },
        examples=[
            ToolExample(
                description="Vergelijk Veilig-budget 2024-2026",
                input={"gemeente": "rotterdam", "programma": "Veilig", "jaren": [2024, 2025, 2026]},
                expected_output_shape='{"programma": "Veilig", "iv3_taakveld": "1.2", "series": [{...}], ...}',
            ),
            ToolExample(
                description="Negatief: enkel jaar → gebruik vraag_begrotingsregel",
                input={"gemeente": "rotterdam", "programma": "Onderwijs", "jaren": [2025]},
                expected_output_shape='{"series": [{...}]} — maar vraag_begrotingsregel is beter voor enkel jaar',
            ),
        ],
    ),
    "traceer_motie": ToolSpec(
        name="traceer_motie",
        summary="GraphRAG: reconstrueer de volledige traceerbaarheid van een motie (indieners → stemgedrag → uitkomst → notulen).",
        ai_description=(
            "Loopt de kennisgraaf van een specifieke motie/amendement door: van indieners (DIENT_IN) "
            "naar partijen (LID_VAN) naar stemgedrag (STEMT_VOOR/STEMT_TEGEN) naar uitkomst "
            "(AANGENOMEN/VERWORPEN) naar gekoppelde notulen-fragmenten.\n\n"
            "Gebruik deze tool wanneer:\n"
            "- Je een specifiek motie document_id hebt (verkregen via zoek_moties of scan_breed) en "
            "  de volledige trace wilt: wie diende in, hoe stemde elke partij, wat was de uitkomst, "
            "  welke debatten hingen ermee samen.\n"
            "- De gebruiker vraagt 'volg deze motie', 'traceer het besluitvormingstraject', of "
            "  'wat is er gebeurd met motie X'.\n\n"
            "Gebruik deze tool NIET wanneer:\n"
            "- Je ZOEKT naar moties op onderwerp — gebruik zoek_moties voor topic-search.\n"
            "- Je een tijdlijn over meerdere moties wilt — gebruik tijdlijn_besluitvorming.\n\n"
            "Retourneert: JSON string met motie-header, indieners, stemgedrag, gerelateerde documenten, "
            "en notulen-fragmenten. trace_available=False wanneer WS1 GraphRAG-edges nog niet zijn "
            "ingested — het veld degradeert graceful naar regel-gebaseerde indieners+steminformatie."
        ),
        stability="experimental",
        added_in_version="0.2.0",
        input_schema={
            "type": "object",
            "required": ["motie_id"],
            "properties": {
                "motie_id": {"type": "string", "description": "document_id van de motie/amendement."},
                "include_notulen": {"type": "boolean", "default": True},
                "max_notulen_chunks": {"type": "integer", "default": 8, "maximum": 20},
            },
        },
        output_schema={"type": "string", "description": "JSON string"},
        examples=[
            ToolExample(
                description="Positief: trace een specifieke motie met bekende ID.",
                input={"motie_id": "12345678", "include_notulen": True},
                expected_output_shape='{"motie": {...}, "indieners": [...], "vote": {...}, "notulen_fragments": [...]}',
            ),
            ToolExample(
                description="Negatief: zoeken op onderwerp → gebruik zoek_moties.",
                input={"motie_id": "leegstand"},
                expected_output_shape="Gebruik zoek_moties('leegstand') om document_ids te vinden.",
            ),
        ],
    ),
    "vergelijk_partijen": ToolSpec(
        name="vergelijk_partijen",
        summary="Vergelijk twee of meer partijen naast elkaar op één onderwerp via vector+BM25 retrieval.",
        ai_description=(
            "Retrievet voor elk van de opgegeven partijen de top-N fragmenten over een onderwerp "
            "via hybrid search (BM25+vector+Jina reranker) en geeft ze naast elkaar terug. "
            "Wanneer WS1 GraphRAG live is, wordt de zoekruimte verrijkt via LID_VAN ∩ SPREEKT_OVER.\n\n"
            "Gebruik deze tool wanneer:\n"
            "- De gebruiker EXPLICIET twee of meer partijen vraagt te vergelijken op één onderwerp, "
            "  bijv. 'hoe denken VVD, PvdA en GroenLinks over warmtenetten?'\n"
            "- Je een side-by-side overzicht wilt van partijstandpunten.\n\n"
            "Gebruik deze tool NIET wanneer:\n"
            "- Je één partij wilt opzoeken → gebruik haal_partijstandpunt_op of zoek_uitspraken.\n"
            "- Je stemmingen en uitkomsten wilt vergelijken → gebruik zoek_moties met partij-filter.\n"
            "- Je de rol-tijdlijn van één persoon nodig hebt → gebruik zoek_uitspraken_op_rol.\n\n"
            "Retourneert: JSON string met per-partij fragmentenlijsten (chunk_id, title, content, "
            "date, similarity_score, document_id) en graph_walk_used boolean."
        ),
        stability="experimental",
        added_in_version="0.2.0",
        input_schema={
            "type": "object",
            "required": ["onderwerp", "partijen"],
            "properties": {
                "onderwerp": {"type": "string", "description": "Concreet onderwerp, geen hele zinnen."},
                "partijen": {"type": "array", "items": {"type": "string"}, "minItems": 2, "description": "Minimaal 2 partijnamen."},
                "datum_van": {"type": ["string", "null"]},
                "datum_tot": {"type": ["string", "null"]},
                "max_fragmenten_per_partij": {"type": "integer", "default": 5, "maximum": 10},
            },
        },
        output_schema={"type": "string", "description": "JSON string"},
        examples=[
            ToolExample(
                description="Positief: vergelijk 3 partijen op warmtenetten.",
                input={"onderwerp": "warmtenetten", "partijen": ["VVD", "PvdA", "GroenLinks"]},
                expected_output_shape='{"onderwerp": "warmtenetten", "partijen": [{"partij": "VVD", "fragmenten": [...]}, ...]}',
            ),
            ToolExample(
                description="Negatief: één partij → gebruik haal_partijstandpunt_op.",
                input={"onderwerp": "veiligheid", "partijen": ["SP"]},
                expected_output_shape="Minimaal 2 partijen vereist — gebruik haal_partijstandpunt_op voor één partij.",
            ),
        ],
    ),
    "lees_fragmenten_batch": ToolSpec(
        name="lees_fragmenten_batch",
        summary="Lees eerste fragmenten van meerdere documenten in één call (latency-optimalisatie).",
        ai_description=(
            "Fetcht de eerste N fragmenten van elk van de opgegeven document_ids in één tool-call. "
            "Vervangt meerdere sequentiële lees_fragment calls die overview queries 15–25s lang maakten.\n\n"
            "Gebruik deze tool wanneer:\n"
            "- Je na een zoek_moties of scan_breed 3–10 documenten wilt inkijken.\n"
            "- Snelheid belangrijker is dan in-document reranking.\n\n"
            "Gebruik deze tool NIET wanneer:\n"
            "- Je één specifiek document wilt lezen met query-reranking → gebruik lees_fragment met query=...\n"
            "- Je meer dan 10 documenten tegelijk wilt ophalen — trim de lijst eerst.\n\n"
            "Retourneert: markdown met per document een header, datum, bronlink, en de eerste "
            "max_fragmenten_per_doc tekstfragmenten."
        ),
        added_in_version="0.2.0",
        input_schema={
            "type": "object",
            "required": ["document_ids"],
            "properties": {
                "document_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
                "max_fragmenten_per_doc": {"type": "integer", "default": 3, "maximum": 5},
            },
        },
        output_schema=_MARKDOWN_OUTPUT,
        examples=[
            ToolExample(
                description="Positief: snel 4 moties doornemen na zoek_moties.",
                input={"document_ids": ["12345", "67890", "11111", "22222"], "max_fragmenten_per_doc": 2},
                expected_output_shape="Markdown met 4 secties, elk met doc-naam, datum, bronlink, en 2 fragmenten.",
            ),
            ToolExample(
                description="Negatief: in-document reranking nodig → gebruik lees_fragment met query.",
                input={"document_ids": ["12345"], "max_fragmenten_per_doc": 5},
                expected_output_shape="Gebruik lees_fragment(document_id='12345', query='je_onderwerp') voor betere reranking.",
            ),
        ],
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_spec(name: str) -> Optional[ToolSpec]:
    """Return the ToolSpec for a tool name, or None if not registered."""
    return REGISTRY.get(name)


def all_public_tools() -> list[str]:
    """Names of all tools eligible for the /public/mcp endpoint."""
    return [name for name, spec in REGISTRY.items() if spec.public]


def registry_size() -> int:
    return len(REGISTRY)
