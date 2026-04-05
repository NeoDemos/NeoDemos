# Beyond RAG: Research Report for NeoDemos

**Date:** April 2, 2026
**Author:** Claude (research assistant for Dennis)
**Scope:** Optimizing retrieval and reasoning over 71,000 Rotterdam City Council documents (1.3M chunks)

---

## Executive Summary

Your current NeoDemos setup — PostgreSQL + Qdrant hybrid search with RRF fusion, semantic chunking via Gemini, Qwen3-8B embeddings on Apple Silicon, and CrossEncoder reranking — is already a solid foundation. But research confirms your intuition: basic RAG has fundamental limitations when dealing with data that requires understanding historical evolution, multi-party dynamics, and financial impact chains.

This report maps out what you can improve within your current setup, what new architectures to consider, and what state-of-the-art research says about the specific challenge of extracting hidden relationships from large document corpora like city council records.

---

## Q1: Getting the Most Out of Your Current RAG Setup

### 1.1 Chunking Strategy Refinements

Your current approach uses a three-tier hierarchical system (atomic < 1K chars, linear 1K-16K, hierarchical > 16K) with semantic chunking via Gemini and ~1,000 character grandchild chunks. Recent benchmarks (February 2026) reveal some important nuances:

**Recursive character splitting at 512 tokens with 50-100 token overlap achieved 69% accuracy in real-document tests, outperforming semantic chunking (54%) in certain retrieval scenarios.** The reason: semantic chunking can produce fragments that are too short (averaging 43 tokens), which hurts downstream LLM generation even when retrieval precision is high.

However, for your domain, pure recursive splitting misses important structural boundaries. The recommendation is a hybrid approach:

- **Keep your hierarchical structure** but consider increasing grandchild chunk size from ~1,000 characters to ~1,500-2,000 characters. Council documents contain complete arguments, motions, and financial clauses that lose meaning when split too small.
- **Respect document structure boundaries.** A motion's "dictum" (het dictum) should never be split from its "overwegingen" (considerations). An amendment should stay with the text it modifies.
- **Add parent-document context to each chunk.** Anthropic's Contextual Retrieval research (September 2024) showed that prepending a short contextual summary to each chunk before embedding reduces retrieval errors by up to 67%. For example, a chunk about a housing budget amendment would be prepended with: "This chunk is from a D66 amendment to the 2024 housing budget, discussed in the commissie BWB on March 15, 2024."

### 1.2 Metadata Enrichment — The Biggest Quick Win

Your current metadata includes chunk type, speaker context, and audio confidence tiers. Research shows metadata enrichment improves retrieval accuracy by 10-15%, and for your domain the potential is even higher because council data is inherently structured:

**Essential metadata to add per chunk:**

- **Party affiliation** of the speaker/author (fractie)
- **Document type classification:** motie, amendement, schriftelijke vragen, begrotingsvoorstel, raadsbesluit, commissieverslag
- **Policy domain tags:** housing (wonen), finance (financiën), mobility (mobiliteit), social (sociaal domein), etc.
- **Referenced legislation or previous decisions** (e.g., "verwijst naar raadsbesluit 2023-0456")
- **Financial amounts mentioned** (extracted via regex or NER)
- **Voting outcome** if available (aangenomen/verworpen, stemverhouding)
- **Legislative stage:** commissie, plenair, hamerstuk, debat

**Self-query retrieval:** With rich metadata, you can implement a self-query retriever where the LLM automatically formulates metadata filters. For example, the query "What did DENK say about the Tweebosbuurt in 2021?" would automatically filter on party=DENK, topic=Tweebosbuurt/housing, year=2021 before doing semantic search.

### 1.3 Hybrid Search Optimization

You already have BM25 + vector search with RRF. Here are refinements:

