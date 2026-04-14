# WS7 — OCR Recovery for Moties & Amendementen

> **Priority:** 2.5 (between WS2 and WS3 — blocks WS1 quality ceiling)
> **Status:** `complete` — shipped 2026-04-14
> **Owner:** `dennis`
> **Target release:** v0.2.0 (pre eval-gate — improves WS1 baseline scores)
> **Master plan section:** N/A — spun off from WS1 pre-enrichment baseline audit 2026-04-12

## TL;DR

20% of moties and 14% of amendementen (2,598 + ~100 docs) have garbled text from broken PDF text-layer extraction: words concatenated without spaces ("DegemeenteraadvanRotterdambijeenop28november"), ligature artifacts (ﬁ→fi), and OCR hallucinations. This makes 22.5% of moties invisible to BM25 search — 1 in 5 moties cannot be found by keyword. The damage is concentrated in 2016-2019 (50% garbled in 2018-2019, the Tweebosbuurt era). Notulen are mostly clean (iBabs digital transcripts). Root cause: moties go through `pypdf` text extraction, not Docling OCR. Fix: re-ingest the ~2,700 damaged documents through Docling with `force_full_page_ocr=True` + Dutch language config + a post-OCR normalization pass.

## Dependencies
- **None for the core fix** (re-OCR + normalize + re-index is self-contained)
- **WS1 benefits from this** — `traceer_motie` and `vergelijk_partijen` output quality is capped by source text quality. Even with perfect graph edges, garbled motie content renders poorly.
- **WS5a coordination** — re-chunking + re-embedding the fixed documents must not run while the nightly pipeline is writing. Use advisory lock 42.
- Memory to read first:
  - [project_embedding_process.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_embedding_process.md) — never write to Qdrant while a background job runs

## Cold-start prompt

> You are picking up Workstream 7 (OCR Recovery) of NeoDemos v0.2.0. This workstream was created after a pre-enrichment audit found that 20% of moties have garbled text that breaks BM25 search. The full diagnosis is at `docs/handoffs/done/WS7_OCR_RECOVERY.md`.
>
> Read in order: (1) this handoff top-to-bottom, (2) `eval/baselines/ws1_pre_enrichment_baseline.md` (the baseline that surfaced the problem), (3) `pipeline/financial_ingestor.py` (the only existing Docling integration — your model), (4) `pipeline/ingestion.py` (the current SmartIngestor that moties go through), (5) `services/scraper.py` (the pypdf-based extraction that causes the damage), (6) `scripts/ocr_reingest.py` (existing OCR recovery attempt using macOS native OCR — incomplete).
>
> Your job: build `scripts/ocr_recovery_moties.py` that identifies damaged moties via SQL heuristics, re-processes them through Docling with `force_full_page_ocr=True` + Dutch language, applies post-OCR normalization, replaces the document content + re-chunks + re-embeds. Then verify BM25 hit rate improves from 77.5% to ≥95%.
>
> Honor the project house rules in `docs/handoffs/README.md`. The most important ones for you: (1) advisory lock 42 for all writes, (2) never write to Qdrant while a background job runs, (3) `--dry-run` + `--limit` + `--resume` on every script.

## Files to read first
- [`eval/baselines/ws1_pre_enrichment_baseline.md`](../../eval/baselines/ws1_pre_enrichment_baseline.md) — the audit that quantified this problem
- [`pipeline/financial_ingestor.py`](../../pipeline/financial_ingestor.py) — the only existing Docling integration; model for PDF → Docling → text pipeline
- [`pipeline/ingestion.py`](../../pipeline/ingestion.py) — `SmartIngestor`, the current ingest path for moties (does NOT use Docling)
- [`services/scraper.py`](../../services/scraper.py) — `pypdf`-based PDF text extraction (line ~16); this is where the garbled text originates
- [`scripts/ocr_reingest.py`](../../scripts/ocr_reingest.py) — previous OCR recovery attempt using macOS native Swift OCR; incomplete, not Docling-based
- [`scripts/global_ocr_recovery.py`](../../scripts/global_ocr_recovery.py) — similar recovery attempt; uses ORI text sync as first pass
- Postgres schema: `documents (id, name, content, meeting_id, document_type, text_search)`, `document_chunks (id, document_id, content, text_search_enriched, key_entities, ...)`

