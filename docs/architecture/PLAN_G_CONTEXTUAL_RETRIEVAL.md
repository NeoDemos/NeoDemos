# Plan G: Metadata Enrichment (Revised — No Vector Dilution)

**Status:** Not started
**Estimated cost:** ~$1-2 in API calls
**Estimated time:** 1 day coding + 3-5 hours batch processing
**Dependencies:** None (can run independently)
**Risk level:** Low
**Revision date:** 2026-04-07 (updated 2026-04-07 for pipeline upgrades)

---

## 0. Why this plan was revised

The original Plan G proposed Anthropic-style contextual retrieval: prepending LLM-generated
context sentences to chunks before embedding. After research and analysis, this approach was
rejected in favour of metadata enrichment. The reasons:

### Evidence against embedding context into vectors

1. **Snowflake/FinanceBench study** (arXiv 2510.24402): LLM-generated chunk summaries
   *lost* 5.8 points on Llama 3. Structured metadata injection (entities, answerable questions)
   gained 20+ points (F1 from 32.9 → 44.1).

2. **Vector dilution is real**: A chunk about "€2,3 miljoen Jeugdtheater subsidie" scores
   lower on exact queries when its vector also encodes "commissie ZWCS" and "cultuurbeleid".
   The vector must serve two masters — content matching AND context matching — and does
   both adequately instead of one well.

3. **Chunk expansion failure**: The same FinanceBench study found that entity/cluster
   metadata expansion dropped F1 from 37.3 → 33.2. More context in the vector ≠ better retrieval.

4. **NAACL 2025**: Fixed 200-word chunks matched or beat semantic chunking across
   retrieval and answer generation tasks. Fancy embedding-time enrichment is not the lever.

### Why our hierarchy already solves the context problem

Our `expand_to_hierarchical_context()` reconstructs context at retrieval time:

```
Grandchild hit (1K chunk)
  → walk up to Parent via child_id (8K section from document_children)
  → walk up to Document via document_id (40K full document + headers)
```

This is **strictly better** than baking context into vectors because:
- Vectors stay pure (focused on semantic content of the chunk)
- Context is always fresh (no stale embedded context if documents update)
- Context granularity adapts to query type:
  - >35% hits from one doc → expand to full document (40K)
  - Multiple hits from one parent OR debate stream → expand to section (8K)
  - Isolated hit → return fragment with document header attribution

### What the original plan got right

- Phase A (context as metadata for BM25) was sound — zero dilution risk
- Phase B (sample validation before committing) was good engineering discipline
- The Warmtebedrijf problem statement remains valid and is addressed below

---

## 0b. Interaction with recent pipeline upgrades

Four upgrades since this plan was drafted affect the implementation details below.

### 1. Party field already populated — exclude from `key_entities`

`services/party_utils.py` already extracts and normalises party mentions from chunk text
into a dedicated `party`/`parties` Qdrant payload field (using two speaker regex patterns:
"De heer/Mevrouw NAME (PARTY)" and "[Speaker (PARTY)]:"). These fields are already searchable
and filterable at query time.

**Implication for Phase A:** The Haiku prompt for `key_entities` must explicitly scope to
*non-party* entities only:
- Project names (Warmtebedrijf, RijnmondRail, M4H)
- Organisations (gemeente Rotterdam, GGD, HbR)
- Budget items and amounts (€2,3 miljoen, krediet, subsidie)
- Locations (Katendrecht, Feyenoord City, Coolsingel)
- Named people without party affiliation (wethouder Kurvers by name)

Do **not** re-extract parties — they are already covered. Duplication would create
inconsistencies if party aliases differ between the two extraction paths.

### 2. tsvector for proper nouns must use `simple`, not `dutch`

