# WS9 — Web Search Intelligence: MCP-as-Backend via Sonnet + Tool Use

> **Status:** `in progress — local implementation done (2026-04-12), needs production deploy`
> **Owner:** `dennis + claude`
> **Priority:** 2 (critical for search quality; landing page works with current search as fallback)
> **Parallelizable:** yes (with WS8; converges at frontend search integration)

---

## TL;DR

Instead of building a custom QueryOrchestrator from scratch, **give Claude Sonnet access to the same MCP tools via the Anthropic API's `tool_use` feature**. This means the web frontend becomes an MCP client that speaks HTTP — Sonnet does the same adaptive tool selection, multi-turn orchestration, and synthesis it does in Claude Desktop. The result is MCP-quality output from the web frontend, with Sonnet's intelligence doing the scaffolding instead of custom Python code.

**Key insight:** The MCP path outperforms the web frontend not because of the model, but because Claude does adaptive tool selection. Rather than replicating that logic in Python, just _use_ Claude.

---

## Architecture: MCP-as-Backend

### How it works

```
Current MCP path (Claude Desktop / ChatGPT):
  User → AI Client → [stdio/SSE MCP transport] → MCP tools → AI synthesizes → User

New web path (Anthropic API + tool_use):
  User → Web Frontend → FastAPI endpoint
       → Anthropic Messages API (Sonnet + tools=[...13 tool schemas...])
       → Sonnet calls tools → FastAPI executes tool functions locally
       → Returns results to Sonnet → Sonnet synthesizes
       → SSE stream → Web Frontend → User
```

### Why this is better than a custom orchestrator

| Approach | Quality | Complexity | Maintenance |
|---|---|---|---|
| Custom QueryOrchestrator (original WS9) | ~80% of MCP | High (prompt tuning, routing logic, model selection) | Every new MCP tool needs Python routing code |
| **MCP-as-Backend (revised WS9)** | ~95% of MCP | Low (tool schemas + one API call loop) | New MCP tools automatically available |

### Cost model

At Sonnet territory (~$3/1M input, $15/1M output):
- Average query: ~2K input tokens (system prompt + query) + ~3K tool result tokens + ~1K output = **~$0.025/query**
- With tool loops (2-3 tool calls): ~$0.04/query
- At rate-limited usage (1 anonymous + 3 free + unlimited select):
  - ~200 queries/day realistic → **~$5/day → ~€150/month**

This is within the Sonnet budget Dennis approved.

---

## Dependencies

| Dependency | Type | Notes |
|---|---|---|
| `mcp_server_v3.py` | read | Extract tool function signatures and schemas |
| `ANTHROPIC_API_KEY` env var | new | Needed for Sonnet API calls |
| WS8 rate limiting (Phase 0B) | hard | Must rate-limit AI searches to control cost |
| `services/rag_service.py` | exists | Some tools call this internally |
| `services/storage.py` | exists | Meeting/agenda data queries |

---

## Cold-start prompt

```
You are picking up WS9_WEB_INTELLIGENCE for the NeoDemos project — a civic intelligence
platform for the Rotterdam municipal council (90,000+ documents, 2002-present).

Read these files first:
- docs/handoffs/WS9_WEB_INTELLIGENCE.md (this file — full spec)
- mcp_server_v3.py (all 13 MCP tool definitions — you'll extract these as Python functions)
- services/ai_service.py (current 3-stage Gemini pipeline — to be replaced)
- main.py (search endpoint at /api/search — wire the new approach here)

The key architecture: instead of building a custom query orchestrator, we give Claude Sonnet
access to the same 13 MCP tools via the Anthropic API's tool_use feature. Sonnet does the
adaptive tool selection and synthesis — just like it does in Claude Desktop via MCP.

This means:
1. Extract the 13 MCP tool functions into callable Python functions
2. Define their schemas as Anthropic tool_use JSON
3. Create a conversation loop: send query → Sonnet returns tool_use → execute tool → 
   send result back → Sonnet synthesizes (or calls another tool)
4. Stream the final answer via SSE to the frontend
```

---

