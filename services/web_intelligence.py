"""
Web Intelligence Service — MCP-as-Backend via Sonnet + Tool Use
---------------------------------------------------------------
Gives Claude Sonnet access to the same 18 MCP tools that Claude Desktop
and ChatGPT use, via the Anthropic API's tool_use feature. This produces
MCP-quality output from the web frontend.

Architecture:
  User → Web Frontend → FastAPI /api/search/stream
       → Anthropic Messages API (Sonnet + tools=[...18 tool schemas...])
       → Sonnet calls tools → FastAPI executes tool functions locally
       → Returns results to Sonnet → Sonnet synthesizes
       → SSE stream → Web Frontend → User

Fallback chain:
  1. Sonnet + MCP tools (primary)
  2. Gemini Flash (if ANTHROPIC_API_KEY missing or API error)
  3. Error SSE event (keyword results remain visible)

WS9 — Web Search Intelligence
"""

import asyncio
import logging
import os
import time
from datetime import date
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt — Dutch, civic intelligence, source-grounded
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Je bent NeoDemos, een civic intelligence platform voor de Rotterdamse gemeenteraad. Je hebt toegang tot 90.000+ officiële raadsdocumenten (2002–heden) via gespecialiseerde zoektools.

{context_primer}

GEDRAG:
- Gebruik ALTIJD minimaal één zoektool voordat je antwoordt — ook bij vragen die mogelijk buiten scope lijken. Zoek eerst, oordeel daarna.
- Verzin NOOIT informatie. Alle beweringen moeten uit opgehaalde documenten komen.
- Elke feitelijke bewering MOET een bronvermelding [n] hebben die verwijst naar een specifiek document.
- Vertaal temporele termen ("vorig jaar", "sinds 2023") naar datum_van/datum_tot parameters. Vandaag is {today}.
- Bij vragen over specifieke partijen: gebruik de partij-parameter.
- Bij complexe vragen: gebruik meerdere tools in volgorde.
- Antwoord altijd in het Nederlands.
- Onderscheid raadsleden van insprekers/burgers.
- Gebruik exacte bedragen en datums uit de bronnen.
- Herhaal nooit dezelfde zin of alinea in je antwoord.

FORMAAT:
- Eenvoudige vragen: 2-5 alinea's met bronverwijzingen [n].
- Vergelijkingen: gebruik een markdown tabel.
- Tijdlijnen: chronologische opsomming.
- Eindig altijd met een genummerde bronnenlijst met document-titels en datums.
- Als je geen relevante resultaten vindt, zeg dat eerlijk — geen speculatie.

