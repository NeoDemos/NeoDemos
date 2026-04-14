# WS10 — Table-Rich Document Extraction (Docling Layout Pass)

## Status: PAUSED — Infrastructure done, full backfill deferred to post-v0.2

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

## Decision (2026-04-13): Defer Full Backfill

**Why paused:** After dry-run validation (50 largest + 50 smallest docs), the ROI picture became clear:
- Large docs (>500K chars): 44% pass rate — these are the high-value targets
- Small docs near the 100K boundary: ~8% pass rate — not worth the compute
- Per-doc processing time at the large end: ~70 min/doc even with MPS GPU

At ~70 min per large doc and ~44% pass rate, a full 655-unique-PDF backfill would take several days of continuous compute for perhaps 150-200 high-quality upgrades. The strategic priority (press/political recognition via WS1 GraphRAG enrichment) is better served by:
1. Running the ~20 highest-ROI candidates now (specific docs confirmed passing in dry-run)
2. Completing WS7 + WS11 + WS12 to unblock WS1
3. Returning to full WS10 backfill post-v0.2 when compute budget allows

**What's fully built and working:** classifier, converters with MPS GPU + parallel writes + content deduplication. The infrastructure is production-ready — this is purely a resource allocation decision.

**WS10 is no longer a WS1 blocker.** WS1 Phase A can start after WS7 + WS11 + WS12.

---

## Scope (verified 2026-04-12)

| Category | Count | Notes |
|---|---|---|
| WS10 table-rich candidates (rows) | **1,024** | Name-matched, >100K chars (refined identification query) |
| Unique PDFs after MD5 dedup | **655** | 370 duplicates = 36% — script propagates results to all copies |
| Clean but incomplete (TABLE_RICH) | **~550** | Clean text, needs layout-only extraction |
| Also garbled (GARBLED_TABLE_RICH) | **~105** | Needs OCR + layout extraction |
| Financial + table-rich overlap | 8 | Routed to WS2 FinancialDocumentIngestor |
| Confirmed dry-run passes (>500K chars) | **~27** | High-ROI curated target list (see below) |

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
   pipeline_options.accelerator_options = AcceleratorOptions(num_threads=4, device=AcceleratorDevice.MPS)
   ```

2. **Layout converter** — for TABLE_RICH docs (fast, ~3-5s/doc with MPS)
   ```python
   PdfPipelineOptions(do_ocr=False)
   pipeline_options.accelerator_options = AcceleratorOptions(num_threads=4, device=AcceleratorDevice.MPS)
   ```

3. **Financial converter** — for FINANCIAL docs (WS2)
   ```python
   PdfPipelineOptions(do_table_structure=True)
   pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
   pipeline_options.accelerator_options = AcceleratorOptions(num_threads=4, device=AcceleratorDevice.AUTO)
   ```

All converters use double-checked locking (`threading.Lock()`) to prevent duplicate model initialization in parallel runs. MPS accelerates DocLayoutYOLO + TableFormer neural net phases; Tesseract OCR subprocess remains CPU-only.

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
# Dry run, largest 50 docs first (default --sort desc)
python scripts/ws10_table_extraction.py --dry-run --limit 50

# Targeted run on the curated 20 high-ROI docs (TABLE_RICH, largest first)
python scripts/ws10_table_extraction.py --type table_rich --limit 20 \
  --workers 4 --skip-re-embed --sort desc

# Garbled variant (use fewer workers — OCR is more memory-intensive)
python scripts/ws10_table_extraction.py --type garbled_table_rich --limit 10 \
  --workers 2 --skip-re-embed

# Resume from checkpoint
python scripts/ws10_table_extraction.py --resume

# Full TABLE_RICH backfill (post-v0.2 when compute allows)
python scripts/ws10_table_extraction.py --type table_rich \
  --workers 4 --skip-re-embed --batch-size 10
```

