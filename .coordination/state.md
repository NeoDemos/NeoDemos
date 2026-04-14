# NeoDemos Project State

> Auto-generated from `.coordination/events.jsonl` — do not edit manually.
> Last rebuilt: 2026-04-14T19:26:34Z

## Active Now

| WS   | Title                                              | Claimed by | Since      | Detail                                                 |
|------|----------------------------------------------------|------------|------------|--------------------------------------------------------|
| WS11 | Corpus completeness 2018-2026 (ORI gap + metadata) | seed       | 2026-04-14 |                                                        |
| WS12 | Virtual notulen backfill & production hardening    | seed       | 2026-04-14 |                                                        |
| WS8f | Admin panel + CMS + GrapeJS editor                 | seed       | 2026-04-14 | Shipped 2026-04-13, pending Dennis QA via /ws-complete |
| WS7  | OCR recovery for moties/amendementen               | seed       | 2026-04-14 |                                                        |
| WS5a | 100% reliable nightly ingest                       | dennistak  | 2026-04-14 | claimed via /ws-claim                                  |
| WS6  | Source-spans-only summarization                    | seed       | 2026-04-14 | Phase 3 DB write running; mode='structured' needs WS1  |

## Blocked

| WS   | Title                                                              | Waiting on      | Unblocks when              |
|------|--------------------------------------------------------------------|-----------------|----------------------------|
| WS1  | GraphRAG retrieval (Flair NER + Gemini enrichment + VN provenance) | WS7, WS11, WS12 | when all blockers complete |
| WS13 | Multi-gemeente pipeline: tenant-aware ingestion                    | WS5a            | when all blockers complete |
| WS14 | Calendar quality & bijlage reconciliation                          | WS8f            | when all blockers complete |
| WS3  | Document journey timelines                                         | WS1             | when all blockers complete |
| WS5b | Multi-portal connectors (search-only)                              | WS5a            | when all blockers complete |

## Available (unclaimed, no blockers)

| WS   | Title                                                       | Dependencies |
|------|-------------------------------------------------------------|--------------|
| WS15 | Per-party voting data (motie_stemmen + zoek_stemgedrag)     | none         |
| WS16 | MCP monitoring & observability                              | WS4          |
| WS2b | IV3 taakveld FK backfill                                    | WS2          |
| WS17 | Production feedback loop (detect → digest → close-the-loop) | WS4          |

## Paused

| WS   | Title                                           | Detail                                        |
|------|-------------------------------------------------|-----------------------------------------------|
| WS10 | Table-rich document extraction (Docling layout) | Infrastructure done; targeted 20-doc run only |

## Escalated

| WS | Title | Detail |
|---|---|---|
| *(none)* | | |

## Recently Completed (last 14 days)

| WS  | Title                                                  | Completed  | Worker    |
|-----|--------------------------------------------------------|------------|-----------|
| WS4 | Best-in-class MCP surface                              | 2026-04-14 | dennistak |
| WS9 | Web intelligence: MCP-as-backend, Sonnet tool_use, SSE | 2026-04-14 | dennistak |
| WS2 | Trustworthy financial analysis                         | 2026-04-12 | seed      |
| WS8 | Frontend redesign: design system, landing, calendar    | 2026-04-12 | seed      |

## Recent events (last 15)

```jsonl
{"agent": "seed", "detail": "Infrastructure done; targeted 20-doc run only", "event": "claimed", "ts": "2026-04-14T15:31:44Z", "ws": "WS10"}
{"agent": "seed", "detail": "Infrastructure done; targeted 20-doc run only", "event": "paused", "ts": "2026-04-14T15:31:44Z", "ws": "WS10"}
{"agent": "seed", "event": "claimed", "ts": "2026-04-14T15:31:44Z", "ws": "WS11"}
{"agent": "seed", "event": "claimed", "ts": "2026-04-14T15:31:44Z", "ws": "WS12"}
{"agent": "seed", "detail": "status=deferred. Deferred to v0.2.1", "event": "note", "ts": "2026-04-14T15:31:44Z", "ws": "WS5b"}
{"agent": "seed", "detail": "Phase 3 DB write running; mode='structured' needs WS1", "event": "claimed", "ts": "2026-04-14T15:31:44Z", "ws": "WS6"}
{"agent": "seed", "event": "claimed", "ts": "2026-04-14T15:31:44Z", "ws": "WS7"}
{"agent": "seed", "detail": "Shipped 2026-04-13, pending Dennis QA via /ws-complete", "event": "claimed", "ts": "2026-04-14T15:31:44Z", "ws": "WS8f"}
{"agent": "dennistak", "detail": "claimed via /ws-claim", "event": "claimed", "ts": "2026-04-14T18:30:57Z", "ws": "WS4"}
{"agent": "dennistak", "detail": "claimed via /ws-claim", "event": "claimed", "ts": "2026-04-14T18:31:41Z", "ws": "WS9"}
{"agent": "dennistak", "detail": "alembic 0005b rename done; Phase 4 eval deferred to Dennis", "event": "completed", "ts": "2026-04-14T18:35:34Z", "ws": "WS9"}
{"agent": "mcp_alert", "detail": "p95 61.1s last 5min", "event": "alert", "metrics": {"p95_latency_ms": 61144.5}, "rule": "latency", "ts": "2026-04-14T18:43:30Z"}
{"agent": "mcp_alert", "detail": "20 calls (20.0x baseline)", "event": "alert", "metrics": {"baseline_5m": 1.0, "calls_5m": 20, "ratio": 20.0}, "rule": "traffic_spike", "ts": "2026-04-14T18:48:32Z"}
{"agent": "Dennis", "commit": "58caebc", "detail": "Statement timeout + T1-T10 tool quality fixes + MCP service-role migration + requirements pin", "event": "completed", "ts": "2026-04-14T19:14:27Z", "ws": "WS4"}
{"agent": "dennistak", "detail": "claimed via /ws-claim", "event": "claimed", "ts": "2026-04-14T19:20:01Z", "ws": "WS5a"}
```
