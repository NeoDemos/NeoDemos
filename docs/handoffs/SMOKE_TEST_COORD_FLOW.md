# Smoke Test — Coordination Flow (end-to-end)

> **Type:** one-shot test plan (not a workstream — no `WS` prefix).
> **Author:** Dennis.
> **Date drafted:** 2026-04-14.
> **Subject under test:** `/ws-claim`, `/ws-complete`, `/ws-reject`, `/ws-status`, and `scripts/coord/archive_ws.py`.
> **Test vehicle:** WS2b (IV3 taakveld FK backfill) — small, unclaimed, unblocked, and its sole precondition (WS2) is already archived.

---

## TL;DR

The coordination layer has never been exercised end-to-end on a real workstream. This plan walks a single WS (WS2b) from `not_started` → `in_progress` → `done` → archived in `docs/handoffs/done/`, verifying at each step that: (1) events append correctly, (2) `rebuild_state.py` produces accurate state, (3) `archive_ws.py` refuses on unmet preconditions, (4) `git mv` + inbound-ref rewrites land without collateral damage. Expected duration: **half a day** if actually executing WS2b's work; **~20 minutes** using the "test marker" shortcut (see Caveats).

---

## Preconditions

Run all of these before starting. If any fails, stop and fix first.

- [ ] **Working tree clean.** `git status --porcelain` returns empty. (Currently there are in-flight edits per `git status` — stash or commit them first.)
- [ ] **On `main`, up to date.** `git pull --ff-only` succeeds.
- [ ] **SSH tunnel live.** `ps aux | grep "ssh.*178.104" | grep -v grep` returns a process. If not: `./scripts/dev_tunnel.sh --bg`.
- [ ] **`.venv` active.** `which python` points inside `.venv/`. If not: `source .venv/bin/activate`.
- [ ] **Coordination state is sane.** `/ws-status` (or `cat .coordination/state.md`) shows:
  - WS2b under **Available (unclaimed, no blockers)**, dependency WS2 listed
  - WS2 under **Recently Completed** (confirms prerequisite archived)
  - No ghost claim on WS2b from a prior test run
- [ ] **Events log sane.** `tail -5 .coordination/events.jsonl` parses as JSON lines, newest entries dated today.
- [ ] **No background embedding / migration running.** `ps aux | grep -E "embed|migrate|ingest" | grep -v grep` returns nothing long-running. (House rule: never write DB while pipelines run.)
- [ ] **Advisory lock 42 free.** `SELECT pg_try_advisory_lock(42);` returns `true` (release immediately with `pg_advisory_unlock(42)` if you grabbed it).

---

## Steps (in order)

### Step 1 — Claim

```
/ws-claim WS2b
```

**Expected:**
- `append_event.py` prints one line confirming `claimed` event written.
- `rebuild_state.py` runs silently (or prints "state.md rebuilt").
- `.coordination/events.jsonl` has a new line with `"event": "claimed", "ws": "WS2b"`.
- `.coordination/state.md` — WS2b moves **out of "Available"** into **"Active Now"** with your agent name in the `Claimed by` column.

**Verify:**
- `git diff .coordination/state.md` shows WS2b table-row migration.
- `git status` shows only `.coordination/state.md` and `.coordination/events.jsonl` modified.

**Failure modes to watch for:**
- Script errors "unknown WS" → `dependencies.yaml` lacks WS2b entry; stop.
- state.md still shows WS2b in "Available" → `rebuild_state.py` failed silently; inspect stderr.

### Step 2 — Do the work

Open `docs/handoffs/WS2b_IV3_TAAKVELD.md`. Follow its **Cold-start prompt** and **Files to read first** list. The three deliverables are:

1. Wire `_assign_iv3` in `pipeline/financial_ingestor.py` to query `programma_aliases`.
2. Write + run `scripts/ws2b_backfill_iv3.py` (with `pg_advisory_lock(42)`, `--dry-run` first, then `--limit 100`, then full).
3. Run the coverage SQL — target `≥ 80%` matched.

Plus the 2026-04-14 addendum:
- IV-1: add `unit` field to `vraag_begrotingsregel` / `vergelijk_begrotingsjaren` response schemas.
- IV-2: JOIN `iv3_taakvelden` to populate `iv3_omschrijving` in responses.

**Commit rule:** one or more commits prefixed `WS2b:` per repo convention. Example:
```
WS2b: wire _assign_iv3 programma_aliases lookup + backfill 61K rows (coverage 87.3%)
```

