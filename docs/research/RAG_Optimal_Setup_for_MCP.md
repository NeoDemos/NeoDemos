# The optimal retrieval architecture for a civic intelligence platform

**Hybrid search — combining BM25 full-text with vector embeddings, layered with metadata filtering and graph traversal — is the right architecture for NeoDemos.** Pure full-text search through MCP will fail on roughly 40% of the queries your professional users actually need (thematic searches, vocabulary mismatches, cross-document synthesis). Pure RAG will fail on the other critical 40% (exact name lookups, vote counts, motion numbers, date-specific queries). The production evidence overwhelmingly favours a hybrid approach that covers both ends, and your existing political knowledge graph is a major architectural asset that should be central to the design. The concrete recommended stack is **PostgreSQL (pgvector + tsvector) + Neo4j + MCP server** — two databases, not four, at roughly €150–350/month.

---

## Hybrid search wins by a wide margin on every benchmark

The question of full-text vs. RAG vs. hybrid for government documents is effectively settled by production data. Microsoft's Azure AI Search benchmark tested all three approaches across thousands of queries and found **hybrid search with a semantic reranker scored 60.1 NDCG@3**, compared to 40.6 for BM25 alone and 43.8 for vector search alone — a 48% improvement over keyword search and 37% over vector search. Anthropic's 2024 contextual retrieval research confirmed the pattern: hybrid (embeddings + BM25) reduced retrieval failures by **49%** over embeddings alone, and adding a reranker pushed that to **67%**.

The reasons are structural, not incidental. Government documents occupy an unusual position in the text spectrum. They use **highly formal, standardised vocabulary** (motie, amendement, raadsbesluit) where BM25 excels at exact matching, but they also discuss **broad policy concepts** (betaalbaar wonen, klimaatadaptatie, energietransitie) where the same topic appears under dozens of different terms across documents and years. No single retrieval method handles both well. The ARLC 2026 Legal RAG Competition, the most recent real-world legal retrieval benchmark, confirmed this: **70% of legal questions were deterministic** (exact names, dates, numbers, boolean outcomes) where BM25 dominates, but the remaining 30% required semantic understanding. The winner used hybrid search with reciprocal rank fusion, pushing grounding accuracy from 0.391 to **0.840**.

For council data specifically, BM25 achieves **NDCG@3 of 79.2 for keyword-type queries** (motion numbers, article references, specific dates) where vector search collapses to 11.7. But for concept-seeking queries, BM25 drops to 39.0 while vector search reaches 45.8. Hybrid covers both. This is not theoretical — Singapore's Pair Search system, which searches 30,000+ parliamentary reports spanning 70 years, uses exactly this architecture: e5 embeddings for semantic search, BM25 for keyword matching, ColBERT v2 reranking, all combined into a multi-signal hybrid score. The team explicitly found that single-metric ranking is "overly biased towards one dimension of result quality."

---

## What breaks without semantic search — and what breaks without keywords

Understanding the failure modes of each approach is critical for making this decision concrete. Here are real query patterns your users will run against Dutch council data, and what happens with each approach.

**Queries that fail with pure full-text search:**

A journalist searching "betaalbaar wonen" (affordable housing) will miss documents discussing "sociale huurwoningen," "woningcorporaties," "huurgrens," or "woonvisie" — the same concept under different terms. A civil servant asking "hoe is het fietsbeleid veranderd sinds 2015?" needs cross-document synthesis across years of cycling-related documents that use varying terminology. A lobbyist searching "vluchtelingenopvang" misses documents about "statushouders," "asielzoekers," "COA-locatie," and "inburgering." CMU research calls vocabulary mismatch "particularly harmful" in legal/government discovery where "missing even just a few relevant results could lead to search failure." The gap is quantifiable: for queries with low term overlap, keyword search achieves only **23.0 NDCG@3 vs. 36.1 for vector search**.

**Queries that fail with pure RAG:**

A politician looking up "besluitenlijst raadsvergadering 14 maart 2023" needs exact date matching — embeddings treat all March 2023 dates as semantically equivalent. A researcher querying "hoeveel stemmen kreeg motie M2023-042?" needs precise number and identifier matching that embedding models handle poorly (they score "23 votes" and "24 votes" at 0.98 similarity). A lawyer searching "artikel 7.3 van de APV" needs exact statutory reference matching. Most critically, embedding models **completely fail on negation** — "moties die NIET zijn aangenomen" (motions NOT adopted) returns adopted motions because "niet aangenomen" and "aangenomen" have near-identical vector representations. Research from Reuter et al. (2025) found that standard RAG pipelines exhibit **document-level retrieval mismatch rates exceeding 95%** on structurally similar documents like contracts and legislative texts, because they share boilerplate language and templates.

