# NeoDemos v3 — Completion Report

**Date:** 2026-04-07  
**Scope:** v3 RAG architecture, MCP v3 server, Dutch compound word fix

---

## 1. What Was Built

### 1.1 v3 RAG Pipeline

A complete rewrite of the retrieval and routing layer.

**Query Router (`services/query_router.py`)**
- Replaced single-signal early-return with full signal collection before routing
- Detects: party, temporal, multi_hop, financial, aggregation, balanced_view signals simultaneously
- Compound routing: queries with party + temporal + multi_hop → `sub_query` strategy (was broken before)
- Dutch temporal extraction: parses "afgelopen vier jaar", "sinds 2022", "de laatste twee jaar" into `date_from`/`date_to` using Dutch number words
- Strategies: `sub_query`, `map_reduce`, `party_filtered`, `standard`

**New Services**
| Service | Purpose |
|---|---|
| `services/decomposition.py` | Sub-query decomposition via Haiku |
| `services/synthesis.py` | Map-reduce synthesis (Gemini map + Claude reduce) |
| `services/reranker.py` | Jina paid tier reranking (500 RPM, 2M TPM) |
| `services/crag_filter.py` | CRAG relevance filter for noisy result sets |
| `services/query_router.py` | Rules-based compound routing + temporal extraction |
| `services/dutch_decompound.py` | Dictionary-based Dutch compound word decompounder |
| `services/embedding.py` | Nebius embedding (API-based, zero local GPU RAM) |
| `services/financial_calc.py` | Financial table computation |
| `services/party_utils.py` | Party name normalization |

---

### 1.2 Dutch Compound Word Fix (BM25)

**Root cause:** PostgreSQL's Dutch Snowball stemmer is suffix-only — it strips endings but never decomposes compound words. `leegstandsbelasting` → `leegstandsbelast`, while `leegstand` → `leegstand`. No match.

**Solution:** Dictionary-based decompounding
- Lexicon: OpenTaal (411K real Dutch words) + domain terms from document titles
- Algorithm: greedy longest-left-match with Dutch interfixes (`s`, `e`, `en`, `er`, `n`)
- Parameters: MIN_LEFT=4, MIN_RIGHT=5, MIN_COMPOUND_LEN=9
- Test suite: 17/17 correct splits, 0 false positives

**Validated splits:**
```
leegstandsbelasting  → leegstand + belasting
warmtebedrijf        → warmte + bedrijf
woningbouwprogramma  → woningbouw + programma
gemeenteraad         → gemeente + raad
schuldhulpverlening  → schuldhulp + verlening
gebiedsontwikkeling  → gebied + ontwikkeling
jeugdgezondheidszorg → jeugd + gezondheidszorg
```

**Database changes:**
- `document_chunks.decomposed_terms TEXT` — stores decomposed parts per chunk
- `text_search` generated column updated: now includes `decomposed_terms` in its tsvector expression
- GIN index rebuilt on updated `text_search`

**Result:**
```
Before: BM25 "leegstand" → 0 matches on "leegstandsbelasting" chunks
After:  BM25 "leegstand" → 8,292 matches (18/18 "leegstandsbelasting" chunks now reachable)
```

**Population:**
- Script: `scripts/populate_decomposed_terms.py`
- Processed: 1,629,768 chunks in ~3 minutes at ~8,000/s
- 1,538,229 chunks with non-empty decomposed terms (94.4%)
- Self-maintaining: future chunks get decomposed at ingestion, `text_search` regenerates automatically

---

### 1.3 MCP v3 Server (`mcp_server_v3.py`)

Runs alongside existing `neodemos` in Claude Desktop — registered as `neodemos_v3`. Claude Desktop handles all reasoning/synthesis; MCP is retrieval-only.

**11 tools:**

