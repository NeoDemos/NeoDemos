# WS11 — Corpus Completeness 2018–2026

> **Status:** `in progress` — WS11c hash standardization: Phase 6 (embedded_at backfill) paused for autovacuum; Phases 7–8 pending. WS11a+WS11b ✅ done.
> **Owner:** `dennis`
> **Priority:** 1 (blocks WS1 enrichment quality)
> **Parallelizable:** yes (WS11a and WS11b can run in parallel)
> **Resume point for a clean agent:** see "Resume Phase 6" section at bottom of this doc.

---

## TL;DR

WS11 started as a coverage + metadata fix triggered by Erik Verweij (2026-04-13) not finding schriftelijke vragen or initiatiefnotities via MCP. It expanded into a full corpus classification effort:

1. **Coverage gap fixed** — 293 missing schriftelijke vragen ingested from ORI; P1 types (raadsvoorstel 324, toezegging 436, brief_college 1,163, afdoeningsvoorstel 5) being ingested now.
2. **Metadata gap fixed** — 62,627 docs across **30 named types** now have `doc_classification`. NULL dropped from 88K → ~26,754 (genuinely unidentifiable docs only).
3. **Future-proofed** — `municipality` column added (zero re-embed cost for multi-city), `source` column added, `CIVIC_DOC_TYPES` guard prevents processor from overwriting pre-set labels.

---

## Architecture

### `doc_classification` as the authoritative type label

`doc_classification` was previously overloaded: pipeline-routing values (`garbled_ocr`, `table_rich`, `regular`) set by `document_processor.py` would overwrite pre-set civic types.

**Fix (2026-04-13):**
- `pipeline/document_classifier.py` — `CIVIC_DOC_TYPES` frozenset (30 types). Processor reads existing value; only writes pipeline-routing type if not in `CIVIC_DOC_TYPES`.
- `doc_classification` is now the authoritative content type label. Pipeline-routing values only land when type is truly unknown.

### New DB columns (migration 0006)

| Column | Type | Default | Purpose |
|---|---|---|---|
| `municipality` | `VARCHAR(50) NOT NULL` | `'rotterdam'` | Multi-city retrieval filtering — zero re-embed cost |
| `source` | `VARCHAR(50)` | NULL | Origin: `ori`, `ibabs`, `scraper`, `manual` |

Both flow through `services/storage.py insert_document()` and into Qdrant chunk payload.

---

## Current DB state (2026-04-13, live counts)

| doc_classification | count | Notes |
|---|---|---|
| NULL | 26,802 | Genuinely unidentifiable (BB-besluitenboek, inspreekbijdragen, etc.) |
| motie | 12,822 | |
| bijlage | 10,129 | |
| brief_college | 5,015 | P1 ingest still running — final count higher |
| schriftelijke_vraag | 3,851 | ORI complete |
| agenda | 3,651 | |
| rapport | 3,297 | |
| verslag | 3,102 | |
| raadsvoorstel | 3,086 | |
| begroting | 2,399 | |
| toezegging | 2,016 | |
| annotatie | 2,008 | |
| afdoeningsvoorstel | 1,703 | |
| adviezenlijst | 1,370 | |
| notulen | 1,177 | |
| spreektijdentabel | 1,094 | |
| besluitenlijst | 1,077 | |
| planning | 898 | |
| monitor_rapport | 895 | |
| notitie | 887 | |
| amendement | 734 | |
| transcript | 719 | |
| presentatie | 652 | |
| initiatiefvoorstel | 522 | |
| ingekomen_stukken | 438 | |
| grondexploitatie | 338 | |
| voorbereidingsbesluit | 332 | |
| rekenkamer | 162 | |
| jaarstukken | 139 | |
| initiatiefnotitie | 111 | |
| memo | 30 | |
| regular / table_rich / financial | 12 | Pipeline-routing residual — will vanish as docs re-process |

