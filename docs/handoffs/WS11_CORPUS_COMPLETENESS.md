# WS11 — Corpus Completeness 2018–2026

> **Status:** `in progress` — Dennis running as of 2026-04-13
> **Owner:** `dennis`
> **Priority:** 1 (blocks eval quality, MCP usefulness, and press readiness)
> **Parallelizable:** yes (WS11a metadata backfill can run in parallel with WS11b ingestion)

---

## TL;DR

Our 2018–2026 corpus has two problems: (1) **coverage gap** — ~2,756 schriftelijke vragen missing from DB (96% ORI gap); (2) **metadata gap** — ~753 docs already in DB have `doc_classification = NULL`, making them invisible to classification-based retrieval.

This workstream makes the corpus 100% complete and correctly classified, plus future-proofs the ingestion and retrieval pipeline for multi-city expansion.

**Trigger:** First external test user (Erik Verweij, 2026-04-13) reported he could not find initiatiefnotities or schriftelijke vragen via the MCP connector.

---

## Architecture

### Critical design fix: civic type guard

`doc_classification` previously stored pipeline-routing values (`garbled_ocr`, `table_rich`, `regular`) set by `document_processor.py`. WS11 uses it for civic content type (`schriftelijke_vraag`, `initiatiefnotitie`). Without a guard, the processor would overwrite pre-set civic types with `regular`.

**Fix already implemented** (as of 2026-04-14):
- `pipeline/document_classifier.py` — `CIVIC_DOC_TYPES` frozenset constant
- `services/document_processor.py:371` — reads existing `doc_classification`; only writes pipeline-routing type if existing value is not in `CIVIC_DOC_TYPES`
- `doc_classification` column comment updated in migration 0006

### New DB columns (migration 0006)

| Column | Type | Default | Purpose |
|---|---|---|---|
| `municipality` | `VARCHAR(50) NOT NULL` | `'rotterdam'` | Multi-city retrieval filtering (zero re-embed cost) |
| `source` | `VARCHAR(50)` | NULL | Origin: `ori`, `ibabs`, `scraper`, `manual` |

Both fields now flow through `services/storage.py insert_document()` and into Qdrant chunk payload (`municipality`, `doc_classification`).

---

## Scope

### Coverage gaps (2018–2026) — verified 2026-04-14 via live ORI queries

| Doc type | In DB | ORI (MediaObject) | ORI (Report) | Gap | Priority |
|---|---|---|---|---|---|
| schriftelijke_vraag | ~120 | 2,882 | 292 (2025-2026 only) | **2,762 (96%)** | **P0** |
| initiatiefnotitie | 42 | 78 | — | **36 (46%)** | **P0** |
| initiatiefvoorstel | 231 | 333 | — | 102 (31%) | P1 |
| motie | 9,398 | 8,177 | 232 | covered (DB has more from meeting bundles) | P2 |
| amendement | 469 | 519 | 86 | 50 (10%) | P2 |
| raadsvoorstel | 1,849 | 2,322 | 319 | **473 (20%)** — larger than expected | P2 |
| toezegging | 2,058 | 2,927 | 431 | **869 (30%)** — larger than expected | P2 |

**Key audit findings:**
- Initiatiefnotitie gap is **much smaller** than initially estimated (36, not "high"). Most were already ingested via iBabs.
- Initiatiefvoorstel gap is 102 (not "700+") — concentrated in 2025-2026 (ingestion lag).
- Raadsvoorstel (473 missing, 20%) and toezegging (869 missing, 30%) have **larger gaps than expected** — flagged for v0.2.1.

**ORI API notes:**
- Index `ori_rotterdam_20250629013104` is Rotterdam-only (70,148 searchable docs). `_cat/indices` reports ~503K but that includes deleted Lucene segments.
- `@type` field maps directly as keyword (no `.keyword` subfield needed).
- `classification` field requires `.keyword` subfield for term queries.

See `docs/ws11_scope.json` for per-year counts and ORI API query templates.

---

## Sub-workstreams

### WS11a — Metadata backfill (0.5 days, no new ingestion)

Fix `doc_classification` on documents already in DB with `doc_classification = NULL`.

**Script:** `scripts/ws11a_classify_existing_docs.py`

