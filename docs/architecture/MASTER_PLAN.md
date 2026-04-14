# NeoDemos — Master Project Plan

> **Status:** Active development — v0.2.0 in progress, public launch targeting Q2 2026
> **Last updated:** 2026-04-12
> **Owner:** Dennis (founder, Rotterdam city council member)
> **Replaces:** this is the new top-level strategy doc. The v0.2 execution plan at [`V0_2_BEAT_MAAT_PLAN.md`](V0_2_BEAT_MAAT_PLAN.md) remains the sprint-level handoff for current work.
>
> ## Reading order
>
> 1. **This document** — vision, strategy, versioned roadmap, technical decisions
> 2. **[`V0_2_BEAT_MAAT_PLAN.md`](V0_2_BEAT_MAAT_PLAN.md)** — current sprint execution (workstreams, competitive analysis, eval gates)
> 3. **[`docs/handoffs/README.md`](../handoffs/README.md)** — individual agent-ready workstream handoffs
>
> ## For agents picking up work
>
> This document is the **strategy doc**, not your task list. Each version milestone defines workstreams. Active workstreams have **self-contained handoffs** at [`docs/handoffs/`](../handoffs/). Pick up the relevant handoff — it has cold-start prompts, file paths, acceptance criteria, and risks. Only read this plan for context on *why* you're doing the work.

---

## Table of Contents

