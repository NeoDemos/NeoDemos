# WS1 — GraphRAG Retrieval

> **Priority:** 1 (highest impact, MAAT cannot match)
> **Status (2026-04-14):** Phase 0 `done` · Phase A bis (VN provenance) `done` · Phase 1 prep `done` (scripts hardened, commits `d6e1d58` + `44c87d5`) · Phase 1 execution `blocked` — waiting on WS11 (corpus gaps). WS7 (OCR) done; WS12 deferred (VN 2025+2026 only); WS10 (table-rich) no longer a blocker.
> **Owner:** `unassigned`
> **Target release:** v0.2.0
> **Master plan section:** [V0_2_BEAT_MAAT_PLAN.md §3](../architecture/V0_2_BEAT_MAAT_PLAN.md)
> **Phase naming:** "Phase A" (build tasks) == "Phase 1" (eval gate text). Same thing — Phase A is the build label; Phase 1 is the execution-stage label used in the eval gate.
> **Next-agent quick-start:** jump to [§Phase 1 Execution Runbook](#phase-1-execution-runbook-agent-pickup-point). All code is ready; the runbook is a 10-step command sequence.

## TL;DR
Today we have 57K KG edges, a politician registry, a domain gazetteer, and 3.3M entity-mentions — and we never query any of it at retrieval time. This workstream lights up the graph: enriches it to ~500K edges via Flair NER + Gemini, exposes it via [`services/graph_retrieval.py`](../../services/graph_retrieval.py) (built in Phase 0, 2026-04-12), wires graph traversal as the 5th retrieval stream, and ships two flagship MCP tools (`traceer_motie`, `vergelijk_partijen`) that no Dutch competitor can match. Phase A bis (added 2026-04-14) tags every edge with provenance metadata so virtual notulen (WS12) can be included in the KG without compromising production-grade output quality.

## Dependencies
- **WS7 (OCR recovery)** ✅ done (2026-04-14) — source text is clean
- **WS11 (corpus completeness)** must complete first — no point enriching an incomplete corpus
- **WS12 (virtual notulen)** ~~must complete first~~ — **deferred to v0.3/v0.4** (2026-04-14). VN provenance will cover 2025+2026 only. The KG enrichment can proceed without waiting for the full 2018–2024 backfill.
- **Phase B (graph service + MCP tools) depends on phase A finishing**
- **WS3 (Journey) depends on this workstream's cross-document motie↔notulen linking**
- Memory to read first:
  - [project_plan_gi_execution.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_plan_gi_execution.md)
  - [project_motie_signatories.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_motie_signatories.md)
  - [project_embedding_process.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_embedding_process.md) — **critical: do not write to Qdrant during enrichment runs**

## Cold-start prompt

> You are picking up Workstream 1 (GraphRAG) of NeoDemos v0.2.0. The full plan is at `docs/architecture/V0_2_BEAT_MAAT_PLAN.md` §3 but you do not need to read it — this handoff is self-contained at `docs/handoffs/WS1_GRAPHRAG.md`.
>
> **Phase 0 is already done (2026-04-12).** All scaffolding code exists: `services/graph_retrieval.py` (665 lines, 24/24 tests pass), 6 enrichment scripts under `scripts/` (gazetteer, Flair NER, BAG import, Gemini, motie linker, Qdrant backfill), and the `traceer_motie` + `vergelijk_partijen` MCP tool implementations. The 5th `graph_walk` stream is wired into `services/rag_service.py` (gated behind `GRAPH_WALK_ENABLED` env var, default false). Your job is to execute Phase A (run the enrichment scripts) and Phase B (test the MCP tools end-to-end), then promote and run the eval gate.
>
> Read in order: (1) this handoff top-to-bottom, (2) `docs/handoffs/WS12_VIRTUAL_NOTULEN_BACKFILL.md` (your hard dep — needs `staging.meetings.quality_score` populated), (3) `services/graph_retrieval.py` (the file you'll be running), (4) `eval/baselines/ws1_pre_enrichment_baseline.md` (the 1.8/5 composite "before" you must beat), (5) `mcp_server_v3.py` (15+ tools incl. your two new ones at the end), (6) `services/rag_service.py` (5-stream retrieval, 5th gated on env var).
>
> Acceptance criteria and eval gate are listed below — all must pass before you mark this workstream `done`. Do not let scope creep into other workstreams; if you find adjacent issues, write them in the `Future work` section instead of fixing them.
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

**Hard dependencies (read these to understand WS1 in current state):**
- [`docs/handoffs/WS12_VIRTUAL_NOTULEN_BACKFILL.md`](WS12_VIRTUAL_NOTULEN_BACKFILL.md) — VN data source feeding WS1 enrichment; provides `staging.meetings.quality_score` that the provenance layer multiplies into edge confidence
- [`eval/baselines/ws1_pre_enrichment_baseline.md`](../../eval/baselines/ws1_pre_enrichment_baseline.md) — the **1.8/5 composite baseline** measured 2026-04-12; Phase 1 must beat this on the 6 MCP chat replay sessions

**Phase 0 artifacts (already shipped 2026-04-12):**
- [`services/graph_retrieval.py`](../../services/graph_retrieval.py) — 665 lines; 4-function public API (extract_query_entities, walk, score_paths, hydrate_chunks) + retrieve_via_graph orchestrator
- [`tests/test_graph_retrieval.py`](../../tests/test_graph_retrieval.py) — 24 unit tests, all passing
- [`scripts/enrich_chunks_gazetteer.py`](../../scripts/enrich_chunks_gazetteer.py) — chunk-text gazetteer quick-win (Heemraadssingel fix)
- [`scripts/run_flair_ner.py`](../../scripts/run_flair_ner.py) — Flair Dutch NER over 1.7M chunks
- [`scripts/import_bag_locations.py`](../../scripts/import_bag_locations.py) — PDOK BAG + CBS Wijk- en Buurtkaart import
- [`scripts/gemini_semantic_enrichment.py`](../../scripts/gemini_semantic_enrichment.py) — Gemini Flash Lite with `--cost-cap` enforcement
- [`scripts/link_motie_to_notulen.py`](../../scripts/link_motie_to_notulen.py) — DISCUSSED_IN / VOTED_IN cross-doc edges
- [`scripts/backfill_qdrant_entity_ids.py`](../../scripts/backfill_qdrant_entity_ids.py) — Qdrant payload entity_ids backfill
- [`eval/scripts/ws1_quality_audit.sql`](../../eval/scripts/ws1_quality_audit.sql) — 12 acceptance-criteria checks ready to run

**Architecture context (background reading):**
- [`docs/architecture/PLAN_G_CONTEXTUAL_RETRIEVAL.md`](../architecture/PLAN_G_CONTEXTUAL_RETRIEVAL.md)
- [`docs/architecture/PLAN_I_LIGHTRAG_ENTITY_EXTRACTION.md`](../architecture/PLAN_I_LIGHTRAG_ENTITY_EXTRACTION.md)
- [`docs/architecture/PLAN_GI_MERGED_STATUS.md`](../architecture/PLAN_GI_MERGED_STATUS.md)
- [`mcp_server_v3.py`](../../mcp_server_v3.py) — 15+ tools incl. `traceer_motie` + `vergelijk_partijen` at EOF; reference shapes: `_format_chunks_v3`, `zoek_moties`
- [`services/rag_service.py`](../../services/rag_service.py) — 5-stream retrieval (`retrieve_parallel_context`); 5th `graph_walk` stream gated on `GRAPH_WALK_ENABLED`

**Postgres schema (verified live 2026-04-11):** `kg_relationships` (column: `relation_type`), `kg_entities` (column: `type`), `kg_mentions` (chunk↔entity link table), `politician_registry`, `documents`, `document_chunks`. `staging.meetings.quality_score` (WS12) feeds the VN provenance layer.

## Build tasks

### Phase A — KG enrichment (foundations, ~3–5 days)

These were originally scoped as standalone v0.2.0 work; they are now folded in as prerequisites for phase B.

- [ ] **[Quick win — do this first] Run [`scripts/enrich_chunks_gazetteer.py`](../../scripts/enrich_chunks_gazetteer.py)** (Phase 0 ✅ — script exists, dry-runnable). Scans chunk-body text against [`data/knowledge_graph/domain_gazetteer.json`](../../data/knowledge_graph/domain_gazetteer.json) (~2,217 entries across 6 lists). Current state: 25.2% `key_entities` coverage on 1,698,930 chunks (per pre-flight baseline 2026-04-12). A chunk like *"bewoners van de Heemraadssingel klagen over parkeerdruk"* in a document titled "Voortgangsrapportage parkeren 2024" currently gets zero `key_entities` tags → invisible to the Qdrant payload filter. This pass closes that gap. Smoke first: `python scripts/enrich_chunks_gazetteer.py --dry-run --limit 1000`. Closes the Heemraadssingel 0-hit failure (see [FEEDBACK_LOG.md 2026-04-11](../../.coordination/FEEDBACK_LOG.md), [eval/baselines/ws1_pre_enrichment_baseline.md R3](../../eval/baselines/ws1_pre_enrichment_baseline.md)).
- [ ] **Run [`scripts/run_flair_ner.py`](../../scripts/run_flair_ner.py)** (Phase 0 ✅ — script exists, GPU auto-detected, `--dry-run` + `--limit` + `--resume`). Flair `ner-dutch-large` over all 1,698,930 chunks. Target: `key_entities` coverage 25.2% → ~65% (combined with the quick-win above, expect ~75%). Flair Dutch LOC tagging picks up street-level entities the static gazetteer misses (Heemraadssingel, Mathenesserlaan, etc.), populating `kg_mentions` at chunk granularity. Estimated runtime: 6-12 hours on Apple Silicon MPS.
- [ ] **Run [`scripts/import_bag_locations.py --gemeente rotterdam`](../../scripts/import_bag_locations.py)** (Phase 0 ✅ — script exists, downloads cached, idempotent). Imports the Dutch national address registry ([Basisregistratie Adressen en Gebouwen](https://www.pdok.nl/introductie/-/article/basisregistratie-adressen-en-gebouwen-ba-1)) joined to the [CBS Wijk- en Buurtkaart](https://www.cbs.nl/nl-nl/dossier/nederland-regionaal/geografische-data/wijk-en-buurtkaart-2024) and emits `LOCATED_IN` edges in `kg_relationships`. For Rotterdam: ~5.000 streets + ~80 buurten + 14 gebieden + 1 gemeente. After import, a query for "Heemraadssingel" can walk one hop to "Middelland" → "Delfshaven" → "Rotterdam" via existing graph traversal. **Note:** the script's CBS download URL may need `--cbs-url` override on first run (CBS rotates filenames annually).
  - **Multi-tenant design constraints (locked here, paid back in WS5b):**
    - Use the **BAG `openbare_ruimte` 16-digit identifier** as the canonical primary key for Location nodes — NOT the street name. Street names are not unique across municipalities ("Hoofdstraat" exists in 100+ towns; "Marnixstraat" in 10+ large cities). Setting this now prevents a painful canonicalization migration when v0.2.1 multi-portal expansion adds Apeldoorn/Zoetermeer/etc.
    - Every `Location` entity in `kg_entities` gets a mandatory `gemeente` attribute, even though v0.2.0 only has Rotterdam data.
    - The `LOCATED_IN` edge type carries a `level` attribute (`buurt` | `wijk` | `gebied` | `stadsdeel` | `gemeente`) so different municipalities with different sub-municipal structures (Amsterdam stadsdelen, Utrecht wijken-only, Rotterdam gebieden) all fit the same schema without per-tenant changes.
  - Output: new ~5.100 nodes + ~5.100 edges in Rotterdam-only mode; rolls forward to ~9,5M addresses + matching edges if/when full Netherlands coverage is enabled in a later release.
- [ ] **Run [`scripts/gemini_semantic_enrichment.py`](../../scripts/gemini_semantic_enrichment.py)** — hardened 2026-04-14 with `--scope` selection (no truncation), exponential backoff on 429/5xx `[2,4,8,16,32]s`, malformed-JSON retry-once, edge-rejection-rate >20% warning, model pinned to `gemini-2.5-flash-lite-001`, max_output_tokens cap, pre-flight checks (BAG fully imported — hierarchy ≥100 AND total ≥4500 expecting ~5,135, no active kg_* writers, staging.meetings quality_score coverage). Advisory lock 42.

  **Pass for:** `answerable_questions`, `section_topic` refinement, semantic relationships (`HEEFT_BUDGET`, `BETREFT_WIJK`, `SPREEKT_OVER`). VN provenance metadata + `confidence = gemini_confidence × source_quality` per Phase A bis.

  **Cost + scope — verified 2026-04-14 against live corpus (1,737,932 chunks, real prices $0.10/1M in + $0.40/1M out):**

  | `--scope` | Chunks | Est. cost | Time @ Tier 3 (4000 RPM) |
  |---|---|---|---|
  | `p1` | ~600K (moties, amendementen, initiatief, afdoening, raadsvoorstel, financial, speaker-attributed notulen) | **~$85** | ~40 min |
  | `p1_p2` (default) | ~885K (P1 + 2020+ briefs + `key_entities`-tagged "other") | **~$125** | ~60 min |
  | `all` | 1.74M | ~$245 | ~2h |

  **No content truncation.** Previous 4000-char cap was cutting motie signatories at document bottom (project_motie_signatories.md). Length filter `[200, 15000]` chars skips noise + outliers without losing mid-chunk content. P90 chunk length is 2,545 chars — 90% of chunks fit comfortably.

  **Execution protocol:**
  1. `--init-schema` — adds `answerable_questions text[]` column.
  2. `--dry-run --limit 10` — no API calls; validate SELECT + prompt builder.
  3. `--limit 100 --no-skip-enriched` — real run, ~$0.50, **STOP for Dennis approval** on cost trajectory + output quality.
  4. Full run: `--scope p1_p2 --cost-cap 150` (default) OR `--scope p1 --cost-cap 100` (strict budget).
- [ ] **Materialize new edges** into `kg_relationships`. Target: 57K → ≥500K edges (Flair semantic relationships dominate; BAG hierarchy adds the constant 5K location skeleton).
- [ ] **Run [`scripts/link_motie_to_notulen.py`](../../scripts/link_motie_to_notulen.py)** (Phase 0 ✅) — cross-document motie↔notulen vote linking. Populates edges connecting a `motie` document to the `notulen` chunks where it was discussed/voted. Writes into `kg_relationships` using the canonical shape `(source_entity_id, target_entity_id, relation_type='DISCUSSED_IN' | 'VOTED_IN', document_id, chunk_id, confidence, metadata)`. **WS3 depends on this.**
- [ ] **Quality audit** — two layers for Phase A (the full eval gate including MCP chat replay runs after Phase B):
  - SQL: row counts per edge type, NULL/orphan checks, coverage % on `key_entities`
  - Deterministic: 100 hand-curated entity→chunk pairs validated
  - *(Optional diagnostic, not a gate)* LLM judge: 200 random edges scored 1–5 by Gemini Flash. If mean < 3.5, iterate the Gemini prompt before proceeding to Phase B. This catches obviously bad edges but cannot catch politically misleading interpretation — the MCP chat replay in the Eval gate (Layer 2) tests that.

### Phase A bis — VN Provenance Layer (added 2026-04-14)

WS12 ships virtual notulen (ASR-transcribed committee meetings) as a first-class data source — and the only source for committee-level debate. Quality varies; `staging.meetings.quality_score` (0.0–1.0) quantifies it per meeting. The existing `INCLUDE_VIRTUAL_NOTULEN` killswitch in [`services/rag_service.py:12`](../../services/rag_service.py#L12) covers the dense/BM25 streams but NOT the new graph_walk stream. Without provenance tagging on KG edges, we cannot ship VN-inclusive enrichment without producing politically misleading output.

Pattern: standard provenance-aware KG (Facebook KG / NELL, per [arXiv 2405.16929](https://arxiv.org/html/2405.16929v2)). Include VN data, tag every edge with provenance, expose filtering at query time.

**Metadata contract** — every new `kg_relationships` row written in Phase A must populate `metadata`:

```json
{
  "source": "virtual_notulen" | "official_notulen" | "motie_body" | "amendement_body"
          | "politician_registry" | "raadslid_rollen" | "bag_pdok" | "cbs_wbk"
          | "flair_ner" | "gemini_flash_lite",
  "source_quality": 0.0-1.0,
  "source_meeting_id": "..." | null,
  "source_doc_id": "..." | null,
  "extractor": "flair_ner" | "gemini_flash_lite" | "regex" | "registry_lookup",
  "extracted_at": "ISO-8601"
}
```

Effective `confidence` column = `base_confidence * source_quality`. A Gemini edge (base 0.85) from a VN meeting with quality_score 0.6 lands as 0.51 — automatically down-ranked by the existing `score_paths` confidence product.

- [ ] **Backfill source tags on the existing 57K edges** (one-shot SQL, ~1 minute, fully reversible). All pre-existing edges came from rule-based extraction over motie documents + politician_registry + raadslid_rollen — none from VN. Run before Phase A scripts:
  ```sql
  UPDATE kg_relationships SET metadata = metadata || jsonb_build_object(
    'source', CASE
      WHEN relation_type = 'LID_VAN' THEN 'politician_registry'
      WHEN relation_type = 'IS_WETHOUDER_VAN' THEN 'raadslid_rollen'
      ELSE 'motie_body'
    END,
    'source_quality', 1.0,
    'extractor', 'regex'
  ) WHERE metadata->>'source' IS NULL;
  ```
- [ ] **Tag Flair NER edges** ([`scripts/run_flair_ner.py`](../../scripts/run_flair_ner.py)) — at write time, JOIN to `documents.is_virtual_notulen` + `staging.meetings.quality_score`. Set `metadata.source = 'flair_ner'`, `source_quality = quality_score (or 1.0 for non-VN)`, `source_meeting_id`, `source_doc_id`. Mentions in `kg_mentions` do NOT need source tags (chunks themselves carry the source via `documents.doc_type`); only relationship edges get tagged.
- [ ] **Tag Gemini semantic edges** ([`scripts/gemini_semantic_enrichment.py`](../../scripts/gemini_semantic_enrichment.py)) — every HEEFT_BUDGET / BETREFT_WIJK / SPREEKT_OVER edge gets `metadata.source = 'gemini_flash_lite'` + the chunk's source provenance fields. Multiply `confidence = gemini_confidence * source_quality` before INSERT.
- [ ] **Tag motie↔notulen edges** ([`scripts/link_motie_to_notulen.py`](../../scripts/link_motie_to_notulen.py)) — when the target chunk is from a VN meeting, set `metadata.source = 'virtual_notulen'` + the meeting's quality_score. Otherwise `'official_notulen'` with quality 1.0.
- [ ] **Update `services/graph_retrieval.py`**:
  - Read `INCLUDE_VIRTUAL_NOTULEN` env var at module load (mirror the rag_service pattern).
  - `walk()`: add `exclude_sources: list[str] | None = None` and `min_source_quality: float = 0.0` parameters; both filter via the CTE.
  - `hydrate_chunks()`: same filter applied at hydration so excluded VN chunks don't leak through.
  - `score_paths()`: add `vn_penalty: float = 0.7` multiplier per VN edge in path (compound with hop penalty).
  - `retrieve_via_graph()`: read env var and pass `exclude_sources=['virtual_notulen']` to `walk()` when killswitch is on.
- [ ] **MCP tool VN-awareness** — `traceer_motie` and `vergelijk_partijen` accept `include_virtual_notulen: bool = True`. Returned JSON includes `{ "virtual_notulen_edge_count": N, "official_edge_count": M }` so the host LLM (and Dennis for press use) can judge grounding.
- [ ] **Add VN unit tests to [`tests/test_graph_retrieval.py`](../../tests/test_graph_retrieval.py)** — 4 new tests:
  1. `walk(exclude_sources=['virtual_notulen'])` filters out VN edges from the CTE result
  2. `walk(min_source_quality=0.7)` filters out edges with `metadata.source_quality < 0.7`
  3. `score_paths(vn_penalty=0.7)` reduces a VN-only path's score by the expected factor
  4. `retrieve_via_graph()` honors `INCLUDE_VIRTUAL_NOTULEN=false` env override (mock the env var)
  All 4 tests should run with the existing FakeCursor pattern — no live DB.

**Pattern references for the VN provenance work:**
- [Uncertainty Management in the Construction of Knowledge Graphs (arXiv 2405.16929, 2024)](https://arxiv.org/html/2405.16929v2) — survey of provenance + confidence patterns; Facebook KG and NELL used as canonical examples
- [Provenance-Aware Knowledge Representation: A Survey (Springer 2020)](https://link.springer.com/article/10.1007/s41019-020-00118-0) — embedded vs associated metadata trade-off (we use embedded via JSONB)
- [Construction of Knowledge Graphs: Current State and Challenges (MDPI Information 2024)](https://www.mdpi.com/2078-2489/15/8/509) — heterogeneous source integration patterns

### Phase B — Graph retrieval service + MCP tools (~5–7 days)

> **Phase 0 status (2026-04-12):** all scaffolding code is shipped. The remaining Phase B work is RUN + TEST + PROMOTE, not BUILD. Boxes below marked ✅ are code-complete from Phase 0; they still need execution / verification against live data.

- [x] **`services/graph_retrieval.py`** — ✅ shipped Phase 0 (665 lines). Public API:
  - `extract_query_entities(query: str) -> list[Entity]` — gazetteer match + politician alias resolution (no Flair at query time, kept for offline enrichment only)
  - `walk(seed_entity_ids: list[int], max_hops: int = 2, edge_types: list[str] | None = None) -> list[Path]` — recursive PostgreSQL CTE traversal of `kg_relationships` (column name `relation_type`). **Hard cap at 2 hops in v0.2.**
  - `score_paths(paths: list[Path], query_intent: str = "") -> list[ScoredPath]` — penalize long paths via `_HOP_PENALTY=0.7`; intent boosts via `_INTENT_EDGE_BOOSTS` table (motie_trace, party_comparison, location, financial)
  - `hydrate_chunks(entity_ids: list[int], gemeente: str | None = None, limit: int = 30) -> list[GraphChunk]` — joins via `kg_mentions` (NOT a nonexistent `chunk_entities` table)
  - `retrieve_via_graph(query, k, query_intent, gemeente)` — orchestration helper used by rag_service
  - `is_graph_walk_ready()` — env-gated readiness (returns False until `GRAPH_WALK_ENABLED=1`)
  - **Phase A bis adds**: `exclude_sources`, `min_source_quality`, `vn_penalty` parameters and env-var honoring.
- [x] **Bug fix — drop the legacy `%%notule%%` filter in `_retrieve_by_keywords()`** ✅ shipped 2026-04-12 (commit 7aad784). Logging added when fallback fires.
- [x] **Add 5th retrieval stream `graph_walk`** to [`services/rag_service.py`](../../services/rag_service.py) `retrieve_parallel_context` ✅ shipped 2026-04-12. Distribution updated to `{"financial": 3, "debate": 3, "fact": 2, "vision": 2, "graph": 2}`. Gated behind `GRAPH_WALK_ENABLED` env var.
- [ ] **Run [`scripts/backfill_qdrant_entity_ids.py`](../../scripts/backfill_qdrant_entity_ids.py)** (Phase 0 ✅ — script exists). Backfills `entity_ids: int[]` into all 1.7M Qdrant payloads from `kg_mentions`. Must run AFTER Phase A enrichment completes. Uses `qdrant.batch_update_points` for one HTTP round-trip per batch of 500 points. Then a graph walk can prune the dense search to *only* chunks mentioning the resolved entities → big speedup.
- [x] **MCP tool `traceer_motie(motie_id: str)`** in [`mcp_server_v3.py`](../../mcp_server_v3.py) ✅ scaffold shipped 2026-04-12. Walks: `motie → DIENT_IN → indieners → LID_VAN → partijen → STEMT_VOOR/TEGEN → uitkomst → BETREFT (wijk/programma) → linked notulen fragments`. Returns: `{motie, indieners, vote: {voor, tegen, uitkomst}, related_documents, notulen_fragments, trace_available, citation_chain, motie_id}`. Currently returns `trace_available: false` until KG enrichment lands. **This is the flagship demo tool for v0.2.0.**
  - [ ] **Test against 10 hand-validated moties** post-Phase A: Feyenoord stadion, Boijmans, Warmtebedrijf (3), Tweebosbuurt sloop, warmtenetten (2), woningbouw, leegstand. All 10 must return correct vote outcome.
- [x] **MCP tool `vergelijk_partijen(onderwerp, partijen, datum_van, datum_tot, max_fragmenten_per_partij)`** ✅ scaffold shipped 2026-04-12. For each party: existing 5-stream retrieval + post-filter on party-name-in-content + top-N. When `is_graph_walk_ready()` is true, the graph_walk stream contributes LID_VAN ∩ SPREEKT_OVER paths.
  - [ ] **Test post-Phase A**: `vergelijk_partijen(onderwerp="warmtenetten", partijen=["Leefbaar Rotterdam","GroenLinks-PvdA","VVD"])` returns differentiated per-party fragments.
- [x] **AI-consumption tool descriptions** ✅ both new tools have "use this when / do NOT use this when" descriptions per WS4 convention. WS4 is `done` per [README](README.md).

## Phase 1 Execution Runbook (agent pickup point)

**Last updated 2026-04-14.** This section is the canonical command sequence for executing WS1 Phase 1. A fresh agent can follow it top-to-bottom once the blockers below clear. All scripts are hardened (exponential backoff, pre-flight checks, advisory lock 42, `--resume` on long runs). No ad-hoc SQL needed — every step is a single CLI invocation.

### Before starting — unblocking conditions

**Status as of 2026-04-15 — all gates satisfied, ready to fire.**

| Gate | Status | Note |
|---|---|---|
| WS7 — OCR recovery | ✅ done 2026-04-14 | moties/amendementen clean; no garbled text into Gemini |
| WS11 — corpus completeness | ✅ done 2026-04-15 (commit `167dad6`) | P1 ingest complete; enriching a complete corpus |
| WS12 — virtual notulen | 🟡 deferred (Phase 1+4 live) | VN provenance scoped to 2025+2026 only; 2018-2024 backfill is v0.3/v0.4. `quality_score` populated for the meetings we have. |
| WS5a Phase A — nightly infra | ✅ done | infrastructure shipped |
| WS5a Phase B — nightly cycles | 🟡 paused per Dennis | Dennis will hold off Phase B until after WS1 v0.2.0 ships — no concurrent Postgres/Qdrant ingest |
| WS6 Phase 3 — summarization writes | 🟡 waiting on Gemini batch return | WS6 writes Postgres-only (summary fields on `document_chunks`); Dennis will serialize so WS6 holds while WS1 holds lock 42 |

The script's `preflight_checks()` will still hard-fail if any other writer is active on `kg_*` / `document_chunks` at fire time — so even with the human coordination above, the script's safety net catches a missed pause.

### Execution order (10 steps, ~24 hours wall clock)

| # | Command | Est. time | Cost | Decision point? |
|---|---|---|---|---|
| 1 | `python scripts/enrich_chunks_gazetteer.py --dry-run --limit 1000` | 1 min | $0 | No |
| 2 | `python scripts/enrich_chunks_gazetteer.py` | ~30 min | $0 | No |
| 3 | `python scripts/import_bag_locations.py --gemeente rotterdam` | ~30 min | $0 | Possibly needs `--cbs-url` override on first run (CBS rotates URLs annually) |
| 4 | `python scripts/run_flair_ner.py` | **6–12 hours** | $0 (local GPU) | Start overnight; checkpoint-resumable |
| 5 | `python scripts/gemini_semantic_enrichment.py --init-schema` | 2 sec | $0 | No |
| 6 | `python scripts/gemini_semantic_enrichment.py --dry-run --limit 10` | 30 sec | $0 | No |
| 7 | `python scripts/gemini_semantic_enrichment.py --limit 100 --no-skip-enriched` | ~1 min | ~$0.50 | **YES — STOP for Dennis approval** on output JSON quality + cost/chunk trajectory |
| 8 | `python scripts/gemini_semantic_enrichment.py --scope p1_p2 --cost-cap 150` | ~60 min @ Tier 3 | **~$125** | Auto-halts on cost cap |
| 9 | `python scripts/link_motie_to_notulen.py` | ~1 hour | $0 | No |
| 10 | `python scripts/backfill_qdrant_entity_ids.py` | 3–4 hours | $0 | Run overnight; last step before deploy |

After step 10, run the quality audit:

```bash
python -c "import psycopg2, os; from dotenv import load_dotenv; load_dotenv(); \
  c = psycopg2.connect(os.getenv('DATABASE_URL')); \
  cur = c.cursor(); \
  cur.execute(open('eval/scripts/ws1_quality_audit.sql').read()); \
  print('check eval/scripts/ws1_quality_audit.sql for expected values')"
```

Or simpler: `psql $DATABASE_URL -f eval/scripts/ws1_quality_audit.sql` if psql is on PATH.

### Cost anchors — what each $ figure is derived from (added 2026-04-15)

The cost numbers in the execution table aren't guesses — they're derived from verified corpus state + verified Gemini Flash-Lite pricing + verified throughput from comparable-shape runs. This subsection documents the inputs so a future agent can re-derive (or sanity-check actuals against expectations during the run).

#### Verified inputs

| Input | Value | Source / verified-when |
|---|---|---|
| Total chunks in `document_chunks` | **1,737,932** | WS11 final state, 2026-04-15 (commit `167dad6`) |
| P90 chunk length | **2,545 chars** | [`scripts/gemini_semantic_enrichment.py:177`](../../scripts/gemini_semantic_enrichment.py#L177) (constant cap derived from corpus measurement) |
| Total documents | ~89,381 (62,627 classified + 26,754 unclassifiable) | WS11 EVAL row in [README](README.md) |
| Schriftelijke vragen recovered | 3,851 (was 96% gap → 0 gap) | WS11 outcome |
| Moties recovered via OCR | 4,192 docs | WS7 outcome (BM25 hit rate 77.6% → 83.7%) |
| Pre-existing KG edges | **57,000** (rule-based + politician_registry + raadslid_rollen) | WS1 TL;DR + Phase A bis backfill SQL |
| Pre-existing entity-mentions | 3,300,000 | WS1 TL;DR |
| `key_entities` coverage on chunks | **25.2%** (target post-Phase A: ≥60%, expected ~75%) | Phase 0 baseline 2026-04-12 |
| Domain gazetteer entries | ~2,217 across 6 lists | [`data/knowledge_graph/domain_gazetteer.json`](../../data/knowledge_graph/domain_gazetteer.json) |

#### Verified pricing (Gemini 2.5 Flash-Lite, Tier 3 paid)

| | Per 1M tokens |
|---|---|
| Input | **$0.10** (handoff worst-case) — script default $0.075 |
| Output | **$0.40** (handoff worst-case) — script default $0.30 |

Override via env vars `GEMINI_COST_INPUT_PER_M` / `GEMINI_COST_OUTPUT_PER_M` if pricing drifts.

#### Per-chunk token budget (computed from above)

| | Tokens / chunk | Cost / chunk @ Tier 3 |
|---|---|---|
| Input (chunk body + system + user prompt) | ~636 (P90 chars / 4) + ~150 overhead = **~786** | $0.0000786 |
| Output (3-5 NL questions + 0-3 edges + topic) | ~150 questions + ~50 edges = **~200** | $0.0000800 |
| **Total per chunk** | ~986 tokens | **~$0.000158** |

That's ~$0.16 per 1,000 chunks — the unit cost that drives every scope estimate below.

#### Cost matrix (verified 2026-04-14, re-confirmed 2026-04-15 against WS11 final corpus)

| `--scope` | Chunks targeted | Derivation | Estimated cost | Time @ Tier 3 (4000 RPM) |
|---|---|---|---|---|
| `p1` | ~600K (moties, amendementen, initiatief, afdoening, raadsvoorstel, financial, speaker-attributed notulen) | 600K × $0.000158 = $95 → **rounded down** | **~$85** | ~40 min |
| `p1_p2` (default) | ~885K (P1 + 2020+ briefs + `key_entities`-tagged "other") | 885K × $0.000158 = $140 → **rounded down for batching savings** | **~$125** | ~60 min |
| `all` | 1.74M | 1.74M × $0.000158 = $275 → **rounded down** | **~$245** | ~2h |

The "rounded down" gap (~10-15%) reflects Gemini Batch API discount (20-50% off list price) minus prompt-overhead inefficiency on small chunks. Net: handoff numbers are slightly conservative against best-case batch billing, slightly optimistic against retry-heavy worst case. Spending more than the table figure is a signal something's wrong (prompt blowing up, retry loop, oversized chunks slipping through the `[200, 15000]` filter).

#### Comparable-run anchors (for sanity-checking during the run)

We have no prior WS1 Gemini run to cite, but two adjacent runs validate the underlying infrastructure:

| Reference run | What it validates | Outcome |
|---|---|---|
| **Layer 1 metadata enrichment** (Apr 6, [`enrich_metadata_checkpoint.json`](../../data/pipeline_state/enrich_metadata_checkpoint.json)) | SQL UPDATE throughput on `document_chunks` at 1.6M scale; pipeline-state checkpoint reliability | 1,630,523 processed → 1,629,909 enriched = **99.96% success rate**, no schema failures |
| **WS6 Gemini Batch run** (Apr 13-15, in flight) | Gemini Batch API at production scale (multi-thousand prompts, 24h SLA, sub-batch concurrency) | First wave: **25,500 results returned** of 29,800 targeted; cap held at $30 (planned $5/day × 6 days). 20 sub-batches submitted via wave-based interleaved submit+poll (`MAX_CONCURRENT_JOBS=15`) — no silent drops |

**Read against these during the run:**

- **Step 7 calibration (100 chunks, ~$0.50)**: actual cost should land between **$0.40-$0.60**. If higher, stop — the prompt or chunk selection is wrong.
- **Step 8 mid-run** (every 50K chunks): cost-per-chunk in checkpoint should stay in the **$0.00012-$0.00018** band. Drift above $0.0002/chunk = retry storm or prompt blow-up; the script logs `WARNING` if `edges_rejected/total > 20%` which is the leading indicator.
- **Final cost** (full p1_p2 run): expect **$110-$140**. Outside that band → root-cause before marking step 8 complete.
- **Edge yield** (post-step 8): expect **~3-5 edges per chunk on P1, ~1-2 on P2** based on signatory density. Total target ≥500K edges (vs. current 57K). If yield <100K after step 8, the prompt's edge-rules section is being ignored — iterate before step 9.

#### Non-cost anchors (count-validation gates during the run)

| After step | SQL check | Expected |
|---|---|---|
| Step 2 (gazetteer) | `SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE array_length(key_entities,1) > 0) / COUNT(*), 1) FROM document_chunks` | ≥35% (from 25.2% baseline; quick-win adds ~10pp) |
| Step 3 (BAG) | `SELECT COUNT(*) FROM kg_entities WHERE type='Location' AND metadata->>'gemeente'='rotterdam'` | ≥4,500 (full state ~5,135) |
| Step 4 (Flair) | Same coverage check as step 2 | ≥60% (handoff acceptance criterion) |
| Step 8 (Gemini) | `SELECT COUNT(*) FROM kg_relationships WHERE metadata->>'source'='gemini_flash_lite'` | ≥300K (drives the ≥500K total target) |
| Step 9 (motie linker) | `SELECT COUNT(*) FROM kg_relationships WHERE relation_type IN ('DISCUSSED_IN','VOTED_IN')` | ≥10K (depends on motie↔notulen overlap density) |
| Step 10 (Qdrant) | `client.count('notulen_chunks', count_filter=Filter(must=[FieldCondition(key='entity_ids', match=MatchAny(any=[1,2,3]))]))` | ≥1.0M points have `entity_ids` populated (≥60% of 1.74M) |

If any expected number is off by >20%, **pause the next step** and root-cause — the script's pre-flight check only catches structural problems, not semantic drift.

---

### Chunk filter rationale (added 2026-04-15)

The SELECT query in `gemini_semantic_enrichment.py` applies `[200, 50000]` char bounds. The upper bound was raised from 15,000 → 50,000 on 2026-04-15 (commit pending, see [§Filter change](#filter-change)).

**Why the filter exists — data provenance, not architectural choice.**

The target architecture is correct: enrich grandchildren only, target 2,500 chars, no chunk should exceed that. The filter is needed because two eras of chunks coexist in the DB:

| Era | When created | max_chunk_chars enforced? | Oversized (>15K) | Max seen |
|---|---|---|---|---|
| v1 (pre-April-5) | before 2026-04-05 | **No** | 1,924 chunks (0.131%) | 1,609,723 chars |
| v2 (post-April-5) | after 2026-04-05 | **Yes** (2,500 limit added commit 1673562) | 78 chunks (0.029%) | 30,704 chars |

The v1 chunker had no size cap; a hard-cut fallback in `_find_best_break()` at [pipeline/ingestion.py:436](../../pipeline/ingestion.py#L436) could emit one oversized grandchild for the entire document.

**"If we only focus on the smallest chunks, why would we still have very large chunks?"** (validated against live DB 2026-04-15)

You're right — we effectively do already. The `[200, 50000]` filter is exactly the "pick the smallest usable unit per document" strategy, implemented at query time instead of at storage time. Validation numbers:

| Category | Count (>50K catastrophic) | Count (>15K wider band) |
|---|---|---|
| Doc has ONLY the oversized grandchild (no smaller siblings) | 1 | 20 |
| Doc has siblings but all are out of band | 0 | 9 |
| **Doc has usable smaller siblings alongside the oversized one** | **82 (98.8%)** | **1,973 (98.6%)** |

For 98.8% of the "catastrophic" cases, the same document already has smaller grandchildren that the filter keeps and sends to Gemini — we just skip the giant sibling. A 1.6M-char blob is **never** fed to the LLM.

The oversized chunks still sit in storage (legacy v1 chunker output, pre-April-5 2026) — they're filtered OUT at Gemini time, not removed from the DB. Physical cleanup via `scripts/resplit_oversized_chunks.py` (not yet written) is post-v0.2.0 — see Future work.

For the 1 truly unrecoverable doc (>50K) / 20 unrecoverable docs (>15K band) where the document has ONLY an oversized grandchild and nothing else: those lose Gemini enrichment entirely until resplit runs. That's ≤0.005% of the scoped corpus — acceptable.

**On the parent/child relationship** (earlier assertions corrected 2026-04-15 against live data):

- `document_children` is NOT always 1:1 with documents. Of 92,295 documents: 66,615 have exactly 1 child, **22,869 have >1 child** (max 393 children/doc).
- Children do NOT always store the same full text as the parent. For some documents (e.g., doc_id 2374427: parent=230,898 chars, single child=7,153 chars), the child is a summary or excerpt.
- For documents with oversized grandchildren: sum(grandchildren content) can be 3-5× the parent length (duplication/overlap in the chunker's output).

None of this changes the filter decision — the filter operates on `document_chunks` alone and is correct. But the earlier "children mirror parents" narrative was an oversimplification, removed.

**Impact of the 15K → 50K change** (audit 2026-04-15):
- Additional chunks included: +1,919 (15K-50K band, legitimate long annexes)
- Remaining exclusions (>50K): 83 catastrophic failures — whole docs, skip correct
- Speaker-attributed notulen exclusion: 0.16% → 0.005%
- Cost delta: +$0.36

---

### Phase 0.5a — Motie pre-enrichment pass (added 2026-04-15)

**Script:** `scripts/enrich_motie_relationships.py`
**Run BEFORE:** `gemini_semantic_enrichment.py`
**Run AFTER:** `populate_kg_relationships.py` (so DIENT_IN baseline exists)

#### Why this pass exists

The rule-based `DIENT_IN` edges (57K baseline) come from `document_chunks.indieners` TEXT[] arrays extracted by regex in `enrich_and_extract.py`. That regex achieves ~75-80% precision. More critically: **there is no Party→Motie proposer edge at all.** Today `traceer_motie` must infer via `Person → LID_VAN → Party` (two hops, lossy). This pass closes both gaps.

#### What it adds

| New edge type | Direction | Description |
|---|---|---|
| `PROPOSED_BY` | Party → Motie | Proposing party — **new, 0 today** |
| `SIGNED_BY` | Person → Motie | Gemini-reconciled signatory (confidence ≥ 0.85) — coexists with DIENT_IN |

DIENT_IN is **not deleted** — SIGNED_BY is additive. Graph traversal can use whichever confidence band suits the query.

#### Scope & cost

- Targets: `LOWER(d.name) LIKE '%motie%' OR LIKE '%amendement%'` — same filter as the main script
- Estimated chunks: ~80K (130,562 motie/amend total, filtering <200 and >50K chars)
- Cost: **~$2-10** (moties are shorter avg than notulen; cost-cap default 30.00)
- Runtime: ~1-2 hours

#### Run command

```bash
# Dry run (no API calls, no writes)
python scripts/enrich_motie_relationships.py --dry-run --limit 500

# Full run
python scripts/enrich_motie_relationships.py --cost-cap 30

# Acceptance gate (run after completion)
psql $DATABASE_URL -c "SELECT COUNT(*) FROM kg_relationships WHERE relation_type = 'PROPOSED_BY';"
# expect >= 3,000
psql $DATABASE_URL -c "SELECT COUNT(*) FROM kg_relationships WHERE relation_type = 'SIGNED_BY';"
# expect >= 10,000

# Rollback if needed
psql $DATABASE_URL -c "DELETE FROM kg_relationships WHERE metadata->>'source' = 'gemini_motie_pass';"
```

---

### Phase 0.5b — Sibling-context calibration experiment (added 2026-04-15)

**Folded into the existing calibration gate** (step 7 of the runbook, ~$0.50 total, 100 chunks).

The `--sibling-context` flag was added to `gemini_semantic_enrichment.py`. When set, each chunk payload includes `prev_chunk_title` and `next_chunk_title` (via `LAG`/`LEAD` window functions on `dc.id PARTITION BY dc.document_id`).

**A/B procedure within the calibration gate:**

```bash
# Baseline: 50 chunks, no sibling context
python scripts/gemini_semantic_enrichment.py --limit 50 --no-skip-enriched --dry-run
# → record edge_count, rejection_rate

# Variant: same 50 chunks + sibling context
python scripts/gemini_semantic_enrichment.py --limit 50 --no-skip-enriched --sibling-context --dry-run
# → record edge_count, rejection_rate
```

**Gate rule:** if sibling-context produces ≥10% more valid edges with no increase in rejection rate → add `--sibling-context` to the full-run command. Otherwise, proceed without it (defer to post-v0.2.0).

---

### End-to-end design walkthrough (7 questions, added 2026-04-15)

#### 1. How we send chunks to the LLM

**Source:** `SELECT` from `document_chunks` with scope filter + length filter `[200, 50000]` — see [`build_chunk_selection_clause`, gemini_semantic_enrichment.py:948-1013](../../scripts/gemini_semantic_enrichment.py#L948-L1013).

**Batching:** 20 chunks per Gemini call (script default). At ~986 tokens/chunk avg, that's ~19.7K input tokens/batch — safely under the 30K output budget.

**Per-chunk context passed** (via [`build_chunk_payload`, gemini_semantic_enrichment.py:914-937](../../scripts/gemini_semantic_enrichment.py#L914-L937)):

| Field | Source | Why |
|---|---|---|
| `id` | `document_chunks.id` | Gemini echoes back — required for write-back join |
| `title` | `document_chunks.title` (200 char trunc) | Section label (notulen agendapunt titles) |
| `doc_name` | `documents.name` (200 char trunc) | Disambiguates "Motie 2024-05" vs "Voortgangsrapportage parkeren 2024" |
| `meeting_name` | `staging.meetings.name` | Temporal + committee context ("Commissie BFO 15-mei-2024") |
| `existing_topic` | `document_chunks.section_topic` | Rule-based value; Gemini overwrites only if strictly more specific |
| `speaker_hint` | chunk content prefix | Pre-extracted speaker tag for SPREEKT_OVER edges |
| `content` | `document_chunks.content` | Full chunk body — **never truncated mid-content** (4,000-char bug fixed 2026-04-14) |

**Hierarchical guarantee:** we read only from `document_chunks`. `document_children.content` is never passed to Gemini. No duplication possible at any corpus size.

#### 2. With what instruction

System prompt is closed-vocabulary + evidence-required. Full text at [gemini_semantic_enrichment.py:265-295](../../scripts/gemini_semantic_enrichment.py#L265-L295):

> *"Je bent een annotator voor Nederlandse gemeenteraadsdocumenten… Je verzint niets: alleen feiten die letterlijk in de chunk staan. Elke edge MOET onderbouwd worden met een quote van maximaal 200 karakters uit het chunk zelf."*

Per chunk, the LLM must return:
- `answerable_questions` — 3-5 natural Dutch questions using concrete entities (names, amounts, wijken, years)
- `section_topic` — ≤80 chars, or empty if rule-based is already more specific
- `edges` — 0 or more relations, **strictly from closed vocabulary**

**Closed relation vocabulary** (only 3 types — Gemini cannot invent new ones):

| Relation | Source → Target | When to emit |
|---|---|---|
| `HEEFT_BUDGET` | Budget → Topic/Document | Explicit EUR amounts (e.g., "EUR 4.5 miljoen jeugdzorg") |
| `BETREFT_WIJK` | Location → Topic/Document | Specific Rotterdam wijk/buurt name (NOT country/province/city) |
| `SPREEKT_OVER` | Person → Topic | Speaker attribution present in chunk or `speaker_hint` |

Response schema is enforced at the API layer ([RESPONSE_SCHEMA, gemini_semantic_enrichment.py:222-262](../../scripts/gemini_semantic_enrichment.py#L222-L262)) — Gemini returns typed JSON only; prose responses fail parse and are retried once, then the batch is dropped.

#### 3. How we deal with cross-chunk relationships

Per-chunk extraction is deliberately independent — no sibling context is passed by default (see Phase 0.5b for the calibration experiment). Cross-chunk linking happens in two separate layers:

**Layer A — rule-based pre-existing edges** (already shipped, 57K edges):
- `populate_kg_relationships.py`: `LID_VAN`, `IS_WETHOUDER_VAN`, `STEMT_VOOR`/`STEMT_TEGEN`, `DIENT_IN`, `AANGENOMEN`/`VERWORPEN`
- Derived from structured fields (politician_registry, raadslid_rollen, motie signatory block regex) — no LLM, no hallucination

**Layer B — cross-document motie↔notulen linker** ([`scripts/link_motie_to_notulen.py`](../../scripts/link_motie_to_notulen.py), Phase 0 ✅):
- Connects motie documents to notulen chunks where the motie is discussed/voted
- Writes `DISCUSSED_IN` / `VOTED_IN` edges with `chunk_id` and `document_id` provenance
- How WS3 journey timelines will stitch motie→debate→vote

**Layer C — query-time graph walk** ([`services/graph_retrieval.py`](../../services/graph_retrieval.py)):
- `walk()` does recursive CTE traversal up to 2 hops
- Hop penalty 0.7×, intent boosts per query type, VN penalty 0.7× per VN edge in path

#### 4. How we prevent hallucination

Five layers (all currently implemented):

1. **Schema-typed output** — `response_schema` passed to the SDK. Gemini cannot return prose; only typed JSON. Malformed → retry once → drop batch.
2. **Closed vocabulary enforcement** — only 3 relation types accepted. Enforced at write time ([gemini_semantic_enrichment.py:1156-1175](../../scripts/gemini_semantic_enrichment.py#L1156-L1175)): off-vocabulary rejected, counted in `stats.edges_rejected`.
3. **Quote requirement** — every edge must include a ≤200-char verbatim quote from the chunk. Enforced in system prompt.
4. **BAG resolution for locations** — `BETREFT_WIJK` source names joined against PDOK BAG skeleton via `resolve_source_entity`. Invented wijk names become `metadata.level='generic'` rows flagged in audits.
5. **Rejection-rate alarm + calibration gate** — `edges_rejected/total > 20%` triggers WARNING. Plus: $0.50 calibration run on 100 chunks before the full run (step 7 of runbook).

**What this catches:** fabricated edges, off-vocabulary relations, invented wijken.

**What this does not catch:** *interpretive* hallucinations — quote-supported but misinterpreted inference. This is what the **Layer 2 MCP chat replay** (6 RED sessions) is designed to surface. Chunk-level LLM-judge scores miss it; real queries catch it.

#### 5. How we establish KG relations across information pieces

Three-stage aggregation, each writing to `kg_entities` + `kg_relationships`:

**Stage 1 — Motie pre-enrichment** (`enrich_motie_relationships.py`, Phase 0.5a):
- PROPOSED_BY (Party → Motie) + SIGNED_BY (Person → Motie, confidence-scored)
- Runs on ~80K motie/amend chunks

**Stage 2 — Chunk-level semantic extraction** (`gemini_semantic_enrichment.py`, main pass):
- Emits HEEFT_BUDGET, BETREFT_WIJK, SPREEKT_OVER edges with chunk_id + quote
- Entities upserted via `UNIQUE(type, name)` in kg_entities

**Stage 3 — Cross-document linking** (rule-based scripts post-Gemini):
- `link_motie_to_notulen.py` — DISCUSSED_IN / VOTED_IN edges
- `populate_kg_relationships.py` — already shipped 57K baseline (LID_VAN, IS_WETHOUDER_VAN, STEMT_*, DIENT_IN, AANGENOMEN/VERWORPEN)
- BAG import — LOCATED_IN hierarchy (Heemraadssingel → Middelland → Delfshaven → Rotterdam)

**Stage 4 — Query-time graph traversal** (`graph_retrieval.py`):
- Recursive CTE over `kg_relationships` with hop cap 2
- Path scoring: `total_confidence × hop_penalty^(hops-1) × intent_boost × vn_factor`
- Hydration via `kg_mentions` (chunk↔entity link table)
- Results returned as 5th retrieval stream alongside dense/BM25/fact/vision

**Entity deduplication:** `UNIQUE(type, name)` on kg_entities prevents duplicate nodes. Same-name collisions across types are separated by `metadata.gemeente` — locked here for v0.2.1 multi-portal.

#### 6. How we leverage metadata

**Input metadata** (passed to Gemini at extraction time):

| Layer | Fields | Purpose |
|---|---|---|
| Document | `d.name`, `d.doc_classification`, `d.municipality`, `d.ocr_quality`, `d.category` | Disambiguation + quality signal |
| Meeting | `meetings.name`, `meetings.start_date`, `meetings.committee`, `meetings.quality_score` | Temporal + political context + VN provenance |
| Chunk | `dc.title`, `dc.chunk_type`, `dc.section_topic` (rule-based), `dc.key_entities`, speaker prefix in content | Section grounding + pre-tagged entities |

**Output metadata** (written to `kg_relationships.metadata` per Phase A bis contract):

```json
{
  "source": "gemini_flash_lite",
  "source_quality": 0.0-1.0,
  "source_meeting_id": "<staging.meetings.id or null>",
  "source_doc_id": "<documents.id>",
  "extractor": "gemini_flash_lite",
  "extracted_at": "<ISO-8601 UTC>",
  "gemini_model": "gemini-2.5-flash-lite-001",
  "gemini_ts": "<same as extracted_at>",
  "rule_section_topic": "<preserved rule-based value>"
}
```

**Retrieval-time metadata use:**
- `exclude_sources=['virtual_notulen']` — `walk()` filters VN edges via CTE
- `min_source_quality=0.7` — filters low-trust edges
- `INCLUDE_VIRTUAL_NOTULEN=false` env killswitch
- `vn_penalty=0.7` — applied per VN edge in path score

#### 7. What the actual output is

**Per chunk, Gemini returns:**

```json
{
  "id": 1247833,
  "answerable_questions": [
    "Welk bedrag wordt uitgetrokken voor jeugdzorg in 2024?",
    "Welke wijken profiteren van het warmtenet-budget?",
    "Wie sprak er in de commissie over de begroting?"
  ],
  "section_topic": "Begroting 2024 jeugdzorg EUR 4.5 miljoen",
  "edges": [
    {
      "source_name": "EUR 4.5 miljoen",
      "source_type": "Budget",
      "target_name": "jeugdzorg 2024",
      "target_type": "Topic",
      "relation_type": "HEEFT_BUDGET",
      "confidence": 0.92,
      "quote": "Het college stelt EUR 4.5 miljoen beschikbaar voor jeugdzorg in 2024."
    }
  ]
}
```

**Persisted to database:**

| Target | What gets written |
|---|---|
| `document_chunks.answerable_questions` | `text[]` of 3-5 NL questions |
| `document_chunks.section_topic` | Refined ≤80 char topic (only if strictly more specific than rule-based) |
| `kg_entities` | Budget/Location/Person/Topic nodes upserted |
| `kg_relationships` | Edge row with confidence, quote, full provenance metadata |
| `kg_mentions` | **Not populated here** — Flair NER handles this separately |

**Aggregate WS1 Phase 1 deliverables** (end-state after all steps):

| Deliverable | Target | Acceptance SQL |
|---|---|---|
| KG edges (total) | 57K → **≥500K** | `SELECT COUNT(*) FROM kg_relationships` |
| PROPOSED_BY edges (motie pass) | 0 → **≥3,000** | `WHERE relation_type='PROPOSED_BY'` |
| SIGNED_BY edges (motie pass) | 0 → **≥10,000** | `WHERE relation_type='SIGNED_BY'` |
| `key_entities` chunk coverage | 25.2% → **≥60%** | `COUNT(*) FILTER (WHERE array_length(key_entities,1)>0) / COUNT(*)` |
| Qdrant entity_ids backfill | 0% → **≥60%** | Qdrant payload sample |
| MCP `traceer_motie` | 10/10 hand-validated correct vote + proposing party | Manual test post-deploy |
| MCP `vergelijk_partijen` | Differentiated per-party fragments on "warmtenetten" | Manual test post-deploy |
| MCP chat replay | 6/6 RED sessions pass, composite ≥3.0/5 | A/B with VN on/off |
| Edges with `metadata.source` populated | 100% | `COUNT WHERE metadata->>'source' IS NULL = 0` |

---

### Concurrency strategy & agent fanout (added 2026-04-15)

WS1 Phase 1 is fundamentally **lock-serialized** — every script that mutates `kg_*` or `document_chunks` must hold `pg_advisory_lock(42)`. You cannot speed up the critical path by throwing more agents at the lock-holding scripts (they'd block or — if they bypass the lock — corrupt segments per [project_embedding_process.md](../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_embedding_process.md)).

**But** ~10h of the 24h wall-clock window is **idle waiting on overnight jobs** (Flair NER + Qdrant backfill). Fan out parallel agents onto independent files during those windows.

#### Three-lane execution model

```
═══════════════════════════════════════════════════════════════════════════════
  LANE 1: lock-holder (single agent, holds pg_advisory_lock(42) end-to-end)
═══════════════════════════════════════════════════════════════════════════════
  ┌─────────┬─────────┬──────────────────┬──────┬─────────┬──────┬───────────┐
  │ gazettr │ BAG     │   Flair NER      │ STOP │ Gemini  │ link │  Qdrant   │
  │ ~30 min │ ~30 min │   6–12h overnite │ $.50 │ ~60 min │ ~1h  │  3–4h     │
  └─────────┴─────────┴──────────────────┴──────┴─────────┴──────┴───────────┘
   step 2     step 3       step 4         step 7   step 8   step 9   step 10
                                            ↑
                                  Dennis approval gate
                                  (manual inspect 100 chunks)

═══════════════════════════════════════════════════════════════════════════════
  LANE 2: parallel prep agents (no lock contention; touch independent files)
═══════════════════════════════════════════════════════════════════════════════
            ┌──[A4]──────┐
            │ stage      │   ← git add -p mcp_server_v3.py VN-awareness edits
            │ MCP commit │     (do NOT push — interleaved with WS4/WS6/WS8f WIP)
            └────────────┘
                          ┌──[A5]────────────────┐
                          │ write 4 VN unit      │   ← tests/test_graph_retrieval.py
                          │ tests (FakeCursor)   │     (no live DB needed)
                          └──────────────────────┘
                                                 ┌──[A6]──────────────────┐
                                                 │ build 10-motie smoke   │   ← prep for §C2
                                                 │ test harness (Python)  │
                                                 └────────────────────────┘
                                                 ┌──[A7]──────────────────┐
                                                 │ prep 6 MCP replay      │   ← R1–R6 from
                                                 │ session scripts (.md)  │     Eval gate Layer 2
                                                 └────────────────────────┘

═══════════════════════════════════════════════════════════════════════════════
  LANE 3: post-promotion agents (after lock 42 releases — fully parallel)
═══════════════════════════════════════════════════════════════════════════════
  ┌─[C1]──┐  ┌─[C2]──────┐  ┌─[C3]──────┐  ┌─[C4]──────────────┐  ┌─[C5]───┐
  │ kamal │  │ traceer_  │  │ vergelijk │  │ 6 MCP replay      │  │ audit  │
  │ deploy│→ │ motie ×10 │  │ partijen  │  │ sessions A/B      │  │ SQL +  │
  │ +flag │  │ smoke     │  │ smoke     │  │ (VN on/off)       │  │ Outcome│
  └───────┘  └───────────┘  └───────────┘  └───────────────────┘  └────────┘
   serial      independent    independent    independent           independent
   (1 owner)   (read-only)    (read-only)    (read-only)           (read-only)

═══════════════════════════════════════════════════════════════════════════════
  PAUSED FOR THE DURATION (Dennis-managed; not agent-managed)
═══════════════════════════════════════════════════════════════════════════════
  • WS5a Phase B nightly cycles → resume after WS1 v0.2.0 ships
  • WS6 Phase 3 Postgres writes → drained before lane 1 starts; held until done
═══════════════════════════════════════════════════════════════════════════════
```

#### Pre-flight fanout (parallel, ~30 min, 3 agents before Lane 1 starts)

| Agent | Task | Lock-safe? |
|---|---|---|
| **P1** | Verify WS5a Phase B cron paused; verify WS6 Phase 3 has no in-flight writes (`pg_stat_activity` filter on `UPDATE document_chunks`) | Read-only |
| **P2** | `python scripts/gemini_semantic_enrichment.py --dry-run --limit 1000` (validates SELECT + prompt builder, no writes, no API spend) | Read-only |
| **P3** | `python scripts/enrich_chunks_gazetteer.py --dry-run --limit 1000` + sanity-check BAG state via `eval/scripts/ws1_quality_audit.sql` | Read-only |

#### Lane 1 step → Lane 2 parallel work map

| Lane 1 active step | Wall time | Lane 2 agents that can work in parallel |
|---|---|---|
| Step 2 (gazetteer) | 30 min | A4 stages MCP commit |
| Step 3 (BAG import) | 30 min | A5 writes VN unit tests |
| Step 4 (**Flair NER**) | **6–12h overnight** | A6 builds 10-motie smoke harness; A7 preps R1–R6 replay scripts; orthogonal WS work (WS15 / WS2b / WS17) can also run since they don't touch `kg_*` |
| Step 7 (Gemini calibration) | 1 min | **STOP — Dennis approval gate.** No agent fires step 8 without explicit go. |
| Step 8 (Gemini full) | 60 min | A8 monitors cost trajectory (`tail -f` checkpoint file) |
| Step 9 (motie linker) | 1 hour | — (small, fast) |
| Step 10 (**Qdrant backfill**) | **3–4h overnight** | A9 dry-runs the §C2 traceer_motie smoke harness against staging |

#### Safety rails (apply to every agent in the fanout)

1. **One lock-holder.** Only one agent runs lock-holding scripts at a time. Others must `SELECT pg_try_advisory_lock(42)` and back off if held — never use `--force` or bypass the lock.
2. **No `mcp_server_v3.py` pushes during the run.** A4 stages the VN-awareness edit; promotion happens in Lane 3 (step C1) after lock 42 releases. Pushing mid-run risks shipping the VN edits without the corresponding KG state.
3. **Step 7 is the human-in-the-loop gate.** $0.50 buys the first 100 enriched chunks. Dennis must inspect output JSON quality + cost-per-chunk before any agent fires step 8 ($125).
4. **Read-only agents stay read-only.** Lane 2 + Lane 3 agents must NOT issue writes against `kg_*` / `document_chunks` / Qdrant. Lane 2 file edits are local (tests + scripts + staged commits); Lane 3 reads only via MCP tools.
5. **`preflight_checks()` is the last line of defense.** If a human coordination step is missed (WS6 didn't drain, WS5a Phase B didn't pause), the script aborts with exit code 5 and a clear "fix this" message. Don't override.

#### Wall-clock summary

| Phase | Critical path | With fanout |
|---|---|---|
| Phase 0 (pre-flight) | 30 min serial | **10 min** with 3 parallel agents |
| Phase 1 (lock-held) | ~24h serial (Flair + Qdrant dominate) | **~24h** — no shrink possible; but Lane 2 productive during Flair/Qdrant idle ~10h |
| Phase 2 (promote + eval) | ~6h serial | **~2h** with 5 parallel agents |
| **Total wall clock** | ~30h | **~26h** with productive fanout |

Real win from fan-out is not raw speed — it's that **all the eval scaffolding is ready the moment Lane 1 finishes**, so Lane 3 can hit the ground running instead of starting from scratch at hour 24.

### Known-good gates between steps

Don't skip these — they catch the predictable failure modes:

- **After step 2**: `SELECT COUNT(*) FROM document_chunks WHERE 'Heemraadssingel' = ANY(key_entities)` must return ≥1. That's the R3 eval-gate prerequisite.
- **After step 3**: `SELECT COUNT(*) FROM kg_entities WHERE type='Location' AND metadata->>'gemeente'='rotterdam'` must return ≥4,500 (full BAG state — the Gemini pre-flight requires this).
- **After step 4**: `SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE array_length(key_entities,1) > 0) / COUNT(*), 1) FROM document_chunks` must show ≥60% coverage (handoff target).
- **After step 7 (calibration)**: manually inspect the 100 enriched chunks. Look for VN vs official confidence differentiation (VN rows have `confidence < gemini_confidence`). Check `stats.edges_rejected` in logs — if >20%, the prompt needs tuning before step 8.
- **After step 8 (full Gemini)**: `SELECT relation_type, COUNT(*) FROM kg_relationships WHERE metadata->>'source'='gemini_flash_lite' GROUP BY 1` must show HEEFT_BUDGET, BETREFT_WIJK, SPREEKT_OVER with reasonable counts (tens of thousands).
- **After step 10**: Qdrant point sample — `client.retrieve(collection_name='notulen_chunks', ids=[<sample_id>], with_payload=True)` must show `entity_ids` populated.

### Promote + test (after audit passes)

11. **Stage `mcp_server_v3.py` VN-awareness edits** (uncommitted as of 2026-04-14; interleaved with WS4/WS6/WS8f WIP). Options: `git add -p` (interactive), or commit the whole file if all WIP from other workstreams is ready. The VN-awareness changes add `include_virtual_notulen` arg + VN/official edge-count fields to `traceer_motie` + `vergelijk_partijen`.
12. Deploy via Kamal: `kamal deploy` (pattern per [`deploy` skill](../../CLAUDE.md) or `scripts/deploy.sh`).
13. Flip `GRAPH_WALK_ENABLED=1` in container env (`config/deploy.yml` or `.env`), restart MCP server + FastAPI.
14. Smoke-test the 15 existing MCP tools — must not regress.
15. Test `traceer_motie` on the 10 hand-validated moties listed in Acceptance Criteria §8.
16. Test `vergelijk_partijen(onderwerp="warmtenetten", partijen=["Leefbaar Rotterdam","GroenLinks-PvdA","VVD"])`.

### Final eval gate (A/B replay)

Run each of the [6 MCP chat replay sessions](#layer-2--mcp-chat-replay-the-real-quality-gate) **twice**:

1. Default mode (`INCLUDE_VIRTUAL_NOTULEN=true`) — max recall
2. Strict mode (`INCLUDE_VIRTUAL_NOTULEN=false`) — provenance-pure

Score each session against the 1.8/5 baseline at [`eval/baselines/ws1_pre_enrichment_baseline.md`](../../eval/baselines/ws1_pre_enrichment_baseline.md). Acceptance:

- Composite score ≥ 3.0/5 (from 1.8)
- VN-strict delta ≤ 0.3 below default on every session (if delta inverts, VN is contributing noise and needs remediation)
- All 4 RED items pass

Fill in the **Outcome** section at the bottom of this file with actuals, surprises, follow-ups. Mark `Status → done` in the header.

### If you get stuck

- **preflight_checks fails on BAG** → step 3 didn't complete. Re-run `scripts/import_bag_locations.py --gemeente rotterdam` (idempotent).
- **preflight_checks fails on active writers** → check `pg_stat_activity` for rogue processes. Often the committee_notulen_pipeline if it's restarted.
- **Gemini cost cap hits early** → verify real spend matches expected (~$0.00028/chunk); if way higher, prompt/chunk selection is wrong, inspect calibration output.
- **graph_walk returns empty in testing** → `GRAPH_WALK_ENABLED` env var not set or `kg_relationships` count < 200K (see `services/graph_retrieval.py:is_graph_walk_ready`).

### Phase-1 code inventory (reference)

All shipped, no further edits required:

| File | Purpose | Shipped in |
|---|---|---|
| [scripts/enrich_chunks_gazetteer.py](../../scripts/enrich_chunks_gazetteer.py) | Chunk-text gazetteer pass (Heemraadssingel fix) | ce64706 |
| [scripts/run_flair_ner.py](../../scripts/run_flair_ner.py) | Flair Dutch NER over chunks | ce64706 |
| [scripts/import_bag_locations.py](../../scripts/import_bag_locations.py) | PDOK BAG + CBS hierarchy import | ce64706 |
| [scripts/gemini_semantic_enrichment.py](../../scripts/gemini_semantic_enrichment.py) | Semantic edge enrichment (hardened 2026-04-14) | ce64706 → d6e1d58 → 44c87d5 |
| [scripts/link_motie_to_notulen.py](../../scripts/link_motie_to_notulen.py) | DISCUSSED_IN / VOTED_IN edges | ce64706 + cf43441 (VN) |
| [scripts/backfill_qdrant_entity_ids.py](../../scripts/backfill_qdrant_entity_ids.py) | Qdrant payload backfill | ce64706 |
| [services/graph_retrieval.py](../../services/graph_retrieval.py) | 5th retrieval stream + VN filters | ce64706 + cf43441 |
| [tests/test_graph_retrieval.py](../../tests/test_graph_retrieval.py) | 30 unit tests | ce64706 + cf43441 |
| [eval/scripts/ws1_quality_audit.sql](../../eval/scripts/ws1_quality_audit.sql) | 12-check post-enrichment audit | ce64706 |
| [eval/baselines/ws1_pre_enrichment_baseline.md](../../eval/baselines/ws1_pre_enrichment_baseline.md) | 1.8/5 composite pre-enrichment baseline | c7d2c13 |

## Acceptance criteria

- [ ] `kg_relationships` row count ≥ 500K (was 57K), including ~5K BAG-derived `LOCATED_IN` edges
- [ ] `key_entities` coverage on chunks ≥ 60% (target ~75% with the quick-win chunk-text gazetteer pass + Flair combined)
- [ ] BAG hierarchy import is reproducible: `scripts/import_bag_locations.py --gemeente rotterdam` rebuilds the location skeleton idempotently from PDOK + CBS sources
- [ ] All `Location` entities in `kg_entities` use BAG `openbare_ruimte` IDs as primary key and have `gemeente` populated
- [ ] Test query: `"Heemraadssingel parkeren"` returns chunks via the new chunk-text-tagged path (was 0 hits in [FEEDBACK_LOG.md 2026-04-11](../../.coordination/FEEDBACK_LOG.md))
- [ ] `services/graph_retrieval.py` exists and exposes the 4 functions above
- [ ] `services/rag_service.py:retrieve_parallel_context` calls a 5th `graph_walk` stream
- [ ] Every Qdrant point payload has `entity_ids` populated
- [ ] `traceer_motie` MCP tool returns a structured trace for the following 10 hand-validated moties, each with correct `vote.uitkomst` AND ≥1 indiener resolved to a politician_registry entry: (1) Feyenoord City stadion, (2) Boijmans depot, (3) Warmtebedrijf "Definitief stekker eruit" 2019-11-12, (4) Warmtebedrijf "Publiek kader" 2020-11-12, (5) Warmtebedrijf "Grip op besluitvorming" 2020-06-18, (6) Tweebosbuurt "Stop de sloop" 2019-10-03, (7) Tweebosbuurt "Bouwen aan een nieuwe buurt" 2018-11-29, (8) warmtenetten 2023, (9) woningbouw leegstand 2024, (10) one operator-chosen recent motie from the 2025 corpus.
- [ ] `vergelijk_partijen` MCP tool returns coherent side-by-side for `topic="warmtenetten", partijen=["Leefbaar Rotterdam", "GroenLinks-PvdA", "VVD"]`
- [ ] Both new tools have AI-consumption descriptions registered with the WS4 tool registry
- [ ] No regression on existing 13 tools (run smoke tests against each)
- [ ] Cross-document motie↔notulen edges populated; visible in `traceer_motie` output
- [ ] **VN provenance:** 100% of `kg_relationships` rows written in Phase A have `metadata.source` populated. Verify: `SELECT COUNT(*) FROM kg_relationships WHERE metadata->>'source' IS NULL` returns zero.
- [ ] **VN provenance:** the 57K pre-existing edges are backfilled with `metadata.source` (motie_body / politician_registry / raadslid_rollen as appropriate).
- [ ] **VN provenance:** `walk(exclude_sources=['virtual_notulen'])` returns no paths that include a VN-derived edge (covered by `tests/test_graph_retrieval.py`).
- [ ] **VN provenance:** `INCLUDE_VIRTUAL_NOTULEN=false` removes VN chunks from both graph_walk results AND their hydrated chunks.
- [ ] **VN provenance:** edge count by source published in the quality audit, with VN-derived share explicitly broken out (see expected distribution in WS1 plan addendum).

## Eval gate

### Layer 1 — Automated metrics (necessary but not sufficient)

| Metric | Target |
|---|---|
| Completeness on rag_evaluator benchmark (30 questions) | ≥ 3.5 (was 2.75) |
| Faithfulness (no regression) | ≥ 4.5 |
| `traceer_motie` precision (10 hand-validated moties) | 10/10 correct vote outcome |
| Graph walk latency p95 | < 1.5s |

Add 10 multi-hop questions to [`eval/data/questions.json`](../../eval/data/questions.json) before measuring completeness. **At least 1 of the 10 must test coalition-status-at-time during historical vote interpretation** *(added 2026-04-11)* — e.g. *"In 2018 stemde de raad over de sloop in de Tweebosbuurt. Welke partijen stemden voor, en waren zij op dat moment coalitie- of oppositiepartij?"* Gold answer requires the system to (a) find the stemming, (b) recognize the date, (c) resolve coalition-at-time via the WS4 `coalition_history` primer field — not guess party roles from training data. Rationale: 2026-04-11 woningbouw session produced exactly this class of framing error (GL/PvdA labelled as opposition on a 2018 vote while they were coalitiepartij). See [FEEDBACK_LOG.md 2026-04-11 "Full session audit"](../../.coordination/FEEDBACK_LOG.md). The failure mode is data-shaped, not instruction-shaped — this benchmark question proves the WS4 primer extension actually works end-to-end.

### Layer 2 — MCP chat replay (the real quality gate)

*(Added 2026-04-12)* The v2 formal eval (completeness 2.75, faithfulness 4.8) measured whether chunks came back and whether text was supported — but it **missed** every failure that actually mattered in production: coalition-at-time framing errors, slot efficiency, scope confusion, missing follow-up depth. Real MCP chat sessions in [FEEDBACK_LOG.md](../../.coordination/FEEDBACK_LOG.md) surfaced those failures far more effectively than any automated metric.

**Principle:** the true quality gate is whether the specific MCP sessions that previously exposed failures now produce qualitatively better results. Abstract LLM-judge scores on random samples do not catch politically misleading output.

Replay these 6 sessions through the live MCP tools **after Phase 1 enrichment**. Each must pass its specific condition. Failure on any red item blocks the WS1 `done` status.

| # | Session | MCP tool sequence | Pass condition | Sev |
|---|---|---|---|---|
| R1 | **Tweebosbuurt 2018 stemming** | `traceer_motie` on the sloop motie | Returns correct vote parties AND identifies GL/PvdA as **coalitiepartij** at time of vote (not opposition). Must use `coalition_history` from `get_neodemos_context`, not guess from training data. | RED |
| R2 | **Warmtebedrijf motie trace** | `traceer_motie` on ≥3 Warmtebedrijf moties (2019-2022) | Returns complete indieners + vote counts + at least 1 DISCUSSED_IN notulen chunk per motie. `trace_available: true`. | RED |
| R3 | **Heemraadssingel parkeren** | `zoek_raadshistorie("Heemraadssingel parkeren")` | Returns ≥3 chunks where BOTH "Heemraadssingel" AND "parkeren" appear in the chunk content (verified via grep). Pre-enrichment baseline: 9 results returned but NONE were on-topic — about parking generally OR about Heemraadssingel cultuurhistorie, not the intersection. The chunk-text gazetteer pass + Flair LOC tagging together close this. | RED |
| R4 | **Partijvergelijking warmtenetten** | `vergelijk_partijen(onderwerp="warmtenetten", partijen=["Leefbaar Rotterdam","GroenLinks-PvdA","VVD"])` | Returns **differentiated** per-party fragments — not the same generic warmtenetten chunks for all three. At least 3 of the 5 fragments per party must mention that party by name. | RED |
| R5 | **Woningbouw 10-jaar research** | Full multi-tool session: `scan_breed` → `zoek_raadshistorie` → `zoek_moties` → `traceer_motie` → `lees_fragment` | Qualitative: graph_walk stream contributes at least 2 chunks that would NOT have appeared via the 4-stream retrieval alone (visible via `stream_type='graph'` tag). | YELLOW |
| R6 | **Haven verduurzaming dossier** | `scan_breed("verduurzaming haven Rotterdam")` → `zoek_uitspraken` | Slot efficiency: ≤2 duplicate `document_id` values in first 8 results (was 4/8 pre-fix). Score floor: no result with `similarity_score < 0.15`. | YELLOW |

**How to run:** use Claude Desktop (or any MCP host) connected to the production NeoDemos MCP server after Phase 1 enrichment + `GRAPH_WALK_ENABLED=1` deploy. Each session is a natural-language conversation that exercises the tool sequence shown. Score pass/fail manually against the condition column. Record results in the **Outcome** section at the bottom of this file.

**VN A/B run (added 2026-04-14):** each of the 6 sessions is run **twice** — once with `INCLUDE_VIRTUAL_NOTULEN=True` (default, max recall) and once with `INCLUDE_VIRTUAL_NOTULEN=False` (provenance-pure). Both scores recorded. Acceptance: the provenance-pure score must be **no more than 0.3 below** the default on any session — if VN is contributing more noise than signal, the delta inverts and VN gets downgraded in the KG write step (re-tag with lower base_quality) instead of patched at query time.

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
| VN-derived edges add noise / mislead (e.g. ASR speaker misattribution → wrong DIENT_IN edge) | Provenance tagging on every edge + `score_paths` VN penalty + A/B eval. Worst case: re-tag VN edges with `source_quality=0` post-hoc to neutralize them without re-running enrichment. |
| `staging.meetings.quality_score` not populated for some VN meetings | Default to 0.5 if NULL — conservative middle ground. Log the count of edges written with a defaulted quality_score; if > 5%, root-cause in WS12. |

## Future work (do NOT do in this workstream)
- **Chunker bug fix:** `_find_best_break` silent hard-cut fallback at [pipeline/ingestion.py:436](../../pipeline/ingestion.py#L436). Re-chunk the 1,204 legacy docs with ≥1 oversized grandchild via `scripts/resplit_oversized_chunks.py` (not yet written): re-split → re-embed in Qdrant → update kg_relationships / kg_mentions FK references. Defer to post-v0.2.0.
- **Motion number normalization:** M2023-042 vs 2023/42 format divergence causes ~30% cross-doc linking failures in `link_motie_to_notulen.py`. Needs a canonical format table. Defer post-v0.2.0.
- **Sibling-context enrichment full incorporation:** if Phase 0.5b A/B calibration shows ≥10% more valid edges → incorporate `--sibling-context` into full run. If it fails the gate → defer to post-v0.2.0.
- Per-municipality KG isolation (out of v0.2 scope; multi-portal deferred to v0.2.1)
- 3+ hop graph walks (capped at 2 in v0.2)
- Active learning loop for entity disambiguation (v0.4+)
- Streaming graph results to the LLM (Anthropic Code Execution work — v0.3)
- Full Netherlands BAG coverage import (only Rotterdam-relevant subset in v0.2.0; the full ~9.5M-address national set lands when WS5b promotes new gemeenten to full mode in v0.3.0+)
- Geographic/spatial queries via PostGIS (BAG provides coordinates, but spatial radius search is out of scope for v0.2)
- **Party-programme-based `haal_partijstandpunt_op` profile seeding** *(decision 2026-04-14)*. The `haal_partijstandpunt_op` scaffold currently returns empty because the profile DB isn't seeded. The "proper" seeding path — ingest 2022 GR verkiezingsprogramma PDFs for each Rotterdamse fractie, extract structured stances per `beleidsgebied` — is **deferred past v0.4**, and may sit at v0.9. Rationale: (a) programmes are 4-year-old strategist copy, register-mismatched with raadszaal behaviour; (b) structured-stance extraction from a 40-page programme is a research-grade problem, not a week's work; (c) the retrieval-based `vergelijk_partijen` (Phase B, this file) already covers the highest-value "who said what + how did they vote" query class. Cheap mitigation in the meantime lives in [WS4 §(4) T6](done/WS4_MCP_DISCIPLINE.md#4-tool-quality-fixes-from-2026-04-14-systematic-testing-added-2026-04-14) — recency-bias the fallback RAG so it stops returning 2015-2017 fragments.
- **`zoek_stemgedrag` + `motie_stemmen` structured voting table** — promoted to v0.2.0 on 2026-04-14 as **[WS15](WS15_MOTIE_STEMMEN.md)** (was v0.3.0 per old MASTER_PLAN WS9). Not a WS1 scope item. KG `STEMT_VOOR/TEGEN` edges from this WS handle qualitative graph walks; WS15 gives the aggregatable SQL layer for "how did D66 vote on restrictive proposals they didn't author." Where they disagree, WS15 is the source of truth (besluitenlijsten beat motie-body party signatories).

## Pipeline integration (added 2026-04-12)

WS2 established the pattern: each workstream ships its processing as an **APScheduler job in `main.py`**, not a server crontab entry. This keeps scheduling in code, version-controlled, and deployed via Kamal.

**What to wire at ship time:**
- [ ] Add an APScheduler job for KG enrichment (Flair NER on new chunks). Pattern:
  ```python
  scheduler.add_job(scheduled_kg_enrichment, IntervalTrigger(hours=6),
                    id='kg_enrichment', max_instances=1, coalesce=True)
  ```
- [ ] The job should find chunks with no `kg_mentions` rows and run Flair NER on them.
- [ ] Use advisory lock 42 to serialize with other pipeline writers.
- [ ] Log to `pipeline_runs` (status: `success`/`failure`, triggered_by: `cron`/`manual`).
- [ ] Log per-document events to `document_events` table (event_type: `kg_enriched`).

**Existing infrastructure to reuse:**
- `services/document_processor.py` — pattern for APScheduler job + logging
- `services/financial_sweep.py` — pattern for find-unprocessed + process + log
- `scripts/nightly/07a_enrich_new_chunks.py` — existing Flair NER logic (wrap into the job)
- `document_events` table — per-document activity log
- `pipeline_runs` table — job-level summary (status constraint: `running/success/failure/skipped`, triggered_by: `cron/manual/smoke_test`)

## Outcome
*To be filled in when shipped. Include: actual edge count, eval scores, surprises, follow-ups.*
