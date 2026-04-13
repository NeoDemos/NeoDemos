"""
Anthropic Tool-Use Schemas for NeoDemos MCP Tools
--------------------------------------------------
Auto-generated from services/mcp_tool_registry.py (single source of truth).

These schemas are passed to the Anthropic Messages API via the `tools` parameter.
The ai_description from the registry is used as the tool description — it follows
FactSet discipline (positive + negative use cases for each tool).

WS9 — Web Intelligence: MCP-as-Backend via Sonnet + Tool Use
"""

import copy
import logging
from services.mcp_tool_registry import REGISTRY, ToolSpec

logger = logging.getLogger(__name__)

# Tools to expose to Sonnet (all retrieval tools, excluding get_neodemos_context
# which is injected into the system prompt instead of being a callable tool).
_EXPOSED_TOOLS = [
    "zoek_raadshistorie",
    "zoek_financieel",
    "zoek_uitspraken",
    "haal_vergadering_op",
    "lijst_vergaderingen",
    "tijdlijn_besluitvorming",
    "analyseer_agendapunt",
    "haal_partijstandpunt_op",
    "zoek_moties",
    "scan_breed",
    "lees_fragment",
    "vat_document_samen",
    "zoek_gerelateerd",
    "zoek_uitspraken_op_rol",
    "traceer_motie",
    "vergelijk_partijen",
    "vraag_begrotingsregel",
    "vergelijk_begrotingsjaren",
]


def _clean_schema_for_anthropic(schema: dict) -> dict:
    """
    Adapt registry input_schema to Anthropic's tool_use format.

    Anthropic's tool_use expects standard JSON Schema. The registry uses
    ["string", "null"] for optional nullable fields — Anthropic supports this
    but we also need to ensure 'required' is present and defaults are handled.
    """
    cleaned = copy.deepcopy(schema)

    # Ensure required field exists
    if "required" not in cleaned:
        cleaned["required"] = []

    # Remove party defaults — party should be neutral unless user is logged in
    # and has selected a party. Sonnet should only fill partij from context.
    props = cleaned.get("properties", {})
    for key in ("partij",):
        if key in props and "default" in props[key]:
            del props[key]["default"]

    return cleaned


def _spec_to_anthropic_tool(name: str, spec: ToolSpec) -> dict:
    """Convert a ToolSpec to Anthropic tool_use format."""
    return {
        "name": name,
        "description": spec.ai_description,
        "input_schema": _clean_schema_for_anthropic(spec.input_schema),
    }


def _build_unregistered_tool(name: str, description: str, schema: dict) -> dict:
    """Build an Anthropic tool definition for tools not yet in the registry."""
    return {
        "name": name,
        "description": description,
        "input_schema": _clean_schema_for_anthropic(schema),
    }


# ---------------------------------------------------------------------------
# Build the tool list
# ---------------------------------------------------------------------------

NEODEMOS_TOOLS: list[dict] = []

# Tools from the registry
for _tool_name in _EXPOSED_TOOLS:
    _spec = REGISTRY.get(_tool_name)
    if _spec is not None:
        NEODEMOS_TOOLS.append(_spec_to_anthropic_tool(_tool_name, _spec))
    else:
        # Tools not yet in registry — define inline with docstring descriptions
        if _tool_name == "traceer_motie":
            NEODEMOS_TOOLS.append(_build_unregistered_tool(
                "traceer_motie",
                (
                    "Reconstruct the complete traceability of a single motie/amendement: "
                    "indieners → partijen → stemgedrag → uitkomst → gekoppelde notulen-fragmenten. "
                    "Requires a motie document_id from zoek_moties or scan_breed.\n\n"
                    "Gebruik deze tool wanneer:\n"
                    "- De gebruiker vraagt om een motie te 'traceren', 'volgen' of 'reconstrueren'.\n"
                    "- Je al een motie document_id hebt en het volledige besluitvormingstraject wilt zien.\n\n"
                    "Gebruik deze tool NIET wanneer:\n"
                    "- Je moties wilt ZOEKEN op onderwerp — gebruik zoek_moties.\n"
                    "- Je alleen de motietekst wilt lezen — gebruik lees_fragment."
                ),
                {
                    "type": "object",
                    "required": ["motie_id"],
                    "properties": {
                        "motie_id": {
                            "type": "string",
                            "description": "Document ID van de motie/amendement (uit zoek_moties of scan_breed).",
                        },
                        "include_notulen": {
                            "type": "boolean",
                            "description": "Of gerelateerde notulen-fragmenten meegenomen moeten worden.",
                            "default": True,
                        },
                        "max_notulen_chunks": {
                            "type": "integer",
                            "description": "Max aantal notulen-fragmenten.",
                            "default": 8,
                        },
                    },
                },
            ))
        elif _tool_name == "vergelijk_partijen":
            NEODEMOS_TOOLS.append(_build_unregistered_tool(
                "vergelijk_partijen",
                (
                    "Plaats twee of meer partijen naast elkaar op één onderwerp en retourneer "
                    "hun standpunten als gerankte fragmentenlijsten. Elk partij-blok bevat "
                    "partij-gefilterde RAG-fragmenten.\n\n"
                    "Gebruik deze tool wanneer:\n"
                    "- De gebruiker letterlijk vraagt partijen te vergelijken op een onderwerp.\n"
                    "- Je twee of meer partijen naast elkaar wilt zetten.\n\n"
                    "Gebruik deze tool NIET wanneer:\n"
                    "- De vraag over één partij gaat — gebruik haal_partijstandpunt_op.\n"
                    "- Je alleen citaten van één partij zoekt — gebruik zoek_uitspraken."
                ),
                {
                    "type": "object",
                    "required": ["onderwerp", "partijen"],
                    "properties": {
                        "onderwerp": {"type": "string"},
                        "partijen": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Lijst van partijnamen, bijv. ['VVD', 'PvdA', 'GroenLinks-PvdA'].",
                        },
                        "datum_van": {"type": ["string", "null"]},
                        "datum_tot": {"type": ["string", "null"]},
                        "max_fragmenten_per_partij": {
                            "type": "integer",
                            "default": 5,
                        },
                    },
                },
            ))
        else:
            logger.warning(f"Tool '{_tool_name}' not in registry and no inline definition — skipped")


def tool_count() -> int:
    """Number of tools exposed to Sonnet."""
    return len(NEODEMOS_TOOLS)
