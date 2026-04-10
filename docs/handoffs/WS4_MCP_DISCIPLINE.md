# WS4 — Best-in-Class MCP Surface

> **Priority:** 4 (the moat MAAT structurally cannot match)
> **Status:** `not started`
> **Owner:** `unassigned`
> **Target release:** v0.2.0 (registry, primer, defense-in-depth); v0.3.0 (TypeScript codegen)
> **Master plan section:** [V0_2_BEAT_MAAT_PLAN.md §6](../architecture/V0_2_BEAT_MAAT_PLAN.md)

## TL;DR
Make the NeoDemos MCP server the *reference implementation for govtech MCP* by adopting FactSet's enterprise discipline (centralized tool registry, defense-in-depth auth, audit logging, AI-consumption tool descriptions) and Figma's design conventions (verb_noun naming, sparse-then-dense, context primer tool). Customers should be able to wire NeoDemos into Claude/ChatGPT/Perplexity with zero friction. Code-execution distribution (`@neodemos/mcp-tools` npm package, Anthropic 98.7% token-savings pattern) is deferred to v0.3.0.

## Dependencies
- **None** for v0.2.0 scope. Fully independent and can start day 1.
- **Coordinates with WS1, WS2, WS3, WS6** which all add new tools that must register here.
- Memory to read first:
  - [project_mcp_and_frontend_architecture.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_mcp_and_frontend_architecture.md)

## Cold-start prompt

> You are picking up Workstream 4 (Best-in-Class MCP Surface) of NeoDemos v0.2.0. Self-contained handoff at `docs/handoffs/WS4_MCP_DISCIPLINE.md`.
>
> Read in order: (1) this handoff, (2) `mcp_server_v3.py` (current 13 tools), (3) `services/mcp_oauth_provider.py` (current OAuth scopes), (4) the FactSet enterprise MCP articles linked below, (5) the Figma tools-and-prompts page linked below.
>
> Your job: ship a `services/mcp_tool_registry.py` that becomes the single source of truth for every MCP tool's metadata (name, scopes, latency, output schema), enforce FactSet's defense-in-depth (parameter validation, output filtering, audit log without secrets), add a `get_neodemos_context()` primer tool, and rewrite all tool descriptions for AI consumption (not human documentation). Coordinate with WS1, WS2, WS3, WS6 — every new tool they ship must register here.
>
> The TypeScript codegen / npm package (`@neodemos/mcp-tools`) is deferred to v0.3.0 — do **not** build it now. Anomaly detection beyond simple rate limits is also v0.3.0.
>
> External references you should read:
> - https://insight.factset.com/enterprise-mcp-model-context-protocol-part-one
> - https://medium.com/@factset/enterprise-mcp-part-3-security-and-governance-27ec39380bbe
> - https://developers.figma.com/docs/figma-mcp-server/tools-and-prompts/

