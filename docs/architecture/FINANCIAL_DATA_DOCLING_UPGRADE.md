# Financial Data Docling Upgrade

## Context

Our Docling test on Rotterdam's Jaarstukken 2024 (916 pages, 15MB) extracted **692 structured tables with 19,375 labelled numbers** and produced working `compute_financial_summary()` output — a massive quality upgrade over pypdf's raw text. Currently all financial documents in our system have `table_json=NULL` and `chunk_type` never equals `"table"`, meaning our `financial_calc.py` and `zoek_financieel` MCP tool operate on unstructured text only.

This plan upgrades **all** financial data using Docling for structured table extraction, following the **staging-first architecture** established for committee transcripts.

---

## Source Inventory

### Tier 0: watdoetdegemeente.rotterdam.nl (28 primary PDFs)

The 4 annual financial document cycles, 2018-2026:

| Type | Years | Count | Notes |
|------|-------|-------|-------|
| Jaarstukken | 2018-2024 | 7 | Annual accounts, ~500-900 pages each |
| Begroting + Tweede Herziening | 2020-2026 | 7 | Budget + mid-year revision bundled |
| Voorjaarsnota / Eerste Herziening | 2019-2025 | 7 | Spring update |
| Eindejaarsbrief / 10-maandsrapportage | 2019-2025 | 7 | Year-end report |

PDF URLs follow `/media/{random-hash}/{name}.pdf` — must be scraped from the homepage.

**These replace existing documents** where they overlap with earlier ingestions.

### Tier 1: High-Value ORI Financial PDFs (~500 PDFs)

Documents with the highest density of structured financial tables:

| Category | ORI Count | Description |
|----------|-----------|-------------|
| Grondexploitaties | ~266 | NPV models, cash flow projections, cost/revenue per project phase |
| GR begrotingen (MRDH, GRJR, GGD) | ~200 | Zienswijze + begroting docs for 9 gemeenschappelijke regelingen |
| Monitor Werk en Inkomen | ~93 | BUIG budget vs. actual, benefit expenditure, tertaal reports |
| Accountantsverslagen | ~76 | Audit opinions with financial finding tables |
| Krediet/investeringsvoorstellen | ~56 | Project budgets, investment decisions |

### Tier 2: Medium-Value ORI Financial PDFs (~600 PDFs)

| Category | ORI Count | Description |
|----------|-----------|-------------|
| DCMR/VRR/Grondbank zienswijze | ~130 | Environmental, fire/safety, land bank financials |
| Begrotingswijzigingen | ~128 | Budget amendment tables |
| Financial voortgangsrapportages | ~200 | Progress reports: vastgoed, Feyenoord City, Woonvisie, NPRZ |
| Belasting/legesverordeningen | ~116 | Tax rate tables |

### Tier 3: Other Table-Rich Documents

Any ORI document with ≥3 detected tables after Docling processing gets table chunks extracted. This catches raadsvoorstellen, rekenkamer reports, and other docs that contain incidental financial tables.

---

## Architecture

### Staging-First Pattern

Following the established best practice from committee transcript ingestion:

```
PDF → Docling Extraction → Staging Schema (chunks only, no vectors)
                              ↓
                          Quality Review (automated + manual)
                              ↓
                          Promotion to Production (embed + Qdrant upsert)
```

### Cloud Database Connections

All scripts use environment variables for cloud DB access:

| Service | Env Var | Notes |
|---------|---------|-------|
| PostgreSQL | `DATABASE_URL` | Cloud-hosted production DB |
| Qdrant | `QDRANT_URL` + `QDRANT_API_KEY` | Qdrant Cloud (HTTPS) |
| Embeddings | `NEBIUS_API_KEY` | Qwen3-Embedding-8B via Nebius |

### Staging Schema Extension

Extend the existing `staging.*` schema with financial-specific tracking:

```sql
-- New staging table for financial document tracking
CREATE TABLE IF NOT EXISTS staging.financial_documents (
    id TEXT PRIMARY KEY,                           -- e.g. "fin_jaarstukken_2024"
    doc_type TEXT NOT NULL,                         -- jaarstukken, begroting, voorjaarsnota, eindejaarsbrief
    fiscal_year INTEGER NOT NULL,
    source_url TEXT,                                -- watdoetdegemeente.nl or ORI download URL
    source TEXT DEFAULT 'watdoetdegemeente',        -- 'watdoetdegemeente', 'ori', 'notubiz'
    pdf_path TEXT,                                  -- local path to downloaded PDF
    page_count INTEGER,
    docling_tables_found INTEGER,                   -- number of tables Docling extracted
    docling_chunks_created INTEGER,                 -- total chunks (tables + text)
    review_status TEXT DEFAULT 'pending',           -- pending, auto_approved, approved, rejected
    quality_score FLOAT,
    promoted_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(doc_type, fiscal_year, source)
);
```