### Step 3 — Fill the `## Outcome` section

Edit `docs/handoffs/WS2b_IV3_TAAKVELD.md`. Replace the placeholder under `## Outcome` with real numbers:
- Final coverage % after backfill
- Top 10 matched programma names (sanity)
- Unexpected NULLs + reason (GR/DCMR etc.)
- Deploy commit SHA

**Also tick the checkboxes** under `## Acceptance criteria` and the addendum sections (IV-1, IV-2).

Commit this separately or fold into the last work commit — either is fine. Message: `WS2b: fill Outcome + tick acceptance criteria`.

### Step 4 — Complete

Confirm working tree is clean (`git status`), then:

```
/ws-complete WS2b "IV3 coverage 87.3% across 61,182 lines; IV-1 + IV-2 shipped"
```

**The command's own preflight will refuse if:**
- Working tree isn't clean → commit first.
- `## Outcome` section is empty → fill it first.
- WS2b isn't in "Active Now" per state.md → event log missed the claim.

**Expected action sequence (per `.claude/commands/ws-complete.md`):**
1. `append_event.py` writes the `completed` event (with short SHA).
2. `archive_ws.py --ws WS2b` runs:
   - Replays events, confirms WS2b status is `done`.
   - `git mv docs/handoffs/WS2b_IV3_TAAKVELD.md → docs/handoffs/done/WS2b_IV3_TAAKVELD.md`.
   - Scans all tracked `.md/.py/.sh/.yaml/.json/.html/.rst/.txt` files and rewrites inbound refs.
   - Prints "Summary: 1 file(s) moved, N ref(s) rewritten across M file(s)."
3. `rebuild_state.py` regenerates state.md.
4. Prints `git status` + `git diff --stat`.

**Do NOT auto-commit.** Review first.

### Step 5 — Review + commit the archive

```bash
git diff --stat           # should be: 1 rename + ~3 ref-rewrite edits + state.md + events.jsonl
git diff docs/            # confirm refs are rewritten, not truncated
git log --oneline -5      # sanity check
```

Then commit per the template in `.claude/commands/ws-complete.md`:
```
Archive WS2b after QA pass

- Append completed event
- git mv docs/handoffs/WS2b_IV3_TAAKVELD.md → docs/handoffs/done/
- Rewrite N inbound refs across M files
```

---

## Verifications (after the whole flow)

- [ ] `git log --oneline` has **2–3 new commits**: the WS2b work commit(s) + the archive commit.
- [ ] `.coordination/events.jsonl` has **≥ 3 new events**: `claimed`, `completed`, any intermediate (e.g. `qa_rejected` if you went around the loop).
- [ ] `docs/handoffs/done/WS2b_IV3_TAAKVELD.md` exists; `docs/handoffs/WS2b_IV3_TAAKVELD.md` does not.
- [ ] `grep -r "handoffs/WS2b_IV3" --include="*.md" --include="*.py"` returns only `docs/handoffs/done/WS2b_IV3_TAAKVELD.md` references (or zero hits in non-done files). Self-refs inside the moved file should also already say `done/` — `archive_ws.py` rewrites pre-move.
- [ ] `/ws-status` shows WS2b in **Recently Completed**, not in Active/Available.
- [ ] `make status` (if the Makefile target exists) agrees.
- [ ] Dashboard `.coordination/state.md` "Active Now" no longer lists WS2b.

---

## Caveats (read before starting)

1. **WS2b is real work.** Wiring `_assign_iv3` + backfilling 61K rows is a real half-day task that touches the production DB. If the goal is purely to smoke-test the coordination layer, one of:
   - **Option A (recommended):** do the work. You get a passing smoke test AND a shipped workstream.
   - **Option B (shortcut):** pick a trivially-small WS with no DB impact. WS2b doesn't qualify — nothing else in the current board does either (WS5a, WS11, WS12 all touch the pipeline). So realistically: Option A, or…
   - **Option C (pure UI test):** add a `## Test marker 2026-04-14` section to `docs/handoffs/WS2b_IV3_TAAKVELD.md` reading *"smoke test placeholder, reverting"*, commit as `WS2b: smoke-test marker`, then proceed through Steps 3–5. Revert the marker after archive. **This leaves WS2b sitting in `done/` unfinished** — you must then manually move it back and append a `released` or `reopened` event. Ugly, but it decouples the coordination test from the actual work. Flag this to future agents if used.