## Files to read first
- [`mcp_server_v3.py`](../../mcp_server_v3.py) — current 13 tools, all `@mcp.tool()` decorators
- [`services/mcp_oauth_provider.py`](../../services/mcp_oauth_provider.py) — current OAuth scopes (`["mcp", "search"]`)
- [`templates/mcp_installer.html`](../../templates/mcp_installer.html) — current installer page
- External:
  - [FactSet Enterprise MCP Part 1](https://insight.factset.com/enterprise-mcp-model-context-protocol-part-one)
  - [FactSet Enterprise MCP Part 3 — Security & Governance](https://medium.com/@factset/enterprise-mcp-part-3-security-and-governance-27ec39380bbe)
  - [Figma MCP Tools and Prompts](https://developers.figma.com/docs/figma-mcp-server/tools-and-prompts/)
  - [Anthropic Code Execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) (read for v0.3.0 prep, do not implement now)

## Build tasks

### Tool registry (~2 days)

- [ ] **`services/mcp_tool_registry.py`** — new file. Single source of truth:
  ```python
  @dataclass
  class ToolSpec:
      name: str                          # "zoek_raadshistorie"
      module: str                        # "mcp_server_v3"
      summary: str                       # one sentence, AI-readable
      ai_description: str                # multi-line, includes "use when" + "do NOT use when"
      scopes: list[str]                  # ["mcp", "search"]
      input_schema: dict                 # JSON Schema
      output_schema: dict                # JSON Schema
      latency_p50_ms: int                # measured, not estimated
      cost_per_call_usd: float           # measured (Jina rerank + Nebius embed cost)
      stability: Literal["stable", "experimental", "deprecated"]
      added_in_version: str              # "0.1.0"
      examples: list[ToolExample]        # at least 2: positive + negative
  REGISTRY: dict[str, ToolSpec] = {...}  # all 13 current + new tools
  ```
- [ ] **Migrate all 13 existing tools** in [`mcp_server_v3.py`](../../mcp_server_v3.py) into the registry. Don't change tool implementations; just register them.
- [ ] **Auto-export OpenAPI spec** to `docs/api/mcp_openapi.json` from the registry. Used by external integrators.

### AI-consumption descriptions (~1 day)

FactSet rule: "tool descriptions need to be written for AI consumption, not human documentation… models interpret descriptions literally and lack contextual knowledge to resolve ambiguities."

- [ ] **Description template** every tool must follow:
  ```
  {one-sentence what it does}

  Use this when:
  - {positive case 1}
  - {positive case 2}

  Do NOT use this when:
  - {negative case → {alternative tool}}
  - {ambiguity → ask user}

  Returns: {one-sentence output description}
  ```
- [ ] Rewrite all 13 existing tool descriptions to this template
- [ ] Coordinate with WS1, WS2, WS3, WS6 — they must use the template for new tools

### Tool-collision detection (~0.5 day)

FactSet pattern: "vector database to score uniqueness of tool descriptions across all other tool descriptions."

- [ ] **`services/mcp_tool_uniqueness.py`** — at server startup:
  - Embed every tool's `ai_description` via existing Qwen3-8B embedder
  - Compute pairwise cosine similarity
  - **Warn at log level WARNING if any pair > 0.85 cosine** — that's a sign of overlap that confuses LLMs
  - **Fail server startup if any pair > 0.95** — clear naming collision

### Defense-in-depth (~2 days)

FactSet 4 layers: tool / parameter / resource / output.

- [ ] **Layer 1 — tool-level scopes** (already have OAuth, just enforce):
  - Each tool's `scopes` field in registry is checked against the request's scopes
  - 403 if missing
- [ ] **Layer 2 — parameter validation decorator** `@validated_params`:
  - Enforce JSON Schema from registry
  - Default string length cap: 10K chars (FactSet rule)
  - Date bounds: no dates < 2000 or > 2030
  - Whitelist `gemeente` to known tenants
- [ ] **Layer 3 — resource-level auth check** during execution:
  - Before returning a chunk, verify the user's `gemeente` claim matches the chunk's `gemeente` payload
  - Reject silently (filter out) — never leak existence
- [ ] **Layer 4 — output filter** `services/output_filter.py`:
  - Strip PII the user's scope doesn't grant access to
  - Strip internal IDs starting with `_internal_`
  - Truncate any field > 50K chars to prevent context bombing

### Audit log (~1 day)

FactSet rule: "log without capturing secrets."

- [ ] **`mcp_audit_log` table** via Alembic:
  ```sql
  CREATE TABLE mcp_audit_log (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ DEFAULT NOW(),
    user_id TEXT,                          -- from OAuth subject
    tool_name TEXT NOT NULL,
    params_hash TEXT,                      -- sha256, never raw params
    scope_used TEXT[],
    latency_ms INT,
    result_size_bytes INT,
    status_code INT,
    ip TEXT,
    error_class TEXT
  );
  CREATE INDEX ON mcp_audit_log (ts DESC);
  CREATE INDEX ON mcp_audit_log (user_id, ts DESC);
  CREATE INDEX ON mcp_audit_log (tool_name, ts DESC);
  ```
- [ ] **`services/audit_logger.py`** — wrap every tool call with begin/end logging
- [ ] **NEVER log**: tokens, signatures, raw parameter values, full result bodies
- [ ] **Daily summary** rendered at `/admin/mcp` showing top tools, top users, p50/p95 latency, error rate

### Context primer tool (~1 day)

Figma's `create_design_system_rules` analogue.

- [ ] **`get_neodemos_context() -> dict`** — zero-arg MCP tool returning a structured primer the LLM can read on first connect:
  ```json
  {
    "version": "0.2.0",
    "gemeenten": [
      {"name": "rotterdam", "mode": "full", "documents": 90000, "date_from": "2002-01-01", "date_to": "2026-04-10"}
    ],
    "document_types": ["notulen", "motie", "amendement", "raadsvoorstel", "raadsbrief", "jaarstukken", "voorjaarsnota", "begroting", "10-maandsrapportage", "agendapunt"],
    "council_composition": {
      "rotterdam": {"parties": ["Leefbaar Rotterdam", "GroenLinks-PvdA", "VVD", "..."], "current_coalition": [...], "total_seats": 45}
    },
    "limitations": [
      "financial line-items only available 2018+ for Rotterdam",
      "transcripts only available for committee meetings, not raadsvergaderingen"
    ],
    "recommended_tool_sequences": [
      {"intent": "begrotingsvragen", "sequence": ["vraag_begrotingsregel", "zoek_financieel"]},
      {"intent": "motie_traceren", "sequence": ["zoek_moties", "traceer_motie"]},
      {"intent": "case_history", "sequence": ["zoek_raadshistorie", "traceer_document"]}
    ]
  }
  ```
- [ ] Tool description: "Call this first when you connect to NeoDemos. It tells you what gemeenten are available, what document types exist, and which tool sequences work for common questions. Cheap to call (<50ms)."

### Rate limiting (~0.5 day)

- [ ] **Per-user-per-minute** simple rate limit middleware: 60 calls/min default, 10/min for expensive tools (`traceer_motie`, `vergelijk_partijen`, `vat_dossier_samen`)
- [ ] Anomaly-detection ML is **deferred to v0.3.0**

## Acceptance criteria

- [ ] `services/mcp_tool_registry.py` exists with all 13 current + new tools registered
- [ ] All tool descriptions follow the AI-consumption template (with positive AND negative use cases)
- [ ] Tool-collision check runs at server startup; no pair > 0.85 cosine
- [ ] All 4 defense-in-depth layers implemented and tested
- [ ] `mcp_audit_log` table exists; every tool call logged; no secrets in log
- [ ] `get_neodemos_context()` tool returns the structured primer
- [ ] OpenAPI spec auto-exported at `docs/api/mcp_openapi.json`
- [ ] `/admin/mcp` dashboard shows audit summary
- [ ] Rate limits enforced (60/min default, 10/min for expensive tools)
- [ ] WS1, WS2, WS3, WS6 tools all registered with conformant descriptions

## Eval gate

| Metric | Target |
|---|---|
| Tool-description cosine uniqueness | No pair > 0.85 |
| First-call agent success without primer (baseline) | measured |
| First-call agent success WITH primer | ≥ 15 percentage points improvement over baseline |
| Audit log overhead per call | < 5ms p50 |
| Defense-in-depth: malicious test corpus blocked | 100% |

## Risks specific to this workstream

| Risk | Mitigation |
|---|---|
| Description rewrites cause LLM regressions | Side-by-side eval before/after on `rag_evaluator` benchmark; rollback if completeness drops > 0.2 |
| Tool registry becomes a maintenance burden | Generate the registry from `@mcp.tool()` decorators where possible; manual entries only for metadata not derivable |
| Audit log volume blows up Postgres | Partition by month; daily aggregation job in WS5a |
| Context primer becomes stale | Auto-generate the gemeenten/document-types fields from Postgres at server boot |
| Defense-in-depth false positives block legit calls | Start with logging-only mode for 7 days, then enforce |

## Future work (do NOT do in this workstream)
- TypeScript codegen + `@neodemos/mcp-tools` npm package — **v0.3.0**
- Anthropic Code Execution with MCP example workflows — **v0.3.0**
- ML-based anomaly detection — **v0.3.0**
- OBO token exchange for service-to-service — **v0.4.0**
- Multi-tenant scope isolation — depends on WS5b multi-portal — **v0.2.1+**

## Outcome
*To be filled in when shipped. Include: registry size, description rewrite delta on eval scores, audit log volume per day, primer impact measurement.*