- **Tune BM25 parameters.** Dutch council language has specific characteristics — formal legal terminology mixed with political rhetoric. The default k1=1.2 and b=0.75 may not be optimal. Consider testing k1=1.5-2.0 (council documents tend to repeat key terms more) and b=0.5-0.6 (document length variation is high between a one-paragraph motion and a 50-page begrotingsvoorstel).
- **Add Dutch-specific text processing.** Ensure your BM25 index handles Dutch compound words (samenstelling), e.g., "woningbouwprogramma" should match "woningbouw" and "programma" separately.
- **Consider ParadeDB's pg_search** as a drop-in for PostgreSQL-native BM25 that's been benchmarked to outperform basic tsvector approaches.

### 1.4 Reranking Improvements

Your CrossEncoder (ms-marco-MiniLM-L-6-v2) adds 3-8 seconds. Two options:

- **Cohere Rerank 3/3.5:** API-based, handles 4K context windows, multilingual including Dutch, specifically trained for complex document structures. Faster than local CrossEncoder for your setup. Reported +20-40% accuracy improvement.
- **ColBERT v2:** Pre-computes document representations, enabling much faster reranking. A good middle ground between speed and quality. Can run on your M5 Pro.

### 1.5 Embedding Model Considerations

Your Qwen3-Embedding-8B is powerful (4,096 dimensions) but processes at only 3-6 chunks/second. For query-time this is fine, but consider:

- **Nomic Embed Text v2 MoE** (released February 2025): First Mixture-of-Experts embedding model, 475M params (305M active), SOTA on both BEIR and MIRACL benchmarks, supports ~100 languages including Dutch, and uses Matryoshka learning for flexible dimensionality. Much faster than your 8B model.
- **E5-NL / MTEB-NL benchmarked models:** Specifically evaluated for Dutch language tasks. Worth benchmarking against Qwen3 on your actual council data.

---

## Q2: Approaches That Build on RAG With Significantly Better Results

### 2.1 GraphRAG — The Most Promising Direction for Council Data

**What it is:** Microsoft's GraphRAG (2024) builds a knowledge graph from your documents, detecting entities and their relationships, then groups them into communities using the Leiden algorithm. Each community gets an LLM-generated summary. Queries can be answered at different levels of abstraction.

**Why it's transformative for NeoDemos:** City council data is fundamentally a web of relationships: parties take positions, councillors propose motions, motions reference budgets, budgets fund programs, programs affect neighborhoods, neighborhoods have residents who submit inspraakreacties. Flat vector search cannot capture these chains. GraphRAG can.

**Real-world precedent:** The UK Government's graph of legislation contains 820K+ nodes and 2.2M edges. A case study on US Congressional hearing transcripts successfully transformed them into temporal knowledge graphs with nodes for hearings, persons, topics, and organizations. A 2025 paper specifically addresses "Graph RAG for Legal Norms: A Hierarchical and Temporal Approach."

**Practical concern:** Full GraphRAG is expensive to build (requires LLM calls for entity extraction across all documents). However, **LightRAG** (EMNLP 2025) reduces overhead by 10x through dual-level retrieval, and **MiniRAG** achieves comparable quality with 4x less storage.

### 2.2 RAPTOR — Hierarchical Summarization Tree

**What it is:** Recursively clusters document chunks, generates summaries at each level, building a tree from detailed leaves to abstract trunk. Queries retrieve from the appropriate level.

**Why it matters for you:** A question like "How has Rotterdam's housing policy evolved since 2018?" needs high-level theme understanding, not individual chunk retrieval. RAPTOR showed 20% absolute accuracy improvement on multi-step reasoning tasks. You build the tree once over your 71K documents, and then different query types hit different levels.

**Practical fit:** The build phase requires clustering + LLM summarization, but it's a one-time cost. Query-time is fast. Open-source implementations exist. Your M5 Pro with 64GB can handle the build phase, though it will take time.

### 2.3 Agentic RAG — Multi-Step Reasoning

**What it is:** Instead of single-shot retrieve-and-generate, an AI agent decomposes complex queries into sub-questions, retrieves iteratively, and synthesizes across multiple retrieval rounds.

**Example for Rotterdam:** "How did the coalition's stance on the Tweebosbuurt demolitions affect the 2022 budget negotiations?"

