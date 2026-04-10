# WS2 — Trustworthy Financial Analysis

> **Priority:** 2 (where MAAT is winning sales conversations today)
> **Status:** `not started`
> **Owner:** `unassigned`
> **Target release:** v0.2.0
> **Master plan section:** [V0_2_BEAT_MAAT_PLAN.md §4](../architecture/V0_2_BEAT_MAAT_PLAN.md)

## TL;DR
When a council member asks "wat is de begrotingsruimte voor wijkveiligheid in 2026 en hoe is dat veranderd t.o.v. 2024?", today the answer comes from text-RAG over jaarstukken — paraphrased numbers, lossy, and *wrong*. This workstream extracts every line item from Docling-parsed table_json into a structured `financial_lines` Postgres table, ships two MCP tools that return *exact rows* with verification tokens, and enforces a **zero-paraphrase contract on euros**. Stanford's legal-RAG study shows 17% hallucination floor for the best commercial systems on text RAG; we get out of that floor by not letting an LLM see euros at all in the answer path.

## Dependencies
- **None.** This workstream is fully independent and can start day 1.
- Memory to read first:
  - [project_financial_docs.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_financial_docs.md) — 4 doc types, 20K table_json chunks
  - [project_pipeline_hardening.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_pipeline_hardening.md)

## Cold-start prompt

> You are picking up Workstream 2 (Trustworthy Financial Analysis) of NeoDemos v0.2.0. The full plan is at `docs/architecture/V0_2_BEAT_MAAT_PLAN.md` §4. This handoff at `docs/handoffs/WS2_FINANCIAL.md` is self-contained — you do not need to read the master plan.
>
> Read in order: (1) this file top-to-bottom, (2) `docs/architecture/FINANCIAL_DATA_DOCLING_UPGRADE.md`, (3) `pipeline/financial_ingestor.py`, (4) `mcp_server_v3.py` `zoek_financieel` tool (around line 349), (5) the existing financial scripts in `scripts/run_financial_batch.py` and `scripts/promote_financial_docs.py`.
>
> Your job: build a structured `financial_lines` Postgres table populated from Docling `table_json` blobs, ship two new MCP tools (`vraag_begrotingsregel`, `vergelijk_begrotingsjaren`) that return exact rows with verification tokens, and prove **zero euro hallucination** on a 30-question benchmark with 100% exact-match accuracy.
>
> The trust contract: an LLM must never paraphrase a euro amount in this workstream's tools. The number returned to the user must be byte-identical to the source PDF cell. If you cannot guarantee that, the tool returns `{error: 'no_exact_match'}` instead of guessing.
>
> Honor the project house rules in `docs/handoffs/README.md`. Coordinate any large backfill with WS5a's pipeline lock.

## Files to read first
- [`docs/architecture/FINANCIAL_DATA_DOCLING_UPGRADE.md`](../architecture/FINANCIAL_DATA_DOCLING_UPGRADE.md)
- [`pipeline/financial_ingestor.py`](../../pipeline/financial_ingestor.py) — current `FinancialDocumentIngestor` class, Docling table extraction
- [`scripts/run_financial_batch.py`](../../scripts/run_financial_batch.py) and [`scripts/promote_financial_docs.py`](../../scripts/promote_financial_docs.py) — existing batch flow
- [`mcp_server_v3.py`](../../mcp_server_v3.py) — `zoek_financieel` tool around line 349; understand how it currently merges text + table_json chunks
- Postgres: `documents` and `chunks` tables; especially the `metadata->>'table_json'` payload structure

## Build tasks

### Schema (~1 day)

- [ ] **Alembic migration creating `financial_lines` table:**
  ```sql
  CREATE TABLE financial_lines (
    id BIGSERIAL PRIMARY KEY,
    gemeente TEXT NOT NULL DEFAULT 'rotterdam',
    document_id TEXT NOT NULL REFERENCES documents(id),
    page INT NOT NULL,
    table_id TEXT NOT NULL,
    row_idx INT NOT NULL,
    col_idx INT NOT NULL,
    programma TEXT,
    sub_programma TEXT,
    jaar INT NOT NULL,
    bedrag_eur NUMERIC(18,2) NOT NULL,
    bedrag_label TEXT,                       -- e.g. "Lasten", "Baten", "Saldo"
    bron_chunk_id BIGINT REFERENCES chunks(id),
    source_pdf_url TEXT,
    sha256 TEXT NOT NULL,                    -- hash of the source row, used in verification token
    extracted_at TIMESTAMPTZ DEFAULT NOW()
  );
  CREATE INDEX ON financial_lines (gemeente, jaar, programma);
  CREATE INDEX ON financial_lines (gemeente, jaar, sub_programma);
  CREATE INDEX ON financial_lines (document_id);
  ```
- [ ] Add a `gemeente` column from day 1 even though we backfill `'rotterdam'` for everything — saves a migration when WS5b lands.

### Extraction (~3 days)

- [ ] **`pipeline/financial_lines_extractor.py`** — new module. Reads `chunks` rows where `metadata->>'table_json' IS NOT NULL`, parses the Docling table structure, identifies header rows (Programma / Sub-programma / Jaar / Lasten / Baten / Saldo), produces one `financial_lines` row per (programma × sub_programma × jaar × label) cell.
- [ ] **Robust header detection** — Rotterdam jaarstukken have inconsistent header layouts across years. Heuristics:
  - Detect "Programma" header column by exact match
  - Detect year columns by 4-digit pattern in header row
  - Detect bedrag columns by `Lasten|Baten|Saldo|Begroting|Realisatie` keyword
