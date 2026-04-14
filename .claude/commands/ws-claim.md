---
description: Claim a workstream. Appends a 'claimed' event to events.jsonl and rebuilds state.md.
---

You are claiming workstream $ARGUMENTS for yourself. This signals to other agents that this WS is being worked on and they should pick up something else.

Run:
```bash
python scripts/coord/append_event.py --event claimed --ws $ARGUMENTS --agent "${COORD_AGENT:-$(whoami)}" --detail "claimed via /ws-claim"
python scripts/coord/rebuild_state.py
```

If the script fails because the WS is unknown, abort and tell Dennis.

After claiming:
1. Open the matching handoff file `docs/handoffs/$ARGUMENTS*.md`
2. Read its `Cold-start prompt` section and follow it
3. Read the `Files to read first` list
4. Begin execution

When you finish, use `/ws-complete $ARGUMENTS "<one-line summary>"`.
