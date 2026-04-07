# v3 Architecture Evaluation Report

**Date:** 2026-04-07
**Run:** v3-full-v1 (20 questions, hallucination check enabled)
**Comparison:** v2-full-baseline → v4-topk15-prompts (v2 best) → v3-full-v1

---

## 1. Overall Results

| Metric | v2 baseline | v2 best | **v3** | Delta vs v2 best |
|--------|------------|---------|--------|-----------------|
| Context Precision | 0.99 | 1.00 | **0.95** | -0.05 |
| Answer Relevance | 4.10 | 4.10 | **4.40** | **+0.30** |
| Faithfulness | 4.80 | 4.45 | **4.75** | **+0.30** |
| Completeness | 2.75 | 3.50 | **3.25** | -0.25 |
| Hallucination Rate | — | — | **0%** | NEW |
| Source Attribution | — | — | **4.6/5** | NEW |
| Avg time/question | — | 177s | **12.5s** | **14x faster** |

**Headline:** Relevance and faithfulness improved. Hallucination rate is 0% on all questions where claim extraction succeeded. Speed improved 14x (paid Jina tier + optimised routing).

---

## 2. Category Breakdown

### Targets Hit

| Category | Metric | v2 best | **v3** | Target | Status |
|----------|--------|---------|--------|--------|--------|
| party_stance | Relevance | 2.5 | **4.5** | 4.0+ | **HIT** |
| party_stance | Faithfulness | 3.0 | **5.0** | ≥4.5 | **HIT** |
| party_stance | Hallucination | — | **0%** | <10% | **HIT** |
| broad_aggregation | Relevance | 4.0 | **5.0** | ≥4.0 | **HIT** |
| broad_aggregation | Completeness | 4.0 | **5.0** | ≥4.0 | **HIT** |
| broad_aggregation | Faithfulness | 5.0 | **5.0** | ≥4.5 | **HIT** |
| absence | Relevance | 4.0 | **5.0** | — | **IMPROVED** |
| informal_opinion | Relevance | 4.0 | **5.0** | — | **IMPROVED** |

### Targets Missed

| Category | Metric | v2 best | **v3** | Target | Status |
|----------|--------|---------|--------|--------|--------|
| multi_hop | Relevance | 2.5 | **2.0** | 3.5+ | **MISSED** |
| multi_hop | Completeness | 3.0 | **2.0** | ≥3.0 | **MISSED** |
| balanced_view | Relevance | 5.0 | **2.0** | — | **REGRESSION** |
| balanced_view | Faithfulness | 5.0 | **2.0** | — | **REGRESSION** |

### Maintained

| Category | v2 best | **v3** | Status |
|----------|---------|--------|--------|
| temporal (8 Qs) | 4.75 / 5.0 | 4.75 / 4.88 | Stable |
| acronym (2 Qs) | 5.0 / 5.0 | 5.0 / 5.0 | Perfect |
| specific_event | 5.0 / 4.0 | 5.0 / 5.0 | Improved |

---

## 3. Root Cause Analysis

### 3a. balanced_view regression (bv-01): 5/5 → 2/5

**Question:** "Wat zijn de belangrijkste aandachtspunten voor de gebiedsontwikkeling M4H?"

**What happened:** v3 retrieved chunks about general Rotterdam municipal policy (health, safety, sport, sustainability) alongside M4H-specific content. The LLM couldn't distinguish between the two and presented general policy points as M4H-specific, which the judge correctly flagged as misleading.

**Why v2 was better:** v2 used top_k=15 with category-aware Gemini prompts that explicitly said "treat both perspectives equally." v3 routed bv-01 through `standard` strategy (no special handling) because the balanced_view router path has no content-filtering logic.

**Fix needed:** balanced_view questions need either:
- Sub-query decomposition ("positive M4H aspects" + "negative M4H aspects") — similar to multi_hop
- Or topic-focused post-retrieval filtering to remove chunks not directly about the query subject

### 3b. multi_hop underperformance (mh-01, mh-02)

**mh-01 (Warmtebedrijf votes):** Relevance 3/5, Faithfulness 4/5
- Sub-query decomposition correctly split into "who voted against" + "what alternatives"
- But the **vote record doesn't exist as a structured chunk** — it's scattered across meeting fragments
- v3 was honest about limitations (faithfulness 4/5) vs v2 which fabricated party positions (faithfulness 1/5)
- Trade-off: more honest but less complete

