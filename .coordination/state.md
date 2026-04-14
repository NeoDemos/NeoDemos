# NeoDemos Project State

> Auto-generated from `.coordination/events.jsonl` — do not edit manually.
> Last rebuilt: 2026-04-14T15:31:44Z

## Active Now

| WS   | Title                                              | Claimed by | Since      | Detail                                                 |
|------|----------------------------------------------------|------------|------------|--------------------------------------------------------|
| WS11 | Corpus completeness 2018-2026 (ORI gap + metadata) | seed       | 2026-04-14 |                                                        |
| WS12 | Virtual notulen backfill & production hardening    | seed       | 2026-04-14 |                                                        |
| WS8f | Admin panel + CMS + GrapeJS editor                 | seed       | 2026-04-14 | Shipped 2026-04-13, pending Dennis QA via /ws-complete |
| WS7  | OCR recovery for moties/amendementen               | seed       | 2026-04-14 |                                                        |
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

| WS   | Title                        | Dependencies |
|------|------------------------------|--------------|
| WS2b | IV3 taakveld FK backfill     | WS2          |
| WS5a | 100% reliable nightly ingest | none         |

## Paused

| WS   | Title                                           | Detail                                        |
|------|-------------------------------------------------|-----------------------------------------------|
| WS10 | Table-rich document extraction (Docling layout) | Infrastructure done; targeted 20-doc run only |

## Escalated

| WS | Title | Detail |
|---|---|---|
| *(none)* | | |

## Recently Completed (last 14 days)

| WS  | Title                                                  | Completed  | Worker |
|-----|--------------------------------------------------------|------------|--------|
| WS4 | Best-in-class MCP surface                              | 2026-04-13 | seed   |
| WS9 | Web intelligence: MCP-as-backend, Sonnet tool_use, SSE | 2026-04-13 | seed   |
| WS2 | Trustworthy financial analysis                         | 2026-04-12 | seed   |
| WS8 | Frontend redesign: design system, landing, calendar    | 2026-04-12 | seed   |

## Recent events (last 15)

```jsonl
{"agent": "seed", "event": "claimed", "ts": "2026-04-12T00:00:00Z", "ws": "WS8"}
{"agent": "seed", "event": "completed", "ts": "2026-04-12T00:00:00Z", "ws": "WS8"}
{"agent": "seed", "detail": "2 reliability follow-ups opened 2026-04-14", "event": "claimed", "ts": "2026-04-13T00:00:00Z", "ws": "WS4"}
{"agent": "seed", "detail": "2 reliability follow-ups opened 2026-04-14", "event": "completed", "ts": "2026-04-13T00:00:00Z", "ws": "WS4"}
{"agent": "seed", "event": "claimed", "ts": "2026-04-13T00:00:00Z", "ws": "WS9"}
{"agent": "seed", "event": "completed", "ts": "2026-04-13T00:00:00Z", "ws": "WS9"}
{"agent": "seed", "blocker": ["WS7", "WS11", "WS12"], "event": "blocked", "ts": "2026-04-14T15:31:44Z", "ws": "WS1"}
{"agent": "seed", "detail": "Infrastructure done; targeted 20-doc run only", "event": "claimed", "ts": "2026-04-14T15:31:44Z", "ws": "WS10"}
{"agent": "seed", "detail": "Infrastructure done; targeted 20-doc run only", "event": "paused", "ts": "2026-04-14T15:31:44Z", "ws": "WS10"}
{"agent": "seed", "event": "claimed", "ts": "2026-04-14T15:31:44Z", "ws": "WS11"}
{"agent": "seed", "event": "claimed", "ts": "2026-04-14T15:31:44Z", "ws": "WS12"}
{"agent": "seed", "detail": "status=deferred. Deferred to v0.2.1", "event": "note", "ts": "2026-04-14T15:31:44Z", "ws": "WS5b"}
{"agent": "seed", "detail": "Phase 3 DB write running; mode='structured' needs WS1", "event": "claimed", "ts": "2026-04-14T15:31:44Z", "ws": "WS6"}
{"agent": "seed", "event": "claimed", "ts": "2026-04-14T15:31:44Z", "ws": "WS7"}
{"agent": "seed", "detail": "Shipped 2026-04-13, pending Dennis QA via /ws-complete", "event": "claimed", "ts": "2026-04-14T15:31:44Z", "ws": "WS8f"}
```
