# Feedback Loop — Research Brief

> **Version:** 1.0 — 2026-04-14
> **Status:** Research only. Execution scoped in [`../handoffs/WS17_FEEDBACK_LOOP.md`](../handoffs/WS17_FEEDBACK_LOOP.md).
> **Purpose:** Durable record of the reasoning behind the NeoDemos feedback-loop design. If someone asks "why not LangSmith?" or "why keep triage manual?", read this.

---

## 1. Problem we're solving

NeoDemos currently runs a manual production → triage → fix → verify loop:

1. Dennis uses the MCP server / web UI in real chat sessions.
2. When something goes wrong, he writes the observation into `.coordination/FEEDBACK_LOG.md` (scheduled to move to `.coordination/FEEDBACK_LOG.md` in Phase 0).
3. Every Monday, he triages the past week's entries into one of four destinations: WS handoff edit, TODOS.md inbox, memory update, or discard.
4. Items in TODOS.md triage inbox get max 7 days before being assigned to a WS or discarded.

This works for v0.2.0 because query volume is low (<100/day). It will not scale to post-press volume without automation.

## 2. Current plumbing (facts)

| Layer | What's there | Gap |
|---|---|---|
| Tool-call audit | `mcp_audit_log` Postgres table (shipped 2026-04-13 via WS4) | No `session_id` — can't reconstruct multi-turn conversations |
| Query logging | `/logs/mcp_queries.jsonl` (raw params) | No rotation, no session grouping |
| Failure signals | `status_code`, `error_class` | No "zero citations", "user frustrated", "repeated retry" detection |
| User rating | None | No thumbs-up/down, no "report issue" |
| Triage workflow | `.coordination/FEEDBACK_LOG.md` + Monday ritual (TODOS.md §triage) | No back-reference from feedback → WS → fix → verify |
| Alerting | None — WS4 §daily-summary deferred to v0.3.0 | |
| Retention | None defined | TODOS.md has a pending "rotate mcp_queries.jsonl" item |

## 3. Design principle (load-bearing)

**Two functions, two automation levels.** This is the most important idea in the brief.

1. **Signal capture** — "what went wrong, where, when?" Automate aggressively. This is drudgery; every well-instrumented AI system in 2024–2026 does this.
2. **Triage** — "which workstream does this belong to? is it real? what's the political / product context?" **Keep manual.** This is where Dennis's Middelburg contact, press timing, and political instinct matter. No LLM-as-judge can substitute.

Dennis's memory [`feedback_eval_quality_audit.md`] explicitly rejects abstract LLM-judge scoring. Reason cited: "formal eval misses real failures; use FEEDBACK_LOG MCP sessions as the true quality gate, not abstract LLM-judge scores." The only thing an LLM-judge on session quality produces is a 3.8/5 average that tells you nothing.

## 4. Why not LangSmith / Helicone / Langfuse / Arize

External hosted observability platforms (2024–2026 state of art) solve the **signal-capture** function well. They do not solve the **triage** function for a domain-specific product with political / strategic context.

Rejected 2026-04-14 for these specific reasons:

- Query data sent to third parties: free-tier limits, privacy review needed, another bill, another dependency.
- NeoDemos already has its data in its own Postgres — the substrate is sufficient.
- Dennis is solo and non-dev. Adding a platform to learn and maintain is overhead for a problem he'll keep solving himself anyway.
- Press-moment focused: every hour not spent on WS1 / WS14 is a risk.

If NeoDemos ever has a team of 5+ and query volume >10K/day, revisit.

## 5. Proposed architecture (v0.2.1 scope — executed in WS17)

### A. Capture layer — one migration, one detection script

1. **Alembic migration** adding `session_id UUID`, `citation_count INT`, `zero_result BOOL`, `client_disconnected BOOL` to `mcp_audit_log`. MCP server + web frontend populate at write time.

2. **`scripts/feedback/detect.py`** — reads last 24h of audit log + `/logs/mcp_queries.jsonl`. Scores sessions on 4 heuristics:
   - Zero-citation answer on a non-trivial query
   - Same query retried > 2× within 5 minutes (user frustration)
   - Session ended after an error
   - Latency > 10s on any tool call

   Output: `.coordination/inbox/feedback_candidates_<YYYY-WW>.md` (per-week rotation). **Pre-triage firehose — never auto-written to FEEDBACK_LOG.** Dennis promotes items manually during Monday triage.

### B. Triage layer — keep manual, reduce friction

3. **`scripts/feedback/digest.py`** runs Sunday night. Reads `.coordination/FEEDBACK_LOG.md`, `events.jsonl`, the latest candidates file. Generates `.coordination/MONDAY_DIGEST.md`:
   - Open feedback items per WS (unresolved)
   - New candidates this week (from `detect.py`)
   - WSs `/ws-complete`d this week with prior feedback items → prompts re-test
   - 1-line statuses from `events.jsonl`

   Dennis reads this at Monday triage instead of scrolling four separate files.

