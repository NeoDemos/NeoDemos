# Le Chat — NeoDemos MCP Integration

> **Status:** supported via standard MCP streamable-http transport
> **Available on:** Le Chat Free, Plus, Pro, Team
> **URL:** `https://mcp.neodemos.nl/mcp` (authenticated) · `https://mcp.neodemos.nl/public/mcp` (public)

## Why Le Chat

- **Free tier has MCP connectors** — no other major chat client offers this
- **iOS + Android native apps** — mobile civic research
- **EU-sovereign stack** — French models, German servers, Dutch data. Zero US-hop
- **Open connector ecosystem** — OAuth 2.1 + DCR means no manual token copying

## Installation (authenticated — full access)

1. Open Le Chat → **Intelligence** → **Connectors** → **+ Add Connector** → **Custom MCP Connector**
2. Paste: `https://mcp.neodemos.nl/mcp`
3. Click "Authorize" → Le Chat redirects to neodemos.nl → log in → consent → back to Le Chat
4. Verify 13 tools appear in the tool list

## Installation (public — no login, journalist/citizen path)

1. Open Le Chat → **Intelligence** → **Connectors** → **+ Add Connector** → **Custom MCP Connector**
2. Paste: `https://mcp.neodemos.nl/public/mcp`
3. No OAuth — tools are immediately available
4. Rate limit: 20 calls/min per IP (5/min for expensive tools)

## Tested flows

The following end-to-end tests were run against Le Chat free-tier on 2026-04-XX (TODO: update after smoke test):

| Query | Expected behavior | Status |
|---|---|---|
| "Hoeveel was de begrotingsruimte voor wijkveiligheid in 2025?" | Markdown table with euro amounts + source link | TODO |
| "Welke moties over warmtenetten zijn verworpen sinds 2022?" | List of verworpen moties with dates + signatories | TODO |
| "Wat is het standpunt van Leefbaar Rotterdam over parkeren?" | Party position with citations | TODO |

## Known Mistral-specific quirks

- **Weaker temporal reasoning** — Mistral Medium 3.1 / Magistral occasionally forgets to translate "vorig jaar" into `datum_van`/`datum_tot`. NeoDemos has a server-side temporal-extraction fallback (see `services/temporal_parser.py`) that catches this.
- **Longer-context degradation** — when a tool returns very long markdown, Mistral sometimes truncates citations. We mitigate with skeleton-formatted responses for canonical question shapes (`vraag_begrotingsregel`, `vergelijk_partijen`, etc.).
- **No `$ref` / `$defs` / `$id` in JSON schemas** — Mistral's grammar compiler rejects these. NeoDemos tool schemas are flat and pass validation.

## Troubleshooting

### "Failed to detect auth method"

Run `curl -i https://mcp.neodemos.nl/mcp` and check the response headers. You should see:

```
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer realm="NeoDemos MCP", resource_metadata="https://mcp.neodemos.nl/.well-known/oauth-protected-resource"
```

If `WWW-Authenticate` is missing, the MCP SDK version is wrong. NeoDemos pins `mcp[cli]` to a specific version in `requirements.txt` to prevent regressions.

### "Authorization redirect fails"

Check `oauth_clients` table in Postgres for a recently-created row. If not present, the DCR failed — check `services/mcp_oauth_provider.py` logs.

### "Tools list is empty after auth"

Le Chat's client sometimes caches an empty tool list. Remove the connector and re-add it.

### "Inspector cross-check"

Before filing a bug with Mistral support, run `npx @modelcontextprotocol/inspector https://mcp.neodemos.nl/mcp` and confirm the same flow works. If Inspector passes but Le Chat fails, file with Mistral; if both fail, fix our server first.

## EU sovereignty one-liner

> NeoDemos + Le Chat: French model (Mistral) talking to German-hosted RAG (Hetzner FSN) over Dutch council data. **Zero US hop.** This is the procurement-friendly framing for Dutch gemeenten that is structurally unavailable to Claude/ChatGPT integrations.

## References

- [Mistral MCP Connectors help](https://help.mistral.ai/en/articles/393572-configuring-a-custom-connector)
- [MCP spec](https://modelcontextprotocol.io/specification)
- [NeoDemos WS4 handoff](../handoffs/done/WS4_MCP_DISCIPLINE.md)