**Total docs:** ~90,500 (growing as P1 ingest completes)
**Classified:** ~63,700 (70%)
**NULL:** ~26,802 (30%) — genuinely unidentifiable; no keyword pattern matches their names

---

## ORI coverage gaps — verified 2026-04-13

| Doc type | In DB | ORI total | Gap ingested | Status |
|---|---|---|---|---|
| schriftelijke_vraag | 3,851 | 3,269 | 293 | ✅ complete |
| initiatiefnotitie | 111 | 78 | 0 | ✅ DB has more (iBabs) |
| initiatiefvoorstel | 522 | 333 | 0 | ✅ DB has more (iBabs) |
| raadsvoorstel | 3,086 | 2,641 | 324 | ✅ ingested |
| toezegging | 2,016 | 3,358 | 436 | ✅ ingested |
| brief_college | 5,015 | 7,399 | 1,163 | ⏳ running |
| afdoeningsvoorstel | 1,703 | 2,551 | 5 | ✅ ingested |

**ORI API notes:**
- Index: `ori_rotterdam_20250629013104` — Rotterdam-only (70,148 searchable docs). `_cat/indices` ~503K includes deleted Lucene segments.
- `@type` maps as direct keyword (NOT `@type.keyword`).
- `classification` requires `.keyword` subfield for term queries.

---

## Sub-workstreams

### WS11a — Metadata backfill ✅ DONE 2026-04-13

Sets `doc_classification` on all existing DB docs via name-pattern ILIKE matching. Three passes:

**Script:** `scripts/ws11a_classify_existing_docs.py`

```bash
python scripts/ws11a_classify_existing_docs.py              # dry-run (default)
python scripts/ws11a_classify_existing_docs.py --execute    # all types
python scripts/ws11a_classify_existing_docs.py --execute --only-new   # P1 only
python scripts/ws11a_classify_existing_docs.py --execute --only-p3    # P3 only
python scripts/ws11a_classify_existing_docs.py --execute --skip-p2    # skip motie/amendement
```

**Results:**

| Pass | Types | Docs updated |
|---|---|---|
| Initial | initiatiefnotitie, initiatiefvoorstel, schriftelijke_vraag, motie, amendement | 17,747 |
| P1 expansion | raadsvoorstel, brief_college, afdoeningsvoorstel, toezegging | 9,896 |
| P3 expansion | 21 meeting/procedural/financial types (agenda, verslag, notulen, rapport, bijlage, begroting, …) | 34,794 |
| **Total** | **30 types** | **~62,437** |

---

### WS11b — ORI ingestion ⏳ P1 running

Fetches missing docs from ORI API, upserts to DB. New docs picked up by `document_processor.py` for chunking + embedding.

**Script:** `scripts/ws11b_ori_ingestion.py`

```bash
python scripts/ws11b_ori_ingestion.py --dry-run           # audit gaps only
python scripts/ws11b_ori_ingestion.py                     # P0: schriftelijke_vraag + initiatiefnotitie
python scripts/ws11b_ori_ingestion.py --include-p1        # + all P1 types
python scripts/ws11b_ori_ingestion.py --resume            # resume from checkpoint
```

**P0 results (done):** 293 schriftelijke vragen inserted. 228 had no ORI text (content=NULL → OCR queue). 65 chunked/embedded.

**P1 results (running):**

| Type | ORI | Missing | Status |
|---|---|---|---|
| raadsvoorstel | 2,641 | 324 | ✅ done |
| toezegging | 3,358 | 436 | ✅ done |
| brief_college | 7,399 | 1,163 | ⏳ ~1,150 inserted |
| afdoeningsvoorstel | 2,551 | 5 | ✅ done |

**Checkpoint:** `data/pipeline_state/ws11b_checkpoint.json` (atomic write, resume-safe)

**After ingestion — embed new docs:**
```bash
# Wait for WS7 to finish first (embeddings should be on recovered text)
python -m services.document_processor --limit 500
# Repeat until 0 unchunked docs remain
```