## Files to read first

| File | Why |
|---|---|
| `mcp_server_v3.py` | All 13 tool definitions — extract functions and schemas |
| `services/ai_service.py` | Current pipeline to replace |
| `main.py` | `/api/search` endpoint to modify |
| `services/rag_service.py` | Retrieval functions called by MCP tools |
| `services/storage.py` | Database queries used by tools |

---

## The 13 MCP Tools (to expose via tool_use)

From `mcp_server_v3.py`:

| Tool | Purpose | Key params |
|---|---|---|
| `zoek_raadshistorie` | General hybrid search | `vraag`, `datum_van`, `datum_tot`, `partij`, `max_results` |
| `zoek_financieel` | Financial document search | `vraag`, `budget_year`, `max_results` |
| `zoek_uitspraken` | Search spoken statements | `vraag`, `partij`, `datum_van`, `datum_tot` |
| `haal_vergadering_op` | Get meeting by ID | `vergadering_id` |
| `lijst_vergaderingen` | List meetings by date range | `datum_van`, `datum_tot`, `commissie` |
| `tijdlijn_besluitvorming` | Decision timeline for a topic | `onderwerp`, `datum_van`, `datum_tot` |
| `analyseer_agendapunt` | Analyze agenda item | `agendapunt_id` |
| `haal_partijstandpunt_op` | Party stance on topic | `beleidsgebied`, `partij` |
| `zoek_moties` | Search motions/amendments | `onderwerp`, `partij`, `datum_van`, `datum_tot`, `status` |
| `scan_breed` | Broad topic scan | `vraag`, `max_results` |
| `lees_fragment` | Read specific chunk by ID | `fragment_id` |
| `zoek_gerelateerd` | Find related documents | `document_id`, `max_results` |
| `zoek_uitspraken_op_rol` | Search statements by role | `vraag`, `rol`, `partij` |

---

## Build Tasks

### Phase 1 — Extract MCP tools as callable functions (Day 1)

**Goal:** Make MCP tool logic callable from Python without the MCP transport layer.

1. **Create `services/mcp_tools_internal.py`**

   Extract the core logic from each `@mcp.tool()` decorated function in `mcp_server_v3.py` into standalone async functions. These should:
   - Accept the same parameters as the MCP tools
   - Return the same string output
   - NOT depend on the FastMCP framework
   - Share the same database connections and service instances

   ```python
   # services/mcp_tools_internal.py
   """
   Internal versions of MCP tools, callable from Python without MCP transport.
   These wrap the same logic as mcp_server_v3.py but return Python objects.
   """
   
   async def zoek_raadshistorie(vraag: str, datum_van: str = None,
                                 datum_tot: str = None, partij: str = None,
                                 max_results: int = 10) -> str:
       """Hybrid search across council documents."""
       # ... same logic as mcp_server_v3.py lines 527-568
   
   async def zoek_moties(onderwerp: str, partij: str = None,
                          datum_van: str = None, datum_tot: str = None,
                          status: str = None) -> str:
       """Search motions and amendments."""
       # ... same logic as mcp_server_v3.py lines 1217-1378
   ```

   **Important:** Do NOT duplicate the logic. Refactor `mcp_server_v3.py` to call these internal functions, keeping the MCP server as a thin wrapper:

   ```python
   # In mcp_server_v3.py — refactored
   from services.mcp_tools_internal import zoek_raadshistorie as _zoek_raadshistorie
   
   @mcp.tool()
   async def zoek_raadshistorie(vraag: str, ...) -> str:
       return await _zoek_raadshistorie(vraag, ...)
   ```

