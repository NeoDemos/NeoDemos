# WS4 — Best-in-Class MCP Surface

> **Priority:** 4 (the moat MAAT structurally cannot match)
> **Status:** `shipped v0.2.0-alpha.2 (2026-04-13)` — core done; Le Chat smoke test + installer card pending
> **Owner:** `claude`
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

## v0.2.0 Blockers — fix before eval gate

> These 8 bugs are verified failures from the 2026-04-10/11 feedback log. They make the MCP surface unreliable in ways that distort the eval baseline. **Do not run the v0.2.0 eval gate before all 8 are shipped.** Each has a full description in the relevant build-tasks section below — this list is the quick-reference and sequencing guide.
>
> Agent: start here, then proceed to Build tasks.

| # | Bug | Status | Location in build tasks |
|---|---|---|---|
| B1 | `zoek_moties` misses initiatiefvoorstellen on single-word queries | ✅ **Fixed 2026-04-13** | §MCP tool bug fixes |
| B2 | Overview queries take 15–25s (sequential lees_fragment calls) | ✅ **Fixed 2026-04-13** (`lees_fragmenten_batch` added) | §MCP tool bug fixes |
| B3 | `lees_fragment` returns chunks in stored order, not query-relevant order | ✅ **Fixed 2026-04-13** (`query=` param wired to Jina reranker) | §Tool API improvements |
| B4 | `zoek_financieel` description gives no example of `budget_year` vs `datum_van` divergence | ✅ **Fixed 2026-04-13** | §Tool API improvements |
| B5 | Dedup-by-document_id only at render time — same doc consumes multiple top_k slots | ✅ **Fixed 2026-04-13** | §Retrieval quality fixes |
| B6 | No minimum score floor — 0.06-similarity noise chunks reach LLM context | ✅ **Fixed 2026-04-13** (`MIN_SIMILARITY=0.15` wired) | §Retrieval quality fixes |
| B7 | Content-empty chunks (< 80 chars) not filtered before returning | ✅ **Fixed 2026-04-13** (`MIN_CONTENT_CHARS=80` wired) | §Retrieval quality fixes |
| B8 | `require_login` returns `RedirectResponse` instead of raising — handlers run with wrong user type | ✅ **Fixed 2026-04-13** (raises `HTTPException(303)`, 11 dead `isinstance` checks removed) | §Defense-in-depth (Layer 1) |

**All 8 blockers shipped 2026-04-13.**

---

## Build tasks

### Tool registry (~2 days)

- [x] **`services/mcp_tool_registry.py`** — new file. Single source of truth:
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
- [x] **Migrate all tools** — 20 tools registered (13 original + `traceer_motie`, `vergelijk_partijen`, `lees_fragmenten_batch`, plus WS2 financial tools). All follow the AI-consumption description template with Use/Do NOT use sections.
- [x] **Auto-export OpenAPI spec** to `docs/api/mcp_openapi.json` from the registry.

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
- [x] All 20 tools rewritten to this template. `zoek_uitspraken_op_rol` scoped correctly; no "call this proactively" instructions.
- [x] WS2 financial tools (`zoek_financieel`, `vraag_begrotingsregel`) registered with conformant descriptions.

### Tool-collision detection (~0.5 day)

FactSet pattern: "vector database to score uniqueness of tool descriptions across all other tool descriptions."

- [x] **`services/mcp_tool_uniqueness.py`** — at server startup:
  - Embed every tool's `ai_description` via existing Qwen3-8B embedder
  - Compute pairwise cosine similarity
  - **Warn at log level WARNING if any pair > 0.85 cosine** — that's a sign of overlap that confuses LLMs
  - **Fail server startup if any pair > 0.95** — clear naming collision

### MCP tool bug fixes (~1 day) *(added 2026-04-11, triaged from TODOS)*

Two MCP retrieval quality bugs found during the 2026-04-10 test session. Both are **fix-before-rewrite** items — land them early so the WS4 tool-description rewrites and eval baselines aren't measuring around broken behavior.

