# WS5a — 100% Reliable Nightly Ingestion Pipeline

> **Priority:** 5 (data freshness is a primary quality dimension per UK AI Playbook)
> **Status:** `not started`
> **Owner:** `unassigned`
> **Target release:** v0.2.0 (Rotterdam-only)
> **Master plan section:** [V0_2_BEAT_MAAT_PLAN.md §7.1](../architecture/V0_2_BEAT_MAAT_PLAN.md)

## TL;DR
Today: 15-min `refresh_service.check_and_download()` runs in [`main.py:69`](../../main.py#L69), but transcription/chunking/embedding/KG enrichment/promotion are all manual via CLI scripts. Goal: a meeting that ends Tuesday 22:00 is fully indexed with timestamped transcripts by Wednesday 06:00, **automatically, every time, with smoke tests proving it.** This is foundational — without it the v0.2.0 demo is "ship features then go run pipeline.py". Multi-portal expansion (WS5b) is explicitly deferred until WS5a has been clean for 14 days.

## Phased execution (2026-04-14; updated 2026-04-15)

WS5a is being shipped in two phases. Phase A was constrained by two concurrent streams; one is now resolved.

- **WS11 (DONE 2026-04-15, commit `2e5ea58`+`167dad6`)** — Phase 6 backfill complete, Phase 7 deployed (`DOCUMENT_PROCESSOR_PHASE2_ENABLED=true` live in prod), Phase 8 CI guard shipped, handoff archived. Phase B embedding-path writes are now safe.
- **WS6 (still in progress)** — `06b_compute_summaries.py` Run 3 has sub-batches PENDING on Google Gemini. Writes `documents.summary_*` under non-blocking advisory lock `7_640_601`. Does not collide with Phase B (different column set; non-blocking lock).

### Phase A — ship now (safe while WS6 / WS11 in flight)

No writer contention with the two live streams. Read-only, isolated namespaces, or pure code refactors.

1. **Chunk attribution audit (read-only)** — `scripts/audit_chunk_attribution.py`. Motivated by the doc 246823 false positive in [FEEDBACK_LOG.md 2026-04-11](../../.coordination/FEEDBACK_LOG.md).
2. **EntityNormalizer pool refactor** — `pipeline/normalization.py:32-39` currently opens raw `psycopg2.connect`, bypassing `services/db_pool.py` and breaking the lock-42 contract. Code merge only; does not run ingestion.
3. **`/admin/pipeline` dashboard** — new route + template reading `pipeline_runs` / `pipeline_failures` / `document_events`. No writes. "Run now" button deferred to Phase B.
4. **Daily 07:00 CET health email** — new APScheduler job reusing existing `services/email_service.py`.
5. **Hourly smoke test** — `scripts/nightly/00_smoke_test.py` on a separate `smoke_test_*` Qdrant collection, isolated from production.
6. **Advisory lock docs** — `pipeline/README.md` registry for lock `42` and `7_640_601`.

### Phase B — pre-execution risk audit (2026-04-15)

Hard-data audit of B.1–B.6 surfaced findings that invalidate the original "~8h" estimate. Revised total effort is **~45–65 hours** after applying WS11's self-healing pipeline primitives. Full audit summary below.

#### Cross-cutting principle from WS11 — prefer pipeline self-heal over bespoke re-embed

WS11 proved three primitives that make re-embedding unnecessary for most repair paths:

1. **Point-ID swap** (`scripts/rekey_vn_points.py` pattern): zero re-embed — change Qdrant IDs + `embedded_at = NOW()`. Used for 7,026 VN points.
2. **Clear chunks → let scheduler re-ingest**: `DELETE FROM document_chunks WHERE document_id IN (...)` triggers `document_processor` Phase 1 (re-chunk from `documents.content`) then Phase 2 (embed only rows where `embedded_at IS NULL`). Used for 8 Phase 9b restored docs.
3. **`set_payload` on Qdrant**: add/modify payload fields without touching vectors — the API is vector-preserving.

**Every Phase B task should be re-scoped around "which of these primitives solves my problem" before writing new embedding code.** Re-embed only when the vector itself must change.

#### Prerequisite fixes (must ship BEFORE any Phase B item)

| Prereq | What | Effort | Why it blocks Phase B |
|---|---|---|---|
| **PR-0 Lock-42 retrofit** | Add `pg_advisory_lock(42)` to [services/document_processor.py](../../services/document_processor.py) embed/upsert block, and to [scripts/promote_committee_notulen.py](../../scripts/promote_committee_notulen.py) + [scripts/promote_financial_docs.py](../../scripts/promote_financial_docs.py) | 0.5d | All three are **active prod writers that bypass the lock-42 contract** we documented in Phase A. Any Phase B writer will race them. This is an independent bug the audit surfaced. |
| **PR-1 Hetzner disk + Whisper path spike** | `df -h` shows 15Gi free (94% used). Dockerfile has no `ffmpeg`. `requirements.txt` has no `mlx-whisper` (and MLX is Apple-Silicon only — prod is AMD EPYC). Decide: Mac-cron / Hetzner-faster-whisper / cloud Whisper | 1d (spike) | B.5 (step 04 transcription) cannot run on Hetzner as written. This decision changes B.5 effort by ±14h and changes deployment surface. |
| **PR-2 `pipeline_runs` DAG columns** | Alembic migration: add `parent_run_id BIGINT REFERENCES pipeline_runs(id)` + `step_index INT` | 2h | B.4 needs DAG state to represent "step 04 of run 2026-04-15". Today `pipeline_runs` is flat; there is no way to express a 7-step sequence. |
| **PR-3 `document_chunks.attribution_status`** *(optional — may be unnecessary)* | Alembic migration: add `attribution_status TEXT DEFAULT 'valid'` + backfill | 2h | Only needed if B.1 repair wants a soft-delete/quarantine state. With the WS11 self-heal primitive (delete chunks → let scheduler re-chunk), no column is needed. Reassess after B.1 design. |

#### Revised Phase B effort & findings

| Item | Original est. | **Hardened est.** | Key audit finding |
|---|---|---|---|
| **B.1** repair chunk attribution | 1h | **4–6h** *(revised down after WS11 self-heal pattern)* | Real mismatch rate ~0.05% (~850 chunks corpus-wide). "Re-attribute by hash" is impossible (no content-hash column), but the WS11 self-heal primitive makes this a non-issue: `DELETE FROM document_chunks WHERE document_id IN (affected_docs)` → `document_processor` Phase 1 re-chunks from current `documents.content` → Phase 2 embeds only the cleared subset on next 20-min cycle. Scheme A guarantees Qdrant orphans get swept by `cleanup_safe_orphans.py`. ~100-line script, not a new ingest engine. All 3 sampled fuzzy chunks are "document rewritten after chunking" (OCR recovery artifacts), not attribution bugs — fuzzy stays warning-only. |
| **B.2** webcast payload backfill | 1h | **1.5–2d** | Handoff refers to `committee_transcripts_staging` which **is a Qdrant collection, not a Postgres table**. Ground truth for timestamps lives in `output/transcripts/*.json` (263 files) on Dennis's laptop — **not yet on Hetzner**. Per-chunk timestamps are **unrecoverable** (chunker flattens segments); only **agenda-item-level** `start_seconds`/`end_seconds` is achievable. `webcast_url` deep-link format across 2022–2026 is unverified. Uses Qdrant `set_payload` (vector-preserving per WS11 primitive #3) — zero re-embed cost. |
| **B.3** step 07 promote + eval | 1h | **10–14h** | Two existing promote scripts use **different Qdrant point-ID schemes** (Scheme A vs `md5(...)[:15]`). Neither takes lock 42. Neither writes to `pipeline_runs`. `_format_chunks_v3` is at [mcp_server_v3.py:403](../../mcp_server_v3.py#L403); the N=20 sample is a handoff assertion with no statistical basis — needs stratified sample by `chunk_type × doc_classification`. **There is NO staging → production gap for standard docs today** — `document_processor` writes straight to prod. Step 07's scope overlaps, not replaces. |
| **B.4** 7-step orchestration at 02:00 CET | 2h | **18–24h** | APScheduler has **no DAG/dependency primitive** — sequencing must be hand-rolled. **02:00 is the worst possible slot**: multiple of 15/20/60/720-min → every existing interval job fires simultaneously. `BackgroundScheduler()` defaults to container UTC (no timezone set). Dual cron surface (APScheduler + systemd in `scripts/nightly/CRONTAB.md`) — no single authority. Requires PR-2 DAG migration. |
| **B.5** steps 03/04 webcast + Whisper | 1–2h | **25–39h** | **HARD BLOCKER: prod Docker has no ffmpeg, no Whisper, AMD EPYC can't run MLX.** Backlog is **496 of 607** committee meetings missing transcripts (82% gap). Each 2h MP4 is 500MB–1.5GB; prod has 15Gi free — 20 downloads could brick prod. Royalcast scraper has **zero tests** + 3 fallback strategies for page structure drift. `TranscriptPostProcessor` has no hallucination guard (reputational risk: wrong speaker → wrong party label in RAG answers). 169 of 607 meetings lack `ibabs_url` (scraper can't start). |
| **B.6** "Run now" button | 0.5h | **~10h** | Needs POST with CSRF (not GET), `.modify(next_run_time=now())` not `.func()` (which would block the HTTP handler for minutes), pre-flight status check (else clicks silently no-op on `max_instances=1`), audit log write. Cron-style scripts (`06b_compute_summaries.py`, `07a_enrich_new_chunks.py`) are **not APScheduler jobs** — need `subprocess.Popen` path or must be excluded from the button. |
| **B.7** 15-min raadsperiode 2026-2030 discovery gap *(added 2026-04-15 from Erik feedback)* | — | **6–10h** | Empty-shell meetings in Postgres (`f9b8b1c0-0073-4528-96cb-c78e3f9aafd8` = 16 april raadsvergadering) + stadsberaad fully missing (`ae86588c-da48-47e1-ac6a-fc0d183f5273` = BWB 15 april). Belongs to the existing 15-min `scheduled_refresh` path, **not** the 02:00 nightly orchestration — agenda PDFs land mid-day and the press-moment test (tomorrow's raadsvergadering) happens before any nightly fires. See dedicated section below. |

#### Top 5 risks (ranked by severity)

1. **Hetzner cannot run Whisper today.** No ffmpeg in Dockerfile, no `mlx-whisper` in prod `requirements.txt`, MLX won't run on AMD x86 anyway. Needs decision: Mac-cron-semi-nightly / faster-whisper Docker layer (~800MB) / cloud Whisper (~€437 for 496-meeting backlog).
2. **Hetzner disk at 94% full (15Gi free).** No disk-quota guard in `MediaProcessor`. A single night's MP4 download could fill the disk and brick the service.
3. **Lock-42 contract is aspirational, not enforced.** `services/document_processor.py` (primary prod writer, 20-min cycle) bypasses lock 42 despite our Phase A docs declaring it mandatory. This is a latent data-corruption hazard whenever two writers race.
4. **Audit data discrepancy.** Phase A 2000-sample suggested 49.5% fuzzy; 20000-sample shows 22.04%. Either the initial sample was biased (low chunk_ids concentrated in one doc) or chunker behavior is region-dependent. Either way, "fuzzy" is a noisy signal and the qa_digest RED-threshold at >40% is currently firing on real corpus state, not regression.
5. **Hallucination surface is unguarded.** `TranscriptPostProcessor` rewrites names via Gemini without round-trip verification. A wrong speaker → wrong party label propagates into RAG answers. Phase A built attribution audit for chunks; transcripts have no equivalent gate.

#### Revised Phase B build order (after prereqs ship)

Now risk-ordered. Each item assumes PR-0 + PR-2 have shipped (PR-3 optional; PR-1 only blocks B.5):

1. **B.7 15-min raadsperiode 2026-2030 discovery gap** (6–10h) — user-reported 2026-04-15; breaks tomorrow's raadsvergadering prep. Ships first because it's the only Phase B item without a prereq (hooks into the existing 15-min `scheduled_refresh` path, no lock-42 or DAG work needed).
2. **B.1 repair chunk attribution** (4–6h) — 100-line script: clear chunks for ~100–300 affected docs; let `document_processor` self-heal via WS11 primitive #2. Closes the integrity loop surfaced in Phase A at trivial cost.
3. **B.6 Run-now button** (10h) — low risk, immediate operational value
4. **B.3 step 07 promote hardening** (10–14h) — extract shared `promote_lib.py` from the two existing scripts, add lock 42 + `pipeline_runs` + dry-run + rollback-via-stale-delete
5. **B.2 webcast payload backfill** (1.5–2d) — agenda-item-level only; uses `set_payload` (WS11 primitive #3, zero re-embed); sync `output/transcripts/*.json` to prod first
6. **B.4 7-step orchestration** (18–24h) — after all individual steps are hardened; pick a non-collision slot (NOT 02:00 — try 02:07 or 03:15)
7. **B.5 steps 03/04** (25–39h) — last because it's the biggest + depends on PR-1 spike outcome. Consider splitting: "backlog catchup as one-shot Mac-cron job" vs "nightly incremental on Hetzner".

#### B.7 — 15-min raadsperiode 2026-2030 discovery gap *(added 2026-04-15 from Erik feedback)*

**Why 15-min cadence, not nightly 02:00:** Agenda PDFs for Rotterdam raadsvergaderingen land **mid-day on the day before** — Erik was prepping for the 16 april raadsvergadering at 12:40 on 15 april and the agenda was already published on iBabs but absent from NeoDemos. A nightly 02:00 run would have delivered it ~10h too late for afternoon prep sessions and misses the "check between meetings" workflow entirely. The existing [main.py:37 `scheduled_refresh`](../../main.py#L37) → [services/refresh_service.py:40 `check_and_download`](../../services/refresh_service.py#L40) **already** implements history + calendar sweep on the right cadence; B.7 fixes the two parsers it depends on.

**Two concrete gaps:**

1. **`ibabs_service.get_meetings_for_year` misses stadsberaad / alternate agendatypes.** [services/ibabs_service.py:25](../../services/ibabs_service.py#L25) hard-codes `agendatype_id="100002367"`. The BWB stadsberaad `ae86588c-da48-47e1-ac6a-fc0d183f5273` on 15 april is **not in Postgres at all** — never discovered. iBabs exposes multiple `agendatypeId` values (raad, commissies, stadsberaad, themabijeenkomst, werkbezoek); we only poll one. Fix: enumerate agendatype IDs via the portal's agendatype-list endpoint on startup (cache for 24h), then fan out `get_meetings_for_year` across all of them. **Don't** hard-code a new list — raadsperiode 2026-2030 may add new types.

2. **`ibabs_service.get_meeting_agenda` returns empty for UUID-format 2026-2030 meetings.** The 16 april raadsvergadering `f9b8b1c0-0073-4528-96cb-c78e3f9aafd8` **is** in Postgres (Phase 2 calendar sweep inserted the meeting row) but has 0 `agenda_items` and 0 `documents`. Phase 2 of `check_and_download` at [services/refresh_service.py:108](../../services/refresh_service.py#L108) calls `ibabs_service.get_meeting_agenda(meeting['id'])` and gets an empty dict. The new raadsperiode portal template has diverged from the old one — [services/ibabs_service.py:_parse_agenda_page](../../services/ibabs_service.py) selectors miss the new DOM. Fix: snapshot the new-period agenda page HTML as a fixture, build a parser variant, add a detection branch (template version signal in `<meta>` or class name). Write regression fixtures for **both** old-numeric and new-UUID page shapes.

**Files to change (tight blast radius):**
- [services/ibabs_service.py](../../services/ibabs_service.py) — agendatype enumeration + agenda parser v2
- [services/refresh_service.py](../../services/refresh_service.py) — no structural change, but verify Phase 1 history sweep also uses the iBabs path when ORI returns empty for UUID IDs (currently only Phase 2 has that fallback)
- [tests/services/test_ibabs_service.py](../../tests/services/) — new regression fixtures for both page shapes

**Acceptance (B.7 alone):**
- [ ] Within one 15-min `scheduled_refresh` cycle after fix deploys, `meetings` has rows for stadsberaad `ae86588c-...` and full `agenda_items` + `documents` for raadsvergadering `f9b8b1c0-...`
- [ ] [/calendar](../../routes/pages.py) surfaces both meetings with agenda preview
- [ ] MCP `haal_vergadering_op` returns non-empty agenda for `f9b8b1c0-...`
- [ ] Fixture tests for both numeric-ID and UUID-ID portal pages pass
- [ ] No new advisory-lock contention (reuses existing `storage.insert_*` writer path)

**Why this sits in WS5a and not WS14 (Calendar Quality):** the failure is ingest-parser-driven, not a view-layer filter. WS14's calendar work is downstream of this — templates render what's in the DB; there's nothing in the DB to render.

**Cross-reference:** [.coordination/FEEDBACK_LOG.md 2026-04-15 Erik — empty UUID raadsperiode](../../.coordination/FEEDBACK_LOG.md)

#### Pre-execution checklist (combined, must be green before starting B.1)

- [ ] PR-0 lock-42 retrofit shipped
- [ ] PR-1 compute-site decision made with cost+timing math on 496-meeting backlog
- [ ] PR-2 Alembic migration for `pipeline_runs.parent_run_id` + `step_index` shipped
- [ ] PR-3 Alembic migration for `document_chunks.attribution_status` shipped
- [ ] Hetzner disk expanded OR `downloads/` on separate volume OR aggressive cleanup policy
- [ ] `ffmpeg` added to Dockerfile (if Hetzner path)
- [ ] Royalcast scraper regression fixtures (5 iBabs page shapes) built as tests
- [ ] `scheduled_document_processor` migrated to explicit lock-42 acquire
- [ ] Existing promote-script point-ID schemes reconciled to ONE scheme
- [ ] Timezone explicitly set on `BackgroundScheduler` OR `TZ=Europe/Amsterdam` in `config/deploy.yml`
- [ ] `output/transcripts/*.json` synced to prod (or strategy decided to re-generate)

### 14-day eval gate — unchanged contract, revised timeline

Clock starts when Phase B items 1–5 are live (original contract). Given the revised 60–90h estimate + prerequisites, realistic calendar is: 1 week prereqs → 2 weeks Phase B build → 14 days eval = **~5 weeks to `/ws-complete WS5a`**. Phase A infrastructure (hourly smoke test + daily qa_digest) is what measures the clock — no new tooling needed.

### 14-day eval gate

Clock starts when Phase B items 1–5 are live. `/ws-complete WS5a` waits until 14 clean nights per the eval table. Phase A infrastructure (hourly smoke test + daily qa_digest) is what measures the clock — no new tooling needed.

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

> See [`pipeline/README.md`](../../pipeline/README.md) for the writer contract (advisory lock registry, the 8 rules, QA gate thresholds, SOP). That file is authoritative for anyone writing to Postgres or Qdrant.

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

### Data integrity audit — chunk → document_id attribution (~1 day) *(added 2026-04-11, triaged from [FEEDBACK_LOG.md 2026-04-11](../../.coordination/FEEDBACK_LOG.md))*

**Why this is in WS5a and not WS1 or WS4:** the failure mode is an *ingest-time* attribution bug, not a retrieval bug. A search for parkeertarieven returned `doc 246823` with a snippet showing "Centrum €3.50 / Buiten centrum €2.00", but when `lees_fragment` was called the document turned out to be a GroenLinks kaderbrief about urban development — no parking content at all. The only way this happens is if a chunk's `document_id` does not match the document the chunk text actually came from. That's an ingest pipeline integrity failure. **This is the single most dangerous failure mode in the platform** because it produces confident-looking hallucinations that no LLM can catch without auditing every source manually.

- [ ] **`scripts/audit_chunk_attribution.py`** — reads every chunk in PostgreSQL `chunks` and Qdrant, verifies that the text content substring-matches the referenced document's raw text blob. Outputs `reports/chunk_attribution_audit.csv` with rows: `(chunk_id, document_id, match_type, severity)` where `match_type ∈ {exact, substring, fuzzy, mismatch}`. Target: 100% `exact` or `substring`; zero `mismatch`.
- [ ] **Root cause analysis** — for any `mismatch` row, trace back through the ingest pipeline (which script wrote the chunk, what was the document hash at write time, was there a concurrent write). Likely culprits: stale document cache, hash collision, ordering bug in `pipeline/staging_ingestor.py`.
- [ ] **Regression test** — new test `tests/pipeline/test_chunk_attribution.py` that ingests a known fixture set and asserts zero mismatches. Becomes a permanent part of the smoke test in step `00_smoke_test.py`.
- [ ] **Corrective action** — if mismatches are found, write `scripts/repair_chunk_attribution.py` that either re-attributes (if the source doc is findable by hash) or quarantines (if not) affected chunks. Runs under `pg_advisory_lock(42)`.
- [ ] **Acceptance** — audit run produces zero `mismatch` rows; smoke test asserts the invariant; add `chunk_attribution_audit_passed` to the daily 07:00 CET health email.

**Related FEEDBACK_LOG entry:** [2026-04-11 zoek_raadshistorie / lees_fragment — Parkeertarieven Rotterdam](../../.coordination/FEEDBACK_LOG.md), specifically the "doc 246823 was a false positive" failure mode.

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
- [ ] **15-min refresh covers raadsperiode 2026-2030** *(B.7, added 2026-04-15)* — stadsberaad + UUID-format raadsvergaderingen surface in `meetings` + `agenda_items` + `documents` within one 15-min cycle; iBabs parser fixtures green for both numeric-ID and UUID-ID page shapes
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

## Pipeline integration (added 2026-04-12)

**Important context:** WS2 shipped an automated pipeline that partially overlaps with WS5a's scope. The following APScheduler jobs already run in production in `main.py`:

| Job | Interval | What it does |
|---|---|---|
| `scheduled_refresh` | 15 min | Poll ORI/iBabs, download documents |
| `scheduled_document_processor` | 20 min | Chunk → Nebius embed → Qdrant → BM25 tsvector → OCR recovery |
| `scheduled_financial_sweep` | 1 hr | Extract financial_lines from table_json chunks |

**WS5a should build on this, not replace it.** The 7-step nightly job graph in the build tasks above should:
- Reuse `document_events` and `pipeline_runs` tables for logging/tracking
- Use the same APScheduler pattern (jobs in `main.py`, not crontab)
- Coordinate with existing jobs via advisory lock 42
- The daily email summary can query `pipeline_runs` + `document_events` for all job statuses

**Tables already available:**
- `pipeline_runs` — job-level summary (status: `running/success/failure/skipped`, triggered_by: `cron/manual/smoke_test`)
- `pipeline_failures` — per-item failure log
- `document_events` — per-document activity timeline

## Outcome
*To be filled in when shipped. Include: actual end-to-end latency, hardest failure mode encountered, lock contention observations, smoke test rotation strategy.*
