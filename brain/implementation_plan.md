# Phase C: Agentic GraphRAG — Implementation Plan

## Pre-flight Audit Results

| Metric | Value |
|---|---|
| Total documents in DB | 71,232 |
| Still at 15k truncation limit | 17,511 (being fixed by `reingest_truncated.py`) |
| Largest document | 2.39M chars (a full raadsvergadering notulen) |
| Docs > 100k chars | 2,860 |
| Docs > 50k chars | 4,749 |

**Document types already in the database:**
| Type | Count |
|---|---|
| Overig (incl. bijlagen, maps, etc.) | 35,370 |
| Brief | 11,830 |
| **Motie** | **9,072** |
| Besluitenlijst | 4,222 |
| Verslag | 2,967 |
| **Raadsvoorstel** | **2,428** |
| Annotatie | 2,073 |
| **Financieel** | **1,761** |
| **Notulen** | **979** |
| **Amendement** | **530** |

**→ Answer to question (i): YES.** All document types are already being ingested indiscriminately — every document attached to every agenda item is downloaded.

---

## i. Zero-Truncation Strategy

### The Problem
`preserve_notulen_text()` removes the 15k limit, but the result is stored as a single Postgres TEXT blob. For a 2.39M char document, this is fine for storage but not for indexing — Gemini's context window (~1M tokens) can handle it for chunking in one pass.

### The Guarantee
1. **No hard character limit anywhere.** The `preserve_notulen_text()` method has `max_length=None`. Postgres TEXT columns support up to 1GB. No truncation will happen.
2. **`reingest_truncated.py`** (already running) will re-fetch the full content for all 17,511 documents that were previously capped at 15k.
3. For **genuinely unextractable content** (e.g., scanned images before OCR era): content will be empty — this is honest, not truncation. The native macOS OCR falls back for these.

---

## ii. Zero-Loss Chunking Architecture

### Core Principle
**Full text is stored in Postgres. Chunks are derived from it. No chunk process deletes source data.**

### For standard documents (< 800k chars)
Gemini receives the full text in one API call and returns semantic chunks:
- Each chunk includes: `title`, `exact text`, `3–5 hypothetical questions`
- Chunks are stored in Postgres `document_chunks` table + Qdrant (with embeddings)
- **Chunk overlap:** 10% overlap between adjacent chunks to preserve context at boundaries

### Large Document Resilience
- **Windowing**: 50k char windows with 20k char overlap.
- **Deduplication**: Implement per-document chunk de-duplication based on text hash.
- **Scaling**: Launch **Worker 1 and Worker 2 immediately**. Increase `MAX_WORKERS` to **5**. 
- **Small-Doc Targeting**: Workers **4 and 5** will prioritize smaller documents (`ORDER BY length ASC`) to### [Database] Search Indexing
- **Trigram Index**: Enable the `pg_trgm` extension in PostgreSQL and create a GIN trigram index on the `name` column of the `agenda_items` table. This allows fast partial matching (e.g. `ILIKE %fraude%`).
- **Hierarchy Awareness**: Ensure `document_chunks` (Grandchild level) also has a GIN index on its `content` if we decide to search there for better snippet precision.

### [Backend] API Search Optimization

#### [MODIFY] [main.py](file:///Users/dennistak/Documents/Final%20Frontier/NeoDemos/main.py)
- **Increased Limit**: Increase the search limit from 20 to 50 to provide more depth across the 71k+ documents.
- **Hierarchy-Aware SQL**: Refactor the search to potentially search across `document_chunks` (Grandchildren) but roll results up to the `agenda_items` (Parent) level. 
- **CTE Optimization**: Use a CTE to identify the top 50 relevant items by rank first. Then, join back to generate `ts_headline` only for those 50 rows, significantly improving speed.
unking strategies per document type (see §iii)

---

## iii. Structure-Aware Chunking for Financial Documents

