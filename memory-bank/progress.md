# Progress

**Completed Features**:
- FastAPI Backend Setup & Route handling
- UI implementation for Meetings and Agenda exploration
- Data Fetching from Open Raadsinformatie API
- Storage migration to PostgreSQL
- Basic LLM heuristic & prompt-based summaries
- RAG Setup: Semantic chunking script `compute_embeddings.py` and Qdrant integration.
- Unified Party-Lens Analysis & historical LLM grounding.

**Immediate To-Do**:
  - [x] Expand calendar data limits (API/UI size 500)
  - [x] Implement parallel background ingestion (2024-2026)
  - [x] Group "Betrekken bij" agenda items
  - [x] Refine Calendar UI (Compact layout + Dropdowns)
  - [x] Fix "Onbekend" core value mapping bug `services/storage.py`'s `get_meeting_details()`.
- [x] Correctly classify 1.x agenda items in 'COR' meetings as substantive.

**Long-Term Roadmap**:
- Phase 4: Enhanced UI & Personalization
  - Implement "My Committees" selection filter
  - Final polish and user testing / Verification
- Porting to iOS / MacOS applications leveraging the current Web APIs.
