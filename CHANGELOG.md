# Changelog

All notable changes to NeoDemos will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Version is the single source of truth in [`VERSION`](VERSION); see [`docs/VERSIONING.md`](docs/VERSIONING.md) for the release process.

---

## [Unreleased]

### Added (WS8f v2 — 2026-04-13)
- **GrapeJS component library** (`static/admin-editor/components.js`) — 27 custom component types with `nd-` prefix using real site CSS classes: `nd-subpage-hero`, `nd-section` (variants), `nd-founder-quote`, `nd-audience-grid`/`-card`, `nd-sovereignty-grid`/`-card`, `nd-stat-grid`/`-card`, `nd-eval-grid`/`-card`, `nd-checklist`, `nd-architecture-steps`/`-step`, `nd-methodology-steps`/`-step`, `nd-compatibility-list`/`-badge`, `nd-btn`, `nd-cta-section`, `nd-testimonial`, free-edit leaves
- **Traits panel** for structured content editing — form inputs (title, subtitle, image URL, CTA label/URL, variant select) instead of free contentEditable
- **Structural locking** — `draggable: false`, `removable: false`, `selectable: false` on internal wrappers; `propagate: ['stylable']` for design-token lockdown; `droppable` containment rules (audience-card only drops into audience-grid)
- **Editor canvas CSS injection** — `canvas.styles: ['/static/dist/main.css']` so blocks render with real site styling in the GrapeJS iframe
- **Template auto-loader** (`GET /admin/api/page/{slug}/template`) — renders current Jinja template with `content()` defaults, strips chrome, returns content-only HTML for editor starter; "Laad sjabloon" button + first-open auto-populate
- **GrapeJS 0.22.15** — upgraded from 0.21.13 (latest stable, DataSources API added upstream, no breaking changes for us)

### Added (WS8f v1 — 2026-04-13)
- **Admin content management** (`/admin/content`) — edit ~95 content items across landing, over, technologie, methodologie, footer sections without touching code
- **GrapeJS visual editor** (`/admin/editor/{slug}`) — drag-and-drop layout editing for /, /over, /technologie, /methodologie with draft/publish workflow
- **Admin panel restructure** — sidebar navigation with Dashboard, Inhoud, Gebruikers, Tokens, Pagina's, Instellingen
- **Subscription tier scaffolding** — `subscription_tier` column on users (default `free_beta`), beta messaging on registration
- Alembic migrations `0008` (site_content + site_pages tables) and `0009` (subscription columns)
- Services: `ContentService` (60s TTL cache), `PageService`
- Seed script `scripts/seed_site_content.py` (95 content items)
- `bleach==6.1.0` for GrapeJS HTML sanitization

### Changed (WS8f — 2026-04-13)
- **main.py split** — 1,508-line monolith refactored into `main.py` (250 lines) + `app_state.py` (145) + `routes/auth.py` (343) + `routes/admin.py` + `routes/pages.py` (194) + `routes/api.py` (687)
- **CSS restructure** — 4,037-line `main.css` split into 12 modules with Tailwind v4 `@layer` directives; build output functionally identical (±0.05%)
- **Templates** — 95 `{{ content() }}` calls across 5 templates (search.html, over.html, technologie.html, methodologie.html, _footer.html); every call has hardcoded fallback so site renders with empty DB
- **Page routes** — `/`, `/over`, `/technologie`, `/methodologie` now check for published GrapeJS page and render stored HTML when available, falling back to Jinja template
- **CSP** — `/admin/editor/*` paths get relaxed CSP (unpkg.com for GrapeJS, `unsafe-eval`); public routes unchanged

### Removed (WS8f — 2026-04-13)
- `static/css/style.css` (958 lines, legacy, only loaded by dead `index.html`)
- `templates/index.html` (no route served it)
- `templates/admin.html` (decomposed into `templates/admin/*.html`)

---

Planned for **v0.2.0 — "GraphRAG + Trustworthy Numbers"**.
Full plan: [`docs/architecture/V0_2_BEAT_MAAT_PLAN.md`](docs/architecture/V0_2_BEAT_MAAT_PLAN.md).