PostgreSQL's `dutch` text search dictionary applies aggressive stemming (Snowball algorithm).
This is correct for content words ("woningbouw" → "woning", "aangenomen" → "neem") but
corrupts proper nouns and project names:
- "Warmtebedrijf" → stemmed unpredictably
- "GroenLinks-PvdA" → split on hyphen, both parts stemmed
- "M4H" → unchanged (OK, but fragile)

**Implication for Phase A SQL:** Split the tsvector into two components — `dutch` for content,
`simple` (no stemming) for entities and topic — and concatenate:

```sql
ALTER TABLE document_chunks ADD COLUMN text_search_enriched tsvector;

UPDATE document_chunks SET text_search_enriched =
    to_tsvector('dutch',
        COALESCE(content, '')                           -- stemmed for recall on verbs/nouns
    ) ||
    to_tsvector('simple',
        COALESCE(section_topic, '') || ' ' ||
        COALESCE(array_to_string(key_entities, ' '), '') || ' ' ||
        COALESCE(array_to_string(answerable_questions, ' '), '')  -- unstemmed for exact entity match
    );

CREATE INDEX idx_chunks_text_search_enriched
    ON document_chunks USING GIN(text_search_enriched);
```

At query time, BM25 must also split accordingly:
```sql
WHERE dc.text_search_enriched @@
    (websearch_to_tsquery('dutch', %s) || websearch_to_tsquery('simple', %s))
```
where both parameters receive the same query string.

### 3. Temporal stripping interacts with `answerable_questions`

`services/ai_service.py::extract_temporal_filters()` and `query_router.py::_extract_dates_from_text()`
now strip Dutch temporal phrases ("afgelopen jaar", "in 2022", "van 2020 tot 2023") from
the query before BM25 and vector search. The cleaned query is what hits the index.

**Implication for Phase A:** Avoid generating date-specific `answerable_questions`. A question
like "Welk besluit is genomen op 12 december 2016?" will not match a user query for
"Warmtebedrijf in 2016" because "2016" is stripped before search. Keep questions
topic-focused, not date-specific:

- Good: "Is het overbruggingskrediet voor het Warmtebedrijf goedgekeurd?"
- Avoid: "Welk besluit nam de raad op 12 december 2016 over het Warmtebedrijf?"

The Haiku prompt should instruct this explicitly.

### 4. Multilingual reranker already handles Dutch

The CrossEncoder was upgraded from `ms-marco-MiniLM-L-6-v2` (English-only) to
`ms-marco-multilingual-MiniLM-L-12-v2` (50 languages, Dutch included). This means the
reranking step already scores Dutch query–chunk pairs correctly without translating or
normalising.

**Implication for Plan G:** No additional reranker work needed. The enriched entity/topic
metadata in Phase A will be reranked by a model that natively understands Dutch.

---

## 1. Problem Statement (unchanged)

Chunks lose document-level context when embedded independently. A chunk that says:

> "Het voorstel wordt aangenomen met 28 stemmen voor en 17 tegen."

...has no mention of "Warmtebedrijf" in its text. That word is in the parent document title.
Vector search for "Warmtebedrijf stemming" will miss this chunk entirely.

This is the root cause of:
- **bv-01 regression:** chunks about general Rotterdam policy matched "M4H gebiedsontwikkeling"
  because they shared terms like "ontwikkeling" and "duurzaam" without topic-specific context
- **Multi-hop failures:** vote records, motie authorship, and budget line items exist in chunks
  that don't mention the broader topic they belong to
- **Party attribution gaps:** a motie chunk discussing details of a proposal doesn't mention the
  submitting party — that info is in the document header

### Root cause analysis

The issue is a **keyword gap**, not a **semantic gap**. The vectors correctly encode the
meaning of the chunk text. The problem is that BM25 and entity-based filtering can't find
these chunks because the relevant terms (topic name, party, date) aren't in the chunk text
or searchable metadata.

**Solution:** Add structured metadata (entities, topic, answerable questions) as searchable
fields — not embedded into vectors.

---