---

## Council data demands structure-aware chunking, not fixed-size splitting

Council meeting minutes are among the most heterogeneous document types that exist: a single file contains numbered agenda items, verbatim speeches by different politicians, procedural text, motion filings, amendment proposals, and vote outcomes with party-by-party breakdowns. Fixed-size chunking at 512 tokens — the most common default — will routinely split a speaker's argument across two chunks, merge the end of one agenda item with the start of another, and separate a motion from its vote outcome. Production experience with legal documents confirms this: IP Chimp's legal RAG deployment found that "text was extracted naively — just dumping content into a single string, then chunking based on token counts. In practice this makes for poor retrieval."

The recommended strategy is **document-structure-aware chunking as the primary method**, using the document's own markers (agenda item boundaries, speaker turns, procedural sections, vote records) as natural chunk boundaries. This is directly supported by Stack Overflow's experience implementing semantic search: "We configured our embedding pipeline to consider questions, answers, and comments as discrete semantic chunks. Our pages are highly structured and have a lot of information built into the structure." Council documents have analogous structure. A law firm managing 500,000+ legal documents achieved **95% precision** using structural boundary detection combined with heading/paragraph parsing.

The practical chunking pipeline should work in layers:

- **Primary splits** at agenda item boundaries (numbered, with titles — clean structural markers)
- **Secondary splits** at speaker turn boundaries within each agenda item
- **Tertiary splits** for procedural actions: motion text, amendment text, and vote results as separate chunks
- **Fallback** recursive splitting for any chunk exceeding 512 tokens (long speeches)
- **Rich metadata on every chunk**: document ID, date, municipality, agenda item number and title, speaker name, party affiliation, and chunk type (speech/motion/vote/procedural)

Hierarchical indexing adds significant value: creating parent summaries at the agenda-item and meeting level enables both broad queries ("what was discussed about housing?") and precise queries ("what exactly did politician X say?") to find appropriate entry points. Overlap between chunks should be minimal (10–15%) when using structural boundaries, because structurally complete units rarely need context from adjacent sections. Instead, attach metadata about the parent context — the agenda item title, the preceding speaker's name — as context enrichment rather than text overlap.

---

## The right embedding model for Dutch political text is multilingual-e5-large-instruct

The September 2025 MTEB-NL benchmark — the first comprehensive Dutch embedding evaluation across 40 datasets — provides clear guidance. **Multilingual-e5-large-instruct** (560M parameters) scores **66.9 average** on MTEB-NL, making it the best model under 1 billion parameters. It handily outperforms all Dutch-specific base models including RobBERT (which additionally suffers from a 128-token limit in its sentence transformer variant) and BERTje. The Dutch-specific E5-NL model (e5-large-trm-nl, 355M parameters) scores 64.4 — impressive for its size and a good fallback if you want faster inference. The absolute top scorer is Qwen3-Embedding-4B at **69.2**, but at 4 billion parameters it requires an A100 GPU.

| Model | Parameters | MTEB-NL score | Context length | Notes |
|---|---|---|---|---|
| Qwen3-Embedding-4B | 4B | 69.2 | 8192 | Best quality, expensive to run |
| **multilingual-e5-large-instruct** | **560M** | **66.9** | **514** | **Best price/performance, recommended** |
| e5-large-trm-nl | 355M | 64.4 | 514 | Best Dutch-specific model |
| bge-m3 | 568M | 63.1 | 8192 | Good alternative |
| OpenAI text-embedding-3-large | Unknown | Not benchmarked | 8191 | Strong but API-only, data leaves your infra |

A critical finding: **multilingual models outperform Dutch-only models** at the same scale. The MTEB-NL benchmark shows that "multilingual models (mBERT, mDeBERTa, XLM-R) are the weakest performers" among base models, but the supervised-instruct multilingual models (e5-instruct, Qwen) dominate everything. Fine-tuning for Dutch political vocabulary is not necessary at launch — Tilburg University's experiment fine-tuning RobBERT for Dutch legal text found "no improvement in downstream task performance." Start with multilingual-e5-large-instruct and fine-tune later only if retrieval quality on political queries proves insufficient.

---

## Metadata filtering is the highest-impact, lowest-effort improvement

City council data has richer queryable metadata than almost any other document type: exact dates, party names, politician names, document types (motion/amendment/decision/minutes), vote outcomes (passed/failed with party-by-party counts), committee names, and policy areas. **Layering metadata filtering with semantic search is the single most impactful architectural decision after choosing hybrid search itself**, because it eliminates entire categories of false positives that embedding models cannot avoid — a housing motion from 2018 when the user asked about 2023, or a VVD speech when they asked about PvdA.

