# NeoDemos — agent orientation

**Project:** AI-powered democracy tool for Dutch municipal councils. Owner: Dennis (solo, non-dev).
**Version:** `0.2.0-alpha.2` — see `CHANGELOG.md`.
**Stack:** PostgreSQL + Qdrant on Hetzner; FastAPI + vanilla JS frontend; Kamal deploys.

## Sources of truth (read these, in this order)

1. [docs/handoffs/README.md](docs/handoffs/README.md) — workstream index, status, parallelism map, house rules
2. [docs/handoffs/WS*.md](docs/handoffs/) — task definitions, acceptance criteria, cold-start prompts
3. [docs/WAY_OF_WORKING.md](docs/WAY_OF_WORKING.md) — workflow conventions
4. [docs/architecture/V0_2_BEAT_MAAT_PLAN.md](docs/architecture/V0_2_BEAT_MAAT_PLAN.md) — master plan
5. [.coordination/state.md](.coordination/state.md) — auto-generated live status (from events.jsonl)
6. [.coordination/FEEDBACK_LOG.md](.coordination/FEEDBACK_LOG.md) — live triage inbox
7. [.agent/rules/](.agent/rules/) — communication + behavioral rules
8. [.claude/commands/](.claude/commands/) — skill references (rag, mcp, deploy, backup, ingest, secure, naming) and coordination commands (ws-claim, ws-complete, ws-status)
9. [pipeline/README.md](pipeline/README.md) — canonical writer contract (advisory locks, 8 rules, QA gate thresholds, SOP) for any code that writes to Postgres or Qdrant

## Do NOT read

- `docs/archive/` — historical artifacts, intentionally frozen
- `docs/archive/memory-bank/` — March content, superseded by handoffs + auto-memory
- `docs/archive/brain/` — stale working notes (except FEEDBACK_LOG, now at `.coordination/FEEDBACK_LOG.md`)

## Cold-start flow for a workstream

1. Read [docs/handoffs/README.md](docs/handoffs/README.md) — find which WS is unblocked and unclaimed
2. Open the matching `docs/handoffs/WS*.md` — copy its `Cold-start prompt`
3. Read the file's `Files to read first` list
4. Work. Commit small. Update the handoff's `Outcome` section when done.
5. `/ws-complete WS<N> "<one-line summary>"` — appends to events.jsonl and regenerates state.md

## House rules

- **Never write to Qdrant/Postgres while embedding/migration jobs may be running.** Use `pg_advisory_lock(42)` or the `pipeline_runs` table. See `.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_embedding_process.md`.
- **Cloud-first dev.** All DB work goes through `./scripts/dev_tunnel.sh --bg`. Never spin up local DB containers.
- **Kamal for all deploys.** Never rsync or SSH-cp to prod. Binary: `/opt/homebrew/lib/ruby/gems/4.0.0/bin/kamal`.
- **Dutch verb_noun for MCP tools:** `zoek_*`, `haal_*_op`, `vat_*_samen`, `traceer_*`, `vergelijk_*`.
- **No scope creep.** If a handoff mentions "while we're here...", write it to the handoff's `Future work` section — do not do it.
