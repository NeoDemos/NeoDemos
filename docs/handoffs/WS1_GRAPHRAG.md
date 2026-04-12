# WS1 — GraphRAG Retrieval

> **Priority:** 1 (highest impact, MAAT cannot match)
> **Status:** `not started`
> **Owner:** `unassigned`
> **Target release:** v0.2.0
> **Master plan section:** [V0_2_BEAT_MAAT_PLAN.md §3](../architecture/V0_2_BEAT_MAAT_PLAN.md)

## TL;DR
Today we have 57K KG edges, a politician registry, a domain gazetteer, and 3.3M entity-mentions — and we never query any of it at retrieval time. This workstream lights up the graph: enriches it to ~500K edges via Flair NER + Gemini, builds `services/graph_retrieval.py`, adds graph traversal as the 5th retrieval stream, and ships two flagship MCP tools (`traceer_motie`, `vergelijk_partijen`) that no Dutch competitor can match.

## Dependencies
- **None for phase A** (Flair + Gemini enrichment)
- **Phase B (graph service + MCP tools) depends on phase A finishing**
- **WS3 (Journey) depends on this workstream's cross-document motie↔notulen linking**
- Memory to read first:
  - [project_plan_gi_execution.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_plan_gi_execution.md)
  - [project_motie_signatories.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_motie_signatories.md)
  - [project_embedding_process.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_embedding_process.md) — **critical: do not write to Qdrant during enrichment runs**

## Cold-start prompt

> You are picking up Workstream 1 (GraphRAG) of NeoDemos v0.2.0. The full plan is at `docs/architecture/V0_2_BEAT_MAAT_PLAN.md` §3 but you do not need to read it — this handoff is self-contained at `docs/handoffs/WS1_GRAPHRAG.md`.
>
> Read in order: (1) this handoff top-to-bottom, (2) `docs/architecture/PLAN_G_CONTEXTUAL_RETRIEVAL.md`, (3) `docs/architecture/PLAN_I_LIGHTRAG_ENTITY_EXTRACTION.md`, (4) `docs/architecture/PLAN_GI_MERGED_STATUS.md`, (5) `mcp_server_v3.py` (current 13 tools), (6) `services/rag_service.py` (current 4-stream retrieval).
>
> Your job is to ship phase A (Flair + Gemini enrichment, ~500K KG edges) followed by phase B (graph_retrieval service + 5th retrieval stream + `traceer_motie` + `vergelijk_partijen` MCP tools). The acceptance criteria and eval gate are listed below — all must pass before you mark this workstream `done`. Do not let scope creep into other workstreams; if you find adjacent issues, write them in the `Future work` section instead of fixing them.
>
> Honor the project house rules in `docs/handoffs/README.md`. The most important one for you: **never write to Qdrant or PostgreSQL during the Flair/Gemini enrichment run** — coordinate via `pg_advisory_lock(42)` or by waiting for the latest `staging.pipeline_runs` row to populate `completed_at`. Memory file `project_embedding_process.md` documents why.
>
> **Schema corrections (verified against live DB 2026-04-11):** an earlier draft of this handoff referenced several tables/columns that do not match reality. Use these names:
>
> - `staging.pipeline_runs` (not `public.pipeline_runs`); status column is `completed_at`, not `finished_at`; there is no `kind` column.
> - `kg_relationships.relation_type` (not `relationship_type`).
> - `kg_entities.type` (not `entity_type`).
> - Chunk table is `document_chunks` (not `chunks`).
> - Chunk↔entity mentions live in `kg_mentions (id, entity_id, chunk_id, raw_mention, created_at)` — there is **no** `chunk_entities` table.
> - `document_chunks.key_entities` is `text[]`, not `jsonb`. Use `array_length(key_entities, 1) > 0` for coverage checks, not `jsonb_array_length`.

## Files to read first
- [`docs/architecture/PLAN_G_CONTEXTUAL_RETRIEVAL.md`](../architecture/PLAN_G_CONTEXTUAL_RETRIEVAL.md)
- [`docs/architecture/PLAN_I_LIGHTRAG_ENTITY_EXTRACTION.md`](../architecture/PLAN_I_LIGHTRAG_ENTITY_EXTRACTION.md)
- [`docs/architecture/PLAN_GI_MERGED_STATUS.md`](../architecture/PLAN_GI_MERGED_STATUS.md)
- [`mcp_server_v3.py`](../../mcp_server_v3.py) — current 13 tools, especially `_format_chunks_v3` and the existing `zoek_moties` tool
- [`services/rag_service.py`](../../services/rag_service.py) — current 4-stream retrieval architecture (`retrieve_parallel_context`)
- Postgres schema: `kg_relationships` (column: `relation_type`), `kg_entities` (column: `type`), `kg_mentions` (chunk↔entity link table), `politician_registry`, `documents`, `document_chunks`