## 2. Approach: Metadata enrichment, vectors untouched

### Phase A: Targeted metadata enrichment (main phase)

Generate three high-value metadata fields per chunk using Haiku. These feed BM25 and
Qdrant payload filtering. Vectors are never touched.

### Phase B: Entity-based Qdrant pre-filtering

Use extracted entities to narrow vector search scope before similarity matching.

### Phase C: Coreference resolution (optional, higher effort)

Replace vague references ("de wethouder") with resolved entities ("wethouder Kurvers (VVD)")
in chunk text. Only this phase touches embeddings — and only for resolved chunks.

---

## 3. Data inventory

| Item | Count | Notes |
|------|-------|-------|
| Total chunks in PostgreSQL | 1,629,768 | All need metadata generation |
| Total points in Qdrant | 1,630,523 | ~800 orphans (slight mismatch, not blocking) |
| Parent documents | 88,590 | Source of document-level context |
| Meetings | ~15,000 | Source of date and committee context |
| Current Qdrant payload fields | document_id, title, content, chunk_type, child_id, start_date, party, parties, speaker, committee, meeting_id, doc_type | From v3 enrichment |

---

## 4. Phase A: Targeted Metadata Enrichment

### 4a. Schema change

```sql
ALTER TABLE document_chunks ADD COLUMN section_topic TEXT;
ALTER TABLE document_chunks ADD COLUMN key_entities TEXT[];
ALTER TABLE document_chunks ADD COLUMN answerable_questions TEXT[];
```

### 4b. Metadata generation script

**File:** `scripts/enrich_chunk_metadata.py`

**Input per chunk:**
```
Document title: {documents.name}
Meeting date: {meetings.start_date}
Committee: {meetings.name}
Doc type: {doc_type from enrichment}
Party: {party from enrichment, if available}
Chunk title: {document_chunks.title}
Chunk text (first 500 chars): {document_chunks.content[:500]}
```

**LLM call (Haiku 4.5):**
```
Prompt: "Analyseer dit fragment uit een gemeentelijk document en geef gestructureerde metadata.

Document: {doc_name}
Datum: {meeting_date}
Commissie: {committee}
Type: {doc_type}
Fragment: {chunk_text[:500]}

Geef in JSON:
1. section_topic: Een korte beschrijving van het onderwerp (max 10 woorden)
2. key_entities: Lijst van projecten, organisaties, bedragen, locaties en persoonsnamen
   die relevant zijn voor dit fragment. Neem ook entiteiten op die NIET in het fragment
   staan maar wel uit de documenttitel of metadata blijken (zoals de projectnaam).
   GEEN partijnamen — die worden apart bijgehouden.
3. answerable_questions: 3-5 vragen die dit fragment kan beantwoorden. Gebruik
   inhoudelijke zoektermen, GEEN specifieke datums in de vraag.

JSON:"
```

**Haiku returns** (example):
```json
{
  "section_topic": "stemming overbruggingskrediet Warmtebedrijf Rotterdam",
  "key_entities": ["Warmtebedrijf", "Rotterdam", "overbruggingskrediet",
                    "28 stemmen voor", "17 stemmen tegen"],
  "answerable_questions": [
    "Is het overbruggingskrediet voor het Warmtebedrijf goedgekeurd?",
    "Hoeveel raadsleden stemden voor het Warmtebedrijf-voorstel?",
    "Wat was de stemverhouding bij het Warmtebedrijf-besluit?"
  ]
}
```

Note: `key_entities` deliberately includes "Warmtebedrijf" even though it's not in the
chunk text — it comes from the document title in the prompt. This is the key fix for
the keyword gap problem. Parties are excluded because `party_utils.py` already handles
party extraction into a dedicated Qdrant payload field.

