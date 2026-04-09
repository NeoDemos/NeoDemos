# NeoDemos MCP Server — Skill Reference

Use this skill when working on the MCP server for Claude Desktop, ChatGPT, and Perplexity integration.

## Versioning

- Version file: `VERSION` (single line, semver: `0.1.0`)
- Version module: `neodemos_version.py` (import `__version__`, `VERSION_LABEL`, `DISPLAY_NAME`, `STAGE`)
- Current: **v0.1.0 (alpha)**
- MCP server name shown to clients: `NeoDemos (alpha)` (from `DISPLAY_NAME`)
- Stage progression: `alpha` → `beta` → `rc` → `` (GA = v1.0)

## Core Design Principle

**MCP tools are pure retrieval/data-delivery layers. The host LLM (Claude, ChatGPT, Perplexity) does ALL reasoning and synthesis. Gemini/AIService is NEVER used in the MCP path.**

This is non-negotiable. It eliminates Gemini API latency (5-15s per call) and lets tool calls complete in 3-8s.

## Architecture

- File: `mcp_server_v3.py` (project root — this is the active server)
- Legacy: `mcp_server.py` (v1, kept for reference but not in Claude Desktop config)
- Framework: `FastMCP` from `mcp[cli]>=1.0.0`
- Transport: `stdio` (Claude Desktop launches as subprocess)
- Services: `RAGService` for retrieval, `StorageService` for SQL — both lazy-initialized
- Model loading: `LocalAIService(skip_llm=True)` — loads Qwen3-8B for embeddings but skips Mistral-24B (~12GB saved)
- BM25: Dual-dictionary tsvector (`dutch` for content, `simple` for entities) via `text_search_enriched` column

## The 12 Tools

| Tool | Type | Key feature |
|------|------|-------------|
| `zoek_raadshistorie` | RAG (ranked chunks + dates) | Party filtering via Qdrant payload |
| `zoek_financieel` | RAG (financial + tables, max_content=1200) | Table boost for budget queries |
| `zoek_uitspraken` | RAG (debate chunks, date-sorted) | Speaker attribution |
| `haal_vergadering_op` | SQL only (full meeting + agenda + docs) | |
| `lijst_vergaderingen` | SQL only (Markdown table by year/committee) | |
| `tijdlijn_besluitvorming` | RAG → yearly bucketing | |
| `analyseer_agendapunt` | RAG + party profile JSON | |
| `haal_partijstandpunt_op` | RAG + party profile JSON | |
| `zoek_moties` | SQL + enriched metadata | **Returns indieners, vote_outcome, vote_counts** |
| `scan_breed` | RAG (broad thematic search) | |
| `lees_fragment` | SQL (full document + chunks) | |
| `zoek_gerelateerd` | SQL (related docs by meeting/keywords) | |

## Enriched Metadata (v0.1.0)

`zoek_moties()` now returns structured data from the metadata enrichment pipeline:
- **Indieners**: Who submitted the motie/amendement (from `document_chunks.indieners`)
- **Vote outcome**: aangenomen/verworpen/ingetrokken/aangehouden (from `document_chunks.vote_outcome`)
- **Vote counts**: `{"voor": N, "tegen": M}` (from `document_chunks.vote_counts`)
- Data source: Tier 2 rule-based extraction on all 1.6M chunks (no LLM)

## Client Configuration

### Claude Desktop
File: `~/Library/Application Support/Claude/claude_desktop_config.json`
```json
{
  "mcpServers": {
    "NeoDemos (alpha)": {
      "command": "/Users/dennistak/Documents/Final Frontier/NeoDemos/.venv/bin/python",
      "args": ["/Users/dennistak/Documents/Final Frontier/NeoDemos/mcp_server_v3.py"],
      "env": { "PYTHONPATH": "/Users/dennistak/Documents/Final Frontier/NeoDemos" }
    }
  }
}
```
After any config change: fully quit Claude Desktop (Cmd+Q), reopen. Verify with: "welke tools heb je?"

### ChatGPT / Perplexity
These require a remote MCP server (SSE transport, not stdio). Deploy with:
```bash
.venv/bin/python mcp_server_v3.py sse --port 8001
```
Then register the SSE endpoint URL in the respective platform's MCP settings.

## Rules When Modifying MCP Tools

1. **Never import or call AIService/Gemini.** The host LLM is the reasoner.
2. **Always use `fast_mode=True`** on all RAG calls — skips CrossEncoder reranking (saves 3-8s).
3. **Return structured data, not prose.** Let the LLM do the synthesis.
4. **Keep tools focused.** Each tool answers one type of question.
5. **All tool names and descriptions are in Dutch** — the end users are Rotterdam city councilors.
6. **Thread `date_from`/`date_to`** params for temporal filtering where applicable.
7. **Party profiles** live at `data/profiles/party_profile_*.json`. Only GroenLinks-PvdA is detailed currently.
8. **Version**: Import from `neodemos_version.py`, never hardcode version strings.
9. **Test with:** `.venv/bin/python -c "from mcp.server.fastmcp import FastMCP; print('OK')"`

## What the Web Frontend Does Differently

The FastAPI web frontend (`main.py`) uses Gemini for 3-stage synthesis (extraction → debate mapping → report). The MCP server skips all of this — the host LLM handles synthesis. Both share the same `RAGService` and `StorageService` underneath. Changes to those services affect both interfaces.