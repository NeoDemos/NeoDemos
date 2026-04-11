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
    gemeente TEXT NOT NULL DEFAULT 'rotterdam',      -- tenant view (multi-tenant routing key)
    entity_id TEXT NOT NULL DEFAULT 'rotterdam'      -- added 2026-04-11: legal entity that owns the line
      REFERENCES financial_entities(id),             -- 'rotterdam' | 'grjr' | 'dcmr' | 'vrr' | ...
    scope TEXT NOT NULL DEFAULT 'gemeente'           -- added 2026-04-11
      CHECK (scope IN ('gemeente', 'gemeenschappelijke_regeling', 'regio', 'nationaal')),
    document_id TEXT NOT NULL REFERENCES documents(id),
    page INT NOT NULL,
    table_id TEXT NOT NULL,
    row_idx INT NOT NULL,
    col_idx INT NOT NULL,
    programma TEXT,
    sub_programma TEXT,
    iv3_taakveld TEXT REFERENCES iv3_taakvelden(code),  -- added 2026-04-11; canonical national key
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
  CREATE INDEX ON financial_lines (gemeente, jaar, iv3_taakveld);  -- added 2026-04-11
  CREATE INDEX ON financial_lines (scope, entity_id, jaar);        -- added 2026-04-11
  CREATE INDEX ON financial_lines (document_id);
  ```
- [ ] Add a `gemeente` column from day 1 even though we backfill `'rotterdam'` for everything — saves a migration when WS5b lands.
- [ ] **`entity_id` + `scope` semantic split** *(added 2026-04-11)*: `gemeente` = which tenant's view this row surfaces in (multi-tenant routing key for v0.2.1); `entity_id` = which legal entity actually owns the line. For Rotterdam's own begroting these are both `rotterdam`. For GRJR-jaarstukken ingested via Rotterdam's portal: `gemeente='rotterdam'`, `entity_id='grjr'`, `scope='gemeenschappelijke_regeling'`. Full spec in §Joint-arrangement (GRJR) & scope handling below.
- [ ] **`financial_entities` reference table** *(added 2026-04-11)* — see §Joint-arrangement section for full spec; create this table in the same Alembic migration as `financial_lines` so the `entity_id` FK resolves.

### IV3 canonical aggregation layer (~1 day) *(added 2026-04-11, triaged from TODOS)*

Rotterdam's programma names drift across years ("SIO-programma" → "Stedelijke ontwikkeling" → "Bestaande stad"). Without a stable backbone, cross-year comparisons hallucinate — this is exactly the failure mode captured in [FEEDBACK_LOG.md 2026-04-10 "Hallucinated datapunten bij eerste begroting-grafiek"](../../brain/FEEDBACK_LOG.md). **IV3** ("Informatie voor Derden" / taakvelden) is the statutory Dutch standard every municipality reports against — same codes for Rotterdam, Amsterdam, Utrecht, all 342 gemeenten. Use it as the canonical aggregation key so multi-year comparisons survive label drift, and so multi-municipality comparisons work without a per-tenant mapping layer.

- [ ] **Download the taakvelden codetabel** from [findo.nl/content/taakvelden-gemeenten](https://findo.nl/content/taakvelden-gemeenten) (see also [findo.nl/content/vraagbaak-iv3](https://findo.nl/content/vraagbaak-iv3)). Store at `data/financial/iv3_taakvelden.json` with fields `{code, hoofdtaakveld, subtaakveld, omschrijving}`. Load into an `iv3_taakvelden` reference table via Alembic.
- [ ] **`programma_aliases` mapping table** via Alembic:
  ```sql
  CREATE TABLE programma_aliases (
    id BIGSERIAL PRIMARY KEY,
    gemeente TEXT NOT NULL,
    jaar INT NOT NULL,
    programma_label TEXT NOT NULL,           -- as it appears in the source doc that year
    iv3_taakveld TEXT NOT NULL REFERENCES iv3_taakvelden(code),
    confidence NUMERIC(3,2),                 -- 1.00 for manual; <1 for LLM-proposed
    source TEXT                              -- 'manual' | 'llm_proposed' | 'regex'
  );
  CREATE UNIQUE INDEX ON programma_aliases (gemeente, jaar, programma_label);
  ```
- [ ] **Seed Rotterdam aliases 2018–2026** — for every programma label appearing in `financial_lines`, map to an IV3 taakveld. Start with an LLM-proposed pass (Gemini Flash Lite), human-review before commit. Expect ~40 programmas × 9 years = ~360 rows with substantial overlap year-to-year.
- [ ] **`vergelijk_begrotingsjaren` aggregation** — when the tool computes a time-series, aggregate on `iv3_taakveld` (resolved via `programma_aliases`), **not** on raw `programma` label. This is the fix for the silent hallucination captured in FEEDBACK_LOG 2026-04-10.
- [ ] **Multi-tenant payoff (noted 2026-04-11)** — because IV3 is a national standard, every municipality added in v0.2.1+ gets cross-year consistency for free. WS5b only needs to seed per-municipality `programma_aliases` rows — no schema changes. This parallels the BAG-native location layer design in WS1 Phase A: lock the canonical national standard now, collect the multi-tenant payoff later.

### Joint-arrangement (GRJR) & scope handling (~1.5 days) *(added 2026-04-11, triaged from [FEEDBACK_LOG.md 2026-04-11 "Jeugdzorgkosten Rotterdam vs. GRJR-regio"](../../brain/FEEDBACK_LOG.md))*

Rotterdam publishes documents from the **Gemeenschappelijke Regeling Jeugdhulp Rijnmond (GRJR)** — a joint arrangement under the Wet Gemeenschappelijke Regelingen covering 9 municipalities with Rotterdam as host (gastgemeente) — on its own portal `watdoetdegemeente.rotterdam.nl`. The ingest pipeline currently tags those documents as `gemeente='rotterdam'`, erasing the distinction that GRJR is a separate legal entity whose €409 mln 2023 total covers 9 municipalities, not just Rotterdam. During the 2026-04-11 jeugdzorg-kosten test, the tool returned the regional total to a user asking "what are Rotterdam's jeugdzorg costs?" with no scope signal — a scope-hallucination failure mode as severe as the 2026-04-10 cluster-label drift one. **Same pattern exists for other joint arrangements** (DCMR Milieudienst Rijnmond, Veiligheidsregio Rotterdam-Rijnmond, MRDH). Solve once, structurally.

- [ ] **`scope` + `entity_id` columns on `financial_lines`** — folded into the base schema above (see §Schema). `gemeente` = tenant view (multi-tenant routing key); `entity_id` = legal entity that owns the row (`rotterdam`, `grjr`, `dcmr`, ...); `scope` = `gemeente | gemeenschappelijke_regeling | regio | nationaal`. For Rotterdam's own begroting all three point at `rotterdam`/`gemeente`; for GRJR-jaarstukken ingested via Rotterdam's portal: `gemeente='rotterdam'`, `entity_id='grjr'`, `scope='gemeenschappelijke_regeling'`.

- [ ] **`financial_entities` reference table** via Alembic (same migration as `financial_lines`):
  ```sql
  CREATE TABLE financial_entities (
    id TEXT PRIMARY KEY,                     -- 'rotterdam', 'grjr', 'dcmr', 'vrr', 'mrdh', ...
    display_name TEXT NOT NULL,              -- 'Gemeenschappelijke Regeling Jeugdhulp Rijnmond'
    kind TEXT NOT NULL,                      -- 'gemeente' | 'gemeenschappelijke_regeling' | 'veiligheidsregio' | ...
    host_gemeente TEXT,                      -- 'rotterdam' for GRJR/DCMR/VRR
    member_gemeenten TEXT[],                 -- ['rotterdam','barendrecht','capelle',...] for GRs
    website TEXT,
    wgr_type TEXT                            -- 'openbaar_lichaam' | 'bedrijfsvoeringsorganisatie' | 'gemeenschappelijk_orgaan' | NULL
  );
  ```
  Seed for Rotterdam's context: `rotterdam`, `grjr` (9 members), `dcmr` (15 members), `vrr` (15 members), `mrdh` (23 members). Future multi-tenant (WS5b) extends this table per new host-gemeente.

- [ ] **Ingest classifier — tag GRJR-documents with `entity_id='grjr'`.** In [`pipeline/financial_ingestor.py`](../../pipeline/financial_ingestor.py), add a `classify_entity(document)` step before writing `documents`:
  - Title/URL regex — `grjr|jeugdhulp.*rijnmond|gemeenschappelijke.*regeling.*jeugd` → `grjr`
  - Same for DCMR, VRR, MRDH (optional for v0.2.0; at minimum GRJR must land)
  - Default: `rotterdam` with `scope='gemeente'`
  - Classifier decision written to `documents.metadata->>'entity_id'` + `documents.metadata->>'scope'`
  - Backfill script `scripts/reclassify_joint_arrangements.py` runs once over existing financial docs

- [ ] **`gr_member_contributions` table + extractor** — GRJR-jaarstukken always include an `inwonerbijdrage`-table (contribution per member municipality). Same for DCMR/VRR/MRDH. Parse this into structured rows so Rotterdam's share of a regional total can be computed deterministically:
  ```sql
  CREATE TABLE gr_member_contributions (
    id BIGSERIAL PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES financial_entities(id),
    jaar INT NOT NULL,
    member_gemeente TEXT NOT NULL,
    bijdrage_eur NUMERIC(18,2) NOT NULL,
    bijdrage_pct NUMERIC(5,4),               -- derived
    bron_chunk_id BIGINT REFERENCES chunks(id),
    document_id TEXT NOT NULL REFERENCES documents(id),
    sha256 TEXT NOT NULL
  );
  CREATE UNIQUE INDEX ON gr_member_contributions (entity_id, jaar, member_gemeente);
  ```
  Extractor logic in `pipeline/financial_lines_extractor.py`: when a document has `scope='gemeenschappelijke_regeling'`, detect tables containing columns like `(Deelnemer|Gemeente)` + `(Bijdrage|Inwonerbijdrage)` and emit contribution rows alongside the regular `financial_lines` rows for the same document.

- [ ] **`vraag_begrotingsregel` supports regional-with-derivation mode:**
  - Signature: `vraag_begrotingsregel(gemeente, jaar, programma, sub_programma=None, include_gr_derived=False)`
  - When `include_gr_derived=True` and a matching `financial_lines` row has `scope='gemeenschappelijke_regeling'`, the tool joins against `gr_member_contributions` for that `(entity_id, jaar)` and `member_gemeente=gemeente`, then returns both the regional total and the derived Rotterdam share as a structured sibling row:
    ```json
    {
      "matches": [
        {"scope": "gemeenschappelijke_regeling", "entity_id": "grjr",
         "programma": "jeugdhulp", "jaar": 2023, "bedrag_eur": 409000000.00,
         "label": "Lasten totaal GRJR",
         "verification": {"sha256": "...", "source_pdf": "...", "page": 14}},
        {"scope": "derived_share", "entity_id": "grjr",
         "derived_from": "grjr", "member_gemeente": "rotterdam",
         "programma": "jeugdhulp", "jaar": 2023,
         "bedrag_eur": 306750000.00, "bijdrage_pct": 0.75,
         "label": "Rotterdams aandeel (afgeleid)",
         "verification": {"method": "derived", "source_lines": ["<grjr_total_line_id>", "<gr_contribution_line_id>"]}}
      ]
    }
    ```
  - **`derived_share` rows carry `verification.method='derived'`** — they are not byte-identical to a PDF cell (they're arithmetic), so they get a different trust class than direct extractions. The tool description must explicitly tell the consuming LLM: "derived rows are deterministic arithmetic of two verified source rows; they are safe to cite but must always be labeled as 'afgeleid uit bijdrageverdeling'."

- [ ] **`zoek_financieel` always emits scope metadata per result.** In [`mcp_server_v3.py:417`](../../mcp_server_v3.py#L417), extend the rendered output so every chunk surfaces `scope` + `entity.display_name` above its content, e.g. prefix `**Bron-entiteit:** GRJR (Gemeenschappelijke Regeling Jeugdhulp Rijnmond — 9 gemeenten)` when `scope='gemeenschappelijke_regeling'`. Also add a top-level `scope_summary` block at the end of the response when mixed scopes are present in one result set, so the LLM cannot silently aggregate across regional and gemeente data.

- [ ] **OCR quality metadata per chunk** — 2026-04-11 feedback surfaced literal OCR artefacts (`"8egroting 2024"`, `"Wygng"`) in GRJR document chunks. Root cause: Docling falls back to EasyOCR for image-based PDFs with no post-OCR quality check. Add at ingest:
  - `chunks.metadata->>'ocr_backend'` — `'native_pdf' | 'easyocr' | 'tesseract'` (already known by Docling)
  - `chunks.metadata->>'ocr_dict_hit_ratio'` — fraction of tokens matching a Dutch word list (cheap: load once from `data/lexicons/nl_words.txt`, intersection count / total tokens)
  - Default retrieval filter in `zoek_financieel` and `vraag_begrotingsregel`: drop chunks with `ocr_dict_hit_ratio < 0.85` unless no higher-quality alternative exists for the query; when forced to use a low-quality chunk, annotate it in the response: `quality_warning: "ocr_artefacten_waarschijnlijk"`. Never silently return `"8egroting"` as if it were correct data.

- [ ] **Joint-arrangement benchmark questions** — add at least 5 questions to the 30-question `financial_questions.json`:
  - "Wat was de totale GRJR-uitgave voor jeugdhulp behandeling in 2023?" → €73.7 mln (scope=gr, entity=grjr)
  - "Wat was Rotterdam's aandeel in de GRJR-jeugdhulpuitgaven in 2023?" → €306.75 mln derived, with `method='derived'` in verification
  - "Welke gemeenten waren aangesloten bij de GRJR in 2023?" → 9 gemeenten list from `financial_entities.member_gemeenten`
  - "Wat was de inwonerbijdrage van Barendrecht aan de GRJR in 2023?" → exact row from `gr_member_contributions`
  - "Wat is het verschil tussen Rotterdam's eigen jeugdzorgbegroting en Rotterdam's GRJR-aandeel in 2023?" → requires both a `scope='gemeente'` row (Rotterdam's own jeugdhulp-budgetregel) and a `scope='derived_share'` row; exposes the trap the test originally fell into.

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
