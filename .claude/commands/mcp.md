# NeoDemos MCP Server — Skill Reference

Use this skill when working on the MCP server for Claude Desktop integration.

## Core Design Principle

**MCP tools are pure retrieval/data-delivery layers. Claude Desktop does ALL reasoning and synthesis. Gemini/AIService is NEVER used in the MCP path.**

This is non-negotiable. It eliminates Gemini API latency (5-15s per call) and lets tool calls complete in 3-8s.

## Architecture

- File: `mcp_server.py` (project root)
- Framework: `FastMCP` from `mcp[cli]>=1.0.0`
- Transport: `stdio` (Claude Desktop launches as subprocess)
- Services: `RAGService` for retrieval, `StorageService` for SQL — both lazy-initialized
- Model loading: `LocalAIService(skip_llm=True)` — loads Qwen3-8B for embeddings but skips Mistral-24B (~12GB saved)

## The 8 Tools

| Tool | Type | fast_mode |
|------|------|-----------|
| `zoek_raadshistorie` | RAG (ranked chunks + dates) | yes |
| `zoek_financieel` | RAG (financial + tables, max_content=1200) | yes |
| `zoek_uitspraken` | RAG (debate chunks, date-sorted) | yes |
| `haal_vergadering_op` | SQL only (full meeting + agenda + docs) | n/a |
| `lijst_vergaderingen` | SQL only (Markdown table by year/committee) | n/a |
| `tijdlijn_besluitvorming` | RAG → synthesize_timeline() | yes |
| `analyseer_agendapunt` | RAG + party profile JSON | yes |
| `haal_partijstandpunt_op` | RAG + party profile JSON | yes |

## Key Patterns

```python
# Lazy init — no model loading until first tool call
_rag_instance = None
def _get_rag():
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = RAGService.__new__(RAGService)
        _rag_instance.local_ai = LocalAIService(skip_llm=True)
        _rag_instance._ensure_resources_initialized()
    return _rag_instance

# Party profile fuzzy-matching
def _load_party_profile(partij: str) -> Optional[Dict]:
    # Matches against data/profiles/party_profile_*.json
```

## Claude Desktop Config

File: `~/Library/Application Support/Claude/claude_desktop_config.json`
```json
{
  "mcpServers": {
    "neodemos": {
      "command": "/Users/dennistak/Documents/Final Frontier/NeoDemos/.venv/bin/python",
      "args": ["/Users/dennistak/Documents/Final Frontier/NeoDemos/mcp_server.py"],
      "env": { "PYTHONPATH": "/Users/dennistak/Documents/Final Frontier/NeoDemos" }
    }
  }
}
```
After any config change: fully quit Claude Desktop (Cmd+Q), reopen. Verify with: "welke tools heb je?"

## Rules When Modifying MCP Tools

1. **Never import or call AIService/Gemini.** Claude Desktop is the LLM.
2. **Always use `fast_mode=True`** on all RAG calls — skips CrossEncoder reranking (saves 3-8s).
3. **Return structured data, not prose.** Let Claude do the synthesis.
4. **Keep tools focused.** Each tool answers one type of question.
5. **All tool names and descriptions are in Dutch** — the end users are Rotterdam city councilors.
6. **Thread `date_from`/`date_to`** params for temporal filtering where applicable.
7. **Party profiles** live at `data/profiles/party_profile_*.json`. Only GroenLinks-PvdA is detailed currently.
8. **Test with:** `.venv/bin/python -c "from mcp.server.fastmcp import FastMCP; print('OK')"`

## What the Web Frontend Does Differently

The FastAPI web frontend (`main.py`) uses Gemini for 3-stage synthesis (extraction → debate mapping → report). The MCP server skips all of this — Claude Desktop handles synthesis. Both share the same `RAGService` and `StorageService` underneath. Changes to those services affect both interfaces.