2. **Define Anthropic tool_use schemas**

   Create `services/tool_schemas.py` with the 13 tool definitions in Anthropic's format:

   ```python
   NEODEMOS_TOOLS = [
       {
           "name": "zoek_raadshistorie",
           "description": "Doorzoek de Rotterdamse raadsinformatie met hybride zoekmachine "
                          "(BM25 + vector + reranking). Gebruik voor algemene vragen over "
                          "raadsbesluiten, debatten en documenten.",
           "input_schema": {
               "type": "object",
               "properties": {
                   "vraag": {
                       "type": "string",
                       "description": "Zoekvraag (Nederlands). Verwijder temporele termen "
                                      "— gebruik datum_van/datum_tot voor tijdsfiltering."
                   },
                   "datum_van": {
                       "type": "string",
                       "description": "Startdatum ISO formaat (bijv. 2024-01-01). Optioneel."
                   },
                   "datum_tot": {
                       "type": "string",
                       "description": "Einddatum ISO formaat. Optioneel."
                   },
                   "partij": {
                       "type": "string",
                       "description": "Partijnaam voor gefilterd zoeken (bijv. 'VVD', 'GroenLinks-PvdA'). Optioneel."
                   },
                   "max_results": {
                       "type": "integer",
                       "description": "Maximaal aantal resultaten (standaard 10, max 25).",
                       "default": 10
                   }
               },
               "required": ["vraag"]
           }
       },
       # ... 12 more tools
   ]
   ```

**Acceptance criteria:**
- [ ] All 13 MCP tools callable as Python async functions
- [ ] `mcp_server_v3.py` refactored to use internal functions (no logic duplication)
- [ ] Tool schemas defined in Anthropic format
- [ ] Existing MCP transport (stdio/SSE) still works after refactor

### Phase 2 — Sonnet conversation loop (Day 1-2)

**Goal:** Build the core API-with-tools loop that gives Sonnet access to MCP tools.

