# NeoDemos RAG Evaluation — Benchmark Contract

## v2 Baseline (frozen)

All v2 results are stored in `eval/runs/` and must never be modified.

| Run | Description | Key result |
|-----|-------------|------------|
| v2-full-baseline | 20 questions, top_k=10, no category prompts | Precision 0.99, Relevance 4.1/5, Faithfulness 4.8/5, Completeness 2.75/5 |
| v3-topk15 | top_k=15, pool=75, baseline prompts | Completeness +0.75, but Faithfulness −0.70 (noise from larger pool) |
| v4-topk15-prompts | top_k=15, pool=75, category-aware prompts, threshold=0.0 | TBD — running |

## Benchmark questions (locked)

The 20 questions in `eval/data/questions.json` are the fixed benchmark.
**Do not modify this file.** All v3 architecture tests must use the same questions
so results are directly comparable to v2-full-baseline.

To run a comparable eval with the new architecture:
```bash
python -m eval_v3.run_eval --questions eval/data/questions.json --run-id "v3-arch-v1"
```

## Weak categories from v2 baseline (what we're fixing)

| Category | v2 Relevance | v2 Completeness | Root cause |
|----------|-------------|-----------------|------------|
| party_stance | 2.5/5 | — | No per-party filtered retrieval; generic topic chunks retrieved |
| broad_aggregation | 3.0/5 | 1.0/5 | Fixed top_k=10 misses 80% of relevant chunks; single Gemini call overwhelmed |
| multi_hop | 2.5/5 | 3.0/5 | Single retrieval pass can't connect vote records + alternatives |

## v3 Architecture changes

See `eval_v3/README.md` for the full architectural spec.

Core changes vs v2:
1. **Query router** — classifies query type, routes to different retrieval strategy
2. **Metadata enrichment** — party, committee, vote_record in Qdrant payloads
3. **Per-party stratified retrieval** — 25 chunks × N parties for party_stance
4. **Map-reduce generation** — parallel Gemini mini-summaries → Claude synthesis for broad_aggregation
5. **Sub-query decomposition** — Haiku decomposes → parallel retrieval → Sonnet synthesizes for multi_hop
6. **Dynamic K** — top_k varies by query type (8 factoid, 15 temporal, 25 party, 60-80 aggregation)
