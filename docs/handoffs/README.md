# NeoDemos v0.2 Workstream Handoffs

> **Purpose:** Each file in this directory is a self-contained agent-ready handoff for one workstream of the v0.2 Beat-MAAT plan. An agent (human or LLM) should be able to pick up a single handoff cold, do the work, and ship it without reading the master plan.
>
> **Master plan:** [`../architecture/V0_2_BEAT_MAAT_PLAN.md`](../architecture/V0_2_BEAT_MAAT_PLAN.md)
> **Roadmap:** [`../VERSIONING.md`](../VERSIONING.md)
> **Way of working:** [`../WAY_OF_WORKING.md`](../WAY_OF_WORKING.md)

---

## Handoff conventions

Every handoff file follows the same shape:

1. **TL;DR** — one paragraph an owner can read in 10 seconds
2. **Status** — `not started` / `in progress` / `blocked` / `review` / `done`
3. **Owner** — who's running it (default `unassigned`)
4. **Dependencies** — other handoffs that must finish first, or memory files to read
5. **Cold-start prompt** — copy-paste this directly into a fresh agent invocation
6. **Files to read first** — before touching code
7. **Build tasks** — concrete file paths and what to add/change
8. **Acceptance criteria** — checklist; all must be true to mark `done`
9. **Eval gate** — measurable thresholds (faithfulness, F1, latency, etc.)
10. **Risks specific to this workstream**

When a workstream finishes:
- Update its `Status` block at the top
- Tick the acceptance checkboxes
- Add a `## Outcome` section at the bottom with what shipped + diffs from the original plan
- Update [`../../CHANGELOG.md`](../../CHANGELOG.md) `[Unreleased]` block

---

## Workstream index

| # | File | Title | Priority | Parallelizable | Depends on |
|---|---|---|---|---|---|
| WS1 | [`WS1_GRAPHRAG.md`](WS1_GRAPHRAG.md) | GraphRAG retrieval | 1 | yes (with WS2/4/5/6) | Flair/Gemini enrichment (WS1 phase A) |
| WS2 | [`WS2_FINANCIAL.md`](WS2_FINANCIAL.md) | Trustworthy financial analysis | 2 | yes (independent) | none |
| WS3 | [`WS3_JOURNEY.md`](WS3_JOURNEY.md) | Document journey timelines | 3 | partially (UI defers) | WS1 (motie↔notulen linking) |
| WS4 | [`WS4_MCP_DISCIPLINE.md`](WS4_MCP_DISCIPLINE.md) | Best-in-class MCP surface | 4 | yes (independent) | none |
| WS5a | [`WS5a_NIGHTLY_PIPELINE.md`](WS5a_NIGHTLY_PIPELINE.md) | 100% reliable nightly ingest | 5 | yes (independent) | none |
| WS5b | [`WS5b_MULTI_PORTAL.md`](WS5b_MULTI_PORTAL.md) | Multi-portal connectors (search-only) | 6 | **deferred to v0.2.1** | WS5a stable for 14d |
| WS6 | [`WS6_SUMMARIZATION.md`](WS6_SUMMARIZATION.md) | Source-spans-only summarization | 8 | yes (independent) | none for v0.2.0 minimum; WS1 for `mode='structured'` |
| WS7 | [`WS7_OCR_RECOVERY.md`](WS7_OCR_RECOVERY.md) | OCR recovery for moties/amendementen | 2.5 | yes (independent) | none; **should run before WS1 Phase 1** |
| WS8 | [`WS8_FRONTEND_REDESIGN.md`](WS8_FRONTEND_REDESIGN.md) | Frontend redesign: design system, landing page, calendar | 1 (launch blocker) | yes (with WS9; converge at search integration) | WS9 (for demo answer + streaming search) |
| WS9 | [`WS9_WEB_INTELLIGENCE.md`](WS9_WEB_INTELLIGENCE.md) | Web intelligence: MCP-as-backend, Sonnet tool_use, SSE streaming | 1 (launch blocker) | yes (with WS8) | none (extract from existing MCP tools) |

