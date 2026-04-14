# WS6 — Source-Spans-Only Summarization (NeoDemos Analyse v2)

> **Priority:** 8 (the GenAI feature MAAT advertises; we can beat it because we have the retrieval)
> **Status:** `in progress` — Phase 3 DB write running (~4K/25.5K at 2026-04-14 08:30); code complete; MCP tool + UI badges pending
> **Owner:** Claude Code (WS6 agent)
> **Target release:** v0.2.0 (per-document summaries, source-spans verifier); v0.3.0 (theme maps, multi-round)
> **Master plan section:** [V0_2_BEAT_MAAT_PLAN.md §8](../architecture/V0_2_BEAT_MAAT_PLAN.md)

## TL;DR
We already have stronger retrieval than MAAT (BM25 + dense + rerank + 4 streams) and a partially-built `analyse` feature in [`services/ai_service.py:47`](../../services/ai_service.py#L47), [`services/synthesis.py`](../../services/synthesis.py), [`services/decomposition.py`](../../services/decomposition.py). What's missing is **discipline**: every sentence in a generated summary must map to a retrieved chunk, or it gets stripped. Stanford's 2025 legal RAG study showed top commercial systems hallucinate at 17% even with citations — defense is source-spans-only verification, not just citations. UK i.AI's [ThemeFinder](https://github.com/i-dot-ai/themefinder) sets the F1 = 0.79–0.82 government bar for theme extraction; we'll match it in v0.3.0. v0.2.0 ships the verifier, the cached per-document summaries, and one new MCP tool.

## Dependencies
- **None** for v0.2.0 minimum.
- WS1 GraphRAG enables `mode='structured'` multi-round retrieval (deferred to v0.3.0)
- Memory to read first: none essential

## Cold-start prompt

> You are picking up Workstream 6 (Source-Spans-Only Summarization) of NeoDemos v0.2.0. Self-contained handoff at `docs/handoffs/WS6_SUMMARIZATION.md`.
>
> Read in order: (1) this handoff, (2) `services/ai_service.py` `analyze_agenda_item` around line 47, (3) `services/synthesis.py` Map-Reduce synthesizer, (4) `services/decomposition.py` sub-query decomposition, (5) `services/policy_lens_evaluation_service.py`, (6) `mcp_server_v3.py` `analyseer_agendapunt` around line 721, (7) `main.py` routes `/api/analyse/agenda/{id}` and `/api/analyse/party-lens/{id}`.
>
> Your job: consolidate the existing scattered analysis paths into a single `services/summarizer.py` module, enforce a **source-spans-only verification pass** (every sentence must map to a retrieved chunk via reranker score; sentences below threshold get stripped), pre-compute and cache `summary_short` + `summary_long` + `themes` for every promoted document, and ship one new MCP tool `vat_document_samen`. Theme maps (ThemeFinder pattern) and multi-round retrieval are **deferred to v0.3.0** — do NOT build them now.
>
> The trust contract: a generated summary either passes the source-span check (✅ verified badge in UI) or it's marked partial (⚠️). No silent hallucinations. Honor the house rules in `docs/handoffs/README.md`.

## Files to read first
- [`services/ai_service.py`](../../services/ai_service.py) — especially `analyze_agenda_item` around line 47 (Gemini Flash 3 path + heuristic fallback)
- [`services/synthesis.py`](../../services/synthesis.py) — Map-Reduce synthesizer (parallel Gemini for summaries, Sonnet for reduction)
- [`services/decomposition.py`](../../services/decomposition.py) — Haiku sub-query splitter
- [`services/policy_lens_evaluation_service.py`](../../services/policy_lens_evaluation_service.py)
- [`mcp_server_v3.py`](../../mcp_server_v3.py) — `analyseer_agendapunt` around line 721
- [`main.py`](../../main.py) — `/api/analyse/agenda/{id}` and `/api/analyse/party-lens/{id}` routes around line 795 / 858
- External:
  - [Stanford legal RAG hallucination study (2025)](https://dho.stanford.edu/wp-content/uploads/Legal_RAG_Hallucinations.pdf) — *why* source-spans-only matters
  - [UK i.AI ThemeFinder](https://github.com/i-dot-ai/themefinder) — F1 0.79–0.82 government bar (for v0.3.0 reference)

## Build tasks

### Consolidation (~2 days)

- [x] **`services/summarizer.py`** — new module that becomes the single entrypoint for all summarization. Replace ad-hoc paths in `synthesis.py` and `ai_service.py`.
  ```python
  class Summarizer:
      def summarize(
          self,
          chunks: list[Chunk],
          mode: Literal['short', 'long', 'themes', 'structured', 'comparison'],
          max_tokens: int = 1500,
          enforce_source_spans: bool = True,
      ) -> SummaryResult: ...

  @dataclass
  class SummaryResult:
      text: str                          # the (verified) summary
      sentences: list[VerifiedSentence]  # each sentence with citation chain
      verified: bool                     # all sentences passed
      stripped_count: int                # sentences removed by verifier
      sources: list[Citation]
      mode: str
      latency_ms: int
  ```
- [x] **For v0.2.0, implement `mode='short'` and `mode='long'` only.** Themes and structured modes are v0.3.0.
- [ ] Migrate `analyze_agenda_item` to call `Summarizer.summarize` internally; keep the old function as a thin wrapper for backward compat with [`main.py:795`](../../main.py#L795)
- [ ] Migrate `synthesize` and `decompose` paths similarly

### Source-spans-only verifier (~2 days)

This is the heart of the workstream.

- [x] **`services/source_span_verifier.py`** — given a generated summary and the chunks it claims to summarize:
  1. Sentence-tokenize the summary (use `nltk` or simple Dutch period-splitting)
  2. For each sentence, run it through the existing Jina v3 reranker against all source chunks
  3. If best chunk score < threshold (default 0.4), **strip the sentence**
  4. If best chunk score ≥ threshold, attach `citation: chunk.id` to the sentence
  5. Return `VerifiedSentence(text, citation, rerank_score)` list
- [ ] **Sentence-stripping rebuilds the summary** without the rejected sentences; if > 30% stripped, mark whole summary `verified: False`
- [ ] **Threshold tuning** — calibrate against the existing `rag_evaluator` benchmark; pick the threshold that maximizes faithfulness without losing > 5% of completeness

### Per-document cached summaries (~1.5 days)

- [x] **Alembic migration** adding `summary_short`, `summary_long`, `summary_themes`, `summary_computed_at`, `summary_verified` columns to `documents`:
  ```sql
  ALTER TABLE documents
    ADD COLUMN summary_short TEXT,
    ADD COLUMN summary_long TEXT,
    ADD COLUMN summary_themes JSONB,
    ADD COLUMN summary_computed_at TIMESTAMPTZ,
    ADD COLUMN summary_verified BOOLEAN;
  CREATE INDEX ON documents (summary_computed_at) WHERE summary_short IS NULL;
  ```
- [x] **`scripts/nightly/06b_compute_summaries.py`** — run as part of WS5a nightly, after step 06 (KG enrich), before step 07 (promote)
  - For each newly-promoted document where `summary_short IS NULL`, compute it via `Summarizer.summarize(mode='short')` and cache
  - Skip if document length < 500 chars (no point summarizing)
- [ ] **Backfill script** for existing documents — Phase 1+2 complete (25,500 JSONL results), Phase 3 DB write pending

### MCP tool (~1 day)

- [ ] **`vat_document_samen(document_id: str, mode: Literal['short', 'long'] = 'short') -> dict`** in [`mcp_server_v3.py`](../../mcp_server_v3.py)
  - Returns:
    ```json
    {
      "document_id": "...",
      "mode": "short",
      "text": "...",
      "verified": true,
      "stripped_count": 0,
      "citations": ["chunk_id_1", "chunk_id_2"],
      "computed_at": "..."
    }
    ```
  - Serves from `documents.summary_short` cache when available; computes on demand otherwise
  - Tool description for AI: "Use this when the user asks for a summary, TL;DR, or overview of a specific document. Do NOT use this for synthesis across multiple documents — for that, retrieve chunks first via `zoek_raadshistorie` and let the host LLM synthesize."
- [ ] Register in WS4 tool registry

### UI verification badge (~0.5 day)

- [ ] In [`templates/search.html`](../../templates/search.html) (and any document detail template), every displayed summary shows:
  - `✅ Geverifieerd` (green) if `verified == true`
  - `⚠️ Gedeeltelijk` (yellow) if `verified == false` with tooltip: "{stripped_count} zinnen verwijderd omdat ze niet direct uit het brondocument konden worden onderbouwd"
- [ ] Click the badge → modal showing the original LLM output and the stripped sentences (transparency)

### Validation (~1 day)

- [ ] **50-document strip test** — pick 50 random documents, generate `mode='short'` summaries, run through verifier. Acceptance: 100% of `verified=true` summaries have all sentences mapping to chunks (audit by hand on 5 random samples).
- [ ] **Faithfulness regression test** — run the existing `rag_evaluator` benchmark with the new `Summarizer` path and confirm faithfulness ≥ 4.5 (no regression).

## Acceptance criteria

- [x] `services/summarizer.py` exists with `Summarizer.summarize` and `SummaryResult` dataclass
- [x] `services/source_span_verifier.py` exists and is wired into `Summarizer`
- [x] `mode='short'` working (v0.2.0 ship); `mode='long'` code complete but **bulk backfill deferred to ~v1.0** (on-demand via MCP fills cache lazily)
- [x] `documents` table has cached summary columns (Alembic migration applied)
- [x] `scripts/nightly/06b_compute_summaries.py` runs as part of WS5a nightly
- [ ] Backfill complete for all existing Rotterdam documents — **Phase 3 DB write pending**
- [ ] `vat_document_samen` MCP tool live and registered with WS4 registry
- [ ] UI shows verification badges on every summary
- [ ] 50-document strip test passes (100% of verified summaries hand-validated on 5 samples)
- [ ] No regression on existing `rag_evaluator` faithfulness (≥ 4.5)
- [ ] Old paths in `synthesis.py` and `ai_service.py` migrated to call `Summarizer` internally

## Eval gate

| Metric | Target |
|---|---|
| Source-spans verification on `mode='short'` summaries | 100% of `verified=true` sentences map to a chunk (5-sample hand audit) |
| Faithfulness on rag_evaluator benchmark | ≥ 4.5 (no regression from 4.8 baseline) |
| Cached summary serve latency p50 | < 50ms |
| On-demand summary p95 latency | < 8s |
| Strip-test on 50 random documents | All summaries either `verified=true` or `verified=false` with `stripped_count > 0` (no silent failures) |

## Risks specific to this workstream

| Risk | Mitigation |
|---|---|
| Verifier strips too aggressively → empty summaries | Calibrate threshold against eval benchmark; fall back to `mode='long'` with lower threshold; never return empty — return original LLM output marked `verified=false` |
| Sentence tokenization wrong on Dutch (compound words, abbreviations) | Use proven library (`nltk` Dutch punkt or `spacy` `nl_core_news_lg`); test on 100 sentences before relying on it |
| Cached summaries become stale when document re-ingested | `06b_compute_summaries.py` re-computes any document where `documents.updated_at > summary_computed_at` |
| Cost runs away on backfill | Hard $5/day cap on Gemini Flash; estimate ~$30 total for Rotterdam backfill |
| Old `analyseer_agendapunt` callers break | Wrapper preserves the old signature; version-bump the response shape only |
| LLM ignores the source-spans constraint and hallucinates anyway | The verifier strips post-hoc — it doesn't trust the LLM. That's the entire point. |

## Future work (do NOT do in this workstream)
- **`mode='long'` bulk backfill — deferred to ~v1.0.** Code path exists (`services/summarizer.py:396` MapReduce). On-demand compute via `vat_document_samen(mode='long')` populates cache lazily. KG build (WS1) + press moment take priority over pre-computing long summaries for docs nobody reads.
- `mode='themes'` per-question theme maps (ThemeFinder pattern, F1 ≥ 0.75 target) — **v0.3.0**
- `mode='structured'` multi-round retrieval (ACL legal-RAG pattern) — **v0.3.0**, depends on WS1
- `mode='comparison'` cross-document side-by-side — **v0.3.0**
- `vat_dossier_samen` for dossier-scoped Q&A — needs dossier feature, defer
- ThemeFinder F1 evaluation against hand-labeled debates — **v0.3.0** with public scoreboard

## Pipeline integration (added 2026-04-12)

WS2 established the pattern: each workstream ships its processing as an **APScheduler job in `main.py`**, not a server crontab entry.

**What to wire at ship time:**
- [x] APScheduler job `scheduled_summarization` in [`main.py`](../../main.py) — 12h interval, processes up to 20 `summary_short IS NULL` docs per firing via real-time `Summarizer.summarize(mode='short')`.
- [x] Advisory lock `7_640_601` (shared with `06b_compute_summaries.py`) on a dedicated connection — scheduled job skips cleanly while a manual backfill is running.
- [x] The `api_summarize` endpoint already exists in [`routes/api.py:367`](../../routes/api.py#L367) for on-demand requests.
- [ ] Optional: log to `pipeline_runs` + `document_events` (event_type: `summary_computed`) — deferred to WS5a wrap.

**Existing infrastructure to reuse:**
- `services/document_processor.py` — APScheduler job pattern
- `scripts/nightly/06b_compute_summaries.py` — existing summarization logic (wrap into the job)
- `pipeline_runs` table — status constraint: `running/success/failure/skipped`, triggered_by: `cron/manual/smoke_test`

## Execution log (2026-04-12 → 2026-04-13)

### What was built

| Component | Status | Notes |
|-----------|--------|-------|
| `services/summarizer.py` | Done | Four-tier: skip / excerpt / direct / extract. Gemini Flash for LLM tier. |
| `services/source_span_verifier.py` | Done | Jina v3 reranker, threshold 0.4, sentence-level strip + citation |
| `services/chunk_selector.py` | Done | MMR-based diverse chunk selection (λ=0.6, jaccard redundancy) |
| `services/storage_ws6.py` | Done | Bulk chunk fetch, summary cache R/W, `list_documents_needing_summary` |
| `scripts/nightly/06b_compute_summaries.py` | Done | 3-phase pipeline: classify → Gemini batch → verify+write. `--replay-from` flag. |
| `scripts/ws6_save_completed_jobs.py` | Done | Incremental Gemini batch result saver (crash-safe JSONL checkpoint) |
| Alembic migration (summary columns) | Applied | `summary_short`, `summary_long`, `summary_verified`, `summary_computed_at` |
| MCP tool `vat_document_samen` | Not yet | Blocked on backfill DB write (Phase 3) |
| UI verification badges | Not yet | Blocked on MCP tool |

### Backfill execution

**Phase 1 — Classify + excerpt** (completed 2026-04-12, ~2.5h)
- 86,217 documents processed with `ThreadPoolExecutor(workers=8)`
- Bulk chunk fetch via `get_chunks_bulk()` (200-doc batches, single SQL per batch)
- Tiers: ~27K skip, ~5K excerpt (verbatim), ~53K LLM-tier (direct + extract)
- Performance: ~70h sequential → ~2.5h parallel

**Phase 2 — Gemini Batch API** (completed 2026-04-13 ~23:20)
- 53,847 prompts submitted as 17 sub-batches of ~1,500 each
- All 17/17 jobs **SUCCEEDED**
- **25,500 results** checkpointed to `logs/ws6_results_8completed.jsonl`
- Gemini Batch API = 50% cost discount vs. real-time
- Total estimated Gemini cost: ~$25–30

**Phase 3 — Verify + DB write** (pending)
- First attempt failed silently: Hetzner disk-space outage → SSH tunnel drop → psycopg2 stale connections (rowcount=0, no exception)
- Fix: JSONL checkpoint written before any DB writes; `--replay-from` flag replays Phase 3 from checkpoint
- Ready to execute:
  ```bash
  python scripts/nightly/06b_compute_summaries.py \
    --replay-from logs/ws6_results_8completed.jsonl \
    --workers 8 --force
  ```

### Key incidents

| Incident | Root cause | Fix |
|----------|-----------|-----|
| 4 zombie processes hammering Jina → 429s | Multiple `kill`/restart left orphans | `kill` all PIDs + `pg_terminate_backend` on stale advisory lock |
| Jina 429 token rate limit (2M TPM) | 4 instances × 8 workers × large chunks | `Semaphore(2)` + `MAX_RERANK_CHUNKS=100` stride-sampling |
| Phase 3 wrote 0 rows despite "success" logs | Hetzner outage → stale DB pool connections | JSONL checkpoint + `--replay-from` flag |
| 25,500 not 53,847 results from Gemini | Server crash mid-Phase 2 interrupted batch submission | Only 17 of planned batches submitted before crash; all 17 completed successfully |

### Architecture decisions

- **Jina concurrency cap**: `threading.Semaphore(2)` in `chunk_selector.py` — Jina v3 paid tier is 2M tokens/min; more than 2 concurrent calls on extract-tier docs bursts past it.
- **Chunk cap per reranker call**: `MAX_RERANK_CHUNKS=100` with stride-sampling — keeps individual Jina calls under token budget while preserving document coverage.
- **JSONL checkpoint before DB writes**: Raw Gemini output saved to disk before any verification or DB write. Crash-safe: `--replay-from` re-runs Phase 3 without re-submitting to Gemini.
- **WS7 integration**: `WHERE ocr_quality IS NULL OR ocr_quality != 'bad'` — excludes OCR-damaged documents from summarization.

## Outcome
*To be completed after Phase 3 replay. Include: chosen rerank threshold, strip rate distribution, faithfulness delta, final backfill cost.*