| Tool | Purpose |
|---|---|
| `zoek_raadshistorie` | Hybrid semantic + BM25 search on council docs |
| `zoek_uitspraken` | Party/member statements with Qdrant party filter |
| `zoek_financieel` | Financial docs with table chunk boost |
| `zoek_beleid` | Policy/vision documents |
| `zoek_context` | Background/context retrieval |
| `zoek_vergadering` | Meeting-specific search |
| `zoek_trends` | Temporal trend analysis |
| `zoek_moties` | Direct SQL on 7,982 motions/amendments by topic+outcome |
| `scan_breed` | Broad scan: 40–80 short snippets for orientation |
| `lees_fragment` | Full chunk content by document_id (deep read) |
| `geef_samenvatting` | Document summary retrieval |

**Key improvements over v1:**
- Jina reranking on every retrieval call (was skipped in v1 `fast_mode=True`)
- Qdrant party payload filter (was keyword append in v1)
- Dynamic top_k from router (was hardcoded 8–24)
- Auto date extraction from query text
- `zoek_moties` bypasses BM25 entirely via SQL LIKE — finds all motion variants
- `scan_breed` + `lees_fragment` enables two-phase deep research (scan many → read few)
- No LLM calls in MCP — fully API-minimal, Claude's subscription handles reasoning

---

### 1.4 Eval Benchmark

- 21 questions (was 20)
- Added `cp-01`: VVD leegstand compound query (party + temporal + multi_hop, difficulty: very_hard)
- QUESTIONS_LOCK updated (MD5: f91aa24cb38a34fed35eeb26674d188f)
- v3 vs v2 comparisons filter to original 20 question IDs

**Category distribution:**
| Category | Count |
|---|---|
| temporal | 8 |
| party_stance | 2 |
| acronym_abbreviation | 2 |
| multi_hop | 2 |
| absence | 2 |
| broad_aggregation | 1 |
| balanced_view | 1 |
| specific_event | 1 |
| informal_opinion | 1 |
| compound (v3-only) | 1 |

**v2 baseline (frozen):** precision 0.99 · faithfulness 4.8 · completeness 2.75

---

## 2. Database State

| Metric | Value |
|---|---|
| Total documents | 88,590 |
| Total chunks | 1,629,768 |
| Chunks with decomposed_terms | 1,538,229 (94.4%) |
| Table chunks (financial) | 81,211 |
| Decision chunks | 107,068 |
| BM25 hits for "leegstand" | 8,292 |

**Chunk type breakdown:** text (908K), quote (314K), header (112K), decision (107K), list (101K), table (81K)

---

## 3. Known Shortfalls Fixed

| Issue | Root Cause | Fix |
|---|---|---|
| VVD leegstand query missed date filter | Router returned early on party match | Collect all signals before routing |
| "leegstand" missed "leegstandsbelasting" | Snowball stemmer is suffix-only | Dictionary decompounding + decomposed_terms |
| Balanced view returned vague answer | `balanced_view` used `standard` strategy | Now routes to `sub_query` |
| MCP returned fewer motions than expected | BM25 missed compound variants | `zoek_moties` uses SQL LIKE directly |
| Temporal phrases not parsed | No Dutch number word support | `_extract_dates_from_text` with Dutch number dict |

---

## 4. What Remains (Future Sessions)

**Plan G — Metadata Enrichment**  
Add `section_topic`, `key_entities`, `answerable_questions` per chunk via Haiku batch.  
Spec: `docs/architecture/PLAN_G_CONTEXTUAL_RETRIEVAL.md`  
~444K chunks to enrich · ~$1 Haiku API cost

**Plan I — LightRAG Entity Extraction**  
Extract entities + relationships → `kg_relationships` table for graph traversal on multi_hop queries.  
Spec: `docs/architecture/PLAN_I_LIGHTRAG_ENTITY_EXTRACTION.md`  
12 entity types · ~1M relationships estimated

**text_search_simple column** — still present in schema (1,050,000 rows populated), can be dropped when Plans G/I are underway to reclaim space.