1. **Create `services/web_intelligence.py`**

   ```python
   """
   Web Intelligence Service — MCP-as-Backend via Sonnet + Tool Use.
   
   Gives Claude Sonnet access to the same 13 tools that MCP clients use,
   via the Anthropic API's tool_use feature. This produces MCP-quality
   output from the web frontend.
   """
   
   import anthropic
   import asyncio
   import json
   import os
   import time
   from typing import AsyncIterator
   from services.mcp_tools_internal import TOOL_DISPATCH  # name → function map
   from services.tool_schemas import NEODEMOS_TOOLS
   
   SYSTEM_PROMPT = """Je bent NeoDemos, een civic intelligence platform voor de Rotterdamse 
   gemeenteraad. Je hebt toegang tot 90.000+ officiële raadsdocumenten (2002-heden) via 
   gespecialiseerde zoektools.
   
   GEDRAG:
   - Gebruik de tools om informatie op te halen. Verzin NOOIT informatie.
   - Elke feitelijke bewering MOET een bronvermelding [n] hebben.
   - Vertaal temporele termen ("vorig jaar", "sinds 2023") naar datum_van/datum_tot parameters.
   - Bij vragen over specifieke partijen: gebruik de partij-parameter.
   - Bij complexe vragen: gebruik meerdere tools in volgorde.
   - Antwoord altijd in het Nederlands.
   - Onderscheid raadsleden van insprekers/burgers.
   - Gebruik exacte bedragen en datums uit de bronnen.
   
   FORMAAT:
   - Eenvoudige vragen: 2-5 alinea's met bronverwijzingen [n].
   - Vergelijkingen: gebruik een markdown tabel.
   - Tijdlijnen: chronologische opsomming.
   - Eindig altijd met een bronnenlijst.
   
   Vandaag is {today}."""
   
   
   class WebIntelligenceService:
       """Sonnet + tool_use loop for web search."""
       
       def __init__(self):
           self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
           self.model = "claude-sonnet-4-6"  # or pin to specific version
           self.max_tool_rounds = 5  # prevent infinite loops
       
       async def query(self, user_query: str) -> dict:
           """Non-streaming: run full tool loop, return final answer."""
           messages = [{"role": "user", "content": user_query}]
           system = SYSTEM_PROMPT.format(today=date.today().isoformat())
           
           total_input_tokens = 0
           total_output_tokens = 0
           tools_called = []
           
           for round_num in range(self.max_tool_rounds):
               response = await asyncio.to_thread(
                   self.client.messages.create,
                   model=self.model,
                   max_tokens=4096,
                   system=system,
                   tools=NEODEMOS_TOOLS,
                   messages=messages,
               )
               
               total_input_tokens += response.usage.input_tokens
               total_output_tokens += response.usage.output_tokens
               
               # Check if Sonnet wants to use tools
               if response.stop_reason == "tool_use":
                   # Extract tool calls and text blocks
                   assistant_content = response.content
                   messages.append({"role": "assistant", "content": assistant_content})
                   
                   # Execute each tool call
                   tool_results = []
                   for block in assistant_content:
                       if block.type == "tool_use":
                           tool_name = block.name
                           tool_input = block.input
                           tools_called.append(tool_name)
                           
                           # Execute the tool
                           tool_fn = TOOL_DISPATCH.get(tool_name)
                           if tool_fn:
                               try:
                                   result = await tool_fn(**tool_input)
                               except Exception as e:
                                   result = f"Fout bij uitvoeren van {tool_name}: {str(e)}"
                           else:
                               result = f"Onbekende tool: {tool_name}"
                           
                           tool_results.append({
                               "type": "tool_result",
                               "tool_use_id": block.id,
                               "content": result,
                           })
                   
                   messages.append({"role": "user", "content": tool_results})
               
               elif response.stop_reason == "end_turn":
                   # Sonnet is done — extract final text
                   answer = ""
                   for block in response.content:
                       if hasattr(block, "text"):
                           answer += block.text
                   
                   return {
                       "answer": answer,
                       "tools_called": tools_called,
                       "rounds": round_num + 1,
                       "input_tokens": total_input_tokens,
                       "output_tokens": total_output_tokens,
                       "cost_usd": (total_input_tokens * 3 + total_output_tokens * 15) / 1_000_000,
                   }
           
           # Max rounds reached
           return {
               "answer": "De analyse is te complex geworden. Probeer een specifiekere vraag.",
               "tools_called": tools_called,
               "rounds": self.max_tool_rounds,
               "input_tokens": total_input_tokens,
               "output_tokens": total_output_tokens,
           }
       
       async def stream(self, user_query: str) -> AsyncIterator[dict]:
           """Streaming: yield SSE events as Sonnet works."""
           messages = [{"role": "user", "content": user_query}]
           system = SYSTEM_PROMPT.format(today=date.today().isoformat())
           tools_called = []
           
           yield {"type": "status", "message": "Vraag analyseren..."}
           
           for round_num in range(self.max_tool_rounds):
               # Non-streaming API call for tool rounds
               # (streaming during tool loops is complex; stream only the final answer)
               response = await asyncio.to_thread(
                   self.client.messages.create,
                   model=self.model,
                   max_tokens=4096,
                   system=system,
                   tools=NEODEMOS_TOOLS,
                   messages=messages,
               )
               
               if response.stop_reason == "tool_use":
                   assistant_content = response.content
                   messages.append({"role": "assistant", "content": assistant_content})
                   
                   tool_results = []
                   for block in assistant_content:
                       if block.type == "tool_use":
                           tool_name = block.name
                           tools_called.append(tool_name)
                           
                           # Show status
                           tool_display = {
                               "zoek_raadshistorie": "Raadsinformatie doorzoeken",
                               "zoek_financieel": "Financiële documenten zoeken",
                               "zoek_moties": "Moties doorzoeken",
                               "haal_partijstandpunt_op": "Partijstandpunt ophalen",
                               "zoek_uitspraken": "Uitspraken doorzoeken",
                               "tijdlijn_besluitvorming": "Besluitvormingstijdlijn opbouwen",
                               "scan_breed": "Breed zoeken",
                               "zoek_gerelateerd": "Gerelateerde documenten zoeken",
                           }.get(tool_name, tool_name)
                           
                           yield {"type": "status", "message": f"📡 {tool_display}..."}
                           
                           tool_fn = TOOL_DISPATCH.get(tool_name)
                           if tool_fn:
                               try:
                                   result = await tool_fn(**block.input)
                               except Exception as e:
                                   result = f"Fout: {str(e)}"
                           else:
                               result = f"Onbekende tool: {tool_name}"
                           
                           tool_results.append({
                               "type": "tool_result",
                               "tool_use_id": block.id,
                               "content": result,
                           })
                   
                   messages.append({"role": "user", "content": tool_results})
                   yield {"type": "status", "message": f"Analyseren (stap {round_num + 2})..."}
               
               elif response.stop_reason == "end_turn":
                   yield {"type": "status", "message": "Antwoord genereren..."}
                   
                   # Stream the final answer
                   # For the final round, we could re-call with streaming enabled
                   # For simplicity, yield the full answer in chunks
                   answer = ""
                   for block in response.content:
                       if hasattr(block, "text"):
                           answer += block.text
                   
                   # Yield in chunks for progressive rendering
                   chunk_size = 50  # characters
                   for i in range(0, len(answer), chunk_size):
                       yield {"type": "chunk", "text": answer[i:i+chunk_size]}
                   
                   yield {
                       "type": "done",
                       "tools_called": tools_called,
                       "rounds": round_num + 1,
                   }
                   return
           
           yield {"type": "done", "error": "Maximaal aantal stappen bereikt."}
   ```

