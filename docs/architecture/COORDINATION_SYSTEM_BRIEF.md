# NeoDemos Agent Coordination System — Build Brief

> **Purpose:** This document is a complete instruction for a Claude Code agent to
> design and plan a unified coordination system for the NeoDemos project.
> The system replaces the current fragmented approach (scattered MDs, memory-bank,
> brain/, handoffs/) with a single source of truth that multiple parallel agents
> can read from and write to without conflicts.
>
> **Do NOT start coding.** Your deliverable is a detailed implementation plan
> (saved to `docs/architecture/COORDINATION_SYSTEM_PLAN.md`) that Dennis can
> review before any code is written.

---

## 1. Context: Who You're Building For

**Dennis** is a banker and former Rotterdam city councillor building NeoDemos —
an AI-powered democracy tool for Dutch municipal councils. He is tech-savvy but
not a developer. He runs **6 Claude Code sessions in parallel** via the
Antigravity IDE, each working on a separate workstream of the project.

**The project** is at version `0.2.0-alpha.2`, has 13 workstreams (WS1–WS13),
a PostgreSQL + Qdrant backend on Hetzner, and a FastAPI + vanilla JS frontend.
The full architecture is documented in `docs/architecture/V0_2_BEAT_MAAT_PLAN.md`.

---

## 2. The Problem We're Solving

The current coordination system uses separate markdown files that don't talk to
each other. When one agent finishes a task, the consequences must be manually
propagated:

### What exists today (and where it lives):

| Layer | Location | Purpose | Pain point |
|-------|----------|---------|------------|
| Workstream definitions | `docs/handoffs/WS*.md` (13 files) | Task specs, acceptance criteria, cold-start prompts | Status updates require editing each file manually; dependency unblocking is not automatic |
| Parallelism map | `docs/handoffs/README.md` | Shows which workstreams can run in parallel | Static ASCII art — goes stale when statuses change |
| Memory bank | `memory-bank/*.md` (7 files) | Persistent project context for agents | Duplicates info from handoffs; agents read both and get confused |
| Brain folder | `brain/*.md` (7 files) | Working notes, feedback log, implementation plans | Unstructured dumping ground; triage ritual can't keep up |
| Agent rules | `.agent/rules/*.md` | Communication style, lessons learned | Fine as-is (small, stable) |
| Command skills | `.claude/commands/*.md` | Quick-reference for RAG, deploy, backup, etc. | Fine as-is (stable reference material) |
| Research docs | `docs/research/*.md` | Investigations, benchmarks, external research | Disconnected from workstreams that use them |
| Architecture docs | `docs/architecture/*.md` | System design, plans, technical decisions | No link to which workstream implements which design |
| Phase reports | `docs/phases/*.md` | Historical completion reports | Archive — no action needed |
| TODOS.md | Root | Triage inbox | Works for Dennis, but agents can't reliably parse it |

### The core failures:

1. **No automatic dependency propagation.** When WS7 finishes, nothing tells
   WS1 it's unblocked. An agent or Dennis must manually edit WS1's status.

2. **Status is scattered.** To know "what's happening right now?" you must read
   13 handoff files, TODOS.md, and the parallelism map. There is no single query
   that returns the current state.

3. **Race conditions on parallel writes.** Two agents editing the same handoff
   README or TODOS.md can overwrite each other's changes.

4. **Knowledge is disconnected.** Research docs, architecture decisions, and
   workstream tasks exist in separate folders with no cross-references. An agent
   working on WS1 (GraphRAG) doesn't automatically know about
   `docs/research/Neo4j.md` unless someone tells it.

5. **Cleanup burden.** After a workstream ships, Dennis or an agent must update:
   the handoff file status, the README index table, the parallelism map, the
   memory-bank progress file, the brain implementation plan, and CHANGELOG.md.
   That's 6+ files for one status change.

---

## 3. Design Requirements

### 3.1 Non-Negotiable Constraints

- **File-based.** The system must work as files in the git repo. No external
  services, no servers, no Docker containers. Claude Code agents interact via
  file reads and writes only.

- **Append-only event log.** State changes (task claimed, task completed,
  dependency unblocked, status changed) must be appended to a log — never
  overwritten. This prevents race conditions when 6 agents write simultaneously.
  Format: JSONL (one JSON object per line).

- **Git-friendly.** All files must merge cleanly in git. JSONL appends merge
  trivially. No binary formats. No SQLite (binary, merge-hostile).