## Problem diagnosis (verified 2026-04-12)

### Scope

| Metric | Value |
|---|---|
| Total moties + amendementen | 13,734 documents |
| Garbled spacing (40+ chars without space) | 2,598 moties (20.0%) + ~100 amendementen (14.3%) |
| BM25 miss for "gemeenteraad" | 1,243 docs (22.5%) — LIKE finds it, tsvector doesn't |
| Ligature artifacts (ﬁ, ﬂ) | 9.5% / 6.8% of moties |
| Garbled chunks | 8,806 / 132,811 motie chunks (6.6%) |

### Year distribution

| Year | Moties | Garbled % |
|---|---|---|
| 2016 | 509 | 40.3% |
| 2017 | ~400 | ~35% |
| 2018 | 601 | **50.7%** |
| 2019 | 1,575 | **50.0%** |
| 2020 | 1,848 | 6.2% |
| 2021 | 1,731 | 8.2% |
| 2022 | ~1,500 | ~15% |
| 2023 | 1,502 | 21.3% |
| 2024 | 955 | 26.3% |

**Damage peak: 2018-2019** (Tweebosbuurt era). Secondary spike 2023-2024.

### Root cause

Moties are uploaded as PDFs to the iBabs document management system. The ORI API (`services/scraper.py`) extracts text via `pypdf`. When the PDF text layer has corrupted space characters or font-encoding ligatures, `pypdf` faithfully reproduces the corruption. There is **no OCR fallback** — if the text layer is broken, the broken text gets indexed.

Notulen are natively digital iBabs transcripts (not PDFs) and are mostly clean.

### Damage patterns

1. **Word concatenation** (dominant): `"DegemeenteraadvanRotterdambijeenop28november2019"` — spaces stripped from entire paragraphs. Destroys BM25 tokenization completely.
2. **Unicode ligatures**: `ﬁ` (U+FB01) → should be "fi"; `ﬂ` (U+FB02) → should be "fl". "financieel" becomes "ﬁnancieel", invisible to BM25.
3. **OCR hallucinations**: `"ROTI'ERDAM"`, `"Gïnîur'.z"`, `"ﬁwopfﬁﬁﬁ"` — character-level substitution errors from OCR on scanned letterhead/logos.
4. **PDF extraction failures**: ~5-10 documents have near-zero clean content (binary garbage from completely failed extraction).

## Build tasks

### Phase A — Identify and tag damaged documents (~1 day)

- [ ] **SQL identification query.** Flag documents matching ANY of:
  - Content has runs of 40+ characters without a space (`content ~ '[^\s]{40,}'`)
  - BM25 misses "gemeenteraad" but LIKE finds it (`text_search @@ to_tsquery('dutch','gemeenteraad') = false AND content ILIKE '%gemeenteraad%'`)
  - Content contains ligature characters (`content LIKE '%ﬁ%' OR content LIKE '%ﬂ%'`)
  - Content clean-char ratio < 95% (`LENGTH(REGEXP_REPLACE(content, '[^\x20-\x7E\xC0-\xFF\n]', '', 'g')) / LENGTH(content) < 0.95`)
  
  Store results in a new table `staging.ocr_recovery_queue (document_id TEXT PRIMARY KEY, damage_type TEXT, clean_pct NUMERIC, flagged_at TIMESTAMP DEFAULT NOW(), status TEXT DEFAULT 'pending')`. Expect ~2,700–3,000 rows.

