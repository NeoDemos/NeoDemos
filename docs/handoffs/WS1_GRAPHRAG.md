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
> Honor the project house rules in `docs/handoffs/README.md`. The most important one for you: **never write to Qdrant or PostgreSQL during the Flair/Gemini enrichment run** — coordinate via `pg_advisory_lock(42)` or by waiting for the `pipeline_runs` row to mark `finished`. Memory file `project_embedding_process.md` documents why.

## Files to read first
- [`docs/architecture/PLAN_G_CONTEXTUAL_RETRIEVAL.md`](../architecture/PLAN_G_CONTEXTUAL_RETRIEVAL.md)
- [`docs/architecture/PLAN_I_LIGHTRAG_ENTITY_EXTRACTION.md`](../architecture/PLAN_I_LIGHTRAG_ENTITY_EXTRACTION.md)
- [`docs/architecture/PLAN_GI_MERGED_STATUS.md`](../architecture/PLAN_GI_MERGED_STATUS.md)
- [`mcp_server_v3.py`](../../mcp_server_v3.py) — current 13 tools, especially `_format_chunks_v3` and the existing `zoek_moties` tool
- [`services/rag_service.py`](../../services/rag_service.py) — current 4-stream retrieval architecture (`retrieve_parallel_context`)
- Postgres schema: `kg_relationships`, `kg_entities`, `chunk_entities`, `politician_registry`, `documents`, `chunks`

## Build tasks

### Phase A — KG enrichment (foundations, ~3–5 days)

These were originally scoped as standalone v0.2.0 work; they are now folded in as prerequisites for phase B.

- [ ] **Run Flair `ner-dutch-large`** on all 1.6M chunks. Target: `key_entities` coverage 28% → ~65%. Use existing pipeline scripts under `scripts/` (look for layer 2 / NER runners).
- [ ] **Gemini Flash Lite enrichment pass** for: `answerable_questions`, `section_topic` refinement, semantic relationships (`HEEFT_BUDGET`, `BETREFT_WIJK`, `SPREEKT_OVER`). Budget: ~$90–130. Small-batch (100% retention) per [project_pipeline_hardening.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_pipeline_hardening.md).
- [ ] **Materialize new edges** into `kg_relationships`. Target: 57K → ≥500K edges.
- [ ] **Cross-document motie↔notulen vote linking** — populate edges connecting a `motie` document to the `notulen` chunks where it was discussed/voted. Schema: `(motie_id, notulen_chunk_id, relationship='DISCUSSED_IN' | 'VOTED_IN')`. **WS3 depends on this.**
- [ ] **Quality audit** — three layers:
  - SQL: row counts per edge type, NULL/orphan checks
  - Deterministic: 100 hand-curated entity→chunk pairs validated
  - LLM judge: 200 random edges scored 1–5 by Gemini Flash, mean ≥ 4.0

### Phase B — Graph retrieval service + MCP tools (~5–7 days)

- [ ] **`services/graph_retrieval.py`** — new file. Functions:
  - `extract_query_entities(query: str) -> list[Entity]` — Flair NER + gazetteer match + politician alias resolution
  - `walk(seed_entities: list[Entity], max_hops: int = 2, edge_types: list[str] | None = None) -> list[Path]` — recursive PostgreSQL CTE traversal of `kg_relationships`. **Hard cap at 2 hops in v0.2.**
  - `score_paths(paths: list[Path], query_intent: str) -> list[ScoredPath]` — penalize long paths, boost matches against query intent classifier
  - `hydrate_chunks(entity_ids: list[int], gemeente: str) -> list[Chunk]` — fetch chunks where entities appear via existing `chunk_entities` join
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

- [ ] `kg_relationships` row count ≥ 500K (was 57K)
- [ ] `key_entities` coverage on chunks ≥ 60%
- [ ] `services/graph_retrieval.py` exists and exposes the 4 functions above
- [ ] `services/rag_service.py:retrieve_parallel_context` calls a 5th `graph_walk` stream
- [ ] Every Qdrant point payload has `entity_ids` populated
- [ ] `traceer_motie` MCP tool returns a structured trace for at least 10 hand-validated moties (Feyenoord stadion, Boijmans, warmtenetten, …)
- [ ] `vergelijk_partijen` MCP tool returns coherent side-by-side for `topic="warmtenetten", partijen=["Leefbaar Rotterdam", "GroenLinks-PvdA", "VVD"]`
- [ ] Both new tools have AI-consumption descriptions registered with the WS4 tool registry
- [ ] No regression on existing 13 tools (run smoke tests against each)
- [ ] Cross-document motie↔notulen edges populated; visible in `traceer_motie` output

## Eval gate

| Metric | Target |
|---|---|
| Completeness on rag_evaluator benchmark | ≥ 3.5 (was 2.75) |
| Faithfulness (no regression) | ≥ 4.5 |
| KG quality LLM-judge (200 random edges) | mean ≥ 4.0 |
| `traceer_motie` precision (10 hand-validated moties) | 10/10 correct vote outcome |
| Graph walk latency p95 | < 1.5s |

Add 10 multi-hop questions to [`rag_evaluator/data/questions.json`](../../rag_evaluator/data/questions.json) before measuring completeness.

## Risks specific to this workstream

| Risk | Mitigation |
|---|---|
| Embedding-segment corruption during Flair/Gemini run | Advisory lock + reads-only window during enrichment; coordinate with WS5a if running |
| Graph-walk explosion (combinatorial paths) | Hard 2-hop cap + path scoring + benchmark with 100 queries before promoting to retrieve_parallel_context |
| Entity resolution false positives (e.g., common Dutch surnames matching multiple politicians) | Politician registry alias disambiguation; fall back to "no entity" rather than wrong entity |
| Gemini cost overrun | Small-batch flags from `project_pipeline_hardening.md`; estimate at $90–130 max |
| KG quality below 4.0 | Iterate prompt + chunk size + add SQL post-filters; do not promote phase A until audit passes |

## Future work (do NOT do in this workstream)
- Per-municipality KG isolation (out of v0.2 scope; multi-portal deferred to v0.2.1)
- 3+ hop graph walks (capped at 2 in v0.2)
- Active learning loop for entity disambiguation (v0.4+)
- Streaming graph results to the LLM (Anthropic Code Execution work — v0.3)

## Outcome
*To be filled in when shipped. Include: actual edge count, eval scores, surprises, follow-ups.*
