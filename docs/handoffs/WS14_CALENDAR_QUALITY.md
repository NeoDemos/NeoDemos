# WS14 — Calendar Quality & Bijlage Reconciliation

> **Status:** `ready to start` — WS8f Phase 7+ shipped 2026-04-15; WS14 unblocked once Dennis QAs WS8f. C1 hotfix can ship standalone regardless.
> **Owner:** `unassigned`
> **Priority:** 1 (Track C launch blocker — press-facing calendar quality)
> **Parallelizable:** yes — Phase B scripts + Phase C read-path + Phase D UI all parallel once WS8f is marked done. B3 (WS11 cross-ref) and B6 (WS5a cross-ref) independent.
> **Target version:** v0.2.0
> **Last updated:** 2026-04-15 — incorporated WS8f Phase 7+ patterns; Dennis's original 5 issues still hold.

---

## TL;DR

Dennis reviewed https://neodemos.nl/calendar and flagged five issues. The list view is good and stays. The rest are data + display bugs: many meetings show no docs, bijlagen are hidden behind annotaties, some docs render twice per agenda item, and some meetings render twice when they are the same logical meeting ingested twice (iBabs + ORI). Fix is four-phase: audit → backfill (junction reconciliation, classifier widening, meeting dedupe) → read-path queries → UI split. Future-proof multi-gemeente by scoping every calendar query through `meetings.municipality`.

**Updated 2026-04-15 — WS8f Phase 7+ lays groundwork we can inherit wholesale:**

1. **API-layer dedupe + label normalization** — `/api/calendar/upcoming` in [routes/api.py](../../routes/api.py) now dedupes `(date_iso, label)` and normalizes weekday-prefixed names ("donderdag 16 april 2026") to `"Raadsvergadering"`. Extract this into `services/calendar_labels.py` and reuse in `get_meetings_filtered`. Solves part of RC4 (display confusion) without waiting for the full Phase B/C backfill.
2. **Committee vs specific-meeting semantics** — chat workbench sidebar already exposes both as separate pickers; inserting `@Commissie Bouwen, Wonen en Buitenruimte` (type) vs `@Commissie Bouwen, Wonen en Buitenruimte 15 Apr` (pinpoint). Committee list lives in [templates/search.html](../../templates/search.html) and [templates/partials/_nav.html](../../templates/partials/_nav.html) — single source of truth for Phase D calendar filter chips.
3. **Oatmeal-aligned token discipline** — `static/css/tokens.css` has the full scale (font-size xs→6xl, line-height tight→relaxed, letter-spacing display, z-index 1→1001, `--container-2xl: 80rem`, `color-mix` borders). Phase D styling reads these; no custom CSS values.
4. **Chat-workbench integration surface** — every meeting row + committee chip on `/calendar` can have "Vraag erover" that deeplinks to `/?q=@<label>%20<date>` (Phase D4 new). Press users click a meeting → land in chat workbench with the `@mention` pre-filled, immediately ready to ask questions scoped to that meeting. This is the WS14→WS8f wedge.
5. **nd-calendar-mini block** — already registered in [static/admin-editor/components.js](../../static/admin-editor/components.js); admins can drop a 5-meeting teaser on any marketing page. WS14 doesn't need to ship a separate widget.
6. **Agent-parallelization pattern** — WS8f Phase 7+ ran 3 agents in parallel (Block 1 hygiene / Block 4 editor parity / Block 5 widget blocks) against non-overlapping files. Same approach fits WS14: Phase A audits + Phase B backfill scripts + Phase C read-path rewrite are on different file sets.

---

## Dependencies