TOOL-STRATEGIE:
- Begin met de meest specifieke tool voor de vraag.
- zoek_raadshistorie is de standaard als geen andere tool specifieker past.
- Voor financiële vragen: begin met zoek_financieel.
- Voor moties/stemmingen: begin met zoek_moties.
- Voor partijvergelijking: gebruik vergelijk_partijen.
- Gebruik lees_fragment om documentinhoud te verdiepen na een eerste zoektocht.
- Maximaal 3-4 tool calls per vraag — wees efficiënt."""

GEMINI_FALLBACK_PROMPT = """Je bent NeoDemos, een civic intelligence platform voor de Rotterdamse gemeenteraad (90.000+ officiële raadsdocumenten, 2002–heden). Beantwoord de vraag op basis van je algemene kennis over de Rotterdamse politiek. Vermeld expliciet dat dit een beperkt antwoord is zonder directe brondocumenten. Vandaag is {today}. Antwoord in het Nederlands."""


# ---------------------------------------------------------------------------
# Tool status display names (Dutch, for SSE status messages)
# ---------------------------------------------------------------------------

TOOL_DISPLAY_NAMES = {
    "zoek_raadshistorie": "Raadsinformatie doorzoeken",
    "zoek_financieel": "Financiële documenten zoeken",
    "zoek_uitspraken": "Uitspraken doorzoeken",
    "haal_vergadering_op": "Vergadering ophalen",
    "lijst_vergaderingen": "Vergaderingen oplijsten",
    "tijdlijn_besluitvorming": "Tijdlijn opbouwen",
    "analyseer_agendapunt": "Agendapunt analyseren",
    "haal_partijstandpunt_op": "Partijstandpunt ophalen",
    "zoek_moties": "Moties doorzoeken",
    "scan_breed": "Breed zoeken",
    "lees_fragment": "Document lezen",
    "vat_document_samen": "Document samenvatten",
    "zoek_gerelateerd": "Gerelateerde documenten zoeken",
    "zoek_uitspraken_op_rol": "Uitspraken op rol zoeken",
    "traceer_motie": "Motie traceren",
    "vergelijk_partijen": "Partijen vergelijken",
    "vraag_begrotingsregel": "Begrotingsregel opvragen",
    "vergelijk_begrotingsjaren": "Begrotingsjaren vergelijken",
}


class WebIntelligenceService:
    """Sonnet + tool_use loop for web search — MCP-quality from the web frontend."""

    def __init__(self, ai_service=None):
        self.client = None
        self.available = False
        self.model = os.getenv("WS9_MODEL", "claude-sonnet-4-6")
        self.max_tool_rounds = 5
        self.max_tool_result_chars = 8000  # truncate large tool outputs
        self.ai_service = ai_service  # optional Gemini fallback

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning("ANTHROPIC_API_KEY not set — WebIntelligenceService unavailable")
            return

        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.available = True

        # Cache the context primer (stable within a server lifetime)
        self._context_primer: Optional[str] = None

    def _get_context_primer(self) -> str:
        """Get the NeoDemos context primer (cached). Runs synchronously."""
        if self._context_primer is None:
            try:
                from services.mcp_tools_internal import get_neodemos_context
                self._context_primer = get_neodemos_context()
            except Exception as e:
                logger.warning(f"Failed to load context primer: {e}")
                self._context_primer = ""
        return self._context_primer

    def _build_system_blocks(self, partij: Optional[str] = None) -> list:
        """
        Build system prompt as a list of content blocks with prompt caching.

        The stable base (SYSTEM_PROMPT + context_primer + today) is marked
        with cache_control so Anthropic caches it across requests on the same day.
        The partij block (per-user, dynamic) is appended uncached.
        """
        base_text = SYSTEM_PROMPT.format(
            today=date.today().isoformat(),
            context_primer=self._get_context_primer(),
        )
        blocks = [
            {
                "type": "text",
                "text": base_text,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        if partij:
            blocks.append({
                "type": "text",
                "text": f"De gebruiker is lid van {partij}. Gebruik dit als standaard partij-context waar relevant.",
            })
        return blocks

    def _get_tools(self) -> list[dict]:
        """Get the Anthropic tool schemas."""
        from services.tool_schemas import NEODEMOS_TOOLS
        return NEODEMOS_TOOLS

    def _get_tools_with_cache(self) -> list[dict]:
        """
        Return tool schemas with cache_control on the last tool.

        Marking the last tool caches the entire tool list, which is static
        and typically 2000+ tokens — well above the 1024-token caching minimum.
        """
        tools = self._get_tools()
        if not tools:
            return tools
        # Shallow copy so we don't mutate the shared list
        return [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]

    async def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool and return its string result, truncated if needed."""
        from services.mcp_tools_internal import TOOL_DISPATCH

        tool_fn = TOOL_DISPATCH.get(tool_name)
        if tool_fn is None:
            return f"Onbekende tool: {tool_name}"

        try:
            result = await tool_fn(**tool_input)
            # Truncate oversized results to stay within context budget
            if len(result) > self.max_tool_result_chars:
                result = result[:self.max_tool_result_chars] + "\n\n[... resultaat ingekort ...]"
            return result
        except Exception as e:
            logger.error(f"Tool {tool_name} execution failed: {e}", exc_info=True)
            return f"Fout bij uitvoeren van {tool_name}: {str(e)}"

    async def _gemini_fallback_stream(self, user_query: str) -> AsyncIterator[dict]:
        """
        Fallback stream using Gemini Flash when Anthropic is unavailable.
        No MCP tools — uses Gemini's general knowledge only.
        Explicitly tells the user this is a limited fallback answer.
        """
        gemini_available = (
            self.ai_service is not None
            and getattr(self.ai_service, "use_llm", False)
            and getattr(self.ai_service, "client", None) is not None
        )
        if not gemini_available:
            yield {"type": "error", "message": "AI-zoekservice tijdelijk niet beschikbaar. De zoekresultaten hieronder zijn nog beschikbaar."}
            return

        yield {"type": "status", "message": "Analyseren (fallback)..."}

        try:
            prompt = (
                GEMINI_FALLBACK_PROMPT.format(today=date.today().isoformat())
                + f"\n\nVraag: {user_query}"
            )

            def _call():
                return self.ai_service.client.models.generate_content(
                    model=self.ai_service.model_name,
                    contents=prompt,
                )

            t0 = time.monotonic()
            response = await asyncio.to_thread(_call)
            answer = response.text or ""
            latency_ms = int((time.monotonic() - t0) * 1000)

            if not answer:
                yield {"type": "error", "message": "Geen antwoord van fallback AI."}
                return

            chunk_size = 80
            for i in range(0, len(answer), chunk_size):
                yield {"type": "chunk", "text": answer[i:i + chunk_size]}

            logger.info(f"WS9 Gemini fallback complete: {latency_ms}ms")
            yield {
                "type": "done",
                "tools_called": [],
                "rounds": 0,
                "latency_ms": latency_ms,
                "cost_usd": 0.0,
                "fallback": "gemini",
            }

        except Exception as e:
            logger.error(f"Gemini fallback failed: {e}", exc_info=True)
            yield {"type": "error", "message": "AI-zoekservice tijdelijk niet beschikbaar. De zoekresultaten hieronder zijn nog beschikbaar."}

    async def query(self, user_query: str, partij: Optional[str] = None) -> dict:
        """
        Non-streaming: run full tool loop, return final answer.

        Args:
            user_query: The user's search query in Dutch.
            partij: Optional party name from user session (injected into context).
        """
        if not self.available:
            return {"answer": None, "error": "AI-zoekservice niet beschikbaar"}

        messages = [{"role": "user", "content": user_query}]
        system_blocks = self._build_system_blocks(partij)
        tools = self._get_tools_with_cache()

        total_input_tokens = 0
        total_output_tokens = 0
        tools_called = []
        t0 = time.monotonic()

        for round_num in range(self.max_tool_rounds):
            try:
                response = await asyncio.to_thread(
                    self.client.messages.create,
                    model=self.model,
                    max_tokens=4096,
                    temperature=0,
                    system=system_blocks,
                    tools=tools,
                    messages=messages,
                )
            except Exception as e:
                logger.error(f"Anthropic API call failed: {e}", exc_info=True)
                return {"answer": None, "error": f"API-fout: {str(e)}"}

            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

            if response.stop_reason == "tool_use":
                assistant_content = response.content
                messages.append({"role": "assistant", "content": assistant_content})

                tool_results = []
                for block in assistant_content:
                    if block.type == "tool_use":
                        tools_called.append(block.name)
                        result = await self._execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                messages.append({"role": "user", "content": tool_results})

            elif response.stop_reason == "end_turn":
                answer = "".join(
                    block.text for block in response.content
                    if hasattr(block, "text")
                )
                latency_ms = int((time.monotonic() - t0) * 1000)
                cost_usd = (total_input_tokens * 3 + total_output_tokens * 15) / 1_000_000

                logger.info(
                    f"WS9 query complete: {len(tools_called)} tools, "
                    f"{round_num + 1} rounds, {latency_ms}ms, "
                    f"${cost_usd:.4f} ({total_input_tokens}in/{total_output_tokens}out)"
                )

                return {
                    "answer": answer,
                    "tools_called": tools_called,
                    "rounds": round_num + 1,
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                    "cost_usd": cost_usd,
                    "latency_ms": latency_ms,
                }
            else:
                # Unexpected stop reason
                break

        return {
            "answer": "De analyse is te complex geworden. Probeer een specifiekere vraag.",
            "tools_called": tools_called,
            "rounds": self.max_tool_rounds,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
        }

    async def stream(self, user_query: str, partij: Optional[str] = None) -> AsyncIterator[dict]:
        """
        Streaming: yield SSE events as Sonnet works.

        Event types:
          - {"type": "status", "message": "..."} — tool call status
          - {"type": "chunk", "text": "..."} — answer text chunk
          - {"type": "done", ...} — final metadata
          - {"type": "error", "message": "..."} — error

        Falls back to Gemini if Anthropic is unavailable or errors on first call.

        Args:
            user_query: The user's search query.
            partij: Optional party from user session.
        """
        if not self.available:
            async for event in self._gemini_fallback_stream(user_query):
                yield event
            return

        messages = [{"role": "user", "content": user_query}]
        system_blocks = self._build_system_blocks(partij)
        tools = self._get_tools_with_cache()

        tools_called = []
        total_input_tokens = 0
        total_output_tokens = 0
        t0 = time.monotonic()

        yield {"type": "status", "message": "Vraag analyseren..."}

        for round_num in range(self.max_tool_rounds):
            try:
                response = await asyncio.to_thread(
                    self.client.messages.create,
                    model=self.model,
                    max_tokens=4096,
                    temperature=0,
                    system=system_blocks,
                    tools=tools,
                    messages=messages,
                )
            except Exception as e:
                logger.error(f"Anthropic API call failed: {e}", exc_info=True)
                if round_num == 0:
                    # No answer sent yet — try Gemini fallback
                    logger.info("Attempting Gemini fallback after Anthropic error")
                    async for event in self._gemini_fallback_stream(user_query):
                        yield event
                else:
                    # Mid-conversation failure — can't cleanly fall back
                    yield {"type": "error", "message": f"Verbinding verloren na stap {round_num}. De zoekresultaten hieronder zijn nog beschikbaar."}
                return

            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

            if response.stop_reason == "tool_use":
                assistant_content = response.content
                messages.append({"role": "assistant", "content": assistant_content})

                tool_results = []
                for block in assistant_content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tools_called.append(tool_name)
                        display = TOOL_DISPLAY_NAMES.get(tool_name, tool_name)
                        yield {"type": "status", "message": f"{display}..."}

                        result = await self._execute_tool(tool_name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                messages.append({"role": "user", "content": tool_results})

                if round_num < self.max_tool_rounds - 1:
                    yield {"type": "status", "message": f"Analyseren (stap {round_num + 2})..."}

            elif response.stop_reason == "end_turn":
                yield {"type": "status", "message": "Antwoord genereren..."}

                answer = "".join(
                    block.text for block in response.content
                    if hasattr(block, "text")
                )

                if not answer.strip():
                    yield {"type": "error", "message": "Geen antwoord ontvangen. Probeer een specifiekere vraag."}
                    return

                # Yield answer in chunks for progressive rendering
                chunk_size = 80
                for i in range(0, len(answer), chunk_size):
                    yield {"type": "chunk", "text": answer[i:i + chunk_size]}

                latency_ms = int((time.monotonic() - t0) * 1000)
                cost_usd = (total_input_tokens * 3 + total_output_tokens * 15) / 1_000_000

                logger.info(
                    f"WS9 stream complete: {len(tools_called)} tools, "
                    f"{round_num + 1} rounds, {latency_ms}ms, "
                    f"${cost_usd:.4f}"
                )

                yield {
                    "type": "done",
                    "tools_called": tools_called,
                    "rounds": round_num + 1,
                    "latency_ms": latency_ms,
                    "cost_usd": cost_usd,
                }
                return
            else:
                break

        yield {
            "type": "done",
            "error": "Maximaal aantal stappen bereikt.",
            "tools_called": tools_called,
            "rounds": self.max_tool_rounds,
        }
