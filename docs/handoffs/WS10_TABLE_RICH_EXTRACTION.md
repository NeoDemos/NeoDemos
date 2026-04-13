# WS10 — Table-Rich Document Extraction (Docling Layout Pass)

## Status: IN PROGRESS

## What This Is

During WS7 OCR recovery we identified a **second, distinct damage type** beyond garbled OCR:
large technical documents (bestemmingsplannen, MER reports, bijlagen) where `pypdf`'s text-layer
extraction is readable but **incomplete** — it misses table cells, figure captions, sidebars, and
column layouts that Docling's visual layout analysis captures.

**This is not an OCR problem. It is a content completeness problem.**

Proof-of-concept: `13gr3031k1 MER Deelrapport Verkeer - versie mei 2013` (doc ID `1021205`)
- pypdf extracted: 374,477 chars (readable, 0 garbled runs)
- Docling extracted: 536,648 chars (+162K, 0 garbled runs, 99.96% clean)
- The 162K extra content came from traffic intensity tables, appendix tables, and figure captions
- Already updated in DB as of 2026-04-12

## Scope (verified 2026-04-12)

| Category | Count | Notes |
|---|---|---|
| WS10 table-rich candidates | **1,336** | Name-matched, >100K chars |
| Clean but incomplete (TABLE_RICH) | **713 (53%)** | Clean text, needs layout-only extraction |
| Also garbled (GARBLED_TABLE_RICH) | **623 (47%)** | Needs OCR + layout extraction |
| Financial + table-rich overlap | 8 | Routed to WS2 FinancialDocumentIngestor |

### WS7 overlap note

Using WS7's proper identification criteria (camelCase transitions + BM25 miss + ligature + low
clean ratio), only 623 of the 1,336 candidates are also garbled. The majority (713) are clean
text that just needs Docling layout extraction — much faster (~3s/doc vs ~8s/doc).

## Architecture: Unified Document Classification

WS10 introduces a `DocumentClassifier` that routes ALL documents to the correct treatment.
This replaces the ad-hoc `_is_garbled_ocr()` check in `document_processor.py`.

### Decision tree (order matters — first match wins)

```
1. TRANSCRIPT           doc_id.startswith("transcript_")
2. FINANCIAL            name ~ begroting|jaarstuk|jaarrekening|voorjaarsnota|10-maands
   2a. + table-rich pattern + >100K → FINANCIAL_TABLE_RICH (WS2 pipeline)
   2b. else → FINANCIAL (WS2 pipeline)
3. GARBLED              _is_garbled_ocr(content[:5000])
   3a. + table-rich pattern + >100K → GARBLED_TABLE_RICH
   3b. else → GARBLED_OCR
4. TABLE_RICH           name ~ bestemmingsplan|MER|deelrapport|bijlage|toelichting + >100K
5. REGULAR              everything else
```

### Treatment per type

| Type | Docling? | Config | Quality Gate |
|---|---|---|---|
| TRANSCRIPT | No | SmartIngestor speaker-aware | N/A |
| FINANCIAL / FINANCIAL_TABLE_RICH | Yes | TableFormer ACCURATE | WS2 pipeline |
| GARBLED_OCR | Yes | force_full_page_ocr=True, Tesseract nld+eng | clean_pct up, garbled down |
| GARBLED_TABLE_RICH | Yes | force_full_page_ocr=True (handles both) | clean_pct up + length >= 110% |
| TABLE_RICH | Yes | do_ocr=False (layout only, ~3s/doc) | length >= 110%, clean_pct stable |
| REGULAR | No | SmartIngestor 4-tier | N/A |

## Files Created/Modified

| File | Action | Description |
|---|---|---|
| `pipeline/document_classifier.py` | **NEW** | DocumentClassifier + DocType enum + DocumentClassification dataclass |
| `pipeline/docling_converters.py` | **NEW** | 3 lazy-cached Docling converter factories (OCR, layout, financial) |
| `services/document_processor.py` | **MODIFIED** | Replaced Phase 0 with classifier-based routing |
| `scripts/ws10_table_extraction.py` | **NEW** | Batch backfill script for 1,336 table-rich candidates |
| `alembic/versions/20260412_0005_add_doc_classification_column.py` | **NEW** | Migration for `doc_classification` column |

## Target Document Profile

Documents that benefit from Docling layout extraction share these traits:

- Content length **> 100K chars** (large docs with substantial table content)
- Document name matches: `bestemmingsplan`, `MER`, `deelrapport`, `bijlage`, `verslag`, `rapportage`
- `ocr_quality IS NULL` (not yet processed by WS7 OCR recovery)
- Has a downloadable PDF URL
- `pypdf` text is **already clean** (clean_pct > 99%) but content length is **suspiciously short**
  relative to the PDF file size (signals tables were skipped)

## Identification Query

