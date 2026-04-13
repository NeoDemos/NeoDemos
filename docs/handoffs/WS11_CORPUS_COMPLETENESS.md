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

| Doc type | In DB (after WS11a+b) | ORI | Gap | Priority |
|---|---|---|---|---|
| schriftelijke_vraag | 3,851 | 3,174 (MediaObject+Report) | **0** ✅ ingested | **P0** |
| initiatiefnotitie | 111 | 78 | covered (DB has more from iBabs) | **P0** |
| initiatiefvoorstel | 522 | 333 | covered (DB has more from iBabs) | P1 |
| raadsvoorstel | 2,762 classified | 2,322+319 | **473 ORI gap** (v0.2.1) | P1 |
| brief_college | 3,852 classified | — | ORI gap TBD | P1 |
| afdoeningsvoorstel | 1,702 classified | — | ORI gap TBD | P1 |
| toezegging | 1,580 classified | 2,927+431 | **869 ORI gap** (v0.2.1) | P1 |
| motie | 12,822 classified | 8,177+232 | covered (DB has more from meeting bundles) | P2 |
| amendement | 734 classified | 519+86 | covered | P2 |

**DB classification state (2026-04-13 after WS11a+b):**

| doc_classification | count |
|---|---|
| NULL | 61,093 |
| motie | 12,822 |
| brief_college | 3,852 |
| schriftelijke_vraag | 3,851 |
| raadsvoorstel | 2,762 |
| afdoeningsvoorstel | 1,702 |
| toezegging | 1,580 |
| amendement | 734 |
| initiatiefvoorstel | 522 |
| initiatiefnotitie | 111 |
| regular/table_rich/financial | 12 (pipeline-routing residual) |

**About the remaining ~26,754 NULL docs (updated 2026-04-13 after P3 backfill):**
These are genuinely unidentifiable documents — no clean name pattern to classify them:
- BB-besluitenboek entries (~1,400) — College board decisions in BB-format without distinct type name
- Inspreekbijdragen — public speaking transcripts filed without a consistent prefix
- GR-stukken — council filing references with numeric/code names
- Monitor-rapporten, spreektijdentabellen, etc. that were already classified as `monitor_rapport` / `spreektijdentabel`
- Remaining ~26,754 are truly unclassifiable from name alone (no keyword match)

NULL now means "genuinely unidentifiable" — not "we haven't classified it yet". All 30 named doc types are fully backfilled.

**Key audit findings (2026-04-13 expanded audit):**
- ORI-API `@type` field maps directly as keyword (no `.keyword` subfield needed).
- `classification` field requires `.keyword` subfield for term queries.
- ORI index `ori_rotterdam_20250629013104` is Rotterdam-only (70,148 searchable docs). `_cat/indices` reports ~503K but that includes deleted Lucene segments.
- `brief_college` and `afdoeningsvoorstel` were missing from original WS11 scope — found via DB name-pattern audit.

See `docs/ws11_scope.json` for per-year counts and ORI API query templates.

---

## Sub-workstreams

### WS11a — Metadata backfill (DONE 2026-04-13)

Fix `doc_classification` on documents already in DB with `doc_classification = NULL`.

**Script:** `scripts/ws11a_classify_existing_docs.py`

```bash
# Dry-run — see what would change
python scripts/ws11a_classify_existing_docs.py

# Execute all types
python scripts/ws11a_classify_existing_docs.py --execute

# Execute only P1 additions (raadsvoorstel, brief_college, afdoeningsvoorstel, toezegging)
python scripts/ws11a_classify_existing_docs.py --execute --only-new

# Skip motie/amendement (P2)
python scripts/ws11a_classify_existing_docs.py --execute --skip-p2
```

**What it does:**
- Sets `doc_classification` by name-pattern match (ILIKE)
- Logs all changes to `logs/ws11a_classification.log`
- Dry-run is default (safe to run repeatedly for auditing)

**Results (as of 2026-04-13, all runs):**

