---
description: Show the current coordination state — reads .coordination/state.md and prints it.
---

Read and display the current contents of `.coordination/state.md`.

Then show the last 15 entries of `.coordination/events.jsonl` in reverse-chronological order (most recent first).

If `.coordination/state.md` does not exist, run `python scripts/coord/rebuild_state.py` first.

Do not edit anything — this command is read-only.