**Webcast timestamp linking** (priority 7) is split across WS5a (schema + backfill) and WS5b (HLS player UI).

**WS8 + WS9 are the public launch critical path.** They are independent of WS1–WS7 and can run in parallel. They converge when WS8 Phase 2 (landing page) wires up the SSE streaming endpoint delivered by WS9.

---

## Parallelism map

```
v0.2.0 — two parallel tracks

  TRACK A: Data quality + retrieval (WS1–WS7)
  ┌─────────────────────────────────────────────────────────────────┐
  │  parallel work — start day 1                                     │
  │                                                                  │
  │  ┌─────────────┐  ┌──────────────┐  ┌────────────┐  ┌────────┐  │
  │  │ WS1 phase 0 │  │     WS2      │  │    WS4     │  │  WS6   │  │
  │  │ (code only) │  │  Financial   │  │  MCP disc. │  │ Summary│  │
  │  │  + WS7 OCR  │  │              │  │            │  │        │  │
  │  └──────┬──────┘  └──────────────┘  └────────────┘  └────────┘  │
  │         │                                                        │
  │         ▼                                                        │
  │  ┌─────────────┐  ┌──────────────┐                              │
  │  │  WS7 → WS1  │  │     WS5a     │                              │
  │  │ OCR → enrich│  │ Nightly pipe │                              │
  │  │  → graph svc│  │              │                              │
  │  └──────┬──────┘  └──────────────┘                              │
  │         │                                                        │
  │         ▼                                                        │
  │  ┌─────────────┐                                                │
  │  │     WS3     │                                                │
  │  │  Journey    │                                                │
  │  │  (no UI)    │                                                │
  │  └─────────────┘                                                │
  │                                                                  │
  │  ► eval gate A ◄                                                │
  └─────────────────────────────────────────────────────────────────┘

  TRACK B: Public launch (WS8 + WS9) — independent of Track A
  ┌─────────────────────────────────────────────────────────────────┐
  │  parallel start — both day 1                                     │
  │                                                                  │
  │  ┌──────────────────────┐   ┌──────────────────────────────┐    │
  │  │        WS9           │   │      WS8 Phase 1             │    │
  │  │  MCP-as-backend      │   │  Design tokens + CSS         │    │
  │  │  Sonnet tool_use     │   │  restructure (independent)   │    │
  │  │  SSE /stream endpoint│   │                              │    │
  │  └──────────┬───────────┘   └──────────────────────────────┘    │
  │             │                                                    │
  │             ▼  (WS9 done → WS8 Phase 2 can wire up)             │
  │  ┌──────────────────────────────────────────────────────────┐    │
  │  │  WS8 Phase 2 — Landing page                              │    │
  │  │  4 elements: demo answer + search + credibility + trust  │    │
  │  │  Demo question: "Heeft het college haar beloftes         │    │
  │  │  waargemaakt?" — cached, real citations, non-partisan    │    │
  │  └──────────┬───────────────────────────────────────────────┘    │
  │             │                                                    │
  │             ▼                                                    │
  │  ┌──────────────────────────────────────────────────────────┐    │
  │  │  WS8 Phase 3+4 — Calendar redesign + template cleanup    │    │
  │  └──────────────────────────────────────────────────────────┘    │
  │                                                                  │
  │  ► eval gate B ◄  → public launch + press outreach             │
  └─────────────────────────────────────────────────────────────────┘

  Tracks A and B run concurrently. Track B gates the press moment.
  Track A gates v0.2.0 eval scores. Both must pass before tag v0.2.0.

v0.2.1 — search-only beyond Rotterdam
  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
  │     WS5b     │  │  WS3 UI      │  │ Webcast HLS  │
  │ ORI fallback │  │  /journey    │  │ player + cite│
  └──────────────┘  └──────────────┘  └──────────────┘
```

