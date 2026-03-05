# System Patterns

**Architecture**:
- **Backend & API**: Python 3.13 / FastAPI.
- **Frontend**: Jinja2 templates served by FastAPI. Vanilla CSS for styling (premium aesthetics focused). No heavy SPA frameworks currently in use.
- **Database**: PostgreSQL (for structured relational data: meetings, agenda items, documents, and ingestion logs).
- **Vector Store**: Qdrant (local Docker container) for Retrieval-Augmented Generation (RAG) embeddings.
- **AI Provider**: Google GenAI (`gemini-3-flash-preview` for synthesis/scoring; `gemini-embedding-001` for vector chunking).

**Design Principles & Patterns**:
- **Lazy Initialization / Caching**: The `PolicyLensEvaluationService` is cached in memory per-party to avoid reloading static JSON profile files on every request.
- **Fail-Safes**: All AI generation includes heuristic fallbacks (e.g., keyword scoring) if the LLM API fails or times out.
- **Substantive Filtering**: Meetings contain many procedural items (e.g., "Ingekomen stukken"). The `StorageService` implements logic (`is_substantive_item()`) to filter out procedural junk from analysis.
- **Memory Bank Loop**: Update `activeContext.md` and `progress.md` automatically after significant tasks. Log bugs or project preferences as permanent rules here in `systemPatterns.md`.

**(Future) Rules to enforce**:
- Treat "Betrekken bij [x]" agenda items as sub-components of the primary agenda item.
- Committees named "Commissie tot onderzoek van de Rekening" (COR) have substantive agenda items even when numbered 1.x.