- **Backward compatible.** The existing workstream MD files are excellent task
  definitions. Don't replace them — augment them. The MD files become the
  "constitution" (what to do); the coordination layer becomes the "live state"
  (what's happening now).

- **Human readable.** Dennis must be able to open any file and understand it
  without tooling. Markdown + JSONL, not databases.

- **Agent-bootstrappable.** A fresh Claude Code session must be able to
  understand the full project state by reading at most 2-3 files (not 20+).

### 3.2 Functional Requirements

1. **Single status query.** One file or command that shows: all workstreams,
   their current status, who's working on them, what's blocked, and what just
   changed. Think of this as the "dashboard file."

2. **Automatic dependency resolution.** When an agent appends
   `{"event": "completed", "workstream": "WS7"}` to the event log, the system
   (or the next agent that reads it) can determine that WS1 is now unblocked
   (because WS7 was WS1's last remaining blocker).

3. **Task claiming.** An agent can claim a workstream by appending to the log.
   Other agents reading the log see the claim and skip that workstream.

4. **Knowledge linking.** Research docs, architecture decisions, and workstream
   tasks should be cross-referenced. When an agent picks up WS1, it should
   know that `docs/research/Neo4j.md` and
   `docs/architecture/PLAN_I_LIGHTRAG_ENTITY_EXTRACTION.md` are relevant.

5. **Change propagation script.** A lightweight script (Python or bash) that
   reads the event log and regenerates the dashboard file. This can be run by
   any agent after appending events, or by Dennis manually.

6. **Consolidation of brain/ and memory-bank/.** These overlap with handoffs
   and create confusion. The plan should specify what gets migrated where and
   what gets archived.

### 3.3 Nice-to-Have

- **Obsidian compatibility.** The knowledge files could use `[[wikilinks]]` so
  Dennis can optionally browse them in Obsidian. But this is cosmetic — don't
  let it drive the architecture.

- **MCP tool integration.** A future MCP tool (`project_status`) that queries
  the coordination system. Not in scope for v1 but design for it.

- **Mermaid dependency graph.** Auto-generated from the event log / dependency
  definitions. Can be rendered in VS Code, Obsidian, or GitHub.

---

## 4. Proposed Architecture (Validate and Refine This)

This is a starting point. Your plan should validate, critique, and improve it.

```
neodemos/
├── .coordination/                    # NEW — the coordination layer
│   ├── events.jsonl                  # Append-only event log (source of truth)
│   ├── state.md                      # Auto-generated dashboard (rebuilt from events.jsonl)
│   ├── dependencies.yaml             # Static dependency graph (WS1 depends on WS7, WS11, WS12)
│   ├── knowledge-map.yaml            # Links workstreams ↔ research docs ↔ architecture docs
│   └── scripts/
│       ├── rebuild_state.py          # Reads events.jsonl → regenerates state.md
│       ├── claim_task.py             # Agent helper: append claim event + verify no conflict
│       └── check_unblocked.py        # After a completion event, check if any WS is now unblocked
│
├── docs/
│   ├── handoffs/WS*.md              # KEEP — task definitions (the "what")
│   ├── architecture/*.md            # KEEP — design docs
│   ├── research/*.md                # KEEP — investigations
│   ├── phases/*.md                  # KEEP — archive
│   └── WAY_OF_WORKING.md           # KEEP
│
├── .agent/rules/*.md                # KEEP — agent behavior guidelines
├── .claude/commands/*.md            # KEEP — skill references
│
├── brain/ → ARCHIVE or MIGRATE      # Consolidate into .coordination/
├── memory-bank/ → ARCHIVE or MIGRATE # Consolidate into .coordination/
└── TODOS.md → MIGRATE               # Triage items move to events.jsonl
```

### Event log format (events.jsonl)

Each line is a self-contained JSON object:

```jsonl
{"ts": "2026-04-12T14:30:00Z", "agent": "CC-session-3", "event": "completed", "ws": "WS2", "detail": "Shipped: vraag_begrotingsregel + vergelijk_begrotingsjaren, 100% numeric accuracy"}
{"ts": "2026-04-12T18:00:00Z", "agent": "CC-session-1", "event": "completed", "ws": "WS8a-e", "detail": "Design tokens, landing, calendar, subpages, polish. Lighthouse 90+/95+"}
{"ts": "2026-04-13T09:00:00Z", "agent": "CC-session-2", "event": "claimed", "ws": "WS8f", "detail": "Starting Wave 1: DB schema + CSS split"}
{"ts": "2026-04-13T16:00:00Z", "agent": "CC-session-5", "event": "completed", "ws": "WS9", "detail": "18 MCP tools, SSE streaming, IP rate limiting. Commit b3104c3"}
{"ts": "2026-04-14T10:00:00Z", "agent": "CC-session-4", "event": "blocked", "ws": "WS1", "blocker": ["WS7", "WS11", "WS12"], "detail": "Cannot start enrichment until corpus work completes"}
{"ts": "2026-04-14T11:00:00Z", "agent": "Dennis", "event": "note", "ws": "WS11", "detail": "P1 ORI ingest running, 62,627 docs classified"}
```

### Dependencies file (dependencies.yaml)

```yaml
# Static dependency definitions — rarely changes
# Format: workstream -> list of workstreams that must be "completed" before it can start

WS1:
  depends_on: [WS7, WS11, WS12]
  description: "GraphRAG retrieval — Flair NER + Gemini enrichment"

WS2b:
  depends_on: [WS2]
  description: "IV3 taakveld FK backfill"

WS3:
  depends_on: [WS1]
  description: "Document journey timelines"

WS5b:
  depends_on: [WS5a]
  description: "Multi-portal connectors"

WS8f:
  depends_on: [WS8]
  description: "Admin panel + CMS"

WS13:
  depends_on: [WS5a]
  description: "Multi-gemeente pipeline"

# Workstreams with no dependencies (can start anytime):
# WS4, WS5a, WS6, WS7, WS8, WS9, WS10, WS11, WS12
```

### Knowledge map (knowledge-map.yaml)

```yaml
# Links workstreams to relevant knowledge artifacts
# Agents read this to know what context to load before starting work

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

WS2:
  architecture:
    - docs/architecture/FINANCIAL_DATA_DOCLING_UPGRADE.md
  # Add more as discovered

WS5a:
  commands:
    - .claude/commands/backup.md
    - .claude/commands/deploy.md
  architecture:
    - docs/architecture/EMBEDDING_PIPELINE_RUNBOOK.md

# ... fill in for all workstreams
```

### Dashboard (state.md) — auto-generated

```markdown
# NeoDemos Project State
> Auto-generated from .coordination/events.jsonl — do not edit manually
> Last rebuilt: 2026-04-14T11:05:00Z

## Active Now
| WS | Title | Agent | Since | Notes |
|----|-------|-------|-------|-------|
| WS7 | OCR Recovery | Dennis | 2026-04-13 | 2,700 docs |
| WS8f | Admin CMS | CC-session-2 | 2026-04-13 | Wave 1: DB+CSS |
| WS11 | Corpus Completeness | CC-session-6 | 2026-04-12 | P1 ORI running |
| WS12 | Virtual Notulen | Dennis | 2026-04-13 | Backfill + harden |

## Blocked
| WS | Title | Waiting On | Unblocked When |
|----|-------|------------|----------------|
| WS1 | GraphRAG | WS7, WS11, WS12 | All three complete |

## Available (no blockers, unclaimed)
| WS | Title | Dependencies |
|----|-------|-------------|
| WS4 | MCP Discipline | none |
| WS5a | Nightly Pipeline | none |

## Recently Completed
| WS | Title | Completed | Agent |
|----|-------|-----------|-------|
| WS9 | Web Intelligence | 2026-04-13 | CC-session-5 |
| WS8a-e | Frontend Redesign | 2026-04-12 | CC-session-1 |
| WS2 | Financial Analysis | 2026-04-12 | CC-session-3 |

## Dependency Graph
(mermaid diagram auto-generated here)
```

---

## 5. What Your Plan Must Cover

Produce a detailed plan in `docs/architecture/COORDINATION_SYSTEM_PLAN.md` with
the following sections:

### 5.1 Architecture Decision

- Validate or improve the proposed architecture above
- Explain why each component exists and what problem it solves
- If you recommend changes, explain the trade-off

### 5.2 Event Log Design

- Final JSONL schema (all event types: claimed, completed, blocked, unblocked,
  note, paused, resumed, dependency_added, dependency_removed)
- How agents should write to it (raw append? helper script? Claude Code command?)
- How to handle the bootstrap: seed events.jsonl with the current state from
  existing handoff files (WS2 done, WS8 done, WS9 done, WS7/11/12 in progress, etc.)

### 5.3 Dependency Resolution Logic

- How `rebuild_state.py` determines blocked/unblocked status
- Edge cases: what if a WS is re-opened after being marked done? What if a
  dependency is added mid-flight?
- How the Mermaid dependency graph is generated

### 5.4 Knowledge Map Design

- How to populate the initial knowledge-map.yaml from existing docs
- Whether [[wikilinks]] should be added to workstream MDs for Obsidian
  compatibility (and the exact syntax to use)
- How agents discover relevant context when picking up a workstream

### 5.5 Migration Plan

- What happens to `brain/` — which files move where, which get archived
- What happens to `memory-bank/` — which files move where, which get archived
- What happens to `TODOS.md` — migrate to events.jsonl or keep separate
- What happens to the handoff README.md index table and parallelism map —
  should these be auto-generated from the coordination layer?
- Step-by-step migration sequence that doesn't break running agents

### 5.6 Agent Protocol

- Exact instructions to add to `.agent/rules/` (or CLAUDE.md if we create one)
  that tell every agent how to interact with the coordination system
- The "cold start" flow: what does an agent read first?
  (Proposed: `.coordination/state.md` → claim a WS → read its handoff MD →
  read knowledge-map entries → start working)
- The "task completion" flow: what does an agent do when it finishes?
  (Proposed: append completion event → run rebuild_state.py → verify state.md
  updated → update handoff MD outcome section → commit)

### 5.7 Scripts Specification

- `rebuild_state.py` — inputs, outputs, logic, error handling
- `claim_task.py` — how it prevents double-claiming (file locking? optimistic
  check + append?)
- `check_unblocked.py` — reads dependencies.yaml + events.jsonl, outputs
  newly-unblocked workstreams
- Whether these should be standalone scripts or Claude Code commands
  (`.claude/commands/`)

### 5.8 Obsidian Compatibility (Optional Layer)

- If Dennis wants to browse the knowledge base in Obsidian, what changes?
- Can `state.md` and the knowledge map be made Obsidian-friendly without
  breaking the agent workflow?
- Recommendation: worth it or not, given the additional complexity?

### 5.9 Implementation Phases

Break the build into phases that can be done incrementally:

- **Phase 1:** Event log + dependencies.yaml + rebuild_state.py
  (minimum viable coordination)
- **Phase 2:** Knowledge map + agent protocol rules
- **Phase 3:** Migration of brain/ and memory-bank/
- **Phase 4:** Obsidian compatibility + Mermaid diagrams
- **Phase 5:** MCP tool (`project_status`) for querying from any agent

### 5.10 Risks and Mitigations

- What could go wrong?
- What if events.jsonl gets large? (rotation strategy)
- What if rebuild_state.py has a bug and generates wrong state.md?
- What if an agent ignores the protocol and edits handoff files directly?

---

## 6. Files To Read First

Before writing the plan, read these files to understand the current system:

1. `docs/handoffs/README.md` — current coordination structure
2. `docs/architecture/V0_2_BEAT_MAAT_PLAN.md` — master plan (long — skim the
   structure, read the workstream summaries)
3. `.agent/rules/communication_guidelines.md` — current agent rules
4. `memory-bank/projectbrief.md` — project north star
5. `memory-bank/progress.md` — what's been done
6. `memory-bank/activeContext.md` — current session state
7. `.coordination/FEEDBACK_LOG.md` — the triage inbox
8. `brain/implementation_plan.md` — active build status
9. `TODOS.md` — current triage system
10. `docs/WAY_OF_WORKING.md` — development workflow
11. At least 2-3 workstream handoff files (e.g. WS1, WS7, WS8f) to understand
    the format in practice

---

## 7. Deliverable

Save your plan to: `docs/architecture/COORDINATION_SYSTEM_PLAN.md`

The plan should be detailed enough that a separate Claude Code agent could pick
it up and implement Phase 1 without asking clarifying questions.

Do not write any code. Do not modify any existing files. Only produce the plan.

---

## 8. Success Criteria

A good plan will:

- [ ] Be immediately understandable to a non-developer (Dennis)
- [ ] Reduce the number of files an agent must update on task completion from 6+ to 2 (event log + handoff outcome)
- [ ] Eliminate race conditions on parallel writes
- [ ] Make dependency unblocking automatic (or near-automatic)
- [ ] Preserve the excellent workstream handoff format that already works well
- [ ] Be implementable in phases, with Phase 1 delivering value within a single session
- [ ] Not require any external tools, services, or databases
- [ ] Be compatible with Claude Code, Antigravity IDE, and optionally Obsidian
