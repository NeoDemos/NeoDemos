# NeoDemos Document Ingest — Skill Reference

Use this skill when working on ingesting new documents from ORI or iBabs, fixing content quality issues, or running/scheduling the gap fixer.

---

## Architecture: Two ingestion paths

```
ORI API (openraadsinformatie.nl)          iBabs (rotterdamraad.bestuurlijkeinformatie.nl)
  └─ MediaObjects (numeric IDs)             └─ Meeting agendas (UUID + bb-number docs)
       │                                          │
       ▼                                          ▼
  ori_document_gap_fixer.py             RefreshService._insert_doc_with_content()
       │                                          │
       └──────────────┬───────────────────────────┘
                      ▼
              documents table (Postgres)
                      │
                      ▼
        chunk_unchunked_documents.py
                      │
                      ▼
        migrate_embeddings.py --recovery-mode
                      │
                      ▼
              notulen_chunks (Qdrant)
```

---

## Key lessons from content quality incidents

### The stub problem (root cause — now fixed)
iBabs scraper inserted docs with `id=<bb-number>` and **no content**, because `ibabs_service._parse_agenda_page()` only parses HTML table rows for bb-numbers, it doesn't download PDFs.

ORI indexes the same document under a **different numeric ID**. When `ori_gap_fixer` ran, it saw the bb-id stub as already present in `existing_ids` and skipped it — leaving the stub with 1000c of stale content from an old `md_text[:1000]` ingest.

**Fixes applied:**
1. `storage.insert_document()` now uses `CASE WHEN LENGTH(new) > LENGTH(existing)` — never downgrades content on conflict
2. `RefreshService._insert_doc_with_content()` — OCRs PDFs before insert; skips stub-only inserts when OCR yields nothing
3. `fix_truncated_docs.py` — one-time cleanup of all three stub categories (A/B/C)

### UUID document recovery via iBabs portal URL
UUID-id iBabs documents (bijlagen, moties, raadsvoorstellen) can **always** be downloaded as PDF from:
```
https://rotterdamraad.bestuurlijkeinformatie.nl/Document/View/{UUID}
```
If UUID stubs are found without a URL, set the URL using this pattern, then run Strategy C:
```sql
UPDATE documents
SET url = 'https://rotterdamraad.bestuurlijkeinformatie.nl/Document/View/' || id
WHERE id ~ '^[0-9a-f]{8}-' AND (content IS NULL OR LENGTH(content) <= 50) AND (url IS NULL OR url = '');
```
This was validated 2026-04-05: 874 previously "unrecoverable" UUID stubs were all successfully OCR'd via this URL pattern.

### ID types and their origins
| ID pattern | Source | Content risk |
|---|---|---|
| `7XXXXXXX` (numeric) | ORI index | Reliable — full md_text or OCR'd |
| `26bbXXXXXX` (bb-prefix) | iBabs scraper | ⚠️ May be stub — verify content length |
| `UUID` (36-char hex) | iBabs bijlagen | ⚠️ Often empty — OCR via `bestuurlijkeinformatie.nl/Document/View/{UUID}` |
| `legacy_*` | Recovery scripts | Varies |

### The 1000c marker
Exactly 1000 characters = ORI `md_text` truncation. ORI caps `md_text` at ~1000 chars for some document types. The full text is only available via `original_url` (PDF download + OCR).

---

## Scripts

### `scripts/ori_document_gap_fixer.py`
Full ORI gap discovery and ingestion. Uses `search_after` pagination (NOT scroll — ORI blocks scroll after page 1).

```bash
# Scope report only
python scripts/ori_document_gap_fixer.py --audit-only

# Full ingest of all missing docs
python scripts/ori_document_gap_fixer.py

# Recent docs only
python scripts/ori_document_gap_fixer.py --min-year 2024

# Limited test run
python scripts/ori_document_gap_fixer.py --limit 100
```

**Year inference priority:** `last_discussed_at` → `[YYbb...]` prefix in name → parent meeting chain → `date_modified`

**Parent resolution:** Checks `is_referenced_by` and `parent` fields against our meetings/agenda_items tables. A 2025 ORI doc can legitimately appear on a 2026 agenda — the code handles this correctly.

