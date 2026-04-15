# NeoDemos Project State

> Auto-generated from `.coordination/events.jsonl` — do not edit manually.
> Last rebuilt: 2026-04-15T12:36:49Z

## Active Now

| WS   | Title                              | Claimed by | Since      | Detail                                                       |
|------|------------------------------------|------------|------------|--------------------------------------------------------------|
| WS16 | MCP monitoring & observability     | Dennis     | 2026-04-14 | Initial seed: Phase 1 shipped 2026-04-14                     |
| WS8f | Admin panel + CMS + GrapeJS editor | dennistak  | 2026-04-15 | Phase 8 shipped 2026-04-15: nd-answer + nd-analyse as Shadow |
| WS5a | 100% reliable nightly ingest       | dennistak  | 2026-04-14 | claimed via /ws-claim                                        |
| WS6  | Source-spans-only summarization    | seed       | 2026-04-14 | Phase 3 DB write running; mode='structured' needs WS1        |

## Blocked

| WS   | Title                                           | Waiting on | Unblocks when              |
|------|-------------------------------------------------|------------|----------------------------|
| WS13 | Multi-gemeente pipeline: tenant-aware ingestion | WS5a       | when all blockers complete |
| WS14 | Calendar quality & bijlage reconciliation       | WS8f       | when all blockers complete |
| WS3  | Document journey timelines                      | WS1        | when all blockers complete |
| WS5b | Multi-portal connectors (search-only)           | WS5a       | when all blockers complete |

## Available (unclaimed, no blockers)

| WS   | Title                                                              | Dependencies |
|------|--------------------------------------------------------------------|--------------|
| WS1  | GraphRAG retrieval (Flair NER + Gemini enrichment + VN provenance) | WS7, WS11    |
| WS15 | Per-party voting data (motie_stemmen + zoek_stemgedrag)            | none         |
| WS2b | IV3 taakveld FK backfill                                           | WS2          |
| WS17 | Production feedback loop (detect → digest → close-the-loop)        | WS4          |

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

| WS   | Title                                                  | Completed  | Worker    |
|------|--------------------------------------------------------|------------|-----------|
| WS11 | Corpus completeness 2018-2026 (ORI gap + metadata)     | 2026-04-15 | dennistak |
| WS7  | OCR recovery for moties/amendementen                   | 2026-04-14 | seed      |
| WS4  | Best-in-class MCP surface                              | 2026-04-14 | dennistak |
| WS9  | Web intelligence: MCP-as-backend, Sonnet tool_use, SSE | 2026-04-14 | dennistak |
| WS2  | Trustworthy financial analysis                         | 2026-04-12 | seed      |
| WS8  | Frontend redesign: design system, landing, calendar    | 2026-04-12 | seed      |

## Recent events (last 15)