### Problem
Financial documents contain tables with row/column relationships that break when chunked naively. A budget row like `"Onderwijs | 2024: €12.5M | 2025: €13.1M | Δ: +4.8%"` loses all meaning if split.

### Approach: Document Type Detection + Specialized Chunking

**Step 1 — Classification:** When a document is ingested, classify it:
```
Doc types: motie, amendement, raadsvoorstel, notulen, financieel, brief, overig
```

**Step 2 — Type-specific Gemini chunking prompt:**

- **`financieel`:** Instruct Gemini to treat every table as an atomic unit. Extract tables as structured JSON (`{type: "table", headers: [...], rows: [[...]]}`). This preserves relationships between line items.
- **`motie/amendement`:** Chunk by clause (overwegende dat, verzoekt het college, etc.)
- **`raadsvoorstel`:** Chunk by section (aanleiding, financiën, besluit)
- **`notulen`:** Chunk by speaker turn + topic

**Step 3 — Structured chunk storage:**
The `document_chunks` table gains a `chunk_type` field: `text | table | list | header`.
Tables are stored as both raw text AND structured JSON for future financial queries.

---

## iv. Knowledge Graph Architecture

### Goal
Explicitly map relationships between: documents, people (raadsleden), organizations (fracties), decisions (moties, amendementen), budget lines, policy proposals.

### Technology
**Postgres graph tables** (no extra DB needed) using recursive CTEs for traversal.

### Schema

```sql
-- Entities (nodes)
CREATE TABLE entities (
    id SERIAL PRIMARY KEY,
    type TEXT NOT NULL,      -- 'person', 'fractie', 'topic', 'budget_line', 'document'
    name TEXT NOT NULL,
    metadata JSONB
);

-- Relationships (edges)
CREATE TABLE relationships (
    id SERIAL PRIMARY KEY,
    source_entity_id INTEGER REFERENCES entities(id),
    target_entity_id INTEGER REFERENCES entities(id),
    relation_type TEXT NOT NULL,  -- 'authored', 'voted_for', 'amends', 'references_budget', ...
    document_id TEXT REFERENCES documents(id),
    chunk_id INTEGER REFERENCES document_chunks(id),
    confidence FLOAT DEFAULT 1.0,
    metadata JSONB
);
```

### Relationship types to extract (via Gemini)
| Relation | Example |
|---|---|
| `authored` | Raadslid X authored motie Y |
| `voted_for/against` | Fractie A voted for raadsvoorstel Z |
| `amends` | Amendement B amends raadsvoorstel Z |
| `references` | Notulen quote from person X |
| `references_budget` | Motie Y references budget line "Klimaat €4.2M" |
| `mentions_topic` | Document Z mentions topic "windenergie" |

### Extraction pipeline
For each chunked document, a second Gemini call extracts entity mentions and relationships from each chunk. Low-confidence extractions (< 0.7) are flagged for review.

---

### Phase B — Large Scale Re-indexing (IN PROGRESS)
- **Goal**: Finish re-embedding 1.3M chunks into 4096D Qdrant.
- **Status**: 32% Complete (Index 427,000+).
- **ETA**: ~72-96 hours.

---

### Phase C — Historical Video Reconstruction (NEXT)

> [!IMPORTANT]
> This is our primary post-migration goal. We are recovering spoken transcripts for committee meetings (2018-2022) where text was missing.

1.  **Transcription Pipeline**:
    *   **Audio Extraction**: Use `ffmpeg` to pull HLS audio streams.
    *   **Local Inference**: Use `mlx-whisper` (Large-v3 Turbo).
    *   **Validated Quality Tiering** (Based on Dutch ASR Benchmarks):
        *   **Gold (≥ 0.85)**: High reliability. Matches standard training-data filters (4.4% WER). Valid for "Virtual Notulen."
        *   **Silver (0.60–0.84)**: Meaningful context but higher Word Error Rate (~15-20%). Labeled as "AI-gegenereerd" with a warning for names/figures.
        *   **Bronze (< 0.60)**: High hallucination risk. Hidden from default search; only accessible via "Deep Research" toggle.

