# NeoDemos pipeline — contract for writers

This is the canonical guide for any script/module/service that writes to Postgres or Qdrant.
If you deviate from these rules, the nightly pipeline (WS5a) will fail and a human gets paged.

## Advisory lock registry

| Lock key | Owner | Blocking? | What it guards | Caller pattern |
|---|---|---|---|---|
| 42 | WS5a nightly pipeline | Yes (`pg_advisory_lock`) | all chunk/document embedding writes, KG enrichment, financial extraction | borrow lock before write; release in `finally` |
| 7_640_601 | WS6 summarization | No (`pg_try_advisory_lock`, skip on contention) | documents.summary_* writes | skip cleanly if another holder |
| (none) | WS11 Phase 6 embedded_at UPDATE | — | one atomic single-statement UPDATE | only one writer at a time in prod |

## Writer contract (the 8 rules)

1. Never write to Postgres/Qdrant without going through `services/db_pool.py`.
2. Never bypass `pg_advisory_lock(42)` for any write that affects `document_chunks`, `documents`, or Qdrant `notulen_chunks`.
3. Always write a row into `pipeline_runs` (status=running → success|failure) around long operations — this powers `/admin/pipeline`.
4. On failure, write to `pipeline_failures` with a stable `item_id` so re-runs pick up where they left off.
5. Emit a `document_events` row on every per-document state change (downloaded, chunked, financial_detected, promoted, etc.).
6. Idempotent by default — re-running must never duplicate rows. Use `ON CONFLICT DO NOTHING` or hash-based dedup.
7. Smoke test fixtures must be tagged `is_smoke_test=true`; they must never leak into user search.
8. Every new script must document its lock key + table footprint at the top.

## QA suite

The nightly `qa_digest` runner invokes every audit tool we own. See `scripts/nightly/qa_digest.py`. Gate thresholds:

| Check | Green | Yellow | Red |
|---|---|---|---|
| chunk_attribution_mismatch | 0 | any | any |
| chunk_attribution_fuzzy | <10% | 10-40% | >40% |
| vector_gaps | 0 | 1-100 | >100 |
| raadslid_roles | 0 | 1-10 | >10 |
| financial_coverage | >80% | 50-80% | <50% |
| failures_queue_depth (24h) | 0 | 1-5 | >5 |
| smoke_test_status (24h) | ≥22/24 | 18-21 | <18 |
| active_writers >5min | 0 | 1 | >1 |
| lock_contention (ungranted) | 0 | 1-3 | >3 |

A RED anywhere produces an OVERALL=RED email + `/admin/pipeline` banner. Don't ship the nightly backfill if OVERALL=RED.

## Standard operating procedure

### Before running any backfill or large write:
1. Verify SSH tunnel up: `ps aux | grep "ssh.*178.104"`
2. Verify no active writer: check `/admin/pipeline` → Activity section, no job in `running` state
3. Verify `qa_digest` last run was GREEN
4. Verify the `DOCUMENT_PROCESSOR_PHASE2_ENABLED` flag is in the expected state (currently OFF pending WS11 Phase 7)

### During the run:
1. Use `pg_advisory_lock(42)` for every write
2. Write progress to `pipeline_runs` at least once per 100 items
3. If you're going to hold lock 42 for >10min, log a warning so operators know

### After the run:
1. Run `scripts/nightly/qa_digest.py --sample-size 5000` and confirm GREEN
2. If YELLOW or RED, flag in `.coordination/FEEDBACK_LOG.md`
3. Update the relevant handoff's Outcome section

## References
- WS5a handoff: `docs/handoffs/WS5a_NIGHTLY_PIPELINE.md`
- Memory: `.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_embedding_process.md`
- Feedback log: `.coordination/FEEDBACK_LOG.md`
