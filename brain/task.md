# Task Tracking: NeoDemos Evolution

## Phase 2: Unified Analysis & User Profile (Final Polish)
- [x] Create/Update 'Profiel' (Settings) page.
- [x] Add single-select for Political Party preference.
- [x] Add multi-select for Committees.
- [x] Implement storage/retrieval via localStorage.
- [x] Fix JavaScript ReferenceErrors in `analyseAgendaItem`.
- [x] Update Committee names in `settings.html` to match actual Rotterdam data.
- [x] Hide 'Gemeenteraad' from selection but keep it implicitly active.

## Phase 18: Restore Frontend Dependencies
- [x] Add `marked.js` to `base.html`.
- [x] Verify standard search rendering.

## Phase 3: Historical Data Ingestion (The Big Search)
- [x] Research `rotterdam.raadsinformatie.nl` API/HTML structure for scraping.
- [/] Develop a scraper that can go back past 2018 (target: 1993+). (70k+ documents ingested so far)
- [/] Map historical documents to the RAG system.
- [/] Populate database with historical meetings, agenda items, and minutes (notulen).
- [/] Extract historical quotes from political parties for future context.
- [/] P0: Fix 15k char truncation — use preserve_notulen_text() in all ingestion scripts.
- [/] P0: Re-ingest 17,511 truncated documents with full content (17,344/17,511 corrected).
- [/] P1: Phase B1 — Run embedding pipeline (71,027 docs).
  - [x] Swarm scaling restored (20 workers active).
  - [x] Perform bulk duplicate cleanup in database (13,809 docs marked done).
  - [x] Scale swarm to 25 workers.
  - [/] Phase B1: Swarm Processing (In Progress: 21,461/71,027 done).
  - [x] JSON repair logic implementation.
  - [x] Optimized Large-Doc Splitting (50k chars with 20k overlap).
  - [x] Implemented Section Heartbeat Logging (Visibility for long docs).
  - [x] Batch Storage Optimization (100-chunk batches for Qdrant payload safety).
  - [x] Parallel Scaling (4 immediate workers, 6 success-gated, W7-10 target small docs).
  - [x] Cross-boundary Continuity & De-duplication (Text-hash based).
  - [x] Title-based Duplicate Prevention (Prevents re-processing same-named documents).
  - [x] JSON Robust Repair (Handles unescaped control characters; recovered 24 affected docs).
- [ ] P1: Phase B2 — OCR Re-ingestion of 1,361 scanned PDFs (after B1 completes).
  - [ ] Identify all documents with < 500 chars or exactly 15k chars.
  - [ ] Download PDFs and run OCR via existing Swift `ocr_pdf` tool.
  - [ ] Update documents.content with OCR'd text.
  - [ ] Re-chunk updated documents via swarm mop-up pass.
- [x] P2: Phase C — Update extraction model to Gemini 2.5 Flash Lite (DONE).
- [ ] P2: Phase C — Run Gemini entity extraction over all chunks to populate graph (MANUAL TRIGGER).
  - [ ] Extract Speakers (*notulen* speaker turns).
  - [ ] Map Speakers → Political Parties (*fracties*).
  - [ ] Identify Topic-Specific Stance (Pro/Anti/Nuanced).
  - [ ] Link Multi-dimensional relationships between entities.
- [ ] P2: Phase D — Productization & Beta Launch Prep.
  - [ ] Implement Citizen-facing Chatbot (Simplified RAG).
  - [ ] Build Councillor Workflow Tools (Speech/Bijdrage generation).
  - [ ] Audit & Fill 2018-2026 meeting/document gaps.
  - [ ] Polish UI/UX for Premium feel and Mobile readiness.
  - [ ] Implement Search interface for meetings/topics.
- [x] Optimization: Switched to Greedy Bin Packing (Large docs first when headroom allows).

## MCP Query Quality Fixes (from FEEDBACK_LOG 2026-04-10)

- [ ] **[BUG/HIGH] `zoek_moties`: initiatiefvoorstel gemist door naam-filter**
  - Root cause: `mcp_server_v3.py:977` filtert alleen op `d.name ILIKE '%initiatiefvoorstel%'`.
    Documenten waarvan de naam niet letterlijk "initiatiefvoorstel" bevat (bijv. "Voorstel
    Engberts en Vogelaar - Leegstand") vallen er volledig buiten, vóór de zoektermen lopen.
  - Fix: voeg content-fallback toe aan de type-gate:
    ```sql
    (LOWER(d.name) LIKE '%motie%'
     OR LOWER(d.name) LIKE '%amendement%'
     OR LOWER(d.name) LIKE '%initiatiefvoorstel%'
     OR LOWER(d.content) LIKE '%initiatiefvoorstel%')   -- ← nieuw
    ```
  - Aanvullend: voeg `document_type` kolom toe aan `documents` tabel (classificatie al
    aanwezig in `raadsvoorstel_extraction_service.py:95`); gebruik die als primaire filter
    zodat we niet meer afhankelijk zijn van naam-matching.

- [ ] **[BUG/MEDIUM] `zoek_moties`: trage response bij overzichtsvragen**
  - Root cause: meerdere `lees_fragment` calls worden sequentieel uitgevoerd; geen fan-out.
  - Fix: MCP-tool retourneert al `LEFT(d.content, 400)` — evalueer of preview voldoende is
    zodat aparte `lees_fragment` calls voor de meeste overzichtsvragen overbodig worden.
    Als de preview te kort is: verhoog naar 800 chars of voeg een `include_preview: bool`
    param toe die de caller laat kiezen.

- [ ] **[BUG/HIGH] Financiële pipeline: stille hallucination bij ontbrekende waarden**
  - Root cause: bij ontbrekende jaardata vult het model gaten op met schattingen zonder
    disclaimer. Clusterindeling varieert per jaar ("Stedelijke ontwikkeling" vs "SIO").
  - Fix 1: nooit een numerieke waarde retourneren zonder een primaire bron-chunk; voeg een
    `bronvermelding`-veld toe aan `zoek_financieel`-resultaten.
  - Fix 2: implementeer IV3-taakvelden als canonieke aggregatielaag (zie
    findo.nl/content/taakvelden-gemeenten) zodat clusters consistent zijn over jaren heen.

## Phase 4: Video & Rich Context (Future)
- [ ] Investigate `rotterdamraad.bestuurlijkeinformatie.nl` for video links.
- [ ] Process video recordings for the RAG platform.
- [ ] Sentiment Tracking over time.