Modern vector databases handle this through integrated single-stage filtering, where metadata constraints and vector similarity are evaluated together during index traversal. Qdrant's Filterable HNSW adds intra-category links so filtered nodes don't break graph connectivity. Weaviate's ACORN approach (v1.27+) uses two-hop expansions to skip filtered-out nodes, achieving up to **10× performance improvement** on selective filters. At NeoDemos's scale of 81,000 documents, even brute-force search on a filtered subset is near-instant — filtering by year reduces the search space to ~8,000 documents.

The query decomposition pattern for MCP is **self-query retrieval**: when a user asks "how did VVD vote on housing motions in 2023?", the system extracts structured filters (party="VVD", document_type="motion", topic="housing", year=2023) and a semantic query ("housing motions voting position") in a single LLM call. The semantic query goes to vector search; the structured filters go to metadata pre-filtering. This can be implemented with a Pydantic schema that defines all filterable fields and uses Claude or GPT-4 to extract them. For MCP specifically, the `council_search` tool should accept both a natural language query parameter and optional structured filter parameters, letting the calling LLM pass either or both.

---

## Your knowledge graph is the biggest architectural advantage — use GraphRAG

NeoDemos already has a political knowledge graph mapping politicians to parties, committees, motions, votes, and policy areas. This is an enormous advantage that most GraphRAG implementations lack, because **the most expensive and error-prone step in GraphRAG is LLM-based entity extraction** — and NeoDemos can skip it entirely.

The production evidence for GraphRAG on multi-hop queries is compelling. Cognilium's benchmarks on 500,000 documents show GraphRAG delivers **2× better accuracy on complex relationship queries** compared to standard RAG. For relationship-heavy queries ("find all contracts where the signatory also approved the related amendment"), standard RAG achieved 34% accuracy while GraphRAG achieved **91%**. The Lettria/AWS benchmark found GraphRAG correct on **80% of answers vs. 50.83% for traditional RAG** across legal and technical documents. An ICLR 2026 paper confirms: "Only methods with robust, efficient multi-hop retrieval consistently outperform baseline LLMs or standard RAG approaches."

This maps directly to NeoDemos's core query types. "Which parties that voted against housing motion X in 2019 changed their position by 2024?" requires traversing Motion → Votes → Parties, then temporal filtering, then comparison — a multi-hop graph operation that vector search fundamentally cannot perform. "Show me the voting pattern of politician X across all housing-related motions" requires entity-centric traversal: Politician → all Votes → filter by policy area. Standard RAG scores **28–34% accuracy** on such queries; graph-enhanced retrieval scores **88–91%**.

The practical integration pattern is the **VectorCypherRetriever**: vector search finds relevant document chunks as entry points, then Cypher queries traverse 1–3 hops in the knowledge graph to gather related context (voting records, committee memberships, party positions). Neo4j 5.x supports native vector indexes, meaning you can run vector search and graph traversal in a single database. The WhyHow.AI case study on Congressional hearing transcripts demonstrates this exact pattern — turning 30 hearing transcripts into temporal knowledge graphs and answering multi-hop questions about politicians' activities over time.

Implementation should be phased: **Phase 1** adds filtered hybrid search (immediate high-value improvement). **Phase 2** adds VectorCypherRetriever for graph-enriched context on multi-hop queries. **Phase 3** adds Text2Cypher for purely structured queries (vote counts, politician lookups) and considers community-level summaries (LazyGraphRAG) for broad thematic analysis.

---

## MCP tools should be domain-aware, not CRUD wrappers

Anthropic's published guidance on MCP tool design is explicit: "A common error we've observed is tools that merely wrap existing software functionality or API endpoints." For NeoDemos, this means **not** exposing `search_full_text`, `search_vectors`, `query_graph` as separate tools. Instead, expose **5–7 domain-aware tools** that map to natural user intents and handle retrieval strategy internally.

Research shows tool selection accuracy degrades with scale. The RAG-MCP paper tested selection accuracy from 1 to 11,100 tools and found a "clear non-monotonic trend" with failure dominating beyond position ~100. Anthropic's own Tool Search Tool experiment showed that with 58 tools loaded upfront, accuracy was 49% — but with dynamic tool discovery, it improved to **74%**. Below 10 tools, loading all definitions is fine and avoids the complexity of tool search.

The recommended MCP tool set for NeoDemos:

| Tool | Purpose | Retrieval strategy |
|---|---|---|
| `council_search` | Primary hybrid search — handles 70%+ of queries | BM25 + vector + metadata filters + RRF fusion |
| `council_get_document` | Retrieve full document by ID | Direct lookup |
| `council_get_politician` | Politician profile + activity summary | Graph traversal + aggregation |
| `council_get_votes` | Structured vote data for a motion or topic | Graph query + metadata filter |
| `council_compare_positions` | Compare party/politician stances on a topic | Graph traversal + temporal filtering + vector search |
| `council_timeline` | Temporal evolution of a policy topic | Date-ordered retrieval + graph context |