### Fixed (in working tree, awaiting deploy)
- **`openai` package missing from `requirements.txt`** ([requirements.txt](requirements.txt)) — `NebiusEmbedder.__init__` lazily imports `openai` ([services/embedding.py:54](services/embedding.py#L54)) but the package was never declared, so the production Docker image had no `openai` installed. Caused **every code path that creates a `RAGService()`** to crash with `ModuleNotFoundError: No module named 'openai'`. This silently broke debate prep, deep research, and any vector-search flow on production. Standard PostgreSQL FTS search was unaffected, which is why it went undetected. Reproduced via `GET /api/search?q=woningbouw&mode=debate&deep=true` returning HTTP 500 in 0.7s. Pinned to `openai>=2.31.0`.
- **OAuth `create_authorization_code` not awaited** ([main.py:478](main.py#L478)) — the consent submit handler called the async provider method without `await`, so no auth code was ever persisted. The OAuth flow could never complete end-to-end. Discovered while building the MCP installer page.
- **OAuth `issuer_url` pointed to wrong host** ([mcp_server_v3.py:51-55](mcp_server_v3.py#L51-L55)) — the MCP SDK's discovery endpoint and `WWW-Authenticate` header advertised `https://neodemos.nl/authorize`, `/token`, `/register`, and `/.well-known/oauth-protected-resource`, but those routes only exist on `mcp.neodemos.nl` (where the SDK actually mounts them). All four advertised URLs returned 404. Verified via smoke test (`curl https://mcp.neodemos.nl/.well-known/oauth-authorization-server`). Fixed by introducing `MCP_BASE_URL` env var separate from `NEODEMOS_BASE_URL`.
- **`mcp_access` defaulted to `FALSE` for new users** ([services/auth_service.py:33-51](services/auth_service.py#L33-L51)) — every freshly registered user got 401 from `load_access_token`'s `WHERE u.mcp_access = TRUE` filter ([services/mcp_oauth_provider.py:294-296](services/mcp_oauth_provider.py#L294-L296)) until an admin manually flipped the column. Now defaults `True` on `create_user()`.
- **`/` route required login** ([main.py:559-568](main.py#L559-L568)) — the search page was gated behind `require_login`, so anonymous visitors couldn't see anything (no public landing page possible). Made public; the template branches on `{% if not user %}` to show landing content only to logged-out visitors.
- **Logo was a `<div>`, not a link** ([templates/base.html:17](templates/base.html#L17)) — clicking the NeoDemos logo from any subpage did nothing. Now an `<a href="/">`.
- **Anonymous nav had no "Zoeken" link** ([templates/base.html](templates/base.html)) — once `/` became public, logged-out visitors had no way to navigate back to search from the installer or login page. Added `Zoeken` to the anonymous nav.
- **Logout button used a 200-character inline `style` attribute** ([templates/base.html:30](templates/base.html#L30)) — code smell. Extracted to `.nav-link-button` class in [static/css/style.css](static/css/style.css).
- **Search silently rejected queries < 3 chars** ([templates/search.html:535-546](templates/search.html#L535-L546)) — clicking "Zoek" with a short query did nothing, no user feedback. Now shows `Voer een zoekterm in.` / `Minimaal 3 tekens om te zoeken.`
- **Typo `DIRECHTE KOPPELING`** in @-mention dropdown ([templates/search.html:114](templates/search.html#L114)) — should be `DIRECTE KOPPELING`.

### Added (in working tree, awaiting deploy)
- **Public landing page** at `/` ([templates/search.html](templates/search.html)) — search box + about block + FactSet-inspired MCP marketing section (eyebrow tag, plain-language MCP explainer, 4-feature grid, example questions block, CTAs to installer + register, client pills). Logged-in users see clean search-only experience via `{% if not user %}` branching.
- **MCP installer page redesign** ([templates/mcp_installer.html](templates/mcp_installer.html)) — two-tab UI: OAuth (zero-config, paste URL into Claude Desktop) and API token (one-click generate). Sub-tabs for Claude Desktop / Claude Code CLI / ChatGPT in each.
- **Auto API token generation endpoint** at `POST /api/mcp/generate-token` ([main.py:515-561](main.py#L515-L561)) — returns the raw token plus pre-filled `claude_desktop_config` JSON and `claude mcp add` CLI command. Auto-grants `mcp_access` if not set.
- **`MCP_BASE_URL` env var** plumbed through [config/deploy.yml](config/deploy.yml) and [.env.example](.env.example) — separates the MCP host (`mcp.neodemos.nl`) from the web host (`neodemos.nl`) so OAuth discovery URLs match where the SDK actually mounts them.

### Known issues
See [open bugs](https://github.com/NeoDemos/neodemos-tracker/issues?q=is%3Aopen+label%3Akind%2Fbug) on the internal tracker (private).

### Added (planned)
- **GraphRAG retrieval** — `services/graph_retrieval.py`, 5th parallel retrieval stream, entity-based Qdrant pre-filtering
- **MCP tool `traceer_motie`** — full motie → indieners → vote → outcome → linked notulen walk
- **MCP tool `vergelijk_partijen`** — side-by-side party position retrieval over a topic
- **Structured financial retrieval** — `financial_lines` Postgres table populated from Docling `table_json` blobs
- **MCP tool `vraag_begrotingsregel`** — exact line-item lookup with verification token (zero-paraphrase contract)
- **MCP tool `vergelijk_begrotingsjaren`** — time-series budget comparison with absolute + percentage delta
- **Document journey view** — `document_journeys` Postgres view + `traceer_document` MCP tool
- **MCP tool registry** — `services/mcp_tool_registry.py` with FactSet-style metadata, scopes, output schemas
- **MCP context primer** — `get_neodemos_context()` zero-arg tool returning gemeenten, coverage, taxonomy, recommended sequences
- **MCP tool-collision detection** — embedding-based uniqueness scoring at startup
- **MCP audit log** — `mcp_audit_log` table; never logs secrets/tokens/raw params
- **Tool-level OAuth scopes** with parameter validation and output filtering (FactSet defense-in-depth)
- **Nightly ingestion job graph** — 7-step pipeline with Postgres-backed state, advisory locks, dead-letter queue, smoke test, daily health email
- **Admin pipeline view** at `/admin/pipeline` showing per-step health
- **Source-spans-only summarization** — `services/summarizer.py` with sentence-level verification pass
- **Per-document cached summaries** (`summary_short`, `summary_long`, `themes`)
- **MCP tool `vat_document_samen`** — explicit summarization, on-demand
- **Verification badges** in UI (`✅ verified` / `⚠️ partial`) for every generated summary

### Changed (planned)
- Folds the previously-planned "v0.2.0 Flair NER + Gemini enrichment" into Workstream 1 of [`V0_2_BEAT_MAAT_PLAN.md`](docs/architecture/V0_2_BEAT_MAAT_PLAN.md) — these are now prerequisites for GraphRAG, not a separate release
- `services/rag_service.py` retrieval fan-out: 4 streams → 5 streams (adds `graph_walk`)
- Old `analyseer_agendapunt` flow consolidated into the new `summarizer.py` module
- All MCP tool descriptions rewritten "for AI consumption, not human documentation" (FactSet rule)

### Eval gate (must pass before tag)
- Completeness ≥ 3.5 (from 2.75 baseline)
- Faithfulness ≥ 4.5 (no regression)
- Numeric accuracy on 30-question financial benchmark = 100%
- 14 consecutive days of clean nightly runs on Rotterdam
- Source-spans-only summaries pass strip-test on 50 random documents

### Deferred to v0.2.1
- Multi-portal connectors (`pipeline/sources/notubiz.py`, `go.py`, `ori_fallback.py`) in **search-only mode** for 5 ORI-fallback gemeenten
- Journey UI route + `templates/journey.html`
- HLS webcast player with `?t=<seconds>` deep links

### Deferred to v0.3.0
- TypeScript codegen for MCP tools + `@neodemos/mcp-tools` npm package (Anthropic Code Execution with MCP pattern)
- Anomaly-detection rate limiting
- ChatGPT and Perplexity MCP registration
- Promote 2 of the 5 search-only gemeenten to full mode

---

## [0.1.0] — 2026-04-08

### Added
- 12 MCP tools (Dutch) for Rotterdam council data retrieval ([`mcp_server_v3.py`](mcp_server_v3.py))
- Hybrid search: BM25 (dual-dictionary dutch+simple) + dense vector (Qwen3-8B 4096D)
- Jina Reranker v3 final-stage reranking
- Metadata enrichment on all 1.6M chunks: `section_topic`, `key_entities`, `vote_outcome`, `vote_counts`, `indieners`
- Knowledge graph Layer 1: 57K edges (`DIENT_IN`, `AANGENOMEN`/`VERWORPEN`, `LID_VAN`, `STEMT_VOOR`/`STEMT_TEGEN`)
- Politician registry: 228 records with aliases
- Domain gazetteer: 2,217 entities
- 881K cleaned entities, 3.3M entity-to-chunk mentions
- OAuth 2.1 user authorization for HTTP/SSE transports ([`services/mcp_oauth_provider.py`](services/mcp_oauth_provider.py))
- Web frontend with `Zoeken` and `NeoDemos Analyse` functions
- Hetzner deployment (`docker-compose.prod.yml` with postgres, qdrant, web, mcp, caddy)
- 15-minute scheduled document refresh ([`main.py:69`](main.py#L69))
- 24-hour scheduled session cleanup ([`main.py:112`](main.py#L112))
- RAG eval framework: 46 questions, Gemini-as-judge, 203 results in [`rag_evaluator/results/results.csv`](rag_evaluator/results/results.csv)

### Known limitations at 0.1.0
- `answerable_questions` per chunk not yet generated (LLM pass pending)
- Flair NER not yet run (key_entities coverage stuck at ~28%)
- No graph retrieval service (KG exists but is not queried at retrieval time)
- No semantic relationships (`HEEFT_BUDGET`, `BETREFT_WIJK`, `SPREEKT_OVER`)
- Single municipality (Rotterdam only)
- No nightly end-to-end ingestion automation (refresh only, not full pipeline)
- No financial line-item structured table (numbers only available via text RAG)
- No document-journey view
