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
_Target: 2 weeks from 2026-04-10 kickoff_

**Strategic reorientation (2026-04-10):** v0.2.0 is now scoped to ship the highest-impact features that structurally beat AethiQs MAAT. The previously-planned "Flair NER + Gemini enrichment" items are folded into Workstream 1 below as prerequisites for GraphRAG, not a separate release.

**Full plan:** [`docs/architecture/V0_2_BEAT_MAAT_PLAN.md`](architecture/V0_2_BEAT_MAAT_PLAN.md)

**Workstreams in v0.2.0:**
- [ ] **WS1 GraphRAG** — Flair NER + Gemini enrichment + ~500K KG edges + `services/graph_retrieval.py` + 5th retrieval stream + `traceer_motie` + `vergelijk_partijen` MCP tools
- [ ] **WS2 Trustworthy financial** — `financial_lines` Postgres table + `vraag_begrotingsregel` + `vergelijk_begrotingsjaren` + verification token (zero-paraphrase contract on euros)
- [ ] **WS3 Document journey** — `document_journeys` view + `traceer_document` MCP tool (UI deferred to v0.2.1)
- [ ] **WS4 Best-in-class MCP** — tool registry + `get_neodemos_context` primer + tool-collision detection + scoped OAuth + audit log + parameter/output filters (FactSet defense-in-depth)
- [ ] **WS5 Reliable nightly ingest** — 7-step idempotent job graph + advisory locks + smoke test + admin dashboard (Rotterdam only)
- [ ] **WS6 Source-spans-only summarization** — `services/summarizer.py` + verification badges + cached per-document summaries

**Eval gate (must pass before tag):**
- Completeness ≥ 3.5 (from 2.75 baseline)
- Faithfulness ≥ 4.5 (no regression)
- Numeric accuracy on 30-question financial benchmark = 100%
- 14 consecutive days of clean nightly runs on Rotterdam
- Source-spans-only summaries pass strip-test on 50 random documents

### v0.2.1 (alpha) — Search Beyond Rotterdam
_Target: 1 week after v0.2.0_

- [ ] Multi-portal connectors in `pipeline/sources/`: `ibabs.py` (refactored), `notubiz.py`, `go.py`, `ori_fallback.py`
- [ ] **Search-only mode** for 5 ORI-fallback gemeenten (Apeldoorn, Zoetermeer, Maastricht, Enschede, Bodegraven)
- [ ] Document journey UI: `/journey/{id}` route + `templates/journey.html` with vertical timeline
- [ ] HLS webcast player (`templates/meeting_player.html`) accepting `?t=<seconds>`
- [ ] Citation upgrade: every transcript-derived chunk gets `[▶ MM:SS]` deep-link in `_format_chunks_v3`

### v0.3.0 (beta) — Open MCP Surface
_Target: 4 weeks after v0.2.1_

- [ ] TypeScript codegen for MCP tools — `@neodemos/mcp-tools` published to npm
- [ ] Anthropic [Code Execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) example workflows
- [ ] Anomaly-detection rate limiting (FactSet pattern)
- [ ] Promote 2 of the 5 search-only gemeenten to full mode (KG + financial + journey)
- [ ] ThemeFinder-style per-agenda-item theme maps + multi-round structured summarization
- [ ] ChatGPT and Perplexity MCP registration
- [ ] First external testers onboarded

### v0.4.0 (beta) — User Testing Ready
_Target: 4 weeks after v0.3.0_

- [ ] Native Notubiz adapter (one customer-driven gemeente)
- [ ] `vergelijk_gemeenten` cross-municipality comparison MCP tool
- [ ] Council-watcher agent (push alerts on new agenda items matching saved queries)
- [ ] Public eval scoreboard at `neodemos.nl/eval` with live precision/faithfulness/numeric-accuracy

### v0.5.0 (beta) — Multi-Municipality Foundation
_Target: TBD_

- [ ] `bestuurslaag` column on kg_entities and politician_registry
- [ ] Parameterized prompts and regex by organisatie
- [ ] Second municipality pilot (smaller city, assuming more limited dataset, e.g. Vlaardingen or Maasssluis)
- [ ] Per-city domain gazetteers

### v0.6.0 (beta) — Agentic Features
_Target: TBD_

- [ ] Council-watcher agent (monitor new agenda items, push alerts)
- [ ] Scheduled briefing generation
- [ ] Cross-municipality comparison queries

### v0.7.0 (rc) — Release Candidate
_Target: TBD_

- [ ] Full audit pass on all data
- [ ] Performance optimization (latency targets: <3s for simple queries)
- [ ] Documentation for external developers
- [ ] Security review

### v1.0.0 (GA) — General Availability
_Target: TBD_

- [ ] Public launch
- [ ] Developer API + API keys
- [ ] SLA for uptime and data freshness
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
- Stack: docker-compose (postgres + qdrant + web + mcp + caddy)
- Domain: `neodemos.nl` (primary), `neodemos.eu` (redirect)
- MCP endpoint: `neodemos.nl/mcp/*` (Caddy reverse proxy → port 8001)
- Config: `docker-compose.prod.yml` — MCP container runs `mcp_server_v3.py`
- TLS: Auto via Caddy + Let's Encrypt

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