## Build tasks

### Phase A — KG enrichment (foundations, ~3–5 days)

These were originally scoped as standalone v0.2.0 work; they are now folded in as prerequisites for phase B.

- [ ] **[Quick win — do this first] Re-run Layer 1 `key_entities` enrichment over chunk text, not just parent-document title.** Current state ([PLAN_GI_MERGED_STATUS.md:14](../architecture/PLAN_GI_MERGED_STATUS.md#L14)): 449K chunks (28%) tagged via gazetteer-match against doc title only. A chunk like *"bewoners van de Heemraadssingel klagen over parkeerdruk"* in a document titled "Voortgangsrapportage parkeren 2024" gets zero `key_entities` tags → invisible to the Qdrant payload filter. Fix: second pass of [`scripts/enrich_and_extract.py`](../../scripts/enrich_and_extract.py) that scans chunk-body text against the existing 2.217-entry [`data/knowledge_graph/domain_gazetteer.json`](../../data/knowledge_graph/domain_gazetteer.json). No NER, no LLM, no new dependencies — just an extra string-match pass. Closes the most common location-query failure mode (see [FEEDBACK_LOG.md 2026-04-11](../../brain/FEEDBACK_LOG.md) Heemraadssingel entry) before Flair even runs.
- [ ] **Run Flair `ner-dutch-large`** on all 1.6M chunks. Target: `key_entities` coverage 28% → ~65% (combined with the quick-win pass above, expect closer to ~75%). Use existing pipeline scripts under `scripts/` (look for layer 2 / NER runners). Flair Dutch LOC tagging picks up street-level entities the static gazetteer misses (Heemraadssingel, Mathenesserlaan, etc.), populating `kg_mentions` at chunk granularity.
- [ ] **BAG-based location hierarchy as KG edges.** Import the Dutch national address registry ([Basisregistratie Adressen en Gebouwen](https://www.pdok.nl/introductie/-/article/basisregistratie-adressen-en-gebouwen-ba-1)) joined to the [CBS Wijk- en Buurtkaart](https://www.cbs.nl/nl-nl/dossier/nederland-regionaal/geografische-data/wijk-en-buurtkaart-2024) and emit `LOCATED_IN` edges in `kg_relationships`. For Rotterdam: ~5.000 streets + ~80 buurten + 14 gebieden + 1 gemeente. After import, a query for "Heemraadssingel" can walk one hop to "Middelland" → "Delfshaven" → "Rotterdam" via existing graph traversal. Replaces the alternative of a separate JSON lookup table — same data, better integrated, composable with the rest of the KG.
  - **Multi-tenant design constraints (locked here, paid back in WS5b):**
    - Use the **BAG `openbare_ruimte` 16-digit identifier** as the canonical primary key for Location nodes — NOT the street name. Street names are not unique across municipalities ("Hoofdstraat" exists in 100+ towns; "Marnixstraat" in 10+ large cities). Setting this now prevents a painful canonicalization migration when v0.2.1 multi-portal expansion adds Apeldoorn/Zoetermeer/etc.
    - Every `Location` entity in `kg_entities` gets a mandatory `gemeente` attribute, even though v0.2.0 only has Rotterdam data.
    - The `LOCATED_IN` edge type carries a `level` attribute (`buurt` | `wijk` | `gebied` | `stadsdeel` | `gemeente`) so different municipalities with different sub-municipal structures (Amsterdam stadsdelen, Utrecht wijken-only, Rotterdam gebieden) all fit the same schema without per-tenant changes.
  - Output: new ~5.100 nodes + ~5.100 edges in Rotterdam-only mode; rolls forward to ~9,5M addresses + matching edges if/when full Netherlands coverage is enabled in a later release.
- [ ] **Gemini Flash Lite enrichment pass** for: `answerable_questions`, `section_topic` refinement, semantic relationships (`HEEFT_BUDGET`, `BETREFT_WIJK`, `SPREEKT_OVER`). Budget: ~$90–130. Small-batch (100% retention) per [project_pipeline_hardening.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_pipeline_hardening.md). Now that BAG-derived `LOCATED_IN` edges exist, `BETREFT_WIJK` should resolve targets to BAG-canonical Location IDs rather than free-text wijk names.
- [ ] **Materialize new edges** into `kg_relationships`. Target: 57K → ≥500K edges (Flair semantic relationships dominate; BAG hierarchy adds the constant 5K location skeleton).
- [ ] **Cross-document motie↔notulen vote linking** — populate edges connecting a `motie` document to the `notulen` chunks where it was discussed/voted. Write into `kg_relationships` using the canonical shape `(source_entity_id, target_entity_id, relation_type='DISCUSSED_IN' | 'VOTED_IN', document_id, chunk_id, confidence, metadata)`. **WS3 depends on this.**
- [ ] **Quality audit** — two layers for Phase A (the full eval gate including MCP chat replay runs after Phase B):
  - SQL: row counts per edge type, NULL/orphan checks, coverage % on `key_entities`
  - Deterministic: 100 hand-curated entity→chunk pairs validated
  - *(Optional diagnostic, not a gate)* LLM judge: 200 random edges scored 1–5 by Gemini Flash. If mean < 3.5, iterate the Gemini prompt before proceeding to Phase B. This catches obviously bad edges but cannot catch politically misleading interpretation — the MCP chat replay in the Eval gate (Layer 2) tests that.

### Phase B — Graph retrieval service + MCP tools (~5–7 days)

- [ ] **`services/graph_retrieval.py`** — new file. Functions:
  - `extract_query_entities(query: str) -> list[Entity]` — Flair NER + gazetteer match + politician alias resolution
  - `walk(seed_entities: list[Entity], max_hops: int = 2, edge_types: list[str] | None = None) -> list[Path]` — recursive PostgreSQL CTE traversal of `kg_relationships` (column name `relation_type`). **Hard cap at 2 hops in v0.2.**
  - `score_paths(paths: list[Path], query_intent: str) -> list[ScoredPath]` — penalize long paths, boost matches against query intent classifier
  - `hydrate_chunks(entity_ids: list[int], gemeente: str) -> list[Chunk]` — fetch chunks where entities appear via the existing `kg_mentions` join (NOT a nonexistent `chunk_entities` table)
- [ ] **Bug fix first — drop the legacy `%%notule%%` filter in `_retrieve_by_keywords()`** at [`services/rag_service.py:438`](../../services/rag_service.py#L438) *(added 2026-04-11, triaged from TODOS)*. The current BM25 fallback used by `scan_breed` / `zoek_raadshistorie` hard-filters to `WHERE d.name ILIKE '%%notule%%'`, silently excluding every motie / amendement / initiatiefvoorstel / raadsvoorstel from document-level BM25 — a whole class of retrieval failures nobody sees. The vector search has no such filter and doesn't need one. Action: (1) remove the filter entirely; (2) log when the fallback fires so we can tell if this path is still load-bearing; (3) if doc-type scoping is ever needed, make it an explicit parameter, not a hidden default. Must land before the 5th stream is added (below), so the regression baseline is clean.
- [ ] **Add 5th retrieval stream `graph_walk`** to [`services/rag_service.py:70`](../../services/rag_service.py#L70) `retrieve_parallel_context`. Reuse the existing Jina v3 reranker to merge with the other 4 streams.
- [ ] **Entity-based Qdrant pre-filtering** — add `entity_ids: int[]` to all Qdrant payloads at promote-time (or via a backfill script). Then a graph walk can prune the dense search to *only* chunks mentioning the resolved entities → big speedup.
- [ ] **MCP tool `traceer_motie(motie_id: str) -> dict`** in [`mcp_server_v3.py`](../../mcp_server_v3.py)
  - Walks: `motie → DIENT_IN → indieners → LID_VAN → partijen → STEMT_VOOR/TEGEN → uitkomst → BETREFT (wijk/programma) → linked notulen fragments`
  - Returns structured: `{motie, indieners, vote: {voor, tegen, uitkomst}, related_documents, journey_anchor, citation_chain}`
  - **This is the flagship demo tool for v0.2.0.** Test it with the Feyenoord stadion files first.
- [ ] **MCP tool `vergelijk_partijen(topic: str, partijen: list[str], date_from: date, date_to: date) -> dict`**
  - For each party: `LID_VAN ∩ SPREEKT_OVER(topic)` → fetch chunks → rerank → top 5
  - Returns side-by-side: `{party: [chunks_with_citations]}`
- [ ] **Tool descriptions written for AI consumption** (not humans). Each must say: when to pick this tool, when *not* to pick it. Coordinate with WS4 — they own the description-writing convention.

## Acceptance criteria

- [ ] `kg_relationships` row count ≥ 500K (was 57K), including ~5K BAG-derived `LOCATED_IN` edges
- [ ] `key_entities` coverage on chunks ≥ 60% (target ~75% with the quick-win chunk-text gazetteer pass + Flair combined)
- [ ] BAG hierarchy import is reproducible: `scripts/import_bag_locations.py --gemeente rotterdam` rebuilds the location skeleton idempotently from PDOK + CBS sources
- [ ] All `Location` entities in `kg_entities` use BAG `openbare_ruimte` IDs as primary key and have `gemeente` populated
- [ ] Test query: `"Heemraadssingel parkeren"` returns chunks via the new chunk-text-tagged path (was 0 hits in [FEEDBACK_LOG.md 2026-04-11](../../brain/FEEDBACK_LOG.md))
- [ ] `services/graph_retrieval.py` exists and exposes the 4 functions above
- [ ] `services/rag_service.py:retrieve_parallel_context` calls a 5th `graph_walk` stream
- [ ] Every Qdrant point payload has `entity_ids` populated
- [ ] `traceer_motie` MCP tool returns a structured trace for at least 10 hand-validated moties (Feyenoord stadion, Boijmans, warmtenetten, …)
- [ ] `vergelijk_partijen` MCP tool returns coherent side-by-side for `topic="warmtenetten", partijen=["Leefbaar Rotterdam", "GroenLinks-PvdA", "VVD"]`
- [ ] Both new tools have AI-consumption descriptions registered with the WS4 tool registry
- [ ] No regression on existing 13 tools (run smoke tests against each)
- [ ] Cross-document motie↔notulen edges populated; visible in `traceer_motie` output

## Eval gate

### Layer 1 — Automated metrics (necessary but not sufficient)

| Metric | Target |
|---|---|
| Completeness on rag_evaluator benchmark (30 questions) | ≥ 3.5 (was 2.75) |
| Faithfulness (no regression) | ≥ 4.5 |
| `traceer_motie` precision (10 hand-validated moties) | 10/10 correct vote outcome |
| Graph walk latency p95 | < 1.5s |

Add 10 multi-hop questions to [`eval/data/questions.json`](../../eval/data/questions.json) before measuring completeness. **At least 1 of the 10 must test coalition-status-at-time during historical vote interpretation** *(added 2026-04-11)* — e.g. *"In 2018 stemde de raad over de sloop in de Tweebosbuurt. Welke partijen stemden voor, en waren zij op dat moment coalitie- of oppositiepartij?"* Gold answer requires the system to (a) find the stemming, (b) recognize the date, (c) resolve coalition-at-time via the WS4 `coalition_history` primer field — not guess party roles from training data. Rationale: 2026-04-11 woningbouw session produced exactly this class of framing error (GL/PvdA labelled as opposition on a 2018 vote while they were coalitiepartij). See [FEEDBACK_LOG.md 2026-04-11 "Full session audit"](../../brain/FEEDBACK_LOG.md). The failure mode is data-shaped, not instruction-shaped — this benchmark question proves the WS4 primer extension actually works end-to-end.

### Layer 2 — MCP chat replay (the real quality gate)

*(Added 2026-04-12)* The v2 formal eval (completeness 2.75, faithfulness 4.8) measured whether chunks came back and whether text was supported — but it **missed** every failure that actually mattered in production: coalition-at-time framing errors, slot efficiency, scope confusion, missing follow-up depth. Real MCP chat sessions in [FEEDBACK_LOG.md](../../brain/FEEDBACK_LOG.md) surfaced those failures far more effectively than any automated metric.

**Principle:** the true quality gate is whether the specific MCP sessions that previously exposed failures now produce qualitatively better results. Abstract LLM-judge scores on random samples do not catch politically misleading output.

Replay these 6 sessions through the live MCP tools **after Phase 1 enrichment**. Each must pass its specific condition. Failure on any red item blocks the WS1 `done` status.

| # | Session | MCP tool sequence | Pass condition | Sev |
|---|---|---|---|---|
| R1 | **Tweebosbuurt 2018 stemming** | `traceer_motie` on the sloop motie | Returns correct vote parties AND identifies GL/PvdA as **coalitiepartij** at time of vote (not opposition). Must use `coalition_history` from `get_neodemos_context`, not guess from training data. | RED |
| R2 | **Warmtebedrijf motie trace** | `traceer_motie` on ≥3 Warmtebedrijf moties (2019-2022) | Returns complete indieners + vote counts + at least 1 DISCUSSED_IN notulen chunk per motie. `trace_available: true`. | RED |
| R3 | **Heemraadssingel parkeren** | `zoek_raadshistorie("Heemraadssingel parkeren")` | Returns ≥1 chunk (was 0 hits pre-enrichment — the chunk-text gazetteer quick-win closes this). | RED |
| R4 | **Partijvergelijking warmtenetten** | `vergelijk_partijen(onderwerp="warmtenetten", partijen=["Leefbaar Rotterdam","GroenLinks-PvdA","VVD"])` | Returns **differentiated** per-party fragments — not the same generic warmtenetten chunks for all three. At least 3 of the 5 fragments per party must mention that party by name. | RED |
| R5 | **Woningbouw 10-jaar research** | Full multi-tool session: `scan_breed` → `zoek_raadshistorie` → `zoek_moties` → `traceer_motie` → `lees_fragment` | Qualitative: graph_walk stream contributes at least 2 chunks that would NOT have appeared via the 4-stream retrieval alone (visible via `stream_type='graph'` tag). | YELLOW |
| R6 | **Haven verduurzaming dossier** | `scan_breed("verduurzaming haven Rotterdam")` → `zoek_uitspraken` | Slot efficiency: ≤2 duplicate `document_id` values in first 8 results (was 4/8 pre-fix). Score floor: no result with `similarity_score < 0.15`. | YELLOW |

**How to run:** use Claude Desktop (or any MCP host) connected to the production NeoDemos MCP server after Phase 1 enrichment + `GRAPH_WALK_ENABLED=1` deploy. Each session is a natural-language conversation that exercises the tool sequence shown. Score pass/fail manually against the condition column. Record results in the **Outcome** section at the bottom of this file.

**Why this replaces the abstract LLM-judge:** The original handoff prescribed "200 random edges scored 1-5 by Gemini Flash, mean ≥ 4.0." That gate catches obviously bad edges but cannot catch the Tweebosbuurt-class failure (politically misleading interpretation of structurally correct data). The 6 replay sessions test end-to-end correctness including the host LLM's interpretation of retrieved context — which is where the actual user trust lives. The edge-quality LLM-judge can still be run as a diagnostic during Phase A enrichment iteration (if the mean drops below 3.5, iterate the Gemini prompt before proceeding to Phase B), but it is **not** a gate for marking WS1 `done`.

## Risks specific to this workstream

| Risk | Mitigation |
|---|---|
| Embedding-segment corruption during Flair/Gemini run | Advisory lock + reads-only window during enrichment; coordinate with WS5a if running |
| Graph-walk explosion (combinatorial paths) | Hard 2-hop cap + path scoring + benchmark with 100 queries before promoting to retrieve_parallel_context |
| Entity resolution false positives (e.g., common Dutch surnames matching multiple politicians) | Politician registry alias disambiguation; fall back to "no entity" rather than wrong entity |
| Gemini cost overrun | Small-batch flags from `project_pipeline_hardening.md`; estimate at $90–130 max |
| KG quality below 3.5 on edge LLM-judge | Iterate Gemini prompt + chunk size + add SQL post-filters; do not promote to Phase B until diagnostic passes. The *real* gate is the MCP chat replay (Eval gate Layer 2), not this score. |
| MCP chat replay failures (Tweebosbuurt-class) | Root-cause whether the failure is in enrichment (Phase A), graph traversal (Phase B), or host-LLM interpretation (WS4 primer). Fix in the responsible workstream before marking WS1 done. |

## Future work (do NOT do in this workstream)
- Per-municipality KG isolation (out of v0.2 scope; multi-portal deferred to v0.2.1)
- 3+ hop graph walks (capped at 2 in v0.2)
- Active learning loop for entity disambiguation (v0.4+)
- Streaming graph results to the LLM (Anthropic Code Execution work — v0.3)
- Full Netherlands BAG coverage import (only Rotterdam-relevant subset in v0.2.0; the full ~9.5M-address national set lands when WS5b promotes new gemeenten to full mode in v0.3.0+)
- Geographic/spatial queries via PostGIS (BAG provides coordinates, but spatial radius search is out of scope for v0.2)

## Outcome
*To be filled in when shipped. Include: actual edge count, eval scores, surprises, follow-ups.*