| Dependency | Type | Notes |
|---|---|---|
| WS8f Phase 7+ QA | hard (sequencing) | Shipped 2026-04-15 — pending Dennis QA. **No file overlap** with WS14: WS8f touched `templates/search.html`, `_nav.html`, `static/admin-editor/components.js`, `routes/admin.py` + `routes/pages.py` (page-creation flow), `static/css/layout.css`, `pages/search.css`, `pages/admin.css`, `pages/landing.css`, `pages/auth.css`. WS14 touches `services/storage.py`, `routes/pages.py` (calendar route only, line 183-207), `templates/calendar.html`, `templates/meeting.html`. Clean split. |
| WS8a-e | completed | Design tokens + calendar list view already shipped |
| WS8f Phase 7+ patterns | **reuse** | Inherit `/api/calendar/upcoming` dedupe+label helper, Oatmeal token discipline, committee-picker single source of truth, `@mention` deeplink contract. See TL;DR #1–5. |
| WS11 B3 expansion | cross-ref | WS11 owns `doc_classification` — WS14 hands them a patch |
| WS5a nightly pipeline | cross-ref | B6: nightly must call `ws14_dedupe_meetings.py` when WS5a lands |
| Plan file | read first | `/Users/dennistak/.claude/plans/glittery-twirling-prism.md` |
| Architecture reference | read first | [docs/architecture/EDITOR_AND_WIDGETS.md](../architecture/EDITOR_AND_WIDGETS.md) — widget-as-block contract + chat-workbench backend shape. Phase D UI work must respect it. |

---

## Cold-start prompt

```
You are picking up WS14_CALENDAR_QUALITY for NeoDemos — civic transparency platform
for Rotterdam raad. The /calendar page has quality issues that block the press
moment. Full plan lives at:
  /Users/dennistak/.claude/plans/glittery-twirling-prism.md

Read these files first:
- docs/handoffs/WS14_CALENDAR_QUALITY.md (this file)
- /Users/dennistak/.claude/plans/glittery-twirling-prism.md (the plan)
- services/storage.py (focus on get_meeting_details L81-202 and get_meetings_filtered L540-644)
- templates/calendar.html (list view — DO NOT redesign)
- templates/meeting.html (meeting detail — bijlage/annotatie split goes here)
- routes/pages.py (calendar route L183-207)
- docs/handoffs/done/WS11_CORPUS_COMPLETENESS.md (doc_classification context)
- docs/handoffs/WS8f_ADMIN_CMS.md (ContentService + router split context)

Before coding: confirm WS8f is marked `done` in docs/handoffs/README.md. If not,
only C1 hotfix is safe to ship; pause Phases A-D until WS8f QA finishes.

Every script must accept --municipality rotterdam --dry-run. Coordinate DB writes
via pg_advisory_lock(42). SSH tunnel required: ./scripts/dev_tunnel.sh --bg.
```

---

## Files to read first

| File | Why |
|---|---|
| `/Users/dennistak/.claude/plans/glittery-twirling-prism.md` | The full plan — source of truth for this handoff |
| [services/storage.py:81-202](../../services/storage.py) | `get_meeting_details` — C1, C4 targets |
| [services/storage.py:540-644](../../services/storage.py) | `get_meetings_filtered` — C2, C3, C4 targets |
| [routes/pages.py:183-207](../../routes/pages.py) | Calendar route — C5 target |
| [templates/calendar.html](../../templates/calendar.html) | List view — D2, D3 (no redesign!) |
| [templates/meeting.html](../../templates/meeting.html) | Meeting detail — D1, D4 |
| [scripts/ws11a_classify_existing_docs.py](../../scripts/ws11a_classify_existing_docs.py) | B3 extension target |
| [scripts/migrate_many_to_many_docs.sql](../../scripts/migrate_many_to_many_docs.sql) | Junction schema baseline |
| [data/municipalities_index.json](../../data/municipalities_index.json) | Gemeente registry for C5 validation |

---

## Root causes

See plan RC1–RC5. Summary:

- **RC1** — `get_meetings_filtered` LEFT JOIN at [services/storage.py:604](../../services/storage.py) only matches `da.meeting_id`, missing agenda-item-only links.
- **RC2** — Legacy docs have direct `documents.meeting_id`/`agenda_item_id` FKs but no `document_assignments` row.
- **RC3** — No `DISTINCT` in [services/storage.py:109-118](../../services/storage.py) document fetch.
- **RC4** — Classifier too strict on `bijlage`, too greedy on `annotatie`; UI treats them identically.
- **RC5** — Logical duplicate meetings from iBabs + ORI dual ingestion with different source-assigned ids.

---

## Build tasks

### Phase A — Diagnosis (read-only audits)

Write to `docs/audits/ws14_calendar_baseline_20260414.md`. Eight queries A1–A8 per plan. Commit baseline BEFORE any write-path work.

### Phase B — Backfill scripts (idempotent, dry-run-first)

- **B1** `scripts/ws14_reconcile_direct_fks.py` — backfill junction from direct FKs, populate both meeting_id + agenda_item_id
- **B2** `scripts/ws14_dedupe_assignments.py` — delete duplicate junction triples
- **B3** Extend `scripts/ws11a_classify_existing_docs.py` — add `%toelichting%`, `%nota%`, `%notitie%`, `%memo%`, `%brief%`, `%concept%` patterns + `agenda_item_id IS NOT NULL AND NULL → bijlage` fallback **(WS11 owns; cross-ref)**
- **B4** Migration `0011_da_unique_nullsafe.py` — `UNIQUE NULLS NOT DISTINCT` on junction triple
- **B5** Harden `scripts/deduplicate_meetings.py` (already on `main`) — add `--municipality`, `--dry-run`, `--since`, `pg_advisory_lock(42)`. Master = most `agenda_items + document_assignments`; tie-break by earliest `inserted_at` (no `source` column on `meetings`). Reparent agenda_items + documents + junction rows, delete losers. **Cross-confirmed 2026-04-14** via MCP testing: `lijst_vergaderingen` returns same meeting twice (numeric iBabs ID `7491843` with empty `commissie`, plus UUID `6ae82df8-...` with populated `commissie` code). Confirms the direction: master should prefer the record with populated `commissie` code. Surface both IDs in `lijst_vergaderingen` response metadata after dedup so downstream lookups still work. Raw entry: [`.coordination/FEEDBACK_LOG.md` 2026-04-14 BUG-007](../../.coordination/FEEDBACK_LOG.md).
- **B6** Add hook to WS5a handoff for nightly wiring **(cross-ref)**
- **B7** Migration `0012_meeting_logical_unique.py` — unique `(municipality, name, start_date, committee)` with NULLS NOT DISTINCT

### Phase C — Read-path fixes

- **C1** Add `DISTINCT` at [services/storage.py:109-118](../../services/storage.py) (1-line hotfix — deployable standalone)
- **C2** Rewrite LEFT JOIN in `get_meetings_filtered` to match junction rows linked at agenda-item level
- **C3** Split `doc_count` into `bijlage_count` + `annotatie_count` + `other_count` via `COUNT … FILTER`
- **C4** Add `municipality: str = 'rotterdam'` to all meetings-touching storage methods
- **C5** `routes/pages.py` — accept `?gemeente=` query param, validate against `data/municipalities_index.json`
- **C6** **Extract `services/calendar_labels.py`** — factor out the dedupe `(date_iso, label)` + weekday-prefix → "Raadsvergadering" logic currently inline in `/api/calendar/upcoming` ([routes/api.py:735-782](../../routes/api.py)). Both `upcoming_meetings` and `get_meetings_filtered` call the helper. One function: `normalize_and_dedupe(rows: list[dict]) -> list[dict]`. Unit-testable; covers RC4 display path without backfill.

### Phase D — UI (WS8f Phase 7+ patterns; list-view layout stays)