2. **Inbound ref rewrite is greedy.** `archive_ws.py` rewrites any `docs/handoffs/WS2b_IV3_TAAKVELD.md` / `../handoffs/WS2b_IV3_TAAKVELD.md` / bare `WS2b_IV3_TAAKVELD.md` hit inside `docs/handoffs/`. Verify the diff — a false positive is possible if another WS doc has a weirdly-structured link.
3. **Other agents may be editing WS2b's file.** If so, `git mv` carries their uncommitted changes forward (same as the WS2 archive commit `88d2348`). That's fine, but confirms the commit before announcing completion.
4. **`--dry-run` exists and is cheap.** Before the live `/ws-complete`, you can run `python scripts/coord/archive_ws.py --ws WS2b --dry-run` and eyeball the planned rewrites. No flag for the slash command itself, but the underlying script supports it.
5. **Event replay is append-only.** If anything goes wrong, **never hand-edit `events.jsonl`** — append a compensating event (`released`, `qa_rejected`, etc.) and let `rebuild_state.py` reconcile.

---

## Abort / rollback

If things go sideways mid-test:

- **After Step 1 claim, before Step 4:** working tree has only `state.md` + `events.jsonl` edits. To undo: `git checkout .coordination/state.md` and manually trim the last line of `events.jsonl` (acceptable here because nothing else references it yet), then `python scripts/coord/rebuild_state.py`. Alternative (cleaner): append a `released` event to restore WS2b to `not_started`, commit nothing.
- **After Step 4 event append, before commit:** `git status` will show the rename + rewrites un-staged. To undo:
  ```bash
  git checkout docs/handoffs/       # reverts rewrites
  git mv docs/handoffs/done/WS2b_IV3_TAAKVELD.md docs/handoffs/WS2b_IV3_TAAKVELD.md
  # then append a compensating event
  python scripts/coord/append_event.py --event released --ws WS2b --agent "Dennis" --detail "smoke test rollback"
  python scripts/coord/rebuild_state.py
  ```
- **After the archive commit:** `git revert <sha>` — safer than `git reset --hard` because we're on `main`. Then append the compensating `released` event as above. **Do not force-push.**
- **Destructive ops are prohibited** unless Dennis explicitly authorizes: no `git reset --hard`, no `git clean -fd`, no `rm -rf .coordination/`.

---

## Success criteria

The smoke test passes iff all of these hold simultaneously at the end:

1. `git log` shows 2–3 clean commits with `WS2b:` prefixes + one `Archive WS2b…` commit.
2. `.coordination/events.jsonl` gained at least a `claimed` and a `completed` event for WS2b, both parseable JSON, timestamps monotonic.
3. `docs/handoffs/done/WS2b_IV3_TAAKVELD.md` exists and contains a filled `## Outcome` section.
4. No tracked file outside `docs/handoffs/done/` references the old path `docs/handoffs/WS2b_IV3_TAAKVELD.md`.
5. `/ws-status` lists WS2b under **Recently Completed** with today's date.
6. No unexpected files modified (scope: only `.coordination/`, `docs/handoffs/{,done/}`, and the WS2b code/scripts).

---

## What this proves

| Component | Proven by |
|---|---|
| `/ws-claim` correctly flips `not_started → in_progress` | Step 1 verification: state.md table migration |
| `append_event.py` serializes correctly | `tail -1 events.jsonl \| jq .` parses cleanly |
| `rebuild_state.py` replay is accurate | state.md matches hand-computed status from events |
| `/ws-complete` preflight catches missing Outcome / dirty tree | (optional) try with dirty tree first; expect refusal |
| `archive_ws.py` refuses wrong-status WS | status replay in `load_ws_status()` must flag `done` |
| `git mv` + ref rewrite is lossless | `git diff` review + grep for stale refs |
| End-to-end loop closes with zero manual JSONL edits | `events.jsonl` touched only by `append_event.py` |

---

## What this does NOT prove

- `/ws-reject` path (claim → reject → re-claim → complete). Do a separate smoke test for that — cheap, since it doesn't archive.
- Batch archival (`--ws WS_A --ws WS_B`). Already exercised by the WS2/WS4/WS8/WS9 seed commit; no need to re-test.
- Concurrent agents claiming the same WS. Out of scope here; design-level assumption is single-writer (Dennis).