```bash
# Step 1: dry-run — see what would change
python scripts/ws11a_classify_existing_docs.py

# Step 2: review output, then execute
python scripts/ws11a_classify_existing_docs.py --execute

# Optional: skip P2 types (motie/amendement)
python scripts/ws11a_classify_existing_docs.py --execute --skip-p2
```

**What it does:**
- Sets `doc_classification` by name-pattern match (ILIKE) — same patterns as `docs/ws11_scope.json`
- Fixes 41 initiatiefnotities + 115 initiatiefvoorstellen with `meeting_id = NULL` via `document_assignments → agenda_items` join
- Logs all changes to `logs/ws11a_classification.log`
- Dry-run is default (safe to run repeatedly for auditing)

**Targets:**
- 111 initiatiefnotities → `doc_classification = 'initiatiefnotitie'`
- 522 initiatiefvoorstellen → `doc_classification = 'initiatiefvoorstel'`
- ~120 schriftelijke_vraag docs → `doc_classification = 'schriftelijke_vraag'`
- ~9,398 moties → `doc_classification = 'motie'` (P2)
- ~469 amendementen → `doc_classification = 'amendement'` (P2)

---

### WS11b — ORI ingestion of missing documents (3 days)

Fetch missing docs from ORI API, upsert, chunk, embed, index in Qdrant.

**Script:** `scripts/ws11b_ori_ingestion.py`

```bash
# Step 1: audit ORI counts vs DB (no writes)
python scripts/ws11b_ori_ingestion.py --dry-run

# Step 2: ingest P0 types (schriftelijke_vraag + initiatiefnotitie)
python scripts/ws11b_ori_ingestion.py

# Resume after interruption
python scripts/ws11b_ori_ingestion.py --resume

# Also ingest initiatiefvoorstel (P1)
python scripts/ws11b_ori_ingestion.py --include-p1

# Step 3: chunk + embed the new docs
python -m services.document_processor --limit 500
# (run repeatedly until no unchunked docs remain)
```

**Algorithm:**
1. Fetch from ORI API (paginated 500/page) via `services/open_raad.py`
   - `fetch_docs_by_name_pattern()` — per year, MediaObject type
   - `fetch_docs_by_classification()` — Report type with `classification=Raadsvragen`
2. Skip if ORI `@id` already in DB or in checkpoint
3. Upsert via `storage.insert_document()` with `doc_classification`, `municipality='rotterdam'`, `source='ori'`
4. If ORI `text[]` available: use as content (skip OCR queue)
5. If no text: `content = NULL` → document_processor picks up for OCR
6. Checkpoint: `data/pipeline_state/ws11b_checkpoint.json` (atomic write, resume-safe)

**ORI API endpoints used** (from `docs/ws11_scope.json`):
- MediaObject name-pattern search per year
- Report classification search (`Raadsvragen`)

**Rate limiting:** 1 req/sec (conservative; ORI has no documented limit)

---

## Execution order

```
1. Run alembic upgrade → head   (migration 0006: municipality + source columns)
2. python scripts/ws11a_classify_existing_docs.py          (dry-run)
3. python scripts/ws11a_classify_existing_docs.py --execute (after review)
4. python scripts/ws11b_ori_ingestion.py --dry-run          (audit gaps)
5. python scripts/ws11b_ori_ingestion.py                    (P0 ingestion)
6. python -m services.document_processor --limit 500        (chunk + embed)
   (repeat step 6 until 0 unchunked docs remain)
7. python scripts/ws11b_ori_ingestion.py --include-p1       (P1, can defer to v0.2.1)
```

**Recommended: run WS7 (OCR recovery) on new docs before final embed pass**
- WS7 cleans garbled text; embeddings should be generated from recovered text
- After WS11b upsert, before final embed: `python scripts/ocr_recovery.py --resume`

---

## ORI API Reference

```
Base: https://api.openraadsinformatie.nl/v1/elastic/ori_rotterdam_20250629013104/_search
Auth: none required (public)
Rate: no documented limit; use 1 req/sec conservatively

Key date field: last_discussed_at (NOT date_modified — ORI indexes 1-3 months late)
```

See `docs/ws11_scope.json` for all query templates.

---

## DB Schema

Documents land in `documents` with:
- `id`: ORI `@id` (stable external key — ON CONFLICT DO UPDATE)
- `name`: ORI `name`
- `url`: ORI `url` or `original_url`
- `content`: ORI `text[]` joined, or NULL (triggers OCR via processor)
- `category`: `'municipal_doc'`
- `doc_classification`: civic type (`schriftelijke_vraag` etc.)
- `municipality`: `'rotterdam'`
- `source`: `'ori'`
- `meeting_id`: derived from ORI `was_generated_by` linkage (nullable)