2.  **Surgical OCR Pipeline**:
    *   **Frame Extraction**: Use `ffmpeg` to pull 1 frame at speaker-change timestamps.
    *   **Vision Linking**: Use Gemini 1.5 Flash to extract "Name Plate" text from frames.
    *   **Role Mapping**: Map extracted names to historical roles (e.g. Inspreker vs Councillor).

---

### Phase D — Agentic GraphRAG (De-constructed)

> [!NOTE]
> Entity extraction from your Shadow PC will be merged here. The goal is to move from "Finding Chunks" to "Understanding Connections."

#### 1. Entity Extraction (Shadow PC Merge)
- **Status**: **COMPLETE** (Ready for Merge).
- **Data Source**: `data/entity_extraction/data/entity_resolution_map.json`.
- **Strategy**: 
    - Instead of re-extracting entities, we will **import** the 1.3M resolved entities directly into our Postgres `entities` table.
    - **Audit Step**: Cross-reference the `document_id` in the Shadow PC data with our new Postgres `documents` table to ensure 100% alignment.
    - **Optimization**: This saves us ~3,000 hours of GLiNER/LLM compute time.

#### 2. Relationship Extraction (Deep Extraction Integration)
- **Data Source**: `data/entity_extraction/data/triplets_deep_extraction.jsonl`.
- **Strategy**: 
    - The "Deep Extraction" triplets (Subject -> Relation -> Object) will be the foundation of our `relationships` table.
    - **Gap Filling**: We only run **Qwen3.5-27B** relationship extraction on the "Gold" tier documents (Phase C) to fill in missing 2024-2026 links.

We will use an MLX-optimized local model on your M5 Pro (64GB RAM).

| Model Option | RAM Usage (Quantized) | Expected Speed | Dutch Language Proficiency |
|---|---|---|---|
| **Qwen3.5-27B-4bit-DWQ** | **~15.6 GB** | **~15-20 tokens/sec** | **Ultra**: Next-gen reasoning with expanded support for 201 languages (Elite Dutch). |
| **Qwen2.5-Coder-32B (4-bit)** | ~22-26 GB | 18-25 tokens/sec | **Elite**: Great for JSON, but superseded by 3.5 reasoning depth. |
| **DeepSeek-V3 (4-bit)** | ~38-42 GB | 10-15 tokens/sec | **High**: Heavy RAM footprint; less efficient for 64GB unified memory limits. |

> [!TIP]
> **Coach Choice**: **Qwen3.5-27B-4bit-DWQ**. This is the state-of-the-art for your machine. At only 15.6 GB RAM, we can run this *simultaneously* with the Qdrant 4096D index (~16GB) while still having 30GB+ free for the OS and background tasks.

#### 3. Source Mapping for Voting & Financials

To build the knowledge graph accurately, we target these high-value document types:

1.  **`Besluitenlijst`**: Explicit results (Aangenomen/Verworpen).
2.  **`Notulen`**: The "Why" and debate context.
3.  **`Financieel`**: Budget tables and financial line items (detected via keyword/table audit).

**Coach Strategy**: We target only the ~9,000 documents of these types. We skip the "silent majority" of attachments to ensure the Graph remains high-signal and compute costs remain low.

#### 3. Graph Construction (Postgres + pgvector)
- **Goal**: Store nodes and edges.
- **Logic**: Use recursive CTEs to allow the "NeoDemos Analyse" to traverse 3-4 levels of relationships (e.g., finding all motions influenced by a specific budget shift).

#### 4. Community Summarization (Leiden Algorithm)
- **Goal**: Cluster related entities into "Topical Communities."
- **Output**: Pre-computed summaries that describe broad political trends (2018-2026).

---

### Architectural Evaluation & Accuracy Review