**Architecture:**
- Follows `enrich_qdrant_metadata.py` pattern: batch scroll, checkpoint-resumable, RAM guard
- Named PostgreSQL cursor with itersize=1000
- Batch Haiku calls: 10 concurrent (asyncio.Semaphore)
- Rate limit: Haiku allows 4000 RPM — 10 concurrent × 0.5s/call = ~1200 RPM (within limits)
- Checkpoint: save offset + count to JSON after each batch of 500
- Write metadata to PostgreSQL via batch UPDATE
- Also set_payload to Qdrant: `section_topic`, `key_entities`, `answerable_questions`

**Cost calculation:**
```
1,629,768 chunks
× ~600 tokens input per call (doc metadata + chunk preview + prompt)
× ~80 tokens output per call (JSON with entities + questions)

Haiku pricing: $0.80/1M input, $4.00/1M output
Input cost:  978M × $0.80/1M = ~$0.78
Output cost: 130M × $4.00/1M = ~$0.52
Total: ~$1.30

With retries and overhead: ~$1.50-2.00
```

**Time estimate:**
```
1,629,768 calls ÷ (10 concurrent × 120/min) = ~136 minutes = ~2.3 hours
Realistic with rate limiting + retries: 3-5 hours
```

### 4c. BM25 integration

Create a combined tsvector using **two dictionaries**: `dutch` (Snowball stemming) for
content words, `simple` (no stemming) for proper nouns and entities. See section 0b.2
for why this split is necessary.

```sql
ALTER TABLE document_chunks ADD COLUMN text_search_enriched tsvector;

UPDATE document_chunks SET text_search_enriched =
    to_tsvector('dutch',
        COALESCE(content, '')                            -- stemmed: recall on verbs/nouns
    ) ||
    to_tsvector('simple',
        COALESCE(section_topic, '') || ' ' ||
        COALESCE(array_to_string(key_entities, ' '), '') || ' ' ||
        COALESCE(array_to_string(answerable_questions, ' '), '')  -- unstemmed: exact entity match
    );

CREATE INDEX idx_chunks_text_search_enriched
    ON document_chunks USING GIN(text_search_enriched);
```

Modify `services/rag_service.py` method `_retrieve_chunks_by_keywords()`:

```python
# Use both dictionaries at query time to match the dual-tsvector index
WHERE dc.text_search_enriched @@
    (websearch_to_tsquery('dutch', %s) || websearch_to_tsquery('simple', %s))
-- Both %s params receive the same query string
```

This directly fixes the Warmtebedrijf problem: "Warmtebedrijf stemming" now matches
because "Warmtebedrijf" is in `key_entities` (indexed unstemmed via `simple`) and
"stemming" matches the content (via `dutch`).

### 4d. Verification

Run the v3 eval benchmark (20 questions) with enriched BM25. Compare:
- Does balanced_view (bv-01) retrieve topic-specific chunks better?
- Does multi_hop find vote records and budget comparisons?
- Do temporal queries maintain their 4.75/5 relevance?
- Do factoid queries maintain precision (no new false positives from entity noise)?

---

## 5. Phase B: Entity-Based Qdrant Pre-Filtering

### 5a. Query entity extraction

Extract entities from the user query to use as Qdrant payload filters:

```python
# Option 1: Rule-based (fast, free)
# Extract capitalised terms, known party names, organisation names
entities = extract_entities_from_query(query_text)

# Option 2: Haiku call (more accurate, ~$0.001/query)
entities = await haiku_extract_entities(query_text)
```

### 5b. Filtered vector search

```python
from qdrant_client.models import Filter, FieldCondition, MatchAny

if extracted_entities:
    entity_filter = Filter(must=[
        FieldCondition(key="key_entities", match=MatchAny(any=extracted_entities))
    ])
    results = self._retrieve_by_vector_similarity_with_filter(
        query_embedding, top_k=top_k*3, qdrant_filter=entity_filter,
        date_from=date_from, date_to=date_to
    )
    # Existing fallback logic handles < 5 results → unfiltered search
```