---

## Workstream interactions

| WS | Interaction |
|---|---|
| **WS5a** (nightly pipeline) | WS11b is one-time batch; WS5a makes ongoing ingestion automatic. Run WS11 first. |
| **WS7** (OCR recovery) | Run WS7 on new docs BEFORE final embed pass — embeddings should be on recovered text. |
| **WS4** (MCP discipline) | After WS11a, MCP tools can add `doc_classification` filter. Add `doc_type` param to `zoek_raadshistorie` in v0.2.1. |
| **WS1** (GraphRAG) | WS11 new docs become graph nodes. Run WS11 before WS1 entity extraction. |

---

## v0.2.0 vs deferred

### v0.2.0 (now)
- Migration 0006 (municipality + source) ✅
- Civic type guard in document_processor ✅
- CIVIC_DOC_TYPES constant ✅
- WS11a backfill script ✅
- WS11b ingestion script (P0: schriftelijke_vraag + initiatiefnotitie) ✅
- municipality in Qdrant payload ✅

### v0.2.1
- WS11b P1: initiatiefvoorstel ingestion (run with `--include-p1`)
- MCP `doc_type` filter parameter (coordinate with WS4)
- ibabs fallback for 2025-2026 recents (ORI covers through mid-2025)

### v0.3+
- Multi-city MCP filter (`gemeente` parameter)
- Per-city ORI index discovery

---

## Success Criteria

- [ ] Migration 0006 applied (`municipality`, `source` columns exist)
- [ ] All 111 initiatiefnotities have `doc_classification = 'initiatiefnotitie'`
- [ ] All 522 initiatiefvoorstellen have `doc_classification = 'initiatiefvoorstel'`
- [ ] Schriftelijke vragen: ≥ 90% ORI coverage for 2018–2026 (target: ~2,700 new docs)
- [ ] All new docs embedded in Qdrant `notulen_chunks` with `municipality` payload field
- [ ] MCP `zoek_moties` (or `zoek_raadshistorie`) returns schriftelijke vragen when asked by topic
- [ ] Erik Verweij re-test: can retrieve initiatiefnotities and schriftelijke vragen by topic

## Verification queries

```sql
-- WS11a check
SELECT doc_classification, COUNT(*)
FROM documents
WHERE name ILIKE '%initiatiefnoti%'
GROUP BY 1;
-- Expected: 111 rows with doc_classification = 'initiatiefnotitie'

-- WS11b check
SELECT doc_classification, COUNT(*)
FROM documents
WHERE doc_classification IN ('schriftelijke_vraag', 'initiatiefnotitie', 'initiatiefvoorstel')
GROUP BY 1
ORDER BY 2 DESC;
-- Expected: schriftelijke_vraag ≥ 2700, initiatiefnotitie ≥ 100, initiatiefvoorstel ≥ 231

-- Civic type guard check (after running document_processor on a test WS11b doc)
SELECT doc_classification FROM documents WHERE source = 'ori' LIMIT 10;
-- Expected: civic types preserved (schriftelijke_vraag etc.), not overwritten with 'regular'

-- municipality field check
SELECT municipality, COUNT(*) FROM documents GROUP BY 1;
-- Expected: 'rotterdam' for all docs

-- Qdrant payload check (via Python)
-- from qdrant_client import QdrantClient
-- q = QdrantClient(url=...) 
-- results = q.scroll('notulen_chunks', limit=1, with_payload=True)
-- assert 'municipality' in results[0][0].payload
```

## Estimated Timeline

| Phase | Effort |
|---|---|
| Migration 0006 + run | 0.5h |
| WS11a dry-run + execute | 0.5 day |
| WS11b dry-run audit | 1h |
| WS11b P0 ingestion (schriftelijke_vraag ~2,756 docs) | 1.5 days |
| WS11b P0 ingestion (initiatiefnotitie, gap TBD) | 0.5 day |
| WS7 OCR pass on new docs | 2h (automated) |
| Embed + index (GPU run via document_processor) | 1 day |
| QA + Erik re-test | 0.5 day |
| **Total P0** | **~4 days** |