| Type | Docs classified | Priority | Run |
|---|---|---|---|
| initiatiefnotitie | 111 | P0 | WS11a initial |
| initiatiefvoorstel | 522 | P0 | WS11a initial |
| schriftelijke_vraag | 3,558 | P0 | WS11a initial |
| motie | 12,982 | P2 | WS11a initial |
| amendement | 764 | P2 | WS11a initial |
| raadsvoorstel | 2,762 | P1 | WS11a P1 expansion |
| brief_college | 3,852 | P1 | WS11a P1 expansion |
| afdoeningsvoorstel | 1,702 | P1 | WS11a P1 expansion |
| toezegging | 1,580 | P1 | WS11a P1 expansion |
| notulen | 1,177 | P3 | WS11a P3 expansion |
| verslag | 3,102 | P3 | WS11a P3 expansion |
| agenda | 3,651 | P3 | WS11a P3 expansion |
| annotatie | 2,008 | P3 | WS11a P3 expansion |
| adviezenlijst | 1,370 | P3 | WS11a P3 expansion |
| besluitenlijst | 1,077 | P3 | WS11a P3 expansion |
| ingekomen_stukken | 438 | P3 | WS11a P3 expansion |
| spreektijdentabel | 1,094 | P3 | WS11a P3 expansion |
| transcript | 719 | P3 | WS11a P3 expansion |
| rapport | 3,297 | P3 | WS11a P3 expansion |
| notitie | 887 | P3 | WS11a P3 expansion |
| presentatie | 652 | P3 | WS11a P3 expansion |
| monitor_rapport | 895 | P3 | WS11a P3 expansion |
| planning | 898 | P3 | WS11a P3 expansion |
| bijlage | 10,129 | P3 | WS11a P3 expansion |
| memo | 30 | P3 | WS11a P3 expansion |
| begroting | 2,399 | P3 | WS11a P3 expansion |
| jaarstukken | 139 | P3 | WS11a P3 expansion |
| grondexploitatie | 338 | P3 | WS11a P3 expansion |
| voorbereidingsbesluit | 332 | P3 | WS11a P3 expansion |
| rekenkamer | 162 | P3 | WS11a P3 expansion |
| **Total backfilled** | **~62,627** | | |
| **Remaining NULL** | **~26,754** | — | genuinely unidentifiable |

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

### v0.2.0 (done)
- Migration 0006 (municipality + source) ✅
- Civic type guard in document_processor ✅
- CIVIC_DOC_TYPES constant — 30 named types, all protected from processor overwrite ✅
- WS11a backfill P0: initiatiefnotitie, initiatiefvoorstel, schriftelijke_vraag, motie, amendement ✅
- WS11a backfill P1: raadsvoorstel, brief_college, afdoeningsvoorstel, toezegging ✅
- WS11a backfill P3: all 21 meeting/procedural/financial types ✅ (62,627 docs classified total)
- WS11b ORI ingestion P0: 293 schriftelijke vragen ✅
- WS11b ORI ingestion P1: raadsvoorstel (324), toezegging (436), brief_college (1,163), afdoeningsvoorstel (5) — running ⏳
- municipality in Qdrant payload ✅
- NULL = genuinely unidentifiable only (~26,754, 30% of corpus) ✅

### v0.2.1
- WS11b P1: raadsvoorstel ORI gap (~473 docs), toezegging ORI gap (~869 docs) — run with `--include-p1`
- WS11b P1: initiatiefvoorstel ORI ingestion (gap confirmed small ~102, can defer)
- MCP `doc_type` filter parameter (coordinate with WS4)
- ibabs fallback for 2025-2026 recents (ORI covers through mid-2025)
- ORI audit for brief_college and afdoeningsvoorstel gaps

### v0.3+
- Multi-city MCP filter (`gemeente` parameter)
- Per-city ORI index discovery

---

## Success Criteria

- [x] Migration 0006 applied (`municipality`, `source` columns exist)
- [x] All 111 initiatiefnotities have `doc_classification = 'initiatiefnotitie'`
- [x] All 522 initiatiefvoorstellen have `doc_classification = 'initiatiefvoorstel'`
- [x] Schriftelijke vragen: 3,851 in DB (ORI coverage complete for existing index)
- [x] All 30 named doc types classified — NULL means genuinely unidentifiable only
- [x] raadsvoorstel, brief_college, afdoeningsvoorstel, toezegging, begroting, rekenkamer etc. all labelled
- [ ] WS11b P1 ingestion complete (1,928 docs — running ⏳)
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