Each tool accepts a natural language query plus optional structured parameters (date range, party, document type). The `council_search` tool does hybrid retrieval internally — the calling LLM never needs to choose between BM25 and vector search. Specialised tools like `council_compare_positions` encode complex multi-step retrieval strategies (query decomposition → graph traversal → temporal comparison) that would be unreliable if left to the LLM to orchestrate. Tools should return **ranked text chunks with structured metadata** (source document, date, speaker, committee), not synthesised answers — let the LLM handle synthesis and reasoning.

Anthropic's guidance also recommends using MCP **Resources** for static reference data (list of known politicians, party names, committee taxonomy, glossary of Dutch municipal terms) and **Prompts** for pre-built analytical templates. Namespacing all tools with a `council_` prefix helps the LLM disambiguate when other MCP servers are connected.

---

## The recommended stack: PostgreSQL + Neo4j + MCP

For 81,000 Dutch documents with rich structured metadata and an existing knowledge graph, the right architecture is **two databases, not four**.

**PostgreSQL 16+ with pgvector 0.8+ and tsvector** serves as the primary document store, full-text search engine, and vector database in one system. PostgreSQL includes a **built-in Dutch text search configuration** (snowball stemmer and stopwords) out of the box. pgvector's HNSW indexes deliver sub-100ms latency at millions of vectors — 81K documents with an estimated 2–4 million chunks is trivially within capacity. Recent benchmarks show pgvector achieving **471 queries per second at 99% recall on 50 million vectors**. Hybrid search requires application-level score fusion (reciprocal rank fusion in ~20 lines of Python), which is the only meaningful trade-off versus Elasticsearch's built-in RRF.

**Neo4j 5.x** hosts the political knowledge graph and provides native vector indexes (since v5.11) plus Lucene-based full-text search with Dutch analyzer support. The neo4j-graphrag Python package provides VectorRetriever, VectorCypherRetriever, HybridRetriever, and Text2Cypher out of the box. For queries that combine semantic search with graph traversal, Neo4j can handle both in a single round-trip.

**The MCP server** (Python, using the official mcp SDK) sits on top, exposing domain-aware tools that route queries to the appropriate backend. Simple keyword searches go to PostgreSQL's tsvector. Semantic queries go to pgvector with metadata pre-filtering. Relationship queries go to Neo4j's Cypher engine. Complex analytical queries combine all three.

This stack costs approximately **€150–350/month** on managed hosting (Supabase for PostgreSQL, Neo4j AuraDB for the graph). A single engineer can maintain it. The migration path is clear: if full-text search needs exceed PostgreSQL's capabilities, add Elasticsearch alongside (don't replace) PostgreSQL. If vector search needs grow past pgvector, migrate vectors to Weaviate. For 81K to 1 million documents, this stack handles everything comfortably.

---

## Conclusion

The architecture decision for NeoDemos reduces to a clear hierarchy of interventions, ordered by impact.

**Hybrid search is non-negotiable.** The 48% improvement over BM25 alone and 37% over vector search alone is too large to leave on the table, especially when your users are professionals who need both exact legislative references and thematic policy analysis. Implement BM25 + vector search with reciprocal rank fusion from day one.

**Structure-aware chunking is the foundation.** Council documents' built-in structure (agenda items, speaker turns, vote sections) should drive chunking, not arbitrary token counts. Every chunk carries rich metadata. This single decision prevents the 95%+ document-level retrieval mismatch that plagues naive RAG on legislative text.

**Metadata filtering is the highest-leverage addition.** Your data's rich structured metadata (dates, parties, committees, vote outcomes) should be the first filter layer before any semantic matching happens. This eliminates entire categories of false positives that embeddings cannot handle.

**The knowledge graph unlocks the queries that differentiate NeoDemos.** Multi-hop questions about voting pattern evolution, party position changes, and politician-committee-vote relationships are exactly where your platform creates unique value — and exactly where standard RAG fails (28–34% accuracy) while GraphRAG succeeds (88–91%). The existing knowledge graph means you skip the most expensive step and go straight to the high-value retrieval patterns.

The practical lesson from every production parliamentary search system studied — Singapore's Pair Search, the UK's Parlex, Italy's LegisSearch — is the same: **no single retrieval method serves legislative data well**. The systems that work combine keyword precision, semantic understanding, structured metadata, and entity relationships into a unified retrieval layer hidden behind a simple interface. That is exactly what the PostgreSQL + Neo4j + MCP architecture achieves.