**Key flags added in Phase 2:**
- `--workers N` — parallel Docling extraction (N workers run concurrently; DB writes serialized)
- `--sort desc|asc` — sort candidates by content length (default: `desc` = largest first)
- Content deduplication built-in: MD5 dedup in `get_candidates()`, bulk-UPDATE propagates to all duplicate rows after successful extraction

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

## Phase 2 Optimizations (added 2026-04-13)

| Optimization | Status | Detail |
|---|---|---|
| MPS GPU acceleration | ✅ done | All 3 converters now use `AcceleratorDevice.MPS` / `AUTO`; double-checked locking added |
| Content deduplication | ✅ done | `DISTINCT ON (MD5(content))` in `get_candidates()`; `PROPAGATE_DUPLICATES_QUERY` after each write |
| Parallel live writes | ✅ done | `ThreadPoolExecutor` workers (Docling only) + serialized `_write_to_db()` (lock 42 + writes) |
| `--sort` flag | ✅ done | Default `desc` (largest first); dry-run validated 44% pass rate on large docs |
| Dry-run validated | ✅ done | 50 largest + 50 smallest; 44%/8% pass rates confirm large-doc focus is correct |

**Speed estimate with Phase 2 optimizations:**
- MPS neural net phases: ~3-5x vs CPU
- 4 parallel workers: ~3-4x
- Content dedup (36% duplicates): ~1.5x
- Combined: ~8-12x vs original sequential CPU baseline (~3-4 docs/hr → ~30-40 docs/hr)

## Already Done

- `1021205` (`13gr3031k1 MER Deelrapport Verkeer`) — updated 2026-04-12, +162K chars, verified in DB
- `pipeline/document_classifier.py` — DocumentClassifier with 7-type decision tree
- `pipeline/docling_converters.py` — 3 lazy-cached converter factories + MPS GPU + threading lock
- `services/document_processor.py` — classifier-routed pipeline (replaces ad-hoc garbled check)
- `scripts/ws10_table_extraction.py` — batch backfill script with parallel writes, dedup, sort flag
- `alembic/versions/20260412_0005_add_doc_classification_column.py` — migration

## Curated High-ROI Target List (verified from DB, 2026-04-13)

Queried live from DB (`documents` where `>200K chars`, `ocr_quality NOT IN (good, degraded)`,
`url IS NOT NULL`, deduped by MD5). Sorted by content length descending.
Total: **21 unique PDFs** (7 TABLE_RICH + 14 GARBLED_TABLE_RICH).
Duplicate rows that will auto-update via `PROPAGATE_DUPLICATES_QUERY`: noted in `dups` column.

### TABLE_RICH — layout-only pass (~3-5s/doc with MPS)

Run first: faster, no OCR, 4 workers safe.

| doc_id | chars | dups | Document name |
|---|---|---|---|
| `6118642` | 2,108K | 1 | [24bb001736] Bijlagen bundel ontwerpbestemmingsplan Zomerhofkwartier |
| `4921772` | 1,544K | 3 | 16bb4133 Ontwerpbestemmingsplan Parapluherziening geluidzone RTHA |
| `4076440` | 849K | 2 | 09gr671 Evaluatierapport gebiedsvisie Bonnenpolder (Van de heer Vreugdenhil) |
| `1021208` | 774K | 1 | 13gr3031k4 MER Deelrapport Externe Veiligheid - versie mei 2013 |
| `6089432` | 716K | 2 | 21bb1050 Milieueffectrapport windpark Maasvlakte 2 |
| `1021221` | 704K | 1 | 13gr3031k Hoofdrapport MER - versie mei 2013 |
| `6095997` | 662K | 1 | 21bb11362 Akoestisch onderzoek geluid op gevels 20210728v2 |

### GARBLED_TABLE_RICH — OCR + layout pass (~8-15s/doc)

Run after TABLE_RICH. Use `--workers 2` (OCR is more memory-intensive).