---

## Execution order (as executed 2026-04-13)

```
1. alembic upgrade head              → migration 0006 (municipality + source)
2. ws11a --dry-run                   → audit: 17,747 P0 docs to classify
3. ws11a --execute                   → P0 classified
4. ws11a --execute --only-new        → P1 classified (9,896 docs)
5. ws11a --execute --only-p3         → P3 classified (34,794 docs)
6. ws11b --dry-run --include-p1      → audit ORI gaps
7. ws11b                             → P0 ingest (293 SVs)
8. ws11b --include-p1 --resume       → P1 ingest (1,928 docs) ← running
9. document_processor --limit 500    → embed new docs ← pending (after WS7)
```

---

## ORI API Reference

```
Base: https://api.openraadsinformatie.nl/v1/elastic/ori_rotterdam_20250629013104/_search
Auth: none required (public)
Rate: no documented limit; use 1 req/sec conservatively

Key date field: last_discussed_at (NOT date_modified — ORI indexes 1-3 months late)
@type: direct keyword field (no .keyword suffix)
classification: requires .keyword suffix for term queries
```

See `docs/ws11_scope.json` for all query templates.

---

## DB Schema

Documents land in `documents` with:
- `id`: ORI `@id` (stable external key — `ON CONFLICT (id) DO UPDATE`)
- `name`: ORI `name`
- `url`: ORI `url` or `original_url`
- `content`: ORI `text[]` joined, or NULL (triggers OCR via processor)
- `category`: `'municipal_doc'`
- `doc_classification`: content type label (one of 30 named types, or NULL if unidentifiable)
- `municipality`: `'rotterdam'`
- `source`: `'ori'`
- `meeting_id`: derived from ORI `was_generated_by` (nullable)

---

## Workstream interactions

| WS | Interaction |
|---|---|
| **WS5a** (nightly pipeline) | WS11b is a one-time batch. WS5a makes ongoing ingestion automatic — run WS11 first. |
| **WS7** (OCR recovery) | Run WS7 on new docs BEFORE the final embed pass — embeddings should be on recovered text, not garbled source. |
| **WS4** (MCP discipline) | `doc_classification` now has 30 reliable values. WS4 can add `doc_type` filter param to `zoek_raadshistorie` in v0.2.1. |
| **WS1** (GraphRAG) | WS11 new docs become graph nodes. Run WS11 before WS1 entity extraction. |

---

## v0.2.0 vs deferred

### v0.2.0 ✅ done (except embed pass)
- Migration 0006 (`municipality` + `source`) ✅
- `CIVIC_DOC_TYPES` guard — 30 types, processor cannot overwrite ✅
- WS11a P0/P1/P3 backfill — 62,437 docs classified ✅
- WS11b P0 ingest — 293 SVs ✅
- WS11b P1 ingest — 1,928 docs ⏳ running
- `municipality` in Qdrant payload ✅
- NULL = genuinely unidentifiable only (~26,802, 30%) ✅

### v0.2.1
- MCP `doc_type` filter parameter on `zoek_raadshistorie` (coordinate with WS4)
- iBabs fallback for 2025-2026 recents (ORI covers through mid-2025)
- `zoek_schriftelijke_vragen` as dedicated MCP tool (or `doc_type` param)

### v0.3+
- Multi-city MCP `gemeente` filter parameter
- Per-city ORI index discovery (`ori_<city>_<date>` naming)

---

## Success Criteria

- [x] Migration 0006 applied (`municipality`, `source` columns exist)
- [x] 30 named doc types all have `doc_classification` set — NULL = unidentifiable only
- [x] Schriftelijke vragen: 3,851 in DB (ORI gap closed)
- [x] initiatiefnotitie: 111 in DB (covered)
- [x] raadsvoorstel, toezegging, brief_college, afdoeningsvoorstel: ORI gaps ingested
- [x] `municipality = 'rotterdam'` on all docs; flows to Qdrant payload
- [ ] WS11b P1 ingest complete (brief_college finishing ⏳)
- [ ] All new docs embedded in Qdrant `notulen_chunks` (pending: wait for WS7 to finish)
- [ ] Erik Verweij re-test: can retrieve initiatiefnotities and schriftelijke vragen by topic

