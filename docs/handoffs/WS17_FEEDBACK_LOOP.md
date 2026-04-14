# WS17 — Production → Feedback → Triage → Fix → Verify Loop

> **Priority:** 4 (post-press-moment quality infrastructure; not a launch blocker)
> **Status:** `not started` — v0.2.1 scope, approved 2026-04-14
> **Owner:** `unassigned`
> **Target release:** v0.2.1
> **Last updated:** 2026-04-14

---

## TL;DR

Today Dennis's quality gate is `brain/FEEDBACK_LOG.md` plus a weekly Monday triage ritual. The ritual is the *right* gate — memory [`feedback_eval_quality_audit.md`](../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/feedback_eval_quality_audit.md) explicitly rejects LLM-as-judge in favour of real MCP chat replay — but it is entirely manual, and at >100 queries/day the friction breaks the loop. WS17 automates **signal capture** (what went wrong?) and **close-the-loop tracking** (did the fix stick?) while keeping **triage** and **scoring** strictly manual. Deliverables: schema instrumentation on `mcp_audit_log`, a `detect.py` candidate-generation script, a Sunday-night `digest.py` that produces `MONDAY_DIGEST.md`, a new `feedback` event type in `events.jsonl`, a `/ws-retest` command, and a warn-not-block hook in `/ws-complete` for WSs with unretested feedback items.

---

## Design principle (LOAD-BEARING)

**Two functions, two automation levels. Do not blur the line.**

| Function | Automate? | Why |
|---|---|---|
| **Signal capture** — zero citations, retries, errors, latency spikes | **Yes, aggressively** | Mechanical. LLM-free. Cheap to run nightly. |
| **Triage** — which WS does this belong to? is it a real bug or user error? what's the political / product context? | **No. Stays manual.** | Requires Dennis's judgement as a raadslid. LLM-as-judge on session quality is **explicitly rejected** (see memory `feedback_eval_quality_audit.md`). |

Every design choice in this handoff falls out of that split. If an agent ever proposes "just have Claude score each session 1–5," that is the principle being violated — reject it.

---

## Dependencies

| Dependency | Type | Notes |
|---|---|---|
| WS4 shipped ✅ | hard | Uses `mcp_audit_log` table + `services/audit_logger.py` as the raw signal source |
| WS16 (monitoring) | soft | Schema migration pattern + script conventions established there — reuse them |
| Phase 0 archival of `brain/FEEDBACK_LOG.md` → `.coordination/FEEDBACK_LOG.md` | hard | Digest reads the canonical path; must be moved before `digest.py` runs |
| `scripts/coord/append_event.py` | reuse | Do **not** fork; extend with a new `event=feedback` case |
| Memory: [`feedback_eval_quality_audit.md`](../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/feedback_eval_quality_audit.md) | read first | The "why no LLM-judge" decision |

---

## Cold-start prompt

```
You are picking up WS17_FEEDBACK_LOOP for NeoDemos — civic transparency platform
for Rotterdam raad (and Middelburg v0.2.1). This workstream automates the
production → feedback → triage → fix → verify loop that today runs entirely
through brain/FEEDBACK_LOG.md + Dennis's Monday ritual.

LOAD-BEARING DESIGN PRINCIPLE (do not violate):
  Two functions, two automation levels.
  1. Signal capture (what went wrong?) — automate aggressively.
  2. Triage (which WS? real bug? political context?) — STAYS MANUAL.
  LLM-as-judge on session quality is EXPLICITLY REJECTED. Mechanical signals
  only: citations present? structured data? latency OK? Never "was the answer
  good?". See memory feedback_eval_quality_audit.md.

Additional non-negotiable constraints:
  - The weekly candidates file is Dennis-only. Never read by agents in any
    other workstream. Add an explicit rule under .agent/rules/.
  - In-product thumbs rating is DEFERRED to v0.4+. Do not build it now.
  - /ws-complete WARNS, does NOT BLOCK, when a WS has unretested feedback
    items. Gates you override defeat the gate.
  - Back-references between FEEDBACK_LOG and events.jsonl use WS IDs, never
    file paths — archived handoffs move paths, IDs are stable.
  - Piggyback on the coordination layer (events.jsonl + scripts/coord/) —
    do NOT build a parallel tracking system.

Read these files first:
  - docs/handoffs/WS17_FEEDBACK_LOOP.md (this file)
  - docs/handoffs/WS4_MCP_DISCIPLINE.md (audit log owner)
  - .coordination/FEEDBACK_LOG.md (post-Phase 0 move from brain/)
  - services/audit_logger.py (current schema + write path)
  - scripts/coord/append_event.py (extend, don't fork)
  - scripts/coord/rebuild_state.py (state.md regenerator — add open-feedback column)
  - .claude/commands/ws-complete.md + ws-status.md (add feedback warn + count)

Build in four phases (see Build tasks below): Phase 1 schema +
instrumentation, Phase 2 detect.py, Phase 3 digest.py + MONDAY_DIGEST.md,
Phase 4 close-the-loop (events.jsonl feedback type, /ws-retest, /ws-complete
warn, /ws-status feedback column). Ship phases independently; each is a
small, reversible PR.

SSH tunnel required for any migration: ./scripts/dev_tunnel.sh --bg.
Coordinate DB writes via pg_advisory_lock(42).
```