**mh-02 (culture vs sport budget):** Relevance 1/5, Faithfulness 5/5
- Sub-query decomposition retrieved **general financial tables** (BUIG, energy, infrastructure) instead of culture/sport-specific budget lines
- The answer started with ~40 lines of irrelevant financial calculations before acknowledging the question can't be answered
- **The financial table boost brought in noise** — retrieved table chunks were about wrong budget posts
- Judge reasoning: "The irrelevant opening makes the answer confusing and not usable"

**Core issue:** Sub-query decomposition improves faithfulness (no hallucination) but retrieval still returns wrong documents for financial comparison questions. The table boost needs to be more targeted.

### 3c. Hallucination rate parsing bug

7 out of 20 questions have `hallucination_rate: -1.0` (displayed as "-100% UNSAFE"). This is a **JSON parsing failure** in the claim extraction step — the claim verifier couldn't parse the LLM's structured output due to Dutch text escaping issues. These questions are NOT actually unsafe; the rate should be `null` (unknown).

Affected: ba-01, ba-02, te-03, te-04, bv-01, se-01, and one more. The 13 questions where parsing succeeded all show 0% hallucination rate.

---

## 4. Strategy Effectiveness

| Strategy | Questions | Avg Relevance | Avg Faithfulness | Verdict |
|----------|-----------|---------------|------------------|---------|
| party_filtered | 2 (ps-01, ps-02) | **4.5** | **5.0** | Excellent — biggest win |
| map_reduce | 2 (ba-01, ba-02) | **5.0** | **5.0** | Excellent — perfect scores |
| sub_query | 2 (mh-01, mh-02) | **2.0** | **4.5** | Faithful but irrelevant |
| standard | 14 (rest) | **4.6** | **4.7** | Solid baseline |

**Map-reduce is the standout architecture.** It achieves perfect scores on both broad_aggregation questions. The parallel Gemini summaries → Claude synthesis pattern works exactly as designed.

**Party-filtered retrieval works** even with limited party metadata coverage (6.5% of chunks). The fallback to keyword boost catches what the filter misses, and Claude Sonnet generation is significantly more faithful than Gemini for party attribution.

**Sub-query decomposition needs work.** The decomposition step itself is fine (Haiku produces good sub-queries), but the retrieval per sub-query brings in noise that isn't filtered before synthesis.

---

## 5. Research-Backed Improvement Recommendations

Based on the v3 results, the RAG_Beyond_RAG_Research_Report, and the specific failure modes observed:

### Priority 1: Fix regressions (immediate)

**A. Route balanced_view through sub_query strategy**
Change: In `query_router.py`, map `balanced_view` → `sub_query` instead of `standard`. Decompose into "positive aspects of [topic]" + "negative aspects of [topic]".
Impact: Fixes bv-01 regression by ensuring both perspectives are retrieved independently.
Effort: 5 lines of code.

**B. Fix hallucination rate parsing bug**
Change: In `eval/metrics/hallucination.py`, catch JSON parse errors and set `hallucination_rate: null` instead of `-1.0`.
Impact: Fixes false UNSAFE flags on 7/20 questions.
Effort: 10 lines of code.

**C. Add topic-relevance filter to sub_query synthesis**
Change: In `services/decomposition.py`, add a prompt instruction to the Sonnet synthesis: "Only include information directly relevant to the original question. Omit retrieved data that doesn't address the question, even if it is factually correct."
Impact: Fixes mh-02 (40 lines of irrelevant financial calculations) and would improve bv-01.
Effort: 2 lines (prompt modification).

### Priority 2: Improve multi_hop (next iteration)

**D. Implement CRAG (Corrective RAG) for sub-query results**
What: After sub-query retrieval, evaluate each chunk's relevance to the original question (not just the sub-query). Discard chunks that score below threshold.
Research basis: CRAG achieved 92.6% composite accuracy by detecting and correcting retrieval failures.
Impact: Prevents the noise accumulation problem in mh-02 where "budget" sub-queries retrieved general financial data.
Effort: ~100 LOC new module.