An agent would: (1) retrieve documents about Tweebosbuurt demolitions and identify the parties involved, (2) retrieve those parties' positions during 2022 budget debates, (3) retrieve the actual budget amendments related to housing/Tweebosbuurt, (4) synthesize the causal chain.

**Your current setup already points this direction** with your stream-based retrieval (vision/financial/debate/fact streams). Agentic RAG formalizes and extends this pattern. LangGraph is the most practical framework for implementation.

### 2.4 Corrective RAG (CRAG)

**What it is:** Adds a quality evaluator after retrieval. If retrieved documents aren't relevant, the system automatically tries alternative strategies: different search modes, query rewriting, or flagging uncertainty.

**Why it matters:** Council queries often use informal language to refer to formal processes. "That thing about the market" might mean "Markthal renovatie begrotingsvoorstel 2023." CRAG handles this gracefully by detecting retrieval failure and adapting.

**Performance:** RAG-EVO (2025) extended CRAG with evolutionary learning, achieving 92.6% composite accuracy. The Higress-RAG framework (February 2026) combines adaptive routing, semantic caching, and dual hybrid retrieval for >90% recall.

### 2.5 Anthropic's Contextual Retrieval

**What it is:** Before embedding, each chunk gets prepended with a short LLM-generated context that explains what document it comes from and what role this chunk plays. Combined with Contextual BM25 indexing.

**Impact:** 67% reduction in retrieval errors. This is probably the highest ROI improvement you can make — it directly addresses the "lost context" problem where a chunk about "het voorstel" (the proposal) becomes meaningless without knowing which proposal.

**Practical:** Can be done via Anthropic's API (no local inference needed). You'd need to re-process your 1.3M chunks, but it's a batch job you run once.

---

## Q3: What's Viable on Your Current Setup?

Your hardware: MacBook Pro M5 Pro, 64GB unified memory, running Qwen3-8B-4bit for embeddings, Qdrant on disk, PostgreSQL 16.

### Immediately viable (weeks of work):

| Approach | Feasibility | Why |
|---|---|---|
| **Metadata enrichment** | High | Batch process using your existing Gemini API or local LLM to extract party, document type, policy domain, financial amounts per chunk. Store in PostgreSQL. |
| **Contextual Retrieval** | High | Batch process via Anthropic API. Re-embed chunks with context prepended. One-time cost. |
| **Self-query retriever** | High | Leverage enriched metadata. LangChain/LlamaIndex have built-in support. |
| **BM25 tuning** | High | Parameter adjustment + Dutch compound word handling. No new infrastructure. |
| **CRAG / query rewriting** | Medium-High | Add evaluation step after retrieval + fallback strategies. Uses your existing LLM infrastructure. |

### Medium-term viable (1-3 months):

| Approach | Feasibility | Why |
|---|---|---|
| **Agentic RAG** | Medium-High | LangGraph orchestration over your existing retrieval. Multiple LLM calls per query but no new infrastructure. |
| **RAPTOR tree** | Medium | Build phase is compute-intensive but one-time. Your 64GB RAM can handle clustering. Summarization via API. |
| **LightRAG** | Medium | 10x cheaper than full GraphRAG. Entity extraction over 71K docs is feasible on your hardware with batching. |
| **Better embedding model** | Medium | Switch to Nomic Embed v2 MoE for speed, or benchmark E5-NL for Dutch quality. Requires re-embedding 1.3M chunks. |

### Longer-term / requires more resources:

| Approach | Feasibility | Why |
|---|---|---|
| **Full GraphRAG** | Lower | Entity extraction across 1.3M chunks with LLM is expensive (API costs or very long local processing). Start with LightRAG. |
| **Knowledge graph (Neo4j)** | Lower | New infrastructure + significant modeling work. But the UK legislation graph shows it's worth it at scale. |
| **Fine-tuned Dutch embedding model** | Lower | Requires training data and GPU compute beyond M5 Pro. Consider when you have evaluation data. |

---

## Q4: State-of-the-Art Research Findings

### 4.1 The RAG Research Explosion