```sql
-- Candidate table-rich documents for Docling layout extraction
SELECT
    d.id, d.name, d.url,
    LENGTH(d.content) AS content_chars,
    pg_size_pretty(LENGTH(d.content)::bigint) AS content_size
FROM documents d
WHERE d.content IS NOT NULL
  AND LENGTH(d.content) > 100000          -- substantial docs only
  AND (d.ocr_quality IS NULL OR d.ocr_quality NOT IN ('good', 'degraded'))
  AND d.url IS NOT NULL
  AND (
      LOWER(d.name) ~ 'bestemmingsplan|MER|deelrapport|milieu.?effect|havenbestemmings'
      OR LOWER(d.name) ~ 'bijlage.*rapport|verslag.*hoorzitting|toelichting'
  )
  AND d.id NOT LIKE 'transcript_%'
ORDER BY LENGTH(d.content) DESC
LIMIT 500;
```

## Docling Converters

Three lazy-cached converter configurations in `pipeline/docling_converters.py`:

1. **OCR converter** — for GARBLED_TABLE_RICH docs
   ```python
   PdfPipelineOptions(do_ocr=True, ocr_options=TesseractCliOcrOptions(
       lang=["nld", "eng"], force_full_page_ocr=True
   ))
   ```

2. **Layout converter** — for TABLE_RICH docs (fast, ~3s/doc)
   ```python
   PdfPipelineOptions(do_ocr=False)
   ```

3. **Financial converter** — for FINANCIAL docs (WS2)
   ```python
   PdfPipelineOptions(do_table_structure=True)
   pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
   ```

## Quality Gate

Accept the Docling output only if:
1. New text length >= 110% of original (must add meaningful content, not just reformat)
2. Clean char ratio does not decrease
3. Garbled runs do not increase (Docling layout analysis should produce 0)

Reject and keep original if Docling output is shorter or garbled — some PDFs are
scanned images without a text layer and need OCR, not layout analysis.

## Batch Backfill Script

`scripts/ws10_table_extraction.py`:

```bash
# Dry run on first 5 documents
python scripts/ws10_table_extraction.py --dry-run --limit 5

# Process TABLE_RICH documents only (layout-only, fast)
python scripts/ws10_table_extraction.py --type table_rich --limit 50

# Process GARBLED_TABLE_RICH (full OCR, slower)
python scripts/ws10_table_extraction.py --type garbled_table_rich --batch-size 10

# Resume from checkpoint
python scripts/ws10_table_extraction.py --resume

# Extract without re-embedding (batch embed later)
python scripts/ws10_table_extraction.py --skip-re-embed --limit 100
```

## Safety

- Uses advisory lock 42 (same as WS7)
- Backup originals to `staging.ocr_recovery_originals` (reuse existing table, ON CONFLICT DO NOTHING)
- Same delete-then-commit-then-rechunk pattern as WS7 to avoid SmartIngestor deadlocks
- `--skip-re-embed` for the main pass; batch re-embed afterwards via document_processor Phase 2
- Checkpoint-resumable to `data/pipeline_state/ws10_table_extraction_checkpoint.json`

## Automated Pipeline Integration

The 15-min `document_processor.py` APScheduler job now uses `DocumentClassifier` for all
new documents:
- REGULAR → SmartIngestor as before
- FINANCIAL → skipped (routed to WS2 pipeline)
- GARBLED_OCR / GARBLED_TABLE_RICH → Docling OCR recovery + SmartIngestor
- TABLE_RICH → Docling layout extraction + SmartIngestor

**New docs are handled automatically** — only the backfill of existing 1,336 docs is manual.

## Workstream Dependencies

| WS | Status | Risk | Mitigation |
|---|---|---|---|
| WS2 Financial | SHIPPED | MEDIUM-HIGH | Financial docs route to existing WS2 pipeline unchanged |
| WS5a Nightly | Not started | MEDIUM | Classifier runs read-only before chunking; all writes use lock 42 |
| WS1 GraphRAG | Not started | LOW | Run WS10 before WS1 Phase A enrichment |
| WS6 Summarization | Not started | LOW | Operates post-ingest |
| WS7 OCR Recovery | In progress | NONE | Complementary; different target docs |

## Already Done

- `1021205` (`13gr3031k1 MER Deelrapport Verkeer`) — updated 2026-04-12, +162K chars
- `pipeline/document_classifier.py` — DocumentClassifier with 7-type decision tree
- `pipeline/docling_converters.py` — 3 lazy-cached converter factories
- `services/document_processor.py` — classifier-routed pipeline (replaces ad-hoc garbled check)
- `scripts/ws10_table_extraction.py` — batch backfill script
- `alembic/versions/20260412_0005_add_doc_classification_column.py` — migration

## Remaining Work

- [ ] Run Alembic migration on production
- [ ] `--dry-run --limit 50` to verify classification accuracy
- [ ] Run on 20 docs, spot-check quality
- [ ] Full backfill: TABLE_RICH first (713 docs, fast), then GARBLED_TABLE_RICH (623 docs)
- [ ] Verify chunk counts and content length increases
- [ ] Consider `vergelijk_tabelgegevens` MCP tool for cross-document table comparison

## Contact

Dennis Tak — raised during WS7 pilot, 2026-04-12.