This narrows the vector search space so that similarity matching only compares
semantically among chunks that are already topic-relevant. The vector does one job
(semantic matching) and the filter does another (topic scoping).

### 5c. Verification

Run v3 eval with entity pre-filtering enabled. Measure:
- Precision improvement (fewer off-topic chunks in top-k?)
- Recall maintenance (do we still find all relevant chunks?)
- Latency impact (filtered search should be faster, not slower)

---

## 6. Phase C: Coreference Resolution (Optional)

### 6a. Problem

Some chunks contain unresolved references:
- "de wethouder stelde voor om het budget te verhogen met 2 miljoen"
  → Which wethouder? Which budget?
- "het voorstel werd aangenomen"
  → Which voorstel?

### 6b. Approach

Walk up to parent context (child_id → document_children.content) and resolve
references using the surrounding text:

```python
# Per chunk with detected unresolved references:
parent_text = get_parent_context(chunk.child_id)
resolved_text = await haiku_resolve_coreferences(chunk.content, parent_text)
# "de wethouder" → "wethouder Kurvers (VVD)"
# "het voorstel" → "het voorstel voor het overbruggingskrediet Warmtebedrijf"
```

### 6c. Impact on embeddings

This phase DOES modify chunk content and therefore requires re-embedding — but only for
chunks that actually changed. Estimate: 10-20% of chunks have resolvable references.

```
~160,000-325,000 chunks × ~600 tokens × $0.006/1M (Nebius)
= ~$0.60-1.20 for re-embedding
```

### 6d. Why this is better than contextual retrieval

Coreference resolution makes the chunk text **more precise**, not more generic.
Instead of prepending "this chunk is from meeting X about topic Y" (diluting the vector
with structural metadata), we replace "de wethouder" with "wethouder Kurvers" — making
the vector MORE specific and better-targeted for queries about Kurvers.

### 6e. Decision gate

Only proceed if Phase A + B show remaining gaps in the eval that are caused by
unresolved references rather than keyword gaps. If the entity metadata in Phase A
covers the gap, Phase C is unnecessary.

---

## 7. Approaches explicitly rejected

| Approach | Why rejected | Evidence |
|----------|-------------|----------|
| **Re-embed with context prepended** (original Plan G Phase C) | Dilutes vectors; hierarchy already provides context at retrieval time | Snowflake: -5.8 points on Llama 3; FinanceBench: expansion dropped F1 37.3→33.2 |
| **Late chunking** (Jina AI) | Inconsistent gains across models/datasets; requires switching to Jina embeddings; our hierarchy solves the same problem | Weaviate analysis: MsMarco with Stella-V5 early chunking outperformed late chunking |
| **RAPTOR tree summarization** | Expensive rebuild on document changes; our hierarchical expansion already provides multi-level abstraction | Impractical for 1.63M chunks with updates |
| **Generic LLM-generated summaries** | Less effective than structured metadata (entities + questions) | FinanceBench: structured metadata F1 44.1 vs summaries that hurt performance |
| **Semantic chunking overhaul** | NAACL 2025 showed fixed chunks match or beat semantic chunking; our Gemini chunking is already in place | Diminishing returns over current approach |

---

## 8. Files to create/modify

| File | Action | Phase | LOC est. |
|------|--------|-------|----------|
| `scripts/enrich_chunk_metadata.py` | NEW | A | ~300 |
| `services/rag_service.py` | MODIFY (BM25 query → text_search_enriched) | A | ~10 |
| SQL migration (3 columns + enriched tsvector index) | NEW | A | ~10 |
| `services/rag_service.py` | MODIFY (entity pre-filter in vector search) | B | ~30 |
| `services/query_router.py` | MODIFY (extract entities from query) | B | ~40 |
| `scripts/resolve_coreferences.py` | NEW | C | ~250 |

---

## 9. Execution checklist