- [ ] **Sanity filters** — drop rows where bedrag is empty, NaN, or unparseable; log to `financial_lines_extraction_failures` table for human review.
- [ ] **Backfill all 4 doc types × 2018–2026** for Rotterdam: jaarstukken, voorjaarsnota, begroting, 10-maandsrapportage. Use the existing financial batch scripts for orchestration.
- [ ] **Coverage report** — script that prints `(programma, jaar, count, sum_eur)` for spot-checking against the published Rotterdam summaries.

### MCP tools (~2 days)

- [ ] **`vraag_begrotingsregel(gemeente: str, jaar: int, programma: str, sub_programma: str | None = None) -> dict`** in [`mcp_server_v3.py`](../../mcp_server_v3.py)
  - Returns structured rows, deterministic, paginated:
    ```json
    {
      "matches": [
        {"programma": "Veilig", "sub_programma": "Wijkveiligheid", "jaar": 2026, "bedrag_eur": 82400000.00, "label": "Lasten",
         "source_pdf": "...", "page": 47, "table_cell_ref": "table_id=...,row=12,col=4",
         "verification": {"sha256": "...", "retrieved_at": "..."}}
      ],
      "total": 1
    }
    ```
  - **Never paraphrase.** If multiple matches, return all. If zero, return `{matches: [], hint: "..."}`.
- [ ] **`vergelijk_begrotingsjaren(gemeente: str, programma: str, jaren: list[int]) -> dict`**
  - Returns time-series: `{programma, series: [{jaar, bedrag_eur, delta_abs, delta_pct}], source_documents: [...]}`
- [ ] **Upgrade `zoek_financieel`** in [`mcp_server_v3.py:349`](../../mcp_server_v3.py#L349) — when the query mentions a specific programma + jaar, route to the structured tool first; fall back to text RAG only for narrative questions ("waarom" / "toelichting").
- [ ] **Verification token format** — every numeric response includes `verification: {table_cell_ref, sha256, retrieved_at}` so a downstream agent can re-fetch and assert the cell content unchanged.
- [ ] **Tool descriptions for AI** — coordinate with WS4 registry. Description must explicitly say: "Use this tool whenever the user asks for a euro amount, budget line, or financial comparison. Do NOT use `zoek_financieel` for exact numbers."

### Benchmark (~1 day)

- [ ] **30-question financial benchmark** — new file `rag_evaluator/data/financial_questions.json`. Each entry: `{question, expected_eur, expected_programma, expected_jaar, source_doc, accept_alternatives: []}`.
- [ ] **Benchmark runner** — `rag_evaluator/run_financial.ts` (or .py if there's already a Python harness). For each question: invoke `vraag_begrotingsregel`, assert `bedrag_eur == expected_eur` exact match. **Target: 30/30.**
- [ ] **Hallucination floor** — separate test: scrape 50 random LLM responses involving euros and verify every euro figure appears verbatim in `financial_lines`. Zero exceptions.

## Acceptance criteria

- [ ] `financial_lines` table created via Alembic migration
- [ ] Backfill complete for jaarstukken + voorjaarsnota + begroting + 10-maandsrapportage 2018–2026 (Rotterdam)
- [ ] Coverage report shows ≥95% of programmas covered for each year
- [ ] `vraag_begrotingsregel` returns structured rows with verification tokens
- [ ] `vergelijk_begrotingsjaren` returns time-series with delta_abs and delta_pct
- [ ] `zoek_financieel` routes structured queries to `vraag_begrotingsregel` first
- [ ] Both new tools registered in WS4 tool registry with AI-consumption descriptions
- [ ] 30-question financial benchmark scores 30/30 exact match
- [ ] Zero LLM-paraphrased euros in test corpus (50 random responses)
- [ ] Extraction failure log reviewed; failures < 5% of total cells

## Eval gate

| Metric | Target |
|---|---|
| Numeric accuracy on 30-question benchmark | **100% exact match** |
| Hallucination floor (50 LLM responses) | **0 paraphrased euros** |
| Coverage of programmas per year | ≥ 95% |
| `vraag_begrotingsregel` p95 latency | < 200ms (deterministic SQL) |

## Risks specific to this workstream

| Risk | Mitigation |
|---|---|
| Docling table extraction inconsistent across years | Per-year header heuristics + manual override file `data/financial/header_overrides.yml` for known-bad years |
| Multiple matches confuse the LLM | Tool always returns *all* matches; LLM is told to ask the user to disambiguate |
| 100% target proves too aggressive | Fall back to: "structured tool always returns the row when unambiguous; ambiguous → returns ranked candidates with `requires_user_clarification: true`". Still beats MAAT. |
| Backfill blocks Qdrant during writes | Run via WS5a pipeline lock; rows go to staging schema first then promote |
| Programma names drift over years (e.g. "Veilig" → "Veiligheid en handhaving") | Maintain a `programma_aliases` table; expose through tool's match logic |

## Future work (do NOT do in this workstream)
- Multi-municipality financial extraction (WS5b territory; Rotterdam-only in v0.2.0)
- Forecast / projection tools (need a clean baseline first)
- Visualizations / charts (UI work, defer)
- Cross-program reallocation analysis (needs WS3 journey)

## Outcome
*To be filled in when shipped. Include: actual coverage %, benchmark score, hardest extraction edge cases, header-override list.*
