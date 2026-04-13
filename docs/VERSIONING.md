# NeoDemos Versioning Plan

## How Versioning Works

**Single source of truth:** `VERSION` file in project root (plain text, one line: `0.1.0`).

**Version module:** `neodemos_version.py` reads `VERSION` and exports:
- `__version__` — semver string (`"0.1.0"`)
- `VERSION_LABEL` — display label (`"v0.1.0"`)
- `DISPLAY_NAME` — product name with stage (`"NeoDemos (alpha)"`)
- `STAGE` — release stage (`"alpha"`, `"beta"`, `"rc"`, or `""` for GA)

**Where version is used:**
- MCP server name shown to Claude/ChatGPT/Perplexity clients
- MCP server instructions (tells the LLM what it's connected to)
- Claude Desktop config key: `"NeoDemos (alpha)"`
- Docker image tags (future)
- API response headers (future)

**To bump version:** Edit `VERSION` file and `STAGE` in `neodemos_version.py`. That's it.

---

## Release Stages

| Stage | Meaning | Who uses it |
|-------|---------|-------------|
| `alpha` | Active development, features incomplete, data may change | Invited testers, max. 5 |
| `beta` | Core features work, external testers invited, data stable | Invited testers, max. 30 |
| `rc` | Release candidate, feature-frozen, bug fixes only | Wider test group, max. 100 |
| _(empty)_ | General availability (GA) = v1.0 | Public |

---

## Version Roadmap

### v0.1.0 (alpha) — Current
_Released: 2026-04-08_

**What's in it:**
- 12 MCP tools (Dutch) for Rotterdam council data retrieval
- Hybrid search: BM25 (dual-dictionary dutch+simple) + vector (Qwen3-8B 4096D)
- Jina v3 reranking
- Metadata enrichment: section_topic, key_entities, vote_outcome, vote_counts, indieners on all 1.6M chunks
- Knowledge graph Layer 1: 57K edges (DIENT_IN, AANGENOMEN/VERWORPEN, LID_VAN, STEMT_VOOR/TEGEN)
- 228 politicians in registry, 2,217 domain entities in gazetteer
- 881K cleaned entities, 3.3M entity-to-chunk mentions
- User Authorization, using OAuth
- Web Front-End with 'Zoeken' and 'NeoDemos' analyse functions

**What's missing:**
- answerable_questions per chunk (needs LLM)
- Flair NER for richer entity extraction
- Graph retrieval service (multi-hop queries)
- Semantic relationships (HEEFT_BUDGET, BETREFT_WIJK, etc.)

### v0.2.0 (alpha) — GraphRAG + Trustworthy Numbers
_Target: 2026-04-24 (2 weeks from 2026-04-10 kickoff)_

**Strategic reorientation (2026-04-10):** v0.2.0 is now scoped to ship the highest-impact features that structurally beat AethiQs MAAT. The previously-planned "Flair NER + Gemini enrichment" items are folded into Workstream 1 below as prerequisites for GraphRAG, not a separate release.

**Full plan:** [`docs/architecture/V0_2_BEAT_MAAT_PLAN.md`](architecture/V0_2_BEAT_MAAT_PLAN.md)

**Workstreams in v0.2.0:** *(updated 2026-04-13)*
- [ ] **WS1 GraphRAG** — Flair NER + Gemini enrichment + ~500K KG edges + `services/graph_retrieval.py` + 5th retrieval stream + `traceer_motie` + `vergelijk_partijen` MCP tools. **Blocked** — waiting on WS7, WS10, WS11, WS12 to finish (enriching garbled/incomplete corpus wastes Gemini spend).
- [x] **WS2 Trustworthy financial** — `financial_lines` Postgres table + `vraag_begrotingsregel` + `vergelijk_begrotingsjaren` + verification token (zero-paraphrase contract on euros). *(shipped 2026-04-12: 61,182 financial_lines, 100% benchmark accuracy)*
- [ ] **WS2b IV3 taakveld FK backfill** — wire `programma_aliases` lookup into extractor's `_assign_iv3` step; backfill `financial_lines.iv3_taakveld` for all 61K rows. See [`WS2b_IV3_TAAKVELD.md`](../handoffs/WS2b_IV3_TAAKVELD.md).
- [ ] **WS3 Document journey** — `document_journeys` view + `traceer_document` MCP tool (UI deferred to v0.2.1). Blocked on WS1 Phase A.
- [ ] **WS4 Best-in-class MCP** — tool registry + `get_neodemos_context` primer + tool-collision detection + scoped OAuth + audit log + parameter/output filters (FactSet defense-in-depth)
- [ ] **WS5 Reliable nightly ingest** — 7-step idempotent job graph + advisory locks + smoke test + admin dashboard (Rotterdam only)
- [ ] **WS6 Source-spans-only summarization** — `services/summarizer.py` + verification badges + cached per-document summaries
- [ ] **WS7 OCR recovery** — Re-OCR 2,700 garbled moties/amendementen via Docling, BM25 hit rate 77.5% → ≥95%. *(in progress — Dennis running)*. **Must finish before WS1 Phase A.**
- [x] **WS8 Frontend redesign** — Design tokens (Tailwind v4), landing page, calendar list view, subpages (over/technologie/methodologie), polish *(WS8a-e done 2026-04-12)*
- [ ] **WS8f Admin panel + content management** — `site_content` + `site_pages` tables, structured form editor at `/admin/content` (68 editable items), GrapeJS visual page builder at `/admin/editor/<slug>`, CSS restructure (Tailwind v4 `@layer` modules), `main.py` router split (→ 4 modules), subscription tier scaffolding (`free_beta`)
- [ ] **WS9 Web intelligence** — Sonnet + tool_use orchestrator, SSE streaming, auto-detect AI vs keyword search *(local implementation done 2026-04-12, needs production deploy + rate limiting)*
- [ ] **WS10 Table-rich extraction** — Docling layout pass for 1,336 table-rich documents *(in progress — classifier + converters done, backfill pending)*
- [ ] **WS11 Corpus completeness** — ORI gap backfill (~2,756 schriftelijke vragen) + metadata backfill (753 docs with NULL doc_classification) + municipality/source columns *(in progress — Dennis running)*
- [ ] **WS12 Virtual notulen backfill** — Promote 2025 virtual notulen to production, backfill 2018-2024 (661 meetings), Whisper API migration *(in progress — Dennis running)*

**Eval gate (must pass before tag):**
- Completeness ≥ 3.5 (from 2.75 baseline)
- Faithfulness ≥ 4.5 (no regression)
- Numeric accuracy on 30-question financial benchmark = 100%
- Nightly pipeline deployed and running (14-day clean streak certified in v0.2.1)
- Source-spans-only summaries pass strip-test on 50 random documents

### v0.2.1 (alpha) — Public Face
_Target: 2026-05-08 (2 weeks after v0.2.0 — nightly pipeline must accumulate 14 clean days from v0.2.0 deploy)_

_Renamed from "Search Beyond Rotterdam" on 2026-04-11 after Archibot competitive review: the public landing pages (`/publiek`, `/eval`, `/coverage`, `/governance`, `/mcp`) are now the centerpiece of this release. Multi-portal connectors ship alongside them but are no longer the headline._

- [ ] **WS13 Multi-gemeente pipeline** — `services/tenant_config.py` + `pipeline/sources/` adapter package + gemeente-configurable `IBabsService` + `onboard_gemeente.py` server-side script. **Discovery (2026-04-13):** 3 of 5 planned expansion cities (Middelburg, Zoetermeer, Enschede) are iBabs; **Apeldoorn and Maastricht are Parlaeus** (ORI-only until v0.3.0 native adapter — HEAD probes return 200 on iBabs domain but ORI `original_url` is authoritative). `scripts/discover_gemeente.py` + `scripts/build_municipalities_index.py` already ship and work. Registry: `data/municipalities_index.json` (309 municipalities, backend detected from ORI `original_url`, Phase 1/2/3 roadmap). See [`WS13_MULTI_GEMEENTE_PIPELINE.md`](../handoffs/WS13_MULTI_GEMEENTE_PIPELINE.md).
- [ ] **Full-mode ingestion** for Middelburg, Zoetermeer, Enschede via iBabs (iBabs gives full document text, meeting structure, VTT); Apeldoorn + Maastricht via ORI-only fallback (Parlaeus native adapter is v0.3.0 scope)
- [ ] **Middelburg press launch** *(Dennis has contact there — this is the press-moment city)* — Portal: **Notubiz** via ORI (`middelburg.raadsinformatie.nl`). **Verified 2026-04-13:** ORI index `ori_middelburg_20250426193224` has **28,434 MediaObject docs** (raw index count 45,835 includes deleted Lucene segments), 1,049 financial doc hits including Programmabegroting 2026-2029 and jaarstukken. PDFs served from `api.notubiz.nl/document/...` — publicly accessible (HTTP 200, no auth, CORS open). **No native Notubiz adapter needed.** Full path: ORI API → `original_url` → download PDF → Docling → `financial_ingestor.py` → `financial_lines` with `gemeente='middelburg'`. Financial counter-demo is **v0.2.1 scope** (same effort as Waalwijk). Activate Dennis's contact for press outreach once first begroting query is working.
- [ ] **Waalwijk counter-demo** *(quiet — no press activation needed)* — **financial data only**: ingest begroting 2025 + jaarstukken 2024 from `waalwijk.bestuurlijkeinformatie.nl` (iBabs, same scraper as Rotterdam). Mention in press pitches as proof of breadth, but Middelburg is the public-facing story. Full brief in master plan §4.
- [ ] Document journey UI: `/journey/{id}` route + `templates/journey.html` with vertical timeline (backend `traceer_document` from v0.2.0 powers this)
- [ ] HLS webcast player (`templates/meeting_player.html`) accepting `?t=<seconds>`
- [ ] Citation upgrade: every transcript-derived chunk gets `[▶ MM:SS]` deep-link in `_format_chunks_v3`
- [ ] **`neodemos.nl/publiek`** — anonymous landing page, zero-login AI search + summarize + traceer (modeled on Archibot's public dashboard, direct wedge vs MAAT paywall)
- [ ] **`neodemos.nl/eval`** — public eval scoreboard (*promoted from v0.4*) with ≥2 named baseline comparators (Gemini Flash web grounding, ChatGPT-4 web search) and a named human evaluator; live precision / faithfulness / completeness / numeric-accuracy + per-question source-chunk trace
- [ ] **`neodemos.nl/mcp`** — public MCP catalog with "Try in Claude Desktop" buttons (WS4 §6.5)
- [ ] **`neodemos.nl/coverage`** — public OCR-quality/coverage dashboard (WS5 §7.1): indexed, rejected (by reason), pending reprocess per gemeente
- [ ] **`neodemos.nl/governance`** — one-pager: models in use, data residency, training-data policy, refusal policy, eval methodology link, incident history (removes procurement-questionnaire friction)
- [ ] **Public-AI audit** — every endpoint against the §2.1 constraint (see V0_2_BEAT_MAAT_PLAN.md)

**Eval gate (must pass before tag):**
- 14 consecutive days of clean nightly runs on Rotterdam (clock starts at v0.2.0 deploy)
- All 5 public landing pages render and link correctly; `/eval` has run at least one full benchmark pass against each named comparator

### v0.3.0 (beta) — Open MCP Surface + Anchor Municipal Connectors
_Target: 2026-06-05 (4 weeks after v0.2.1)_

- [ ] TypeScript codegen for MCP tools — `@neodemos/mcp-tools` published to npm (generated from `services/mcp_tool_registry.py`; requires npm org setup + CI publish pipeline)
- [ ] Anthropic [Code Execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) example workflows
- [ ] Anomaly-detection rate limiting (FactSet pattern)
- [ ] Promote 2 of the 5 search-only gemeenten to full mode (KG + financial + journey) — **data-pipeline-bound**: Flair NER + Gemini enrichment + KG build for each gemeente takes 3-5 days compute; schedule these runs early in the sprint
- [ ] First external testers onboarded (≤5; requires scoped OAuth from WS4, onboarding guide, and rate limits in place)
- [ ] Developer documentation started: tool reference + quickstart guide (needed before testers arrive)
- [ ] **Native Parlaeus adapter** `pipeline/sources/parlaeus.py` *(added 2026-04-11)* — Parlaeus (made by Qualigraf; "Qualigraf" in MAAT's VNG integration list IS Parlaeus) covers **3 of the 5 anchor MAAT customers**: Apeldoorn (~265k residents), Maastricht, and Bodegraven-Reeuwijk. Qualigraf confirmed live at `apeldoorn.parlaeus.nl`, `maastricht.parlaeus.nl`. Parlaeus = ~2% of Dutch municipalities by count but 3/5 of our highest-priority competitive targets. Prerequisite: v0.2.1 ORI-fallback for these gemeenten live and stable.

### v0.4.0 (beta) — User Testing Ready + Voice + Historical Depth
_Target: 2026-07-03 (4 weeks after v0.3.0)_

- [ ] _(Public eval scoreboard promoted to v0.2.1 on 2026-04-11 — see above)_
- [ ] `vergelijk_gemeenten` cross-municipality comparison MCP tool — requires ≥2 full-mode municipalities (Rotterdam + the 2 promoted in v0.3.0)
- [ ] Council-watcher agent: monitor new agenda items matching saved queries; push alerts via email + webhook (Slack/Teams)
- [ ] **Voice-first citizen PWA** — thin wrapper over the public MCP surface for Claude/Gemini voice modes; structurally impossible for MAAT's stack (V0_2_BEAT_MAAT_PLAN.md §9). Prerequisite: v0.3.0 public MCP surface stable.
- [ ] **Native Notubiz adapter** `pipeline/sources/notubiz.py` — Notubiz = ~38% of Dutch municipalities (second-largest portal after iBabs). No confirmed anchor MAAT customers on Notubiz (Apeldoorn is Parlaeus, not Notubiz — confirmed 2026-04-11). ORI-fallback in v0.2.1 gives partial coverage; this is full-depth ingestion. Prerequisite: Parlaeus adapter (v0.3.0) shipped and stable.
- [ ] **ThemeFinder-style** per-agenda-item theme maps + multi-round structured summarization
- [ ] ChatGPT and Perplexity MCP registration
- [ ] **Pre-2018 historical backfill** *(added 2026-04-11)* — iterate Rotterdam iBabs calendar backwards from 2018, diff against `documents`, produce `reports/pre_2018_missing.csv`, human sanity-check (earliest iBabs date? doc types? OCR-able?), then run approved set through hardened pipeline in small off-peak batches. Update `neodemos.nl/coverage` with "historische diepte" badge. Full methodology in V0_2_BEAT_MAAT_PLAN.md §7.1.

### v0.5.0 (beta) — Multi-Municipality Foundation + Agentic Features
_Target: TBD_

- [ ] `bestuurslaag` column on `kg_entities` and `politician_registry` (schema migration + backfill for Rotterdam)
- [ ] Parameterized prompts and regexes by `organisatie` — audit all hardcoded Rotterdam-specific strings across pipeline + MCP + templates
- [ ] Second municipality pilot (smaller city with more limited dataset, e.g. Vlaardingen or Maassluis)
- [ ] Per-city domain gazetteers
- [ ] Scheduled briefing generation (council-watcher extended: digest emails on configurable schedule)

### v0.6.0 (rc) — Release Candidate
_Target: TBD_

- [ ] Full data quality audit: spot-check KG edges (sample 500), financial lines (reconcile against source PDFs for 3 years), politician registry completeness
- [ ] Performance targets: p50 < 2s for `zoek_*` tools, p50 < 5s for `traceer_*` tools (profile and fix top regressions)
- [ ] External security review (pentest on MCP OAuth surface + API endpoints)
- [ ] Developer documentation complete: all MCP tools documented with examples, integration guide, rate limits
- [ ] Expand external testers to ≤30

### v1.0.0 (GA) — General Availability
_Target: TBD_

- [ ] Public launch
- [ ] Developer API + API keys (self-serve portal)
- [ ] SLA for uptime (99.5%) and data freshness (T+8h for new meetings)
- [ ] Billing infrastructure (B2B subscriptions)

---

## Deployment Targets

| Platform | Transport | URL | Status |
|----------|-----------|-----|--------|
| Claude Desktop | stdio (local) | n/a | Active (v0.1.0) |
| Claude Desktop | SSE (remote) | mcp.neodemos.nl | Hetzner deployed, needs v3 update |
| ChatGPT | SSE | mcp.neodemos.nl | Not yet registered |
| Perplexity | SSE | mcp.neodemos.nl | Not yet registered |
| Web frontend | HTTP | neodemos.nl | Active |

### Hetzner deployment

- Server: Hetzner VPS
- Stack: docker-compose (postgres + qdrant + web + mcp) via **Kamal** (migrated from Caddy to kamal-proxy 2026-04)
- Domain: `neodemos.nl` (primary), `neodemos.eu` (redirect)
- MCP endpoint: `neodemos.nl/mcp/*` (kamal-proxy → port 8001)
- Config: `docker-compose.prod.yml` — MCP container runs `mcp_server_v3.py`
- TLS: Auto via kamal-proxy + Let's Encrypt

### To deploy a new version to Hetzner

```bash
# 1. Bump VERSION file
echo "0.2.0" > VERSION

# 2. Update STAGE in neodemos_version.py if needed

# 3. Commit and push
git add VERSION neodemos_version.py
git commit -m "Bump version to v0.2.0"
git push

# 4. Deploy to Hetzner (via Kamal or manual)
# See docs/hetzner/03_KAMAL_DEPLOY.md for full instructions
```

---

## Git Tag Convention

Per naming conventions: `v<major>.<minor>.<patch>`

```bash
git tag -a v0.1.0 -m "v0.1.0: Metadata enrichment, KG Layer 1, enriched BM25"
git push origin v0.1.0
```

---

## Changelog

Maintain `CHANGELOG.md` in project root (create when needed). Format:

```markdown
## [0.1.0] - 2026-04-08
### Added
- Metadata enrichment pipeline (section_topic, key_entities, vote_outcome, vote_counts, indieners)
- Knowledge graph Layer 1 (57K edges, 881K clean entities)
- Politician registry (228 records with aliases)
- Domain gazetteer (2,217 entities)
- Enriched BM25 (dual-dictionary tsvector)
- Structured vote data in zoek_moties MCP tool

### Changed
- MCP server name: "NeoDemos (alpha)"
- BM25 uses text_search_enriched column (dutch + simple dictionaries)
- docker-compose.prod.yml: MCP container uses mcp_server_v3.py
```
