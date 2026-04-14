# WS16 — MCP Monitoring & Observability

> **Status:** `in progress` — Phase 1 scripts (`watch.py` + `stats.py` + `alert.py`) shipping today (2026-04-14) alongside this handoff; Phase 2 deferred to v0.2.1
> **Owner:** `Dennis` (with Claude assist)
> **Priority:** 2 (press-moment hygiene — without this we'd be flying blind during the first journalist demo)
> **Parallelizable:** yes — no file overlap with any other active WS; depends only on WS4 schema already in prod
> **Target version:** v0.2.0 (Phase 1); v0.2.1 (Phase 2)
> **Last updated:** 2026-04-14

---

## TL;DR

WS4 shipped the `mcp_audit_log` table on 2026-04-13 — every MCP tool call is now persisted with tool name, latency, scopes, result size, and error class. WS16 closes the observability loop with three small CLI scripts Dennis can run from his laptop: `watch.py` (live tail during demos), `stats.py` (24h rollup for press-prep sanity checks), and `alert.py` (macOS notification when error rate or latency blows up). All three are read-only against Postgres, reuse the existing `services/db_pool.py` pool, and run on macOS launchd. No external telemetry services (LangSmith / Helicone / Datadog explicitly rejected 2026-04-14 — too heavyweight for a solo-founder press moment and they leak user queries to a third party). A proper admin page + session-replay UI are deferred to v0.2.1 once the press moment has landed.

---

## Dependencies

| Dependency | Type | Notes |
|---|---|---|
| WS4 `shipped 2026-04-13` | hard | Provides `mcp_audit_log` table (Alembic `20260413_0007`), `services/audit_logger.py`, and the `logged_tool` decorator that populates rows |
| `services/db_pool.py` | code | All three scripts reuse the pool — no new connections, no standalone psycopg imports |
| SSH tunnel (`./scripts/dev_tunnel.sh --bg`) | runtime | Scripts run locally; Postgres is tunnel-only on Hetzner |
| macOS launchd | runtime | Phase 1 `alert.py` is invoked by a `~/Library/LaunchAgents/com.neodemos.mcp-alert.plist` on a 5-min schedule |

No other hard deps. Independent of WS1, WS11, WS12, WS14 corpus work.

---

## Cold-start prompt

```
You are picking up WS16_MCP_MONITORING for NeoDemos — civic transparency platform
for Rotterdam raad, solo founder (Dennis). WS4 already shipped the mcp_audit_log
table to prod on 2026-04-13. Your job is the read-path: three small macOS-only
CLI scripts that let Dennis see what's happening on the MCP surface during the
press-moment run-up.

Read these files first:
- docs/handoffs/WS16_MCP_MONITORING.md (this file)
- docs/handoffs/done/WS4_MCP_DISCIPLINE.md (provider contract — what gets written to the audit log)
- alembic/versions/20260413_0007_mcp_audit_log.py (exact schema)
- services/db_pool.py (reuse this pool — do NOT open new connections)
- services/audit_logger.py (the writer side — read-only reference, never modify)
- scripts/mcp/watch.py (already scaffolded — finish + harden)
- scripts/mcp/stats.py (already scaffolded — finish + harden)
- scripts/mcp/alert.py (to be created)

Design constraints — these are non-negotiable, repeat them to yourself:
- NO external telemetry services. LangSmith, Helicone, Datadog were rejected
  2026-04-14. Keep it local.
- macOS-only for v1. launchd for scheduling, osascript for notifications. No
  cross-platform abstractions, no systemd, no Docker.
- READ-ONLY against Postgres. Scripts NEVER INSERT/UPDATE/DELETE mcp_audit_log.
  Validate with EXPLAIN — no write locks.
- Piggyback on services/db_pool.py. Do not import psycopg directly. Do not open
  a new pool. Do not set its own DSN.
- Mask PII on screen. watch.py is for live demos — show tool_name, latency,
  error_class, truncated params_hash. Hash user_id. Drop IP.
- Fail gracefully. If the SSH tunnel is down, print "tunnel unavailable, retry"
  and exit 0 — NEVER wedge, NEVER retry-loop forever, NEVER page Dennis with a
  Python stack trace at 7am.

Phase 1 ships today (2026-04-14) as part of v0.2.0. Phase 2 is deferred to
v0.2.1 (admin page, session reconstruction, log rotation).
```

---

## Files to read first

| File | Why |
|---|---|
| [`alembic/versions/20260413_0007_mcp_audit_log.py`](../../alembic/versions/20260413_0007_mcp_audit_log.py) | Exact schema — field names, types, indexes. Source of truth for query shape. |
| [`services/db_pool.py`](../../services/db_pool.py) | Reuse this pool. Scripts get a connection via the same entrypoint the web app uses. |
| [`services/audit_logger.py`](../../services/audit_logger.py) | The writer side. Read-only reference — WS16 scripts never touch this file, but need to know what columns get filled vs NULL. |
| [`scripts/mcp/watch.py`](../../scripts/mcp/watch.py) | Scaffold already in tree. Harden per Phase 1 acceptance. |
| [`scripts/mcp/stats.py`](../../scripts/mcp/stats.py) | Scaffold already in tree. Harden per Phase 1 acceptance. |
| [`docs/handoffs/done/WS4_MCP_DISCIPLINE.md`](done/WS4_MCP_DISCIPLINE.md) | Provider contract for the audit log — what every field means, what `error_class` values exist, what `params_hash` does NOT contain (secrets — never). |
| [`logs/mcp_queries.jsonl`](../../logs/) | Secondary mirror of MCP calls (JSONL); rotation is a Phase 2 concern. |
| Memory: [`feedback_mcp_uptime.md`](../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/feedback_mcp_uptime.md) | 2026-04-14 double-outage lessons — why fast-feedback matters. |

---

## Design principles (repeat these in every PR description)

1. **No external telemetry.** LangSmith / Helicone / Datadog explicitly rejected 2026-04-14. Query text is raadslid research — it never leaves our infra. No third-party SDKs in `requirements.txt` for this WS.
2. **macOS-only for v1.** launchd, osascript, `~/Library/LaunchAgents/`. Cross-platform abstractions are premature — there's exactly one user (Dennis, on a MacBook) until v0.2.1+.
3. **Read-only against Postgres.** Scripts NEVER `INSERT`/`UPDATE`/`DELETE` on `mcp_audit_log` or any table. Enforced belt-and-braces by a read-only replica role in the pool.
4. **Piggyback on existing infra.** Reuse `services/db_pool.py`, reuse the tunnel from `./scripts/dev_tunnel.sh --bg`, reuse WS8f's admin shell for Phase 2. No new dependencies.
5. **Graceful degradation.** Tunnel down → print a friendly line and exit 0. Own infra failing is never a user-visible alert.
6. **Privacy-first output.** `user_id` hashed, IP dropped, raw params never printed (params_hash already stored as sha256 by WS4 — do NOT try to reverse it).

---

## Build tasks

### Phase 1 — Pre-press CLI scripts (v0.2.0, SHIPPING NOW 2026-04-14)

All three scripts live under `scripts/mcp/`. Same invocation pattern: `python scripts/mcp/<name>.py [flags]`. All read-only. All reuse `services/db_pool.py`. Python 3.12, no new dependencies beyond what's already in `requirements.txt` (`psycopg`, `rich` for colour output — `rich` already present via WS9).

- [ ] **`scripts/mcp/watch.py` — live tail.** Polls `mcp_audit_log` every 2 s for rows newer than the last-seen `id`, prints one line per call to stdout. Colour-coded: green for success (<1 s latency), yellow for slow (1–5 s), red for error (`error_class IS NOT NULL`). Columns: `HH:MM:SS  tool_name  latency_ms  result_bytes  user#<short-hash>  [error_class]`. Mask `user_id` as `user#<sha256[:6]>`; never print IP, never print raw params. Flag `--since=5m` to backfill recent history before tailing. Flag `--tool=<name>` to filter to one tool. Graceful exit on SIGINT. On DB disconnect print `[tunnel down, retrying in 10s]` and retry up to 3 times before exiting 1.

- [ ] **`scripts/mcp/stats.py` — window summary.** One-shot, prints a human-readable block. Flag `--window=24h` (accepts `1h`, `6h`, `24h`, `7d`, `30d`). Flag `--json` for machine-readable output (Phase 2 admin page will consume this). Sections:
  1. **Headline** — `N calls · P50=<ms> · P95=<ms> · error_rate=<pct>%`
  2. **Tool usage** — sorted desc by call count, with p50/p95 latency per tool, plus `result_bytes` p50 per tool (catches "result is silently empty" regressions)
  3. **Error breakdown** — count per `error_class`, with most recent example timestamp per class (`snippet_provenance_mismatch`, `empty_chunk`, `timeout`, etc. — classes defined in WS4)
  4. **Top users** — top 5 by call count (hashed IDs). During solo testing Dennis is 100% — that's expected, not a bug.
  5. **Quiet tools** — tools in the registry with zero calls in window (catches dead-tool regressions after rewrites). Cross-references `services/mcp_tool_registry.py` for the full set.
  6. **Scope usage** — count per `scope_used[]` element (helps spot whether `public` scope traffic is ramping vs authenticated).
  All queries indexed on `(ts)` — confirm with EXPLAIN that no seq-scan lands on prod. Output is plain ASCII (pipe into `| pbcopy` for press-prep notes). Exits 0 on zero-rows-in-window (prints a clear "No calls in the last 24h" message, not a traceback).

- [ ] **`scripts/mcp/alert.py` — anomaly detector (launchd).** Runs every 5 min via launchd. Checks three rules; fires at most one macOS notification per rule per hour (cooldown file at `~/.neodemos/alert_cooldown.json`). **Start with exactly these 3 rules — do not expand to 10.** Add more only after 2 weeks of running without alert fatigue.
  1. **Error spike** — `error_rate > 10%` over the last 15 min AND `call_count >= 5` (suppress noise on quiet traffic where 1 error = 100%).
  2. **Latency regression** — `p95 > 8000ms` over the last 15 min for any tool with `call_count >= 3`. 8 s is the "a journalist notices and writes about it" threshold.
  3. **Silence** — `0 calls in the last 30 min` during window 09:00–22:00 Europe/Amsterdam (not a 3am-nobody-cares alert). Catches MCP-server-is-down-but-kamal-thinks-it's-fine.

  Fires via `osascript -e 'display notification "..." with title "NeoDemos MCP"'`. On SSH tunnel unavailable, exit 0 (silent) — do NOT alert on own-infra being off.

- [ ] **launchd plist installation** — create `ops/launchd/com.neodemos.mcp-alert.plist` (committed to repo) with `StartInterval=300`, `ProgramArguments` pointing at the venv Python + `alert.py`, `StandardOutPath` and `StandardErrorPath` under `/tmp/neodemos-mcp-alert.{out,err}` for debugging, `WorkingDirectory` set to the repo root so relative imports resolve. Install with `ln -sf "$(pwd)/ops/launchd/com.neodemos.mcp-alert.plist" ~/Library/LaunchAgents/com.neodemos.mcp-alert.plist && launchctl load ~/Library/LaunchAgents/com.neodemos.mcp-alert.plist`. Document unload command in the plist top-comment for when Dennis is travelling and wants quiet hours: `launchctl unload ~/Library/LaunchAgents/com.neodemos.mcp-alert.plist`. Verify via `launchctl list | grep neodemos` after install.

- [ ] **README section in `scripts/mcp/`** — single short `scripts/mcp/README.md` with the three invocation patterns + the install/uninstall launchd commands. No auto-generated docs, no Sphinx, no mkdocs — just one file a human can skim in 30 seconds. Link to this handoff for the "why."

### Phase 2 — Admin page + session reconstruction (v0.2.1, POST-PRESS)

Deferred for two reasons: (a) no stakeholder needs it before the press moment — Dennis is the only user today; (b) session reconstruction requires schema work that benefits from first watching real traffic patterns for 2–4 weeks.

- [ ] **`session_id` migration on `mcp_audit_log`** — add `session_id UUID NULL` column via Alembic `20260501_00XX_mcp_audit_log_session_id.py`. Backfill strategy: correlate by `user_id + ts` within a 30-min rolling window (best-effort; old rows get NULL). Index `(session_id, ts)`. Update `services/audit_logger.py` to populate `session_id` from the MCP request context going forward. **House rule applies:** no DDL on `mcp_audit_log` while MCP server is serving traffic — use `CREATE INDEX CONCURRENTLY` and run the ALTER during a deploy window (see `feedback_mcp_uptime.md`).

- [ ] **Admin page at `/admin/mcp/queries`** — read-only, admin-scope, mirrors the `stats.py` output in HTML with live-refresh-on-interval. Filters by tool, user (hashed), time range. Links to the WS8f content CMS shell so Dennis reuses the existing admin layout. Renders inside the router split Dennis did in WS8f — add to `routes/admin.py`, no new router file.

- [ ] **Read-only conversation replay UI** — given a `session_id`, render the ordered tool calls with params (hash-only — raw params never persisted, so replay is structural, not literal), latencies, and results-size timeline. Purpose: debug "why did that journalist demo go sideways 3 days ago" after the fact. Explicitly NOT a full transcript viewer — we don't have the raw Claude turns, only MCP-side tool invocations. UX: a single page at `/admin/mcp/queries/session/<uuid>` with a vertical timeline; click a tool call to expand its stored metadata. No editing, no re-running — purely forensic.

- [ ] **Session heatmap on admin page** — visualize which tools cluster together in real sessions. Gives Dennis data to argue for tool consolidation (if `zoek_moties` and `zoek_uitspraken` are always called back-to-back, maybe they should merge; if `get_neodemos_context` is never called, the primer isn't pulling its weight). Matplotlib → static PNG on request, not a live D3 chart.

- [ ] **Log rotation for `logs/mcp_queries.jsonl`** — currently appends without bound. Add `scripts/mcp/rotate_logs.py` that renames daily (`.jsonl.YYYY-MM-DD`), gzips files older than 7 days, deletes files older than 90 days. Wire into launchd with `StartCalendarInterval` at 03:00 local. Defer until after the file actually hits >100 MB.

- [ ] **Pre-canned SQL views for `stats.py` heavy queries.** Once traffic is real (>1K calls/day), the five-section `stats.py` output starts issuing five separate aggregate queries on the same window. Collapse into a `mcp_audit_log_hourly_rollup` materialised view refreshed every 5 min; `stats.py` reads the rollup and only hits the raw table when `--window < 1h`. Keeps the read-only contract.

- [ ] **PagerDuty / email escalation for Rule 1 + 2.** Today `alert.py` fires a macOS notification — fine when Dennis is at his laptop, useless when he's not. Phase 2 adds a second sink: if the notification fires AND Dennis hasn't dismissed it within 30 min, send an email via Postmark (already in the stack for auth). Still no third-party telemetry — email is transactional, not observability.

---

## Acceptance criteria

**Phase 1 (ship before v0.2.0 tag) — ticky-box, no ambiguity:**

_watch.py:_

- [ ] `python scripts/mcp/watch.py` prints live MCP calls without crashing when Dennis runs it during a query session. Verified by live-tailing while issuing 3 test queries via Claude Desktop.
- [ ] `python scripts/mcp/watch.py --since=1h` backfills and then tails.
- [ ] `python scripts/mcp/watch.py --tool=zoek_raadshistorie` filters correctly.
- [ ] `watch.py` masks `user_id` (shows `user#<6-char-hash>`), drops IP, never prints raw params. Verified by grepping output for `@` (email marker) and IP-like regex — both return zero.
- [ ] `watch.py` colour-coding is correct: green < 1000ms, yellow 1000–5000ms, red > 5000ms OR `error_class IS NOT NULL`.

_stats.py:_

- [ ] `python scripts/mcp/stats.py --window 24h` produces human-readable summary with headline + tool usage + error breakdown + top users + quiet tools + scope usage. Output fits in 80 columns.
- [ ] `stats.py` numbers match a hand-written `SELECT COUNT(*) FROM mcp_audit_log WHERE ts > now() - interval '24 hours'` — spot-check before ship.
- [ ] `stats.py` EXPLAIN shows `Index Scan using idx_mcp_audit_log_ts` for all queries (no seq-scan on prod).
- [ ] `stats.py --window 24h --json` emits valid JSON that `python -c 'import json, sys; json.load(sys.stdin)'` accepts.
- [ ] `stats.py` on an empty window prints `No calls in the last 24h` and exits 0.

_alert.py:_

- [ ] `python scripts/mcp/alert.py` with launchd plist installed fires a macOS notification within 5 min of a deliberate error injection (raise in one tool → restart MCP → verify notification).
- [ ] `alert.py` respects the 1-hour-per-rule cooldown — verified by injecting two consecutive errors and seeing only one notification.
- [ ] `alert.py` exits 0 (silent) when SSH tunnel is down — verified by killing tunnel and running directly.
- [ ] `alert.py` exits 0 outside 09:00–22:00 Europe/Amsterdam for Rule 3 only — verified by setting system clock to 03:00 locally.
- [ ] `alert.py` respects the `~/.neodemos/alert_enabled=0` kill-switch — verified by writing `0` and confirming no notification fires on injected error.
- [ ] `alert.py` uses `zoneinfo.ZoneInfo("Europe/Amsterdam")` for silence-rule hour check (grep-verified; no naive `datetime.now().hour`).

_launchd:_

- [ ] `~/Library/LaunchAgents/com.neodemos.mcp-alert.plist` is loaded and runs every 5 min — confirmed via `launchctl list | grep neodemos`.
- [ ] The plist is a symlink into `ops/launchd/com.neodemos.mcp-alert.plist` in the repo (NOT a standalone file in `~/Library/LaunchAgents/`).
- [ ] `/tmp/neodemos-mcp-alert.out` and `.err` are written on each 5-min tick for debugging.

_General:_

- [ ] All three scripts reuse `services/db_pool.py` (verified by grep — no `psycopg.connect(` outside the pool module).
- [ ] `scripts/mcp/README.md` exists with invocation examples + launchd install/uninstall commands.
- [ ] No new entries in `requirements.txt` (all dependencies already present for WS4/WS9).

**Phase 2 (deferred — do NOT check in v0.2.0):**

- [ ] `mcp_audit_log.session_id` column added via migration + populated going forward.
- [ ] `/admin/mcp/queries` page renders for admin users only.
- [ ] Session replay view at `/admin/mcp/queries/session/<uuid>` renders tool timeline.
- [ ] Log rotation keeps `logs/mcp_queries.jsonl` under 100 MB steady-state.

---

## Eval gate

Simple three-point check — runs in <5 min of Dennis's time:

| Check | Source | Target |
|---|---|---|
| `watch.py` catches a test query | Live tail while Dennis runs `zoek_raadshistorie("test")` via Claude Desktop | Line appears within 3 s of the call landing in Postgres |
| `stats.py --window 1h` matches DB truth | Compare `stats.py` headline call count to `SELECT COUNT(*) FROM mcp_audit_log WHERE ts > now() - interval '1 hour'` | Off-by-zero exact match |
| `alert.py` fires on injected anomaly | Raise `ValueError("wsdbg")` inside one registered tool, restart MCP, issue 5 calls to that tool | macOS notification fires within 10 min (worst-case: one full 5-min tick + processing lag) |

No percentage targets, no eval benchmark — these scripts are tooling, not product. If the three checks pass, they ship.

---

## Risks specific to this workstream

| Risk | Mitigation |
|---|---|
| **Alert fatigue.** 10 rules out the gate means the notifications get muted within a week and the tool becomes useless. | Start with 3 rules only. Revisit after 2 weeks of real-world running. Add a rule only when Dennis can point to a specific incident where it would have fired. |
| **SSH tunnel flakiness.** Tunnel drops are routine during laptop sleep/wake. If `alert.py` pages Dennis every time the tunnel drops, he'll disable the agent within a day. | `alert.py` exits 0 on tunnel-unavailable. `watch.py` prints `[tunnel down, retrying]` and caps retries at 3 before exiting 1. Neither produces a user-visible alert on own-infra-off. |
| **Privacy on screen-share.** Dennis will live-demo `watch.py` to journalists. If user_id or IP shows up, that's a story he doesn't want. | `watch.py` hashes user_id, drops IP entirely, truncates params_hash to 8 chars. Manual verification checkbox before every press demo. |
| **Dennis's own queries dominate stats.** During solo testing, he's 100% of traffic. Rules keyed on "unusual user" would fire constantly. | Rules 1–3 are all absolute thresholds (error rate, latency, silence) — they don't try to detect "weird users." Post-press, when real traffic lands, add per-user rules as Phase 2. |
| **launchd plist drift.** Dennis edits the plist by hand, breaks it, doesn't notice for weeks. | Plist is committed to the repo at `ops/launchd/com.neodemos.mcp-alert.plist`; the install command symlinks from `~/Library/LaunchAgents/`. Changes go through git. |
| **`mcp_audit_log` partitioning delayed.** WS4 noted partition-by-month as future work. If table grows past ~10M rows, `stats.py` queries slow down. | `stats.py` always queries via indexed `ts` range — stays fast up to ~100M rows. Revisit if we onboard a second gemeente. Tracked in WS4 `Post-ship`. |
| **Scripts write to the audit log by accident.** If a future editor adds an `INSERT` to `watch.py` for "session tracking," we've corrupted the read-only guarantee. | Add `REPLICA` role credentials in `services/db_pool.py` (dedicated read-only DSN); scripts connect with those. Belt-and-braces: Postgres will reject any stray write. |
| **Colima not running when Dennis opens laptop.** `watch.py` needs the SSH tunnel; tunnel needs working network; neither needs Colima. But if a future editor adds a `docker ps` check, the script fails on cold-boot mornings. | Scripts must NOT touch Docker/Colima. If that check sneaks in during a refactor, the acceptance test catches it ("tunnel unavailable → exit 0"). |
| **Notification spam during development.** Dennis breaks a tool locally, every 5 min launchd fires an alert. | `alert.py` reads `~/.neodemos/alert_enabled` (single-file flag). Missing file = enabled; file containing `0` = suppressed. Document the toggle in the `scripts/mcp/README.md`. |
| **Timezone drift for Rule 3 silence alert.** Server-side `ts` is UTC; Dennis is Europe/Amsterdam (CET/CEST). A naive local-hour check will alert at 04:00 local during DST transitions. | Explicit `zoneinfo.ZoneInfo("Europe/Amsterdam")` in `alert.py` silence-rule, unit-tested with a fake clock at both DST boundaries. |

---

## Future work (do NOT do in this workstream)

- **Feedback-loop automation** — auto-creating FEEDBACK_LOG entries from `error_class` spikes, auto-filing WS entries, auto-running regression repro. That's **WS17** (separate workstream, not yet written). WS16 is detection-only.
- **External observability integrations** — LangSmith / Helicone / Datadog / Honeycomb / Grafana Cloud. Explicitly rejected 2026-04-14 (scope, cost, privacy — query text is sensitive raadslid research). Revisit if we onboard a second gemeente and a solo CLI stops scaling.
- **Cross-session PII detection** — scanning params_hash patterns for accidental user-PII leakage across sessions. Adjacent to GDPR work; owner should be security-focused, not monitoring-focused. Defer past v0.3.0.
- **Cross-project reuse** — packaging `scripts/mcp/` as a standalone `neodemos-mcp-monitor` pip tool for other Dutch civic projects. NeoDemos-specific schema for now; revisit only if a second deployer asks.
- **Prometheus / OpenTelemetry export** — tempting but premature. At current traffic (<100 calls/day from Dennis alone), three CLI scripts are strictly better than a metrics stack. Revisit at >10K calls/day.
- **Anomaly detection via ML** — flagged in WS4 `Future work` as v0.3.0+. Not in WS16.
- **Web-based dashboard with charts** — Phase 2 admin page is HTML tables, not Chart.js. If we want graphs, use `stats.py` output piped into a spreadsheet. YAGNI.

---

## Outcome

_To be filled when Phase 1 ships and launchd agent is running._