**OCR fallback:** If `text`/`md_text` < 500c, downloads PDF from `original_url` and runs native macOS OCR.

### `scripts/fix_truncated_docs.py`
One-time (and re-runnable) fix for all content quality issues. Checkpointed.

```bash
python scripts/fix_truncated_docs.py --dry-run    # scope only
python scripts/fix_truncated_docs.py              # all strategies A+B+C
python scripts/fix_truncated_docs.py --strategy C # only UUID iBabs stubs
```

| Strategy | What | How |
|---|---|---|
| A | bb-id stubs WITH richer ORI sibling | Delete stub + chunks |
| B | bb-id stubs WITHOUT sibling (1000c truncated) | ORI name search → OCR fallback |
| C | UUID iBabs stubs (empty, have URL) | OCR from stored iBabs URL → ORI fallback |

### `scripts/chunk_unchunked_documents.py`
Chunks all documents in Postgres that have no `document_chunks` yet.

```bash
python scripts/chunk_unchunked_documents.py          # all unchunked
python scripts/chunk_unchunked_documents.py --dry-run # count only
```

Safe to run while `migrate_embeddings.py` is running — writes only to Postgres, new chunks get IDs above the embedding checkpoint.

### `scripts/migrate_embeddings.py --recovery-mode`
Embeds all chunks missing from Qdrant. Always use `--recovery-mode` — runs a fresh live audit at startup so it picks up any new chunks.

```bash
python scripts/migrate_embeddings.py --recovery-mode
```

**Rate:** ~3 chunks/sec on M5 Pro. **Do not run** while `optimize_qdrant.py` is active.

---

## Full pipeline run (after a gap is discovered)

```bash
# 1. Discover and ingest missing ORI docs
python scripts/ori_document_gap_fixer.py

# 2. Fix any content stubs
python scripts/fix_truncated_docs.py

# 3. Chunk all new docs
python scripts/chunk_unchunked_documents.py

# 4. Embed new chunks
python scripts/migrate_embeddings.py --recovery-mode
```

---

## Automated ingestion (RefreshService)

`services/refresh_service.py` runs on a scheduler (configured in `main.py`):
- **Phase 1 (History sweep):** Fetches meetings from last ingestion date → now via ORI
- **Phase 2 (Calendar sweep):** Fetches upcoming meetings (now → +60 days) via iBabs fallback when ORI is behind

**Key invariant:** `_insert_doc_with_content()` is called for every new doc — it OCRs the PDF before inserting, so no stubs enter the DB from the automated path.

**UUID URL fallback:** If an iBabs UUID doc has no URL stored by the scraper, the download URL can be constructed as `https://rotterdamraad.bestuurlijkeinformatie.nl/Document/View/{UUID}`. This should be set before calling `_insert_doc_with_content()` to ensure OCR can proceed.

**ORI lag:** ORI typically lags iBabs by days to weeks for new meetings. For 2026+ meetings, the Calendar sweep uses iBabs directly as primary source.

### Recovery playbook for future UUID stub issues
If a future audit finds UUID docs with NULL content and no URL:
```bash
# 1. Set URLs using the iBabs portal pattern
psql -c "UPDATE documents SET url = 'https://rotterdamraad.bestuurlijkeinformatie.nl/Document/View/' || id
         WHERE id ~ '^[0-9a-f]{8}-' AND (content IS NULL OR LENGTH(content) <= 50) AND (url IS NULL OR url = '');"

# 2. OCR all of them
python scripts/fix_truncated_docs.py --strategy C

# 3. Chunk + embed
python scripts/chunk_unchunked_documents.py
python scripts/migrate_embeddings.py --recovery-mode
```

---

## Safety rules

1. **Never write to Qdrant** while `migrate_embeddings.py` is running. Read-only Qdrant queries are safe.
2. **Never run `optimize_qdrant.py`** during migration (triggers compaction).
3. `ori_document_gap_fixer.py` writes only to Postgres — safe to run anytime.
4. `fix_truncated_docs.py` writes only to Postgres — safe to run anytime.
5. Check `ps aux | grep migrate_embeddings` before any Qdrant write operation.
