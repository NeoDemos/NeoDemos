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

### v0.2.0 (alpha) — Flair NER + Gemini Enrichment
_Target: week of 2026-04-14_

- [ ] Flair ner-dutch-large on all 1.6M chunks (key_entities coverage: 28% → ~65%)
- [ ] Gemini Flash Lite: answerable_questions + section_topic refinement (~$90-130)
- [ ] Semantic relationships via LLM (HEEFT_BUDGET, BETREFT_WIJK, SPREEKT_OVER)
- [ ] kg_relationships: 57K → ~500K-1M edges
- [ ] Quality audit (SQL + deterministic + LLM judge)

### v0.3.0 (alpha) — Graph Retrieval
_Target: week of 2026-04-21_

- [ ] `services/graph_retrieval.py`: entity extraction from query + SQL CTE traversal
- [ ] Graph context prepend for multi_hop and balanced_view queries
- [ ] Entity-based Qdrant pre-filtering at query time
- [ ] v3 eval benchmark: completeness target >=3.25 (from 2.75)

### v0.4.0 (beta) — User Testing Ready
_Target: week of 2026-04-28_

- [ ] mcp.neodemos.nl deployed with v3 server + all enrichments
- [ ] Cross-document motie↔notulen vote linking
- [ ] ChatGPT and Perplexity MCP registration
- [ ] Basic error handling and graceful degradation
- [ ] First external testers onboarded

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