The RAG field has exploded: over 1,200 papers on arXiv in 2024 alone (vs. <100 in 2023), and the market reached $1.85 billion in 2024 with 49% CAGR. The consensus in 2025-2026 research is clear: **basic RAG is a starting point, not a destination.**

### 4.2 Key Academic Papers Relevant to NeoDemos

**On GraphRAG for legislative data:**
- "From Local to Global: A Graph RAG Approach to Query-Focused Summarization" (Microsoft, arXiv 2404.16130) — the foundational GraphRAG paper
- "Graph RAG for Legal Norms: A Hierarchical and Temporal Approach" (arXiv 2505.00039, 2025) — specifically builds GraphRAG for legal/regulatory compliance with temporal evolution tracking
- "Towards Practical GraphRAG: Efficient Knowledge Graph Construction and Hybrid Retrieval at Scale" (arXiv 2507.03226, 2025) — addresses cost reduction, achieving 94% of LLM-based performance with 65-80% cost savings

**On parliamentary/legislative NLP:**
- ParlLawSpeech: A full-text European legislative corpus linking bills to plenary speeches to adopted laws
- Norwegian Parliamentary Debates Dataset: ~1 million speeches from 1945-2024 — demonstrates large-scale parliamentary corpus handling
- "US Legislative Graph" (VLDB 2025 Workshop): The widest structured database of congressional legislation, using LLM fine-tuning for entity extraction
- Legal information extraction survey (Springer, 2025): Covers NER, relationship extraction, and event detection specifically for legislative documents

**On reliability and hallucination mitigation:**
- MEGA-RAG: Multi-evidence guided answer refinement, specifically addressing hallucination
- MARCH: Improved accuracy from 55.20% to 74.93% using multi-agent validation combined with RAG
- "Mitigating Hallucination in LLMs: An Application-Oriented Survey on RAG, Reasoning, and Agentic Systems" (arXiv 2510.24476) — key finding: RAG alone supplements facts but cannot guarantee logical consistency; combining RAG with reasoning is most effective

**On temporal knowledge:**
- Temporal Knowledge Graph Reasoning Survey (arXiv 2509.15464): Foundation models need to treat time as fundamentally sequential, not just another edge attribute. Critical for tracking how party positions and policies evolve over council terms.
- Legal frameworks using 3-stage hierarchical prompts + 3-layer knowledge graphs (legal ontology, representation, instances)

**On efficient retrieval at scale:**
- ATLAS system: 900M+ nodes, 5.9B edges from 50M documents — demonstrates that graph-based retrieval scales
- MA-DPR (Manifold-aware Dense Passage Retrieval): +26% out-of-distribution recall via shortest-path distance in KNN graphs
- LongRAG: Processes entire document sections instead of 100-word chunks, reducing context loss by 35% in legal documents

**On Dutch language processing:**
- MTEB-NL benchmark: Extends the Massive Text Embedding Benchmark to Dutch, but highlights a key limitation — lack of fine-tuning datasets for Dutch
- E5-NL (arXiv 2509.12340): Specific embedding benchmark and models for Dutch

### 4.3 The Emerging Consensus

The research points toward a convergence of techniques that, when combined, dramatically outperform basic RAG:

1. **Structured knowledge representation** (graphs, not just vectors) is necessary for understanding relationships
2. **Multi-level abstraction** (RAPTOR-style hierarchies) is necessary for questions that span different scales
3. **Iterative retrieval with reasoning** (agentic patterns) is necessary for complex multi-hop questions
4. **Self-correction** (CRAG patterns) is necessary for reliability
5. **Rich metadata** is the foundation that makes all of the above work better

For NeoDemos specifically, the most impactful research direction is the intersection of GraphRAG with temporal reasoning over legislative data — this is exactly what the "Graph RAG for Legal Norms" paper addresses, and it maps directly to your need to understand how party positions, policy decisions, and budgets evolve and interrelate across council terms.

---

## Recommended Roadmap