**1. Is this the most accurate approach for long texts, financials, and statistics?**
*Yes, it is considered the state-of-the-art approach for mixed data.* Traditional RAG is highly accurate at finding small text snippets but it fundamentally destroys structured, tabular data (like municipal budgets) because it forces tabular data into generic text embeddings. Agentic GraphRAG explicitly maps statistical data (like budget variances) and structured JSON into the graph itself. This ensures the AI can reason analytically over financial data rather than guessing based on nearby text.

**2. Token cost scale (How many tokens per query?)**
*Highly Efficient: ~1,500 to 4,000 input tokens per query.* Surprisingly, GraphRAG significantly reduces token costs at query time. Instead of forcing the LLM to read 50 raw PDF pages to figure out a multi-year policy trend (which would cost 50,000+ tokens and suffer from the "lost in the middle" problem), the agent retrieves the explicitly pre-computed "Global Community Summary" (~500 tokens) and specific graph edges. The heavy lifting is done upfront.

**3. Infrastructure portability (Other Cities / National Parliament)**
*Extremely portable.* The core processing engine (`kg_entities`, `kg_relationships`, semantic routing) is 100% agnostic to Rotterdam. The semantic concepts of a *Politician*, *Party*, *Policy*, and *Budget* apply universally across the Netherlands. To deploy to Amsterdam or the Tweede Kamer, we simply point the Phase A ingestion scripts to their respective public APIs; the Phase B and Phase C chunking/graphing pipelines require no architectural changes.

**4. Are there simpler approaches that yield the same outcome?**
*No.* The simpler approach is "Naive RAG" (just embedding raw text chunks). While Naive RAG is vastly easier to build, it completely fails at:
- **Global Questions** (e.g., "Summarize the housing debate from 2018-2023"). Naive RAG will just pull 10 random housing quotes and miss the big picture.
- **Structured Aggregation** (e.g., "What is the true delta for healthcare?"). Naive RAG cannot perform structural lookups across multiple tables.
Agentic GraphRAG absorbs the massive complexity upfront (which the 20 workers are doing right now) so the actual end-user querying remains highly accurate, cheap, and robust.

---

### Structured Financial Extraction (Decision)
**Decision:** Full JSON extraction approved. The pipeline explicitly extracts financial tables into queryable JSON, enabling precise, structured financial queries alongside standard vector search.

---

---

## Phase D — Productization & Beta Launch (QUEUED)

This phase focuses on turning the technical capabilities of Phase B and C into a polished, user-centric product ready for testing with a select group of users.

### 1. Unified Interface & Search
- **Smart Search:** Implement a dedicated search interface to find specific meetings, topics, and documents using the hybrid retrieval engine.
- **UI/UX Polish:** Refactor the current interface to be tighter, cleaner, and more professional (Premium Design).
- **Mobile Feedback:** Ensure responsiveness for mobile usage by councillors on the go.

### 2. Specialized Workflows
- **Citizen-Facing Chatbot:** A simplified, accessible RAG interface for the general public to query council activity.
- **Councillor Workflow Tools:**
    - **Drafting Tooling:** Tools to copy and edit 'NeoDemos analyse' output directly in the app.
    - **Speech Generation ('Bijdrage'):** A tool to generate a short, structured speech (bijdrage) for committees based on documents, within specific time constraints.
    - **Conclusion Synthesis:** Automatically ending speeches with specific questions, motions, or amendments derived from the analysis.

### 3. Data Integrity (2018–2026)
- **Deep Back-fill:** Perform a final verification pass to ensure 100% of meetings, agendas, and documents for the **2018–2026 period** are fully ingested, chunked, and graphed.
- **Gap Analysis:** Identify and fill any missing meeting cycles from the historical logs.

### 4. Readiness for Beta Testing
- Phase D completion marks the threshold for a **Controlled Beta Launch** with a select group of City Councillors and heavy users.
