# Phase 3: Historical Data Ingestion Implementation Plan

The goal of this phase is to rapidly expand the NeoDemos knowledge base by scraping historical meeting data from `rotterdam.raadsinformatie.nl`, going back to 1993 where possible. This will provide a rich dataset for the RAG system to draw upon for party vision and historical context.

## User Review Required

> [!IMPORTANT]
> Scraping a large volume of historical data (1993-present) will take significant time and potentially consume large amounts of disk space for PDFs. I will focus on 'Notulen' (minutes) first as they contain the most valuable historical context (quotes/positions).

## Proposed Changes

### [Component: Scraper (`scripts/historical_ingest.py`)]
#### [NEW] [historical_ingest.py](file:///Users/dennistak/Documents/Final Frontier/NeoDemos/scripts/historical_ingest.py)
A standalone script to:
1.  **Discover**: Use the `raadsinformatie.nl` search API to find documents matching "Notulen" or "Verslag".
2.  **Crawl**: Follow links to meeting pages (`/vergadering/{id}`).
3.  **Extract**: Parse meeting metadata (date, committee, agenda items).
4.  **Download**: Fetch PDF documents.
5.  **Process**: Use OCR/PDF extraction to convert documents to text.
6.  **Store**: Upsert data into the PostgreSQL `meetings`, `agenda_items`, and `documents` tables.

### [Component: Database / Storage]
- Ensure the `documents` table can handle large amounts of text.
- Optimize indexing for historical searches.

### [Component: RAG / AI Service]
- Trigger re-indexing of the RAG system as new data is ingested.
- Refine the 'Historical Context' prompt in `LLMAlignmentScorer` to utilize this new depth of data.

---

## Verification Plan

### Automated Tests
- Run the scraper for a small date range (e.g., 2000-2001) and verify that:
    - Meetings are created in the DB.
    - PDFs are downloaded and converted.
    - Agenda items are linked correctly.

### Manual Verification
- Verify in the NeoDemos UI that historical meetings appear in the calendar/search.
- Check that the 'Analyse' feature now cites older records for "Historische Context".