### Document Replacement Strategy

**For watdoetdegemeente.nl Tier 0 docs that already exist in production:**

1. Staging ingestion creates new chunks with deterministic `doc_id` = `fin_{type}_{year}` (e.g., `fin_jaarstukken_2024`)
2. At promotion time, the promoter:
   - Deletes old `document_children` + `document_chunks` for that `doc_id` (CASCADE handles cleanup)
   - Deletes old Qdrant points filtered by `document_id`
   - Inserts new chunks with `table_json` and `chunk_type="table"`
   - Generates embeddings and upserts to production Qdrant
3. Net result: old unstructured chunks are replaced with Docling-extracted structured chunks

**For new ORI documents (Tier 1-3):**

No replacement needed — these are new documents. Standard staging → promotion flow.

### New Files

| File | Purpose |
|------|---------|
| `scripts/download_financial_pdfs.py` | Scrape watdoetdegemeente.nl + download all Tier 0 PDFs |
| `pipeline/financial_ingestor.py` | Docling-based extraction → staging schema ingestion |
| `scripts/ingest_financial_docs.py` | Orchestrator: download → extract → stage → promote |
| `scripts/discover_ori_financial.py` | Query ORI API for Tier 1-3 financial PDFs |
| `scripts/promote_financial_docs.py` | Review + promote financial docs (extends promotion pattern) |

### Modified Files

| File | Change |
|------|--------|
| `scripts/create_staging_schema.py` | Add `staging.financial_documents` table |
| `pipeline/ingestion.py` | No changes (reuse `SmartIngestor.ingest_document()` as-is) |
| `services/rag_service.py` | No changes (already filters `chunk_type` and uses `table_json`) |
| `mcp_server_v3.py` | No changes (`zoek_financieel` already boosts table chunks) |

---

## Implementation Phases

### Phase 1: Staging Schema + Download Pipeline

**1a. Extend staging schema**

Add `staging.financial_documents` tracking table (SQL above).

**1b. Download Tier 0 PDFs** (`scripts/download_financial_pdfs.py`)

1. Fetch `watdoetdegemeente.rotterdam.nl` homepage
2. Parse all PDF links with BeautifulSoup, classify by doc type + year
3. Download to `data/financial_pdfs/{type}/{year}.pdf` (gitignored)
4. Write manifest to `staging.financial_documents` (source_url, pdf_path, page_count)
5. Idempotent: skip already-downloaded files

**1c. Discover ORI financial PDFs** (`scripts/discover_ori_financial.py`)

1. Query ORI Elasticsearch for financial MediaObjects by category:
   - Grondexploitaties: `name:*grondexploitatie*`
   - GR begrotingen: `name:*zienswijze* OR name:*gemeenschappelijke regeling*` + financial terms
   - Monitor Werk en Inkomen: `name:*monitor werk*`
   - Accountantsverslagen: `name:*accountant*`
   - Kredietvoorstellen: `name:*krediet* OR name:*investeringsvoorstel*`
2. Download PDFs via iBabs public URLs (`original_url` field)
3. Register in `staging.financial_documents` with `source='ori'`

### Phase 2: Docling Extraction Pipeline (`pipeline/financial_ingestor.py`)

Core class: `FinancialDocumentIngestor` (extends `StagingIngestor`)

```
Input: PDF path + metadata from staging.financial_documents
  ↓
Docling DocumentConverter (TableFormerMode.ACCURATE)
  ↓
For each element in Docling output:
  IF table:
    → chunk_type = "table"
    → table_json = {"headers": [...], "rows": [[...]]}
    → content = markdown rendering of table (for embedding)
  IF text:
    → chunk_type = "text"
    → table_json = NULL
    → content = Docling markdown, max ~1000 chars with overlap
  ↓
Write to staging.documents, staging.document_children, staging.document_chunks
Update staging.financial_documents (docling_tables_found, docling_chunks_created)
```

Key design decisions:

- **Staging only** — no embeddings, no Qdrant writes during extraction
- **Table-aware chunking**: Never split a table across chunks. Each table = one chunk.
- **Narrative chunking**: Docling markdown text, split at ~1000 chars respecting paragraph boundaries
- **Document hierarchy**: `document_children` per chapter/section detected by Docling headings
- **Deterministic IDs**: `fin_{type}_{year}` for Tier 0, ORI document ID for Tier 1-3
- **Multi-level header cleanup**: Post-process Docling tables to deduplicate repeated header values (known quirk with merged cells)

### Phase 3: Quality Review + Auto-Approval

**Automated quality checks** (run per document after extraction):

| Check | Auto-approve if | Auto-reject if |
|-------|----------------|----------------|
| Tables found | ≥ 1 table extracted | 0 tables AND doc_type is financial |
| Dutch number parsing | ≥ 50% of table cells parse as numbers | < 10% parse rate |
| `compute_financial_summary()` | Produces ≥ 1 year-over-year comparison | N/A |
| Chunk count | ≥ 10 chunks | < 3 chunks for a >50 page PDF |

Documents passing all checks → `review_status = 'auto_approved'`
Documents failing any check → `review_status = 'pending'` (manual review needed)

**Manual review CLI:**
```bash
python scripts/promote_financial_docs.py --list
python scripts/promote_financial_docs.py --preview fin_jaarstukken_2024
python scripts/promote_financial_docs.py --approve fin_jaarstukken_2024
python scripts/promote_financial_docs.py --approve-batch --min-score 0.7
```

### Phase 4: Promotion to Production

Following the established pattern from `promote_committee_notulen.py`:

1. **Delete old production data** for the document ID:
   - `DELETE FROM document_children WHERE document_id = %s` (CASCADE cleans chunks)
   - Qdrant: `delete(filter={"document_id": doc_id})`
2. **Copy from staging to production**:
   - `documents` record (upsert)
   - `document_children` with ID remapping
   - `document_chunks` with remapped `child_id`, preserving `chunk_type` and `table_json`
3. **Generate embeddings** (Nebius API, Qwen3-Embedding-8B, 4096-dim)
4. **Upsert to Qdrant** `notulen_chunks` collection with payload:
   - `doc_type: "financial"`
   - `chunk_type: "table"` or `"text"`
   - `document_id`, `title`, `start_date`
   - `table_json` stored in Qdrant payload for MCP tool access
5. **Enrichment pass** (reuse Plan G Layer 1):
   - `section_topic` from document structure
   - `key_entities` from gazetteer match on financial terms/programme names
   - Update `text_search_enriched` tsvector for BM25

### Phase 5: ORI Tier 1-3 Processing

Same pipeline, different discovery:

1. `discover_ori_financial.py` populates `staging.financial_documents` with ORI source PDFs
2. `ingest_financial_docs.py` processes them through Docling → staging
3. Auto-approval based on table count and quality checks
4. Batch promotion for auto-approved documents

**Tier 3 adaptive approach**: Process ALL ORI PDFs from financial agenda items through Docling. If a document yields ≥ 3 tables, extract table chunks. If < 3 tables, ingest as regular text (skip table extraction). This catches incidental tables in raadsvoorstellen, rekenkamer reports, etc.

---

## Execution Order

```
Step 1: Extend staging schema (staging.financial_documents table)

Step 2: Download Tier 0 PDFs (28 from watdoetdegemeente.nl)

Step 3: Process jaarstukken-2024 FIRST (known-good baseline from test)
        → Verify in staging: table counts, chunk quality, financial_calc output
        → Promote to production
        → Test zoek_financieel MCP tool
        → Test compute_financial_summary() in web frontend

Step 4: Process remaining 27 Tier 0 PDFs (batch, overnight)
        → Auto-approve + promote in batches

Step 5: Discover + download Tier 1 ORI financial PDFs (~500)
        → Process through Docling (batch, may take 2-3 days on CPU)
        → Auto-approve + promote

Step 6: Discover + download Tier 2 ORI financial PDFs (~600)
        → Same pipeline

Step 7: Tier 3 adaptive scan of remaining ORI financial agenda items
```

---

## Processing Estimates

| Step | Count | Docling Time | Embed Time | Total |
|------|-------|-------------|-----------|-------|
| Tier 0 (watdoetdegemeente) | 28 PDFs | ~12-18h (CPU) | ~4-5h | ~20h |
| Tier 1 (ORI high-value) | ~500 PDFs | ~40-80h (CPU) | ~10h | ~50-90h |
| Tier 2 (ORI medium-value) | ~600 PDFs | ~30-60h (CPU) | ~10h | ~40-70h |
| Tier 3 (ORI adaptive) | ~500 PDFs | ~20-40h (CPU) | ~5h | ~25-45h |