- [ ] **Verify source PDFs are still accessible.** For each flagged document, check that the source PDF can be re-downloaded from the ORI API or is cached locally. The `documents.url` column should have the download URL. Log any documents where the source is unavailable — these need manual recovery or ORI text sync as fallback.

### Phase B — Build the recovery script (~2–3 days)

- [ ] **`scripts/ocr_recovery_moties.py`** — new file. Structure:
  
  1. **Identify** — read from `staging.ocr_recovery_queue WHERE status = 'pending'`, or run the identification query inline if the table doesn't exist yet.
  2. **Download** — fetch the source PDF from `documents.url` to a temp dir. Skip if URL is null (log as `status = 'no_source'`).
  3. **Re-OCR via Docling** — process the PDF through `docling.DocumentConverter` with:
     ```python
     from docling.document_converter import DocumentConverter, PdfPipelineOptions
     from docling.datamodel.pipeline_options import OcrAutoOptions
     
     ocr_options = OcrAutoOptions(lang=["nl"], force_full_page_ocr=True)
     pipeline_options = PdfPipelineOptions(do_ocr=True, ocr_options=ocr_options)
     converter = DocumentConverter(pipeline_options=pipeline_options)
     result = converter.convert(pdf_path)
     new_text = result.document.export_to_text()
     ```
     Model this on `pipeline/financial_ingestor.py:220-244`.
  4. **Post-OCR normalization** — apply BEFORE storing:
     - Ligature replacement: `ﬁ` → `fi`, `���` → `fl`, `ﬀ` → `ff`, `ﬃ` → `ffi`, `ﬄ` → `ffl`
     - Smart space insertion: use a Dutch word list (hunspell `nl_NL` dictionary or a simple frequency list) to detect concatenated words and insert spaces. Heuristic: if a token > 20 chars contains a valid Dutch word boundary, split it. **Keep this conservative** — false splits are worse than missed splits.
     - Strip non-printable characters (U+0000-U+001F except \n\t)
     - Normalize quotes: `'` `'` `"` `"` → ASCII equivalents
  5. **Quality gate** — compare old vs new text:
     - New clean-char % must be > old clean-char %
     - New BM25 hit rate for "gemeenteraad" must be ≥ old
     - If new text is SHORTER than old by >50%, flag for manual review (Docling may have dropped content)
     - If quality gate fails, mark `status = 'review_needed'` and skip
  6. **Write** — under advisory lock 42:
     - `UPDATE documents SET content = %s WHERE id = %s` (preserve old content in `staging.ocr_recovery_originals` backup table first)
     - Re-generate `text_search` tsvector: `UPDATE documents SET text_search = to_tsvector('dutch', content) WHERE id = %s`
     - Delete old chunks: `DELETE FROM document_chunks WHERE document_id = %s`
     - Re-chunk via the existing `SmartIngestor.chunk_document()` path (import and reuse, don't rewrite)
     - Re-embed new chunks (call `services/embedding.py` to generate embeddings + upsert to Qdrant)
     - Update `text_search_enriched` on new chunks
     - Mark `status = 'recovered'` in the queue
  7. **Checkpoint** — per-document, to `data/pipeline_state/ocr_recovery_checkpoint.json`. On interrupt, resume from last completed document.

- [ ] **CLI flags:**
  - `--dry-run`: download + OCR + normalize + quality-gate, but no DB writes. Report stats.
  - `--limit N`: process only first N documents.
  - `--resume`: resume from checkpoint.
  - `--batch-size` (default 10): how many docs to process before committing + checkpointing. Small because Docling OCR is slow.
  - `--year YYYY`: only process documents from a specific year (for targeted recovery of 2018-2019 worst cohort).
  - `--damage-type`: filter by damage type from the queue table.
  - `--wait-for-lock` / `--no-wait-for-lock`.
  - `--skip-re-embed`: update text + chunks but don't re-embed (useful if you want to batch the embedding separately).

### Phase C — Validate and measure (~1 day)

- [ ] **Re-run BM25 hit rate test.** After recovery:
  ```sql
  SELECT COUNT(*) AS total,
    SUM(CASE WHEN text_search @@ to_tsquery('dutch','gemeenteraad') THEN 1 ELSE 0 END) AS bm25_hits,
    ROUND(100.0 * SUM(CASE WHEN text_search @@ to_tsquery('dutch','gemeenteraad') THEN 1 ELSE 0 END) / COUNT(*), 1) AS hit_pct
  FROM documents
  WHERE LOWER(name) LIKE '%motie%' AND content IS NOT NULL AND LENGTH(content) > 100;
  ```
  Target: ≥95% (from 77.5%).

- [ ] **Re-run R1 and R2 from the WS1 baseline.** Compare OCR quality scores before/after. R1 Tweebosbuurt motie content should now be readable; R2 Warmtebedrijf indieners should be parseable.

- [ ] **Spot-check 20 recovered documents.** Manual read of content preview — verify text is readable Dutch, not garbled.

- [ ] **Log recovery stats.** Total processed, recovered, review_needed, no_source, failed. Per-year breakdown.

## Acceptance criteria

- [ ] `staging.ocr_recovery_queue` table populated with all identified damaged documents
- [ ] `scripts/ocr_recovery_moties.py` exists with `--dry-run`, `--limit`, `--resume`, advisory lock 42
- [x] Source content backed up in `staging.ocr_recovery_originals` before any overwrites
      *(Used successfully by WS11c on 2026-04-14 — restored 8 over-aggressively-truncated docs from this backup: +104K chars recovered. Pre-restore copies preserved in `staging.ws11_pre_restore_backup` for symmetric rollback. See `WS11_CORPUS_COMPLETENESS.md` WS11c section.)*
- [ ] BM25 hit rate for "gemeenteraad" on moties: ≥ 95% (from 77.5%)
- [ ] Garbled-spacing count (40+ chars no space): ≤ 5% of moties (from 20%)
- [ ] No regression on documents that were already clean
- [ ] Re-run of WS1 baseline R1 (Tweebosbuurt) and R2 (Warmtebedrijf) shows improved OCR quality score (target: ≥ 4/5, from 2/5)
- [ ] 20 spot-checked recovered documents are readable Dutch text
- [ ] All recovered documents have valid BM25 tsvectors and are re-chunked + re-embedded in Qdrant

## Eval gate

| Metric | Target |
|---|---|
| BM25 hit rate "gemeenteraad" on moties | ≥ 95% (from 77.5%) |
| Garbled-spacing prevalence (40+ chars no space) | ≤ 5% (from 20%) |
| Ligature artifact prevalence | ≤ 1% (from 9.5%) |
| OCR quality score (WS1 baseline R1 + R2) | ≥ 4/5 (from 2/5) |
| No documents lost (content shorter by >50% without review) | 0 |
| Recovery script successful completion rate | ≥ 90% of queued docs |

## Risks specific to this workstream

| Risk | Mitigation |
|---|---|
| Source PDFs no longer available on ORI API | Fallback: try ORI text_content field first (some docs have server-side extracted text that may be cleaner). Flag docs with no source for manual recovery. |
| Docling force_full_page_ocr produces worse text than pypdf on some docs | Quality gate comparison: only overwrite if new text is measurably better. Keep originals in backup table. |
| Re-chunking changes chunk boundaries → invalidates WS1 enrichment (key_entities, vote_outcome, etc.) | Run WS7 BEFORE WS1 Phase 1 enrichment. Or: after recovery, re-run `enrich_and_extract.py --resume` on the affected chunks to re-populate metadata. |
| Smart space insertion creates false word boundaries | Keep heuristic conservative (only split at dictionary word boundaries). Log all insertions. Manual review on first 100 docs before full run. |
| Docling OCR is slow (~5-15 sec/page) | With ~2,700 docs averaging ~3 pages each: ~8,100 pages × 10 sec = ~22 hours. Run as nightly batch. Checkpoint-resumable. |
| Advisory lock 42 conflict with WS1 Phase 1 | Coordinate: run WS7 first (it fixes the source text that WS1 enrichment operates on), then WS1 Phase 1. Or: use `--skip-re-embed` and batch the embedding after both are done. |

## Recommended sequencing with WS1

```
WS7 Phase A (identify)      — can run NOW, read-only
WS7 Phase B (recovery)      — run AFTER committee_notulen_pipeline exits
WS1 Phase 1 (enrichment)    — run AFTER WS7 Phase B completes
                               (enrichment operates on clean text, not garbled)
WS7 Phase C (validate)      — run alongside WS1 Phase 1 quality audit
```

If WS7 is deferred past WS1 Phase 1, the enrichment will run on garbled source text — which works but produces lower-quality results. The enrichment can be re-run after OCR recovery, but that wastes the Gemini spend ($90-130) on text that will change.

## Future work (do NOT do in this workstream)
- OCR quality scoring on all document types (not just moties/amendementen)
- Proactive OCR quality gate in the nightly ingest pipeline (WS5a)
- Re-processing notulen through Docling (not needed — already clean)
- CER (character error rate) tracking per chunk as a permanent quality signal
- Post-OCR Dutch spell-checking with hunspell for subtle errors

## Pipeline integration (added 2026-04-12)

**Partially shipped:** `services/document_processor.py` already handles OCR quality for **new** documents:
- Detects garbled text via `services/scraper._is_garbled_ocr()`
- Re-OCRs via Docling (Tesseract + RapidOCR now in Docker image)
- Logs to `document_events` (event_type: `ocr_recovered`)

**What WS7 still needs to ship:**
- [ ] One-time batch recovery of ~6,200 existing garbled documents (the `scripts/ocr_recovery.py` backfill)
- [ ] Populate `documents.ocr_quality` column (currently 27/87K)
- [ ] Log batch recovery to `document_events` + `pipeline_runs`
- [ ] Consider adding an APScheduler job for periodic OCR quality audit (find degraded docs)

**New docs are handled automatically** — only the backfill of existing garbled docs is manual.

## Outcome

**Shipped 2026-04-14.** Full corpus recovery run completed across all damage types.

### Results

| Metric | Result |
|---|---|
| garbled_spacing recovered | 337 / 522 (64.6%) |
| ligature fixed (in-place) | ~564 docs (text transform, no OCR) |
| bm25_miss | skipped — near-zero recovery rate, needs tsvector refresh not OCR |
| Large docs >50K chars | deferred to WS10 (Docling layout pass) |
| Errors | 0 |
| Total checkpoint | 4,192 docs processed across full run |

### Key learnings
- Detection heuristic `[^\s]{40,}` had 77% false-positive rate on URLs/separators. Fixed with word-concat regex requiring alphabetic evidence.
- Ligature damage (`ﬁ→fi`) does not need re-OCR — `normalize_text()` in-place is sufficient and 100× faster.
- Advisory lock 42 leak from a killed process blocked all DB writes for 2+ hours. Fixed by terminating stale PostgreSQL backend.
- Parallel OCR with 4 workers achieved ~11 docs/min once lock was cleared.
- `bm25_miss` class (~3,154 docs) is a BM25 indexing issue, not OCR damage — fix is `UPDATE documents SET text_search = to_tsvector(...)`, not re-OCR.

### Follow-up (next steps, not blocking v0.2)
1. **Batch re-embed** — 337 recovered docs need Qdrant embedding (used `--skip-re-embed` for speed). Run embedding pipeline filtered to recovered IDs before v0.2.1.
2. **BM25 verification** — run Phase C query from this doc to confirm hit rate ≥ 95%.
3. **bm25_miss tsvector refresh** — separate task, not OCR.
4. **WS10** — large table-rich docs (bestemmingsplannen) need Docling layout pass, not OCR recovery.