1. [Vision & Mission](#1-vision--mission)
2. [The Democratic Feedback Loop](#2-the-democratic-feedback-loop)
3. [Strategic Goals — 12-Month Win Condition](#3-strategic-goals--12-month-win-condition)
4. [90-Day Execution Plan](#4-90-day-execution-plan)
5. [Product Architecture](#5-product-architecture)
6. [Freemium Model & Pricing](#6-freemium-model--pricing)
7. [Competitive Landscape](#7-competitive-landscape)
8. [Technical Stack](#8-technical-stack)
9. [Existing Infrastructure](#9-existing-infrastructure)
10. [Knowledge Graph Architecture](#10-knowledge-graph-architecture)
11. [Open Source Component Map](#11-open-source-component-map)
12. [Build vs Buy vs Fork Matrix](#12-build-vs-buy-vs-fork-matrix)
13. [Development Roadmap](#13-development-roadmap)
14. [Key Strategic Risks](#14-key-strategic-risks)
15. [Founder Unfair Advantages](#15-founder-unfair-advantages)
16. [Open Questions & Decisions](#16-open-questions--decisions)

---

## 1. Vision & Mission

**Mission:** NeoDemos sluit de democratische feedbackloop — burgers begrijpen beleid, evalueren het, en hun input bereikt raadsleden voordat de vergadering begint.

**In English:** NeoDemos closes the democratic feedback loop — citizens understand policy, evaluate it, and their aggregated input reaches councillors before the meeting begins.

**Positioning:** Not a better document search engine. Civic intelligence vs document retrieval. NeoDemos helps democracy scrutinize policy; competitors help civil servants produce it.

**One-line pitch (for press/political audiences):**
> "NeoDemos is the first platform that closes the full democratic feedback loop — from policy comprehension to citizen evaluation to structured input for elected representatives."

**Core differentiator:** Cross-policy conclusions that others glossed over for years — connecting dots across policy domains, document types, political actors, and time spans that competitors' single-document semantic search structurally cannot surface. The product is an *investigation engine*, not a Q&A chatbot.

---

## 2. The Democratic Feedback Loop

This is the core concept that no existing platform in the world has fully implemented:

```
Policy created by college/government
        ↓
LAYER 1 — COMPREHENSION  [v1.0.0]
Professionals & citizens understand policy through
AI-powered plain-language explanation with depth on demand
        ↓
LAYER 2 — EVALUATION  [v1.5.0–v2.0.0]
Citizens evaluate specific policies, motions, and decisions
Structured feedback linked to KG entities (motie, dossier, beleidsgebied)
        ↓
LAYER 3 — AGGREGATION & DELIVERY  [v2.0.0]
Feedback aggregated by theme, not by for/against vote count
Structured briefing delivered to councillors 3 days before meetings
        ↓
Councillors better informed → better democratic decisions
        ↓
[loop repeats with next policy cycle]
```

**Critical design principle for Layer 2:** Aggregate *themes and questions* that citizens raise, NOT vote counts. This is legally safer, politically more palatable, and more useful to councillors than "37% approve."

**Critical design principle for Layer 3:** Frame output as "37 citizens reacted to motie M2024-042; main themes: betaalbaarheid (18x), uitvoerbaarheid (12x), milieu-impact (7x)" — not as citizen instructions to the councillor.

---

## 3. Strategic Goals — 12-Month Win Condition

**Primary goal:** Press and political recognition as the civic AI standard in the Netherlands.

**What "winning" looks like by April 2027:**
- Cited or profiled by at least 2 of: NRC, Trouw, Follow the Money, Binnenlands Bestuur, BNR
- Named publicly by at least 2 raadsleden from different parties
- Spoken at GovTech Day or Nederlandse Vereniging voor Raadsleden event
- Published at least 5 original cross-policy investigations using NeoDemos
- At least 1 active pilot with a Rotterdam fractie or committee

**What "winning" does NOT require in year 1:**
- Paying enterprise municipality clients (that's year 2)
- Neo4j running (evaluate for v1.0, not a gate for public launch)
- RAGAS scores ≥ 90% (ongoing improvement, not a gate)
- DigiD integration (requires municipality partnership)
- Complete feedback loop (that's v2.0)

---

## 4. 90-Day Execution Plan

> **Day 1 = after v0.2.0 ships (target: May 2026)**

The single most important strategic reframe: **stop building infrastructure, start publishing investigations.**

### Priority order: content first, infrastructure second

**Days 1–21: First investigation published**

Pick the most explosive verifiable cross-policy question in Rotterdam politics. Top candidates:

- **Coalitieakkoord vs uitvoering:** Which coalition agreement promises have been blocked by moties filed by coalition parties themselves?
- **Jeugdzorg budget-consequence chain:** Which parties voted for jeugdzorg cuts 2019-2022, and what did they say publicly about the resulting deficit in 2024-2025?
- **Klimaat ambitie vs begrotingsrealisatie:** Which climate motions were adopted since 2020, and what is the spending realisation rate?

Workflow: Use NeoDemos MCP + Claude to research → write up as Follow the Money-style article → publish on Substack or LinkedIn → send personally to: 1 NRC journalist, 1 Trouw journalist, 1 Follow the Money journalist.

**Days 22–60: Accountability series (4 more investigations)**

Same workflow. Each piece:
- Uses NeoDemos by name in the text
- Includes a cross-policy conclusion that required multi-document traversal
- Is falsifiable and specifically sourced

**Days 61–90: Political endorsement + web presence**

- Get 2 raadsleden (different parties) to use NeoDemos on camera
- Submit for GovTech Day
- Write op-ed for Binnenlands Bestuur under own name as raadslid
- Public web interface live (v0.3.0)

---

## 5. Product Architecture

### Three-layer product, two user types

**Primary target (in priority order):** 1) Gemeenteraadsleden and support staff, 2) Political journalists, 3) Civil servants, 4) Engaged citizens.
**Wow-moment trigger:** Combination of how parties vote + policy explained simply with depth available.
**User funnel:** Search (anonymous, rate-limited) → Free account (more searches) → MCP (unlimited, power users).

### Layer 1 — Comprehension (v0.3.0 MVP, v1.0.0 production)

User types a question in plain Dutch. Returns:

1. **Simple explanation** — 2 sentences, readable by any citizen (B1 reading level)
2. **Party voting positions** — visual, color-coded per party, on relevant motions (from structured `motie_stemmen` table, NOT RAG synthesis)
3. **Depth on demand** — sources, debate quotes, timeline, full document links

Each claim has a clickable source link. When confidence is low, the system says "ik weet het niet zeker — bekijk hier de bronnen zelf" rather than synthesizing an uncertain answer.

Tech: Claude Sonnet via Anthropic API `tool_use` with the same 13 MCP tools (MCP-as-Backend architecture — see WS9). Jinja2/FastAPI frontend with design token system (Instrument Serif + Inter, Civic Blue + Muted Gold palette). No login required for basic use; rate-limited AI searches (1/day anon, 3/day free account, unlimited select users).

### Layer 2 — Evaluation (v1.5.0 alpha, v2.0.0 production)

- Structured citizen feedback on specific moties/agendapunten/dossiers
- Feedback linked to KG entities (not free text floating in a void)
- Section-specific commenting (citizen responds to a specific clause, not the whole document)
- Progressive verification: anonymous → email → phone → iDIN
- No DigiD in MVP (requires municipality partnership + Logius approval process)
- Anti-astroturfing: rate limiting + content fingerprinting + coordination detection
- **Legal review required before build** — see Risk 7

### Layer 3 — Councillor delivery (v2.0.0)

- Jinja2 + WeasyPrint briefing documents (PDF + Word)
- Aggregated by theme (BERTopic clusters), not sentiment scores
- Delivered via email 3 days before scheduled vergadering
- Linked to iBabs/Notubiz meeting schedules via OpenRaadsinformatie API
- Structured output via Instructor + Pydantic models

### User interface structure (updated 2026-04-12, WS8)

```
Anonymous (no login)
├── Search-as-hero landing page (Instrument Serif + Inter, Civic Blue + Muted Gold)
├── Unlimited keyword search
├── 1 AI analysis/day (Sonnet + tool_use, MCP-quality)
├── Filterable meeting calendar (list view default)
├── Trust content: EU sovereignty, founder authority, democratic ambition
└── Rate limit → "Maak een gratis account"

Free account (email)
├── Everything above
├── 3 AI analyses/day
├── MCP nudge card → "Onbeperkt via uw AI-assistent"
├── Export for Word
└── Search history (future)

Select (invited raadsleden, journalists)
├── Everything above
├── Unlimited AI analyses
├── MCP access (OAuth, unlimited)
└── Priority for new features

Pro — €19/month (future, post-launch)
├── Everything above
├── Motievolger / alerts
├── Pre-vergadering briefings
└── Municipality comparison (when available)

Raadslid — €49/month (future)
├── Everything above
├── Pre-meeting briefings (automated, 3 days before vergadering) [v2.0]
├── Citizen feedback aggregated per dossier [v2.0]
├── Motion alerts: notify when topic you follow gets new motion
└── Export as Word/PDF for formal use
```

---

## 6. Freemium Model & Pricing

| Feature | Anoniem | Gratis account | Pro €19/mnd (future) |
|---|---|---|---|
| Zoeken + AI-analyse | Onbeperkt | Onbeperkt | Onbeperkt |
| Kalender + vergaderingen | ✅ | ✅ | ✅ |
| Zoekgeschiedenis | ❌ | ✅ | ✅ |
| Partijlens | ❌ | ✅ | ✅ |
| Export voor Word | ❌ | ✅ | ✅ |
| Vergaderingvolger | ❌ | ✅ | ✅ |
| MCP toegang | ❌ | ✅ | ✅ |
| Motievolger / alerts | ❌ | ❌ | ✅ |
| Pre-vergadering briefings | ❌ | ❌ | ✅ |

**Pricing rationale (v0.2.0 launch):** Everything works without login. No rate limiting at launch — the press moment is worth more than the API bill (~$5/day at expected volumes). Account creation is incentivized by pull features (save history, export, MCP) not by restricting search. Cost monitoring with $10/day alert; rate limiting code dormant, activates only if needed. Paid tiers deferred to post-launch.

**User funnel (pull, not push):**
1. Anonymous user searches → sees value immediately (unlimited)
2. After 3rd search → subtle nudge: "Bewaar uw zoekgeschiedenis" (dismissable)
3. Logged-in user → after 5th search → MCP nudge: "Gebruik NeoDemos in Claude/ChatGPT"
4. MCP user → deepest engagement, strongest retention

---

## 7. Competitive Landscape

### Direct Dutch competitors

| Platform | Strength | Key weakness vs NeoDemos |
|---|---|---|
| **Codi.nu** (JoinSeven, Zeist) | 5 ministry clients, €59–99/mnd, 10M+ docs, ~12 people | National policy focus; no vote tracking; no MCP; no neural reranking; raadsinformatie is an afterthought |
| **MAAT** (AethiQs) | 10+ municipalities, embedded in iBabs/Notubiz | Document search shell, not civic intelligence; no MCP; no KG; no party/vote analysis; public tier is BM25-only |
| **Raadzaam.nl** | Utrecht deployment, source citations | Search engine only; no feedback layer; no aggregation; no party positions |
| **NotuBiz** | Incumbent meeting platform | Not an intelligence layer; provides raw meeting data, not analysis |

**Codi.nu specific intelligence:**
- Built by JoinSeven B.V., ~12 people, bootstrapped + Microsoft for Startups credits
- Uses "Heptagon" proprietary data platform, multi-LLM (OpenAI, Mistral, Meta)
- No evidence of vector database, neural reranking, hybrid search, or MCP server
- National policy focus (Kamervragen, ministeries) — NOT municipal depth

**Winning frame:** "Codi helps civil servants read documents. NeoDemos helps democracy work."

### Three most dangerous future competitors

1. **Go Vocal** (CitizenLab) — 500+ government clients, could add AI comprehension + Dutch council integration
2. **Policy Synth / Citizens Foundation** — AI-agent architecture, proven in Estonia (7 enacted laws)
3. **EU Horizon projects** (AI4Deliberation, ORBIS, iDEM) — €15–20M funded, building the same problem space as open-source outputs 2025–2027

**Window of opportunity:** 18–24 months before these threats mature into products.

---

## 8. Technical Stack

### Decisions locked (2026-04-12)

| Decision | Choice | Rationale |
|---|---|---|
| **Embedding model** | **Qwen3-Embedding-8B** (4096-dim, via Nebius API) | MTEB retrieval 70.88 vs BGE-M3 54.60 (+16 pts). Wins every task type. Dutch retrieval ~68-72 vs 60. Don't re-embed. |
| **Reranker** | **Jina v3** (API) | Working well. CC-BY-NC is a weights license, not an API restriction. Evaluate BGE-reranker-v2-m3 swap when licensing becomes a business blocker. |
| **Auth** | **Custom OAuth 2.1** (`services/auth_service.py`, `services/mcp_oauth_provider.py`) | Already built. Don't deploy Authentik — saves 1.5GB RAM + weeks of integration. Add email magic links for public frontend. |
| **Reverse proxy** | **kamal-proxy** (via Kamal deploy) | Migrated from Caddy on 2026-04-11. Handles Let's Encrypt TLS, routes to web:8000 + MCP:8001. |
| **Graph DB (v0.2)** | **PostgreSQL CTEs** (`services/graph_retrieval.py`) | Working, 2-hop max. Add missing indexes immediately. Sufficient for current workstreams. |
| **Graph DB (v1.0)** | **Neo4j CE** (evaluate) | CTEs cannot do 3+ hop pattern matching needed for cross-policy investigations. Neo4j CE has index-free adjacency, full Cypher, efficient deep traversal. See §10. |
| **LLM inference** | **Anthropic API** (Claude Sonnet) + Groq/Together for smaller tasks | No GPU on CCX33. API inference only. |
| **Deployment** | **Kamal v2** | Binary at `/opt/homebrew/lib/ruby/gems/4.0.0/bin/kamal`. Never rsync/SSH. |

### Query routing architecture

```
User question
     │
     ▼
Query classifier
(regex patterns + embedding similarity)
     │
     ├── VOTE QUERY ──► PostgreSQL motie_stemmen table
     │                   → exact aggregation per partij/thema/periode
     │                   → structured JSON result
     │
     ├── GRAPH QUERY ──► graph_retrieval.py (v0.2: CTE, v1.0: Neo4j)
     │                    → entity extraction → walk → score → hydrate
     │                    → reasoning chain with per-hop confidence
     │
     └── EXPLANATION QUERY ──► Qdrant hybrid search
                                → Qwen3-Embedding-8B dense vectors
                                → PostgreSQL BM25 sparse search
                                → RRF fusion
                                → Jina v3 reranker (top 8 from 50)
                                → Claude Sonnet synthesis
          │
          ▼
     Claude combines all layers:
     [Simpele uitleg from RAG] + [Exacte stemcijfers from SQL]
     + [Cross-policy chain from graph] + [Source links on every claim]
```

### Guiding architectural principles

1. **KG is required for precision** — party vote queries cannot be answered via RAG alone
2. **RAG alone fails on vote aggregation** — "Hoe stemde de VVD?" needs SQL, not chunk retrieval
3. **Hybrid architecture** — query classifier routes to the right backend
4. **No unnecessary dependencies** — no LangChain, no LlamaIndex. Direct FastAPI + asyncpg + Qdrant client
5. **CPU-only deployment** — Hetzner CCX33 has no GPU. All models via API or CPU inference
6. **Graceful refusal is a feature** — when confidence is low, say so. Never synthesize uncertain cross-policy claims.

---

## 9. Existing Infrastructure

### What's running today (verified 2026-04-12)

| Component | Status | Details |
|---|---|---|
| PostgreSQL 16 + pgvector | ✅ Running | 90K documents, 1.6M chunks, `kg_entities` + `kg_relationships` + `kg_mentions` tables |
| Qdrant | ✅ Running | Qwen3-Embedding-8B vectors (4096-dim), collection `notulen_chunks` |
| Jina v3 reranker | ✅ Running | API-only (500 RPM), `services/reranker.py` |
| FastAPI MCP server | ✅ Running | `mcp_server_v3.py`, FastMCP framework |
| NeoDemos MCP tools | ✅ **18 tools** | See tool list below |
| graph_retrieval.py | ✅ Built | 666 lines, functional, disabled pending 200K edges (`GRAPH_WALK_ENABLED=False`) |
| Custom OAuth 2.1 | ✅ Built | `services/auth_service.py` + `services/mcp_oauth_provider.py` |
| KG edges | ✅ 57K | Targeting 500K in WS1. Types: `LID_VAN`, `DIENT_IN`, `STEMT_VOOR/TEGEN`, `SPREEKT_OVER`, `BETREFT` |
| Domain gazetteer | ✅ 2,217 entries | `data/knowledge_graph/domain_gazetteer.json` |
| Entity mentions | ✅ 3.3M | `kg_mentions` table |
| Politician registry | ✅ Active | Deduplication + alias resolution |
| Hetzner CCX33 | ✅ Running | 8 vCPU, 32GB RAM, ~5.5GB used |
| kamal-proxy | ✅ Running | TLS via Let's Encrypt, ports 80/443 |
| Jinja2 web UI | ✅ 15 templates | Server-side rendered, admin/demo purposes |

### MCP tool inventory (18 tools)

`zoek_raadshistorie`, `zoek_financieel`, `zoek_uitspraken`, `zoek_uitspraken_op_rol`, `zoek_moties`, `zoek_gerelateerd`, `scan_breed`, `haal_vergadering_op`, `lijst_vergaderingen`, `tijdlijn_besluitvorming`, `analyseer_agendapunt`, `haal_partijstandpunt_op`, `lees_fragment`, `vat_document_samen`, `get_neodemos_context`, `traceer_motie`, `vergelijk_partijen`

### Known MCP tool issues

- `tijdlijn_besluitvorming` — groups results under "0000"; unreliable standalone timeline
- `zoek_moties` — matches too broadly; `uitkomst` field shows "?" despite title-level status info
- `zoek_financieel` — surfaces older documents when querying recent data
- `zoek_uitspraken` — disrupted by role changes (raadslid → wethouder)
- Duplicate results across tools consume result slots; no server-side deduplication
- No chronological sorting in any tool output
- `lees_fragment` requires document_id but search results may return chunk_id

### Server capacity

| Service | RAM |
|---|---|
| PostgreSQL 16 + pgvector | ~2 GB |
| Qdrant | ~1 GB |
| FastAPI backend (web + MCP) | ~512 MB |
| kamal-proxy | ~32 MB |
| Uptime Kuma (monitoring) | ~128 MB |
| **Total current** | **~3.7 GB / 32 GB** |
| **Headroom for Next.js + Redis + Neo4j** | ~28 GB available |

---

## 10. Knowledge Graph Architecture

### Current state (2026-04-12)

- **57K edges** in `kg_relationships` (PostgreSQL), targeting 500K via WS1
- **Graph traversal** via recursive CTEs in `services/graph_retrieval.py` (666 lines)
- **Hard-capped at 2 hops** (`MAX_HOPS_V02 = 2`) — intentional, prevents combinatorial explosion
- **⚠️ Zero indexes on `kg_relationships`** — critical performance bug. At 500K edges, each 2-hop walk requires ~31 full table scans → ~7 second latency. **Fix immediately.**

### Target graph schema

```
(Politician)-[:LID_VAN]->(Partij)
(Politician)-[:ZIT_IN]->(Commissie)
(Politician)-[:DIENDE_IN]->(Motie)
(Politician)-[:SPRAK_OVER]->(AgendaPunt)
(Partij)-[:STEMDE {uitslag}]->(Motie)
(Partij)-[:STEMDE {uitslag}]->(Amendement)
(Motie)-[:INGEDIEND_IN]->(Vergadering)
(Motie)-[:GAAT_OVER]->(Beleidsgebied)
(Motie)-[:HEEFT_STATUS]->(MotieStatus)
(Amendement)-[:WIJZIGT]->(Motie)
(Vergadering)-[:BEHANDELDE]->(AgendaPunt)
(AgendaPunt)-[:LEIDDE_TOT]->(Besluit)
(Commissie)-[:BEHANDELDE]->(AgendaPunt)
(Document)-[:BEVAT]->(AgendaPunt)
(Beleidsgebied)-[:IS_ONDERDEEL_VAN]->(HoofdBeleidsgebied)
(Location)-[:LOCATED_IN {level}]->(Location)
```

### Graph DB decision framework

| Requirement | PostgreSQL CTEs | Apache AGE | Neo4j CE |
|---|---|---|---|
| 2-hop queries (v0.2 scope) | ✅ Works (with indexes) | ✅ Overkill | ✅ Overkill |
| 3-5 hop cross-policy chains | ❌ Hard-capped, exponential blowup | ⚠️ Cypher syntax but still relational joins | ✅ Native, efficient |
| Path pattern matching (edge-type sequences) | ❌ Cannot express | ✅ Cypher support | ✅ Full Cypher 5.x |
| Variable-length paths | ❌ Must hardcode depth | ✅ `*2..5` syntax | ✅ Native |
| Performance at 500K+ edges | ⚠️ Needs indexes, degrades at 3+ hops | ⚠️ Still table scans | ✅ Index-free adjacency, O(1) neighbor lookup |
| Operational complexity | ✅ Already running | ⚠️ Community-maintained post-Bitnine acquisition | ⚠️ Separate JVM process, ~4GB RAM |
| SQL interop | ✅ Native | ✅ Same PostgreSQL | ❌ Separate DB, needs sync |

**Decision (locked 2026-04-12):**

- **v0.2.0:** Keep PostgreSQL CTEs. Add missing indexes. Sufficient for 2-hop WS1 deliverables.
- **v0.5.0:** Evaluate Neo4j CE with a prototype of the cross-policy `verbind_beleid` tool. Benchmark 4-hop queries on 500K edges. If CTEs with indexes are fast enough, defer Neo4j further.
- **v1.0.0:** If cross-policy investigations require 3+ hop pattern matching (they will), deploy Neo4j CE alongside PostgreSQL. Use Neo4j for graph traversal, PostgreSQL for everything else. Sync via change-data-capture or batch ETL.
- **Apache AGE is skipped.** It adds Cypher syntax without solving the fundamental performance problem (still relational joins, not index-free adjacency). The stability risk (community-maintained since Bitnine acquisition in Jan 2025) makes it unsuitable as a production dependency.

### Immediate fix: missing indexes

```sql
-- Run NOW on production PostgreSQL
CREATE INDEX CONCURRENTLY idx_kg_rel_source ON kg_relationships(source_entity_id);
CREATE INDEX CONCURRENTLY idx_kg_rel_target ON kg_relationships(target_entity_id);
CREATE INDEX CONCURRENTLY idx_kg_rel_source_target ON kg_relationships(source_entity_id, target_entity_id);
CREATE INDEX CONCURRENTLY idx_kg_rel_type ON kg_relationships(relation_type);
```

Expected impact: 2-hop walk latency from ~7s → ~150ms at 500K edges.

### KG build sequence

**Phase 1 (v0.2.0, in progress):** Enrich KG to 500K edges via Flair NER + Gemini. Build BAG location hierarchy. Enable `graph_retrieval.py` at 200K edge threshold. All in PostgreSQL.

**Phase 2 (v0.3.0):** Build `motie_stemmen` PostgreSQL table from besluitenlijsten. Regex/structured parsing, not LLM extraction. This is the structured voting data layer.

**Phase 3 (v0.5.0):** Neo4j CE evaluation. Prototype `verbind_beleid` cross-policy investigation tool. If Neo4j proves necessary, migrate KG edges + entities to Neo4j while keeping PostgreSQL for documents, chunks, and structured data.

**Phase 4 (v1.0.0):** Full Neo4j deployment if evaluation positive. Cross-policy investigation tools production-grade. 3-5 hop queries available.

---

## 11. Open Source Component Map

### Retrieval & comprehension

| Component | Library | License | Status |
|---|---|---|---|
| **Primary embedding** | **Qwen3-Embedding-8B** (Qwen/Alibaba) | Apache 2.0 | ✅ Deployed via Nebius API |
| Hybrid search | Qdrant native RRF + PostgreSQL BM25 | Apache 2.0 | ✅ Deployed |
| **Reranker** | **Jina v3** (API) | CC-BY-NC (weights) | ✅ Deployed — API use is commercially fine |
| Reranker alternative | BGE-reranker-v2-m3 (BAAI) | MIT | Evaluate when Jina licensing matters |
| LLM inference | Claude Sonnet via Anthropic API | Commercial | ✅ Deployed |
| Document parsing | Docling (IBM Research) | MIT | Available, not yet integrated |
| Chunking | Recursive 512-token | — | ✅ Deployed |

### Dutch NLP

| Component | Library | License | Notes |
|---|---|---|---|
| Dutch NER | spaCy nl_core_news_lg + Flair ner-dutch-large | CC-BY-SA / MIT | Flair in WS1 enrichment |
| Dutch BERT | RobBERT-2023-large (KU Leuven) | MIT | +18.6pts over BERTje on DUMB |
| Dutch sentence embeddings | NFI robbert-2022-dutch-sentence-transformers | Apache 2.0 | Dutch govt origin |
| Entity linking | REL (Radboud University) | MIT | Dutch-built, GERBIL SOTA |
| Topic modeling | BERTopic (Maarten Grootendorst) | MIT | For v2.0 feedback clustering |

### Knowledge graph

| Component | Library | License | Status |
|---|---|---|---|
| Graph traversal (v0.2) | PostgreSQL recursive CTEs | — | ✅ Deployed |
| Graph database (v1.0) | Neo4j Community Edition | GPL v3 | Evaluate at v0.5.0 |
| Entity extraction | spaCy + Flair + Gemini Flash | MIT / Commercial | WS1 in progress |

### Citizen feedback layer (v2.0)

| Component | Library | License | Notes |
|---|---|---|---|
| Feedback backend | FastAPI + PostgreSQL | MIT | Build natively |
| Consensus algorithm | Polis `red-dwarf` algorithm | AGPL | Fork core PCA+KMeans (~200 lines) |
| Auth | Custom OAuth 2.1 (existing) | — | ✅ Built. Add email magic links. |
| Anti-astroturfing | SlowAPI + custom PG queries | MIT | Rate limiting + coordination detection |
| GDPR consent | Klaro | BSD-3 | <20KB, Dutch language built-in |

### Frontend & infrastructure

| Component | Library | License | Notes |
|---|---|---|---|
| Frontend framework | Next.js | MIT | For v0.3.0 public interface |
| UI components | shadcn/ui + Radix UI | MIT | ARIA accessibility |
| Dutch govt design | NL Design System (Rotterdam theme) | EUPL-1.2 | Government-credible styling |
| Charts | Recharts | MIT | Party voting, trend lines |
| Real-time | SSE via FastAPI | — | Server→client only |
| Monitoring | Uptime Kuma | MIT | ~128MB RAM |
| Error tracking | Sentry Cloud free | — | 5K errors/month free |
| Reverse proxy | kamal-proxy (via Kamal) | MIT | Auto HTTPS, ~32MB RAM |
| Deployment | Kamal v2 | MIT | Docker-based, zero-downtime |

### Dutch data sources

| Source | Type | Coverage | Access |
|---|---|---|---|
| OpenRaadsinformatie API | ElasticSearch REST | 300+ municipalities incl. Rotterdam | Open, free |
| iBabs (Rotterdam) | REST API (OAuth2) | Rotterdam council meetings, agendas, docs | Requires approval |
| BAG (Basisregistratie Adressen en Gebouwen) | PDOK REST | National address registry | Open |
| CBS Wijk- en Buurtkaart | REST | Geographic hierarchy | Open |
| Wikidata | REST | Politicians, party affiliations | Open |

---

## 12. Build vs Buy vs Fork Matrix

| Component | Decision | Reason | Est. days |
|---|---|---|---|
| RAG pipeline | **EXTEND** existing | Already built; add query classifier + graph stream | 5–10 |
| motie_stemmen table | **BUILD** | Regex parse besluitenlijsten; no LLM needed | 3–5 |
| Next.js frontend | **BUILD** | Custom with shadcn/ui; Claude Code optimized | 7–15 |
| Graph traversal (v0.2) | **KEEP** existing CTEs | Already 666 lines, functional | 0 |
| Neo4j CE (v1.0) | **EVALUATE** then deploy | For 3+ hop cross-policy queries | 5–10 |
| Feedback system | **BUILD** | Native FastAPI/PostgreSQL simpler than forking Ruby | 5–12 |
| Polis consensus | **FORK** core only | ~200 lines PCA+KMeans; avoid full stack | 3–10 |
| Councillor briefings | **BUILD** | Jinja2 + WeasyPrint + Instructor | 3–7 |
| Auth | **KEEP** existing OAuth 2.1 | Already built; add magic links | 1–2 |
| Dutch NER | **USE** then fine-tune | spaCy/Flair baseline now; RobBERT fine-tune later | 1–10 |
| Topic modeling | **USE** BERTopic | Off-the-shelf, MIT | 2–4 |
| Embedding model | **KEEP** Qwen3-Embedding-8B | Superior on every benchmark | 0 |
| Reranker | **KEEP** Jina v3 API | Working, evaluate BGE swap later | 0 |
| Deployment | **KEEP** Kamal v2 | Working | 0 |
| LLM inference | **BUY** API | Anthropic + Groq/Together, ~€20–50/month | 1–2 |
| DigiD | **DEFER** | Requires municipality partnership | — |
| Apache AGE | **SKIP** | Neither fish nor fowl; uncertain stability | — |
| Authentik | **SKIP** | Custom OAuth already exists; saves 1.5GB RAM | — |
| Decidim/Consul | **SKIP** | Ruby monoliths; incompatible stack | — |

---

## 13. Development Roadmap

### Version overview

| Version | Codename | Layer | Target | Key deliverable |
|---|---|---|---|---|
| **v0.2.0** | Infrastructure | — | May 2026 | KG enriched, MCP hardened, financial + summarization |
| **v0.3.0** | Public Launch | 1 (MVP) | Jun 2026 | Public frontend, structured votes, first investigation |
| **v0.5.0** | Recognition | 1 (depth) | Aug 2026 | 5 investigations, 20 users, cross-policy tool, Neo4j eval |
| **v1.0.0** | Reliable | 1 (production) | Oct 2026 | Layer 1 production-grade, Pro tier, proven reliable |
| **v1.5.0** | Feedback Alpha | 2 (alpha) | Jan 2027 | Feedback collection, clustering, verification tiers |
| **v2.0.0** | Full Loop | 2+3 (production) | Apr 2027 | Complete democratic feedback loop |

---

### v0.2.0 — Infrastructure (in progress)

> **Execution plan:** [`V0_2_BEAT_MAAT_PLAN.md`](V0_2_BEAT_MAAT_PLAN.md)
> **Handoffs:** [`docs/handoffs/`](../handoffs/)

| WS | Handoff | Title | Status |
|---|---|---|---|
| WS1 | [`WS1_GRAPHRAG.md`](../handoffs/WS1_GRAPHRAG.md) | GraphRAG retrieval (57K → 500K edges) | in progress |
| WS2 | [`WS2_FINANCIAL.md`](../handoffs/done/WS2_FINANCIAL.md) | Trustworthy financial analysis | in progress |
| WS3 | [`WS3_JOURNEY.md`](../handoffs/WS3_JOURNEY.md) | Document journey timelines | blocked on WS1 |
| WS4 | [`WS4_MCP_DISCIPLINE.md`](../handoffs/done/WS4_MCP_DISCIPLINE.md) | Best-in-class MCP surface | in progress |
| WS5a | [`WS5a_NIGHTLY_PIPELINE.md`](../handoffs/WS5a_NIGHTLY_PIPELINE.md) | Nightly ingest pipeline | pending |
| WS6 | [`WS6_SUMMARIZATION.md`](../handoffs/WS6_SUMMARIZATION.md) | Source-spans summarization | in progress |

**Eval gate:** Completeness ≥ 3.5, Faithfulness ≥ 4.5, KG ≥ 500K edges, Nightly 14 clean days. Full gate in [`docs/handoffs/README.md`](../handoffs/README.md).

**Immediate fix (pre-eval):** Add missing `kg_relationships` indexes.

---

### v0.3.0 — Public Launch

> **Target:** June 2026
> **Depends on:** v0.2.0 shipped
> **Handoffs:** Create at [`docs/handoffs/`](../handoffs/) when v0.2.0 ships

> **Note:** WS numbers continue from v0.2.0 (which uses WS1–WS7).

| WS | Title | Scope | Delegatable to | Est. days |
|---|---|---|---|---|
| WS8 | **Public Next.js Frontend** | Single search → 3-layer response. shadcn/ui + Tailwind. Mobile-first. Party voting visualization. Source citations with document links. Confidence indicators. Graceful refusal UI. Deploy on Hetzner behind kamal-proxy. WCAG 2.1 AA. | Claude Code agent | 7–10 |
| WS9 | **Structured Voting Data** | Create `motie_stemmen` PostgreSQL table. Parse stemuitslagen from besluitenlijsten (regex, not LLM). Cross-reference with KG vote edges. Expose via MCP tool `zoek_stemgedrag`. | Claude Code agent | 3–5 |
| WS10 | **Query Classifier + Hybrid Response** | Route user questions to vote-SQL / graph-walk / RAG based on intent. Combine results in single Claude response. Test on 50 query benchmark. | Claude Code agent | 3–5 |
| WS11 | **Graceful Refusal Flow** | Confidence scoring per retrieval stream. UI state for low confidence ("ik weet het niet zeker"). Source-citation requirement on every claim. Vote queries ONLY from structured data, never RAG synthesis. | Claude Code agent | 2–3 |
| WS12 | **First Investigation + Press** | Pick top cross-policy question. Research via MCP + Claude. Write Follow the Money-style article. Publish. Send to 3 named journalists. | Dennis (not delegatable) | 5–7 |

**Acceptance criteria for v0.3.0:**
- [ ] Public frontend live at neodemos.nl (no login required for base use)
- [ ] motie_stemmen table populated with ≥80% of Rotterdam stemuitslagen
- [ ] Query classifier routes vote/explanation/graph queries correctly on 50-question benchmark
- [ ] Every answer shows clickable source links
- [ ] Low-confidence answers show explicit uncertainty
- [ ] First investigation published and sent to ≥3 journalists
- [ ] 5 named users have tested the frontend and given feedback

**Cold-start prompt for WS8 (Next.js frontend):**

> You are building the public web frontend for NeoDemos — a Dutch civic intelligence platform for the Rotterdam city council. Read this handoff top-to-bottom, then read `mcp_server_v3.py` (the MCP server you'll call), `services/rag_service.py` (retrieval architecture), and `services/graph_retrieval.py` (graph queries).
>
> Build a Next.js app with shadcn/ui + Tailwind. Single search input → 3-layer response: (1) simple 2-sentence explanation, (2) party voting positions color-coded, (3) expandable depth with sources. Mobile-first. Dutch language UI. Deploy as Docker container on Hetzner behind existing kamal-proxy. No login for base use; 5 questions/day anonymous rate limit.
>
> Backend calls: FastAPI endpoints on localhost:8000 (same server). Do NOT call the Anthropic API directly from the frontend — all LLM calls go through the FastAPI backend.
>
> Acceptance: WCAG 2.1 AA, loads in <3s on 4G, every answer has clickable source links, graceful refusal when confidence is low.

---

### v0.5.0 — Recognition

> **Target:** August 2026
> **Depends on:** v0.3.0 shipped + first investigation published

| WS | Title | Scope | Delegatable to | Est. days |
|---|---|---|---|---|
| WS13 | **Cross-Policy Investigation Tool** | Build `verbind_beleid` MCP tool — takes 2+ beleidsgebieden + periode + optional party filter, returns multi-hop reasoning chain with per-hop confidence and source links. Start with PostgreSQL CTEs; benchmark whether 3+ hops require Neo4j. | Claude Code agent | 5–7 |
| WS14 | **Neo4j CE Evaluation** | Install Neo4j CE on staging. Migrate 500K KG edges. Benchmark `verbind_beleid` queries (4-hop) on Neo4j vs CTE. Decision: deploy or defer. | Claude Code agent | 5–7 |
| WS15 | **OpenRaadsinformatie Daily Sync** | Scheduled fetch from ORI API for Rotterdam. Upsert new documents + trigger embedding pipeline. Detect new moties/amendementen automatically. | Claude Code agent | 3–5 |
| WS16 | **Investigation Series** | 4 more published investigations (total 5). Each uses NeoDemos by name. Target themes: coalitieakkoord vs uitvoering, jeugdzorg chain, klimaat ambitie vs realisatie, inspraak vs agendering. | Dennis (not delegatable) | ongoing |
| WS17 | **Named User Cohort** | Recruit 20 named users: 5 raadsleden (mixed coalitie/oppositie), 3 journalists, 12 burgers. Document feedback. | Dennis (not delegatable) | ongoing |

**Acceptance criteria for v0.5.0:**
- [ ] `verbind_beleid` produces verifiable 3+ hop reasoning chains
- [ ] Neo4j evaluation complete with written recommendation
- [ ] ORI sync running daily without manual intervention for 14 days
- [ ] 5 investigations published
- [ ] 20 named users recruited and active
- [ ] 2 raadsleden from different parties have used NeoDemos publicly

---

### v1.0.0 — Reliable

> **Target:** October 2026
> **Depends on:** v0.5.0 shipped + Neo4j decision made

| WS | Title | Scope | Delegatable to | Est. days |
|---|---|---|---|---|
| WS18 | **Neo4j Production Deploy** (if evaluation positive) | Deploy Neo4j CE on Hetzner. Sync KG from PostgreSQL. Update `graph_retrieval.py` to use Cypher. Update MCP tools. | Claude Code agent | 7–10 |
| WS19 | **Pro Tier + Stripe** | Stripe integration. Account management. Saved searches, unlimited queries, party comparisons, export. | Claude Code agent | 5–7 |
| WS20 | **Reliability Hardening** | Error tracking (Sentry). Automated health checks. Fallback when LLM API is down. Response latency p95 < 3s. Uptime target 99.5%. | Claude Code agent | 3–5 |
| WS21 | **NL Design System Integration** | Rotterdam theme from NL Design System. Government-credible visual identity. | Claude Code agent | 3–5 |
| WS22 | **GovTech Presentation** | Submit and present at GovTech Day or NVvR event. Op-ed for Binnenlands Bestuur. | Dennis | — |

**Acceptance criteria for v1.0.0:**
- [ ] All Layer 1 features production-grade
- [ ] Uptime ≥ 99.5% over 30 days
- [ ] Response latency p95 < 3s
- [ ] Pro tier live with Stripe payments
- [ ] Spoken at ≥1 public event
- [ ] Cited by ≥1 major Dutch media outlet
- [ ] ≥50 registered users

---

### v1.5.0 — Feedback Alpha

> **Target:** January 2027
> **Depends on:** v1.0.0 shipped + legal review on feedback aggregation (see Risk 7)

| WS | Title | Scope | Delegatable to | Est. days |
|---|---|---|---|---|
| WS-F1 | **Feedback Collection UI** | Section-specific commenting on moties/agendapunten. Feedback linked to KG entities. Anonymous + email-verified tiers. | Claude Code agent | 5–8 |
| WS-F2 | **Feedback Clustering** | BERTopic on feedback entries. Dutch stop words + domain-specific topic naming aligned to Rotterdam beleidsgebieden. Manual seed topics. | Claude Code agent | 5–7 |
| WS-F3 | **Verification Tiers** | Progressive: anonymous → email → phone → iDIN. SlowAPI rate limiting. Coordination detection (same device fingerprint + timing analysis). | Claude Code agent | 5–7 |

**Acceptance criteria for v1.5.0:**
- [ ] Citizens can leave feedback on specific moties/agendapunten
- [ ] Feedback clustered into meaningful themes (manual review of 100 entries)
- [ ] 3 verification levels operational
- [ ] ≥1 fractie committed to receive feedback briefings
- [ ] Legal opinion obtained on feedback aggregation → councillor delivery
- [ ] GDPR consent flow (Klaro) operational

---

### v2.0.0 — Full Loop

> **Target:** April 2027
> **Depends on:** v1.5.0 shipped + fractie commitment + legal clearance

| WS | Title | Scope | Delegatable to | Est. days |
|---|---|---|---|---|
| WS-F4 | **Councillor Briefing Generation** | Jinja2 + WeasyPrint templates. Aggregated by theme (BERTopic clusters). Structured output via Instructor + Pydantic. PDF + Word export. | Claude Code agent | 5–7 |
| WS-F5 | **Meeting Schedule Integration** | iBabs or ORI API meeting schedules. Trigger briefing generation T-3 days before vergadering. | Claude Code agent | 3–5 |
| WS-F6 | **Automated Briefing Delivery** | SendGrid email delivery. Raadslid accounts + briefing preferences. Per-dossier subscription. | Claude Code agent | 3–5 |
| WS-F7 | **Anti-Astroturfing v2** | Coordination pattern detection. Content fingerprinting. Admin review dashboard. Never present raw vote counts — always themed aggregation. | Claude Code agent | 5–7 |
| WS-F8 | **Governance Framework** | Formal agreement with ≥1 fractie. Terms of use for feedback contributors. Transparency page explaining how feedback reaches councillors. Data retention policy. | Dennis + jurist | — |

**Acceptance criteria for v2.0.0:**
- [ ] Complete feedback loop: citizen submits → clustered → briefing generated → delivered to councillor
- [ ] ≥1 fractie receives and acknowledges briefings for ≥3 consecutive vergaderingen
- [ ] Anti-astroturfing detects and flags ≥90% of coordinated test attacks
- [ ] GDPR-compliant data retention and deletion
- [ ] Governance agreement signed with participating fractie(s)
- [ ] Raadslid tier (€49/month) live with ≥5 paying subscribers

---

### Workstream creation process (for future versions)

When a new version begins planning:

1. **Create handoff files** in `docs/handoffs/` following the template in [`docs/handoffs/README.md`](../handoffs/README.md): TL;DR, status, owner, dependencies, cold-start prompt, files to read, build tasks, acceptance criteria, eval gate, risks.
2. **Update this plan** (§13) to move the version from "target" to "in progress" with actual dates.
3. **Update the handoffs README** workstream index table.
4. Each workstream handoff should be **self-contained** — an agent picks it up cold and can ship it without reading this master plan.

---

## 14. Key Strategic Risks

### Risk 1: Participation washing

**Risk:** Feedback layer is implemented but councillors don't actually read or respond to briefings.
**Mitigation:** Don't launch Layer 3 without at least 1 formal commitment from a fractie. Frame briefings as themes/questions, not mandates.

### Risk 2: Astroturfing and manipulation

**Risk:** Coordinated flooding of feedback damages credibility.
**Mitigation:** Never present raw vote counts. Always present themes. Progressive verification. Log and surface coordination patterns. Frame as "37 unverified citizens raised betaalbaarheid" with verification-level context.

### Risk 3: Go Vocal expands into Dutch municipal market

**Risk:** Go Vocal (500+ clients) adds LLM comprehension + iBabs integration.
**Mitigation:** Move fast on press recognition. Build Rotterdam-specific ontology. Pursue formal fractie partnerships.

### Risk 4: EU-funded open source arrives

**Risk:** AI4Deliberation, ORBIS, or iDEM produce production-ready tools by 2027.
**Mitigation:** Position NeoDemos as the implementation layer on top of EU research, not a competitor to it. These are academic outputs, not products — they will lack municipal integration, UX, and the political ontology.

### Risk 5: DigiD requirement from municipalities

**Risk:** Municipality requires DigiD for verified participation.
**Mitigation:** Use iDIN (bank-based verification via Signicat, ~€0.15-0.50/verification) as intermediate layer. Available to any company without Logius approval.

### Risk 6: Single-server failure

**Risk:** Hetzner CCX33 downtime takes everything down.
**Mitigation:** Uptime Kuma + Sentry. Automated Hetzner snapshots. Consider read replica when paying users exist.

### Risk 7: Legal risk on feedback aggregation (NEW)

**Risk:** Delivering structured citizen feedback to councillors could intersect with Gemeentewet (formal inspraak procedures), GDPR Article 9 (political opinions as special category data), and Wet open overheid (councillor briefings may become Woo-plichtig).
**Mitigation:** Obtain legal opinion from a jurist with gemeenterecht experience BEFORE building v1.5.0. Design principles ("themes not votes, questions not mandates") are correct but need legal validation. Contact VNG for jurist recommendations. This is a v1.5.0 pre-requisite, not a v1.0.0 blocker.

### Risk 8: Graph database migration (NEW)

**Risk:** Migrating from PostgreSQL CTEs to Neo4j CE mid-stream disrupts working system.
**Mitigation:** Keep PostgreSQL as the authoritative data store. Neo4j is a read-only graph index — if it fails, fall back to CTE-based traversal. ETL sync, not replication. Test migration on staging with full 500K edge set before production switch. Budget 2 weeks for migration including rollback testing.

---

## 15. Founder Unfair Advantages

1. **You are the story.** An elected Rotterdam politician who built civic AI because democracy needed it. That's a Financial Times profile. No competitor can claim this.

2. **You know which questions matter.** Competitors guess what council members need. You ARE one. Build for those exact questions.

3. **Rotterdam is politically explosive right now.** Jeugdzorg deficit, housing crisis, coalition tensions. This is a content factory, not a liability.

4. **Technical infrastructure ahead of all Dutch competitors.** 90K documents, 1.6M chunks, 18 MCP tools, Qwen3-Embedding-8B (MTEB retrieval 70.88 — categorically superior to alternatives), 57K KG edges growing to 500K, custom OAuth, neural reranking. More sophisticated than anything Codi, MAAT, or Raadzaam has demonstrated publicly.

5. **The funding and institutional moment.** GPT-NL piloting with 27 municipalities, €15-20M in EU Horizon funding flowing into this space, Dutch WOO creating data access mandates. The institutional tailwind is exceptional.

6. **Solo founder + AI agentic team = €2K for what competitors spend €40K+ on.** Your unit economics are extraordinary because AI agents do the engineering work. A knowledge graph that would take a 3-person team 6 months costs you €2K and 2 months. This cost advantage is structural, not temporary.

---

## 16. Open Questions & Decisions

### Decided (2026-04-12)

| Question | Decision | Rationale |
|---|---|---|
| Embedding model | **Keep Qwen3-Embedding-8B** | MTEB retrieval +16 pts over BGE-M3. Don't re-embed. |
| Auth server | **Keep custom OAuth 2.1** | Already built. Don't deploy Authentik. |
| Reverse proxy | **kamal-proxy** | Migrated from Caddy on 2026-04-11. |
| Graph DB (v0.2) | **PostgreSQL CTEs + add indexes** | Sufficient for 2-hop. Fix missing indexes immediately. |
| Graph DB (v1.0) | **Evaluate Neo4j CE at v0.5.0** | CTEs can't do 3+ hop pattern matching. Skip Apache AGE. |
| Version structure | **v1.0 = Layer 1, v2.0 = full loop** | Comprehension first, then feedback. |
| Platform access | **Open from day 1, premium later** | All Dutch competitors are gated. Openness is the wedge. |

### Open — Technical

- [ ] **Jina v3 → BGE-reranker swap timing:** Check Jina API ToS for commercial use. If API use is commercially fine (likely), defer swap. If not, prioritize for v0.3.0.
- [ ] **Qwen3 vector quantization:** Apply scalar quantization in Qdrant to reduce 4096-dim storage from ~39GB to ~10GB at 1.6M vectors. Test recall impact.
- [ ] **iBabs API access:** Contact Topicus or Rotterdam ICT for API credentials. Fallback: ORI API covers most needs.
- [ ] **E5-NL benchmark:** Run comparison on 500 Rotterdam queries vs Qwen3. Likely unnecessary given Qwen3's dominance, but worth confirming for Dutch-specific edge cases.

### Open — Product

- [ ] **Feedback verification threshold:** At what verification level does feedback appear in councillor briefings? Needs legal input.
- [ ] **Municipality expansion sequence:** Rotterdam first. Which second? Criteria: ORI integration, politically active, friendly contact.
- [ ] **Language:** Dutch-only MVP. Add English landing page for press/EU positioning at v0.5.0.
- [ ] **First paid customer target:** Which Rotterdam fractie or commissie is most likely to pay €49/month? Who is the political champion?

### Open — Strategic

- [ ] **Open source strategy:** Arguments for (EU credibility, potential funding) vs against (competitor advantage). Defer decision to v1.0.0.
- [ ] **GPT-NL partnership:** Apply for early access via VNG/ICTU. Who is the warm introduction?
- [ ] **Press strategy:** Which journalist gets the first investigation? NRC Binnenland, Trouw Politiek, or Follow the Money?
- [ ] **Legal counsel for v1.5.0:** Find jurist with gemeenterecht + GDPR experience. VNG may have recommendations.

---

## Appendix A: Key Reference URLs

| Resource | URL |
|---|---|
| Qwen3-Embedding-8B | `https://huggingface.co/Qwen/Qwen3-Embedding-8B` |
| OpenRaadsinformatie API | `https://api.openraadsinformatie.nl/v1/elastic/` |
| VNG ODS Open Raadsinformatie | `https://github.com/VNG-Realisatie/ODS-Open-Raadsinformatie` |
| Rotterdam iBabs portal | `https://gemeenteraad.rotterdam.nl` |
| NL Design System | `https://github.com/nl-design-system` |
| Neo4j Community Edition | `https://neo4j.com/deployment-center/` |
| BGE-reranker-v2-m3 | `https://huggingface.co/BAAI/bge-reranker-v2-m3` |
| RobBERT-2023 | `https://huggingface.co/pdelobelle/robbert-2023-dutch-large` |
| BERTopic | `https://github.com/MaartenGr/BERTopic` |
| Docling (IBM) | `https://github.com/docling-project/docling` |
| Polis community | `https://github.com/polis-community` |
| Instructor | `https://python.useinstructor.com` |
| Klaro (GDPR consent) | `https://klaro.org` |
| Uptime Kuma | `https://github.com/louislam/uptime-kuma` |
| Go Vocal (competitor) | `https://www.govocal.com` |
| Codi.nu (competitor) | `https://codi.nu` |
| EU AI4Deliberation | `https://cordis.europa.eu/project/id/101178806` |

## Appendix B: Cost Estimates

### Monthly operating costs (current)

| Item | Cost |
|---|---|
| Hetzner CCX33 | ~€45/month |
| Nebius API (Qwen3 embeddings) | ~€5/month (re-embedding is ~€3 one-time) |
| Jina reranker API | ~€10–20/month |
| Anthropic API (Claude Sonnet) | ~€20–50/month |
| Groq/Together (smaller LLM tasks) | ~€5–10/month |
| SendGrid (free tier) | €0 |
| Sentry Cloud (free tier) | €0 |
| **Total current** | **~€85–130/month** |

### Additional costs at v1.0.0

| Item | Cost |
|---|---|
| Neo4j CE (if deployed, self-hosted) | €0 (GPL, self-hosted on Hetzner) |
| Stripe transaction fees | 1.4% + €0.25 per transaction |
| iDIN verification (v1.5.0+) | ~€0.15–0.50 per check |

### One-time knowledge graph build cost

| Item | Cost |
|---|---|
| Gemini Flash enrichment (WS1 phase A) | ~€90–130 |
| Flair NER (CPU, self-hosted) | €0 (runs on CCX33) |
| Accuracy audit (Claude Opus, 200 samples) | ~€5 |
| **Total KG build** | **~€95–135** |

---

## Document History

| Date | Change | Author |
|---|---|---|
| 2026-04-12 | Initial version. Incorporates strategic planning sessions, competitive analysis, tech decisions (Qwen3 confirmed, AGE skipped, Neo4j eval at v0.5), versioned roadmap v0.2→v2.0. | Dennis + Claude |

---

*This is the living strategy document for NeoDemos. Update after each major decision or version milestone. Execution-level details belong in workstream handoffs at `docs/handoffs/`, not here.*