**Speedup options:**
- GPU: 10x faster Docling (if available)
- Parallel CPU: 4 PDFs simultaneously → 4x faster
- `TableFormerMode.FAST`: 2x faster, slightly lower quality
- ORI `text_pages`: For Tier 3, use pre-extracted text from ORI instead of Docling for non-table documents

**Estimated total new chunks:** 50,000-100,000 across all tiers (based on 692 tables from one 916-page jaarstukken).

**Estimated cost:**
- Nebius embedding: ~$1-2 total (bulk rate)
- Docling: Free (CPU only, no API costs)
- ORI API: Free (public, no auth required)

---

## Verification

1. **DB check**: `SELECT chunk_type, count(*), count(table_json) FROM document_chunks WHERE document_id LIKE 'fin_%' GROUP BY chunk_type` — should show "table" rows with non-null `table_json`
2. **MCP test**: `zoek_financieel("begroting 2024 woningbouw")` — should return structured table chunks with year columns
3. **Financial calc test**: Financial query → `compute_financial_summary()` → year-over-year comparisons like "Totale woonlasten: 2023 → 2024: €925 → €1.025 (+€100, +10.8%)"
4. **Agentic loop test**: Web frontend analysis of a financial agenda item → confidence check triggers RAG retrieval → supplementary financial context appears in synthesis
5. **Spot check**: Compare Docling `table_json` for a known table (e.g., "Overzicht van baten en lasten per BBV-taakveld") against the original PDF visually
6. **Replacement check**: Verify old unstructured jaarstukken chunks are gone: `SELECT count(*) FROM document_chunks WHERE document_id = 'old_id' AND table_json IS NULL` = 0

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Qdrant corruption during embed | Staging-first: no Qdrant writes until promotion. Process one doc at a time. |
| Docling OOM on large PDFs | Monitor RAM; fall back to page-range processing if >1GB RSS |
| Old financial data lost during replacement | Backup: snapshot production `document_chunks` for affected `doc_id`s before promotion |
| Duplicate documents (watdoetdegemeente + ORI) | Deduplicate by content hash; Tier 0 takes precedence (full document vs. ORI companion) |
| ORI PDF download failures | Retry with exponential backoff; log failures to staging.financial_documents |
| Multi-level table headers (Docling quirk) | Post-process: deduplicate repeated header cell values before storing `table_json` |
| transformers version conflict | Already verified: 5.5.0 is compatible with all project dependencies |
| watdoetdegemeente.nl URL structure changes | Scraper logs all found URLs; hardcoded fallback URL list as backup |
| Cloud DB connection during long batch runs | Connection pool with reconnect logic; checkpoint after each PDF |

---

## Document Types That Benefit from Docling Table Extraction

Summary of all document categories ranked by table density:

| Priority | Category | Source | Est. PDFs | Why |
|----------|----------|--------|-----------|-----|
| CRITICAL | Jaarstukken, Begroting, Voorjaarsnota, Eindejaarsbrief | watdoetdegemeente.nl | 28 | Primary budget docs, 100s of tables each |
| HIGH | Grondexploitaties | ORI | 266 | NPV models, cash flow tables |
| HIGH | GR begrotingen (MRDH, GRJR, GGD, DCMR, VRR) | ORI | 200 | Inter-municipal budget allocations |
| HIGH | Monitor Werk en Inkomen | ORI | 93 | Structured tertaal reports |
| HIGH | Accountantsverslagen | ORI | 76 | Audit finding tables |
| HIGH | Krediet/investeringsvoorstellen | ORI | 56 | Project budget tables |
| MEDIUM | Begrotingswijzigingen | ORI | 128 | Budget amendment tables |
| MEDIUM | Financial voortgangsrapportages | ORI | 200 | Progress reports with budget tracking |
| MEDIUM | Belasting/legesverordeningen | ORI | 116 | Tax rate tables |
| MEDIUM | Rekenkamer reports | ORI | 446 | Audit reports (selective — not all have tables) |
| LOW | Deelnemingen (Eneco, Stedin, RET) | ORI | ~100 | Mostly narrative, few tables |
| ADAPTIVE | Any ORI doc with ≥3 Docling tables | ORI | ~500 | Catches incidental tables in any document |
