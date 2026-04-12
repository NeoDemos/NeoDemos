-- WS6: Source-Spans-Only Summarization — cached per-document summary columns
--
-- Additive-only migration. Production-safe against the running
-- committee_notulen_pipeline because:
--   • lock_timeout fails the ALTER within 3s if a writer holds the table,
--     rather than queueing indefinitely behind it
--   • ADD COLUMN IF NOT EXISTS is metadata-only in PG 11+ (no table rewrite),
--     because every new column is nullable with no DEFAULT
--   • CREATE INDEX CONCURRENTLY does not block writes to `documents` at all
--     during the build, at the cost of running OUTSIDE a transaction block
--
-- Existing reads and writes to `documents` continue to work identically:
-- the new columns are all nullable, no constraints are changed, and the
-- existing INSERT in services/storage.py:362 uses explicit column names
-- so new columns cannot break positional inserts.
--
-- ── PRE-FLIGHT (verify no active writer on documents) ─────────────────
--
-- Before running, paste this into psql and make sure no rows come back:
--
--   SELECT pid, state, wait_event, query_start,
--          LEFT(query, 100) AS query
--   FROM pg_stat_activity
--   WHERE state <> 'idle'
--     AND query ILIKE '%documents%'
--     AND pid <> pg_backend_pid();
--
-- If rows come back, wait until they finish, then retry.
--
-- ── HOW TO APPLY ───────────────────────────────────────────────────────
--
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f scripts/migrate_ws6_summary_columns.sql
--
-- The -v ON_ERROR_STOP=1 flag is important: without it, psql keeps going
-- after an error and the second phase runs even if the first failed.
--
-- ── RECOVERY (if Phase 2 fails mid-build) ─────────────────────────────
--
-- A failed CREATE INDEX CONCURRENTLY leaves behind an INVALID index that
-- IF NOT EXISTS will skip on retry. Drop it first:
--
--   DROP INDEX CONCURRENTLY IF EXISTS idx_documents_summary_needed;
--
-- then re-run this file.
--
-- ── VERIFICATION (after running) ──────────────────────────────────────
--
--   \d documents                         -- should show 5 new columns
--   SELECT indexname, indexdef FROM pg_indexes
--     WHERE tablename='documents' AND indexname='idx_documents_summary_needed';
--   -- verify "valid": SELECT indisvalid FROM pg_index
--   --   WHERE indexrelid = 'idx_documents_summary_needed'::regclass;
--
-- Handoff: docs/handoffs/WS6_SUMMARIZATION.md
-- ──────────────────────────────────────────────────────────────────────


-- ────────────────────────────────────────────────────────────────────────
-- Phase 1: metadata-only column adds.
-- Takes a brief ACCESS EXCLUSIVE lock on documents. Fails within 3s if
-- the lock cannot be acquired, rather than queueing behind a long writer.
-- ────────────────────────────────────────────────────────────────────────

SET lock_timeout = '3s';
SET statement_timeout = '30s';

BEGIN;

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS summary_short       TEXT,
    ADD COLUMN IF NOT EXISTS summary_long        TEXT,
    ADD COLUMN IF NOT EXISTS summary_themes      JSONB,
    ADD COLUMN IF NOT EXISTS summary_computed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS summary_verified    BOOLEAN;

COMMIT;


-- ────────────────────────────────────────────────────────────────────────
-- Phase 2: partial index for the nightly "find docs needing summary" query.
--
-- CREATE INDEX CONCURRENTLY cannot run inside a transaction block, so this
-- is a separate top-level statement. It does not take ACCESS EXCLUSIVE on
-- the table at any point — reads and writes continue throughout the build.
--
-- On ~86K eligible rows with a partial WHERE, this typically completes in
-- seconds. lock_timeout is reset because CONCURRENTLY needs brief locks
-- at the start and end of its build and those should wait, not fail fast.
-- ────────────────────────────────────────────────────────────────────────

RESET lock_timeout;
RESET statement_timeout;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_documents_summary_needed
    ON documents (id)
    WHERE summary_short IS NULL;
