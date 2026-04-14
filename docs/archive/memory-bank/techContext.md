# Technical Context

**Tech Stack**:
- **Python**: 3.13 (managed via `.venv`)
- **Web Framework**: FastAPI, Uvicorn, Jinja2
- **Database**: PostgreSQL (`psycopg2-binary`, heavily nested dictionary mapping for JSON APIs)
- **Vector DB**: Qdrant (Docker)
- **LLM**: Google GenAI Python SDK (`google-genai`)
- **PDF Extraction**: `pypdf`
- **Task Scheduling**: `APScheduler`

**Development Environment**:
- **OS**: macOS
- **Environment Variables**: Managed via `.env` (contains `GEMINI_API_KEY`, DB credentials, etc.)
- **Background Processes**: `compute_embeddings.py` runs persistently to chunk and embed documents into Qdrant.
- **Ingestion**: `ingest_data.py` must run periodically or manually to populate SQLite/Postgres with Open Raadsinformatie blobs.

**Key Dependencies Notes**:
- Use `psycopg2` for direct query execution (`RealDictCursor` favored for dictionary mapping).
- `gemini-3-flash-preview` must be specified as the model ID for text generation. `gemini-embedding-001` for embeddings.
