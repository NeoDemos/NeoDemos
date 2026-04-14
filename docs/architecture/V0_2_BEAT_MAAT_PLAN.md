# NeoDemos v0.2 → v0.4 Plan — Beat MAAT

> **Status:** Planning · **Date:** 2026-04-10 · **Owner:** Dennis · **Replaces:** the Flair-NER-only v0.2 entry in `docs/VERSIONING.md` (those items are folded into Workstream 1 below)
>
> **Reading order:** Section 1 (critical eval of MAAT) → Section 2 (strategic priorities) → Sections 3–8 (workstreams) → Section 9 (sequencing) → Section 10 (risks).
>
> ## ⚠️ For agents picking up work
>
> This document is the **strategy doc**, not the agent task list. Each workstream has a **self-contained agent-ready handoff** at [`docs/handoffs/`](../handoffs/README.md) with cold-start prompts, file paths, acceptance criteria, eval gates, and risks. **Do not work directly from this plan — pick up the relevant handoff:**
>
> | Workstream | Handoff file | Status |
> |---|---|---|
> | WS1 GraphRAG | [`docs/handoffs/WS1_GRAPHRAG.md`](../handoffs/WS1_GRAPHRAG.md) | not started |
> | WS2 Trustworthy financial | [`docs/handoffs/done/WS2_FINANCIAL.md`](../handoffs/done/WS2_FINANCIAL.md) | not started |
> | WS3 Document journey | [`docs/handoffs/WS3_JOURNEY.md`](../handoffs/WS3_JOURNEY.md) | not started |
> | WS4 Best-in-class MCP | [`docs/handoffs/WS4_MCP_DISCIPLINE.md`](../handoffs/WS4_MCP_DISCIPLINE.md) | not started |
> | WS5a Nightly pipeline | [`docs/handoffs/WS5a_NIGHTLY_PIPELINE.md`](../handoffs/WS5a_NIGHTLY_PIPELINE.md) | not started |
> | WS5b Multi-portal (search-only) | [`docs/handoffs/WS5b_MULTI_PORTAL.md`](../handoffs/WS5b_MULTI_PORTAL.md) | deferred to v0.2.1 |
> | WS6 Source-spans summarization | [`docs/handoffs/WS6_SUMMARIZATION.md`](../handoffs/WS6_SUMMARIZATION.md) | **in progress** — Phase 3 DB backfill running (2026-04-14) |
>
> Index, parallelism map, house rules, and how-to-invoke-an-agent are in [`docs/handoffs/README.md`](../handoffs/README.md).

---

## 1. Critical Evaluation of MAAT vs Best Practices

### 1.1 What MAAT is (verified from public sources)

MAAT (AethiQs) is a **closed-UI semantic search + summarization shell** that sits on top of the existing iBabs/Notubiz/GO/Qualigraf council systems. Confirmed feature set:

| Feature | Source | Notes |
|---|---|---|
| Closed-domain RAG (chunked docs + transcripts + cite-returning generator) | [Waalwijk pilot press release](https://aethiqs.nl/gemeente-waalwijk-start-pilot-met-ai%E2%80%91assistent-maat/), [Emerce wire](https://www.emerce.nl/wire/gemeente-waalwijk-start-pilot-aiassistent-maat-begrotingscyclus-2026) | **Pipeline never publicly disclosed.** The words "RAG", "embedding", "vector store", "LLM", "knowledge graph" appear zero times across all AethiQs marketing. Verbatim Waalwijk copy: *"baseert antwoorden alleen op de ingelezen documenten en transcripties"* + *"directe bronverwijzingen (documentpassage of vergaderfragment)"* — textbook naive single-round RAG. Front-end inspected from Zoetermeer live page: jQuery + Tailwind + Azure Blob CDN (`aetazusamaatfrontend.blob.core.windows.net`) confirmed. Backend is **likely Django** (CSRF token pattern in forms) and **likely Elasticsearch** (referenced in page inspection), both inferred not confirmed — AethiQs has not disclosed their stack. |
| **Public (free) tier = BM25 only** | Live inspection of [zoetermeer.aethiqs-maat.nl/search/](https://zoetermeer.aethiqs-maat.nl/search/) | Natural-language query *"Wanneer sprak de pvda over parkeren"* → **0 results**. Keyword query *"pvda parkeren"* → 12. The `Doorzoek de inhoud van alle relevante tekst- en vergaderfragmenten` button (the actual RAG feature) is **greyed out for non-logged-in users**. AI toolbox is paywalled. **MAAT has structurally chosen not to serve citizens with AI.** |
| Nightly auto-transcripts of meetings | [aethiqs.nl/maat/raadsinformatie/](https://aethiqs.nl/maat/raadsinformatie/) | Quality not disclosed |
| Second-level webcast playback | Zoetermeer customer page | UI feature |
| Multi-portal: iBabs, Notubiz, GO, Qualigraf | VNG | Confirmed integrations |
| Dossier builder + export | VNG | UI workflow |
| GenAI summarization + Q&A in dossier | VNG, Waalwijk pilot | Constrained-context chat |
| Source citations | Binnenlands Bestuur | Required by gemeenten |
| Closed Azure West-EU tenancy | aethiqs.nl | Sovereignty story |
| Customers | VNG | 10 gemeenten + Ministry SZW (Apeldoorn, Zoetermeer, Maastricht, Enschede, Bodegraven, Emmen, Het Hogeland, Westerkwartier, Westerwolde, + SZW) |

### 1.2 What MAAT does NOT publish (and what that tells us)

| Best practice | MAAT public position | Implication |
|---|---|---|
| **Eval scoreboard** (precision/faithfulness/recall) | None published | They cannot be benchmarked. The UK i.AI [Consult/ThemeFinder](https://github.com/i-dot-ai/themefinder) project publishes F1 = 0.79–0.82 against human reviewers — *that* is the bar in govtech 2026 |
| **Open MCP/API surface** | None — closed UI only | Customers cannot wire MAAT into their own LLM agents. [European Parliament's Archibot](https://claude.com/customers/european-parliament) and [Riksdagsmonitor](https://github.com/Hack23/riksdagsmonitor) (32 MCP tools) are the new bar |
| **Knowledge graph / structured retrieval** | Zero public mentions — not in any press release, case study, customer article, or griffier interview. Their "timeline" widget is a flat chronological accordion UI (client-side only), not a traversal graph. | Pure semantic search hits a ceiling on multi-hop questions (ACL 2025: legal RAG hallucination rate still 17% for top commercial systems — see [Stanford legal RAG study](https://dho.stanford.edu/wp-content/uploads/Legal_RAG_Hallucinations.pdf)). **NeoDemos already has 57K typed KG edges today** (`LID_VAN`, `DIENT_IN`, `STEMT_VOOR/TEGEN`, `SPREEKT_OVER`, `BETREFT`) with 500K–1M targeted in WS1 — a structural capability MAAT's architecture does not contain at all. |
| **Structured numeric extraction** for budget docs | "Answers questions about the begroting" — no schema | Pure-text RAG over jaarstukken paraphrases numbers. This is *the* failure mode for begrotingscyclus questions |
| **Provenance / verification tokens** | "Source citations" only | FactSet's enterprise MCP framework explicitly calls out "output filtering before response" as a defense-in-depth requirement |
| **Multi-municipality comparison** | Per-gemeente by construction | Closed-tenancy architecture cannot answer "hoe staat Rotterdam tegenover Amsterdam over warmtenetten" |
| **Document journey** (motie → committee → council → vote → outcome → news) | Not advertised | Closest thing is "timeline" in their UI; appears to be flat chronological list, not a causal chain |

### 1.3 Critical assessment

MAAT is a **2023-era RAG product packaged as municipal SaaS**. Its competitive moat is sales (10 paying gemeenten) and integrations (iBabs/Notubiz/GO/Qualigraf), not architecture. On every dimension that matters in 2026 — open agent surfaces, GraphRAG, structured numeric grounding, public eval — they are **silent or absent**.

We do not need to copy MAAT's UX feature-for-feature. We need to ship the things they **structurally cannot** do because their architecture is a closed semantic-search shell.

### 1.4 Best-practice references we will copy from

- **FactSet enterprise MCP** ([Part 1](https://insight.factset.com/enterprise-mcp-model-context-protocol-part-one), [Part 3](https://medium.com/@factset/enterprise-mcp-part-3-security-and-governance-27ec39380bbe)): tool descriptions as API contracts, central tool registry, vector-DB tool selection, defense-in-depth (tool/parameter/resource/output authorization), OAuth dynamic client registration, OBO token exchange, audit logging without secrets.
- **Figma MCP** ([Tools and Prompts](https://developers.figma.com/docs/figma-mcp-server/tools-and-prompts/)): `verb_noun` naming, `get_metadata` returning sparse outlines for cheap exploration before expensive full retrieval, `create_design_system_rules` style "context primer" tool.
- **Anthropic Code Execution with MCP** ([engineering blog](https://www.anthropic.com/engineering/code-execution-with-mcp)): expose MCP servers as a code filesystem, progressive disclosure, in-execution filtering, 98.7% token savings on large workflows.
- **UK i.AI Consult / ThemeFinder** ([github.com/i-dot-ai/themefinder](https://github.com/i-dot-ai/themefinder)): public F1 evaluation, open source, theme extraction over structured Q&A data, used by No10 data science team.
- **European Parliament Archibot** ([claude.com customers](https://claude.com/customers/european-parliament)): Claude over 2.1M docs, 80% search-time reduction — production proof that LLM-over-parliament-archive works at scale.
- **Riksdagsmonitor** ([github.com/Hack23/riksdagsmonitor](https://github.com/Hack23/riksdagsmonitor)): 32 MCP tools, multi-party balance, GDPR-compliant OSINT, 14 languages — the open-source counterpart we should benchmark against.
- **Multi-round legal RAG** ([ACM MM 2025](https://dl.acm.org/doi/10.1145/3731715.3733451)): iterative query refinement → 78.67% recall on contracts. Directly applicable to the document journey workstream.

---

## 2. Strategic Priorities (in order)

The user-stated ranking, locked for v0.2–v0.4:

1. **GraphRAG features** (highest impact, MAAT cannot match)
2. **Trustworthy financial analysis** (begrotingscyclus = where MAAT is winning sales conversations today)
3. **Document journey timelines** — arrival → committee → council → moties → vote → outcome
4. **Best-in-class MCP** modeled on FactSet/Figma
5. **100% reliable auto-ingestion**
6. **Multi-portal connectors** — *search-only first, AI tooling later*
7. **Timestamped video-segment quotes**
8. **GenAI summarization** evolving NeoDemos Analyse

The order matters: items 1–4 are the *moat*. Items 5–8 are *parity table-stakes* without which the moat cannot be demoed.

### 2.1 Strategic wedge — Public-AI-by-default

MAAT's public tier is BM25 keyword search in a prettier wrapper; every AI feature (semantic `doorzoek`, summarization, chat, dossier) is **behind a paid login**. This is the single largest asymmetric opening in the Dutch govtech market right now.

**Hard design constraint, applies across every workstream in this plan:**

> **Every MCP tool and every RAG endpoint that does not contain PII MUST be callable unauthenticated in production.** Rate-limiting and CAPTCHA where needed, but no login wall on semantic search, no login wall on summarization, no login wall on `traceer_motie`, no login wall on `vraag_begrotingsregel`. Login is reserved for *personalization* (saved searches, dossier builder, alerts) — never for *access*.

This is free to ship (it's a policy, not code) and it reframes the entire v0.2 marketing narrative. Every existing workstream must be audited against this constraint before v0.2.0 ships. The public MCP endpoint and public web UI are both in scope.

**Govtech precedent:** the European Parliament ships [Archibot](https://claude.com/customers/european-parliament) (aka "Ask the EP Archives") as a public anonymous dashboard at [archidash.europarl.europa.eu/ep-archives-anonymous-dashboard](https://archidash.europarl.europa.eu/ep-archives-anonymous-dashboard) — the URL literally contains "anonymous". The EP built a RAG assistant over 1952-era legislative history (Claude 3 Sonnet on Amazon Bedrock, ~75K indexed files per third-party analysis — Bussola-Tech; the EP's own marketing says "2.1M documents" which is the archive size, not the indexed corpus), no login required, 50+ languages. If the EP can ship an anonymous AI dashboard over European legislative archives, a Dutch gemeente can ship one over its begroting. This precedent dissolves the procurement objection MAAT's paywall implicitly accepts.

---

## 3. Workstream 1 — GraphRAG Retrieval (Priority 1)

**Goal:** Ship the first user-facing GraphRAG feature in NeoDemos. A query like *"hoe heeft Leefbaar Rotterdam zich opgesteld over warmtenetten in de afgelopen 4 jaar?"* should fan out across the KG (`LID_VAN`, `DIENT_IN`, `STEMT_VOOR/TEGEN`, `SPREEKT_OVER`), pull supporting chunks, and return a structured, fully-cited answer.

### 3.1 Foundations (folding in former v0.2/v0.3 from VERSIONING.md)

These were already planned. They are **prerequisites** for GraphRAG, not separate work:

- [ ] **[Quick win — do first] Layer 1 enrichment over chunk text, not just doc title** — current state populates `key_entities` on 28% of chunks via gazetteer-match against the **parent document title** only. Chunks whose body mentions a known entity ("Heemraadssingel", "Middelland") but whose document title is generic ("Voortgangsrapportage parkeren 2024") get zero tags → invisible to the Qdrant payload filter. Cheap fix: second pass of [`scripts/enrich_and_extract.py`](scripts/enrich_and_extract.py) scanning chunk bodies against the existing 2.217-entry `data/knowledge_graph/domain_gazetteer.json`. No NER, no LLM, no new dependencies. Closes the most common location-query failure mode (see [FEEDBACK_LOG.md 2026-04-11](../../brain/FEEDBACK_LOG.md) Heemraadssingel entry) before Flair even runs. **Added 2026-04-11.**
- [ ] **Flair `ner-dutch-large`** on all 1.6M chunks (key_entities coverage 28% → ~65%, combined with the quick-win pass above expect ~75%) — already scoped in old v0.2.0
- [ ] **BAG-based location layer as KG edges** *(added 2026-04-11)* — import [BAG](https://www.pdok.nl/introductie/-/article/basisregistratie-adressen-en-gebouwen-ba-1) joined to [CBS Wijk- en Buurtkaart](https://www.cbs.nl/nl-nl/dossier/nederland-regionaal/geografische-data/wijk-en-buurtkaart-2024) and emit `LOCATED_IN` edges in `kg_relationships`. For Rotterdam: ~5.000 straten + ~80 buurten + 14 gebieden + 1 gemeente. After import, a query for "Heemraadssingel" walks one hop to "Middelland" → "Delfshaven" → "Rotterdam" via the existing graph traversal — no separate lookup table needed. **Critical multi-tenant design constraints, locked now, paid back by WS5b:** (1) primary key for Location nodes is the **BAG 16-digit `openbare_ruimte` ID**, NOT the street name (street names collide across municipalities — "Hoofdstraat" in 100+ towns); (2) every `Location` entity has a mandatory `gemeente` attribute; (3) `LOCATED_IN` edges carry a `level` attribute (`buurt`/`wijk`/`gebied`/`stadsdeel`/`gemeente`) so Amsterdam stadsdelen, Utrecht wijken-only, and Rotterdam gebieden all fit the same schema without per-tenant migrations. These three choices add zero work in v0.2.0 but make v0.2.1 multi-portal expansion essentially free for the location layer.
- [ ] **Gemini Flash Lite enrichment**: `answerable_questions` + section_topic refinement + semantic relationships (`HEEFT_BUDGET`, `BETREFT_WIJK`, `SPREEKT_OVER`) — old v0.2.0, ~$90–130. `BETREFT_WIJK` should resolve targets to BAG-canonical Location IDs rather than free-text wijk names.
- [ ] **kg_relationships scale-up:** 57K → ~500K–1M edges (Flair semantic relationships dominate; BAG hierarchy adds the ~5K location skeleton)
- [ ] **Quality audit:** SQL counts + deterministic spot-checks + LLM-judge sample of 200 entities, plus a regression check that the [FEEDBACK_LOG.md 2026-04-11](../../brain/FEEDBACK_LOG.md) Heemraadssingel query now returns chunks (was 0 hits)

### 3.2 New build

- [ ] **`services/graph_retrieval.py`** ([new file](services/graph_retrieval.py))
  - `extract_query_entities(query) -> list[Entity]` — Flair NER + gazetteer match + politician registry alias resolution
  - `walk(seed_entities, max_hops=2, edge_types=...)` — recursive PostgreSQL CTE traversal of `kg_relationships`
  - `score_paths(paths) -> ranked` — penalize long paths, boost paths matching query intent
  - `hydrate_chunks(entity_ids) -> Chunk[]` — fetch chunks where entities appear via existing `chunk_entities` join
- [ ] **Hybrid graph + dense retrieval** — extend [services/rag_service.py:70](services/rag_service.py#L70) so the existing 4-stream parallel fan-out gets a 5th stream: `graph_walk`. Reuse the existing Jina rerank to merge.
- [ ] **Entity-based Qdrant pre-filtering** — add `entity_ids: int[]` to Qdrant payloads at promote-time so a graph walk can prune the vector search to *only* chunks mentioning the resolved entities (huge speedup).
- [ ] **New MCP tool: `traceer_motie(motie_id)`** in [mcp_server_v3.py](mcp_server_v3.py)
  - Walks: motie → DIENT_IN → indieners → LID_VAN → partijen → STEMT_VOOR/TEGEN → uitkomst → BETREFT (wijk/programma) → linked notulen fragments
  - Returns a structured "trace" object: `{motie, indieners, vote, outcome, related_documents, citation_chain}`
  - This is the **flagship demo** for Workstream 1 — no other Dutch product does this
- [ ] **New MCP tool: `vergelijk_partijen(topic, partijen[], date_from, date_to)`**
  - Pulls each party's chunks via `LID_VAN ∩ SPREEKT_OVER`, runs the existing reranker, returns side-by-side
- [ ] **Cross-document motie ↔ notulen vote linking** (was planned for v0.4) — promoted into v0.2 because it is a hard prerequisite for `traceer_motie`

### 3.3 Eval target

- [ ] Add 10 multi-hop questions to [rag_evaluator/data/questions.json](rag_evaluator/data/questions.json) requiring graph walk
- [ ] **Completeness target: 2.75 → ≥3.5** (the v0.3 target was 3.25 — raise it now that we have actual graph retrieval)
- [ ] Faithfulness must not drop (≥4.5 baseline)

---

## 4. Workstream 2 — Trustworthy Financial Analysis (Priority 2)

**Goal:** When a council member asks *"wat is de begrotingsruimte voor wijkveiligheid in 2026 en hoe is dat veranderd t.o.v. 2024?"* the answer is **a number, not a paraphrase**, with the exact line item, source PDF, and page reference. Zero hallucination of euros.

### 4.1 The MAAT failure mode we're targeting

Pure-text RAG over jaarstukken/voorjaarsnota produces sentences like *"Het programma Veilig kreeg ongeveer 80 miljoen euro"* — paraphrased, lossy, and **wrong** when the actual line item is €82.4M, split across two sub-programs, with €3.1M ravijnjaar 2026 cuts. Council members notice this immediately and lose trust.

### 4.2 Build

- [ ] **Structured `table_json` retrieval** — already exists in `pipeline/financial_ingestor.py` via Docling but is currently mixed into the same chunk soup. Build a dedicated **Postgres table**: `financial_lines (id, gemeente, document_id, page, programma, sub_programma, jaar, bedrag_eur, bron_chunk_id, table_id, row_idx, col_idx)` populated from the `table_json` blobs.
- [ ] **New MCP tool: `vraag_begrotingsregel(gemeente, jaar, programma, sub_programma=None)`** in [mcp_server_v3.py](mcp_server_v3.py)
  - Returns exact line items as a **structured payload**, not a paraphrase: `{programma, bedrag_eur, jaar, source_pdf, page, table_cell_ref}`
  - Paginates if multiple matches; deterministic
- [ ] **New MCP tool: `vergelijk_begrotingsjaren(gemeente, programma, jaren[])`**
  - Returns time-series of line items with absolute and percentage delta
- [ ] **`zoek_financieel` upgrade** ([mcp_server_v3.py:349](mcp_server_v3.py#L349)) — when the query mentions a specific programma/jaar, route to the structured tool first; fall back to text RAG only for narrative questions
- [ ] **Verification token** — every numeric answer from these tools includes a `verification` field: `{table_cell_ref, sha256_of_source_chunk, retrieved_at}`. UI/agent can re-fetch and assert the cell content unchanged.
- [ ] **Coverage**: backfill `financial_lines` for jaarstukken + voorjaarsnota + begroting + 10-maandsrapportage 2018–2026 (existing scripts in [scripts/run_financial_batch.py](scripts/run_financial_batch.py))
- [ ] **Waalwijk financial docs ingested in v0.2.0** alongside Rotterdam *(added 2026-04-11)*. Waalwijk is on **iBabs** (`waalwijk.bestuurlijkeinformatie.nl` — confirmed live with financial documents visible). Cost: one tenant config entry. Their public financial docs (begroting 2025 confirmed at `waalwijk.begrotingsapp.nl/begroting-2025` and on iBabs; jaarstukken 2024, voorjaarsnota, 10-maands) go through the exact same `financial_ingestor.py` → `financial_lines` pipeline we're building for Rotterdam. This is zero new engineering and proves the WS2 pipeline is gemeente-agnostic from day one.

### 4.3 Eval target

- [ ] **Numeric accuracy benchmark**: 30 hand-curated "exact-number" questions where the answer is a euro amount. **Target: 100% exact match** (not "within 10%"). This is the trust contract.
- [ ] Hallucination floor: zero euros in any answer that don't appear verbatim in `financial_lines`
- [ ] **Waalwijk counter-demo** *(updated 2026-04-11)*. WS2 delivers for Rotterdam in v0.2.0 exactly what Waalwijk is paying MAAT to pilot-evaluate. Since we're also ingesting Waalwijk's iBabs financial docs in v0.2.0, the counter-demo costs zero extra work.
  - **Pilot status (verified 2026-04-11):** started ~25 September 2025 ([Dutch IT Channel](https://www.dutchitchannel.nl/news/712416/gemeente-waalwijk-lanceert-ai-assistent-maat-voor-begrotingscyclus-2026)), no published end date, no evaluation report found. Window still open. AethiQs is actively using Waalwijk as sales collateral to offer temporary access to other municipalities during the 2026 budget period.
  - **Key quote** — Jeroen Deneer (gemeentesecretaris Waalwijk): *"Met MAAT verwachten we dat technische vragen sneller en consistenter beantwoord kunnen worden, zonder extra druk op de ambtelijke organisatie."* These are the exact questions `vraag_begrotingsregel` answers with a specific euro amount and table cell reference.
  - **Note:** comparison in step 3 is against MAAT's free public tier (BM25 only) — we cannot test their paid RAG. Acknowledge this explicitly in the published piece; the relevant question is whether their paid tier returns exact line items or paraphrased prose, which their marketing does not address.
  1. From Waalwijk's iBabs (`waalwijk.bestuurlijkeinformatie.nl`), pull the begrotingsbehandeling raadsvergadering for November 2024 — the public record of questions asked when begroting 2025 was debated.
  2. Run those questions against `vraag_begrotingsregel` + `vergelijk_begrotingsjaren` → `{programma, bedrag_eur, jaar, page, table_cell_ref}` per answer.
  3. Run same questions against MAAT free public tier → document the BM25-only gap.
  4. Publish timed to Waalwijk's evaluation report or AethiQs' next public knowledge event.

---

## 5. Workstream 3 — Document Journey Timelines (Priority 3)

**Goal:** Visualize the *causal chain* of how a document arrived in the system, was discussed in a committee, debated in the council, voted on, and what moties/amendementen were attached. This is fundamentally *not* what MAAT's flat chronological timeline does.

### 5.1 The data model

Add a new Postgres view `document_journeys`:

```
document_journey (
  root_document_id,           -- the originating raadsvoorstel/raadsbrief
  events JSONB[]              -- ordered list of:
                              --   {type: 'arrival', meeting_id, date, source_portal}
                              --   {type: 'committee', meeting_id, date, agenda_item_id}
                              --   {type: 'council', meeting_id, date, agenda_item_id}
                              --   {type: 'motie',     motie_id, indieners, outcome}
                              --   {type: 'amendement',amendement_id, indieners, outcome}
                              --   {type: 'vote',      uitkomst, voor, tegen, per_partij}
                              --   {type: 'press',     news_url}            (future)
                              --   {type: 'webcast',   meeting_id, start_seconds, end_seconds}
)
```

This is computable from existing tables: `documents`, `meetings`, `agenda_items`, `kg_relationships` (DIENT_IN, BETREFT_DOCUMENT, STEMT_VOOR/TEGEN), and the new motie↔notulen linking from Workstream 1.

### 5.2 Build

- [ ] **`services/journey_service.py`** — `build_journey(root_document_id)` materializes the view above
- [ ] **New MCP tool: `traceer_document(document_id)`** — returns the journey JSON, ordered chronologically, with citation IDs at every step
- [ ] **UI route `/journey/{document_id}`** in [main.py](main.py) + new template `templates/journey.html` rendering a vertical timeline (D3 or just clean HTML/CSS — no need for a heavy charting library)
  - Each event card has: date, type icon, summary, "open fragment" link, "play webcast at this point" link (Workstream 5)
- [ ] **From any search result** in `templates/search.html`, add a "📜 Tijdlijn" button that opens the journey view
- [ ] **Cross-event RAG**: in the journey view, a side panel runs `vraag_aan_dossier` (Workstream 6) constrained to *only* the journey's events — so the council member can ask "is the budget impact ever discussed in any of the committee debates?"

### 5.3 Eval target

- [ ] Manually validate 20 journeys end-to-end against the canonical gemeenteraad records. Target: 100% of expected events captured, ≤2 false positives across all 20.

---

## 6. Workstream 4 — Best-in-Class MCP Surface (Priority 4)

**Goal:** Make the NeoDemos MCP server the **reference implementation for govtech MCP**, modeled on FactSet's enterprise patterns and Figma's tool design discipline. Customers should be able to wire this into Claude/ChatGPT/Perplexity/Cursor with zero friction.

### 6.1 Tool design discipline (apply to all current + new tools)

Adopt these conventions repository-wide ([mcp_server_v3.py](mcp_server_v3.py)):

- [ ] **`verb_noun` naming** in Dutch (`zoek_*`, `haal_*_op`, `lijst_*`, `traceer_*`, `vergelijk_*`, `vraag_*`, `analyseer_*`, `lees_*`). Already mostly compliant; audit and fix outliers.
- [ ] **Tool descriptions written for AI consumption, not humans** (FactSet rule). Each description must answer: *when should the LLM pick this tool over the others?* — include negative cases ("do NOT use for X, use Y instead").
- [ ] **Sparse-then-dense pattern** (Figma `get_metadata`): every "list" tool returns a sparse outline with IDs only; the LLM follows up with `lees_fragment` for full content. Already partly true — formalize it.
- [ ] **Centralized tool registry** — new file `services/mcp_tool_registry.py` listing every tool with metadata (`name, scopes, latency_p50_ms, cost_per_call_usd, output_schema`). Used for: FactSet-style tool-collision detection, automatic OpenAPI export, audit logging schema.
- [ ] **Tool-selection embedding index** (FactSet pattern): build a small Qdrant collection `mcp_tool_descriptions` with embeddings of every tool description. At server startup, score uniqueness; warn if any pair's cosine > 0.9.

### 6.2 New "context primer" tool (Figma `create_design_system_rules` analogue)

- [ ] **`get_neodemos_context()`** — zero-arg tool returning a structured primer the LLM can read on first connect:
  - Available gemeenten + their portals
  - Date coverage per source
  - Council composition (parties, seats, current coalition)
  - **Current wethouders by portfolio** *(added 2026-04-11)* — name, party, start date, portfolios; generated from `persons_roles` table at server boot, never hardcoded. This is the fix for LLM role-date hallucination: LLMs confidently guess political tenure from training data (e.g. "wethouder until 2026 elections" when the actual switch was mid-coalition in July 2024). The right fix is data in the primer, not behavioral instructions in tool descriptions.
  - Document type taxonomy
  - Known limitations ("financial lines only available 2018+ for Rotterdam")
  - Recommended tool sequences for common questions ("voor begrotingsvragen, gebruik eerst `vraag_begrotingsregel`, dan `zoek_financieel` voor toelichting")

### 6.3 Security & governance (FactSet defense-in-depth)

- [ ] **Tool-level scopes** — extend [services/mcp_oauth_provider.py:13](services/mcp_oauth_provider.py#L13) so each tool declares required scopes; reject calls without them
- [ ] **Parameter validation layer** — central decorator `@validated_params` enforcing length caps (10K char default), type bounds, gemeente whitelist
- [ ] **Output filter** — every tool result passes through `services/output_filter.py` that strips PII the user's scope doesn't grant access to
- [ ] **Audit log** — `mcp_audit_log` table: `(ts, user_id, tool, params_hash, latency_ms, result_size, scope_used, ip)`. **Never** log secrets/tokens/raw params (FactSet rule).
- [ ] **Anomaly detection** — flag unusual tool sequences, rapid-fire patterns, or parameter combinations no human would try (FactSet's "AI-specific monitoring"). Start with simple rate limits in v0.2; ML in v0.4+.

### 6.4 Code-execution-friendly distribution (Anthropic pattern)

- [ ] **Generate a TypeScript stub package** at build time: `services/mcp_codegen.py` reads the registry → emits `dist/neodemos-mcp/<tool>.ts` wrapper functions matching Anthropic's [code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) pattern
- [ ] **Publish to npm as `@neodemos/mcp-tools`** — agents using Claude Code or similar code-first MCP harnesses get 98.7% token savings out of the box
- [ ] **README in the npm package** with the recommended workflow: import → call typed functions → only the relevant data hits the model context

### 6.5 Public MCP catalog — direct competitive wedge vs MAAT's closed UI

MAAT has no documented API, no MCP surface, no OpenAPI — the product *is* the Django UI. This makes our tool contracts themselves a marketing artifact.

- [ ] **`docs/mcp-catalog.md`** — auto-generated from `mcp_tool_registry.py` on every release, committed to the repo, served at `neodemos.nl/mcp`. Each tool entry contains: name, description-for-agents, parameters, output schema, example call, example LLM interaction, latency p50, cost per call, and a red-team section listing what the tool will refuse. FactSet "tool contract as API contract" pattern made public.
- [ ] **Browsable tool gallery** at `/mcp` with a "Try in Claude Desktop" button per tool that emits the right MCP client config. Zero-friction onboarding for journalists, researchers, and other gemeenten.
- [ ] Linked from the homepage and from every search result ("Deze resultaten zijn ook opvraagbaar via MCP — [bekijk de tool](neodemos.nl/mcp/zoek_raadsstuk)")

### 6.6 Mistral Le Chat compatibility — Europe-first amplifier *(added 2026-04-11)*

NeoDemos is European civic infrastructure. Shipping only on Claude/ChatGPT clients quietly cedes the European-AI-sovereignty narrative to the closest competitor that ships a Mistral integration. Le Chat has been the only major chat client with **free-tier** MCP custom connectors since 2025-09-02 (Notion/GitHub/Linear/Stripe ship in their official directory), supports OAuth 2.1 + DCR + streamable HTTP `/mcp`, and reaches mobile (iOS/Android) — three properties Claude.ai does not have today.

The transport, OAuth, and discipline work in §6.1–§6.4 *already produces* a Le Chat-compatible server. Verified 2026-04-11: NeoDemos's existing `mcp_server_v3.py` + `services/mcp_oauth_provider.py` + MCP SDK 1.26 emit a 401 + `WWW-Authenticate: Bearer ..., resource_metadata="..."` per RFC 9728 (the one hard requirement of Le Chat's auth-detection probe), and the FastMCP-generated tool schemas are clean of `$id`/`$schema`/`$defs`/`$ref` (the Mistral grammar-compiler rejection list per [litellm#13389](https://github.com/BerriAI/litellm/pull/13389)). Marginal engineering work: ~2 days, scoped to a single subsection in [WS4 §Mistral Le Chat compatibility](../handoffs/WS4_MCP_DISCIPLINE.md).

The strategic adds are:
- **Public no-auth `https://mcp.neodemos.nl/public/mcp`** — second FastMCP instance over the same registry, registers only retrieval tools tagged `public=True`. A journalist or citizen pastes one URL into Le Chat free, no login. This is the §2.1 public-AI-by-default constraint, made executable in the single chat client whose business model permits it. Claude.ai's connectors require Pro; OpenAI's require ChatGPT Plus; Le Chat's are free. The wedge is asymmetric and time-limited.
- **Mobile reach** via Le Chat iOS/Android — NeoDemos has no other mobile path before v0.3.0 PWA work.
- **EU sovereignty story made literal** — French model, German hosting (Hetzner FSN), Dutch council data, zero US hop. This is the procurement-friendly narrative for Dutch gemeenten that Claude/ChatGPT integrations *cannot* match without re-routing through US infrastructure.
- **Output discipline as a forcing function** — Mistral Medium 3.1 / Magistral Medium 1.2 / Small 3.2 (Le Chat's model router rotates among these) are weaker at long-context and ambiguous tool selection than Claude Opus. Designing for them improves the experience for *every* client. Mitigations (server-side temporal-extraction fallback, pre-rendered Markdown answer skeletons, strict tool-budget cap ≤25) are detailed in [WS4 §Output discipline for weaker models](../handoffs/WS4_MCP_DISCIPLINE.md).

**Pitch one-liner:** *"NeoDemos is the only Dutch civic intelligence platform that ships free-tier MCP on the European chat assistant Dutch gemeenten can procure without a US hop. MAAT's product is a closed Django UI; ours is a tool contract that any LLM in Le Chat, Claude, or ChatGPT can call."*

### 6.5 Eval target

- [ ] All current + new tools have `output_schema` declared in the registry
- [ ] No two tool descriptions have cosine similarity > 0.85
- [ ] First-call agent task success rate (without `get_neodemos_context` primer) measured against baseline; primer should improve it by ≥15 percentage points

---

## 7. Workstream 5 — 100% Reliable Auto-Ingestion + Multi-Portal (Priorities 5, 6, 7)

**Goal:** A meeting that ends Tuesday at 22:00 is fully indexed with timestamped transcripts by Wednesday 06:00, **automatically, every time**. New gemeenten can be added in *search-only* mode without rebuilding the AI stack for them.

### 7.1 Reliability before scope

The current state is *partial* automation: [main.py:69](main.py#L69) runs `refresh_service.check_and_download()` every 15 minutes via APScheduler, but the rest of the pipeline (transcription, chunking, embedding, KG enrichment, promotion) is manual. The user requirement is **100% reliability** — that means observability and idempotency *before* multi-portal scope.

- [ ] **Job graph**, each step idempotent and resumable via Postgres-backed state:
  - `01_discover_meetings` — poll all source adapters since last successful run
  - `02_download_documents` — fetch PDFs, dedupe by hash
  - `03_download_webcasts` — fetch MP4s for committee meetings (only those with new fragments)
  - `04_transcribe` — [pipeline/extractor.py](pipeline/extractor.py) + [pipeline/transcript_postprocessor.py](pipeline/transcript_postprocessor.py)
  - `05_chunk_and_stage` — write to staging Qdrant collection
  - `06_kg_enrich` — Flair + Gemini metadata pass on new chunks only
  - `07_promote` — staging → production after eval-pass check
- [ ] **Postgres advisory lock** (`SELECT pg_advisory_lock(42)`) around any writer step. Honors the **never-write-while-embedding** rule from `memory/project_embedding_process.md`. Search reads continue uninterrupted.
- [ ] **Job state table**: `pipeline_runs (id, job_name, started_at, finished_at, status, error, items_processed)`. Each step writes its own row.
- [ ] **Failure handling**: `max_retries=3`, exponential backoff, dead-letter queue table `pipeline_failures` for human inspection
- [ ] **Daily health email** at 07:00 CET listing yesterday's runs, items processed, errors. Use existing Hetzner mailer.
- [ ] **`/admin/pipeline` page** in [templates/admin.html](templates/admin.html) showing job graph status (green/yellow/red per step), last run, queue depth
- [ ] **Smoke test job** running every hour: ingest one known-good test document end-to-end, fail the deploy if it doesn't make it through
- [ ] **OCR quality gate + public coverage dashboard** — *direct wedge vs both MAAT (hides coverage) and Archibot (silently rejected 95% of the archive: 75K indexed out of 1.5M available — the single biggest under-told fact from their case study)*. Add a `rejection_reason` column to `documents` (values: `ocr_low_confidence`, `unparseable_structure`, `duplicate_hash`, `out_of_scope`, `pending_reprocess`). Pipeline step `03b_ocr_quality_gate` runs a confidence check; anything below threshold is rejected with the reason logged, not silently dropped. Ship **`neodemos.nl/coverage`** as a public dashboard showing: total documents per gemeente, indexed, rejected (grouped by reason), pending reprocess, last-run timestamp. Backfill Rotterdam on ship. **Pitch:** *"We don't silently include garbage, we don't silently exclude it either. Here's exactly what's in our index and what isn't."*
- [ ] **Pre-2018 historical backfill** *(added 2026-04-11, assigned to v0.3.0 — see §9)*. Current state: full coverage 2018–2026, partial pre-2018. Rotterdam is on iBabs (`bestuurlijkeinformatie.nl`), which exposes a paginated meeting calendar going back to whenever the gemeente first adopted the system (estimated ~2010–2012 for Rotterdam; pre-iBabs records, if any, would require the Stadsarchief separately).

  **How we discover the missing documents (audit methodology):**
  1. **`scripts/audit_pre_2018_coverage.py`** — iterates the iBabs calendar endpoint backwards from 2018-01-01, paginating through all historical meetings by year. For each meeting: fetch the agenda, collect all document GUIDs, compare against `documents.source_url` (or a hash of the GUID). Emit `reports/pre_2018_missing.csv` with columns `(meeting_date, meeting_id, document_guid, document_url, document_type, reason_absent)`. `reason_absent` can be: `not_in_db`, `in_db_but_no_chunks`, `in_db_but_ocr_rejected`. This gives a clean count of the actual gap — not a guess.
  2. **Hard-stop check**: what is the earliest iBabs record date for Rotterdam? If iBabs returns nothing before 2010, the pre-2010 archive lives in a different system (Rotterdam Stadsarchief or earlier portal). Log the earliest available date and surface it on `/coverage` — don't promise data that isn't there.
  3. **Human sanity-check before any ingestion**: share `pre_2018_missing.csv` with Dennis. Questions to answer: how many docs? what types (raadsbesluiten, moties, notulen, bijlagen, persberichten)? Is there a hard iBabs cut-off year? Are the missing items OCR-able (are they scanned PDFs or born-digital)?
  4. **Run the approved set** through the hardened pipeline in small batches (≤500 docs/run) during off-peak hours, honoring the `pg_advisory_lock` writer rule. Pipeline steps: OCR quality gate → Docling → chunking → embedding → staging → KG enrichment → promotion.
  5. **Update `/coverage`** with a "historische diepte" badge showing earliest indexed document date.

  **Not v0.2.0 critical.** Slot into v0.3.0 after the 14-day nightly stability bake. Benefit: multi-decade trend queries that neither MAAT nor Archibot can answer for the same corpus.

### 7.2 Multi-portal connectors — search-only first

The user is explicit: **search-only first, AI tooling later**. This is the right call — it adds breadth without forcing us to validate the full RAG/KG stack on every new portal.

- [ ] **Refactor [pipeline/scraper.py](pipeline/scraper.py)** into `pipeline/sources/` with one module per portal:
  - `ibabs.py` — existing Rotterdam logic, generalized
  - `notubiz.py` — new
  - `go.py` — new (GO GemeenteOplossingen)
  - `ori_fallback.py` — queries `api.openraadsinformatie.nl/v1/elastic/ori_*` as a universal fallback for any gemeente without a native adapter
- [ ] **Common interface** (single Protocol class):
  ```python
  class SourceAdapter(Protocol):
      portal_name: str
      def list_meetings(self, gemeente: str, since: date) -> list[Meeting]: ...
      def fetch_documents(self, meeting_id: str) -> list[Document]: ...
      def fetch_webcast(self, meeting_id: str) -> WebcastRef | None: ...
  ```
- [ ] **`gemeente` column** on every staging/promoted table; backfill existing rows with `'rotterdam'`
- [ ] **Single Qdrant collection with mandatory `gemeente` payload filter** (NOT per-gemeente collections — simpler reranker pooling)
- [ ] **Search-only mode flag** per gemeente: if `mode='search_only'`, the ingest pipeline runs steps 01–05 and stops (no KG enrichment, no financial-lines extraction, no journey building). The MCP search tools work; the GraphRAG/financial/journey tools return `not available for this gemeente yet`.
- [ ] **Tenant config**: `data/tenants/<gemeente>/config.yml` declaring portal type, mode, branding
- [ ] **Portal map for the 5 target gemeenten** *(verified 2026-04-11 from live URLs + Digimonitor data)*:

  | Gemeente | Portal | URL confirmed | In ORI? | v0.2.1 strategy |
  |---|---|---|---|---|
  | Zoetermeer | **iBabs** | `zoetermeer.bestuurlijkeinformatie.nl` | Yes, gaps | Direct iBabs scraper — same code as Rotterdam |
  | Enschede | **iBabs** | `enschede.bestuurlijkeinformatie.nl` | Yes, gaps | Direct iBabs scraper |
  | **Apeldoorn** | **Parlaeus** (= Qualigraf) | `apeldoorn.parlaeus.nl` ✓ confirmed live | Yes | ORI fallback v0.2.1; native Parlaeus adapter v0.3.0 |
  | Maastricht | **Parlaeus** (= Qualigraf) | `maastricht.parlaeus.nl` ✓ confirmed | Yes | ORI fallback v0.2.1 |
  | Bodegraven-Reeuwijk | **Parlaeus** (= Qualigraf) | parlaeus portal ✓ confirmed by search | Yes | ORI fallback v0.2.1 |
  | **Waalwijk** *(pilot target)* | **iBabs** | `waalwijk.bestuurlijkeinformatie.nl` ✓ financial docs confirmed | Yes, gaps | iBabs scraper direct — financial docs are here |

  **Correction 2026-04-11:** Apeldoorn is **Parlaeus, not Notubiz**. Earlier inference from `apeldoorn.raadsinformatie.nl` (a legacy/ORI-viewer URL) was wrong; `apeldoorn.parlaeus.nl` is the current live RIS, confirmed from [ANP persportaal](https://persportaal.anp.nl/artikel/8e519f62-8c2c-4a9e-b5d0-9f051f629e37/ai-zoekmachine-maat-verbetert-zoekfunctionaliteit-in-apeldoorns-raadsinformatiesysteem) and search results. Note: **Parlaeus is made by Qualigraf** — the "Qualigraf" integration in MAAT's VNG case study IS the Parlaeus integration. 3 of 5 target municipalities are on Parlaeus. Notubiz has no confirmed anchor targets in this list.

  Key finding (wooverheid.nl, July 2024): **iBabs has the largest ORI gaps** — agenda items and attachments appear on the web but are missing from the ORI API. Notubiz gaps are smaller. Figures not published; directional only. **Do NOT use ORI fallback for Zoetermeer or Enschede** — use the existing iBabs scraper.

- [ ] **Day-1 strategy by portal type:**
  - **Zoetermeer + Enschede + Waalwijk (iBabs)**: extend `pipeline/sources/ibabs.py` with gemeente config entries. Same domain structure as Rotterdam, same scraper, zero new code.
  - **Apeldoorn + Maastricht + Bodegraven (Parlaeus)**: ORI fallback for v0.2.1 search-only; native `pipeline/sources/parlaeus.py` adapter in v0.3.0 (3 anchor targets justifies this over Notubiz).
  - **Notubiz**: no confirmed anchor target municipalities identified yet. Build opportunistically in v0.4 when a Notubiz gemeente commits.
- [ ] **Portal-agnostic persistent citation IDs** — *direct competitive wedge vs MAAT, whose citations are hard-coupled to `{gemeente}.bestuurlijkeinformatie.nl/Reports/Item/<guid>` URLs*. Every document and every chunk gets a stable `neodemos://doc/<sha256-prefix>` and `neodemos://chunk/<id>` URI that resolves to whichever portal the gemeente is on today. Store `portal_url_history[]` per document so migrations are traceable. All MCP tool outputs return the `neodemos://` URI as the primary citation and the current portal URL as a convenience link. **Pitch to gemeenten:** *"Our citations still work in 10 years even if you change portals. MAAT's don't."*
- [ ] **Inherits BAG-native location layer from WS1** *(added 2026-04-11)* — WS1 builds the location KG using BAG `openbare_ruimte` IDs as primary keys (not street names) with mandatory `gemeente` attributes and a generic `LOCATED_IN` edge `level` attribute. WS5b therefore gets Apeldoorn/Zoetermeer/Maastricht/Enschede/Bodegraven location coverage essentially for free: import the relevant BAG subset for each new gemeente, emit the hierarchy edges, done. No schema migration, no street-name disambiguation, no canonicalization fight. The five day-1 search-only gemeenten should each have their BAG location skeleton imported as part of the backfill. *(Prerequisite: BAG location layer must be implemented in WS1 first — coordinate with WS1 §3.2 timeline.)*

### 7.3 Stage gate (this is what "without overloading ourselves" looks like)

Multi-portal is a **separate deploy** from the GraphRAG/financial/journey workstreams. Sequence:

1. v0.2.0 ships GraphRAG + financial + journey + nightly orchestration on **Rotterdam only** (the system we trust)
2. v0.2.1 ships search-only for the 5 ORI-fallback gemeenten
3. v0.3.0 promotes one or two of those gemeenten to **full mode** after a 4-week stability bake
4. Native iBabs/Notubiz/GO adapters land in v0.3.x as we have customer demand

This avoids the trap of debugging a Notubiz scraper while also debugging a graph-walk service.

### 7.4 Webcast timestamp linking (Priority 7)

Already partially built — [pipeline/extractor.py](pipeline/extractor.py) produces speaker-attributed segments with timestamps. Two gaps to close:

- [ ] **Schema migration**: ensure every transcript chunk in Qdrant has `start_seconds`, `end_seconds`, `webcast_url` in its payload. Backfill existing rows ([scripts/create_staging_schema.py](scripts/create_staging_schema.py)).
- [ ] **HLS player template**: new `templates/meeting_player.html` with a `<video>` element pointed at the Royalcast HLS URL, accepting `?t=<seconds>` query param
- [ ] **Citation upgrade**: every transcript-derived chunk in `_format_chunks_v3` ([mcp_server_v3.py:189](mcp_server_v3.py#L189)) gets a Markdown link `[▶ 12:34](https://neodemos.nl/play/{meeting_id}?t=754)` as a citation suffix
- [ ] **In the journey view** (Workstream 3), every webcast event gets a thumbnail + click-to-play

### 7.5 Eval target

- [ ] **Zero manual interventions for 14 consecutive days** measured via the smoke test job
- [ ] **Search-only gemeenten**: equivalent search latency to Rotterdam (p50 < 800ms)
- [ ] **Webcast click-through** from any transcript citation lands within ±2 seconds of the spoken phrase (manual spot-check: 30 random citations)

---

## 8. Workstream 6 — GenAI Summarization (Priority 8) — evolving NeoDemos Analyse

**Goal:** Match-and-beat MAAT on document summarization, building on the existing NeoDemos Analyse function ([services/ai_service.py:47](services/ai_service.py#L47), `/api/analyse/agenda/{id}` in [main.py:795](main.py#L795)).

### 8.1 What we already have

- `analyze_agenda_item()` ([services/ai_service.py:47](services/ai_service.py#L47)) using Gemini Flash 3 — extracts key points, conflicts, decision points, controversial topics, critical questions; falls back to heuristics
- Party-lens evaluation ([services/policy_lens_evaluation_service.py](services/policy_lens_evaluation_service.py)) — evaluates an agenda item through a party's ideological filter
- Map-Reduce synthesizer ([services/synthesis.py](services/synthesis.py)) — parallel Gemini calls for summaries, Sonnet for reduction
- Sub-query decomposition ([services/decomposition.py](services/decomposition.py)) — Haiku splits multi-hop questions into 2–4 sub-queries
- MCP tool `analyseer_agendapunt` ([mcp_server_v3.py:721](mcp_server_v3.py#L721))

We are *ahead* of MAAT here on architecture — we just don't expose it well.

### 8.2 Best practices to copy from government summarization

- **UK i.AI ThemeFinder** ([github.com/i-dot-ai/themefinder](https://github.com/i-dot-ai/themefinder)): one-to-many Q&A theme extraction with public F1 = 0.79–0.82. Their pattern: structured per-question theme maps, not a single global summary. Apply to council debates: per-agenda-item, per-party theme map.
- **Multi-round legal RAG** ([ACM MM 2025](https://dl.acm.org/doi/10.1145/3731715.3733451)): iterative query refinement → 78.67% recall. We do single-round retrieval today; one extra round for "did I miss anything?" should boost completeness measurably.
- **EU Parliament Archibot**: Claude over 2.1M docs → 80% search-time reduction. The lesson: a *retrieval-first* answer with citations is more trusted than a stylish summary.
- **Stanford legal RAG hallucination study**: 17% hallucination floor even for top commercial systems. Defense = source-spans-only summarization (every sentence in the summary maps to a chunk; sentences without a mapping are stripped).

### 8.3 Build

- [ ] **`services/summarizer.py`** — new module replacing the ad-hoc summarization paths in `synthesis.py` and `ai_service.py`. Single entrypoint: `summarize(chunks, mode, max_tokens)` where `mode in {short, long, themes, structured, comparison}`.
- [ ] **Source-spans-only mode** — generated summary is constrained to sentences whose claims map to retrieved chunks. Enforced via a verifier pass: each sentence → reranker score against the chunk it cites; below threshold → strip.
- [ ] **Per-document summaries** — every promoted document gets a pre-computed `summary_short`, `summary_long`, `themes` cached in Postgres. UI/MCP serve from cache; only recompute on document update.
- [ ] **Per-agenda-item theme map** (ThemeFinder pattern) — for council debates with multiple speakers, return a structured theme→speakers→quotes map instead of a flat narrative
- [ ] **Multi-round retrieval option** — the `mode='structured'` summarizer runs an extra retrieval pass: "given this draft summary, what facts are still missing?" → re-query → revise. ACL legal-RAG pattern.
- [ ] **MCP tool: `vat_document_samen(document_id, mode='short'|'long'|'themes')`** — explicit, on-demand
- [ ] **MCP tool: `vat_dossier_samen(dossier_id, mode='structured')`** — uses the dossier-builder from a future workstream; for now just takes a list of `document_id`s
- [ ] **Verification badge in UI**: every summary shows `✅ verified` if all sentences pass the source-span check, `⚠️ partial` otherwise. This is the visible counterpart to MAAT's invisible "trust me" approach.

### 8.4 Eval target

- [ ] **Source-faithfulness**: 100% of sentences in `mode='short'` summaries map to a retrieved chunk (verified by the strip pass)
- [ ] **Theme-extraction F1 ≥ 0.75** against a hand-labeled set of 20 council debates (matching the i.AI Consult bar of 0.76)
- [ ] **Latency**: per-document `summary_short` < 2s p50 (cached), `mode='structured'` < 15s p95

---

## 9. Sequencing & Milestones

### v0.2.0 — "GraphRAG + Trustworthy Numbers" (alpha)
**Target:** 2 weeks from kickoff (revised from VERSIONING.md's "Flair NER only" v0.2)

| Workstream | Scope in v0.2.0 |
|---|---|
| WS1 GraphRAG | Foundations (Flair, Gemini enrichment, ~500K edges) + `traceer_motie` + `vergelijk_partijen` + 5th retrieval stream |
| WS2 Financial | `financial_lines` table + `vraag_begrotingsregel` + `vergelijk_begrotingsjaren` + verification token |
| WS3 Journey | `document_journeys` view + `traceer_document` MCP tool (no UI yet) |
| WS4 MCP | Tool registry, `get_neodemos_context`, audit log, scope-based auth, **Mistral Le Chat compatibility** *(added 2026-04-11)* — verified `/mcp` end-to-end against Le Chat custom connector, public no-auth `/public/mcp` endpoint for the §2.1 wedge, server-side temporal-extraction fallback, pre-rendered Markdown answer skeletons, Le Chat installer card, `mcp[cli]` pin, submit to Mistral connectors directory. See [WS4 §Mistral Le Chat compatibility](../handoffs/WS4_MCP_DISCIPLINE.md). |
| WS5 Pipeline | Job graph + smoke test + advisory lock + admin page (Rotterdam only) |
| WS5 Multi-portal | **Deferred to v0.2.1** |
| WS6 Summarization | `summarizer.py` + source-spans-only mode + cached per-document summaries |

**Eval gate (must pass before tag):**
- Completeness ≥ 3.5 (from 2.75)
- Faithfulness ≥ 4.5 (no regression)
- Numeric accuracy on financial benchmark = 100%
- 14 consecutive days clean nightly runs on Rotterdam
- Source-spans-only summaries pass strip-test on 50 random documents

### v0.2.1 — "Public Face" (alpha)
**Target:** 1 week after v0.2.0

- WS5 Multi-portal: ORI-fallback adapter live, search-only mode for 5 gemeenten (Apeldoorn, Zoetermeer, Maastricht, Enschede, Bodegraven)
- WS3 Journey: UI route `/journey/{id}` + `templates/journey.html`
- WS5 Webcast: HLS player + timestamp citations in all transcript chunks
- **`neodemos.nl/publiek`** — first-class anonymous landing page, zero-login search + summarize + traceer, modeled on [Archibot's anonymous dashboard](https://archidash.europarl.europa.eu/ep-archives-anonymous-dashboard). The name "publiek" is deliberate branding of the public-AI wedge (§2.1). Every demo link goes here; the gemeente-branded pages become secondary.
- **Public eval scoreboard at `neodemos.nl/eval`** *(promoted from v0.4)* — live precision / faithfulness / completeness, numeric-accuracy from the financial benchmark, per-question trace with source chunks. **Requirements added 2026-04-11:** must include **≥2 named baseline comparators** (proposed: Gemini Flash with web grounding + ChatGPT-4 web search; add MAAT free-tier via a nightly scraper if their ToS permit) and a **named human evaluator** who signed off on the question set (proposed: Rotterdam griffier or a Dutch-politics academic — credit on the methodology page). Archibot's published eval is "30 questions by an EU legislative-history expert vs ChatGPT 3.5" — a beatable bar if we publish a more rigorous methodology with named comparators.
- **Public MCP catalog at `neodemos.nl/mcp`** *(WS4 §6.5)* — browsable, with "Try in Claude Desktop" buttons
- **Public coverage dashboard at `neodemos.nl/coverage`** *(WS5 §7.1)* — OCR quality gate transparency
- **Governance one-pager at `neodemos.nl/governance`** — half-day write-up covering: models in use (Gemini Flash, Claude, Jina v3), data residency (Hetzner FSN EU), training-data policy (never trained on user queries or gemeente documents), refusal policy, eval methodology link, incident history. Removes 80% of procurement security-questionnaire friction. Archibot's entire procurement narrative leans on "Constitutional AI + permanent control of the solution"; ours should be more specific and more auditable.
- **Public-AI audit** — run the §2.1 constraint check against every shipped endpoint; any tool behind a login wall that doesn't need PII gets unlocked or gets a public tracking issue

### v0.3.0 — "Open MCP Surface + Citizen Voice" (beta)
**Target:** 4 weeks after v0.2.1

- WS4 MCP: TypeScript codegen + `@neodemos/mcp-tools` published to npm + Code Execution with MCP example workflows
- WS4 MCP: Anomaly-detection rate limiting
- WS5 Multi-portal: promote 2 of the 5 search-only gemeenten to full mode (KG + financial + journey)
- WS6 Summarization: ThemeFinder-style per-agenda-item theme maps + multi-round structured mode
- ChatGPT and Perplexity MCP registration (was old v0.4)
- First external testers onboarded (was old v0.4)
- **Voice-first citizen PWA** — *direct wedge vs MAAT, which is desktop-web-only and architecturally cannot reach mobile/voice without a rewrite*. A thin PWA wrapping the public MCP surface so a citizen can say *"Hé, wat besloot de gemeenteraad van Rotterdam vorig jaar over parkeren in IJsselmonde?"* and get a cited answer in 10 seconds. This is structurally impossible for MAAT's jQuery+Django+session-cookie stack. No native app needed — PWA + existing MCP tools + Claude/Gemini voice modes.
- **Pre-2018 historical backfill** *(WS5 §7.1, added 2026-04-11)* — run the audit script, sanity-check the diff, and push the missing set through the hardened pipeline in small off-peak batches. Enables multi-decade trend queries (*"hoe is Leefbaar Rotterdam's standpunt over warmtenetten veranderd sinds 2014?"*) that neither MAAT nor Archibot can answer for the same corpus. Update `neodemos.nl/coverage` with a "historische diepte" badge once complete.
- **Native Notubiz adapter** `pipeline/sources/notubiz.py` *(promoted from v0.4 on 2026-04-11)* — Apeldoorn uses Notubiz (`apeldoorn.raadsinformatie.nl`), is an anchor MAAT customer, and ORI-only gives incomplete coverage for it. Notubiz covers ~38% of all Dutch municipalities (Digimonitor), so this adapter has the highest multi-portal leverage of any portal after iBabs. Scope: full-depth ingestion — documents, transcripts, speakers, webcasts — equivalent to iBabs Rotterdam. Not search-only. Prerequisite: v0.2.1 ORI-fallback for Apeldoorn must be live and stable first so we have a diff target.

### v0.4.0 — "User Testing Ready" (beta)
**Target:** 4 weeks after v0.3.0

- _(Notubiz adapter promoted to v0.3.0 — see above)_
- Cross-gemeente comparison (`vergelijk_gemeenten`) — depends on WS1 + WS5
- Council-watcher agent (push alerts on new agenda items matching saved queries)
- Public eval scoreboard at `neodemos.nl/eval` showing live precision/faithfulness/numeric-accuracy

### v0.5.0 → v1.0.0
Unchanged from existing VERSIONING.md (multi-municipality foundation → agentic features → RC → GA).

---

## 10. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Embedding-segment corruption during nightly runs | Medium | High | Postgres advisory lock around all writers ([memory/project_embedding_process.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_embedding_process.md)); reads continue unblocked |
| Hallucination on financial numbers | Medium | Critical (loss of trust) | Structured `financial_lines` table + verification token + zero-paraphrase contract; never let LLMs synthesize euros |
| Graph walk explosion (combinatorial paths) | Medium | Medium | Hard cap at 2 hops in v0.2; path scoring penalizes length; benchmark with 100 queries before promoting |
| MCP tool count grows past LLM attention budget | Medium | Medium | FactSet uniqueness scoring + adopt Anthropic Code Execution pattern in v0.3 to bypass the limit entirely |
| Multi-portal scope creep blocks v0.2 ship | High | High | **Hard rule**: WS5 multi-portal defers to v0.2.1; v0.2.0 is Rotterdam-only |
| MAAT moves first on a comparable feature | Low | Medium | They have a sales motion, not an engineering motion. Open MCP surface + public eval scoreboard are moves they cannot match without disclosing internals |
| Numeric benchmark proves harder than 100% | Medium | High | Fall back to "structured tool always returns the row; LLM paraphrasing only allowed in narrative mode with source-span verification" — still beats MAAT |
| Customers expect MAAT-style polished UI | High | Low | Our wedge is the open MCP surface, not the UI. Ship a clean-but-minimal UI in v0.2 and win demos via Claude Desktop integration |
| Search-only gemeenten erode quality perception | Medium | Medium | Clear UI badge "Beperkte modus" + tool responses include `mode='search_only'` explicitly so the LLM tells the user what's not available |

---

## 11. Definition of Done for v0.2.0

- [ ] All v0.2.0 tasks above checked
- [ ] Eval gate passed
- [ ] CHANGELOG.md `[0.2.0]` section written
- [ ] [docs/VERSIONING.md](../VERSIONING.md) v0.2.0 entry updated to match what shipped
- [ ] `VERSION` bumped, git tag `v0.2.0` pushed
- [ ] Hetzner deploy via [scripts/deploy.sh](../../scripts/deploy.sh)
- [ ] Memory updated: project memory entry for v0.2 status, replacing the stale `project_plan_gi_execution.md` entry
- [ ] One end-to-end demo recorded: a multi-hop question that exercises GraphRAG + financial + journey + summary in a single Claude Desktop session

---

## 12. References

### MCP best practices
- [FactSet — Enterprise MCP Part 1](https://insight.factset.com/enterprise-mcp-model-context-protocol-part-one)
- [FactSet — Enterprise MCP Part 3 (Security & Governance)](https://medium.com/@factset/enterprise-mcp-part-3-security-and-governance-27ec39380bbe)
- [Figma — Tools and Prompts](https://developers.figma.com/docs/figma-mcp-server/tools-and-prompts/)
- [Figma — MCP Server Guide](https://github.com/figma/mcp-server-guide)
- [Anthropic — Code Execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)

### Government RAG / summarization
- [UK i.AI Consult](https://ai.gov.uk/projects/consult/)
- [UK i.AI ThemeFinder (GitHub)](https://github.com/i-dot-ai/themefinder)
- [UK ThemeFinder evaluation case study](https://ai.gov.uk/evaluations/case-study-using-themefinder-for-analysis-of-responses-to-dsit-s-digital-inclusion-action-plan-call-for-evidence/)
- [UK Government AI Playbook](https://www.gov.uk/government/publications/ai-playbook-for-the-uk-government/artificial-intelligence-playbook-for-the-uk-government-html)
- [European Parliament Archibot — Anthropic case study](https://claude.com/customers/european-parliament)
- [Riksdagsmonitor (32 MCP tools, Sweden)](https://github.com/Hack23/riksdagsmonitor)

### Legal/legislative RAG research
- [Stanford — Legal RAG Hallucinations (2025)](https://dho.stanford.edu/wp-content/uploads/Legal_RAG_Hallucinations.pdf)
- [Multi-Round RAG for Legal Documents (ACM MM 2025)](https://dl.acm.org/doi/10.1145/3731715.3733451)
- [LRAGE — Legal RAG Evaluation Tool (2025)](https://arxiv.org/html/2504.01840v1)
- [Towards Reliable Retrieval in RAG Systems (NLLP 2025)](https://aclanthology.org/2025.nllp-1.3.pdf)

### Competitor
- [AethiQs MAAT — raadsinformatie](https://aethiqs.nl/maat/raadsinformatie/)
- [VNG case study — MAAT](https://vng.nl/praktijkvoorbeelden/jouw-maatje-voor-het-vinden-van-raadsinformatie)
- [Binnenlands Bestuur — MAAT verovert gemeenten](https://www.binnenlandsbestuur.nl/digitaal/ai-systeem-voor-raadsinformatie-verovert-gemeenten)
