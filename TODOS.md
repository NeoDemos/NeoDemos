# NeoDemos — Open TODOs

Quick-capture list. Larger workstreams live in [docs/architecture/V0_2_BEAT_MAAT_PLAN.md](docs/architecture/V0_2_BEAT_MAAT_PLAN.md) and handoffs in [docs/handoffs/](docs/handoffs/).

---

## Now / this week

- [ ] Review overnight pipeline run results (2025 + 2018 data batches)
- [ ] Commit `docs/VERSIONING.md` changes + `.kamal/` accessory config
- [ ] First pass through [brain/FEEDBACK_LOG.md](brain/FEEDBACK_LOG.md) from today's Claude.ai MCP test session

---

## v0.2.0 workstreams (target 2026-04-24)

| # | Workstream | Handoff | Status |
|---|---|---|---|
| WS1 | GraphRAG retrieval | [WS1_GRAPHRAG.md](docs/handoffs/WS1_GRAPHRAG.md) | not started |
| WS2 | Trustworthy financial analysis | [WS2_FINANCIAL.md](docs/handoffs/WS2_FINANCIAL.md) | not started |
| WS3 | Document journey timelines | [WS3_JOURNEY.md](docs/handoffs/WS3_JOURNEY.md) | not started |
| WS4 | Best-in-class MCP surface | [WS4_MCP_DISCIPLINE.md](docs/handoffs/WS4_MCP_DISCIPLINE.md) | not started |
| WS5a | Nightly pipeline | [WS5a_NIGHTLY_PIPELINE.md](docs/handoffs/WS5a_NIGHTLY_PIPELINE.md) | not started |
| WS6 | Source-spans summarization | [WS6_SUMMARIZATION.md](docs/handoffs/WS6_SUMMARIZATION.md) | not started |

---

## Infrastructure / ops

- [ ] Verify Kamal deploy after `.kamal/` config changes
- [ ] Add `logs/mcp_queries.jsonl` to `.gitignore` (query log, not for version control)
- [ ] Set up log rotation for `logs/mcp_queries.jsonl` (cron or logrotate)

---

## Observability (new, from query logging)

- [ ] Write a quick analysis script: `scripts/analyze_query_log.py` — top tools, slow calls, popular queries
- [ ] After ~50 real queries: review log and seed [rag_evaluator/data/questions.json](rag_evaluator/data/questions.json) with real user questions

---

## Deferred to v0.2.1

- [ ] WS5b — Multi-portal connectors (search-only first)
- [ ] Audio transcript pipeline (notulen → annotaties for commissievergaderingen)

---

_Add new items at the top of the relevant section. Move to plan docs once scoped._