---

## Verification queries

```sql
-- Full classification breakdown
SELECT doc_classification, COUNT(*) FROM documents GROUP BY 1 ORDER BY 2 DESC;
-- Expected: 30 types populated, NULL ≤ 27,000

-- Schriftelijke vragen coverage
SELECT COUNT(*) FROM documents WHERE doc_classification = 'schriftelijke_vraag';
-- Expected: ≥ 3,800

-- ORI source check (civic type guard)
SELECT doc_classification, COUNT(*) FROM documents WHERE source = 'ori' GROUP BY 1;
-- Expected: civic types preserved, NOT overwritten with 'regular'

-- municipality completeness
SELECT municipality, COUNT(*) FROM documents GROUP BY 1;
-- Expected: all rows = 'rotterdam'

-- Unchunked ORI docs (embed queue)
SELECT COUNT(*) FROM documents d
WHERE source = 'ori'
  AND content IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM document_chunks dc WHERE dc.document_id = d.id);
-- Expected: 0 after embed pass

-- Qdrant payload check (Python)
-- from qdrant_client import QdrantClient
-- q = QdrantClient(url=...)
-- r = q.scroll('notulen_chunks', limit=1, with_payload=True)
-- assert 'municipality' in r[0][0].payload
-- assert r[0][0].payload['municipality'] == 'rotterdam'
```

---

## WS11c — Qdrant hash standardization + orphan cleanup ⏳ IN PROGRESS (2026-04-14)

> **Trigger:** during embedding pass planning we discovered 4 embedding writers + 1 audit reader used **two incompatible MD5 formulas** for Qdrant point IDs. This produced duplicate/orphan points and made the `embedded_at` marker column unreliable.

### Scheme A (canonical, single source of truth)

```python
# services/embedding.py
def compute_point_id(document_id: str, db_id: int) -> int:
    return int(hashlib.md5(f"{document_id}_{db_id}".encode()).hexdigest()[:15], 16)
```

All embedding writers now import this helper. Legacy Scheme B (`md5(f"{doc}_{child_id}_{chunk_index}")[:15]`) is deprecated — used only by `scripts/repair_scheme_b_points.py` and `scripts/ws10_finalize_embeddings.py::legacy_scheme_b_point_id()` for cleanup of legacy points.

### Files changed (commit `568cf3c` — already deployed via Kamal)

| File | Change |
|---|---|
| `services/embedding.py` | Added `compute_point_id()` canonical helper |
| `services/document_processor.py` | Scheme A + `DOCUMENT_PROCESSOR_PHASE2_ENABLED` env-var kill-switch |
| `scripts/migrate_embeddings.py` | Import helper, remove local MD5 |
| `scripts/audit_vector_gaps.py` | Import helper |
| `scripts/ws10_finalize_embeddings.py` | Helper + `legacy_scheme_b_point_id` for cleanup |
| `scripts/promote_committee_notulen.py` | Uses `RETURNING id` from production INSERT, not staging |
| `pipeline/ingestion.py` | INSERT-first, then embed, with helper |
| `pipeline/staging_ingestor.py` | INSERT-first, then embed, with helper |
| `scripts/compute_embeddings.py` | Deprecation banner (legacy Scheme C — do not run) |
| `config/deploy.yml` | `DOCUMENT_PROCESSOR_PHASE2_ENABLED=false` added to env.clear |
| `alembic/0010` | Removed the 1.74M-row in-txn backfill (was killing SSH tunnel); column + partial index only |
| `alembic/0011` | New `document_relationships` table (615 afdoening_van + 2,867 related_raadsvoorstel edges populated via `scripts/map_document_relationships.py`) |

