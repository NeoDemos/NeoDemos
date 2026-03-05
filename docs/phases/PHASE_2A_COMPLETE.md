# Phase 2a - Party Analysis Infrastructure: COMPLETE

## Summary

Successfully built the foundational infrastructure for political party analysis in NeoDemos. Phase 2a focused on data acquisition and organization, establishing all necessary systems for extracting and analyzing party positions from notulen (meeting minutes) and election programmes.

## Accomplishments

### 1. Database Schema Expansion ✓
**File**: `scripts/create_party_analysis_schema.py`

Created 5 new PostgreSQL tables:
- **topics** (21 policy categories): Stores policy topics derived from Rotterdam's council committee structure
  - Covers all major council areas: Werk & Inkomen, Onderwijs, Zorg, Bouwen & Wonen, Klimaat, etc.
  - Includes cross-cutting themes: Burgerparticipatie, Digitalisering, Migratie & Integratie
  
- **party_programmes**: Stores party election programmes (PDF content + metadata)
  - Fields: party_name, election_year, file_path, extraction_status, pdf_content
  - Tracks ingestion and analysis status
  
- **party_positions**: Extracted positions on topics from programmes and notulen
  - Links party name → topic → position text
  - Tracks source (programme vs. notulen), confidence scores, analysis metadata
  
- **party_statements**: Individual statements extracted from notulen
  - Tracks speaker, meeting, date, topic, context
  - Enables detailed statement-level analysis
  
- **document_classifications**: Classifies documents as notulen and tracks extraction status
  - Marks notulen for later analysis
  - Tracks extraction completion

### 2. Topic Categories Initialization ✓
**File**: `scripts/initialize_topics.py`

Populated 21 policy topics based on Rotterdam's council structure:

**Committee-Based Topics** (8):
- Werk & Inkomen, Onderwijs, Samenleven, Schuldhulpverlening & Armoedebestrijding
- Zorg & Welzijn, Cultuur, Sport
- Bouwen & Wonen, Buitenruimte & Groen
- Mobiliteit & Verkeer, Haven & Scheepvaart
- Economie & Bedrijven, Klimaat & Milieu
- Veiligheid, Bestuur & Organisatie, Financiën

**Cross-Cutting Topics** (5):
- Burgerparticipatie, Digitalisering, Genderbeleid
- Migratie & Integratie, Ruimtelijke Ordening

### 3. Notulen Acquisition ✓
**File**: `scripts/fetch_notulen_v2.py`

Successfully fetched and stored **97 notulen documents**:
- Strategy: Direct search for "notulen" keyword in OpenRaadsinformatie API
- Fetched 111 documents, successfully stored 97 (87% success rate)
- Handles PDFs from multiple formats and sources
- Stored full text content for later analysis
- Marked all as notulen in document_classifications table

**Data Quality Note**: Notulen are from multiple Dutch municipalities, not just Rotterdam. Phase 2b should filter for Rotterdam-specific notulen when conducting party analysis.

### 4. GroenLinks-PvdA 2025 Programme Ingestion ✓
**File**: `scripts/ingest_party_programme.py` + `scripts/ingest_party_programme.py`

Successfully ingested GroenLinks-PvdA 2025 election programme:
- **File**: `Verkiezingsprogramma-2025-glpvda_PRINT-DEF-DEF-DEF.pdf`
- **Content**: 195,479 characters across 94 pages
- **Stored**: Complete PDF content in `party_programmes` table
- **Sections Identified**: 1088 distinct sections identified (includes both major policy areas and detailed paragraphs)

### 5. Database Modifications
- Made `documents.agenda_item_id` nullable (notulen aren't linked to agenda items)
- Made `documents.meeting_id` nullable (notulen exist independently)

## Data Integrity

### Topics Table
```
21 topics ingested, 0 duplicates
Coverage: All major Rotterdam council committees + cross-cutting themes
```

### Documents
```
Notulen in system: 97
Notulen classification status: marked for analysis
Party programmes: 1 (GroenLinks-PvdA 2025)
```

## Key Files Created

```
scripts/
├── create_party_analysis_schema.py    (DB schema creation)
├── initialize_topics.py                (Topic population)
├── fetch_notulen_v2.py                 (Notulen fetching)
└── ingest_party_programme.py            (Programme PDF ingestion)

Database Tables:
├── topics (21 rows)
├── document_classifications (97 notulen classified)
├── party_programmes (GroenLinks-PvdA 2025 ingested)
├── party_positions (empty - ready for analysis)
└── party_statements (empty - ready for extraction)
```

## Next Steps (Phase 2b - AI Analysis)

The infrastructure is now ready for Phase 2b:

1. **Develop AI Analysis Pipeline**
   - Design prompt strategy for Gemini 2.5 Flash
   - Implement token-efficient chunking
   - Extract positions from GroenLinks-PvdA programme → party_positions table

2. **Extract Notulen Statements**
   - Identify party statements in notulen
   - Classify by topic using AI
   - Link to topics and store in party_statements table

3. **Create Comparison Interface**
   - Side-by-side programme vs. notulen positions
   - Topic-based filtering
   - Party position tracking over time

4. **Expand to Other Parties**
   - Replicate pipeline for other parties (VVD, D66, SP, CDA, etc.)
   - 12 party programmes available in source directory

## Technical Decisions

1. **Direct API Search** (not agenda-based): Notulen are published independently, not in meeting agendas
2. **Nullable Foreign Keys**: Notulen don't reference specific meetings or agenda items
3. **Full PDF Storage**: Store complete programme text for comprehensive analysis
4. **Topic-First Approach**: Topics derived from council structure (not programme structure) for consistency
5. **Status Tracking**: extraction_status field allows future batching and progress monitoring

## Database Queries for Verification

```sql
-- Check topics
SELECT COUNT(*), COUNT(DISTINCT keywords) FROM topics;
-- Result: 21 topics with keyword arrays

-- Check notulen
SELECT COUNT(*) FROM documents 
WHERE id IN (SELECT document_id FROM document_classifications WHERE is_notulen = TRUE);
-- Result: 97 notulen documents

-- Check programme
SELECT party_name, election_year, LENGTH(pdf_content) as content_length
FROM party_programmes;
-- Result: GroenLinks-PvdA, 2025, 195,479 chars
```

---

**Status**: Phase 2a infrastructure complete and validated. Ready to begin Phase 2b (AI analysis pipeline).
