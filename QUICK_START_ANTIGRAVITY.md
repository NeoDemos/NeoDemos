# Quick Start Guide for Antigravity Agent

**Current Status**: ✅ System FULLY FUNCTIONAL  
**Next Task**: Execute semantic chunking & integrate RAG  
**Time to Complete**: 3-4 hours (mostly waiting for chunking)  

---

## What You're Inheriting

A **fully working NeoDemos analysis system** with:
- ✅ Unified analysis endpoint: `/api/analyse/agenda/{id}?party={name}`
- ✅ Full documents analyzed (no truncation)
- ✅ Realistic party alignment scores (40-65%, not inflated 90%)
- ✅ RAG infrastructure ready
- ⏳ Semantic chunking NOT YET EXECUTED (your main task)

---

## Your Main Task (Priority Order)

### 1. Execute Semantic Chunking [Est: 2-4 hours]

```bash
cd /Users/dennistak/Documents/Final\ Frontier/NeoDemos

# Test first (fast, ~5 min, 2 documents):
python3 scripts/compute_embeddings.py

# Monitor output - should see chunking progress for 2 docs

# If test passes, run full production (all 129 docs):
# Edit line 415 in scripts/compute_embeddings.py:
#   Change: service.process_notulen_documents(limit=2)
#   To:     service.process_notulen_documents()

# Run in background:
nohup python3 scripts/compute_embeddings.py > chunking.log 2>&1 &

# Monitor completion:
tail -f chunking.log
# Wait for: "✅ Semantic chunking and embedding computation complete!"
```

**What happens**: 
- Processes 129 notulen documents
- Chunks each semantically (avg 10-12 chunks per doc = ~1500 total chunks)
- Generates 3072-dim embeddings for each chunk
- Stores in Qdrant local collection + PostgreSQL metadata

**Cost**: ~$0.95 (Gemini + embeddings)

---

### 2. Integrate RAG into Analysis [Est: 30 min, after chunking done]

Add historical context retrieval to analysis prompt:

**File to Modify**: `services/ai_service.py`

**Location**: In `analyze_agenda_item()` method (around line 68-70)

**What to add**:
```python
# After loading documents, before calling Gemini:
from services.rag_service import RAGService

rag = RAGService()
relevant_chunks = rag.retrieve_relevant_context(
    query_text=f"{item_name} {' '.join([d['content'][:500] for d in documents])}",
    top_k=10
)

# Format chunks for prompt
if relevant_chunks:
    historical_context = rag.format_retrieved_context(relevant_chunks)
    # Add to the prompt sent to Gemini
else:
    historical_context = ""
```

**Expected result**: Analysis includes past decisions as context for current judgment

---

## How to Know Things Are Working

### Check 1: App is Running
```bash
curl http://localhost:8000/
# Should return HTML homepage
```

### Check 2: Unified Endpoint Works
```bash
curl "http://localhost:8000/api/analyse/agenda/6123921?party=GroenLinks-PvdA" | python3 -m json.tool
```

Expected response keys:
- `alignment_score`: 0.65 (realistic)
- `agenda_item_id`: "6123921"
- `source`: "unified_party_lens_analysis"

### Check 3: Chunking Completed
```bash
python3 -c "
from qdrant_client import QdrantClient
client = QdrantClient(path='./data/qdrant_storage')
collection = client.get_collection('notulen_chunks')
print(f'Chunks in Qdrant: {collection.points_count}')
"

# Should print: Chunks in Qdrant: ~1500 (after full run)
```

---

## Key Files You'll Touch

| File | Purpose | Line(s) |
|------|---------|---------|
| `scripts/compute_embeddings.py` | **RUN THIS** to execute chunking | Line 415 (limit param) |
| `services/ai_service.py` | Add RAG call to prompt builder | ~Line 68-70 |
| `services/rag_service.py` | Already done - just use it | No changes needed |
| `main.py` | Unified endpoint - don't touch | Lines 147-264 |

---

## If Things Go Wrong

### Chunking Hangs
- Check logs: `tail -f chunking.log`
- If stuck, kill process: `pkill -9 -f compute_embeddings`
- Restart: `python3 scripts/compute_embeddings.py`
- Script has pre-chunking for large docs (>800KB), should handle it

### Qdrant Lock Error
```
RuntimeError: Storage folder ./data/qdrant_storage is already accessed
```
**Fix**: Kill any running Python processes:
```bash
pkill -9 -f python
rm -rf ./data/qdrant_storage  # if needed
python3 scripts/compute_embeddings.py  # restart
```

### Low Memory During Chunking
- Script processes docs sequentially, shouldn't be memory-heavy
- If issues: reduce `limit` in chunking to process fewer docs at once

### No vectors after chunking
- Check: `ls -la ./data/qdrant_storage/`
- Verify: `chunking.log` for errors
- Check PostgreSQL: `SELECT COUNT(*) FROM document_chunks;`

---

## Context You Need

**Database**: PostgreSQL on localhost  
- User: postgres
- Password: postgres  
- DB: neodemos

**LLM**: Google Gemini 3 Flash Preview (1M token context)  
- API key in `.env`
- Model costs: $0.075/1M input, $0.3/1M output

**Vector DB**: Qdrant local mode
- Path: `./data/qdrant_storage/`
- Collection: `notulen_chunks`
- Dimensions: 3072 (from gemini-embedding-001)

**Test Meeting**: 6123915 (Commissie Bouwen, Wonen en Buitenruimte)  
- Agenda item: 6123921 (Housing development - Kop van Homerus)
- Should show GL-PvdA alignment: ~65% (realistic concerns about social housing loss)

---

## Success Checkpoints

1. ✅ **Chunking Test Passes** (2 docs)
   - No errors in output
   - See chunks being generated
   
2. ✅ **Full Chunking Completes** (all 129 docs)
   - Qdrant has ~1500 vectors
   - PostgreSQL has chunk metadata
   - Takes 2-4 hours
   
3. ✅ **RAG Integration Compiles**
   - No import errors
   - Retrieval calls don't crash
   
4. ✅ **End-to-End Test Works**
   - Analysis includes historical context
   - Scores are realistic
   - No truncation

---

## Handoff Documents Location

See these files for detailed context:

- **Full Details**: `AGENT_HANDOFF.md` (in this repo)
- **Implementation Notes**: `RAG_IMPLEMENTATION_PROGRESS.md` (if exists)
- **This Quick Ref**: `QUICK_START_ANTIGRAVITY.md`

---

**Ready to proceed?** Start with chunking execution above. Good luck!