| doc_id | chars | dups | Document name |
|---|---|---|---|
| `6121453` | 2,300K | 3 | [24bb003180] Bijlage 1 ontwerpbestemmingsplan Mallegat |
| `c4162cd6-6fad-4821-86dc-2c4d0a81c615` | 1,541K | 1 | [24bb001887] Bijlage bestemmingsplan Hollands Tuin 7772 MB |
| `6084617` | 1,396K | 1 | 20bb5311 Bijlage 1 Bestemmingsplan Piekstraat Punt |
| `1021207` | 1,294K | 1 | 13gr3031k3 MER Deelrapport Lucht - versie mei 2013 |
| `6084625` | 981K | 1 | 20bb5718 Bijlage 1d Ontwerpbestemmingsplan Parkstad Zuid |
| `6094654` | 915K | 1 | 21bb6800 Bijlage rapport Broze bedoelingen |
| `244806` | 854K | 1 | 10gr2747a Rapport Ordeverstoring en groepsgeweld bij evenementen |
| `6096469` | 783K | 1 | 21bb12981 Bijlage 2 Rotterdams Omgevingseffectrapport (ROER) |
| `6121641` | 772K | 1 | [24bb004663] Bijlagen bij ontwerpbestemmingsplan Ostadehof |
| `6086727` | 757K | 1 | 20bb10298 Bijlage 1 ontwerpbestemmingsplan Groenenhagen-Tuinenhoven |
| `6118401` | 750K | 1 | [24bb002444] Geactualiseerd akoestisch onderzoek wegverkeerslawaai |
| `6099980` | 749K | 1 | [22bb003826] Bijlage 1 bestemmingsplan Nicolaasschool met bijlagen |
| `6090556` | 734K | 1 | 21bb616 Bijlage 1 ontwerpbestemmingsplan Tarwewijk |
| `6114141` | 697K | **8** | [23bb007318] Bijlage 3d MER MIRT Oeververbinding ← **8 dups, highest bulk ROI** |

### Run commands

```bash
# Clear any stale checkpoint first
rm -f data/pipeline_state/ws10_table_extraction_checkpoint.json

# Step 1: TABLE_RICH — 7 docs, layout-only, 4 workers
python scripts/ws10_table_extraction.py --type table_rich --limit 7 \
  --workers 4 --skip-re-embed --sort desc

# Step 2: GARBLED_TABLE_RICH — 14 docs, OCR+layout, 2 workers
python scripts/ws10_table_extraction.py --type garbled_table_rich --limit 14 \
  --workers 2 --skip-re-embed --sort desc

# Step 3: Batch re-embed after both complete
# (use document_processor Phase 2 or direct re-embed loop)
```

## Remaining Work

- [x] Run Alembic migration on production — done (0011 at head as of 2026-04-14)
- [ ] Targeted 21-doc run on curated high-ROI candidates — **run BEFORE Phase 4 (migrate_embeddings)**
- [ ] Spot-check 3-5 docs post-run: verify chunk counts and content length increases
- [ ] Full backfill: **deferred to post-v0.2** — TABLE_RICH (655 unique PDFs), then GARBLED_TABLE_RICH
- [ ] Consider `vergelijk_tabelgegevens` MCP tool for cross-document table comparison (post-backfill)

## Embedding Integration with WS11 (2026-04-14)

WS10 uses `--skip-re-embed` so rechunked docs land in `document_chunks` with `embedded_at IS NULL`.
**Run WS10 curated batch BEFORE `scripts/migrate_embeddings.py` (Phase 4 of WS11).**

`migrate_embeddings.py` scans `WHERE id > checkpoint AND embedded_at IS NULL` in ascending ID order.
WS10 chunks always get fresh SERIAL IDs (above the checkpoint), so they're picked up in the same
Phase 4 pass — no separate embedding step needed.

Sequence:
```
WS11 Phase 3 complete → WS11 Phase 5 (rekey VN) → WS10 curated batch → WS11 Phase 4 (migrate_embeddings)
```

WS10 runs Docling locally (MPS GPU). Phase 4 calls Nebius API via SSH tunnel. They do not conflict
and can overlap: WS10 creates chunks, Phase 4 embeds them as it scrolls upward through IDs.

## Contact

Dennis Tak — raised during WS7 pilot, 2026-04-12.