2. **Wire into FastAPI SSE endpoint**

   Add to `main.py`:
   ```python
   from starlette.responses import StreamingResponse
   from services.web_intelligence import WebIntelligenceService
   
   web_intel = WebIntelligenceService()
   
   @app.get("/api/search/stream")
   async def search_stream(q: str, request: Request):
       """SSE endpoint for AI-powered search with tool use."""
       # Rate limit check here (from WS8 Phase 0B)
       
       async def event_generator():
           async for event in web_intel.stream(q):
               yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
       
       return StreamingResponse(
           event_generator(),
           media_type="text/event-stream",
           headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
       )
   ```

**Acceptance criteria:**
- [ ] Sonnet calls MCP tools via API tool_use
- [ ] Multi-turn tool loops work (Sonnet calls tool → gets result → calls another tool or synthesizes)
- [ ] Max 5 rounds per query (cost safety)
- [ ] Tool execution status streamed to frontend
- [ ] Final answer streamed via SSE
- [ ] Token/cost logged per query

### Phase 3 — Frontend integration (Day 2-3)

**Goal:** Connect the web frontend to the new Sonnet+tools backend.

1. **Update `search.html` JavaScript** to consume SSE:
   ```javascript
   async function performAISearch(query) {
       const eventSource = new EventSource(`/api/search/stream?q=${encodeURIComponent(query)}`);
       
       eventSource.onmessage = (event) => {
           const data = JSON.parse(event.data);
           
           switch (data.type) {
               case 'status':
                   updateLoadingStatus(data.message);
                   break;
               case 'chunk':
                   appendToAnswer(data.text);
                   break;
               case 'done':
                   finalizeAnswer(data);
                   eventSource.close();
                   break;
           }
       };
       
       eventSource.onerror = () => {
           showError('Verbinding verloren. Probeer opnieuw.');
           eventSource.close();
       };
   }
   ```

2. **Auto-detect: keyword-only vs AI search**
   ```javascript
   function shouldTriggerAI(query) {
       // Question patterns
       const questionWords = /^(wie|wat|waar|wanneer|waarom|hoe|welke|hoeveel|vergelijk)/i;
       const hasQuestionMark = query.includes('?');
       const isLongEnough = query.length > 15;
       
       return hasQuestionMark || (questionWords.test(query.trim()) && isLongEnough);
   }
   ```

3. **Parallel execution**: Keyword search fires immediately (fast), AI search starts in parallel if auto-detected. Keyword results show first, AI answer streams in above them.

4. **Rate limit UX**:
   - Before AI search: check `GET /api/search/limit` → returns `{remaining: 2, total: 3}`
   - If `remaining === 0`: show upgrade CTA instead of AI answer
   - Keyword results always shown (never gated)

**Acceptance criteria:**
- [ ] Keyword results appear within 500ms
- [ ] AI answer streams progressively with status updates
- [ ] Tool calls visible as status messages ("📡 Moties doorzoeken...")
- [ ] Auto-detect correctly identifies questions vs keyword searches
- [ ] Rate limit UI shows remaining searches
- [ ] Rate limit hit shows keyword results + upgrade CTA

