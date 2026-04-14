# NeoDemos Multi-Agent Coordination System — Master Reference

> **Version:** 1.0 — 2026-04-14
> **Author:** Drafted with Claude (Opus) for Dennis
> **Status:** Design specification — to be validated and implemented in phases
>
> **Purpose:** This is the complete reference document capturing the research,
> architecture decisions, and operational protocols for running NeoDemos as a
> coordinated multi-agent system. It supersedes `COORDINATION_SYSTEM_BRIEF.md`.
> A Claude Code agent can pick this up cold and produce a phase-by-phase
> implementation plan, or Dennis can use it as the reference for his own
> setup inside Antigravity IDE.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [The Problem We're Solving](#2-the-problem-were-solving)
3. [Research: Current State of Multi-Agent Coordination](#3-research-current-state-of-multi-agent-coordination)
4. [Architecture: The Dual-Store Hybrid](#4-architecture-the-dual-store-hybrid)
5. [Topology: Manager + Workers](#5-topology-manager--workers)
6. [Agentic Loops with Memory Reset](#6-agentic-loops-with-memory-reset)
7. [The Three-Tier Permission Model](#7-the-three-tier-permission-model)
8. [Running This in Antigravity: CLI vs GUI](#8-running-this-in-antigravity-cli-vs-gui)
9. [Implementation Phases](#9-implementation-phases)
10. [Risks and Mitigations](#10-risks-and-mitigations)
11. [Appendix A: Concrete File Schemas](#appendix-a-concrete-file-schemas)
12. [Appendix B: Settings.json Tier-1 Allowlist Template](#appendix-b-settingsjson-tier-1-allowlist-template)
13. [Appendix C: Hook Examples](#appendix-c-hook-examples)
14. [Appendix D: Slash Commands for the Loop](#appendix-d-slash-commands-for-the-loop)
15. [Appendix E: Migration of Existing Files](#appendix-e-migration-of-existing-files)

---

## 1. Executive Summary

NeoDemos currently coordinates 6 parallel Claude Code agents using scattered
markdown files across `docs/handoffs/`, `memory-bank/`, `brain/`, and `TODOS.md`.
Status changes require manual edits to 6+ files. There is no enforcement
preventing two agents from corrupting shared state. Dennis is burning time on
approval clicks and using Opus for work Sonnet could do perfectly well.

The proposed system fixes this with four layered changes:

- **Dual-store coordination layer.** SQLite (binary, enforces correctness via
  transactions and write locks) as the live state; JSONL (append-only, git-diffable)
  as the audit log; PreToolUse hooks as the enforcement mechanism that prevents
  rogue writes. Existing workstream handoff MDs stay — they remain the task
  definitions. Knowledge docs (research, architecture) get linked to workstreams
  via a `knowledge-map.yaml`.

- **Manager + Worker topology.** One Opus Manager session at max thinking
  performs QA review on every completed workstream and on high-risk tool calls.
  Five Sonnet Worker sessions execute workstreams. Token cost drops ~5x while
  quality stays high because Sonnet is excellent at scoped execution and Opus
  earns its keep on decomposition and review.

- **Agentic loops with memory reset.** Workers run a `claim → execute →
  complete → /clear → claim again` loop. Handoff MDs are already designed as
  self-contained cold-starts, so memory reset between tasks is safe and prevents
  context contamination.

- **Three-tier permission model.** Tier 1: auto-approve read-only and scoped
  edits (80% of current clicks eliminated). Tier 2: auto-deny dangerous
  operations via hook. Tier 3: route the risky middle zone to the Manager for
  contextual review. Dennis's click load drops to a handful of strategic
  decisions per day.

End state: Dennis opens Antigravity in the morning, checks `state.md` (the
auto-generated dashboard), glances at the Manager's overnight QA reports, and
intervenes only where strategic judgment is needed. The workers run themselves.

---

## 2. The Problem We're Solving

### 2.1 Current state of NeoDemos coordination

NeoDemos is a mature multi-workstream project: 13 workstreams (WS1–WS13), a
master plan (`docs/architecture/V0_2_BEAT_MAAT_PLAN.md`), a memory bank, a
brain folder, a triage inbox, and agent rules. The individual pieces are
excellent. The failure is that they're not connected.

| Layer | Location | Purpose | Pain point |
|-------|----------|---------|------------|
| Workstream definitions | `docs/handoffs/WS*.md` (13 files) | Task specs, acceptance criteria, cold-start prompts | Status updates require editing each file manually; dependency unblocking is not automatic |
| Parallelism map | `docs/handoffs/README.md` | Shows which workstreams can run in parallel | Static ASCII art — goes stale when statuses change |
| Memory bank | `memory-bank/*.md` (7 files) | Persistent project context | Duplicates info from handoffs; agents read both and get confused |
| Brain folder | `brain/*.md` (7 files) | Working notes, feedback log, implementation plans | Unstructured dumping ground; triage ritual can't keep up |
| Agent rules | `.agent/rules/*.md` | Communication style, lessons learned | Fine as-is |
| Command skills | `.claude/commands/*.md` | Quick-reference for RAG, deploy, backup, etc. | Fine as-is |
| Research docs | `docs/research/*.md` | Investigations, benchmarks, external research | Disconnected from workstreams that use them |
| Architecture docs | `docs/architecture/*.md` | System design, plans, technical decisions | No link to which workstream implements which design |
| Phase reports | `docs/phases/*.md` | Historical completion reports | Archive — no action needed |
| TODOS.md | Root | Triage inbox | Works for Dennis, but agents can't reliably parse it |

### 2.2 The five core failures

**No automatic dependency propagation.** When WS7 finishes, nothing tells WS1
it's unblocked. An agent or Dennis must manually edit WS1's status.

**Status is scattered.** To know "what's happening right now?" you must read 13
handoff files, TODOS.md, and the parallelism map. There is no single query that
returns the current state.

**Race conditions on parallel writes.** Two agents editing the same handoff
README or TODOS.md can overwrite each other's changes silently.

**Knowledge is disconnected.** An agent working on WS1 (GraphRAG) doesn't
automatically know about `docs/research/Neo4j.md` or
`docs/architecture/PLAN_I_LIGHTRAG_ENTITY_EXTRACTION.md`. Dennis has to tell
each fresh agent what to read.

**Cleanup burden after shipping.** When a workstream ships, someone must update
the handoff file, the README index table, the parallelism map, the memory-bank
progress file, the brain implementation plan, and CHANGELOG.md. Six files for
one state change. Most of these get missed.

### 2.3 The secondary failures

**Token waste.** Dennis uses Opus for almost everything because he's worried
about mistakes. Opus is ~5x the cost of Sonnet per token. For well-scoped
execution against an acceptance checklist, Sonnet is genuinely excellent.

**Click fatigue.** Claude Code inside Antigravity asks for approval on almost
every tool call. With 6 parallel agents, Dennis is clicking approve hundreds of
times per day. Most of these approvals are meaningless (read a file, run a
test). The rare ones that matter get lost in the noise.

**No continuous operation.** Workers stop after one workstream instead of
picking up the next unclaimed one. Dennis has to manually feed them the next
handoff.

**No quality gate.** When a worker claims something as "done," nothing
independently verifies it. Bugs slip through. Dennis catches them later, in
production.

---

## 3. Research: Current State of Multi-Agent Coordination

This section captures the full research done into current best practices. Skip
to Section 4 if you just want the architecture proposal.

### 3.1 Obsidian as an agent coordination layer

Obsidian's appeal is the `[[wikilink]]` graph — dependencies are visualized, the
vault is plain markdown, and the Dataview plugin allows queries. There is even
an Obsidian MCP server that lets LLMs read and write vault files.

**The honest verdict:** Obsidian is excellent for *human* knowledge management
and very weak for *agent* coordination. Reasons:

- Reading a vault requires parsing markdown — slow and error-prone. Each agent
  sees a potentially stale snapshot.
- Wikilinks encode relationships but not transactions. There is no conflict
  detection when two agents both try to "update the same node."
- Dataview queries require the agent to interpret results rather than query a
  structured API.
- Obsidian is optimized for a single human editor, not six concurrent writers.

**Where Obsidian does shine:** as Dennis's personal birds-eye view. If the
knowledge files in the coordination layer are Obsidian-compatible (standard
markdown + wikilinks), Dennis can browse the whole project in Obsidian, use
the graph view to see workstream dependencies, and render Dataview dashboards.
But Obsidian should never be the system of record for live state.

### 3.2 Local database approaches (SQLite, JSONL, hybrid)

**SQLite (binary database file):**
- Pros: real transactions, real write locks, referential integrity via foreign
  keys, single file, battle-tested, Python standard library
- Cons: binary format does not merge in git, no built-in audit trail, agents
  reading the raw file need SQL knowledge
- Best for: the live state store — "who is working on what right now"

**JSONL (append-only log):**
- Pros: git-friendly (appends merge cleanly, no conflicts), full audit history
  for free, human-readable, any language can parse it
- Cons: reading the current state requires processing the full log, no
  built-in uniqueness or referential constraints, rogue agents can corrupt
  the file by overwriting instead of appending
- Best for: the audit log — "what happened and when"

**Hybrid (both):**
- SQLite holds the canonical live state. Writes are gated by an enforcement
  wrapper that also appends to JSONL.
- JSONL is the disaster-recovery and audit trail. If SQLite corrupts, you can
  rebuild from JSONL.
- Git commits both. The JSONL merges cleanly; SQLite is rebuilt from JSONL in
  post-merge hooks if conflicts occur.
- This is the production-proven pattern for agent coordination.

**Beads (github.com/steveyegge/beads):** Steve Yegge built a purpose-made tool
for AI-agent task coordination using exactly this pattern — append-only JSONL
plus a SQLite cache rebuilt from the log. It's worth reading the Beads README
even if you don't use the tool directly; the design principles are right.

### 3.3 File-based coordination patterns

Ranked from best to worst for 6 simultaneous agents:

**Event log (JSONL) — BEST.** Each task update is an append. No overwrites ever.
Git merges trivially. Full history built in. Used by Beads, modern agent
frameworks, and virtually every production agent coordination system.

**Claim files.** Agent creates `.claimed_by_worker_3.lock` before starting
work, deletes it when done. Simple, no database. Downside: zombie locks when an
agent crashes — needs manual cleanup.

**Versioned task list.** Task JSON has `"claimed_by": "...", "version": N`.
Agents check version before writing and increment on write. Works but requires
disciplined optimistic-concurrency logic in every agent. Prone to silent
failures if an agent skips the check.

**Overwrite state files — WORST.** Single `state.json` updated by each agent.
Concurrent writes lose updates silently. This is roughly what NeoDemos does
today with the handoff READMEs.

### 3.4 Known patterns from the Claude Code / agentic coding community

**Claude Code Agent Teams (built-in, v2.1.32+).** One session is "team lead,"
others are teammates. Teammates share a task list, claim via file locking, send
each other messages. Dependencies resolve automatically. Token-expensive (each
teammate has a full context window) but removes the coordination burden.

**Split-and-Merge.** One orchestrator breaks work into N independent tasks, fans
out to N parallel Claude Code processes (usually in git worktrees), collects
results, and merges. Cleaner than full Agent Teams for bulk operations but less
dynamic.

**Git worktrees for agent isolation.** Each agent works in its own git worktree
(a lightweight checkout of the same repo). Prevents agents from stepping on each
other at the filesystem level. NeoDemos already uses this
(`.claude/worktrees/agent-a97617cf/`).

**MCP-based coordination.** Exposing the coordination layer as an MCP server so
agents interact via structured tool calls (`claim_task`, `complete_task`,
`query_state`) rather than raw file edits. This is the long-term right answer.

### 3.5 How agents learn about each other's changes

| Approach | Latency | Mechanism |
|----------|---------|-----------|
| Polling task list | 1–5 seconds | Each agent checks after finishing; lead notifies on updates |
| Event bus / message queue | ~100ms | Agents post to shared queue; all agents subscribe (requires infra) |
| Shared file watcher | 1–10 seconds | Agent writes to `events.jsonl`; others stat-check for changes |
| Git push/pull | 5–30 seconds | Agent commits, pulls to see others' commits |

For NeoDemos, polling or file-watching against `events.jsonl` is plenty fast.
No agent is waiting on millisecond-latency inter-agent communication.

### 3.6 Conclusions that shape our architecture

1. Pure JSONL is elegant but offers zero enforcement — rogue agents can
   corrupt state. We need enforcement.
2. Pure SQLite is safe but opaque to git and harder for humans to audit.
3. The right answer is **both**: SQLite + JSONL, with PreToolUse hooks as the
   enforcement mechanism that prevents rogue writes.
4. Obsidian is for Dennis, not for agents.
5. Existing workstream handoff MDs are excellent and should be kept as-is —
   they're the "what to build." The coordination layer is the "what's happening."

---

## 4. Architecture: The Dual-Store Hybrid

### 4.1 High-level directory layout

```
NeoDemos/
├── .coordination/                       # NEW — the coordination layer
│   ├── state.db                         # SQLite — live state (not committed to git)
│   ├── events.jsonl                     # Append-only audit log (committed to git)
│   ├── state.md                         # Auto-generated human-readable dashboard
│   ├── dependencies.yaml                # Static dependency graph
│   ├── knowledge-map.yaml               # Links WS to research + architecture docs
│   ├── capabilities.yaml                # Which workers can pick up which WS
│   ├── approval_queue.jsonl             # Manager approval requests from Tier-3 hooks
│   ├── approval_decisions.jsonl         # Manager's responses
│   └── scripts/
│       ├── rebuild_state.py             # events.jsonl → state.db → state.md
│       ├── claim_task.py                # Atomic claim (writes to both stores)
│       ├── complete_task.py             # Atomic completion + unblock check
│       ├── next_task.py                 # Returns next unclaimed unblocked WS for a worker
│       ├── manager_review.py            # Manager's QA loop
│       └── approval_hook.py             # PreToolUse hook for Tier-3 routing
│
├── .claude/
│   ├── settings.json                    # Permissions (Tier 1 + 2) + hooks config
│   ├── hooks/                           # NEW — hook scripts
│   │   ├── pre_tool_use.py              # Enforcement: blocks bad writes, routes Tier-3
│   │   └── post_tool_use.py             # Logging: append every tool use to events.jsonl
│   ├── commands/                        # Expanded with coordination commands
│   │   ├── next-task.md                 # /next-task — worker claims next job
│   │   ├── complete-task.md             # /complete-task — marks current done
│   │   ├── status.md                    # /status — read state.md
│   │   ├── review.md                    # /review — Manager reviews a completion
│   │   ├── loop.md                      # /loop — run the claim → execute → clear cycle
│   │   └── [existing: rag, backup, deploy, etc.]
│   └── agents/                          # NEW — agent-type profiles (if using subagents)
│
├── docs/
│   ├── handoffs/WS*.md                  # KEEP — task definitions (the "what")
│   ├── architecture/*.md                # KEEP — design docs
│   ├── research/*.md                    # KEEP — investigations
│   └── phases/*.md                      # KEEP — archive
│
├── .agent/rules/                        # KEEP — expanded with coordination protocol
├── brain/                               # ARCHIVE — migrate to .coordination/
└── memory-bank/                         # ARCHIVE — migrate to .coordination/
```

### 4.2 Why the dual store

SQLite gives us:
- Atomic transactions — an agent cannot leave the state half-updated
- Unique constraints — two agents cannot both "claim" the same workstream
- Foreign keys — a task cannot be completed if it doesn't exist
- Write locks — concurrent writes serialize automatically

JSONL gives us:
- Git-friendly append-only merges — no conflicts on parallel writes
- Full audit trail — every state change has a timestamp and author
- Disaster recovery — SQLite can always be rebuilt from JSONL
- Human readability — Dennis can `tail -f` the log and see live activity

The enforcement happens via a **pre-tool-use hook** that intercepts any agent's
write attempt to `.coordination/*` and redirects it through the
`claim_task.py` / `complete_task.py` wrappers. These wrappers do the SQLite
write first (with its transactional guarantees), then append to JSONL. Both
succeed or both fail.

### 4.3 Data flow for a typical workstream completion

```
Worker (Sonnet) decides WS7 is complete
        │
        ▼
Worker runs /complete-task WS7 "OCR recovery hit 96.2%"
        │
        ▼
Slash command invokes complete_task.py
        │
        ├─ Open SQLite transaction
        ├─ UPDATE workstream SET status='review', completed_at=NOW WHERE id='WS7'
        ├─ Check: does any WS have WS7 as its last blocker?
        │     → Yes: WS1 now has dependencies satisfied
        │     → UPDATE workstream SET status='available' WHERE id='WS1'
        ├─ APPEND to events.jsonl: {"event":"completed", "ws":"WS7", ...}
        ├─ APPEND to events.jsonl: {"event":"unblocked", "ws":"WS1", ...}
        ├─ Commit transaction
        │
        ▼
Manager (Opus) watching events.jsonl sees completion event
        │
        ▼
Manager runs manager_review.py on WS7
        │
        ├─ Reads WS7 handoff MD (acceptance criteria)
        ├─ Reads recent commits in relevant code paths
        ├─ Reads any test output or eval results
        ├─ Judges: did worker actually satisfy criteria?
        │
        ├─ If YES: APPEND {"event":"qa_passed", "ws":"WS7", "reviewer":"manager"}
        │          UPDATE state: WS7.status = 'done'
        │
        └─ If NO: APPEND {"event":"qa_rejected", "ws":"WS7", "reasons":[...]}
                  UPDATE state: WS7.status = 'needs_rework'
                  (next worker picking it up reads the rejection reasons)
        │
        ▼
rebuild_state.py regenerates state.md
        │
        ▼
Dennis opens state.md in the morning — sees exactly what happened
```

### 4.4 What the agents read and write

| Who | Reads | Writes |
|-----|-------|--------|
| Worker (Sonnet) | `state.md`, handoff MD for claimed WS, `knowledge-map.yaml` entries for that WS | Code files in the workstream's scope, `events.jsonl` via wrapper scripts |
| Manager (Opus) | `events.jsonl` (watches for completions), handoff MDs, diffs, test results | `events.jsonl` via wrapper scripts (qa_passed / qa_rejected), `approval_decisions.jsonl` for Tier-3 decisions |
| Dennis | `state.md`, weekly events.jsonl summary, Manager's QA reports | Occasional manual events (claim, reassign, note) via slash commands |

Nobody writes to `state.md` directly — it's regenerated. Nobody writes to
`state.db` directly — it goes through wrappers. Nobody writes to `events.jsonl`
directly — same reason. This is enforced by hooks (see Section 7).

---

## 5. Topology: Manager + Workers

### 5.1 The rationale

Three things shape this design:

**Opus is expensive but irreplaceable for judgment.** Review, architectural
decomposition, and catching subtle bugs are exactly what Opus at max thinking
does best. Use it there.

**Sonnet is excellent at scoped execution.** Given a clear handoff with
acceptance criteria, Sonnet writes correct code. It's not worse than Opus for
this — it just needs clearer inputs. Your handoff MDs already provide those
inputs.

**Review is cheaper than generation.** Opus reading 500 lines of Sonnet's code
and judging it costs a fraction of Opus writing those 500 lines. The cost math
is strongly in favor of a small, expensive reviewer over many expensive workers.

### 5.2 Session configuration

Dennis runs these sessions inside Antigravity:

| Session | Model | Thinking | Role | Context priorities |
|---------|-------|----------|------|-------------------|
| Manager | Opus | max | QA review, Tier-3 approvals, decomposition of new WS | Handoffs, V0_2_BEAT_MAAT_PLAN, recent events.jsonl |
| Worker-FE | Sonnet | standard | Frontend workstreams (WS8-series) | WS8 handoffs, frontend skill cards, design tokens |
| Worker-Pipeline | Sonnet | standard | Pipeline and ingest (WS5a, WS7, WS10, WS11, WS12) | Relevant handoffs, `ingest.md`, `backup.md` |
| Worker-Data | Sonnet | standard | RAG / KG / MCP (WS1, WS3, WS4, WS6) | Relevant handoffs, `rag.md`, `mcp.md`, research docs |
| Worker-Finance | Sonnet | standard | Financial (WS2, WS2b) | Relevant handoffs, financial architecture docs |
| Worker-Ops | Sonnet | standard | Deploy, infra, cross-cutting | `deploy.md`, `secure.md`, WAY_OF_WORKING.md |

Each worker has a `capabilities.yaml` entry listing which workstreams it's
allowed to claim. The `next_task.py` script respects this — a frontend worker
will never claim a pipeline workstream.

### 5.3 What the Manager actually reviews

The Manager runs two distinct kinds of review:

**Post-hoc workstream review (the main job).** When a worker marks a workstream
as `review` status via `/complete-task`, the Manager:

1. Reads the handoff MD's `Acceptance criteria` checklist
2. Reads the `git diff` of all commits since the claim event
3. Reads any test output or eval numbers the worker reported
4. Reads the worker's completion notes
5. Runs the eval gate if one is defined (e.g., WS2 required 100% numeric
   accuracy on 30 questions)
6. Judges: does the output actually meet the criteria? Any regressions?
   Any hidden assumptions?
7. Appends `qa_passed` or `qa_rejected` with specific reasons

**Tier-3 permission reviews (the secondary job).** When a worker tries to do
something risky (see Section 7), the hook routes the request to the Manager's
`approval_queue.jsonl`. The Manager:

1. Reads the request context (what tool, what args, what WS, what reasoning)
2. Decides: allow, deny, or request more info
3. Writes the decision to `approval_decisions.jsonl`
4. The worker's hook was blocking on this file — proceeds or aborts accordingly

### 5.4 Rate limits and escalation

Workers can fail Manager QA. That's expected and fine. But there needs to be an
escalation rule so you don't burn tokens forever on a task Sonnet fundamentally
can't handle:

- If a WS fails Manager QA twice with the same worker, pause and escalate to
  Dennis (append `escalation` event, send notification, wait)
- If the Manager itself is uncertain (confidence threshold not met), escalate
  to Dennis rather than approving ambiguously
- If the approval queue grows beyond 5 pending items, pause all workers — this
  usually means the Manager is stuck or Dennis is away

---

## 6. Agentic Loops with Memory Reset

### 6.1 The loop

Each worker runs this loop continuously:

```
1. /next-task
   ├─ Script queries SQLite for available unclaimed workstreams
   │  matching this worker's capability tags
   ├─ If none: sleep 60 seconds, retry
   ├─ If found: claim it (write claim event, update SQLite)
   └─ Output: the workstream ID + handoff file path

2. Read the handoff MD cold
   ├─ Read "Cold-start prompt"
   ├─ Read "Files to read first"
   ├─ Read knowledge-map.yaml entries for this WS
   └─ Build mental model

3. Execute the workstream
   ├─ Write code
   ├─ Run tests
   ├─ Iterate until acceptance criteria met
   └─ Update handoff MD "Outcome" section

4. /complete-task WS<N> "<summary>"
   ├─ Script writes completion event, updates SQLite
   ├─ Checks for newly-unblocked workstreams
   └─ Manager will wake up and review

5. /clear
   ├─ Clears the worker's context window
   ├─ Next /next-task starts fresh
   └─ No contamination from previous workstream

6. GOTO 1
```

### 6.2 Why memory reset is essential

Without `/clear`, workers accumulate context from previous workstreams. This
causes several observable problems:

- **Hallucinated continuity.** The worker thinks it "knows" something about
  the current WS because it worked on a related one earlier. It confidently
  uses an API that doesn't exist in this scope.
- **Context window exhaustion.** Eventually the worker hits its limit and
  starts degrading.
- **Decision drift.** Earlier workstream decisions bleed into later ones, even
  when they shouldn't apply.

The handoff MDs were designed explicitly to be self-contained cold-starts. They
have a "Cold-start prompt," a "Files to read first" list, and all acceptance
criteria inline. This means `/clear` between tasks is not just safe — it's the
intended way to use the handoff system.

### 6.3 How workers call the loop in Antigravity

Each worker session gets the same initial instruction:

```
You are a NeoDemos worker. Your capabilities: [frontend / pipeline / data / finance / ops].
Run this loop continuously:

1. /next-task
2. Read the handoff, execute, run tests, update outcome
3. /complete-task <ws-id> "<summary>"
4. /clear
5. Repeat from step 1

Stop and alert Dennis only if:
- /next-task reports no available workstreams (for > 10 minutes)
- Manager QA has rejected the same WS twice
- A Tier-3 approval request is pending > 30 minutes
- You hit an error you cannot recover from
```

In Antigravity's chat UI, you paste this once per worker session. The session
then runs autonomously. Dennis checks in periodically rather than driving.

### 6.4 Edge cases and failure modes

- **Worker crashes mid-task.** The claim event has a timestamp; a sweeper
  script checks for claims older than N hours with no completion and releases
  them. Any worker can then re-claim.
- **Worker is in the middle of uncommitted changes when /clear fires.** The
  `/complete-task` command requires a commit as a prerequisite — if there are
  uncommitted changes, it refuses to proceed. This prevents work loss.
- **Two workers race on /next-task.** SQLite's unique constraint on `(ws_id,
  status='claimed')` prevents double-claim. The second worker gets an error and
  retries for the next available task.
- **Manager is offline when workers complete.** Completions queue up as `review`
  status. When Manager comes back, it processes the queue in order.

---

## 7. The Three-Tier Permission Model

### 7.1 The problem with flat approvals

Claude Code's default behavior is to ask the human for approval on most tool
calls. With 6 workers running, Dennis is clicking "approve" hundreds of times a
day. The vast majority of those approvals are for operations that are perfectly
safe (reading a file the handoff told the worker to read, running a test,
editing a file inside the worker's scope). The rare approvals that genuinely
matter (force push, drop table, deploy without staging) get lost in the noise.

The fix is to grade operations by risk and handle each tier differently.

### 7.2 The three tiers

**Tier 1 — Auto-approve via `settings.json`.** Pre-approved operations that
Dennis never sees. Read-only by default, plus scoped writes to project
directories where the worker is supposed to work. See Appendix B for the full
template.

Examples: `Read`, `Grep`, `Glob`, `Bash(ls:*)`, `Bash(git status:*)`,
`Bash(git diff:*)`, `Bash(python -m pytest:*)`, `Edit(services/**)`,
`Edit(pipeline/**)`, `Edit(docs/handoffs/WS*.md)`, `Write(brain/**)` (workers'
scratch space).

**Tier 2 — Auto-deny via hook.** Dangerous operations blocked outright. The
worker cannot do these even if it wants to. Blocked operations get logged as
`escalation` events and the worker must notify Dennis.

Examples: `Bash(rm -rf:*)`, `Bash(git push --force:*)`,
`Bash(git reset --hard:*)`, `Write(.env)`, `Write(.env.*)`,
`Bash(kamal deploy:*)` (must go through a staged deploy script),
`Bash(psql:*DROP TABLE:*)`, `Write(.coordination/state.db)` (only wrappers write
here), direct `Write(.coordination/events.jsonl)` (same).

**Tier 3 — Route to Manager.** The risky middle zone. The hook intercepts the
call, writes a request to `approval_queue.jsonl`, and blocks the worker while
waiting for the Manager's decision in `approval_decisions.jsonl` (typically
~30 seconds). The Manager has full context to decide.

Examples: `Bash(alembic upgrade:*)` (schema migrations),
`Bash(python scripts/seed_*:*)` (data seeds), `Edit(.claude/commands/*.md)`
(modifying worker skills), writes outside the current worker's claimed scope,
any operation touching Qdrant collections,
`Bash(rclone copy:* drive:*)` (backup to external).

### 7.3 Impact

On a typical day running 6 workers, the distribution is approximately:

- ~80% of tool calls: Tier 1 (invisible to Dennis)
- ~5%: Tier 2 (blocked, logged as escalations if triggered — usually means a
  bug in a worker's plan)
- ~15%: Tier 3 (Manager decides — Dennis sees a summary, not individual clicks)

Dennis's actual click load: near zero. He reviews the Manager's QA reports and
occasional escalations, not individual tool calls.

### 7.4 Why this is better than "auto-approve everything"

Antigravity (like most agentic IDEs) has a "YOLO mode" that auto-approves
everything. That's tempting but bad — it removes all safety, including the
Tier-2 rail against truly destructive operations. The three-tier model keeps
the guardrails where they matter (Tier 2) while eliminating the noise where
they don't (Tier 1), and uses the Manager as the arbiter of genuine judgment
calls (Tier 3).

---

## 8. Running This in Antigravity: CLI vs GUI

### 8.1 The short answer

Use the Antigravity GUI for running sessions and chatting with agents. Use the
Claude Code CLI (accessible from Antigravity's integrated terminal) for
everything else — configuration, hook installation, custom slash commands,
MCP setup, and coordination-layer initialization.

**Why:** the GUI is Antigravity's strength for interactive work but does not
expose all of Claude Code's configuration surface. Hooks, settings.json, and
custom commands are file-based and live in specific locations on disk. You
edit those files directly.

### 8.2 What the GUI handles well

- Spawning and naming Claude Code sessions (your 6 workers + 1 manager)
- Interactive chat with each agent
- Viewing file diffs as the agent works
- Tier-1 approvals that slip through (Antigravity shows them inline)
- Multi-session awareness (you can see all 7 sessions at once)
- Git integration (commits, branches, PR creation)

### 8.3 What must be done via CLI or direct file editing

These all live in specific files that you edit directly (either via the
Antigravity terminal or your favorite editor):

**`.claude/settings.json`** — your Tier-1 allowlist and Tier-2 denylist live
here. You edit this file directly. Antigravity has no GUI for it. After
editing, restart your Claude Code sessions for changes to take effect.

**`.claude/hooks/pre_tool_use.py` and `post_tool_use.py`** — the actual hook
scripts. These are Python files referenced from settings.json. Must be
executable (`chmod +x`). Must be tested from the CLI before relying on them in
live agent sessions.

**`.claude/commands/*.md`** — your custom slash commands (`/next-task`,
`/complete-task`, `/review`, `/loop`). Markdown files with a specific schema.
See Appendix D.

**`.coordination/` directory** — the entire coordination layer. Created
initially via a one-time bootstrap script you run from the CLI. After that,
agents interact with it automatically via the hooks and slash commands.

**`.mcp.json`** — if you want the coordination layer exposed as an MCP server
(for long-term cleanliness). This is configuration only; you edit the file.

### 8.4 Bootstrap sequence (run these once from the terminal)

```bash
# 1. Navigate to the project root
cd ~/path/to/NeoDemos

# 2. Create the coordination directory
mkdir -p .coordination/scripts

# 3. Run the initial state seed (populates SQLite + events.jsonl from current
#    handoff MDs — i.e. WS2 done, WS8 done, WS9 done, WS7/11/12 in progress, etc.)
python .coordination/scripts/bootstrap.py

# 4. Install hook scripts
mkdir -p .claude/hooks
cp <your_hooks>/*.py .claude/hooks/
chmod +x .claude/hooks/*.py

# 5. Update .claude/settings.json (Tier 1 allowlist + Tier 2 denylist + hook
#    registration). See Appendix B.

# 6. Install custom slash commands
#    (markdown files in .claude/commands/ — see Appendix D)

# 7. Verify the hook fires correctly
python .claude/hooks/pre_tool_use.py --test

# 8. Git-commit everything except state.db (which is local, rebuilt from jsonl)
echo ".coordination/state.db" >> .gitignore
git add .coordination/ .claude/ .gitignore
git commit -m "Bootstrap multi-agent coordination layer"
```

After this, all subsequent work happens through the GUI: open 7 Claude Code
chats (1 Manager + 6 Workers), paste the initial instruction for each session's
role, and start the loop.

### 8.5 Antigravity-specific tips

- **Session naming.** Name your sessions descriptively: "NeoDemos-Manager-Opus",
  "NeoDemos-Worker-FE-Sonnet", etc. Helps when 7 tabs are open.
- **Worktree isolation.** You already use git worktrees
  (`.claude/worktrees/agent-*/`). Keep this — it prevents workers from stepping
  on each other at the filesystem level while they edit code. The coordination
  layer lives in the main tree and is shared.
- **Model selection.** Set each session's model explicitly in Antigravity's
  session config. The Manager must be Opus (max thinking); workers are Sonnet.
- **Auto-resume on session crash.** Antigravity supports resuming sessions. If
  a worker crashes mid-loop, resuming it will pick up from its last command,
  which should naturally proceed to `/complete-task` or re-enter the loop.
- **Checkpoints.** Use Antigravity's checkpoint feature before running the
  initial bootstrap — gives you a clean revert point if something goes wrong.

### 8.6 What you cannot do in Antigravity (yet)

- Cross-session messaging is limited. The Manager cannot directly "send a
  message" to a Worker session. Coordination happens via the file layer
  (events.jsonl, approval_decisions.jsonl), and the Worker polls.
- You cannot script the spawning of sessions from outside the GUI in the
  current release. You open each of the 7 sessions manually, paste the role
  instruction, and start. This is a one-time setup cost per working day.

---

## 9. Implementation Phases

Phase 1 delivers real value in a single session. Each subsequent phase is
incremental and optional.

### Phase 1 — Minimum Viable Coordination (target: one afternoon)

Deliverables:
- `.coordination/` directory with events.jsonl, state.db (created), and
  bootstrap script
- `dependencies.yaml` populated for all 13 workstreams
- `rebuild_state.py` that generates `state.md` from events.jsonl
- `claim_task.py` and `complete_task.py` wrappers
- Seed the state with current WS statuses (from existing handoff READMEs)

Success criterion: Dennis can run `python .coordination/scripts/rebuild_state.py`
and `state.md` accurately reflects what every handoff file says today.

### Phase 2 — Slash Commands and Agent Protocol (target: one afternoon)

Deliverables:
- `.claude/commands/next-task.md`, `complete-task.md`, `status.md`
- Updated agent rules in `.agent/rules/` explaining the new protocol
- Test with one worker session claiming and completing one small workstream

Success criterion: a worker session can execute `/next-task`, read the
returned handoff, do work, and `/complete-task` — and `state.md` updates
correctly afterward.

### Phase 3 — Manager + QA Loop (target: one afternoon)

Deliverables:
- `manager_review.py` that watches events.jsonl and reviews completions
- `.claude/commands/review.md` (manager uses this to batch-review)
- Manager session instruction template
- Test: one worker ships a WS, Manager reviews and either approves or rejects

Success criterion: the Manager correctly catches a deliberately-introduced
regression in a test workstream and writes a `qa_rejected` event.

### Phase 4 — Permission Hooks (target: one afternoon)

Deliverables:
- `.claude/hooks/pre_tool_use.py` implementing Tier 1/2/3 logic
- Expanded `settings.json` with Tier-1 allowlist
- Tier-3 routing to `approval_queue.jsonl` → Manager → `approval_decisions.jsonl`
- Test: dangerous operation blocked, safe operation auto-approved,
  middle-zone operation routed to Manager

Success criterion: Dennis runs a full day of 6 workers and clicks approve
fewer than 10 times.

### Phase 5 — Knowledge Map + Migration (target: one afternoon)

Deliverables:
- `knowledge-map.yaml` linking workstreams to research and architecture docs
- Migration of `brain/` and `memory-bank/` — move relevant content into
  workstream MDs, archive the rest
- Update `docs/handoffs/README.md` to auto-generate the index table from
  state.db

Success criterion: a fresh worker picking up WS1 automatically loads
`docs/research/Neo4j.md` without being told.

### Phase 6 — Optional Polish

- Obsidian compatibility (wikilinks added to workstream MDs)
- Mermaid dependency diagram auto-generated in state.md
- MCP server exposing the coordination layer as tools
  (`coord_claim`, `coord_complete`, `coord_status`)
- Integration with CI so test failures auto-append `qa_rejected` events

---

## 10. Risks and Mitigations

**Risk: Hook bugs break all agents at once.** Mitigation: hooks are Python
scripts with explicit fallbacks — if the hook fails unexpectedly, it defaults
to "ask Dennis" (the current behavior), not "auto-deny." Test thoroughly with
`--test` flag before relying on them.

**Risk: SQLite corruption.** Mitigation: JSONL is the source of truth.
`rebuild_state.py` can recreate SQLite from JSONL at any time. Add a pre-commit
hook that verifies consistency.

**Risk: Workers ignore the protocol and edit handoff files directly.**
Mitigation: Tier-2 hook blocks direct writes to `.coordination/`; Tier-1
permits writes to handoff MDs (workers update their own Outcome sections), but
the claim/complete state lives in SQLite and is enforced there.

**Risk: Manager burns too many tokens on reviews.** Mitigation: Manager reviews
are triggered only by completion events, not by every tool call. A typical WS
review is ~5K tokens (diff + acceptance criteria + reasoning). At 3 WSs/day,
that's 15K tokens of Opus for review vs. ~500K tokens saved by running workers
on Sonnet instead of Opus.

**Risk: Escalation loops.** A WS fails QA → worker retries → fails again →
retries... Mitigation: hard stop after 2 failed QA cycles. Escalates to Dennis.

**Risk: events.jsonl grows unbounded.** Mitigation: quarterly rotation. Archive
old events to `events-2026Q1.jsonl` etc. SQLite is rebuilt from the current
active log.

**Risk: Dennis loses visibility.** Mitigation: state.md is always current.
Weekly "digest" script summarizes the week's events into a human-readable
report. Manager's QA reports are organized chronologically.

**Risk: Antigravity IDE changes break the setup.** Mitigation: everything is
file-based and git-versioned. If Antigravity changes, the underlying
coordination layer continues to work with any Claude Code frontend (Cursor,
terminal, etc.).

---

## Appendix A: Concrete File Schemas

### A.1 `events.jsonl` schema

Every line is a JSON object. Required fields: `ts`, `event`, `agent`. Other
fields depend on the event type.

```jsonl
{"ts": "2026-04-14T09:00:00Z", "agent": "Worker-Pipeline", "event": "claimed", "ws": "WS7", "capabilities": ["pipeline"]}
{"ts": "2026-04-14T09:00:00Z", "agent": "Worker-Pipeline", "event": "started", "ws": "WS7"}
{"ts": "2026-04-14T14:30:00Z", "agent": "Worker-Pipeline", "event": "completed", "ws": "WS7", "detail": "BM25 hit rate 96.2%", "commit": "abc123"}
{"ts": "2026-04-14T14:31:00Z", "agent": "system", "event": "unblocked", "ws": "WS1", "reason": "All dependencies satisfied: WS7, WS11, WS12"}
{"ts": "2026-04-14T14:45:00Z", "agent": "Manager", "event": "qa_passed", "ws": "WS7", "reviewer_notes": "Acceptance criteria met. Recovery above 95% target."}
{"ts": "2026-04-14T15:00:00Z", "agent": "Manager", "event": "qa_rejected", "ws": "WS7b", "reasons": ["Missing eval run", "Outcome section empty"], "action": "Worker must re-run eval and update outcome"}
{"ts": "2026-04-14T15:30:00Z", "agent": "Dennis", "event": "note", "ws": "WS11", "detail": "P1 ORI ingest slower than expected, but on track"}
{"ts": "2026-04-14T16:00:00Z", "agent": "Worker-Data", "event": "approval_requested", "ws": "WS4", "tool": "Bash", "command": "alembic upgrade head", "reason": "Need to apply new tool_descriptions schema"}
{"ts": "2026-04-14T16:00:30Z", "agent": "Manager", "event": "approval_granted", "request_id": "req-8f3a", "ws": "WS4", "conditions": "Run on staging schema first"}
{"ts": "2026-04-14T18:00:00Z", "agent": "system", "event": "escalation", "ws": "WS12", "reason": "QA rejected twice — manual review required"}
```

### A.2 `dependencies.yaml` schema

```yaml
WS1:
  depends_on: [WS7, WS11, WS12]
  description: "GraphRAG retrieval — Flair NER + Gemini enrichment"
  estimated_hours: 40

WS2:
  depends_on: []
  description: "Trustworthy financial analysis"
  estimated_hours: 20
  status_override: "done"  # Already shipped

WS2b:
  depends_on: [WS2]
  description: "IV3 taakveld FK backfill"
  estimated_hours: 8

# ... for all 13 workstreams
```

### A.3 `knowledge-map.yaml` schema

```yaml
WS1:
  research:
    - docs/research/Neo4j.md
    - docs/research/RAG_Beyond_RAG_Research_Report.md
    - docs/research/RAG_Optimal_Setup_for_MCP.md
  architecture:
    - docs/architecture/PLAN_I_LIGHTRAG_ENTITY_EXTRACTION.md
    - docs/architecture/PLAN_G_CONTEXTUAL_RETRIEVAL.md
  commands:
    - .claude/commands/rag.md
    - .claude/commands/mcp.md
  related_workstreams:
    - WS3  # Journeys use the same KG

WS5a:
  research: []
  architecture:
    - docs/architecture/EMBEDDING_PIPELINE_RUNBOOK.md
  commands:
    - .claude/commands/backup.md
    - .claude/commands/deploy.md
    - .claude/commands/ingest.md
  related_workstreams:
    - WS5b
    - WS13

# ... for all 13 workstreams
```

### A.4 `capabilities.yaml` schema

```yaml
Worker-FE:
  tags: [frontend, design, ux]
  allowed_workstreams: [WS8, WS8f, WS9]  # WS9 has FE components
  max_parallel: 1

Worker-Pipeline:
  tags: [pipeline, ingest, infra]
  allowed_workstreams: [WS5a, WS7, WS10, WS11, WS12]
  max_parallel: 1

Worker-Data:
  tags: [rag, kg, mcp, search]
  allowed_workstreams: [WS1, WS3, WS4, WS6]
  max_parallel: 1

Worker-Finance:
  tags: [financial, eval]
  allowed_workstreams: [WS2, WS2b]
  max_parallel: 1

Worker-Ops:
  tags: [deploy, infra, security, cross-cutting]
  allowed_workstreams: "*"  # Can pick up anything when idle, but low priority
  max_parallel: 1

Manager:
  tags: [review, decompose, approve]
  # Manager doesn't claim workstreams — reviews completions
```

### A.5 `state.md` schema (auto-generated)

```markdown
# NeoDemos Project State
> Auto-generated from .coordination/events.jsonl — do not edit manually
> Last rebuilt: 2026-04-14T18:00:00Z
> Version: 0.2.0-alpha.2

## Active Now
| WS | Title | Worker | Since | Detail |
|----|-------|--------|-------|--------|
| WS7 | OCR Recovery | Worker-Pipeline | 2026-04-13 09:00 | 2,700 docs — 96.2% hit rate |
| WS8f | Admin CMS | Worker-FE | 2026-04-13 14:00 | Wave 1: DB+CSS split |
| WS11 | Corpus Completeness | Worker-Pipeline (queued) | — | P1 ORI running in bg |
| WS12 | Virtual Notulen | Dennis | 2026-04-13 11:00 | Backfill + harden |

## Blocked
| WS | Title | Waiting On | Unblocks When |
|----|-------|------------|---------------|
| WS1 | GraphRAG | WS7, WS11, WS12 | All three complete QA |

## Available (unclaimed, no blockers)
| WS | Title | Capability Required |
|----|-------|---------------------|
| WS4 | MCP Discipline | data |
| WS5a | Nightly Pipeline | pipeline/ops |

## In QA Review
| WS | Worker | Submitted | Manager |
|----|--------|-----------|---------|
| (none) | | | |

## Recently Completed (last 7 days)
| WS | Title | Completed | Worker | QA |
|----|-------|-----------|--------|----|
| WS9 | Web Intelligence | 2026-04-13 | Worker-FE | ✅ passed |
| WS8a-e | Frontend | 2026-04-12 | Worker-FE | ✅ passed |
| WS2 | Financial | 2026-04-12 | Worker-Finance | ✅ passed |

## Escalations (need Dennis)
| WS | Reason | Since |
|----|--------|-------|
| (none) | | |

## Dependency Graph
(mermaid diagram auto-generated here)
```

---

## Appendix B: Settings.json Tier-1 Allowlist Template

This is a starting point. Expand as you observe which approvals keep coming up.

```json
{
  "permissions": {
    "allow": [
      "Read",
      "Grep",
      "Glob",
      "LS",

      "Bash(ls:*)",
      "Bash(cat:*)",
      "Bash(head:*)",
      "Bash(tail:*)",
      "Bash(wc:*)",
      "Bash(find:*)",
      "Bash(grep:*)",
      "Bash(tree:*)",

      "Bash(git status:*)",
      "Bash(git diff:*)",
      "Bash(git log:*)",
      "Bash(git branch:*)",
      "Bash(git show:*)",
      "Bash(git blame:*)",
      "Bash(git stash:*)",
      "Bash(git add:*)",
      "Bash(git commit:*)",
      "Bash(git checkout:*)",

      "Bash(python -m pytest:*)",
      "Bash(pytest:*)",
      "Bash(python -c:*)",
      "Bash(python -m json.tool:*)",
      "Bash(pip list:*)",
      "Bash(pip show:*)",

      "Bash(npm list:*)",
      "Bash(npm test:*)",
      "Bash(npm run:*)",

      "Bash(docker ps:*)",
      "Bash(docker logs:*)",

      "Bash(psql -c:*SELECT*:*)",
      "Bash(psql -c:*\\\\d*:*)",

      "Edit(services/**)",
      "Edit(pipeline/**)",
      "Edit(scripts/**)",
      "Edit(eval/**)",
      "Edit(rag_evaluator/**)",
      "Edit(docs/handoffs/WS*.md)",
      "Edit(docs/phases/**)",
      "Edit(brain/**)",
      "Edit(tests/**)",

      "Write(docs/handoffs/WS*.md)",
      "Write(tests/**)",
      "Write(brain/**)"
    ],
    "deny": [
      "Bash(rm -rf:*)",
      "Bash(rm -r:*)",
      "Bash(git push --force:*)",
      "Bash(git push -f:*)",
      "Bash(git reset --hard:*)",
      "Bash(git clean -fd:*)",
      "Bash(sudo:*)",
      "Bash(chmod 777:*)",
      "Bash(curl:*| bash*)",
      "Bash(wget:*| bash*)",

      "Write(.env)",
      "Write(.env.*)",
      "Edit(.env)",
      "Edit(.env.*)",

      "Write(.coordination/state.db)",
      "Write(.coordination/events.jsonl)",

      "Bash(kamal deploy:*)",
      "Bash(kamal rollback:*)",
      "Bash(*DROP TABLE*)",
      "Bash(*DROP DATABASE*)",
      "Bash(*TRUNCATE*)"
    ]
  },
  "hooks": {
    "PreToolUse": ".claude/hooks/pre_tool_use.py",
    "PostToolUse": ".claude/hooks/post_tool_use.py"
  }
}
```

**Note:** The above is suggestive. Test carefully. `deny` takes precedence over
`allow`. Anything not matched by either goes to the hook for Tier-3 routing.

---

## Appendix C: Hook Examples

### C.1 `pre_tool_use.py` skeleton

```python
#!/usr/bin/env python3
"""
Pre-tool-use hook: enforces Tier 2 (deny) and routes Tier 3 (manager review).
Tier 1 (allow) is handled by settings.json and never reaches this script.
"""
import json
import sys
import time
from pathlib import Path

COORDINATION_DIR = Path(".coordination")
APPROVAL_QUEUE = COORDINATION_DIR / "approval_queue.jsonl"
APPROVAL_DECISIONS = COORDINATION_DIR / "approval_decisions.jsonl"
TIER3_TIMEOUT_SECONDS = 120

# These patterns route to Tier 3 (Manager review)
TIER3_PATTERNS = [
    "alembic upgrade",
    "alembic downgrade",
    ".claude/commands/",
    "rclone copy",
    "python scripts/seed_",
    "qdrant_client",
]

def request_payload():
    return json.load(sys.stdin)

def is_tier3(tool_name, tool_input):
    serialized = json.dumps({"tool": tool_name, "input": tool_input})
    return any(pat in serialized for pat in TIER3_PATTERNS)

def request_manager_approval(payload):
    request_id = f"req-{int(time.time())}-{payload.get('session_id', 'unknown')[:6]}"
    request = {
        "request_id": request_id,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": payload.get("session_id"),
        "tool": payload.get("tool_name"),
        "input": payload.get("tool_input"),
        "context": payload.get("context", ""),
    }
    with open(APPROVAL_QUEUE, "a") as f:
        f.write(json.dumps(request) + "\n")

    # Block until decision appears
    start = time.time()
    while time.time() - start < TIER3_TIMEOUT_SECONDS:
        if APPROVAL_DECISIONS.exists():
            with open(APPROVAL_DECISIONS) as f:
                for line in f:
                    decision = json.loads(line)
                    if decision.get("request_id") == request_id:
                        return decision
        time.sleep(2)

    return {"decision": "timeout", "reason": "Manager did not respond in 120s"}

def main():
    payload = request_payload()
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input", {})

    # Check Tier 3
    if is_tier3(tool_name, tool_input):
        decision = request_manager_approval(payload)
        if decision.get("decision") == "approved":
            print(json.dumps({"permissionDecision": "allow"}))
            return
        else:
            print(json.dumps({
                "permissionDecision": "deny",
                "reason": f"Manager denied: {decision.get('reason', 'no reason given')}",
            }))
            return

    # Otherwise fall through to default (Claude Code asks Dennis)
    print(json.dumps({"permissionDecision": "ask"}))

if __name__ == "__main__":
    main()
```

### C.2 `post_tool_use.py` — logging every tool call

```python
#!/usr/bin/env python3
"""Post-tool-use hook: append every tool call to events.jsonl for audit."""
import json
import sys
import time
from pathlib import Path

EVENTS_LOG = Path(".coordination/events.jsonl")

def main():
    payload = json.load(sys.stdin)
    event = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "agent": payload.get("session_id", "unknown"),
        "event": "tool_use",
        "tool": payload.get("tool_name"),
        "success": payload.get("success", True),
    }
    with open(EVENTS_LOG, "a") as f:
        f.write(json.dumps(event) + "\n")

if __name__ == "__main__":
    main()
```

---

## Appendix D: Slash Commands for the Loop

Claude Code custom slash commands are markdown files in `.claude/commands/`.
Each file is a prompt that the command expands into.

### D.1 `.claude/commands/next-task.md`

```markdown
Run the Python script `python .coordination/scripts/next_task.py --worker $WORKER_NAME`.

Parse the output. It will either:
- Return a workstream ID (e.g., "WS7") and the path to the handoff file
- Return "no_tasks" if nothing is available

If a workstream is returned:
1. Read the handoff file completely
2. Read all files listed in `knowledge-map.yaml` for this workstream
3. Read the "Cold-start prompt" section and follow it
4. Begin execution

If "no_tasks":
- Wait 5 minutes
- Re-run /next-task
- If still no tasks after 3 retries, notify Dennis
```

### D.2 `.claude/commands/complete-task.md`

```markdown
You are completing workstream $ARG1 with summary "$ARG2".

Before marking complete, verify:
1. All acceptance criteria in the handoff are met
2. All code changes are committed (run `git status`)
3. The handoff file's "Outcome" section is updated with what shipped
4. Any eval gate has been run and results are captured

If any of these fail, DO NOT proceed. Report what's missing.

If all checks pass, run:
`python .coordination/scripts/complete_task.py --ws $ARG1 --summary "$ARG2"`

The script will:
- Write completion event to events.jsonl
- Update state.db
- Check for newly-unblocked workstreams
- Notify the Manager to begin QA review

After the script succeeds, run `/clear` to reset your context, then `/next-task`.
```

### D.3 `.claude/commands/status.md`

```markdown
Read and display the current contents of `.coordination/state.md`.

Then parse the last 20 entries of `.coordination/events.jsonl` and show them
chronologically, most recent first.
```

### D.4 `.claude/commands/review.md` (Manager only)

```markdown
You are the NeoDemos Manager. Review the next pending completion in QA.

Run `python .coordination/scripts/next_review.py` to get the next workstream
awaiting QA.

For that workstream:
1. Read the handoff MD's "Acceptance criteria" section
2. Run `git log --since="<claim timestamp>" --pretty=format:"%h %s" -- <scope>`
   to see what was committed during this workstream
3. For each acceptance criterion, verify it was met
4. If an eval gate is defined, re-run it or verify the worker's results
5. Check for regressions: run relevant test suites

Then decide:
- If all criteria met with no regressions: run
  `python .coordination/scripts/qa.py --ws <ID> --decision passed --notes "..."`
- If criteria not met: run
  `python .coordination/scripts/qa.py --ws <ID> --decision rejected --reasons "..."`

After the decision is recorded, run `/review` again for the next pending item,
or stop if the queue is empty.
```

### D.5 `.claude/commands/loop.md`

```markdown
You are a NeoDemos worker in continuous loop mode.

Run this cycle repeatedly until Dennis stops you:

1. Execute /next-task
2. Read the returned handoff and any linked knowledge
3. Execute the workstream according to its cold-start prompt
4. When done, execute /complete-task <ws-id> "<summary>"
5. Execute /clear
6. Return to step 1

Stop and notify Dennis if any of these conditions occur:
- /next-task reports "no_tasks" three times in a row
- You encounter an error you cannot recover from
- A Tier-3 approval has been pending for more than 30 minutes
- The Manager has rejected your work on the same WS twice

Your capability profile is: $WORKER_CAPABILITIES

Begin now.
```

---

## Appendix E: Migration of Existing Files

This is the proposed mapping of today's files to the new system.

| Current location | Destination | Action |
|------------------|-------------|--------|
| `docs/handoffs/WS*.md` | Unchanged | KEEP — these are the task definitions |
| `docs/handoffs/README.md` | Simplified | Auto-generate index table from state.db; keep parallelism narrative |
| `docs/architecture/*.md` | Unchanged | KEEP — link via knowledge-map.yaml |
| `docs/research/*.md` | Unchanged | KEEP — link via knowledge-map.yaml |
| `docs/phases/*.md` | Unchanged | KEEP — archive |
| `docs/WAY_OF_WORKING.md` | Unchanged | KEEP |
| `.agent/rules/*.md` | Expanded | ADD: coordination protocol (worker loop, manager behavior, hook expectations) |
| `.claude/commands/*.md` | Expanded | ADD: next-task, complete-task, status, review, loop |
| `.claude/settings.json` | Updated | REWRITE: Tier-1 allowlist, Tier-2 denylist, hook registration |
| `memory-bank/projectbrief.md` | Archive + link | Move north-star content to `.coordination/PROJECT.md` |
| `memory-bank/progress.md` | Replaced | Superseded by `state.md` (auto-generated) |
| `memory-bank/activeContext.md` | Archive | Superseded by state.md + events.jsonl |
| `memory-bank/productContext.md` | Keep + move | Relocate to `docs/PRODUCT_CONTEXT.md` |
| `memory-bank/techContext.md` | Keep + move | Relocate to `docs/architecture/TECH_CONTEXT.md` |
| `memory-bank/systemPatterns.md` | Keep + move | Relocate to `docs/architecture/SYSTEM_PATTERNS.md` |
| `.coordination/FEEDBACK_LOG.md` | Replaced | Triage items become `note` events in events.jsonl |
| `brain/implementation_plan.md` | Replaced | Superseded by handoff MDs |
| `brain/task.md` | Replaced | Superseded by next-task flow |
| `brain/walkthrough.md` | Keep + move | Relocate to `docs/WALKTHROUGH.md` |
| `brain/historical_ingestion_plan.md` | Archive | Move to `docs/archive/` |
| `brain/roadmap_and_progress.md` | Replaced | Superseded by state.md + V0_2_BEAT_MAAT_PLAN.md |
| `brain/feyenoord_city_deep_dive.md` | Keep + move | Relocate to `docs/research/` |
| `TODOS.md` | Replaced | Items become `note` events; operational tasks get handoff MDs |
| `AGENT_HANDOFF.md` (root) | Archive | Historical record of March handoff — not needed |
| `CHANGELOG.md` | Keep | Still useful for tagged releases |

The migration should happen workstream-by-workstream, not big-bang. Phase 1
introduces the coordination layer without touching existing files. Phase 5
does the archival work once the new system has proven itself.

---

## End of master document

**Next step:** If Dennis approves the design, an implementation agent can pick
up Phase 1 from Section 9 and begin building. All required schemas, hook
examples, and slash command templates are in the appendices — nothing needs to
be designed from scratch.