**Critical path (Track A):** WS7 OCR recovery → WS1 phase 1 (enrichment) → WS1 phase 2 (graph svc + MCP) → WS3.
**Critical path (Track B):** WS9 (SSE endpoint) → WS8 Phase 2 (landing page + demo answer) → press outreach.
**Earliest blocker (Track A):** WS7 should run before WS1 Phase 1 so enrichment operates on clean text.
**Earliest blocker (Track B):** WS9 must ship before the demo answer can be wired to the live search stream.

---

## Eval gate for tagging v0.2.0

### Track A — Data quality (must pass before tag)

| Metric | Source | Target |
|---|---|---|
| Completeness | [rag_evaluator/](../../rag_evaluator/) | ≥ 3.5 (from 2.75) |
| Faithfulness | [rag_evaluator/](../../rag_evaluator/) | ≥ 4.5 (no regression) |
| Numeric accuracy | WS2 financial benchmark | **100%** on 30 questions |
| Nightly reliability | WS5a smoke test logs | 14 consecutive clean days |
| Source-spans strip test | WS6 verifier | Pass on 50 random docs |
| Tool-description uniqueness | WS4 startup check | No pair > 0.85 cosine |
| KG Layer 2 size | WS1 quality audit | ≥ 500K relationship edges |

### Track B — Public launch (must pass before press outreach)

| Metric | Source | Target |
|---|---|---|
| Demo answer quality | Manual review by Dennis | Impressive enough to be first thing a journalist sees |
| Lighthouse Performance | `lighthouse https://neodemos.nl` | ≥ 90 |
| Lighthouse Accessibility | `lighthouse https://neodemos.nl` | ≥ 95 (WCAG AA) |
| WS9 MCP quality parity | Side-by-side comparison (web vs MCP) | ≥ 90% answer quality vs direct MCP |
| Mobile search above fold | Playwright screenshot at 375px | Search bar + CTA visible without scroll |
| Demo answer cached | `GET /` response time | < 200ms (pre-rendered, no API call) |
| Landing headline rotation | Config check | `LANDING_HEADLINE` env var wired, weekly swap documented |

---

## How to invoke an agent on a handoff

For a fresh LLM agent (Claude Code, Cursor, etc.):

1. Open the handoff file (e.g. `WS2_FINANCIAL.md`)
2. Copy the **Cold-start prompt** block verbatim
3. Paste into the agent's first message
4. Let the agent read its `Files to read first`, then start the `Build tasks`
5. The agent should not need to read other handoffs unless its `Dependencies` say so

For a human owner: just read top-to-bottom and check off `Acceptance criteria` as you go.

---

## House rules (apply to every workstream)

These are project-wide and override anything in an individual handoff:

1. **Never write to Qdrant/Postgres while a background embedding/migration is running.** Use `pg_advisory_lock(42)` or coordinate via the `pipeline_runs` table (built in WS5a). Memory: [project_embedding_process.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_embedding_process.md)
2. **Cloud-first dev.** All work hits the Hetzner Postgres + Qdrant via SSH tunnel. Start with `./scripts/dev_tunnel.sh --bg`. Never spin up local DB containers.
3. **Conventional naming.** New MCP tools use Dutch `verb_noun` (`zoek_*`, `haal_*_op`, `vat_*_samen`, `traceer_*`, `vergelijk_*`). New services live in `services/`. New pipeline steps live in `pipeline/` or `scripts/`. New tables get an Alembic migration.
4. **Test on staging before promoting.** KG/financial/journey writes go through staging schemas first. Use [scripts/promote_*](../../scripts/) helpers.
5. **Cite back to the master plan.** Each PR description should reference the workstream (e.g. "WS2 — financial_lines table") so reviewers can find context.
6. **Update the handoff file as you go.** Status → in progress → review → done. Add an `## Outcome` section at the bottom when shipped.
7. **No scope creep.** If a handoff suggests "while we're here, we should also…" — write it down in the handoff's `## Future work` section instead of doing it. The whole point of the multi-workstream split is to ship in parallel without entanglement.
