# NeoDemos RAG Pipeline — Skill Reference

Use this skill when working on retrieval, chunking, embedding, or the ingestion pipeline.

> **Read `docs/architecture/EMBEDDING_PIPELINE_RUNBOOK.md` first.** It is the authoritative source on embedding parameters and overrides anything here if they conflict.

## System Architecture

```
PostgreSQL (localhost:5432/neodemos)
  ├── meetings, agenda_items, documents
  ├── document_children (parent sections, tier 1)
  ├── document_chunks (grandchild fragments, tier 2) + text_search (tsvector)
  └── embedding_queue (planned, not yet created)

Qdrant (localhost:6333, standalone server, NOT Docker)
  └── notulen_chunks: 607K+ points, 4096D cosine, INT8 scalar quantization, on_disk=True

Embedding model: Qwen3-Embedding-8B-4bit-DWQ via MLX (Apple Silicon)
Reranker: CrossEncoder ms-marco-multilingual-MiniLM-L-12-v2 (multilingual; skipped in fast_mode)
```

## Retrieval Pipeline (services/rag_service.py)

**Hybrid search:** Vector (Qdrant) + BM25 keyword (PostgreSQL tsvector) → Reciprocal Rank Fusion → optional CrossEncoder reranking.

**4 parallel search streams:** vision (ideology), financial (budgets), debate (minutes), fact (rules). Each stream gets its own query via `retrieve_parallel_context()`.

**Key parameters:**
- `fast_mode=True` — skips CrossEncoder, saves 3-8s. Used by MCP tools and all AIService RAG calls.
- `date_from`/`date_to` — ISO date strings, filter via Qdrant `DatetimeRange` on `start_date` payload + SQL JOIN to `meetings.start_date`.
- `score_threshold=0.15` — intentionally relaxed for recall; reranker handles precision.

### Precision-Critical Search Configuration

For a 607K+ point collection with fine-grained retrieval needs, these Qdrant search parameters are **non-negotiable**:

```python
from qdrant_client.models import SearchParams, QuantizationSearchParams

results = client.query_points(
    collection_name="notulen_chunks",
    query=query_embedding,
    limit=top_k,
    score_threshold=0.15,
    query_filter=query_filter,
    search_params=SearchParams(
        hnsw_ef=256,          # Default 128. Higher = better recall, slightly slower.
                              # At 607K points, 256 is the sweet spot for precision.
        quantization=QuantizationSearchParams(
            rescore=True,     # CRITICAL: Re-scores top candidates with original
                              # (non-quantized) vectors. Without this, INT8 quantization
                              # loses ~3-5% precision on fine-grained queries.
            oversampling=2.0, # Fetch 2x candidates before rescoring for better pool.
        ),
    ),
)
```

**Why this matters:** Qdrant's evaluation guide (qdrant.tech/blog/rag-evaluation-guide) confirms that quantized collections must use `rescore=True` to maintain Precision@k. Without it, approximate scores from INT8 quantization silently degrade fine-grained retrieval.

### Payload Indexing

Ensure these payload indexes exist on `notulen_chunks` for fast filtered search:

```python
from qdrant_client.models import PayloadSchemaType

client.create_payload_index("notulen_chunks", "start_date", PayloadSchemaType.DATETIME)
client.create_payload_index("notulen_chunks", "document_id", PayloadSchemaType.KEYWORD)
client.create_payload_index("notulen_chunks", "title", PayloadSchemaType.TEXT)
```

Without payload indexes, date-filtered queries do a linear scan over all 607K points. With indexes, Qdrant uses an inverted index for O(log n) filtering.

**Check existing indexes:** `GET http://localhost:6333/collections/notulen_chunks` → look for `payload_schema` in the response.

### Reranking Strategy

**Model choice:** Use `cross-encoder/ms-marco-multilingual-MiniLM-L-12-v2` (not the English-only L-6 variant). Our corpus is Dutch municipal documents — an English-only reranker systematically underscores Dutch text, especially political terminology.

