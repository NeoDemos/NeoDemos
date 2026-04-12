# WS10 — Table-Rich Document Extraction (Docling Layout Pass)

## Status: READY TO START

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

## Approach

Use **Docling with `PdfPipelineOptions(do_ocr=False)`** — we do NOT want OCR here, we want
Docling's layout analysis to extract structured content from the native PDF text layer.
This is much faster than force_full_page_ocr (~3s/doc vs ~8s/doc).

```python
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat

pipeline_options = PdfPipelineOptions(do_ocr=False)  # layout-only, no OCR
converter = DocumentConverter(
    format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
)
```

## Quality Gate

Accept the Docling output only if:
1. New text length >= 110% of original (must add meaningful content, not just reformat)
2. Clean char ratio does not decrease
3. Garbled runs do not increase (Docling layout analysis should produce 0)

Reject and keep original if Docling output is shorter or garbled — some PDFs are
scanned images without a text layer and need OCR, not layout analysis.

## Script to Write

`scripts/ws10_table_extraction.py` — modelled on `scripts/ocr_recovery.py` but with:
- `do_ocr=False` (layout-only Docling)
- Tighter length improvement threshold (110% minimum, not 50%)
- `--doc-type` filter defaulting to the patterns above
- Queue table: `staging.table_extraction_queue`
- Metadata tag: `{"recovery": "docling_table_extraction_ws10"}`

## Safety

- Use advisory lock 42 (same as WS7)
- Backup originals to `staging.ocr_recovery_originals` (reuse existing table, ON CONFLICT DO NOTHING)
- Same delete-then-commit-then-rechunk pattern as WS7 to avoid SmartIngestor deadlocks
- `--skip-re-embed` for the main pass; batch re-embed afterwards

## Estimated Scope

Run the identification query above to get the exact count. Expected: 200–600 documents
(bestemmingsplannen + MER reports are a small but high-value subset of the 90K corpus).

## Already Done

- `1021205` (`13gr3031k1 MER Deelrapport Verkeer`) — updated 2026-04-12, +162K chars

## Dependencies

- WS7 OCR recovery should complete first (so `ocr_quality` column is populated for
  actually-garbled docs — this prevents WS10 running Docling on already-recovered docs)
- Docling installed: `pip install docling` (already in requirements.txt)
- No new DB migrations needed

## Contact

Dennis Tak — raised during WS7 pilot, 2026-04-12.