- [x] **[MCP bug] `zoek_moties` is title-only for single-word queries — misses initiatiefvoorstellen.** At [`mcp_server_v3.py:981-996`](../../mcp_server_v3.py#L981-L996) the topic filter builds `LOWER(d.name) LIKE '%term%'` clauses only. The content-match branch is gated on `len(search_terms) >= 3`, so "leegstand" never hits content. Moties happen to carry the topic in their title ("Motie Leegstandsbelasting") so they match; initiatiefvoorstellen with generic titles ("Initiatiefvoorstel Engberts & Vogelaar over wonen") are invisible. **Action:** (1) always include content in the OR clause — add `LOWER(d.content) LIKE %s` alongside the name match regardless of term count; (2) keep the `>= 2 terms must match` precision guard for multi-word queries but drop the `>= 3` gate; (3) add a regression test in [`tests/mcp/test_zoek_moties.py`](../../tests/): `zoek_moties("leegstand")` must return the Engberts/Vogelaar initiatiefvoorstel. Source: [FEEDBACK_LOG.md 2026-04-10 "Initiatiefvoorstel Engberts & Vogelaar ontbreekt"](../../brain/FEEDBACK_LOG.md).

- [x] **[MCP latency] Overview queries run sequentially and take 15–25s.** The Claude.ai client serializes tool calls within a turn, so looping `lees_fragment` over 4–8 hits dominates wall time. **Fix cheapest-first:** (a) bump `zoek_moties` content preview from 300 → 1500 chars so the host LLM rarely needs to follow up with `lees_fragment` at all; (b) add a `lees_fragmenten_batch(document_ids: list[str])` tool that returns N documents in one call and register it in the tool registry; (c) longer-term: parallelize across the async pool inside a single tool invocation (deferred to v0.3). **Start with (a)** — one-line change, biggest UX win. Source: [FEEDBACK_LOG.md 2026-04-10 "Trage responsetijd bij leegstand-overzichtsvraag"](../../brain/FEEDBACK_LOG.md).

### Tool API improvements (~1 day) *(added 2026-04-11, triaged from [FEEDBACK_LOG.md 2026-04-11](../../brain/FEEDBACK_LOG.md))*

Three targeted changes from the 2026-04-11 parkeertarieven and GRJR-scope audits.

- [x] **`lees_fragment(document_id, query=...)` — optional query param for in-document re-ranking.** Today `lees_fragment` returns fragments in their stored order (typically 1–5 sequentially), which means the chunk that `zoek_raadshistorie` found can be buried when the user reads the document. Failure case: for `fin_jaarstukken_2019`, `zoek_raadshistorie` correctly surfaced the Middelland venstertijden paragraph, but `lees_fragment(doc_id)` returned financial-summary tables instead. **Action:** (1) add optional `query: str | None = None` parameter; (2) when present, re-rank the document's fragments against the query using the existing Jina v3 reranker before slicing; (3) update the tool description to explain when to pass a query (always, if you just found this doc via a topic search). Register the new schema in the tool registry.

- [x] **Zero-result coverage signal.** Today when `zoek_raadshistorie` returns 0 results for "parkeerbelasting 2005", the LLM cannot tell whether no docs exist or the query didn't match — and fills the gap with estimates. Fix: append a one-line footer only on zero-result responses, using static constants maintained in `mcp_server_v3.py`:
  ```
  _Corpus: Rotterdam raadsdocumenten 2002–heden. 0 resultaten betekent niet dat het beleid niet bestaat — probeer een bredere zoekvraag of controleer de datumfilter._
  ```
  No WS5a dependency, no schema change, no decorator — just a string append in the zero-branch of each retrieval tool. Update the constants when new year-ranges are ingested. Defer richer per-doc-type coverage JSON to v0.3.0 after WS5a's `/coverage` dashboard exists.

- [x] **Rewrite `zoek_financieel` tool description to clarify `budget_year` vs `datum_van`** *(added 2026-04-11)*. Today's description at [mcp_server_v3.py:438-440](../../mcp_server_v3.py#L438-L440) says `budget_year` is "nauwkeuriger" but gives no example of when they diverge. Include this example verbatim: *"Begroting 2025 wordt in oktober 2024 ingediend (publicatiedatum) maar beschrijft fiscaal jaar 2025 (budget_year). Gebruik `budget_year=2025` voor 'wat is de begrotingsruimte voor 2025'; gebruik `datum_van='2024-10-01'` voor 'welke begrotingsdocumenten werden in oktober 2024 gepubliceerd'."* When WS2 ships, add to the same description: *"Elke match bevat een `scope` veld. Aggregeer nooit over `scope='gemeente'` en `scope='gemeenschappelijke_regeling'` heen zonder dat expliciet te benoemen."*

### Source URLs in MCP output (~0.5 day) *(added 2026-04-11)*

`documents.url` already exists and has **97% coverage** (85,791 / 88,489 docs) — confirmed by audit 2026-04-11. It contains direct PDF download links (`api1.ibabs.eu/publicdownload.aspx?...`) for both `meeting` and `financial` documents. No schema changes needed. The `document_chunks` table does not carry `url`, so one batch query per response is needed.

The 3% gap by category: `municipal_doc` 2024–2026 (~1,600 docs, URL not captured at ingest — acceptable for v0.2.0), `video_transcript` (~280 docs, no PDF exists — citation via webcast timestamp, handled in WS5a).

*Note: `meetings.ibabs_url` was considered and ruled out — only 32% coverage and meeting-page level, not document level.*

- [x] **In `_format_chunks_v3` ([mcp_server_v3.py:256](../../mcp_server_v3.py#L256)), batch-fetch `documents.url` for all rendered chunks** — one query, no JOIN:
  ```sql
  SELECT id, url FROM documents WHERE id = ANY(%s)
  ```
  Append `[Brondocument ↗](url)` to each result line. Chunks where `url IS NULL` render without a link (3% of corpus — acceptable).

- [x] **Apply to `lees_fragment` as well** — include the document URL at the top of the fragment response. Same query pattern.

### Retrieval quality fixes (~0.5 day) *(added 2026-04-11, triaged from [FEEDBACK_LOG.md 2026-04-11 "Haven & Duurzaamheid"](../../brain/FEEDBACK_LOG.md))*

Three one-line fixes observed when testing `zoek_uitspraken` and `scan_breed` on the haven/duurzaamheid topic: the same document consumed 4 of 8 result slots; a chunk containing only "Geen stukken ontvangen" occupied a slot; scores as low as 0.06 appeared alongside 0.77 for comparable queries.

- [x] **Dedup by `document_id` before the `top_k` cut in all retrieval tools.** `zoek_raadshistorie` already passes `dedup_by_doc=True` to `_format_chunks_v3`, but that deduplicates at *render time* — the same document still consumes multiple `top_k` slots upstream. Fix the retrieval layer: after reranking, keep only the highest-scoring chunk per `document_id` before slicing to `top_k`. Effect: `max_resultaten=8` returns 8 unique documents, not 8 chunks from potentially 2 documents. Apply to all retrieval tools (`zoek_uitspraken`, `scan_breed`, `zoek_gerelateerd` — verify each). Source: `zoek_uitspraken` returned doc 6115020 and notulen-2025-04-10 each 4× in an 8-slot response.

- [x] **Minimum score floor: drop chunks with similarity < 0.15 before rendering.** `scan_breed` returned fragments scoring 0.06 alongside 0.77 for the same query — the 0.06 chunk is noise that burns a result slot and introduces irrelevant text into LLM context. Add a `MIN_SIMILARITY = 0.15` constant in `mcp_server_v3.py` and filter before slicing. If filtering leaves fewer than 3 results, relax to 0.10 (don't return empty responses for borderline queries). The existing `table_chunks` threshold of 0.25 in `zoek_financieel` is already a precedent — generalise it.

- [x] **Filter content-empty chunks before returning.** A chunk whose content (stripped) is shorter than 80 chars — e.g. "Geen stukken ontvangen", a bare section header, an empty table cell — provides no value and wastes a slot. Add a `MIN_CONTENT_CHARS = 80` guard in `_format_chunks_v3` (or the retrieval layer). Log filtered chunks to `mcp_audit_log` with `error_class='empty_chunk'` so ingest can fix the root cause.

### Defense-in-depth (~2 days)

FactSet 4 layers: tool / parameter / resource / output.

- [x] **Layer 1 — tool-level scopes** — enforced via `logged_tool` decorator; 403 if scopes missing.
- [x] **Fix `require_login` to raise instead of return** *(added 2026-04-11 from QA pass)* — [services/auth_dependencies.py:64-71](../../services/auth_dependencies.py#L64-L71) `require_login` returns a `RedirectResponse` from a FastAPI dependency. FastAPI does **not** short-circuit dependency returns: the handler then runs with `user = RedirectResponse(...)`. Any handler that accesses `user["role"]` raises `TypeError`; any handler that doesn't check type leaks data to unauthenticated callers. `require_admin` at [services/auth_dependencies.py:74-82](../../services/auth_dependencies.py#L74-L82) already has a `isinstance(user, RedirectResponse)` workaround — proof that the author knows this is broken. Fix: raise `HTTPException(status_code=303, headers={"Location": "/login"})` or register a dedicated exception handler. Must audit every `Depends(require_login)` call site in [main.py](../../main.py) — some may depend on the current (broken) behavior and need an isinstance-check removed.
- [x] **Layer 2 — parameter validation** `services/mcp_validation.py` — string cap 10K, dates 2000–2030, gemeente whitelist `{"rotterdam"}`. Applied via `logged_tool` decorator.
- [x] **Fail-fast on insecure default secrets in production** *(added 2026-04-11 from QA pass)* — two findings bundled: [services/auth_dependencies.py:24](../../services/auth_dependencies.py#L24) defaults `SECRET_KEY` to the literal `"change-me-in-production"`, and [services/db_pool.py:36](../../services/db_pool.py#L36) defaults `DB_PASSWORD` to `"postgres"` (mirrored in [docker-compose.yml:9](../../docker-compose.yml#L9)). Production Kamal already injects real values via [config/deploy.yml](../../config/deploy.yml) secrets so neither is an active vulnerability today — but add a startup validation in [main.py](../../main.py) lifespan: if `os.getenv("ENVIRONMENT") == "production"` and either env var is unset or equals the insecure default, raise on boot. Fail fast beats a forgiving dev default that silently ships to prod.
- [x] **Replace f-string SQL method interpolation** *(added 2026-04-11 from QA pass)* — `assert method in {...}` whitelist added before f-string. — [services/rag_service.py:503-514](../../services/rag_service.py#L503-L514) uses an f-string to inject the Postgres full-text-search function name (`to_tsquery`, `plainto_tsquery`, `websearch_to_tsquery`) into the SQL template. The value is currently safe because it's set to one of three hardcoded literals at [services/rag_service.py:473-477](../../services/rag_service.py#L473-L477) — but this is exactly the parameterization anti-pattern that Layer 2 is meant to eliminate. Fix: either `assert method in {"to_tsquery", "plainto_tsquery", "websearch_to_tsquery"}` immediately before the f-string, or branch into three literal SQL strings. One future refactor that reads `method` from a request parameter turns this into a SQL injection.
- [x] **Enforce admin password quality at user creation** *(added 2026-04-11 from QA pass)* — `logger.warning` if ADMIN_PASSWORD < 12 chars at boot. — [main.py:94-100](../../main.py#L94-L100) the lifespan admin-seed calls `auth_service.create_user(admin_email, admin_password, ...)` with whatever is in `ADMIN_PASSWORD`. Add a minimum-length check (≥12 chars) and log a `logger.warning` if the password appears low-entropy (e.g. dictionary word, all lowercase, no digits). Do not fail-fast — a weak dev admin is recoverable; a missing admin is not.
- [x] **Layer 3 — resource-level auth check** — gemeente claim verified against chunk payload in `logged_tool`; filtered silently.
- [x] **Layer 4 — output filter** `services/output_filter.py`:
  - Strip PII the user's scope doesn't grant access to
  - Strip internal IDs starting with `_internal_`
  - Truncate any field > 50K chars to prevent context bombing
  - **Snippet provenance verification** *(added 2026-04-11, triaged from [FEEDBACK_LOG.md 2026-04-11](../../brain/FEEDBACK_LOG.md))* — for every search hit, verify the returned snippet substring-matches the cited document's stored text (not just that the `document_id` exists). This catches the `doc 246823` failure mode where the snippet shown in the search result was not actually present in the document. Mismatches are logged to `mcp_audit_log` with `error_class='snippet_provenance_mismatch'`, the offending hit is dropped from the response, and the daily health email flags it. **Interacts with WS5a §Data integrity audit** — the root-cause fix for mismatches lives in the ingest pipeline (chunk→document_id attribution); the Layer 4 filter is the last-line defense against bad data escaping into LLM context.
- [ ] **Container hardening** *(added 2026-04-11 from QA pass)* — two [Dockerfile](../../Dockerfile) issues: (1) base image `FROM python:3.12-slim` at [Dockerfile:5](../../Dockerfile#L5) is not pinned to a digest or a patch version, so a rebuild can silently pick up a new base; (2) no `USER` directive, so the container runs as root. Fix: pin to a specific patch version (e.g. `python:3.12.10-slim-bookworm`) or a SHA256 digest; add `RUN useradd -m app && chown -R app:app /app` before `CMD` and `USER app`. Ensure the healthcheck curl command still works as the unprivileged user (it should — port 8000 is >1024).

### Audit log (~1 day)

FactSet rule: "log without capturing secrets."

- [x] **`mcp_audit_log` table** via Alembic migration `20260413_0007` — applied to production 2026-04-13:
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
- [x] **`services/audit_logger.py`** — exists; wired into `logged_tool` decorator. No secrets logged.
- [ ] **Daily summary** rendered at `/admin/mcp` — **deferred to v0.3.0** (data accumulating)
- [x] **Hash API tokens at rest** *(added 2026-04-11 from QA pass)* — [services/auth_service.py:192-196](../../services/auth_service.py#L192-L196) `create_api_token` inserts `raw_token` directly into `api_tokens.token` and [services/auth_service.py:206-232](../../services/auth_service.py#L206-L232) `validate_api_token` compares with a plain equality predicate. A DB dump or SQL-read vulnerability would leak every live token. Fix: add a `token_hash` column via the same Alembic migration that adds `mcp_audit_log`, hash tokens on insert (sha256 is fine — these are already cryptographically-random `secrets.token_urlsafe(48)` strings, not user passwords, so an HMAC or SHA-256 is the right choice; do not use bcrypt here — it's slow and unnecessary), and hash-then-compare on validate. Existing tokens must be invalidated on deploy (force regeneration via the MCP installer page). Coordinate with the `/admin/mcp` dashboard: the audit log row should include `api_token_id` (from `api_tokens.id`) so per-token usage is trackable even with hashed storage.
- [x] **Migrate `print()` error paths to `logger.exception`** *(added 2026-04-11 from QA pass)* — stdout is not captured by the audit-log / Kamal-log pipeline, so these failures are invisible in production:
  - [services/rag_service.py:529-531](../../services/rag_service.py#L529-L531) — chunk keyword search failure
  - [services/rag_service.py:549-550](../../services/rag_service.py#L549-L550) — `_get_chunk_questions` failure
  - [services/rag_service.py:255-256](../../services/rag_service.py#L255-L256) — reranking failure
  - [services/reranker.py:149,166](../../services/reranker.py#L149) — rate-limit backoff messages
  
  Replace every `print(f"...failed: {e}")` with `logger.exception("...")`. The audit-log "<5ms p50" gate already cares about observability overhead — keeping these in stdout breaks the MCP latency SLO tracking.
- [x] **Cap `_party_profile_cache` and `_party_lens_cache` in main.py** *(added 2026-04-11 from QA pass)* — replaced with `TTLCache(maxsize=500, ttl=3600)`. — [main.py:55-56](../../main.py#L55-L56) these module-level dicts have no TTL or eviction and will grow with every unique party-name variation seen. Replace with `cachetools.TTLCache(maxsize=500, ttl=3600)` or `functools.lru_cache(maxsize=...)`. Relevant to the WS4 "<5ms p50 audit log overhead" gate because cache bloat drives p99 latency spikes.

### Context primer tool (~1 day)

Figma's `create_design_system_rules` analogue.

- [x] **`get_neodemos_context() -> dict`** — zero-arg MCP tool returning a structured primer the LLM can read on first connect:
  ```json
  {
    "version": "0.2.0",
    "gemeenten": [
      {"name": "rotterdam", "mode": "full", "documents": 90000, "date_from": "2002-01-01", "date_to": "2026-04-10"}
    ],
    "document_types": ["notulen", "motie", "amendement", "raadsvoorstel", "raadsbrief", "jaarstukken", "voorjaarsnota", "begroting", "10-maandsrapportage", "agendapunt"],
    "council_composition": {
      "rotterdam": {
        "parties": ["Leefbaar Rotterdam", "GroenLinks-PvdA", "VVD", "..."],
        "current_coalition": [...],
        "total_seats": 45,
        "wethouders": [
          {"name": "Lansink Bastemeijer", "party": "VVD", "since": "2024-07-11", "portfolios": ["haven", "economie", "RTHA"]},
          {"name": "...", "party": "...", "since": "...", "portfolios": [...]}
        ],
        "coalition_history": [
          {"start": "2018-06-07", "end": "2022-05-29", "parties": ["Leefbaar Rotterdam", "VVD", "D66", "CDA", "GroenLinks", "PvdA", "CU-SGP"]},
          {"start": "2022-05-30", "end": null, "parties": ["..."]}
        ]
      }
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
  The `wethouders` array is the fix for LLM role-date hallucination *(added 2026-04-11)*: LLMs confidently guess political tenure from training data rather than looking it up — this is a data problem, not an instruction problem. The primer gives the LLM current roster facts at session start. `zoek_uitspraken_op_rol` is then correctly scoped to what it's actually for: full role history and period filtering. No "call this proactively" instruction needed. Generated from the `persons_roles` table at server boot — never hardcoded.

  The `coalition_history` array is the same-class fix for **time-varying coalition context during historical vote interpretation** *(added 2026-04-11)*. Failure case from the 2026-04-11 woningbouw session: the LLM interpreted the 2018 Tweebosbuurt stemming as "GroenLinks/PvdA stemden tegen" (opposition-framing), while both parties were *coalitiepartij* at that moment and stemden vóór hun eigen beleid. `current_coalition` alone cannot prevent this — the LLM needs coalition-status-at-time. `coalition_history` is a small timeline (one entry per college-periode) that lets the LLM mentally map any historical vote date to the coalition composition at that date without guessing from training data. Generate from `persons_roles` rows where `role = 'wethouder'`, grouped by college-periode start/end dates — same source as `wethouders`, different aggregation. Never hardcoded.
- [x] Tool description: "Call this first when you connect to NeoDemos. It tells you what gemeenten are available, what document types exist, and which tool sequences work for common questions. Cheap to call (<50ms)."

### Dependency hygiene (~0.5 day) *(added 2026-04-11 from QA pass)*

- [ ] **Pin every package in [requirements.txt](../../requirements.txt)** — 18 of 26 lines currently have no version constraint (fastapi, uvicorn, httpx, psycopg2-binary, pgvector, qdrant-client, requests, tqdm, etc.). The `openai` package missing from `requirements.txt` outage documented in [`CHANGELOG.md`](../../CHANGELOG.md) `[Unreleased] > Fixed` is the same class of bug. Pin to currently-installed versions via `pip freeze` → sanity-check → commit. Do this at the END of WS4, not the start — when the full dependency surface is known after the audit log, scoped OAuth, and tool registry work has landed.

### Rate limiting (~0.5 day)

- [x] **Per-user-per-minute** rate limiting — `services/mcp_rate_limiter.py`: 60/min authenticated (10/min expensive), 20/min public IP (5/min expensive). Sliding-window implementation.
- [ ] Anomaly-detection ML is **deferred to v0.3.0**

### Mistral Le Chat compatibility (~2 days) *(added 2026-04-11)*

**Why this is in WS4 and not a separate workstream:** the transport, OAuth, and tool-discipline work this WS already ships *is* the work needed for Le Chat. Marginal cost is small; strategic upside is large (mobile reach via iOS/Android, free-tier no-paywall AI, EU sovereignty story). See [`memory/project_le_chat_mcp.md`](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_le_chat_mcp.md) for the verified Le Chat MCP requirements.

**Cold-start summary so this section is self-contained:** Le Chat is the only major chat client whose MCP connectors ship on the **free tier** (announced 2025-09-02), supports **None / Bearer / OAuth 2.1 with Dynamic Client Registration**, mandates **`/mcp` path for streamable HTTP** (`/sse` is deprecated by Mistral), and requires **`401 + WWW-Authenticate` containing the `resource_metadata` URL** per RFC 9728. NeoDemos already meets all of these via [`mcp_server_v3.py:69`](../../mcp_server_v3.py#L69) (FastMCP `streamable-http` transport, default path `/mcp`) and [`services/mcp_oauth_provider.py`](../../services/mcp_oauth_provider.py) (OAuth 2.1 + DCR). Tool schemas were verified on 2026-04-11 to be free of `$id` / `$schema` / `$defs` / `$ref` (Mistral's grammar compiler rejects these — see [litellm#13389](https://github.com/BerriAI/litellm/pull/13389)), so no schema-stripping is required. The 401/WWW-Authenticate emission lives in `mcp/server/auth/middleware/bearer_auth.py:98-117` of MCP SDK 1.26 — verified present.

#### Verification before any other Le Chat work
- [ ] **End-to-end smoke test against a real Le Chat custom connector.** *(pending — prod deployed 2026-04-13, ready to run)* Add `https://mcp.neodemos.nl/mcp` via Le Chat → Intelligence → Connectors → + Add Connector → Custom MCP Connector. Verify in order: (1) the platform's auth-detection pings the URL and receives a `401` with `WWW-Authenticate: Bearer ..., resource_metadata="..."` — confirm independently with `curl -i https://mcp.neodemos.nl/mcp`; (2) DCR completes and a new row appears in `oauth_clients`; (3) authorization redirect lands back on Le Chat; (4) Le Chat lists all 13 NeoDemos tools; (5) `get_neodemos_context()` is called first (after WS4 §Context primer tool ships); (6) `zoek_raadshistorie("warmtenetten Leefbaar Rotterdam")` returns Dutch citations; (7) `vraag_begrotingsregel(gemeente='rotterdam', jaar=2025, programma='Veilig')` renders inline as a Markdown table; (8) clicking a `[Brondocument ↗]` link from §Source URLs opens the iBabs PDF. **Document every failure** in a new file `docs/integrations/le_chat.md` — that file becomes the public install guide for gemeenten.
- [ ] **MCP Inspector cross-check.** *(pending)* Before contacting Mistral support for any failure, run `npx @modelcontextprotocol/inspector https://mcp.neodemos.nl/mcp` and confirm the same flow works. If Inspector passes but Le Chat fails, the gap is in Le Chat's client and we file with Mistral; if both fail, fix our server first.
- [ ] **Pin `mcp[cli]` to a specific version** in [`requirements.txt`](../../requirements.txt) *(pending — part of broader §Dependency hygiene pass)* (currently `mcp[cli]>=1.0.0`). The `WWW-Authenticate` emission is load-bearing and an SDK regression there silently breaks Le Chat. Pinning protects against this. Coordinate with §Dependency hygiene below; this is one specific package that **must** be pinned even if the broader pin pass slips.

#### Public no-auth endpoint for the §2.1 wedge
- [x] **`https://mcp.neodemos.nl/public/mcp`** — live 2026-04-13. Second FastMCP instance, no auth, only registers tools whose `scopes ⊆ ["mcp", "search"]` from the WS4 tool registry. **One server file, one registry, two FastMCP instances** — do NOT fork [`mcp_server_v3.py`](../../mcp_server_v3.py). Implementation: extend `mcp_server_v3.py` to also instantiate a `_public_mcp = FastMCP(..., auth=None, auth_server_provider=None)` and decorate tools with a `@public` marker that registers them on both. Both servers run in the same process, mounted at different paths via Starlette routing in [`config/deploy.yml`](../../config/deploy.yml). This is the journalist/citizen path: paste one URL into Le Chat free, no login. Aligns with [V0_2_BEAT_MAAT_PLAN.md §2.1 public-AI-by-default constraint](../architecture/V0_2_BEAT_MAAT_PLAN.md). **Eligibility audit:** every existing tool in `mcp_server_v3.py` must be tagged `public=True`/`public=False` during the registry migration above; default `public=True` for all retrieval tools, `public=False` only for `vat_dossier_samen` (uses dossier IDs scoped to a user) and any future write tools.
- [x] **Per-IP rate limit on `/public/mcp`** — 20/min per IP, 5/min for expensive tools. `RateLimitMiddleware` applied via Starlette Mount composition.
- [x] **CORS:** `https://chat.mistral.ai` + wildcard on public endpoint only; authenticated `/mcp` not widened.

#### Output discipline for weaker models (folds into §AI-consumption descriptions)
The Mistral models behind Le Chat (Mistral Medium 3.1, Magistral Medium 1.2, Small 3.2 — picked per task by Le Chat's router) are demonstrably less robust than Claude Opus / GPT-4o at long-context reasoning, ambiguous tool selection, and faithful relay of long retrieval blobs. The mitigations below are *additive* to the description rewrites in §AI-consumption descriptions; they apply to all clients but are particularly load-bearing for Mistral.

- [x] **Server-side temporal-extraction fallback in MCP retrieval tools.** Move the existing `extract_temporal_filters` (currently only on the `/api/search` web route in [`main.py`](../../main.py)) into [`services/temporal_parser.py`](../../services/temporal_parser.py) and call it inside `zoek_raadshistorie` / `zoek_uitspraken` / `zoek_financieel` / `scan_breed` whenever the LLM omitted `datum_van`/`datum_tot` but the query string contains a Dutch temporal phrase ("vorig jaar", "sinds 2023", "afgelopen maanden", "recent", "eerder"). Mistral models forget to translate these reliably even when the `instructions` field at [`mcp_server_v3.py:75`](../../mcp_server_v3.py#L75) explicitly tells them to. This is the safety net that prevents a "vorig jaar" query returning chunks from 2018. Existing function in `main.py` is the source — extract, don't rewrite.
- [ ] **Pre-rendered Markdown answer skeletons for canonical question shapes** — `traceer_motie`, `vraag_begrotingsregel`, `vergelijk_partijen`, `vergelijk_begrotingsjaren`. The tool returns a near-final Markdown block with citations baked in (heading, structured table, "Bron: [doc ↗]" lines). The LLM only has to add framing prose and answer the user's specific phrasing. Helps Mistral relay correctly; doesn't hurt Claude (which edits more aggressively). Add a `format: Literal["skeleton","raw"] = "skeleton"` parameter to each canonical-shape tool. Default `skeleton`. Skeleton mode is the v0.2.0 ship; raw mode exists for debugging.
- [ ] **Soft tool-budget cap.** Le Chat does not publish a hard limit but the "too many tools" problem is real (Cursor: 40 hard cap; Copilot: 128 hard cap; each tool eats 550–1400 tokens of context). NeoDemos has 13 today and WS1+WS2+WS3+WS6 add ~6 more. **Action:** keep the total ≤ 25 in v0.2.0; if WS3 and WS6 push over, merge tools rather than split. Track in the registry and warn at startup if `len(REGISTRY) > 25`.

#### Installer & marketing
- [ ] **Le Chat installer card** in [`templates/mcp_installer.html`](../../templates/mcp_installer.html). *(pending)* Three pieces: (1) "Copy URL" button populating `https://mcp.neodemos.nl/mcp` for authenticated users and `https://mcp.neodemos.nl/public/mcp` for the public flow, (2) 4-step screenshot walkthrough mirroring [help.mistral.ai/393572](https://help.mistral.ai/en/articles/393572-configuring-a-custom-connector), (3) link to `docs/integrations/le_chat.md` for troubleshooting. Match the visual style of the existing Claude Desktop / Cursor cards.
- [ ] **Submit to Mistral connectors directory.** Mistral curates a directory at [help.mistral.ai/393505](https://help.mistral.ai/en/articles/393505-browsing-the-mcp-connectors-directory) (Notion, GitHub, Linear, Stripe, etc.). No public submission form yet; pitch by email to Mistral developer relations: *"NeoDemos — Civic intelligence for Dutch municipal councils. 90.000+ documents from Rotterdam, free public tier, OAuth 2.1, EU-hosted (Hetzner FSN)."* This is marketing, not engineering — hand off to whoever owns external comms but **must** be a checklist item in WS4 so it does not slip.
- [ ] **EU sovereignty one-liner** in `docs/integrations/le_chat.md`: French model (Mistral) talking to German-hosted RAG (Hetzner FSN) over Dutch council data. Zero US hop. This is the procurement-friendly framing for Dutch gemeenten that is structurally unavailable to Claude/ChatGPT integrations.

#### Future work — do NOT do in v0.2.0
- Mistral La Plateforme / `mistralai` Python SDK integration (calling Mistral models *from* NeoDemos) — only useful if we ever build an alt-frontend; not needed for Le Chat compatibility.
- Le Chat-specific tool packs or Memories integration — out of scope; stay within standard MCP spec.
- Contributing a NeoDemos demo to the Mistral cookbook — defer to v0.3.0 once `/public/mcp` has 30 days of stable traffic.

## Acceptance criteria

- [x] `services/mcp_tool_registry.py` exists — **20 tools** registered
- [x] All tool descriptions follow the AI-consumption template (with positive AND negative use cases)
- [x] Tool-collision check runs at server startup; no pair > 0.85 cosine
- [x] All 4 defense-in-depth layers implemented (Layer 1: scopes, Layer 2: param validation, Layer 3: gemeente claim, Layer 4: output filter)
- [x] `mcp_audit_log` table exists (migration `20260413_0007`, applied to prod); every tool call logged; no secrets in log
- [x] `get_neodemos_context()` tool exists and returns structured primer
- [x] OpenAPI spec auto-exported at `docs/api/mcp_openapi.json`
- [ ] `/admin/mcp` dashboard shows audit summary — **deferred to v0.3.0**
- [x] Rate limits enforced (60/min authenticated, 10/min expensive; 20/min public IP, 5/min expensive)
- [x] WS2 tools registered with conformant descriptions; WS1/WS3/WS6 to coordinate when those workstreams ship
- [ ] **Le Chat compatibility verified end-to-end** — both endpoints live; smoke test pending
- [ ] `mcp[cli]` pinned in `requirements.txt` — pending §Dependency hygiene pass
- [x] `docs/integrations/le_chat.md` exists
- [x] Tool count in registry ≤ 25 — currently **20**

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

**Shipped 2026-04-13 as v0.2.0-alpha.2.** Deployed via `kamal deploy` (web) + `kamal accessory boot mcp` (MCP server). Both containers live on Hetzner FSN.

**Registry:** 20 tools (13 original + `traceer_motie`, `vergelijk_partijen`, `lees_fragmenten_batch` + WS2 financial tools). All descriptions follow the AI-consumption template. Tool count well within the 25-tool soft cap.

**Infrastructure shipped:**
- `services/mcp_tool_registry.py` — 20 ToolSpec entries, OpenAPI export
- `services/mcp_tool_uniqueness.py` — startup cosine collision check
- `services/mcp_validation.py` — Layer 2 param validation
- `services/mcp_rate_limiter.py` — sliding-window rate limiting middleware
- `services/audit_logger.py` — wired into `logged_tool` decorator
- `services/output_filter.py` — Layer 4 output filter (context bomb, PII, snippet provenance)
- `services/temporal_parser.py` — temporal fallback for all 4 retrieval tools
- `alembic/versions/20260413_0007_mcp_audit_log.py` — applied to prod

**Endpoints live:**
- `https://mcp.neodemos.nl/mcp` — authenticated OAuth 2.1, 20 tools
- `https://mcp.neodemos.nl/public/mcp` — unauthenticated, all retrieval tools, rate-limited 20/min per IP, CORS open for `chat.mistral.ai`

**All 8 v0.2.0 blockers resolved.** Regression test `tests/mcp/test_zoek_moties.py` passes.

**Pending (not blocking v0.2.0):**
- Le Chat smoke test (8-step checklist in §Verification — prod is ready, test is manual)
- Le Chat installer card in `mcp_installer.html`
- `mcp[cli]` pinned in `requirements.txt` (part of §Dependency hygiene)
- `/admin/mcp` audit dashboard (deferred to v0.3.0)
- Dockerfile hardening (USER directive + base image digest pin)

**Eval delta:** not yet measured — run `rag_evaluator` benchmark before/after to quantify description rewrite impact on completeness scores.

---

## Post-ship reliability follow-ups (opened 2026-04-14)

Two items added after today's MCP outages — one caused by a `/mcp` routing bug, one caused by an `ALTER TABLE users` holding a lock that blocked every `validate_api_token` call. Background in memory `feedback_mcp_uptime.md` and in the deploy runbook incident log.

Recommended execution order: ship (1) first (pure code change, zero-risk), then (2). Both are individually zero-downtime once deployed.

### (1) Statement timeout on the auth path

**Why:** When a schema change or long-running query locks the `users` table, every MCP request hangs instead of failing fast. Today that took kamal-proxy from healthy to 504 within minutes because the uvicorn event loop stalled on the blocked `validate_api_token` queries. A 3 s `statement_timeout` turns that into a handful of 500s to individual callers, instead of a service-wide outage.

- [ ] Add `SET LOCAL statement_timeout = '3s'` (or equivalent psycopg `options`) to the DB connection path used by `services/auth_service.py::AuthService.validate_api_token` and `validate_session`. Keep the rest of the app on its current default.
- [ ] Confirm no legitimate auth query exceeds 3 s under normal load (check `logs/mcp_queries.jsonl` p99 latency for auth-gated tools; indexed PK lookup on `api_tokens.token_hash` should be single-digit ms).
- [ ] Add a regression note in the file referencing `feedback_mcp_uptime.md` so the next editor knows why it's there.
- [ ] Ship via `kamal deploy` — blue-green, zero downtime.

**Files:** `services/auth_service.py` (primary), possibly `services/db_pool.py` if the timeout is applied per-pool rather than per-query.

### (2) MCP service-role migration (accessory → service role in Kamal)

**Why:** Today the MCP server is a Kamal **accessory**. `kamal accessory reboot mcp` is stop-then-start — ~10–15 s of user-visible downtime on every MCP deploy. Web has zero-downtime blue-green because it's a Kamal **service** with a `proxy:` block. Promoting MCP to a second service role gives it the same treatment: new container boots alongside old, kamal-proxy atomically swaps traffic when `/up` passes, old drains. Rationale in memory `feedback_deploy_window.md` and `feedback_mcp_uptime.md`.

**Config is already staged** in `config/deploy.yml` (edited 2026-04-14, not yet deployed). Deploy runbook in `.claude/commands/deploy.md` has already been rewritten to describe MCP-as-service. Nothing more to edit before shipping.

- [ ] Run `kamal deploy -r mcp` from the project root (after Colima is up). Kamal builds the image (cached unless code changed), boots `neodemos-mcp-<sha>` as a service role, waits for `/up`, then registers `mcp.neodemos.nl` + `mcp.neodemos.eu` against the new target via kamal-proxy.
- [ ] Verify kamal-proxy handled the hostname hand-off cleanly:
      `curl -sI https://mcp.neodemos.nl/mcp` → expect `HTTP/2 401` within a second (the OAuth challenge).
- [ ] Clean up the orphaned old accessory container — a one-time violation of the "no manual docker on host" rule, justified because Kamal no longer tracks it:
      `ssh -i ~/.ssh/neodemos_ed25519 deploy@178.104.137.168 'sudo docker stop neodemos-mcp && sudo docker rm neodemos-mcp'`.
- [ ] Confirm docker ps no longer lists the accessory: only `neodemos-mcp-<sha>` should remain.
- [ ] If the hostname swap does NOT go zero-downtime (brief 502s from kamal-proxy while re-registering), log the observed outage in the deploy runbook incident section so we know what the actual transition cost is for future reference.

**Risk:** low. Both hostnames are already TLS-issued; kamal-proxy just rebinds the target. In the worst case, a brief 5–10 s blip during re-registration — still strictly better than the 10–15 s that every `accessory reboot` costs today.

**Rollback:** put `mcp:` back in the `accessories:` block, run `kamal accessory boot mcp`. The old accessory config is preserved in git history (commit range around `bad7d5d`…`97f2fb5`).

### (3) Cross-process Jina token budget with priority tiers *(added 2026-04-14)*

**Why:** Today every Python process (MCP server, WS6 backfill, synthesizer, future multi-gemeente jobs) runs its own local `_TokenBucket` in [`services/reranker.py:127`](../../services/reranker.py#L127) with the same default ceiling (`JINA_TPM_BUDGET=1800000`). They all independently push up to that cap, then collide at Jina's actual **2M TPM hard limit** and start 429-retrying. Retry storms across workers caused WS6 hangs on 2026-04-14 — and any concurrent MCP query is starved behind the backfill. We need coordination *across processes* with priority, not bigger per-process budgets.

**Tactical fix shipped 2026-04-14 (Tier 1):** WS6 backfill now runs with `JINA_TPM_BUDGET=1000000` (half), leaving MCP with ~800K TPM headroom. Simple env-var isolation, no shared state. *Does not solve the general problem* — breaks if we add another background job, and still doesn't give MCP priority when Jina itself is the bottleneck.

**Proper fix (Tier 3):** distributed token bucket in Redis with priority queue.

- [ ] Add Redis to the Kamal stack as an accessory (if not already present for other uses). Single instance, local-only, no persistence needed for this use case.
- [ ] New module `services/jina_budget.py` — Redis-backed token bucket, same `acquire(tokens, priority)` signature as the current local `_TokenBucket`.
  - Rolling 60-second window, implemented with Redis sorted set or Lua script.
  - Budget configured to Jina's actual ceiling (~1.8M TPM, with 10% headroom for unaccounted overhead).
  - `priority: "interactive" | "background"`. Background acquires block when current spend > 70% of budget; interactive always acquires.
  - Fail-soft: on Redis unavailable, fall back to the current per-process `_TokenBucket` so a Redis outage doesn't take down rerank entirely.
- [ ] Refactor `services/reranker.py` to route through `jina_budget.acquire()` instead of the local bucket.
- [ ] Every MCP tool that calls the reranker passes `priority="interactive"`. The Summarizer, WS6 scripts, and any nightly/batch job passes `priority="background"`.
- [ ] Instrument: add `/api/admin/jina-budget` endpoint (admin-only) returning current window spend, interactive vs background split, and throttle events in the last hour. Useful for capacity planning when we add gemeenten.

**Files:** `services/jina_budget.py` (new), `services/reranker.py` (route calls), `services/summarizer.py` (tag priority), `scripts/nightly/06b_compute_summaries.py` (tag priority), `mcp_server_v3.py` or `routes/api.py` (admin endpoint), `config/deploy.yml` (Redis accessory).

**Rollout:** Ship behind a feature flag `JINA_BUDGET_BACKEND=redis|local` with default `local` until the new path has 48h of production traffic. Flip to `redis` after soak.

**Risk:** medium. Gets us cross-process priority right but introduces a Redis dependency on the hot retrieval path. Mitigate with fail-soft fallback to local bucket if Redis is unreachable.

**When to ship:** After v0.2.0 press moment. Current Tier 1 workaround (env-var budget split) is sufficient for single-gemeente Rotterdam operation. Tier 3 becomes necessary when we onboard additional gemeenten in v0.2.1+ where background jobs multiply.