### C. Close-the-loop — piggyback on coordination layer

4. **New `feedback` event type in `events.jsonl`.** When Dennis triages a FEEDBACK_LOG entry into WS4, append:
   ```json
   {"ts":"…","agent":"Dennis","event":"feedback","ws":"WS4","source":"feedback_log#2026-04-11-entry-3","severity":"high"}
   ```
   `/ws-status` shows open-feedback count per WS. `/ws-complete` warns (does NOT block) if the WS has unretested feedback items.

5. **`/ws-retest <WS>`** — pulls feedback items linked to WS from `events.jsonl`, prints a checklist of original failing queries. Dennis re-runs them through MCP, confirms they now work, then runs `/ws-complete`.

### D. In-product rating (deferred to v0.4+)

6. Thumbs-up/down on MCP answers. Cheap noisy signal. Low priority — Dennis's own sessions are higher-signal than anonymous thumbs.

## 6. Pitfalls (critical coaching)

1. **Don't let `detect.py` auto-write to FEEDBACK_LOG.** If the script dumps 200 candidates/week into the curated log, FEEDBACK_LOG stops being curated. Separate file (`feedback_candidates_<week>.md`). Dennis promotes candidates manually. Same reason we don't auto-triage.

2. **Four heuristics are already three too many for v1.** Start with *one* — "zero-citation answer on a non-trivial query." Measure signal/noise for 2 weeks. Only add more if signal ratio stays high. Every noisy heuristic trains you to ignore all of them.

3. **`session_id` is load-bearing — instrument everything at once.** Half-instrumentation (MCP has session_id, web UI doesn't) creates a blind spot exactly where users spend most time.

4. **"Daily summary in `/admin`" from WS4 is a trap.** A dashboard implies someone reads it daily. Dennis won't. Weekly digest that lands in the Monday ritual will actually get read. Build for the behavior you'll exhibit, not the one you'd like to.

5. **LLM-as-judge on session quality is tempting and wrong.** Someone will eventually suggest "let's have Claude score each session 1-5." Don't. The score converges to 3.8 and tells you nothing. Mechanical signals only (citations present? structured data? latency OK?), not quality judgments.

6. **Back-references rot.** A FEEDBACK_LOG entry linked as `[WS4_MCP_DISCIPLINE.md §Tool API]` breaks when WS4 archives (unless `archive_ws.py` catches the pattern). Use stable identifiers — the WS ID, not the path.

7. **Press moment comes first.** Don't build any of this before WS14 (calendar quality) and WS1 (GraphRAG). Everything here is valuable in v0.3.0. In v0.2.0 the manual ritual suffices because volume is <100/day.

## 7. File placement

| File | Home | Audience | Why |
|---|---|---|---|
| `FEEDBACK_LOG.md` | `.coordination/FEEDBACK_LOG.md` (after Phase 0 move from `brain/`) | Dennis + agents reference | Handoffs cite specific entries — needs a stable, agent-visible path |
| `feedback_candidates_<week>.md` | `.coordination/inbox/` | Dennis only | `inbox/` subdir signals "pre-triage, don't read." `.agent/rules/` gets explicit "do not read `.coordination/inbox/`" rule |
| `MONDAY_DIGEST.md` | `.coordination/` | Dennis only | Weekly artifact, overwritten each Sunday |

## 8. Sequencing

- **v0.2.0 (NOW, pre-press):** nothing. Manual ritual stays. Coordination layer built today IS the scaffolding this plugs into.
- **v0.2.1 (post-press):** WS17 phases A–C above (schema, detect, digest, close-the-loop).
- **v0.3.0+:** in-product rating (optional), cross-project reuse (if NeoDemos has sibling tools).

## 9. Open design questions (resolved 2026-04-14)

1. **In-product thumbs rating?** No for v0.3. Maybe v0.4.
2. **Where do candidates live?** `.coordination/inbox/feedback_candidates_<week>.md`. Not readable by agents.
3. **Should `/ws-complete` block on unretested feedback?** No — warn only. Gates you override defeat the gate.

## 10. Cross-references

- Execution plan: [`docs/handoffs/WS17_FEEDBACK_LOOP.md`](../handoffs/WS17_FEEDBACK_LOOP.md)
- Monitoring (separate concern, shares `mcp_audit_log`): [`docs/handoffs/WS16_MCP_MONITORING.md`](../handoffs/WS16_MCP_MONITORING.md)
- Coordination layer (foundation): [`../../.coordination/`](../../.coordination/)
- Original audit log: [`docs/handoffs/done/WS4_MCP_DISCIPLINE.md`](../handoffs/done/WS4_MCP_DISCIPLINE.md)
- Memory grounding the "no LLM-judge" rule: `.claude/projects/.../memory/feedback_eval_quality_audit.md`