### Phase 4 — System prompt tuning & eval (Day 3-4)

**Goal:** Tune the system prompt to produce output that matches MCP quality.

1. **A/B test system prompts**:
   - Run 20 queries from MCP replay logs through the web path
   - Compare output quality (citations, structure, accuracy, Dutch quality)
   - Iterate on system prompt based on failures

2. **System prompt refinements to test**:
   - Add 1-2 few-shot examples directly in system prompt
   - Experiment with tool ordering guidance ("voor financiële vragen, begin met zoek_financieel")
   - Test whether `temperature=0` vs `temperature=0.3` affects tool selection quality
   - Test `tool_choice="auto"` vs `tool_choice="any"` on first turn

3. **Cost optimization**:
   - Measure actual token usage per query type
   - Consider prompt caching for the system prompt (saves ~90% on system prompt tokens)
   - Evaluate whether some simple queries can skip Sonnet and use the existing keyword-only path

4. **Fallback chain**:
   ```python
   # If Anthropic API is down or slow:
   # 1. Try Sonnet
   # 2. Fall back to current Gemini pipeline (ai_service.py)
   # 3. Fall back to keyword-only results
   ```

**Acceptance criteria:**
- [ ] 20 MCP-replay queries produce comparable output
- [ ] System prompt produces well-cited Dutch output
- [ ] Prompt caching enabled for system prompt
- [ ] Fallback to Gemini pipeline if Anthropic unavailable
- [ ] Per-query cost tracking in logs

---

## Key Differences from Original WS9

| Aspect | Original WS9 (Custom Orchestrator) | Revised WS9 (MCP-as-Backend) |
|---|---|---|
| **Architecture** | Custom Python router + planner + synthesizer | Sonnet + tool_use (Claude IS the orchestrator) |
| **Model** | Tiered: Flash/Haiku/Sonnet by query type | Sonnet for all AI queries (rate-limited) |
| **Tool selection** | Rule-based Python logic | Sonnet decides adaptively |
| **New tools** | Require Python routing code | Automatically available (just add schema) |
| **Quality** | ~80% of MCP | ~95% of MCP |
| **Cost per query** | ~$0.008 (tiered) | ~$0.03-0.05 (Sonnet) |
| **Monthly cost** | ~€120 at 500 q/day | ~€150 at 200 q/day (rate-limited) |
| **Code complexity** | High (query router, orchestrator, prompts per type) | Low (one API loop + tool schemas) |
| **Maintenance** | Every new feature needs routing logic | Just add tool schema + function |

The rate limiting from WS8 makes the higher per-query cost manageable. At realistic volumes (1 anon + 3 free + some select users), actual daily queries will be ~100-200, not 500.

---

## Eval Gate

| Metric | Target | How to measure |
|---|---|---|
| MCP quality parity | >90% (manual review) | 20 MCP-replay queries, side-by-side comparison |
| Avg latency (simple query) | <5s | Timer in web_intelligence.py |
| Avg latency (multi-tool query) | <12s | Timer in web_intelligence.py |
| Avg cost per query | <$0.05 | Token logging |
| Tool loop safety | 0 infinite loops | Max 5 rounds enforced |
| Availability | Fallback works | Test with ANTHROPIC_API_KEY revoked |

---

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Sonnet cost higher than projected | Medium | Rate limiting caps exposure; monitor daily cost |
| Anthropic API latency >10s | Medium | Fallback to Gemini pipeline; non-blocking keyword results |
| Sonnet hallucinates tool names | Very Low | Schema validation; only registered tools in TOOL_DISPATCH |
| Tool results too large for context | Medium | Truncate tool output to 8K chars; tools already return condensed text |
| Refactoring mcp_server_v3.py breaks MCP | Medium | Run MCP test suite after refactor; keep MCP as thin wrapper |
| SSE blocked by kamal-proxy buffering | Low | Set `X-Accel-Buffering: no` header; test in staging |