**E. Targeted financial table retrieval**
What: Instead of generic `chunk_type='table'` filter, match table headers against the query's financial concepts (e.g. "cultuur", "sport") using keyword overlap before including table chunks.
Impact: Fixes the financial table boost bringing in irrelevant tables.
Effort: ~40 LOC in `rag_wrapper_v3.py`.

**F. Vote record extraction**
What: Build a structured extraction pipeline for vote records from notulen. Parse patterns like "aangenomen met X voor en Y tegen" and store in a dedicated table.
Research basis: The US Legislative Graph paper demonstrates structured vote record extraction from parliamentary transcripts.
Impact: Directly addresses mh-01 (Warmtebedrijf votes) — the data exists but isn't structured.
Effort: ~200 LOC extraction script + schema addition. Medium term.

### Priority 3: Architecture improvements (next sprint)

**G. Contextual Retrieval (Anthropic)**
What: Prepend each chunk with a LLM-generated context sentence before embedding: "This chunk is from a [party] [document_type] about [topic], discussed on [date]."
Research basis: 67% retrieval error reduction in Anthropic's research.
Impact: Addresses the core problem of chunks losing context. A motie chunk about "het voorstel" would be contextualised as "This chunk is from a PvdA motion about Warmtebedrijf."
Effort: Batch re-processing of 1.63M chunks via API. One-time cost ~$30-50 in API calls.
Caveat: Requires re-embedding all chunks. Could be combined with an embedding model upgrade.

**H. Self-query retriever with metadata filters**
What: Let the LLM automatically formulate Qdrant metadata filters from the query. "What did DENK say about Tweebosbuurt in 2021?" → party=DENK, date=2021, topic filters.
Research basis: Metadata enrichment improves retrieval 10-15% per research report.
Impact: Generalises the party_filtered approach to any metadata dimension (doc_type, committee, policy domain).
Effort: ~150 LOC. Builds on existing enrichment metadata.

### Priority 4: Strategic improvements (1-3 months)

**I. LightRAG entity extraction**
What: Extract domain-specific entities (partij, raadslid, motie, begrotingspost, wijk) and their relationships. 10x cheaper than full GraphRAG.
Research basis: LightRAG (EMNLP 2025) achieves comparable quality to GraphRAG with dual-level retrieval.
Impact: Enables true multi-hop reasoning by traversing entity relationships instead of relying on text similarity.
Effort: Major initiative. Entity extraction over 71K documents.

**J. RAPTOR hierarchical summaries**
What: Build a summarization tree over document clusters for high-level thematic queries.
Research basis: 20% accuracy improvement on multi-step reasoning.
Impact: Questions like "How has Rotterdam's housing policy evolved?" that need thematic understanding beyond chunk-level retrieval.
Effort: One-time build phase + ongoing maintenance.

---

## 6. Proposed Execution Order

```
Week 1 (Quick fixes):
  A. Route balanced_view through sub_query           [5 min]
  B. Fix hallucination parsing bug                   [30 min]
  C. Add topic-relevance instruction to synthesis    [15 min]
  → Re-run v3-full-v2 and compare

Week 2 (Multi_hop improvements):
  D. CRAG relevance filter for sub-query results     [half day]
  E. Targeted financial table matching               [half day]
  → Re-run v3-full-v3 and compare

Week 3-4 (Architecture):
  G. Contextual Retrieval batch job                  [2-3 days]
  H. Self-query metadata filters                     [1 day]
  → Re-run v3-full-v4 with new embeddings

Month 2-3 (Strategic):
  F. Vote record extraction pipeline                 [1 week]
  I. LightRAG entity extraction                      [2-3 weeks]
```

---

## 7. Success Criteria for v3-full-v2

After applying Priority 1 fixes (A, B, C):

| Category | Current v3 | Target v3-v2 |
|----------|-----------|-------------|
| party_stance | 4.5 rel, 5.0 faith | Maintain |
| broad_aggregation | 5.0 / 5.0 / 5.0 | Maintain |
| balanced_view | 2.0 rel, 2.0 faith | **4.0+ rel, 4.0+ faith** |
| multi_hop | 2.0 rel, 4.5 faith | **3.0+ rel, maintain faith** |
| Overall relevance | 4.40 | **4.60+** |
| Overall faithfulness | 4.75 | **4.80+** |
| Hallucination rate | 0% (where parsed) | 0% (all 20 parseable) |