---

## Files to read first

| File | Why |
|---|---|
| [docs/handoffs/WS4_MCP_DISCIPLINE.md](WS4_MCP_DISCIPLINE.md) | Audit log schema owner; WS4 §Post-ship is the pattern for add-on instrumentation |
| `.coordination/FEEDBACK_LOG.md` (post-move) | Current triage inbox; canonical format for entries the digest consumes |
| [services/audit_logger.py](../../services/audit_logger.py) | Current `mcp_audit_log` writer — add new columns here |
| [scripts/coord/append_event.py](../../scripts/coord/append_event.py) | Reuse helper; extend `--event` enum with `feedback` |
| [scripts/coord/rebuild_state.py](../../scripts/coord/rebuild_state.py) | State.md regenerator — add per-WS open-feedback column |
| [.claude/commands/ws-complete.md](../../.claude/commands/ws-complete.md) | Add warn-not-block feedback check |
| [.claude/commands/ws-status.md](../../.claude/commands/ws-status.md) | Add feedback-count column |
| Memory: [`feedback_eval_quality_audit.md`](../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/feedback_eval_quality_audit.md) | The "no LLM-judge" decision — re-read before every heuristic proposal |

---

## Build tasks

### Phase 1 — Schema + instrumentation

**Goal:** enrich `mcp_audit_log` with the four fields `detect.py` needs. One migration, two code paths (MCP server, web frontend), no behaviour change for users.

- [ ] **Alembic migration** `migrations/versions/00XX_mcp_audit_log_feedback_fields.py`:
  ```sql
  ALTER TABLE mcp_audit_log
    ADD COLUMN session_id UUID,
    ADD COLUMN citation_count INT,
    ADD COLUMN zero_result BOOL DEFAULT FALSE,
    ADD COLUMN client_disconnected BOOL DEFAULT FALSE;
  CREATE INDEX idx_mcp_audit_log_session_id ON mcp_audit_log(session_id);
  CREATE INDEX idx_mcp_audit_log_created_at ON mcp_audit_log(created_at DESC);
  ```
  Use `NULLS NOT DISTINCT` semantics where relevant. Must run under `pg_advisory_lock(42)`. Must obey the `feedback_mcp_uptime.md` rule: **no exclusive locks on `users` / `sessions` / `api_tokens`** — this migration only touches `mcp_audit_log`, which is safe, but the agent must confirm no blocking queries are holding the table before running.

- [ ] **`services/audit_logger.py`** — extend the writer signature. `session_id` comes from the MCP OAuth session on the server side and from a cookie on the web side. `citation_count` is the length of the `sources[]` array returned by the tool. `zero_result` is `citation_count == 0` for tools whose `result_type` is in `{search, retrieve}`. `client_disconnected` is set via `asyncio.CancelledError` handler in the request lifecycle.

- [ ] **MCP server** (`mcp_server_v3.py`) — thread `session_id` through every `@mcp.tool()` call; populate on audit write.

- [ ] **Web frontend** (`routes/search.py` or `routes/api.py`, wherever `/api/search/stream` lives) — same four fields; reuse the same writer.

- [ ] **Backfill policy:** new columns NULLable; no historical backfill. Detection script only looks at `created_at >= NOW() - interval '24 hours'`.

### Phase 2 — Detection (`scripts/feedback/detect.py`)