**Reranker threshold:** Filter at `-2.0` (not `-5.0`). The CrossEncoder outputs log-odds scores where:
- `> 0.0` = likely relevant
- `-2.0 to 0.0` = possibly relevant (keep for recall)
- `< -2.0` = almost certainly irrelevant (safe to discard)

At `-5.0`, virtually nothing is filtered, making the reranker just a sorter. At `-2.0`, clearly irrelevant noise is removed while preserving borderline-relevant chunks.

**Candidate pool:** Feed `top_k * 5` candidates to the reranker. Research shows diminishing returns beyond 5x oversampling for cross-encoders.

### Evaluation (Measuring Retrieval Quality)

**Metrics to track** (from Qdrant's RAG Evaluation Guide):
- **Precision@10** — What fraction of the top-10 results are actually relevant? Target: >0.6
- **MRR (Mean Reciprocal Rank)** — Where does the first relevant result appear? Target: >0.7
- **NDCG@10** — Does the ranking order match relevance grades? Target: >0.5

**How to evaluate:**
1. Build a test set: 30-50 Dutch municipal queries with known-relevant chunk IDs (manually curated).
2. Run retrieval pipeline, measure Precision@k and MRR.
3. Compare: vector-only vs. hybrid vs. hybrid+reranker.
4. Use `rag_evaluator/` directory (already exists) for evaluation scripts.

**Recommended frameworks:** Ragas (Python, open-source), Quotient AI, Arize Phoenix.

## Embedding Rules (from the Runbook)

**These are non-negotiable:**
1. **Single-item embedding only.** Never batch-embed mixed-length texts through MLX — padding pollutes mean pooling and produces NaN/Inf vectors.
2. **Qdrant upsert batch size: 16** (`wait=False`).
3. **Point IDs are deterministic:** `int(md5(f"{doc_id}_{db_id}")[:15], 16)` — re-running is idempotent.
4. **GPU flush every 64 chunks:** `mx.synchronize()` + `mx.clear_cache()` + `gc.collect()`. Without this, MLX silently hangs.
5. **Threaded timeout (120s)** on each embedding call. On timeout → `os.execv` self-restart.
6. **Checkpoint:** `data/pipeline_state/migration_checkpoint.json`, written every 16-chunk batch.
7. **Speed: 3-6 chunks/sec** on M5 Pro. Don't try to improve with batching.
8. **RAM guard:** Skip if system RAM > 40GB of 64GB (via `vm_stat`).
9. **Use `skip_llm=True`** when only embedding — avoids loading Mistral-24B (~12GB).

## Chunking (pipeline/ingestion.py — SmartIngestor)

**Current 3-tier approach:**
- Atomic (<1,000 chars): single chunk, no split
- Linear (1K-16K chars): 1 child → semantic grandchildren
- Hierarchical (>16K chars): multi-child split → semantic grandchildren per child

**Planned improvements** (from `docs/research/RAG_Beyond_RAG_Research_Report.md`):
- **Increase chunk size** from ~1,000 to 1,500-2,000 chars. Benchmarks: 512-token chunks with overlap outperform shorter semantic chunks (69% vs 54%).
- **Dutch section-aware splitting** instead of Gemini for ~80% of docs:
  ```python
  SECTION_PATTERNS = [
      r'^\s*\d+\.\s+\S',        # "1. Onderwerp"
      r'^\s*[A-Z][A-Z\s]{3,}$', # "FINANCIËLE CONSEQUENTIES"
      r'^\s*Artikel\s+\d+',     # "Artikel 3"
  ]
  ```
- **Never split:** motion dictum from overwegingen, amendment from its target text, table rows mid-table.
- **Contextual Retrieval (future):** Prepend context summary per chunk before embedding — reduces retrieval errors by 67% (Anthropic research).
- **Metadata enrichment (future):** Extract per chunk: `fractie`, document type, policy domain, financial amounts, legislative stage.

## Auto-Ingest Pipeline (NOT YET IMPLEMENTED)

**The gap:** `RefreshService` downloads docs → PostgreSQL, but `SmartIngestor` (chunking) and embedding are run manually. No automatic pipeline connects them.

**Planned flow:**
```
Every 4h: RefreshService polls ORI API + iBabs → new docs → embedding_queue table
Nightly 11 PM: IngestWorker → lock file → resource check → OCR → chunk → embed → Qdrant upsert
```

**Implementation order:**
1. Create `embedding_queue` table (schema only)
2. Add StorageService queue methods (enqueue, get_pending, mark_done/failed)
3. Add enqueue call in RefreshService after `insert_document()`
4. Build `services/ingest_worker.py` (referencing runbook embedding rules)
5. Add scheduler jobs to `main.py`
6. Backfill queue for existing un-embedded docs
7. **Wait for current migration to finish** before enabling nightly runs
8. Improve chunking (size increase + Dutch section detection)
9. Metadata enrichment batch pass
10. Contextual Retrieval batch pass (prepend context, re-embed)

## Future: Qdrant-Native Sparse Vectors

Currently BM25 runs in PostgreSQL (tsvector). Qdrant supports native sparse vectors (BM42, SPLADE) which enable single-system hybrid search:

```python
# Future: named vectors for true hybrid in Qdrant
client.create_collection(
    collection_name="notulen_chunks_v2",
    vectors_config={
        "dense": models.VectorParams(size=4096, distance=models.Distance.COSINE),
    },
    sparse_vectors_config={
        "bm42": models.SparseVectorParams(modifier=models.Modifier.IDF),
    },
)

# Single query combining both:
client.query_points(
    collection_name="notulen_chunks_v2",
    prefetch=[
        models.Prefetch(query=dense_embedding, using="dense", limit=100),
        models.Prefetch(query=sparse_vector, using="bm42", limit=100),
    ],
    query=models.FusionQuery(fusion=models.Fusion.RRF),  # Built-in RRF
    limit=top_k,
)
```

**Trade-off:** This eliminates PostgreSQL from the retrieval hot path and simplifies the architecture, but requires re-embedding all 607K points with sparse vectors. Only pursue after the current pipeline is stable and evaluated.

**Advantage of current PostgreSQL BM25:** Uses `dutch` text search configuration with proper stemming and stop words. Qdrant's BM42 would need a Dutch tokenizer configured separately.

## Data Sources

- **Primary:** ORI API at `api.openraadsinformatie.nl/v1/elastic` (Elasticsearch queries)
- **Fallback:** iBabs scraping at `rotterdamraad.bestuurlijkeinformatie.nl` (HTML parsing via BeautifulSoup, for 2026+ meetings when ORI returns empty)
- Both handled by `RefreshService` + `OpenRaadService` + `iBabsService`

## Safety Rules

1. **A background `migrate_embeddings.py` may be running.** Never write to Qdrant concurrently. Read-only queries (query_points, scroll) are safe. Ask the user before any write operations.
2. **Never run `optimize_qdrant.py`** (triggers compaction) while migration is in progress.
3. **Never delete or recreate** Qdrant collections or PostgreSQL tables while a background job runs.
4. **Never call `manual_memory_reset()`** — forces a 30-minute model reload. See runbook.
5. **Two Qdrant collections exist:** `notulen_chunks` (live) and `notulen_chunks_local` (from SmartIngestor). MCP + web app use `notulen_chunks`.

## Frontend Integration

The web frontend (`main.py`) uses Gemini gemini-2.5-flash-lite for a 3-stage synthesis pipeline:
1. Information Extraction (Gemini call)
2. Debate Mapping (Gemini call) — **stages 1+2 run in parallel via asyncio.gather**
3. Professional Dossier Synthesis (Gemini call, **streamed via SSE**)

The `/api/analyse/unified/` endpoint returns `StreamingResponse` (text/event-stream). The meeting.html template uses `EventSource` for progressive rendering. `date_from`/`date_to` and `fast_mode=True` are threaded through all RAG calls in AIService.
