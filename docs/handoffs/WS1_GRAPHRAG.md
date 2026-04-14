# WS1 — GraphRAG Retrieval

> **Priority:** 1 (highest impact, MAAT cannot match)
> **Status (2026-04-14):** Phase 0 `done` · Phase A bis (VN provenance) `done` · Phase 1 prep `done` (scripts hardened, commits `d6e1d58` + `44c87d5`) · Phase 1 execution `blocked` — waiting on WS7 (OCR), WS11 (corpus gaps), WS12 (virtual notulen + `staging.meetings.quality_score`). WS10 (table-rich) is no longer a blocker.
> **Owner:** `unassigned`
> **Target release:** v0.2.0
> **Master plan section:** [V0_2_BEAT_MAAT_PLAN.md §3](../architecture/V0_2_BEAT_MAAT_PLAN.md)
> **Phase naming:** "Phase A" (build tasks) == "Phase 1" (eval gate text). Same thing — Phase A is the build label; Phase 1 is the execution-stage label used in the eval gate.
> **Next-agent quick-start:** jump to [§Phase 1 Execution Runbook](#phase-1-execution-runbook-agent-pickup-point). All code is ready; the runbook is a 10-step command sequence.

## TL;DR
Today we have 57K KG edges, a politician registry, a domain gazetteer, and 3.3M entity-mentions — and we never query any of it at retrieval time. This workstream lights up the graph: enriches it to ~500K edges via Flair NER + Gemini, exposes it via [`services/graph_retrieval.py`](../../services/graph_retrieval.py) (built in Phase 0, 2026-04-12), wires graph traversal as the 5th retrieval stream, and ships two flagship MCP tools (`traceer_motie`, `vergelijk_partijen`) that no Dutch competitor can match. Phase A bis (added 2026-04-14) tags every edge with provenance metadata so virtual notulen (WS12) can be included in the KG without compromising production-grade output quality.

## Dependencies
- **WS7 (OCR recovery)** must complete first — enrichment must operate on clean source text, not garbled OCR
- **WS11 (corpus completeness)** must complete first — no point enriching an incomplete corpus
- **WS12 (virtual notulen)** must complete first AND `staging.meetings.quality_score` must be populated for every VN meeting — WS1's provenance layer depends on it (see Phase A section "VN Provenance Layer" below)
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

- [ ] **[Quick win — do this first] Run [`scripts/enrich_chunks_gazetteer.py`](../../scripts/enrich_chunks_gazetteer.py)** (Phase 0 ✅ — script exists, dry-runnable). Scans chunk-body text against [`data/knowledge_graph/domain_gazetteer.json`](../../data/knowledge_graph/domain_gazetteer.json) (~2,217 entries across 6 lists). Current state: 25.2% `key_entities` coverage on 1,698,930 chunks (per pre-flight baseline 2026-04-12). A chunk like *"bewoners van de Heemraadssingel klagen over parkeerdruk"* in a document titled "Voortgangsrapportage parkeren 2024" currently gets zero `key_entities` tags → invisible to the Qdrant payload filter. This pass closes that gap. Smoke first: `python scripts/enrich_chunks_gazetteer.py --dry-run --limit 1000`. Closes the Heemraadssingel 0-hit failure (see [FEEDBACK_LOG.md 2026-04-11](../../brain/FEEDBACK_LOG.md), [eval/baselines/ws1_pre_enrichment_baseline.md R3](../../eval/baselines/ws1_pre_enrichment_baseline.md)).
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

All three must be `done` per [README](README.md) before firing step 1:

- **WS7** — OCR recovery complete (moties/amendementen clean; otherwise Gemini enriches garbled text and produces garbage edges)
- **WS11** — corpus completeness done (no point enriching a partial corpus)
- **WS12** — virtual notulen ingested AND `staging.meetings.quality_score` populated for every VN meeting (the Phase A bis provenance layer multiplies edge confidence by this score; missing defaults to 0.5 conservative)

The script's `preflight_checks()` will hard-fail if upstream state is wrong, so you cannot accidentally fire on a half-baked corpus.

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
- [ ] Test query: `"Heemraadssingel parkeren"` returns chunks via the new chunk-text-tagged path (was 0 hits in [FEEDBACK_LOG.md 2026-04-11](../../brain/FEEDBACK_LOG.md))
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

Add 10 multi-hop questions to [`eval/data/questions.json`](../../eval/data/questions.json) before measuring completeness. **At least 1 of the 10 must test coalition-status-at-time during historical vote interpretation** *(added 2026-04-11)* — e.g. *"In 2018 stemde de raad over de sloop in de Tweebosbuurt. Welke partijen stemden voor, en waren zij op dat moment coalitie- of oppositiepartij?"* Gold answer requires the system to (a) find the stemming, (b) recognize the date, (c) resolve coalition-at-time via the WS4 `coalition_history` primer field — not guess party roles from training data. Rationale: 2026-04-11 woningbouw session produced exactly this class of framing error (GL/PvdA labelled as opposition on a 2018 vote while they were coalitiepartij). See [FEEDBACK_LOG.md 2026-04-11 "Full session audit"](../../brain/FEEDBACK_LOG.md). The failure mode is data-shaped, not instruction-shaped — this benchmark question proves the WS4 primer extension actually works end-to-end.

### Layer 2 — MCP chat replay (the real quality gate)

*(Added 2026-04-12)* The v2 formal eval (completeness 2.75, faithfulness 4.8) measured whether chunks came back and whether text was supported — but it **missed** every failure that actually mattered in production: coalition-at-time framing errors, slot efficiency, scope confusion, missing follow-up depth. Real MCP chat sessions in [FEEDBACK_LOG.md](../../brain/FEEDBACK_LOG.md) surfaced those failures far more effectively than any automated metric.

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
- Per-municipality KG isolation (out of v0.2 scope; multi-portal deferred to v0.2.1)
- 3+ hop graph walks (capped at 2 in v0.2)
- Active learning loop for entity disambiguation (v0.4+)
- Streaming graph results to the LLM (Anthropic Code Execution work — v0.3)
- Full Netherlands BAG coverage import (only Rotterdam-relevant subset in v0.2.0; the full ~9.5M-address national set lands when WS5b promotes new gemeenten to full mode in v0.3.0+)
- Geographic/spatial queries via PostGIS (BAG provides coordinates, but spatial radius search is out of scope for v0.2)
- **Party-programme-based `haal_partijstandpunt_op` profile seeding** *(decision 2026-04-14)*. The `haal_partijstandpunt_op` scaffold currently returns empty because the profile DB isn't seeded. The "proper" seeding path — ingest 2022 GR verkiezingsprogramma PDFs for each Rotterdamse fractie, extract structured stances per `beleidsgebied` — is **deferred past v0.4**, and may sit at v0.9. Rationale: (a) programmes are 4-year-old strategist copy, register-mismatched with raadszaal behaviour; (b) structured-stance extraction from a 40-page programme is a research-grade problem, not a week's work; (c) the retrieval-based `vergelijk_partijen` (Phase B, this file) already covers the highest-value "who said what + how did they vote" query class. Cheap mitigation in the meantime lives in [WS4 §(4) T6](WS4_MCP_DISCIPLINE.md#4-tool-quality-fixes-from-2026-04-14-systematic-testing-added-2026-04-14) — recency-bias the fallback RAG so it stops returning 2015-2017 fragments.
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