**Goal:** mechanical signal capture. No LLM. Output is a pre-triage firehose, **not** a FEEDBACK_LOG entry.

- [ ] **New dir** `scripts/feedback/` — mirrors `scripts/coord/` convention.

- [ ] **`scripts/feedback/detect.py`** — CLI:
  ```
  python scripts/feedback/detect.py [--since 24h] [--out .coordination/inbox/]
  ```
  Reads last N hours of `mcp_audit_log` **and** `logs/mcp_queries.jsonl` (if present). Scores sessions on these four heuristics — **start with one (H1) and measure signal ratio for 2 weeks before enabling the others**:

  | ID | Rule | Threshold |
  |---|---|---|
  | H1 | Zero-citation answer on a non-trivial query | `zero_result = true AND LENGTH(query) > 20 AND tool IN ('zoek_*', 'vraag_*')` |
  | H2 | Same query retried > 2× within 5 min (frustration) | group by `session_id`, same or near-same (Levenshtein ≤ 3) query text |
  | H3 | Session ended after an error | last audit row in session had `status != 'ok'` |
  | H4 | Latency > 10s on any tool call | `latency_ms > 10000` |

  **Output format:** `.coordination/inbox/feedback_candidates_<YYYY-WW>.md` (per ISO week, append-only within a week, new file each Monday). Each candidate is a markdown block:
  ```
  ## 2026-04-15 14:32 — H1 (zero-citation on substantive query)
  - session_id: 8f3a...
  - user: <redacted-user-id>
  - query: "hoeveel heeft rotterdam uitgegeven aan..."
  - tool: zoek_financieel
  - latency_ms: 1840
  - candidate_ws: UNASSIGNED  ← Dennis fills in
  - promote: [ ]               ← Dennis ticks to move to FEEDBACK_LOG
  ```

- [ ] **Rule file** `.agent/rules/feedback_candidates_are_dennis_only.md`:
  > Files under `.coordination/inbox/feedback_candidates_*.md` are **Dennis's pre-triage firehose**. Agents must NOT read, grep, summarise, or act on these files. Only `.coordination/FEEDBACK_LOG.md` (post-promotion) is in-scope for agents.

