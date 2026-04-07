# NeoDemos v3 RAG Architecture — Implementation Plan

**Date:** 2026-04-06
**Status:** Approved plan, pending implementation
**Baseline:** v2-full-baseline (frozen), v4-topk15-prompts (latest v2 run)

---

## Problem Statement

The v2 RAG pipeline uses a single-pass hybrid search (Qdrant vector + PostgreSQL BM25 + RRF fusion + Jina Reranker v3). This works well for factoid and temporal queries but structurally fails on three categories:

| Category | v2 Best | Root Cause |
|----------|---------|------------|
| party_stance | Relevance 2.5/5 | No per-party filtered retrieval — returns generic topic chunks |
| multi_hop | Relevance 2.5/5, Faithfulness 2.5/5 | Single retrieval pass can't connect votes + alternatives |
| broad_aggregation | Completeness 4.0/5 (prompt-fixed) | Still single-pass; architectural limit at scale |

Category-aware Gemini prompts fixed broad_aggregation completeness (1.0 → 4.0) but cannot help party_stance or multi_hop — these need architectural changes.

---

## Data Reality

Verified findings that shape the implementation:

- **`party_statements` table is EMPTY** (0 rows)
- **`kg_entities` has 0 entries with party affiliation (`fractie`)**
- **31,909 chunks** contain regex-extractable party patterns like `De heer NAME (PARTY)`
- **`meetings.committee`** uses numeric IDs but **`meetings.name`** has full committee names (22 mappings)
- **Qdrant**: 1,630,523 points, collection `notulen_chunks`
- **Current payload keys**: `document_id`, `title`, `content`, `chunk_type`, `child_id`, `start_date`
- **Missing from payloads**: `party`, `speaker`, `committee`, `doc_type`, `meeting_id`

---

## Architecture Overview

```
                    ┌──────────────┐
                    │ Query Router │  ← Tier 1: rules, Tier 2: Haiku
                    └──────┬───────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
   ┌──────▼──────┐  ┌─────▼──────┐  ┌──────▼──────┐
   │  Standard   │  │   Party    │  │  Map-Reduce  │
   │  Retrieval  │  │  Filtered  │  │  Synthesis   │
   │ (factoid,   │  │ (party_    │  │ (broad_agg)  │
   │  temporal,  │  │  stance)   │  │              │
   │  absence)   │  │            │  │              │
   └──────┬──────┘  └─────┬──────┘  └──────┬──────┘
          │                │                │
          │         ┌──────▼──────┐         │
          │         │  Sub-Query  │         │
          │         │ Decompose   │         │
          │         │ (multi_hop) │         │
          │         └──────┬──────┘         │
          │                │                │
          └────────────────┼────────────────┘
                           │
                    ┌──────▼───────┐
                    │  Generation  │
                    │ Gemini/Claude│
                    └──────────────┘
```

---

## Phase 1: Qdrant Metadata Enrichment

**File:** `scripts/enrich_qdrant_metadata.py`
**Run:** Overnight (~2-4 hours for 1.63M points)

Scrolls all Qdrant points in batches of 500 and adds:
- `party`: Primary party extracted from chunk text via regex
- `parties`: All parties mentioned in chunk (list)
- `speaker`: First speaker name found
- `committee`: Resolved from meeting committee ID → name
- `meeting_id`: From documents table join
- `doc_type`: Classified from document name

Uses checkpoint/resume pattern from `sync_qdrant_dates.py` with RAM guard.

**Shared dependency:** `services/party_utils.py` — party alias map + extraction functions.

---

## Phase 2: Core Services

### Query Router (`services/query_router.py`)

Two-tier classification:
1. **Rule-based** (<1ms): metadata hints, keyword matching
2. **LLM** (Haiku): when rules are ambiguous

Returns `QueryRoute` dataclass with: query_type, dynamic top_k, parties list, strategy name.

| Query Type | top_k | Strategy |
|-----------|-------|----------|
| factoid | 10 | standard |
| temporal | 15 | standard |
| party_stance | 25/party | party_filtered |
| broad_aggregation | 50 | map_reduce |
| multi_hop | 15/sub-query | sub_query |
| absence | 10 | standard |

### Map-Reduce Synthesizer (`services/synthesis.py`)

For broad_aggregation:
1. **MAP**: Split 50+ chunks into groups of 4-5, parallel Gemini calls for mini-summaries
2. **REDUCE**: Claude Sonnet synthesizes all mini-summaries into structured answer

### Sub-Query Decomposer (`services/decomposition.py`)

For multi_hop:
1. **DECOMPOSE**: Haiku splits question into 2-4 sub-queries
2. **RETRIEVE**: Parallel retrieval per sub-query
3. **MERGE**: Deduplicate + rerank merged pool
4. **SYNTHESIZE**: Sonnet produces answer with cross-source linking

---

## Phase 3: Service Modifications

- **`services/rag_service.py`**: Add `_retrieve_by_vector_similarity_with_filter()` accepting Qdrant `Filter` parameter for party-filtered retrieval. Fallback to keyword boost if filter returns < 5 results.
- **`eval/instrumentation/tracer.py`**: Add `start_date` and `party` to `StageResult.metadata`.

---

## Phase 4: v3 Eval Framework

New `eval_v3/` package that:
- Extends v2 `EvalConfig` with v3 toggles (router, map-reduce, decomposition)
- Wraps routing logic in `V3InstrumentedRAGService`
- Reuses all v2 judges, metrics, and reporters
- Saves runs to `eval_v3/runs/`
- Compares against v2 runs via `--compare-with`

---

## Phase 5: First v3 Eval Run

```bash
python -m eval_v3.run_eval --run-id "v3-arch-v1" \
  --compare-with "v4-topk15-prompts" --hallucination
```

**Success criteria:**
- party_stance relevance: 2.5 → 4.0+
- multi_hop relevance: 2.5 → 3.5+
- broad_aggregation completeness: maintain 4.0+
- Overall faithfulness: >= 4.5
- No new UNSAFE hallucination flags

---

## File Inventory

| File | Action | Phase |
|------|--------|-------|
| `scripts/enrich_qdrant_metadata.py` | NEW | 1 |
| `services/party_utils.py` | NEW | 2 |
| `services/query_router.py` | NEW | 2 |
| `services/synthesis.py` | NEW | 2 |
| `services/decomposition.py` | NEW | 2 |
| `services/rag_service.py` | MODIFY | 3 |
| `eval/instrumentation/tracer.py` | MODIFY | 3 |
| `eval_v3/__init__.py` | NEW | 4 |
| `eval_v3/config.py` | NEW | 4 |
| `eval_v3/run_eval.py` | NEW | 4 |
| `eval_v3/instrumentation/__init__.py` | NEW | 4 |
| `eval_v3/instrumentation/rag_wrapper_v3.py` | NEW | 4 |

---

## Benchmark Lock

All evaluations use the same 20 questions from `eval/data/questions.json`.
MD5: `6ab59fcbe8ec78f0a6ca613a61d4b273` (verified in `eval_v3/QUESTIONS_LOCK`).
v2 results in `eval/runs/` are frozen and must never be modified.
