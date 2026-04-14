# WS11 — Corpus Completeness 2018–2026

> **Status:** `in progress` — WS11b P1 ingest running; embed pass pending after WS7 finishes
> **Owner:** `dennis`
> **Priority:** 1 (blocks WS1 enrichment quality)
> **Parallelizable:** yes (WS11a and WS11b can run in parallel)

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
