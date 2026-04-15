# NeoDemos Project State

> Auto-generated from `.coordination/events.jsonl` — do not edit manually.
> Last rebuilt: 2026-04-15T05:46:18Z

## Active Now

| WS   | Title                                              | Claimed by | Since      | Detail                                                       |
|------|----------------------------------------------------|------------|------------|--------------------------------------------------------------|
| WS11 | Corpus completeness 2018-2026 (ORI gap + metadata) | dennistak  | 2026-04-14 | claimed via /ws-claim — resuming phase 6 (where we left off) |
| WS16 | MCP monitoring & observability                     | Dennis     | 2026-04-14 | Initial seed: Phase 1 shipped 2026-04-14                     |
| WS8f | Admin panel + CMS + GrapeJS editor                 | dennistak  | 2026-04-14 | Phase 7 — rejection follow-up: page creation + asset uploads |
| WS5a | 100% reliable nightly ingest                       | dennistak  | 2026-04-14 | claimed via /ws-claim                                        |
| WS6  | Source-spans-only summarization                    | seed       | 2026-04-14 | Phase 3 DB write running; mode='structured' needs WS1        |

## Blocked

| WS   | Title                                                              | Waiting on | Unblocks when              |
|------|--------------------------------------------------------------------|------------|----------------------------|
| WS1  | GraphRAG retrieval (Flair NER + Gemini enrichment + VN provenance) | WS11       | when all blockers complete |
| WS13 | Multi-gemeente pipeline: tenant-aware ingestion                    | WS5a       | when all blockers complete |
| WS14 | Calendar quality & bijlage reconciliation                          | WS8f       | when all blockers complete |
| WS3  | Document journey timelines                                         | WS1        | when all blockers complete |
| WS5b | Multi-portal connectors (search-only)                              | WS5a       | when all blockers complete |

## Available (unclaimed, no blockers)

| WS   | Title                                                       | Dependencies |
|------|-------------------------------------------------------------|--------------|
| WS15 | Per-party voting data (motie_stemmen + zoek_stemgedrag)     | none         |
| WS2b | IV3 taakveld FK backfill                                    | WS2          |
| WS17 | Production feedback loop (detect → digest → close-the-loop) | WS4          |

## Paused

| WS   | Title                                           | Detail                                                                           |
|------|-------------------------------------------------|----------------------------------------------------------------------------------|
| WS12 | Virtual notulen backfill & production hardening | Deferred to v0.3/v0.4: Erik Verweij (only user so far) confirmed virtual notulen |
| WS10 | Table-rich document extraction (Docling layout) | Infrastructure done; targeted 20-doc run only                                    |

## Escalated

| WS | Title | Detail |
|---|---|---|
| *(none)* | | |

## Recently Completed (last 14 days)

| WS  | Title                                                  | Completed  | Worker    |
|-----|--------------------------------------------------------|------------|-----------|
| WS7 | OCR recovery for moties/amendementen                   | 2026-04-14 | seed      |
| WS4 | Best-in-class MCP surface                              | 2026-04-14 | dennistak |
| WS9 | Web intelligence: MCP-as-backend, Sonnet tool_use, SSE | 2026-04-14 | dennistak |
| WS2 | Trustworthy financial analysis                         | 2026-04-12 | seed      |
| WS8 | Frontend redesign: design system, landing, calendar    | 2026-04-12 | seed      |

## Recent events (last 15)

```jsonl
{"agent": "seed", "detail": "Initial seed: not_started, v0.2.1 scope", "event": "note", "ts": "2026-04-14T19:27:49Z", "ws": "WS17"}
{"agent": "mcp_alert", "detail": "0 calls in 15min (was averaging 65/hr)", "event": "alert", "metrics": {"calls_15m": 0, "prior_60m_calls": 65}, "rule": "silent", "ts": "2026-04-14T19:28:49Z"}
{"agent": "mcp_alert", "detail": "p95 60.9s last 5min", "event": "alert", "metrics": {"p95_latency_ms": 60905.4}, "rule": "latency", "ts": "2026-04-14T19:33:51Z"}
{"agent": "dennistak", "detail": "claimed via /ws-claim — resuming phase 6 (where we left off)", "event": "claimed", "ts": "2026-04-14T19:34:12Z", "ws": "WS11"}
{"agent": "Dennis", "commit": "9b40f75", "detail": "OCR recovery shipped 2026-04-14; follow-ups (re-embed, bm25_miss, large docs) folded into WS11", "event": "completed", "ts": "2026-04-14T19:39:51Z", "ws": "WS7"}
{"agent": "Dennis", "event": "qa_rejected", "reason": "Visual editor lacks page-creation affordance; overall editing feels thin. Considering full CMS migration for v0.2.0 — proper templates + visual editor with our tailor-made components as CMS-managed elements. Needs thorough research first (Payload/Strapi/Directus/etc.). WS8f stays in_progress pending that direction decision.", "ts": "2026-04-14T19:49:33Z", "ws": "WS8f"}
{"agent": "mcp_alert", "detail": "0 calls in 15min (was averaging 46/hr)", "event": "alert", "metrics": {"calls_15m": 0, "prior_60m_calls": 46}, "rule": "silent", "ts": "2026-04-14T20:04:04Z"}
{"agent": "mcp_alert", "detail": "p95 16.9s last 5min", "event": "alert", "metrics": {"p95_latency_ms": 16854.7}, "rule": "latency", "ts": "2026-04-14T20:09:06Z"}
{"agent": "Dennis", "detail": "Deferred to v0.3/v0.4: Erik Verweij (only user so far) confirmed virtual notulen is a nice-to-have. Phase 1+4 (2025+2026) are done and live. Phase 2+3 (server infra + 2018-2024 backfill) moved to backlog. WS1 dependency on WS12 removed — VN provenance will be 2025+2026-only until resumed.", "event": "paused", "ts": "2026-04-14T20:09:13Z", "ws": "WS12"}
{"agent": "mcp_alert", "detail": "10 calls (4.4x baseline)", "event": "alert", "metrics": {"baseline_5m": 2.25, "calls_5m": 10, "ratio": 4.44}, "rule": "traffic_spike", "ts": "2026-04-14T20:14:08Z"}
{"agent": "mcp_alert", "detail": "0 calls in 15min (was averaging 53/hr)", "event": "alert", "metrics": {"calls_15m": 0, "prior_60m_calls": 53}, "rule": "silent", "ts": "2026-04-14T20:44:22Z"}
{"agent": "dennistak", "detail": "claimed via /ws-claim", "event": "claimed", "ts": "2026-04-14T20:48:56Z", "ws": "WS5a"}
{"agent": "mcp_alert", "detail": "0 calls in 15min (was averaging 36/hr)", "event": "alert", "metrics": {"calls_15m": 0, "prior_60m_calls": 36}, "rule": "silent", "ts": "2026-04-14T21:14:35Z"}
{"agent": "dennistak", "detail": "Phase 7 — rejection follow-up: page creation + asset uploads + deeper editor traits + axe-core", "event": "claimed", "ts": "2026-04-14T21:14:58Z", "ws": "WS8f"}
{"agent": "mcp_alert", "detail": "p95 65.1s last 5min", "event": "alert", "metrics": {"p95_latency_ms": 65092.0}, "rule": "latency", "ts": "2026-04-14T21:19:37Z"}
```
