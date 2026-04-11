# WS5a — 100% Reliable Nightly Ingestion Pipeline

> **Priority:** 5 (data freshness is a primary quality dimension per UK AI Playbook)
> **Status:** `not started`
> **Owner:** `unassigned`
> **Target release:** v0.2.0 (Rotterdam-only)
> **Master plan section:** [V0_2_BEAT_MAAT_PLAN.md §7.1](../architecture/V0_2_BEAT_MAAT_PLAN.md)

## TL;DR
Today: 15-min `refresh_service.check_and_download()` runs in [`main.py:69`](../../main.py#L69), but transcription/chunking/embedding/KG enrichment/promotion are all manual via CLI scripts. Goal: a meeting that ends Tuesday 22:00 is fully indexed with timestamped transcripts by Wednesday 06:00, **automatically, every time, with smoke tests proving it.** This is foundational — without it the v0.2.0 demo is "ship features then go run pipeline.py". Multi-portal expansion (WS5b) is explicitly deferred until WS5a has been clean for 14 days.

## Dependencies
- **None** for v0.2.0 scope. Fully independent and can start day 1.
- **Critical coordination** with WS1, WS2 (any large backfills must go through this workstream's locks)
- **WS5b (multi-portal) is deferred** until this workstream proves 14 days of clean runs
- Memory to read first:
  - [project_embedding_process.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_embedding_process.md) — **the reason advisory locks exist**
  - [project_pipeline_hardening.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_pipeline_hardening.md)
  - [reference_embedding_runbook.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/reference_embedding_runbook.md)

## Cold-start prompt

> You are picking up Workstream 5a (Nightly Ingestion Pipeline) of NeoDemos v0.2.0. Self-contained handoff at `docs/handoffs/WS5a_NIGHTLY_PIPELINE.md`.
>
> Read in order: (1) this handoff, (2) `main.py:69` to see the current 15-min refresh job, (3) `pipeline/ingestion.py`, `pipeline/staging_ingestor.py`, `pipeline/financial_ingestor.py`, (4) `pipeline/extractor.py` and `pipeline/transcript_postprocessor.py` for the transcription chain, (5) `docs/architecture/EMBEDDING_PIPELINE_RUNBOOK.md`, (6) the project memory file `project_embedding_process.md` which explains why advisory locks are non-negotiable.
>
> Your job: build a 7-step idempotent job graph that turns "manual pipeline run" into "automatic nightly run with observability and smoke tests". Every step must be resumable, tracked in a `pipeline_runs` Postgres table, and protected by `pg_advisory_lock(42)` so concurrent search reads are never blocked but concurrent writes are serialized. Ship a `/admin/pipeline` dashboard for at-a-glance health and a daily 07:00 CET email summary. The acceptance bar is **14 consecutive days of clean nightly runs** with zero manual interventions, measured by the smoke test job.
>
> Multi-portal connector work (Notubiz, GO, ORI fallback) is **deferred to WS5b in v0.2.1**. Do NOT scope-creep into multi-portal in this workstream.

## Files to read first
- [`main.py`](../../main.py) — especially line 69 (`scheduled_refresh`) and line 112 (`cleanup_sessions`)
- [`pipeline/ingestion.py`](../../pipeline/ingestion.py) — `SmartIngestor`
- [`pipeline/staging_ingestor.py`](../../pipeline/staging_ingestor.py)
- [`pipeline/financial_ingestor.py`](../../pipeline/financial_ingestor.py)
- [`pipeline/extractor.py`](../../pipeline/extractor.py) — `WhisperTranscriber`, `SpeakerDetector`, `TranscriptAligner`
- [`pipeline/transcript_postprocessor.py`](../../pipeline/transcript_postprocessor.py)
- [`pipeline/scraper.py`](../../pipeline/scraper.py) — Royalcast / iBabs scraping
- [`docs/architecture/EMBEDDING_PIPELINE_RUNBOOK.md`](../architecture/EMBEDDING_PIPELINE_RUNBOOK.md)
- [`scripts/run_financial_batch.py`](../../scripts/run_financial_batch.py), [`scripts/promote_financial_docs.py`](../../scripts/promote_financial_docs.py)

## Build tasks

### Job graph (~3 days)

7 steps, each in its own script under `scripts/nightly/`, each idempotent and resumable:

- [ ] **`scripts/nightly/01_discover_meetings.py`** — poll iBabs (Rotterdam only in v0.2.0) for meetings since last successful run; insert new rows into `meetings` table (skip duplicates by `external_id`); write a `pipeline_runs` row with `step='discover', status='running'`.
- [ ] **`scripts/nightly/02_download_documents.py`** — for each new meeting, fetch PDFs into `downloads/`; dedupe by sha256.
- [ ] **`scripts/nightly/03_download_webcasts.py`** — for committee meetings only, fetch MP4 from Royalcast Company Webcast SDK; idempotent by `meeting_id`.
- [ ] **`scripts/nightly/04_transcribe.py`** — run [`pipeline/extractor.py`](../../pipeline/extractor.py) + [`pipeline/transcript_postprocessor.py`](../../pipeline/transcript_postprocessor.py) on new MP4s; produce timestamped speaker-attributed segments.
- [ ] **`scripts/nightly/05_chunk_and_stage.py`** — chunk new docs/transcripts via existing tiered chunker; embed via Qwen3-8B; write to staging Qdrant collection. **MUST acquire `pg_advisory_lock(42)` before writing.**
- [ ] **`scripts/nightly/06_kg_enrich.py`** — Flair NER + Gemini metadata pass on new chunks only (delta-mode, not full re-run). Coordinates with WS1.
- [ ] **`scripts/nightly/07_promote.py`** — promote staging → production after a per-step eval-pass check (sample N=20 chunks, ensure `_format_chunks_v3` rendering doesn't fail). **MUST acquire `pg_advisory_lock(42)` before writing.**

### State tracking (~1 day)

- [ ] **`pipeline_runs` table** via Alembic:
  ```sql
  CREATE TABLE pipeline_runs (
    id BIGSERIAL PRIMARY KEY,
    job_name TEXT NOT NULL,                -- "01_discover_meetings"
    started_at TIMESTAMPTZ DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL,                  -- 'running' | 'success' | 'failure' | 'skipped'
    items_discovered INT DEFAULT 0,
    items_processed INT DEFAULT 0,
    items_failed INT DEFAULT 0,
    error_message TEXT,
    error_traceback TEXT,
    triggered_by TEXT                      -- 'cron' | 'manual' | 'smoke_test'
  );
  CREATE INDEX ON pipeline_runs (job_name, started_at DESC);
  CREATE INDEX ON pipeline_runs (status, started_at DESC);
  ```
- [ ] **`pipeline_failures` dead-letter** table for items that failed all retries:
  ```sql
  CREATE TABLE pipeline_failures (
    id BIGSERIAL PRIMARY KEY,
    job_name TEXT NOT NULL,
    item_id TEXT NOT NULL,                 -- meeting_id, document_id, etc.
    item_type TEXT NOT NULL,
    failed_at TIMESTAMPTZ DEFAULT NOW(),
    retry_count INT,
    error_class TEXT,
    error_message TEXT,
    raw_payload JSONB
  );
  ```
- [ ] **Resumability**: each step queries `pipeline_runs` for the last successful `finished_at` of its predecessor and processes only newer items. Re-running a failed step picks up where it left off.

### Advisory locks (~0.5 day)

- [ ] **All writer steps wrap their writes in `pg_advisory_lock(42)` ... `pg_advisory_unlock(42)`**. Reads (search queries from the live MCP server) are never blocked.
- [ ] If a previous run is still holding the lock (e.g. crashed without unlock), the new run waits with a 30-min timeout, then aborts and writes a `pipeline_failures` row.
- [ ] Document the lock contract in `pipeline/README.md` and reference it from this handoff and from `project_embedding_process.md`.
- [ ] **Route `EntityNormalizer` through `services/db_pool.py`** *(added 2026-04-11 from QA pass)* — [pipeline/normalization.py:25-32](../../pipeline/normalization.py#L25-L32) `EntityNormalizer._get_connection` opens a raw `psycopg2.connect(self.db_url)` instead of using the shared pool. Every other writer in the codebase goes through [services/db_pool.py](../../services/db_pool.py) — which is what `pg_advisory_lock(42)` discipline relies on to serialize writes. `EntityNormalizer` is called from [pipeline/main_pipeline.py](../../pipeline/main_pipeline.py) and [pipeline/committee_notulen_pipeline.py](../../pipeline/committee_notulen_pipeline.py), both of which will run under this nightly job graph. Refactor: replace the cached `self.connection` pattern with a `get_connection()` context manager per call site in `normalize_speaker` (or, if contention makes per-call checkout too chatty, borrow one connection for the lifetime of a `normalize_segments` batch and return it on exit). Audit all callers to ensure they cooperate with the new lifecycle.

### Failure handling (~1 day)

- [ ] **Retry policy**: each step retries up to 3 times with exponential backoff (1s, 4s, 16s).
- [ ] **Dead-letter queue**: after 3 failures, item moves to `pipeline_failures` table with full traceback.
- [ ] **Slack/email alert** on any `failure` status (use existing Hetzner mailer or systemd `OnFailure=`).

### Orchestration (~1 day)

- [ ] **Cron-style scheduler** — keep this simple. Use systemd timers on Hetzner OR APScheduler in `main.py`. **Do NOT add Airflow.**
- [ ] **Schedule**: nightly at 02:00 CET (after most meetings end). Steps run sequentially: 01 → 02 → 03 → 04 → 05 → 06 → 07.
- [ ] **Hourly smoke test** (`scripts/nightly/00_smoke_test.py`): inject a known-good test document end-to-end through all 7 steps in a `smoke_test_*` collection (separate from production). **Fail the deploy if smoke test fails 3 hours in a row.**

### Observability (~1.5 days)

- [ ] **`/admin/pipeline` page** in [`templates/admin.html`](../../templates/admin.html) (or new `templates/admin_pipeline.html`):
  - Job graph rendered with green/yellow/red per step
  - Last run start/end/duration per step
  - Items processed today, this week, this month
  - Queue depth (`pipeline_failures` count)
  - "Run now" button for manual triggers (scoped to admin OAuth role)
- [ ] **Daily health email** at 07:00 CET to `dennis@neodemos.nl` listing:
  - Yesterday's runs per step
  - Items processed
  - Errors with item IDs
  - Smoke test status for the past 24h
  - Link to `/admin/pipeline`
- [ ] **Prometheus-style metrics** at `/metrics` (optional but cheap): `pipeline_step_duration_seconds`, `pipeline_items_processed_total`, `pipeline_failures_total`

### Webcast schema for citations (~0.5 day)

This part is shared with WS5b (UI is in v0.2.1) but the schema work must happen here:

- [ ] Ensure every transcript chunk in Qdrant has `start_seconds`, `end_seconds`, `webcast_url` in its payload. Check [`pipeline/staging_ingestor.py:12`](../../pipeline/staging_ingestor.py#L12) — `start_date` is currently the only timestamp field.
- [ ] **Backfill script** `scripts/nightly/backfill_webcast_timestamps.py` — populates the new payload fields for existing transcript chunks by re-querying `committee_transcripts_staging`. Must run with advisory lock.

### Data integrity audit — chunk → document_id attribution (~1 day) *(added 2026-04-11, triaged from [FEEDBACK_LOG.md 2026-04-11](../../brain/FEEDBACK_LOG.md))*

**Why this is in WS5a and not WS1 or WS4:** the failure mode is an *ingest-time* attribution bug, not a retrieval bug. A search for parkeertarieven returned `doc 246823` with a snippet showing "Centrum €3.50 / Buiten centrum €2.00", but when `lees_fragment` was called the document turned out to be a GroenLinks kaderbrief about urban development — no parking content at all. The only way this happens is if a chunk's `document_id` does not match the document the chunk text actually came from. That's an ingest pipeline integrity failure. **This is the single most dangerous failure mode in the platform** because it produces confident-looking hallucinations that no LLM can catch without auditing every source manually.

- [ ] **`scripts/audit_chunk_attribution.py`** — reads every chunk in PostgreSQL `chunks` and Qdrant, verifies that the text content substring-matches the referenced document's raw text blob. Outputs `reports/chunk_attribution_audit.csv` with rows: `(chunk_id, document_id, match_type, severity)` where `match_type ∈ {exact, substring, fuzzy, mismatch}`. Target: 100% `exact` or `substring`; zero `mismatch`.
- [ ] **Root cause analysis** — for any `mismatch` row, trace back through the ingest pipeline (which script wrote the chunk, what was the document hash at write time, was there a concurrent write). Likely culprits: stale document cache, hash collision, ordering bug in `pipeline/staging_ingestor.py`.
- [ ] **Regression test** — new test `tests/pipeline/test_chunk_attribution.py` that ingests a known fixture set and asserts zero mismatches. Becomes a permanent part of the smoke test in step `00_smoke_test.py`.
- [ ] **Corrective action** — if mismatches are found, write `scripts/repair_chunk_attribution.py` that either re-attributes (if the source doc is findable by hash) or quarantines (if not) affected chunks. Runs under `pg_advisory_lock(42)`.
- [ ] **Acceptance** — audit run produces zero `mismatch` rows; smoke test asserts the invariant; add `chunk_attribution_audit_passed` to the daily 07:00 CET health email.

**Related FEEDBACK_LOG entry:** [2026-04-11 zoek_raadshistorie / lees_fragment — Parkeertarieven Rotterdam](../../brain/FEEDBACK_LOG.md), specifically the "doc 246823 was a false positive" failure mode.

## Acceptance criteria

- [ ] All 7 nightly scripts exist under `scripts/nightly/` and run end-to-end on a known-good test document
- [ ] `pipeline_runs` and `pipeline_failures` tables created via Alembic
- [ ] All writer steps acquire `pg_advisory_lock(42)`; documented in `pipeline/README.md`
- [ ] Retry + dead-letter behavior verified via fault-injection test
- [ ] Cron schedule running nightly at 02:00 CET on Hetzner production
- [ ] Hourly smoke test job running and reporting to `pipeline_runs`
- [ ] `/admin/pipeline` page live and accurate
- [ ] Daily 07:00 CET email being delivered
- [ ] Webcast `start_seconds`/`end_seconds`/`webcast_url` populated on all transcript Qdrant payloads (backfilled)
- [ ] **Chunk attribution audit run produces zero `mismatch` rows** *(added 2026-04-11)* — `scripts/audit_chunk_attribution.py` clean; regression test in place; invariant part of the smoke test
- [ ] **14 consecutive days of clean nightly runs** with zero manual interventions

## Eval gate

| Metric | Target |
|---|---|
| Consecutive clean nights | **14** |
| Smoke test pass rate | 100% over the 14-day window |
| End-to-end latency (meeting end → searchable) | < 8 hours p95 |
| Lock contention warnings | 0 in the 14-day window |
| Dead-letter queue size | < 5 items at end of 14-day window |

## Risks specific to this workstream

| Risk | Mitigation |
|---|---|
| Embedding-segment corruption (the original reason for `project_embedding_process.md`) | Hard advisory lock contract, reviewed in code review for every writer step |
| Royalcast scraping flaky | Webcast steps (03, 04) are non-blocking; failure does not abort the document pipeline (01, 02, 05, 06, 07 still run) |
| Whisper transcription crashes on long meetings | Chunked transcription with checkpoint resume per `EMBEDDING_PIPELINE_RUNBOOK.md` |
| Gemini cost overrun in step 06 | Delta-mode only (new chunks); hard $5/day cap with circuit breaker |
| Schedule drift if Hetzner reboots | systemd timer with `Persistent=true` |
| Concurrent manual runs collide with nightly | Advisory lock + check `triggered_by='manual'` rows in `/admin/pipeline` warning before triggering |
| Smoke test masks real failures (false green) | Use a different test document weekly; rotate via `data/smoke_tests/` directory |

## Future work (do NOT do in this workstream)
- Multi-portal connectors (Notubiz, GO, ORI fallback) — **WS5b in v0.2.1**
- HLS webcast player UI — **WS5b in v0.2.1**
- Multi-tenancy schema migration — **WS5b in v0.2.1**
- Real-time ingestion (sub-hour latency) — defer to v0.4+
- ML-based anomaly detection on pipeline metrics — defer to v0.3+

## Outcome
*To be filled in when shipped. Include: actual end-to-end latency, hardest failure mode encountered, lock contention observations, smoke test rotation strategy.*