### Execution phases (as of 2026-04-14 ~22:00 local)

| Phase | Status | Detail |
|---|---|---|
| **0. Read-only verification** | ✅ done | 2000 Scheme B points confirmed in Qdrant |
| **1. Kill-switch added + local uvicorn stopped** | ✅ done | |
| **2. `compute_point_id` helper + all 6 writers updated** | ✅ done | Commit `568cf3c` |
| **2b. Deploy WS11 changes to Hetzner** | ✅ done | Kamal blue-green; `DOCUMENT_PROCESSOR_PHASE2_ENABLED=false` live in prod |
| **3. Repair 2000 mis-keyed Scheme B chunks** | ✅ done | `scripts/repair_scheme_b_points.py` |
| **5. Re-key 7026 VN points Scheme B → Scheme A** | ✅ done | `scripts/rekey_vn_points.py` (no re-embed, just ID swap + `embedded_at = NOW()`) |
| **4. `migrate_embeddings.py --recovery-mode` for ~94K gap** | ✅ done | 84,890 / 84,922 upserted (32 skipped: NaN or short content) |
| **9. Orphan audit + cleanup** | ✅ done | 274,596 orphans deleted (265,558 safe + 4,903 intact-doc NO_OVERLAP + 4,135 preserved after investigation) |
| **9b. Restore 8 docs from WS7 backup** | ✅ done | +104K chars recovered from `staging.ocr_recovery_originals`; pre-restore copies in `staging.ws11_pre_restore_backup`. Chunks cleared to trigger rechunking on Phase 7. |
| **6. Backfill `embedded_at`** | ⚠️ **IN PROGRESS — stalled** | See "Resume Phase 6" below |
| **7. Re-enable `DOCUMENT_PROCESSOR_PHASE2_ENABLED=true` + redeploy** | ⏳ pending | After Phase 6 |
| **8. CI guard against divergent MD5 literals** | ⏳ pending | After Phase 7 |

### Current live state (2026-04-14 ~22:00 local)

```
document_chunks:
  total rows:       1,738,281
  embedded_at IS NULL:     1,641,224
  embedded_at IS NOT NULL:   97,057  (from Phase 5 VN rekey)
  dead tuples:        688,086  (autovacuum running after rolled-back UPDATE)

Qdrant notulen_chunks:
  ~1,872,962 points (all Scheme A; 274,596 orphans removed)

Partial index:
  idx_document_chunks_unembedded  ← DROPPED (must be recreated after Phase 6)
  Definition was: CREATE INDEX ... ON document_chunks USING btree (id) WHERE (embedded_at IS NULL)

Production on Hetzner:
  DOCUMENT_PROCESSOR_PHASE2_ENABLED=false (scheduler skips Phase 2 embedding)
  Phase 1 (chunking) still active
  MCP + web healthy
```

### 🚨 START HERE — Session handoff snapshot (2026-04-14 21:07 local)

Session was closed mid-Phase-6 with an autovacuum running. **Check autovacuum first before doing anything else.**

**State at handoff:**
```
Timestamp:                     2026-04-14T21:07:27 local
embedded_at IS NULL:           1,641,224  (unchanged — backfill never committed)
embedded_at IS NOT NULL:          97,057  (from Phase 5 VN rekey only)
dead tuples in document_chunks:  688,086  (from rolled-back 81-min UPDATE)
partial index idx_document_chunks_unembedded: DROPPED (must rebuild)
active autovacuum on document_chunks: YES (pid 125353, wait=DataFileRead, age 522s at snapshot)
production DOCUMENT_PROCESSOR_PHASE2_ENABLED: false (scheduler skipping Phase 2)
Qdrant notulen_chunks count: ~1,872,962 points (all orphans removed)
SSH tunnel alive: ps aux | grep 178.104  → should show PID 12349
```