### Phase 1: Optimize Current RAG (April-May 2026)
1. Enrich metadata on existing chunks (party, document type, policy domain, financial amounts)
2. Implement Contextual Retrieval (prepend context to chunks, re-embed)
3. Add self-query retriever with metadata filtering
4. Tune BM25 parameters for Dutch council language
5. Evaluate Cohere Rerank 3 vs. your current CrossEncoder

### Phase 2: Add Intelligence Layer (June-August 2026)
1. Implement CRAG (corrective retrieval with query rewriting)
2. Build agentic RAG with LangGraph for multi-step queries
3. Build RAPTOR summarization tree for thematic queries
4. Benchmark Dutch-specific embedding models

### Phase 3: Knowledge Graph (September-December 2026)
1. Start with LightRAG for entity-relationship extraction
2. Build domain-specific entity types (partij, raadslid, motie, begrotingspost, wijk, beleidsterrein)
3. Add temporal edges (raadsperiode, vergaderdatum, inwerkingtreding)
4. Implement graph-enhanced retrieval alongside vector search

---

## Key Sources

**Chunking & Optimization:**
- [Document Chunking for RAG: 9 Strategies Tested](https://langcopilot.com/posts/2025-10-11-document-chunking-for-rag-practical-guide)
- [RAG Chunking Strategies: The 2026 Benchmark Guide](https://blog.premai.io/rag-chunking-strategies-the-2026-benchmark-guide/)

**Hybrid Search & PostgreSQL:**
- [Hybrid Search in PostgreSQL: The Missing Manual (ParadeDB)](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual)
- [pgvector 0.8.0 Improvements](https://aws.amazon.com/blogs/database/supercharging-vector-search-performance-and-relevance-with-pgvector-0-8-0-on-amazon-aurora-postgresql/)

**GraphRAG:**
- [Microsoft GraphRAG](https://www.microsoft.com/en-us/research/project/graphrag/) | [GitHub](https://github.com/microsoft/graphrag)
- [Graph RAG for Legal Norms (arXiv 2505.00039)](https://arxiv.org/html/2505.00039v2/)
- [LightRAG (EMNLP 2025)](https://github.com/HKUDS/LightRAG)
- [UK Government Knowledge Graph of Legislation](https://ai.gov.uk/blogs/understanding-legislative-networks-building-a-knowledge-graph-of-uk-legislation/)
- [Congressional Hearing Transcripts as Knowledge Graphs](https://medium.com/enterprise-rag/case-study-turning-congressional-hearing-transcripts-into-temporal-knowledge-graphs-0d78075181c7)

**RAPTOR:**
- [RAPTOR: Recursive Abstractive Processing (arXiv 2401.18059)](https://arxiv.org/html/2401.18059v1)

**Corrective & Self-RAG:**
- [CRAG (OpenReview)](https://openreview.net/forum?id=JnWJbrnaUE)
- [The 2025 Guide to RAG](https://www.edenai.co/post/the-2025-guide-to-retrieval-augmented-generation-rag)

**Contextual Retrieval:**
- [Anthropic's Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval)

**Parliamentary Data:**
- [ParlLawSpeech European Legislative Corpus](https://parllawspeech.org/data/)
- [Norwegian Parliamentary Debates Dataset](https://www.nature.com/articles/s41597-024-04142-x)
- [US Legislative Graph (VLDB 2025)](https://www.vldb.org/2025/Workshops/VLDB-Workshops-2025/LLM+Graph/LLMGraph-2.pdf)

**Dutch Language:**
- [E5-NL Dutch Embeddings (arXiv 2509.12340)](https://arxiv.org/abs/2509.12340)
- [Nomic Embed Text v2 MoE](https://huggingface.co/nomic-ai/nomic-embed-text-v2-moe)

**Hallucination Mitigation:**
- [Mitigating Hallucination Survey (arXiv 2510.24476)](https://arxiv.org/abs/2510.24476)

**Agentic RAG & Multi-Hop:**
- [Agentic RAG with Knowledge Graphs (arXiv 2507.16507)](https://arxiv.org/html/2507.16507v1)
- [Awesome-GraphRAG (GitHub)](https://github.com/DEEP-PolyU/Awesome-GraphRAG)
