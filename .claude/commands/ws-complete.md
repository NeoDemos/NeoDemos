---
description: Dennis's QA verdict. Marks a workstream done AND archives its handoff file + rewrites inbound refs.
---

**This command is Dennis's QA verdict.** Agents do NOT run this — they finish their work, update the `Outcome` section of the handoff, commit, and notify Dennis. Dennis runs `/ws-complete` when satisfied after reviewing the diff, running the feature, and checking the Outcome section.

Usage: `/ws-complete WS7 "BM25 hit rate now 96.2% across 2,700 docs"`.

## Preflight (refuse to proceed if any fails)

1. **Working tree is clean.** Run `git status`. If there are uncommitted changes, stop and show them to Dennis — he probably wants to commit them first.
2. **Handoff file exists.** Confirm `docs/handoffs/$1_*.md` resolves to exactly one file.
3. **Outcome section present.** The handoff must have an `## Outcome` section with content below it (not empty). If missing, stop and tell Dennis which handoff is missing the section.
4. **WS is currently claimed.** Check `.coordination/state.md` — if the WS is not in "Active Now", stop and ask Dennis if he wants `--force` behavior.

## Action

If preflight passes, run this sequence:

```bash
# 1. Append the completion event (this is the QA-passed verdict)
python scripts/coord/append_event.py \
    --event completed \
    --ws $1 \
    --agent "Dennis" \
    --detail "$2" \
    --commit "$(git rev-parse --short HEAD)"

# 2. Archive the handoff file + rewrite all inbound refs
python scripts/coord/archive_ws.py --ws $1

# 3. Rebuild the dashboard
python scripts/coord/rebuild_state.py

# 4. Show what changed so Dennis can review before committing
git status
git diff --stat
```

Then tell Dennis to review `git diff`, and if it looks right, commit with a message like:

```
Archive $1 after QA pass

- Append completed event
- git mv docs/handoffs/$1_*.md → docs/handoffs/done/
- Rewrite N inbound refs across M files
```

Do NOT commit automatically. Dennis eyes the diff first.

## If the archive step fails

`archive_ws.py` will refuse to proceed if the WS status is not `done` after the event append. That means the event log didn't update — rare but possible if events.jsonl is locked or the append script errored. Show the stderr to Dennis and stop.