**First action — verify autovacuum state:**
```bash
cd "/Users/dennistak/Documents/Final Frontier/NeoDemos" && source .venv/bin/activate && set -a && source .env && set +a && python -c "
import os, psycopg2
c = psycopg2.connect(os.environ['DATABASE_URL']).cursor()
c.execute(\"SELECT pid, wait_event, EXTRACT(EPOCH FROM (NOW() - query_start))::int FROM pg_stat_activity WHERE query LIKE '%autovacuum%document_chunks%' AND state='active'\")
rows = c.fetchall()
print('autovacuum active:', bool(rows))
for r in rows: print(f'  pid={r[0]} wait={r[1]} age={r[2]}s')
c.execute(\"SELECT n_dead_tup, n_live_tup FROM pg_stat_user_tables WHERE relname='document_chunks'\")
d, l = c.fetchone()
print(f'dead tuples: {d:,} (target: < 10K before proceeding)')
print(f'live tuples: {l:,}')
"
```

**Decision tree:**

| Result | Action |
|---|---|
| Autovacuum still running, dead > 100K | Wait 5-15 min, recheck. Do NOT kick off Phase 6 yet (I/O contention). |
| Autovacuum still running, dead < 50K | Safe to proceed — autovac is in final pass; it won't meaningfully slow a new UPDATE. |
| Autovacuum NOT running, dead < 10K | Proceed immediately to "Resume Phase 6" step 2. |
| Autovacuum NOT running, dead still ≥ 100K | Unusual — check `pg_stat_user_tables.last_autovacuum` timestamp. May need to trigger manual `VACUUM (VERBOSE) document_chunks;` before Phase 6. |

Once autovacuum is done OR dead tuples are low, proceed to the numbered steps below.

---

### Resume Phase 6 — a clean agent picks up here

**Goal:** set `embedded_at = NOW()` on all ~1.64M chunks whose Scheme A point IS in Qdrant.

**What went wrong last time:**
- `--exact` mode used `WHERE id = ANY(%s::int[])` with 10K-element arrays per batch → 170 small commits → WAL rotation overhead → 125s/batch, ETA ~6 hours
- Single atomic UPDATE with partial index present → 81 min without commit, cycling through WAL/DataFile I/O, WAL rate dropped from 12.7 → 2.6 MB/sec → killed via `pg_terminate_backend(120116)` → clean rollback
- Partial index `idx_document_chunks_unembedded` was the main WAL amplifier (every row update removes one entry from it)

**Recommended path forward:**

1. **Wait for autovacuum** — `pg_stat_user_tables.n_dead_tup` is 688K. Watch it drop to ≤10K before proceeding. Check via:
   ```sql
   SELECT n_dead_tup, n_live_tup FROM pg_stat_user_tables WHERE relname = 'document_chunks';
   SELECT pid, wait_event, EXTRACT(EPOCH FROM (NOW() - query_start))::int AS age
   FROM pg_stat_activity WHERE query LIKE '%autovacuum%document_chunks%';
   ```

2. **Run single atomic UPDATE (no partial index now; should be 5-15 min)**:
   ```python
   import os, psycopg2, time
   conn = psycopg2.connect(os.environ["DATABASE_URL"])
   conn.autocommit = False
   cur = conn.cursor()
   cur.execute("SET statement_timeout = 0")
   cur.execute("SET synchronous_commit = OFF")
   t0 = time.time()
   cur.execute("UPDATE document_chunks SET embedded_at = NOW() WHERE embedded_at IS NULL")
   rows = cur.rowcount
   conn.commit()
   print(f"Updated {rows:,} rows in {time.time()-t0:.1f}s")
   ```
   Run via `nohup ... &; disown` so it survives client timeouts.