- [ ] **Cron/launchd** — nightly at 03:00 local. Register via existing launchd pattern (see `.claude/commands/` for pattern). Do not use `CronCreate` (that's for scheduled agents, not shell jobs).

### Phase 3 — Monday digest (`scripts/feedback/digest.py`)

**Goal:** the one thing Dennis reads on Monday morning. Under 1 page. Single file, lands at a fixed path, overwrites weekly.

- [ ] **`scripts/feedback/digest.py`** — runs Sunday 22:00 local. Inputs:
  - `.coordination/FEEDBACK_LOG.md` (promoted, triaged items)
  - `.coordination/events.jsonl` (WS state transitions this week)
  - `.coordination/inbox/feedback_candidates_<current-ISO-week>.md` (Dennis may have ticked `promote: [x]`)

  Output: `.coordination/MONDAY_DIGEST.md`, with sections:

  1. **Open feedback per WS** — unresolved FEEDBACK_LOG items grouped by `ws` field, counts + 1-line summaries. Sort: most items first.
  2. **New candidates this week** — raw count from `detect.py` output, broken down by heuristic (H1/H2/H3/H4). NOT the entries themselves — just counts + a pointer to the inbox file.
  3. **Retest required** — WSs that got `/ws-completed` this week that have unretested feedback items. Explicit: "run `/ws-retest WS<N>` before closing the loop."
  4. **WS status deltas** — from `events.jsonl`: who claimed what, who completed what, who rejected what. One line each.

- [ ] **Acceptance shape:**
  ```markdown
  # Monday Digest — 2026-W17

  ## Open feedback (12 items across 4 WSs)
  - WS4: 5 open (3 retrieval, 2 latency)
  - WS2: 3 open (all numeric-accuracy edge cases)
  - ...

  ## New candidates this week (detect.py)
  - H1 (zero-citation): 18
  - Total: 18 (H2/H3/H4 not yet enabled)
  - Inbox: .coordination/inbox/feedback_candidates_2026-W17.md

  ## Retest required
  - WS11: completed 2026-04-18, had 2 open feedback items at claim time.
    Run `/ws-retest WS11`.

  ## WS status deltas
  - WS14: claimed (dennis) 2026-04-15
  - WS12: completed (claude) 2026-04-17
  - ...
  ```

- [ ] Must be < 80 lines for a typical week. If it's longer, heuristics are too loose — flag in a risks review, don't just paginate it.

### Phase 4 — Close-the-loop integration

**Goal:** make open feedback items visible at the moment they matter — `/ws-status`, `/ws-complete`, a new `/ws-retest`.

- [ ] **New event type in `events.jsonl`:**
  ```json
  {"ts": "...", "agent": "dennis", "event": "feedback", "ws": "WS4",
   "source": "feedback_log#2026-04-14-entry-3", "severity": "medium",
   "note": "zoek_moties misses initiatiefvoorstellen"}
  ```
  Extend `scripts/coord/append_event.py` to accept `--event feedback --source ... --severity ...`. Source format is **always** `feedback_log#<date>-entry-<N>` — never a file path, because `FEEDBACK_LOG.md` itself may move or rotate.

- [ ] **`scripts/coord/rebuild_state.py`** — add `open_feedback` and `retested_since_last_complete` columns to the per-WS table in `state.md`. Count open = `feedback` events with no corresponding `feedback_resolved` event, grouped by WS.

- [ ] **`/ws-status`** (`.claude/commands/ws-status.md`) — surface the new column. Format:
  ```
  WS4   in_progress  owner=claude   open_feedback=2   retested=yes
  WS11  claimed      owner=dennis   open_feedback=0
  ```

- [ ] **`/ws-complete`** (`.claude/commands/ws-complete.md`) — on invocation:
  ```
  if open_feedback(WS) > 0 and not retested_since_last_complete(WS):
    print(f"WARNING: WS{N} has {n} open feedback items that have not been retested.")
    print(f"Consider running /ws-retest WS{N} first. Continue? [y/N]")
  ```
  **Warn, not block.** Gate you override defeats the gate.

- [ ] **New `/ws-retest <WS>` command** — `.claude/commands/ws-retest.md`:
  ```
  1. Read events.jsonl — find all feedback events with ws=<WS> and no
     corresponding feedback_resolved.
  2. For each: print original failing query + expected behaviour from the
     FEEDBACK_LOG entry (resolved via source= back-ref).
  3. Print a checklist for Dennis: [ ] query 1, [ ] query 2, ...
  4. Dennis re-runs each through MCP (manually), then marks resolved:
     python scripts/coord/append_event.py --event feedback_resolved --ws WS4 \
       --source feedback_log#2026-04-14-entry-3
  5. After all resolved: `/ws-complete WS<N>` runs clean (no warning).
  ```

---

## Acceptance criteria

**Phase 1 (schema):**
- [ ] Migration runs clean on staging + prod; no locking incidents
- [ ] All four fields populated on every new `mcp_audit_log` row (MCP + web)
- [ ] No historical backfill attempted

**Phase 2 (detect):**
- [ ] `detect.py --since 24h` runs in < 30s on a week of audit data
- [ ] Candidates file appears under `.coordination/inbox/` nightly
- [ ] Only H1 enabled initially; H2/H3/H4 gated behind a config flag
- [ ] `.agent/rules/feedback_candidates_are_dennis_only.md` committed

**Phase 3 (digest):**
- [ ] `MONDAY_DIGEST.md` generated Sunday 22:00 local
- [ ] File < 80 lines for a representative week
- [ ] Contains all four sections in the prescribed order

**Phase 4 (close-the-loop):**
- [ ] `events.jsonl` accepts `feedback` and `feedback_resolved` events
- [ ] `state.md` has `open_feedback` + `retested` columns
- [ ] `/ws-status` shows them
- [ ] `/ws-complete` warns (does not block) on unretested feedback
- [ ] `/ws-retest` prints a runnable checklist
- [ ] Back-reference round-trip verified: pick 3 FEEDBACK_LOG entries, find them in `events.jsonl` via `source=`, resolve them, confirm `/ws-status` count drops

---

## Eval gate

| Metric | Source | Target |
|---|---|---|
| Detection recall (backtest) | `detect.py` run against last 4 weeks of audit data | ≥ 80% of items Dennis manually logged in FEEDBACK_LOG during that window are present in the candidates file for the same week |
| Detection noise | Weekly candidates file size | ≤ 50 entries/week. If higher, heuristics too loose — tighten before adding more heuristics |
| Digest length | `wc -l MONDAY_DIGEST.md` | ≤ 80 lines for a representative week |
| Back-reference round-trip | Manual — 3 items | `feedback` event → FEEDBACK_LOG entry → `feedback_resolved` event all linkable via `source=feedback_log#<id>` |
| Zero LLM calls in the loop | Code review of `detect.py` + `digest.py` | No `anthropic`, `openai`, `gemini`, or `mcp.tool()` calls in either script |

---

## Risks specific to this workstream

| Risk | Mitigation |
|---|---|
| **Heuristic noise drowns signal.** Enabling all 4 heuristics on day 1 produces hundreds of candidates, Dennis stops reading the file, the loop dies. | **Start with H1 only.** Measure signal ratio for 2 weeks. Only add H2/H3/H4 one at a time after evidence the previous is tuned. |
| **Candidates file bloat.** Weekly file > 50 entries means heuristics are too loose. | Hard limit in the eval gate. If exceeded, tighten thresholds — do not paginate or archive harder. |
| **LLM-as-judge creep.** Someone (or a future agent) proposes "just have Claude score each session 1–5." | Explicitly rejected in memory `feedback_eval_quality_audit.md` + cold-start prompt + this risks section. Reject on sight. |
| **Dashboard temptation.** A pretty admin summary page you'd "glance at daily" — you won't. | Do not build a dashboard. The weekly digest that lands in the Monday ritual **will** be read; a daily dashboard won't. Decision already logged. |
| **Rot of back-references** as handoffs archive and FEEDBACK_LOG rotates. | Back-refs use **WS IDs** (stable) and `feedback_log#<date>-entry-N` (content-addressable), never file paths. Archive tooling (`scripts/coord/archive_ws.py`) must leave these intact. |
| **Parallel tracking system.** Tempting to build a separate `feedback.jsonl` or new DB table. | Piggyback on `events.jsonl` + `scripts/coord/append_event.py` exclusively. One source of truth for WS state. |
| **Candidates file read by an agent.** An agent working on WS4 greps `.coordination/` for context and picks up unvetted candidates as if they were triaged bugs. | `.agent/rules/feedback_candidates_are_dennis_only.md` — explicit file-level deny. Reviewed at every cold-start. |
| **Warn-not-block ignored forever.** Dennis keeps clicking through. | Acceptable. Per explicit decision 2026-04-14: gates you override defeat the gate. A warn that's noticed 30% of the time still catches 30% of regressions. |
| **Migration locks audit table** during a spike. | Migration is additive only (ADD COLUMN, ADD INDEX CONCURRENTLY where possible). No rewrites. Per `feedback_mcp_uptime.md`, check `pg_stat_activity` before running. |
| **`session_id` missing for legacy clients** (curl, old MCP sessions). | Column is NULLable; `detect.py` H2 (retry grouping) falls back to `(user_id, query-prefix, minute-bucket)` when `session_id IS NULL`. |

---

## Future work (explicitly out of scope)

Do **not** pull any of these into WS17. Each is either rejected on principle or deferred to a later version.

- **In-product thumbs up/down rating** — deferred to v0.4+. Approved to skip for v0.3.
- **External telemetry integration** (Sentry, Datadog, PostHog) — explicitly rejected. Open platform, own your data.
- **Auto-triage of candidates into WS buckets** (via classifier or LLM) — explicitly rejected. Triage is the one thing that stays human.
- **LLM-judge session quality scores** — explicitly rejected (see risks + memory `feedback_eval_quality_audit.md`).
- **Session replay UI** (timeline viewer for a session's tool calls) — deferred until post-v0.3, and only if Monday digest demand proves the need.
- **Anomaly detection on tool-call distributions** — v0.3+ (WS4 TypeScript / cost-observability track).
- **SLO / error-budget framing** — v0.3+ when multi-gemeente traffic justifies it.

---

## Open questions for Dennis

- [ ] Confirm Phase 0 move path: `brain/FEEDBACK_LOG.md` → `.coordination/FEEDBACK_LOG.md` — still planned for v0.2.1 cut?
- [ ] Which week to run the one-time backtest against? Recommendation: the most recent 4 complete ISO weeks at the time Phase 2 ships.
- [ ] Launchd vs cron for the nightly `detect.py` — preference?

---

## Outcome

*(populated when shipped)*