Preconditions from WS8f Phase 7+ already live:
- Oatmeal-aligned tokens in `tokens.css` — Phase D styling reads these, no new CSS values.
- `color-mix` border tokens, font-size scale, line-height scale, `--container-2xl`, pill radii all available.
- Chat-workbench sidebar is the canonical committee picker (6 Rotterdam committees) — **single source of truth**.

Tasks:

- **D1** `templates/meeting.html` — split rendering: **Bijlagen** section (📎, prominent, `font-size: var(--font-size-base)`) vs **Annotaties** section (📝, muted, `color: var(--color-text-secondary)`, `font-size: var(--font-size-sm)`). Fallback `📄` for NULL. Cards use `border: 1px solid var(--color-border-subtle)`, `border-radius: var(--radius-md)`.
- **D2** `templates/calendar.html` list view — show `X agendapunten · Y bijlagen · Z annotaties`. Use `{{ content() }}` for the labels so Dennis can edit via `/admin/content`. Typography: `font-size: var(--font-size-sm)`, tabular-nums on counts.
- **D3** `templates/calendar.html` empty-state badge "Geen documenten beschikbaar" for zero-zero meetings. Do NOT hide them. Badge uses `background: var(--color-surface-sunken)`, `color: var(--color-text-tertiary)`, `font-size: var(--font-size-xs)`, pill radius.
- **D4** `templates/meeting.html` gemeente badge in header when `meeting.municipality != 'rotterdam'` (inert; WS13-ready).
- **D5** **"Vraag erover" deeplink** — on every meeting row (`/calendar`) + meeting detail header (`/meeting/{id}`), add a small link/button that navigates to `/?q=@<encoded label>%20<date_short>`. The landing page already supports prefilled queries via `example-topic` seed pattern — extend `performSearch` to read `?q=` on load and pre-populate the hero composer + auto-focus (do NOT auto-submit, user may want to add context). This gives users a single-click path: *see a meeting → ask a question scoped to that meeting*.
- **D6** Committee filter chips on `/calendar` → source from the same 6-committee list used in chat sidebar. Extract the list to a shared constant (either Python `services/committees.py` or Jinja `partials/_committee_list.html` include). Eliminates the current "arbitrary chips from DB" behavior that can drift.
- **D7** **Oatmeal polish sweep on `/calendar` + `/meeting`** — identical to WS8f Phase 7+ on subpages. Agentable: one agent does tokens + typography + card borders + spacing rhythm across `pages/calendar.css` + `pages/meeting.css` (plus the `.cal-*` rules that live in `auth.css`, the grab-bag). No DOM changes; mechanical token swap + prose max-width 65ch on agenda-item titles, `line-height-relaxed` on summaries.
- **D8** Scrollable-list separator pattern — if any list on `/calendar` or `/meeting` scrolls, apply the same `mask-image` fade + bottom `color-mix` border used in the chat sidebar meeting list ([static/css/pages/search.css](../../static/css/pages/search.css) — `.sidebar-picker[data-picker="meeting"] .sidebar-list`). Visual discipline consistency.

### Phase E — Multi-gemeente future-proofing

Embedded in B/C/D. Contract: `meetings.municipality` is the single source of truth. Do NOT denormalize municipality onto `document_assignments` unless A1/A2 perf demands it. Per-portal annotatie/bijlage conventions (iBabs vs Notubiz vs Parlaeus) are WS13 scope.

### Phase F — WS8f chat-workbench integration

D5 described the "Vraag erover" deeplink. The reciprocal direction is also cheap and valuable:

- **F1** Landing-page hero reads `?q=` on load — when user arrives at `/?q=@Commissie%20BWB%2015%20Apr`, the hero composer is pre-filled with that `@mention`, cursor parks at end so user can append their question. Mirrors `example-topic` seed pattern already in [templates/search.html](../../templates/search.html).
- **F2** Meeting-detail page, when logged in, can surface the chat composer inline below the agenda list as an "ask about this meeting" affordance. Re-uses the Claude composer component CSS from `pages/search.css`. `@mention` pre-seeded with the meeting label. Submitting navigates to `/?q=...` (state 2 of chat workbench). Avoids embedding SSE inside the `/meeting` page (that's Phase 8 territory for the nd-answer Web Component).
- **F3** Chat-sidebar **Specifieke vergadering** picker currently loads from `/api/calendar/upcoming?limit=50`. Extend the sidebar with a "Bekijk volledige kalender" link to `/calendar` so users who need more than upcoming get routed. One-line template change.

### Phase G — Execution recommendation (WS8f Phase 7+ pattern)

Single-session parallelization, same shape as WS8f Phase 7+:

1. **Solo, ~30 min:** C1 DISTINCT hotfix + C6 extract `services/calendar_labels.py` + Phase A baseline audit. Commit the baseline before anything else.
2. **3 agents in parallel, ~3h:**
   - Agent A: Phase B backfill scripts (B1 + B2 + B5 + B7) — non-UI, isolated in `scripts/` and `alembic/versions/`.
   - Agent B: Phase C read-path rewrite (C2 + C3 + C4 + C5) in `services/storage.py` + `routes/pages.py`.
   - Agent C: Phase D + F UI pass (D1 + D2 + D3 + D5 + D6 + D7 + F1 + F2) — `templates/calendar.html`, `templates/meeting.html`, `static/css/pages/calendar.css`, `static/css/pages/meeting.css`, auth.css cal-* rules. **Oatmeal polish** same as WS8f's subpage polish.
3. **Solo, ~15 min:** run Phase A re-audit against the post-B numbers; verify acceptance criteria thresholds; Playwright 20-meeting UI smoke; commit.

B3 (WS11 cross-ref) and B6 (WS5a cross-ref) are fire-and-forget pings — open a note in WS11 / WS5a handoffs and move on.

Total budget: ~4–5 hours in one session, same shape as WS8f Phase 7+.

---

## Acceptance criteria

**Numeric (vs Phase A baseline):**

- [ ] 2023-2026 meetings with ≥1 agenda_item showing ≥1 `bijlage` (excl. procedural-only): **≥95%**
- [ ] Duplicate junction rows: **0** (B4 enforced)
- [ ] Duplicate meeting rows on /calendar: **0** logical duplicates (B7 enforced; A7 re-run confirms)
- [ ] Duplicate documents per agenda item on /calendar: **0** (walk all 2023-2026 meetings, assert uniqueness)
- [ ] 2024 meetings with annotaties-only / zero bijlagen: **drop ≥60%**
- [ ] `documents` rows with direct FKs but missing junction row: **0**

**Manual UI smoke (staging before prod):**

- [ ] 5 random meetings per year × {2023, 2024, 2025, 2026} = 20 meetings
- [ ] Bijlagen present + visually distinct from annotaties per meeting
- [ ] No duplicates (document OR meeting level)
- [ ] Mobile 375px renders cleanly

**No regressions:**

- [ ] `pytest tests/` green
- [ ] Playwright visual diff vs `review-2-calendar-desktop.png` — list view layout unchanged
- [ ] `?q=…`, committee filter, `show_empty` toggle still work
- [ ] `get_meetings_filtered(year=2025)` cardinality unchanged pre/post

---

## Eval gate

| Metric | Source | Target |
|---|---|---|
| Bijlage visibility | A1 re-run after B1/B3 | ≥ 95% of 2023-2026 substantive meetings show ≥1 bijlage |
| Meeting deduplication | A7 re-run | 0 logical-duplicate groups |
| Junction integrity | A3 re-run | 0 duplicate triples |
| UI discrimination | Playwright 20-meeting suite | bijlage/annotatie visually distinct, no UI duplication |

---

## Risks specific to this workstream

| Risk | Mitigation |
|---|---|
| B5 meeting dedupe reparents docs incorrectly, losing data | Dry-run first; transaction; verify reparented counts match baseline; keep 7-day DB backup per `skills/backup` |
| B7 unique constraint blocks legit edge cases (committee NULL, meeting renamed mid-cycle) | Use `NULLS NOT DISTINCT` + audit pre-migration for any existing NULL committee rows; coordinate with WS5a author when nightly lands |
| C2 JOIN rewrite kills query perf at scale | EXPLAIN ANALYZE before/after; fallback to subquery-materialized CTE if seq scan regresses |
| D1 visual split breaks mobile layout | Playwright 375px + 768px screenshot diff gate |
| WS13 multi-gemeente lands before WS14 | Very unlikely (WS13 not started); but if it does, coordinate `municipality` default-to-rotterdam semantics |
| New duplicate meetings appear between B5 run and B7 migration | Run B5 immediately before B7 in same session; B7 migration fails loudly if duplicates remain |

---

## Workstream hand-offs triggered by WS14

- **WS11** — apply B3 classifier expansion; re-run on 2023-2026 docs
- **WS5a** — (when started) wire `ws14_dedupe_meetings.py` into nightly post-ingest step
- **WS13** — consume `municipality` parameterization delivered in C4/C5; register per-portal classifier profiles
- **WS8f Phase 8** — once `nd-answer` Web Component ships (Shadow DOM + SSE auto-reconnect), admins can embed it on meeting-detail pages for "ask about this meeting" inline UX without redirecting to `/?q=`. F2 becomes richer.

---

## Acceptance additions from WS8f Phase 7+ integration

Layered on top of the existing numeric / smoke / no-regression gates:

- [ ] `services/calendar_labels.py` exists; `/api/calendar/upcoming` AND `get_meetings_filtered` both call `normalize_and_dedupe`; the helper has unit tests covering: (1) weekday-prefix name → "Raadsvergadering", (2) `(date_iso, label)` dedup, (3) empty-committee fallback.
- [ ] Committee filter chips on `/calendar` match the 6-committee list used in chat sidebar ([templates/search.html](../../templates/search.html) `<section data-picker="committee">`). Shared constant.
- [ ] "Vraag erover" deeplink on every meeting row + meeting detail header navigates to `/?q=@<label>%20<date>`. Pre-fills the hero composer. Does NOT auto-submit.
- [ ] Landing page reads `?q=` on load and pre-populates the hero composer textarea; cursor parked at end. Works in both initial state and transitions into chat if user hits `↑`.
- [ ] Oatmeal polish sweep on `/calendar` + `/meeting`: all font-sizes / line-heights / letter-spacings / z-indices / spacings reference tokens. No hardcoded `0.85rem` / `1.1rem` / `rgba(…)` borders. `color-mix` for subtle dividers. Typography passes the same eyeball check as the WS8f-polished subpages.
- [ ] Scrollable lists on `/calendar` + `/meeting` use the mask-fade + color-mix separator pattern from the chat sidebar meeting list.
- [ ] Mobile 375px renders cleanly — heading scale, card gaps, chips all respect tokens.

---

## Carry-over watchlist (shared with WS8f)

Not strictly WS14 scope but relevant context while executing:

- **Migration 0009** (`subscription_tier` on users) — blocked by embedding pipeline; unchanged. Apply in next quiescent window; does not interact with WS14 schema.
- **WS8f Phase 8** — `nd-answer` + `nd-analyse` as Web Components (Shadow DOM + SSE auto-reconnect). Queued. WS14's F2 integration gets lighter once they ship.
- **Subpage 48rem container flag** — Agent A's WS8f handoff flagged that `.subpage` constrained 3-up grids to 48rem; may need bump to 64rem. No impact on `/calendar` or `/meeting` directly, but worth a coordinated call when WS14 does Phase D polish.

---

## Phase A + C1 + C6 shipped 2026-04-15

Safe subset executed in one session. Schema / data writes (Phase B, C2–C5, D, F)
deliberately untouched.

**Files created/changed:**

- `docs/audits/ws14_calendar_baseline_20260415.md` — all 8 read-only audit
  queries (A1–A8) with summary table + run instructions. **Numbers left as
  `TODO` — DB access was sandbox-blocked in the agent session.** Dennis
  runs the queries via `psql -h 127.0.0.1 -U postgres` (tunnel already up)
  or the python snippet in the doc's "Run instructions" section. No SQL
  executed against prod in this session.
- `services/storage.py` (L109–L118) — `get_meeting_details` document SELECT
  now uses `SELECT DISTINCT ON (d.id) d.*` to kill duplicate rows per
  agenda_item when the junction table contains duplicate triples (RC3 in
  the handoff). Row shape and ORDER semantics preserved. Comment tagged
  "WS14 C1 hotfix" for traceability.
- `services/calendar_labels.py` — new module. Extracts the weekday-prefix
  → "Raadsvergadering" substitution + `(date_iso, label)` dedup logic out
  of `/api/calendar/upcoming`. Pure functions, no DB / no FastAPI import.
  Exports `NL_WEEKDAYS`, `NL_MONTHS`, `normalize_label`, `format_date_nl`,
  `normalize_and_dedupe`. `normalize_and_dedupe` is **stable** (preserves
  input order), which matters for the later `sort(... reverse=True)` in
  the API layer.
- `services/test_calendar_labels.py` — plain-assert tests (no pytest
  dependency). Covers: (a) weekday-prefix substitution, (b) (date, label)
  dedup with iBabs+ORI example, (c) empty-committee fallback, plus shape /
  skip-invalid-row / None-input edge cases. Run via
  `python3 services/test_calendar_labels.py`.
- `routes/api.py` (`/api/calendar/upcoming`) — refactored to call
  `normalize_and_dedupe`. Inline NL_MONTHS / weekday tuple / dedup set
  removed. API response shape unchanged (same keys: `id`, `label`,
  `date_short`, `date_nl`, `date_iso`, `is_past`). Ordering unchanged
  (`sort(key=date_iso, reverse=True)` still happens at the route level,
  outside the pure helper). `limit` still clamped `[1, 50]`.

**Explicitly NOT done (deferred to next WS14 session):**

- Phase B: migrations 0011_da_unique / 0012_meeting_logical_unique,
  dedupe script, bijlage backfill. A2 / A4 / A7 baseline numbers are
  preconditions.
- Phase C2 (LEFT JOIN rewrite in `get_meeting_details`), C3 (doc_count
  split), C4 / C5 (municipality param). C1 here is a localized band-aid
  that only removes duplicate-row symptoms; the root-cause junction-table
  dedup still waits on B1.
- Phase D (UI split bijlage/annotatie on calendar.html / meeting.html).
- Phase F (chat-workbench integration).
- Running the audit queries themselves (sandbox-blocked).

**Risks noted:**

- C1 uses `SELECT DISTINCT ON (d.id) d.* ORDER BY d.id, d.name`. The
  outer call site in `get_meeting_details` re-sorts implicitly by
  agenda-item order, so changing the internal document ordering from
  "whatever the junction returned" to "d.id, d.name" is a subtle shift.
  Docs attached to the same agenda item may now appear in a different
  order than before. Acceptable for now (was arbitrary before too); flag
  for Phase D UX polish.
- `normalize_and_dedupe` uses `_dt.utcnow().date()` as the default `today`.
  For a Rotterdam-facing UI this is off by 1 hour from CET but irrelevant
  for date-only comparisons. Explicit `today=` injection available for
  tests.
- The audit doc hard-codes `'rotterdam'` in A7 because `meetings` has no
  `municipality` column pre-WS13. If WS13 adds the column before we
  re-run, update A7 before executing.