3. **Rebuild partial index CONCURRENTLY (non-blocking, ~1-3 min)**:
   ```sql
   CREATE INDEX CONCURRENTLY idx_document_chunks_unembedded
     ON document_chunks USING btree (id) WHERE (embedded_at IS NULL);
   ```

4. **Residual cleanup for the ~32 Phase-4-skipped chunks:** after the UPDATE, a handful of chunks will have `embedded_at IS NOT NULL` but their point isn't actually in Qdrant (they failed Phase 4 embedding due to NaN/Inf or API errors — known IDs include `574026` and `574283` from `logs/migration_errors.log`). Run one more targeted audit:
   ```bash
   python -c "from scripts.audit_vector_gaps import compute_missing_ids; ..."
   ```
   For each missing ID, `UPDATE document_chunks SET embedded_at = NULL WHERE id = %s` so Phase 7's re-enabled scheduler picks them up.

5. **Phase 7 — re-enable scheduler embedding + redeploy**:
   - Edit `config/deploy.yml` → set `DOCUMENT_PROCESSOR_PHASE2_ENABLED=true`
   - `/opt/homebrew/lib/ruby/gems/4.0.0/bin/kamal deploy` (blue-green, zero-downtime)
   - Verify Phase 2 embeds only the ~32 residual chunks + new content on next 20-min cycle — not scanning 1.64M. If it does scan 1.64M, the backfill didn't stick.

6. **Also triggers rechunking of 8 restored docs** (Phase 1 of document_processor will pick them up — they currently have 0 chunks after Phase 9b cleared them):
   - `4057206`, `1020213`, `4794934`, `4794967`, `3138758`, `2409594`, `2377618`, `4079005`
   - These have new restored content in `documents.content` but no chunks. Phase 1 chunks them → new Scheme A points written.

7. **Phase 8 — CI guard** (low priority, small follow-up):
   - Add a test that greps the codebase for `md5\(f['"]\S+['"]\).hexdigest\(\)\[:15\]` and fails CI if any match is found outside `services/embedding.py`.

### Files written for Phase 9 audit (useful for future forensics)

- `data/pipeline_state/orphan_snapshot/orphan_points_full.jsonl.gz` — 274K full payload snapshot (50 MB gz)
- `data/pipeline_state/orphan_snapshot/orphan_full_audit.jsonl.gz` — per-orphan categorization
- `data/pipeline_state/orphan_snapshot/orphan_summary.json`, `orphan_full_audit_summary.json` — stats
- `data/pipeline_state/orphan_snapshot/no_overlap_truncated_docs.txt` — 158 truncated docs (orphan_sum > current_doc_len)
- `data/pipeline_state/orphan_snapshot/no_overlap_intact_orphans.txt` — 4,903 IDs (deleted)
- `data/pipeline_state/orphan_snapshot/no_overlap_preserved_orphans.txt` — 4,135 IDs (deleted after Phase 9b)
- `scripts/repair_scheme_b_points.py`, `scripts/rekey_vn_points.py`, `scripts/cleanup_safe_orphans.py` — one-shot cleanup scripts (idempotent; dry-run flags)

### Lessons to encode

- **Batched UPDATEs with `WHERE id = ANY(%s)` on 10K-element arrays are slow over an SSH tunnel.** Use `WHERE id BETWEEN lo AND hi` with an indexed column, OR a single atomic UPDATE.
- **Partial indexes on frequently-updated predicates amplify WAL by 30-50%.** Consider dropping + recreating CONCURRENTLY when doing a one-shot full-column UPDATE.
- **Kamal deploy does NOT run Alembic migrations automatically.** Run them manually via SSH tunnel before or after deploy.
- **`DROP INDEX CONCURRENTLY` is safe to run while a writer holds `ShareUpdateExclusiveLock` from autovacuum** — both are compatible locks.
- **Never extrapolate from a small sample when auditing 274K items.** The 200-orphan sample suggested 0 data loss but the full 274K audit revealed 8 docs with genuine recoverable content (+104K chars).
