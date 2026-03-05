# NeoDemos Evolution: Roadmap & Progress Status
*Last Updated: 2026-03-05*

## 🗺️ The Processing Flow

This roadmap defines the transition from a flat document database to a high-intelligence Agentic RAG system.

### Phase A: Data Ingestion (COMPLETED)
*   **Action:** Scraped 71,232 documents from the Rotterdam API (meetings, motions, minutes).
*   **Result:** Established the raw document database.
*   **Baseline:** Generated 71,060 high-level summaries using Gemini.

### Phase B: Semantic Intelligence (IN PROGRESS)
*   **Status:** Granular Semantic Chunking & Vectorization.
*   **Goal:** Break documents into ~50k char overlapping sections for precise needle-in-a-haystack retrieval.
*   **Stack:** `smart_controller.py` (20-worker swarm) + Qdrant Vector Store + Gemini 2.5 Flash Lite.
*   **Progress:** ~11,227/71,027 documents fully chunked (~15.8%).

### Phase C: Agentic GraphRAG (QUEUED — MANUAL TRIGGER)
*   **Trigger:** **Will not start automatically.** Requires explicit user approval after Phase B verification.
*   **Goal:** Build a Knowledge Graph (Entities & Relationships).
*   **Result:** Ability to query complex relationships like *"Which parties consistently oppose budget increases for mobility?"*

### Phase D: Productization & Beta Launch (QUEUED)
*   **Goal:** Translate intelligence into a polished end-user product.
*   **Key Features:** Citizen Chatbot, Councillor speech drafting tool, 2018-2026 data integrity, and premium UI/UX.
*   **Milestone:** Ready for testing with real city councillors and users.

---

## 📈 Budget Cycle Completion Stats
*Based on documents matching: 'begroting', 'voorjaarsnota', 'eindejaarsbrief', etc.*

| Year Range | Total Docs | % Completion | Status |
| :--- | :--- | :--- | :--- |
| **All Time** | **1,712** | **7.4%** | [||........] |
| **2023-2026**| 425 | 2.6% | [|.........] |
| **2018-2022**| 626 | 7.5% | [||........] |
| **2002-2017**| 648 | 13.9%| [|||.......] |

---

## 🧪 Testing the Baseline
Before moving to Phase C, we will use the **scripts/quick_rag_test.py** tool to verify query coherence.

**Run a test query:**
```bash
.venv/bin/python3 scripts/quick_rag_test.py "Uw vraag hier"
```

---

## 💻 Hardware Guardrails (M1 8GB Optimization)
*   **Postgres:** Stable; utilizes disk-based storage with negligible RAM footprint (~0.2% per process).
*   **Qdrant:** Currently utilizing only **~172MB (2.1%)** of RAM for the collection. It is highly optimized for Apple Silicon.
*   **Python Swarm:** Sharing memory across threads. Overall system load is stable. If system lag occurs, we can scale down to 10-12 workers without losing progress.
*   **Graph RAG:** Extraction happens on Google's chips (via API); the local graph building is optimized for low memory overhead.