```
Phase A — Metadata Enrichment (1 day):
  [ ] Run SQL: ADD COLUMNS section_topic, key_entities, answerable_questions
  [ ] Write scripts/enrich_chunk_metadata.py
  [ ] Smoke test: --limit 100, verify entity extraction + question quality on Dutch text
  [ ] Run full batch (3-5 hours, checkpoint-resumable)
  [ ] Sync metadata to Qdrant payload fields
  [ ] Create enriched tsvector index
  [ ] Modify rag_service.py BM25 queries to use text_search_enriched
  [ ] Run v3 eval benchmark, compare retrieval metrics
  [ ] Decision: if bv-01 and multi_hop improve → proceed to Phase B

Phase B — Entity Pre-Filtering (half day):
  [ ] Implement query entity extraction (rule-based first, Haiku upgrade later)
  [ ] Add entity-based Qdrant pre-filter to _retrieve_by_vector_similarity()
  [ ] Fallback to unfiltered when entity filter returns < 5 results
  [ ] Run v3 eval benchmark — measure precision vs recall tradeoff
  [ ] Decision: if remaining gaps are from unresolved references → proceed to Phase C

Phase C — Coreference Resolution (conditional, 1-2 days):
  [ ] Identify chunks with unresolved references ("de wethouder", "het voorstel", etc.)
  [ ] Write scripts/resolve_coreferences.py
  [ ] Resolve using parent context (walk up to child_id)
  [ ] Re-embed only changed chunks via Nebius
  [ ] Run v3 eval benchmark — verify precision improvement, no regressions
```

---

## 10. Risks and mitigations

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Haiku generates poor entity extraction on Dutch text | Low | Noisy entities hurt BM25 precision | Smoke test on 100 chunks first; Haiku is strong on Dutch |
| Entity pre-filter is too restrictive | Medium | Misses relevant chunks | Existing fallback pattern: < 5 results → unfiltered search |
| Enriched tsvector index is too large | Low | Disk space / query speed | GIN indexes are efficient; monitor pg_total_relation_size |
| Answerable questions overlap with existing chunk_questions | Low | Redundant data | Review overlap; may merge or deduplicate later |
| Coreference resolution introduces errors | Medium | Wrong entity attributed to chunk | Only apply to high-confidence resolutions; keep original text as backup column |

---

## 11. Success criteria

| Metric | Current (v3 baseline) | Target after Phase A+B |
|--------|----------------------|----------------------|
| Precision | 0.99 | Maintain ≥ 0.95 |
| Faithfulness | 4.8/5 | Maintain ≥ 4.5 |
| Completeness | 2.75/5 | Improve to ≥ 3.25 |
| bv-01 (balanced view) | Regression (off-topic chunks) | Topic-specific chunks retrieved |
| Multi-hop vote/budget queries | Partial failures | Vote records found via entity metadata |

---

## 12. References

- Anthropic: [Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval) (Sep 2024) — 67% error reduction, but by combining context + BM25 + reranking (not context alone)
- Snowflake: [Long-Context Isn't All You Need](https://www.snowflake.com/en/engineering-blog/impact-retrieval-chunking-finance-rag/) — markdown-aware chunking + metadata > summaries for financial docs
- arXiv 2510.24402: [Metadata-Driven RAG for Financial QA](https://arxiv.org/html/2510.24402v1) — entity + question metadata: F1 32.9 → 44.1; chunk expansion: F1 37.3 → 33.2
- Jina AI: [Late Chunking](https://jina.ai/news/late-chunking-in-long-context-embedding-models/) — cheaper than contextual retrieval but inconsistent gains
- NAACL 2025: [Reliable Retrieval in RAG](https://aclanthology.org/2025.nllp-1.3.pdf) — fixed chunks match semantic chunking
- arXiv 2504.14493: [FinSage](https://arxiv.org/abs/2504.14493) — multi-modal financial RAG with metadata summaries, 92.51% recall