```jsonl
{"agent": "dennistak", "detail": "Plan only — handoff seeded 2026-04-15; execution blocked on WS8f QA + WS14 Phase D/F", "event": "claimed", "ts": "2026-04-15T08:51:11Z", "ws": "WS8g"}
{"agent": "mcp_alert", "detail": "p95 37.8s last 5min", "event": "alert", "metrics": {"p95_latency_ms": 37809.0}, "rule": "latency", "ts": "2026-04-15T09:14:55Z"}
{"agent": "mcp_alert", "detail": "0 calls in 15min (was averaging 36/hr)", "event": "alert", "metrics": {"calls_15m": 0, "prior_60m_calls": 36}, "rule": "silent", "ts": "2026-04-15T09:35:04Z"}
{"agent": "dennistak", "detail": "Round 5 feedback shipped 2026-04-15: instellingen !important fix, logo bumped 3xl, Gemini favicon (6 sizes + ICO), /abonnement editor-editable, landing long-scroll (5 Oatmeal-inspired sections), Profiel avatar picker + tier switch (mig 0013 pending), /abonnement 2-tier rewrite + FAQ, tool_rounds 5->8 with synthesis fallback, cost tracking (mig 0012 pending), WS14 Phase A audit doc + C1 DISTINCT + C6 calendar_labels.py.", "event": "note", "ts": "2026-04-15T09:56:24Z", "ws": "WS8f"}
{"agent": "mcp_alert", "detail": "p95 52.2s last 5min", "event": "alert", "metrics": {"p95_latency_ms": 52169.4}, "rule": "latency", "ts": "2026-04-15T10:00:16Z"}
{"agent": "mcp_alert", "detail": "9 calls (15.4x baseline)", "event": "alert", "metrics": {"baseline_5m": 0.58, "calls_5m": 9, "ratio": 15.43}, "rule": "traffic_spike", "ts": "2026-04-15T10:00:16Z"}
{"agent": "dennistak", "detail": "claimed via /ws-claim", "event": "claimed", "ts": "2026-04-15T10:18:55Z", "ws": "WS8f"}
{"agent": "mcp_alert", "detail": "0 calls in 15min (was averaging 33/hr)", "event": "alert", "metrics": {"calls_15m": 0, "prior_60m_calls": 33}, "rule": "silent", "ts": "2026-04-15T10:35:33Z"}
{"agent": "mcp_alert", "detail": "0 calls in 15min (was averaging 36/hr)", "event": "alert", "metrics": {"calls_15m": 0, "prior_60m_calls": 36}, "rule": "silent", "ts": "2026-04-15T11:05:47Z"}
{"agent": "mcp_alert", "detail": "7 calls (14.0x baseline)", "event": "alert", "metrics": {"baseline_5m": 0.5, "calls_5m": 7, "ratio": 14.0}, "rule": "traffic_spike", "ts": "2026-04-15T11:15:52Z"}
{"agent": "mcp_alert", "detail": "0 calls in 15min (was averaging 13/hr)", "event": "alert", "metrics": {"calls_15m": 0, "prior_60m_calls": 13}, "rule": "silent", "ts": "2026-04-15T11:46:06Z"}
{"agent": "dennistak", "detail": "WS14 Phase A (audit baseline with real numbers), Phase B scripts (B1/B2 dry-run verified, B4/B7 migrations created), Phase C (C2-C5 + C6 wired), Phase D (D1-D8), Phase F (F1-F3) all shipped 2026-04-15. Awaiting Dennis approval to run B1/B2 live + apply migrations 0014/0015.", "event": "note", "ts": "2026-04-15T12:09:25Z", "ws": "WS14"}
{"agent": "dennistak", "detail": "Phase 8 shipped 2026-04-15: nd-answer + nd-analyse as Shadow DOM Web Components, GrapesJS blocks registered. WS8f carry-overs complete: subpage 48rem→64rem, Phase 8 Web Components. Pending: migration 0009 + 0013 apply, Dennis sign-off on WS8g + pricing.", "event": "note", "ts": "2026-04-15T12:09:28Z", "ws": "WS8f"}
{"agent": "mcp_alert", "detail": "0 calls in 15min (was averaging 10/hr)", "event": "alert", "metrics": {"calls_15m": 0, "prior_60m_calls": 10}, "rule": "silent", "ts": "2026-04-15T12:16:20Z"}
{"agent": "dennistak", "detail": "LIVE: B1 backfill (2117 junction rows), B2 dedupe (3781 rows deleted), WS11 classifier re-run (571 docs), WS14 B3 bijlage fallback (3117 docs). Post-audit: A2=0, A3=0 for 2023-2026, A8 NULL rate cut 93% (14%→0.4% for 2023). Migrations 0014/0015 still deferred (pg_dump + audit holding shared locks). B5 dry-run: 236 groups, 262 losers, 1432 docs to reparent — awaiting Dennis approval for live.", "event": "note", "ts": "2026-04-15T12:36:49Z", "ws": "WS14"}
```
