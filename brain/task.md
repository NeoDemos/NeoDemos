# Task Tracking: NeoDemos Evolution

## Phase 2: Unified Analysis & User Profile (Final Polish)
- [x] Create/Update 'Profiel' (Settings) page.
- [x] Add single-select for Political Party preference.
- [x] Add multi-select for Committees.
- [x] Implement storage/retrieval via localStorage.
- [x] Fix JavaScript ReferenceErrors in `analyseAgendaItem`.
- [x] Update Committee names in `settings.html` to match actual Rotterdam data.
- [x] Hide 'Gemeenteraad' from selection but keep it implicitly active.

## Phase 3: Historical Data Ingestion (The Big Search)
- [x] Research `rotterdam.raadsinformatie.nl` API/HTML structure for scraping.
- [/] Develop a scraper that can go back past 2018 (target: 1993+). (70k+ documents ingested so far)
- [/] Map historical documents to the RAG system.
- [/] Populate database with historical meetings, agenda items, and minutes (notulen).
- [/] Extract historical quotes from political parties for future context.
- [/] P0: Fix 15k char truncation — use preserve_notulen_text() in all ingestion scripts.
- [/] P0: Re-ingest 17,511 truncated documents with full content (17,344/17,511 corrected).
- [/] P1: Phase B1 — Run embedding pipeline (71,027 docs).
  - [x] Swarm scaling (20 workers active).
  - [/] Phase B1: Swarm Processing (In Progress: 11,227/71,027 done).
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

## Phase 4: Video & Rich Context (Future)
- [ ] Investigate `rotterdamraad.bestuurlijkeinformatie.nl` for video links.
- [ ] Process video recordings for the RAG platform.
- [ ] Sentiment Tracking over time.
