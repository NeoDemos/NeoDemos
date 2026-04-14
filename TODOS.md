# NeoDemos — Triage Inbox & Operations

**Scope rules** *(updated 2026-04-11 — keeps this file from becoming a graveyard)*:

- **Feature work lives in [`docs/handoffs/`](docs/handoffs/).** If an item has a clear workstream home, move it there — do not keep it here.
- **Raw observations live in [`brain/FEEDBACK_LOG.md`](brain/FEEDBACK_LOG.md).** This file is for *already-triaged* items that need follow-up.
- **What belongs here:**
  - (a) Items triaged from `FEEDBACK_LOG.md` that genuinely have no clear WS home (**max 7 days** — if it lingers, either force a WS assignment or discard)
  - (b) Operational / ops tasks that don't fit any WS (deploys, certs, gitignore, log rotation, one-off scripts)
  - (c) Explicit parking with a re-eval date
- **Weekly triage ritual (Mondays):** walk `FEEDBACK_LOG.md` and the Triage Inbox below. Each item → WS handoff edit, memory update, operational task, or discard. No item stays untriaged for more than 7 days.

---

## Triage inbox

*Items awaiting WS assignment. Weekly-cleaned. **Empty is a healthy state** — if this section is always growing, triage discipline is slipping.*

_(empty — last cleaned 2026-04-11)_

---

## Operational / ops

- [ ] Review overnight pipeline run results (2025 + 2018 data batches)
- [ ] Commit `docs/VERSIONING.md` changes + `.kamal/` accessory config
- [ ] Verify Kamal deploy after `.kamal/` config changes
- [ ] Add `logs/mcp_queries.jsonl` to `.gitignore` (query log, not for version control)
- [ ] Set up log rotation for `logs/mcp_queries.jsonl` (cron or logrotate)
- [ ] Write a quick analysis script: `scripts/analyze_query_log.py` — top tools, slow calls, popular queries
- [ ] After ~50 real MCP queries logged: review and seed [rag_evaluator/data/questions.json](rag_evaluator/data/questions.json) with real user questions

### MCP reliability follow-ups (from 2026-04-14 outages)

Full context + file paths in [WS4 §Post-ship reliability follow-ups](docs/handoffs/WS4_MCP_DISCIPLINE.md#post-ship-reliability-follow-ups-opened-2026-04-14). Memory: [feedback_mcp_uptime.md](.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/feedback_mcp_uptime.md).

- [ ] **(1) Statement timeout on auth path** — add `SET LOCAL statement_timeout = '3s'` to `validate_api_token` / `validate_session` in [services/auth_service.py](services/auth_service.py). Pure code change; ship via `kamal deploy` (zero-downtime). Do this first — it's the safety net that limits future auth-table-lock blast radius.
- [ ] **(2) Promote MCP from accessory to Kamal service role** — config already staged in [config/deploy.yml](config/deploy.yml). Run `kamal deploy -r mcp`, verify `curl -sI https://mcp.neodemos.nl/mcp` returns HTTP/2 401, then SSH-remove the orphaned `neodemos-mcp` accessory container. After this, MCP deploys are blue-green zero-downtime (matches web service).

---

## v0.2.0 workstreams (target 2026-04-24)

*Dashboard view only. Feature tasks live in the handoff docs — do NOT duplicate them here.*

| # | Workstream | Handoff | Status |
|---|---|---|---|
| WS1 | GraphRAG retrieval | [WS1_GRAPHRAG.md](docs/handoffs/WS1_GRAPHRAG.md) | not started |
| WS2 | Trustworthy financial analysis | [WS2_FINANCIAL.md](docs/handoffs/done/WS2_FINANCIAL.md) | not started |
| WS3 | Document journey timelines | [WS3_JOURNEY.md](docs/handoffs/WS3_JOURNEY.md) | not started |
| WS4 | Best-in-class MCP surface | [WS4_MCP_DISCIPLINE.md](docs/handoffs/WS4_MCP_DISCIPLINE.md) | not started |
| WS5a | Nightly pipeline | [WS5a_NIGHTLY_PIPELINE.md](docs/handoffs/WS5a_NIGHTLY_PIPELINE.md) | not started |
| WS6 | Source-spans summarization | [WS6_SUMMARIZATION.md](docs/handoffs/WS6_SUMMARIZATION.md) | not started |

---

## Parked (re-eval on listed trigger)

- [ ] **WS5b — Multi-portal connectors (search-only first)** — re-eval on v0.2.0 ship; moves active in v0.2.1. Handoff: [WS5b_MULTI_PORTAL.md](docs/handoffs/WS5b_MULTI_PORTAL.md)
- [ ] **Audio transcript pipeline** (notulen → annotaties for commissievergaderingen) — re-eval at v0.2.1 ship

---

## Triage log

*Items recently moved out of this file. Keeps the audit trail without the clutter.*

| Date | Item | Moved to | Reason |
|---|---|---|---|
| 2026-04-11 | `zoek_moties` title-only bug for single-word queries | [WS4 §MCP tool bug fixes](docs/handoffs/WS4_MCP_DISCIPLINE.md) | MCP tool quality fix — natural WS4 home |
| 2026-04-11 | RAG BM25 `%notule%` fallback filter | [WS1 §Phase B](docs/handoffs/WS1_GRAPHRAG.md) | `services/rag_service.py` bug; WS1 already edits that file |
| 2026-04-11 | Overview query latency (`zoek_moties` → `lees_fragment` sequential) | [WS4 §MCP tool bug fixes](docs/handoffs/WS4_MCP_DISCIPLINE.md) | Tool API design (preview chars, batch tool) |
| 2026-04-11 | IV3 / taakvelden canonical aggregation layer | [WS2 §IV3 canonical aggregation](docs/handoffs/done/WS2_FINANCIAL.md) | Explicit WS2 prerequisite — financial schema concern |
| 2026-04-11 | Chunk → `document_id` attribution audit (from FEEDBACK_LOG) | [WS5a §Data integrity audit](docs/handoffs/WS5a_NIGHTLY_PIPELINE.md) | Ingest integrity — pipeline reliability concern |
| 2026-04-11 | `lees_fragment(query=...)` re-ranking (from FEEDBACK_LOG) | [WS4 §Tool API improvements](docs/handoffs/WS4_MCP_DISCIPLINE.md) | MCP tool API change |
| 2026-04-11 | `corpus_coverage` metadata on search tool responses (from FEEDBACK_LOG) | [WS4 §Tool API improvements](docs/handoffs/WS4_MCP_DISCIPLINE.md) | Output schema change (depends on WS5a coverage data) |
| 2026-04-11 | Snippet provenance verification (from FEEDBACK_LOG) | [WS4 §Defense-in-depth Layer 4](docs/handoffs/WS4_MCP_DISCIPLINE.md) | Fits naturally in the output filter |

---

_Add new items at the top of the **Triage inbox**, not inside sections they don't belong in. Move to handoff docs once a WS is assigned, then log the move in the Triage log above._
