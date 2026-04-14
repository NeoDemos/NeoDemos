# WS14 — Calendar Quality & Bijlage Reconciliation

> **Status:** `not started` — plan approved 2026-04-14; blocked on WS8f QA for Phase D; C1 hotfix can ship immediately
> **Owner:** `unassigned`
> **Priority:** 1 (Track C launch blocker — press-facing calendar quality)
> **Parallelizable:** no for Phase D (waits on WS8f); yes for B3 (WS11 cross-ref) and B6 (WS5a cross-ref)
> **Target version:** v0.2.0
> **Last updated:** 2026-04-14

---

## TL;DR

Dennis reviewed https://neodemos.nl/calendar and flagged five issues. The list view is good and stays. The rest are data + display bugs: many meetings show no docs, bijlagen are hidden behind annotaties, some docs render twice per agenda item, and some meetings render twice when they are the same logical meeting ingested twice (iBabs + ORI). Fix is four-phase: audit → backfill (junction reconciliation, classifier widening, meeting dedupe) → read-path queries → UI split. Future-proof multi-gemeente by scoping every calendar query through `meetings.municipality`.

---

## Dependencies

| Dependency | Type | Notes |
|---|---|---|
| WS8f `done` | hard (sequencing) | No file overlap but ContentService pattern is used in Phase D copy |
| WS8a-e | completed | Design tokens + calendar list view already shipped |
| WS11 B3 expansion | cross-ref | WS11 owns `doc_classification` — WS14 hands them a patch |
| WS5a nightly pipeline | cross-ref | B6: nightly must call `ws14_dedupe_meetings.py` when WS5a lands |
| Plan file | read first | `/Users/dennistak/.claude/plans/glittery-twirling-prism.md` |

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
- docs/handoffs/WS11_CORPUS_COMPLETENESS.md (doc_classification context)
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
- **B5** Harden `scripts/deduplicate_meetings.py` (already on `main`) — add `--municipality`, `--dry-run`, `--since`, `pg_advisory_lock(42)`. Master = most `agenda_items + document_assignments`; tie-break by earliest `inserted_at` (no `source` column on `meetings`). Reparent agenda_items + documents + junction rows, delete losers. **Cross-confirmed 2026-04-14** via MCP testing: `lijst_vergaderingen` returns same meeting twice (numeric iBabs ID `7491843` with empty `commissie`, plus UUID `6ae82df8-...` with populated `commissie` code). Confirms the direction: master should prefer the record with populated `commissie` code. Surface both IDs in `lijst_vergaderingen` response metadata after dedup so downstream lookups still work. Raw entry: [`brain/FEEDBACK_LOG.md` 2026-04-14 BUG-007](../../brain/FEEDBACK_LOG.md).
- **B6** Add hook to WS5a handoff for nightly wiring **(cross-ref)**
- **B7** Migration `0012_meeting_logical_unique.py` — unique `(municipality, name, start_date, committee)` with NULLS NOT DISTINCT

### Phase C — Read-path fixes

- **C1** Add `DISTINCT` at [services/storage.py:109-118](../../services/storage.py) (1-line hotfix — deployable standalone)
- **C2** Rewrite LEFT JOIN in `get_meetings_filtered` to match junction rows linked at agenda-item level
- **C3** Split `doc_count` into `bijlage_count` + `annotatie_count` + `other_count` via `COUNT … FILTER`
- **C4** Add `municipality: str = 'rotterdam'` to all meetings-touching storage methods
- **C5** `routes/pages.py` — accept `?gemeente=` query param, validate against `data/municipalities_index.json`

### Phase D — UI (waits for WS8f `done`)

- **D1** `templates/meeting.html` — split rendering: **Bijlagen** section (📎, prominent) vs **Annotaties** section (📝, muted). Fallback `📄` for NULL.
- **D2** `templates/calendar.html` list view — show `X agendapunten · Y bijlagen · Z annotaties`. Use `{{ content() }}` for the labels so Dennis can edit via `/admin/content`.
- **D3** `templates/calendar.html` empty-state badge "Geen documenten beschikbaar" for zero-zero meetings. Do NOT hide them.
- **D4** `templates/meeting.html` gemeente badge in header when `meeting.municipality != 'rotterdam'` (inert; WS13-ready).

### Phase E — Multi-gemeente future-proofing

Embedded in B/C/D. Contract: `meetings.municipality` is the single source of truth. Do NOT denormalize municipality onto `document_assignments` unless A1/A2 perf demands it. Per-portal annotatie/bijlage conventions (iBabs vs Notubiz vs Parlaeus) are WS13 scope.

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
