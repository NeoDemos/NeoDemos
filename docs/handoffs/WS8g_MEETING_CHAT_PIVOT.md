# WS8g — Meeting-Level Chat Workbench Pivot

> **Status:** `not started` — plan only; blocked on WS8f Phase 7+ QA
> **Owner:** unassigned
> **Priority:** 2 (launch enhancer — improves investigation workflow, does not block press moment)
> **Parallelizable:** yes with WS14 (different files) — can run after WS8f done
> **Target version:** v0.2.1 candidate (fits v0.2.0 only if WS8f QA passes clean and WS14 Phase D/F land ahead of the press window)
> **Last updated:** 2026-04-15

---

## TL;DR

Today `/meeting/{id}` renders an inline SSE "analyse" side panel the moment the user clicks an agenda item. One click = one shot. No follow-ups, no cross-item comparison, no preserved context. WS8g reshapes the page into the same chat-workbench layout shipped on `/zoeken` in WS8f Phase 7+ (2026-04-15): a left sidebar listing every agenda item, a chat thread in the middle column, and a pinned `.claude-composer` at the bottom — styled identically to `/zoeken`. Clicking an agenda item spawns an assistant turn in the existing thread with the agenda text pre-injected as `[context: agendapunt=…]`; the user can then ask follow-ups or click a second item without losing what was said about the first. A new Profiel preferences form lets users configure the default section headings and custom prompts the analyse response must cover (e.g. "Samenvatting, Standpunten partijen, Relevante moties, Historische context"). Single-thread with context chips is the **recommended** strategy — per-item threading is the fallback if Dennis prefers hard reset on click.

---

## Context / why

- The current analyse box on `/meeting/{id}` forces a "one question per agenda item" UX. After the answer streams, the box is closed; asking "hoe stemde GroenLinks?" means re-clicking, re-firing the full analyse, losing formatting.
- Users can't attach extra context (a motie, a party lens), can't compare two agenda items on the same meeting fluidly, and can't follow up in natural language.
- WS8f Phase 7+ solved all of this on `/zoeken` with the chat-workbench pattern (`conversation_store`, SSE `session_id`, `prior_messages`, `attached_context`, `.claude-composer`, mention-menu). WS8g = bring that same UX one level deeper, scoped to a single meeting.
- Reinforces the WS14 Phase F direction (chat workbench is the canonical "ask about this meeting" surface). WS14 F2 currently routes users from `/meeting` back out to `/?q=...`; WS8g gives them an inline alternative so they never leave the meeting context.
- Profiel preferences close the loop: users who repeatedly want the same analysis shape (press team, council staff) stop retyping instructions.

---

## Dependencies

| Dep | Type | Notes |
|---|---|---|
| WS8f Phase 7+ QA | hard | Need `services/conversation_store.py`, `web_intelligence.stream(prior_messages=, attached_context=)`, `GET /api/search/stream?session_id=&meeting_id=&doc_type=&partij_ctx=`, Claude composer CSS, mention-menu JS — all shipped 2026-04-15, pending Dennis QA. |
| WS14 Phase D/F | soft cross-ref | WS14 D5/F1 deeplink `/?q=@meeting` takes users *out* of `/meeting`. WS8g is the richer inline alternative. Ship WS14 first; WS8g is the follow-up that makes the deeplink optional rather than mandatory. |
| WS9 MCP tool loop | completed | `services/web_intelligence.py` agentic loop + `agentic_meeting_analysis` already uses MCP tools to find sources. Reuse as-is. |
| `services/avatars.py` (WS8f Phase 7+) | completed | Not blocking. Profiel form reuses the existing `avatar_gallery()` picker. |
| Migration 0009 (`subscription_tier`) | soft | Must land before any new `users` column migration (avoid revision-chain conflicts). Documented in WS8f Known limitations. |

---

## UX specification

Target layout — desktop ≥1200px:

```
┌──────────────────────────────────────────────────────────────────────┐
│  Raadsvergadering — donderdag 15 april 2026       [gemeente: rotterdam] │
├──────────────┬────────────────────────────────────────┬──────────────┤
│ Agendapunten │ Chat thread                            │ Bronnen      │
│              │                                        │ (desktop)    │
│ ▸ 1 Opening  │  ┌──────────────────────────────────┐ │              │
│ ● 2 Wonen    │  │ U: Analyseer "2 Woningbouw…"    │ │ · Motie 123  │
│ ▸ 3 Begrot.  │  └──────────────────────────────────┘ │ · Notulen §4 │
│ ▸ 4 Motie X  │  ┌──────────────────────────────────┐ │ · Bijlage 7  │
│ ▸ 5 Sluiting │  │ Assistent: (streaming…)          │ │              │
│              │  │ # Samenvatting                   │ │              │
│ [chip: ap#2] │  │ # Standpunten partijen           │ │              │
│              │  │ # Relevante moties               │ │              │
│              │  │ # Historische context            │ │              │
│              │  └──────────────────────────────────┘ │              │
│              │                                        │              │
│              ├────────────────────────────────────────┤              │
│              │  ⌨ Vraag door over dit agendapunt…  ↵ │              │
└──────────────┴────────────────────────────────────────┴──────────────┘
```

- **Left sidebar** (reuses `.chat-sidebar` from `static/css/pages/search.css`): numbered agenda list. Active item gets the `● ` bullet + `--color-accent` left-border. `data-agenda-item-id` on each row.
- **Middle column** (`.chat-thread` + `.chat-workbench` shell): header = meeting name + date + `gemeente` badge (WS14 D4 alignment). Empty state copy: "Kies een agendapunt om te beginnen". On click → append a user bubble ("Analyseer agendapunt *{seq}. {title}*") + stream the assistant bubble.
- **Composer** (`.claude-composer` pinned bottom): identical to `/zoeken`. `@mention` autocomplete preserved. Hidden while thread is empty; fades in after the first assistant turn.
- **Right rail (desktop only, ≥1200px)**: `.chat-sources` populated by SSE `tool_result` events as MCP tools return. Collapses to inline `<details>` on narrow screens.
- **Mobile (<768px)**: sidebar collapses into a horizontal scrollable chip row above the thread. Selecting an item auto-scrolls chat into view; composer is sticky at viewport bottom.

### Key design decisions (need Dennis sign-off)

1. **Context switch strategy — recommended: single thread + context chips (Option B).** When user clicks agenda item #5 after already discussing #2, the existing thread is preserved. A dismissible chip `ap#5 • Begroting Wonen` appears above the composer, and the spawned user bubble reads "Analyseer agendapunt 5. Begroting Wonen". The orchestrator receives `attached_context={"meeting_id": ..., "agenda_item_id": 5}` plus full `prior_messages`. Rationale: matches `/zoeken`'s sidebar-picker pattern exactly (meeting + doc_type + partij chips already work this way), keeps the user's investigative flow intact, and cross-item comparison ("vergelijk met agendapunt 2") becomes a natural follow-up. Option A (new thread per click) is the fallback if QA finds the context bleeds poorly.
2. **First-click payload to orchestrator.** Include `(a)` agenda-item title + seq, `(b)` the agenda-item description text if present, `(c)` titles of documents attached via `document_assignments`, `(d)` the user's `analysis_sections` list rendered as a markdown heading skeleton the model should fill. Sent as an appended `[context: agendapunt=…]` hint, same shape as existing `meeting_id` hint.
3. **Profile prompts → system blocks.** `analysis_sections` and `custom_prompts` get appended to `web_intelligence._build_system_blocks(partij)` output via a new `user_preferences` param, behind a feature flag so anon users keep the current default.

---

## Profile schema additions

New Alembic migration `20260416_0010_user_analysis_preferences.py` (requires 0009 `subscription_tier` to land first):

```python
# alembic/versions/20260416_0010_user_analysis_preferences.py
revision = "0010_user_analysis_preferences"
down_revision = "0009_subscription_tier"

def upgrade():
    op.add_column("users", sa.Column("analysis_sections", JSONB,
        nullable=False,
        server_default=sa.text("""'["Samenvatting","Standpunten partijen","Relevante moties","Historische context"]'::jsonb""")))
    op.add_column("users", sa.Column("custom_prompts", ARRAY(sa.Text), nullable=False, server_default="{}"))
    op.add_column("users", sa.Column("avatar_slug", sa.Text, nullable=True))
```

- `analysis_sections JSONB` — ordered list of Dutch headings the analyse response must cover. Default = `["Samenvatting", "Standpunten partijen", "Relevante moties", "Historische context"]`.
- `custom_prompts TEXT[]` — user-authored follow-up prompts; rendered as auto-suggest chips under the composer after the first turn.
- `avatar_slug TEXT NULL` — override for auto-assigned civic avatar from `services/avatars.py`. Cross-refs WS8f Phase 7+ Profiel picker (still deferred).

---

## Backend contract

- **SSE endpoint extension.** `GET /api/search/stream` in `routes/api.py` gains `agenda_item_id: int | None = None`. When set, orchestrator hydrates the agenda item via `storage.get_agenda_item(id)` (add if missing) and injects `[context: agendapunt=<seq>. <title>; documenten=<titles>]` into the user turn. `meeting_id` is always sent alongside.
- **New service `services/analysis_preferences.py`.** `get(user_id) -> UserPreferences` + `update(user_id, sections=None, prompts=None, avatar=None)`. Request-scoped cache (per-request memoize; no global 60s TTL — preferences change rarely but stale reads in-flight would be confusing).
- **Orchestrator plumbing.** `web_intelligence.stream(…, user_preferences=None)`. When preferences present, `_build_system_blocks(partij)` appends a "Structuur van je antwoord" block listing `analysis_sections` as required H2 headings, and a "Voorkeuren van de gebruiker" block listing `custom_prompts` as context only (not instructions).
- **Composer follow-ups** reuse the existing `session_id` contract — same conversation store, same 1h TTL, same MAX_TURNS_PER_SESSION=6. Namespace `session_id` by prefix `meeting:{id}:{uuid}` to avoid collision with `/zoeken` sessions (see Risks).

---

## Task breakdown

### Phase A — Read-only audit / plan confirmation (~0.5h)

- Verify WS8f Phase 7+ landed and QA passed (`docs/handoffs/README.md` shows `done`).
- Confirm migration 0009 applied in prod (`alembic current`).
- Read [templates/meeting.html](../../templates/meeting.html), [routes/pages.py](../../routes/pages.py) meeting route, [services/web_intelligence.py](../../services/web_intelligence.py) `stream()`, [services/conversation_store.py](../../services/conversation_store.py), [static/css/pages/search.css](../../static/css/pages/search.css) chat-workbench block.
- Decide Option A vs Option B with Dennis (default: Option B — see UX decision #1).

### Phase B — Backend + migration (~1.5h, Agent A)

- Migration `0010_user_analysis_preferences.py`.
- `services/analysis_preferences.py` with tests.
- `routes/api.py` — add `agenda_item_id` query param to `/api/search/stream`; pass through to `web_intelligence.stream()`.
- `services/web_intelligence.py` — accept `agenda_item_id` + `user_preferences`; extend `_build_system_blocks` and the `[context: …]` hint renderer.
- `services/storage.py` — add `get_agenda_item(id)` if not present (returns seq, title, description, attached-doc titles).

### Phase C — Frontend restructure + hydration JS (~2h, Agent B)

- Rewrite [templates/meeting.html](../../templates/meeting.html) into the 3-column `.chat-workbench[data-state="initial"]` shell. Agenda list in `.chat-sidebar`, thread container in `.chat-thread`, `.claude-composer` pinned. Preserve existing meeting header + Bijlagen/Annotaties tabs on a secondary route or collapsed accordion (acceptance: users can still reach the raw doc list — no regression).
- New `static/js/meeting-chat.js` — converts agenda-item click into an SSE chat turn. Reuses the `/zoeken` stream handler verbatim (extract shared helpers into `static/js/chat-workbench.js` if not already done).
- Pull `.claude-composer` + `.chat-bubble` + `.chat-sources` CSS from [static/css/pages/search.css](../../static/css/pages/search.css) into a shared `@layer components` block if not already shared. Avoid copy-paste.
- Mobile: sidebar collapse + auto-scroll.

### Phase D — Profiel preferences form (~1h, Agent C)

- [templates/profiel.html](../../templates/profiel.html) — section editor (chips user can re-order via drag or arrow buttons), custom-prompt textarea (one per line), avatar picker from `avatar_gallery()`.
- `POST /profiel/preferences` in `routes/pages.py` → `analysis_preferences.update(...)`.
- Server-side validation: max 8 sections, max 200 chars each; max 10 custom prompts, max 500 chars each.

### Phase E — Docs (~0.5h, solo)

- Extend [docs/architecture/EDITOR_AND_WIDGETS.md](../architecture/EDITOR_AND_WIDGETS.md) with a "Chat-workbench cross-page pattern" section describing the shared shell + how `/zoeken`, `/meeting/{id}`, and the future `nd-answer` Web Component all consume it.
- Update [docs/handoffs/README.md](README.md) inbound refs (coordination script regenerates).

---

## Execution recommendation

~5–6 hours in one focused session; same 3-agent-parallel shape as WS8f Phase 7+ and WS14 Phase G:

- **Agent A**: Phase B (backend + migration + orchestrator plumbing).
- **Agent B**: Phase C (template rewrite + hydration JS + CSS share-extract).
- **Agent C**: Phase D (Profiel form + route + validation).

Then solo: Phase E docs + acceptance tests + Playwright smoke covering (i) click item → turn spawns, (ii) follow-up reuses session, (iii) second item click preserves thread + adds chip, (iv) preferences persist and affect next turn's headings.

---

## Acceptance criteria

- [ ] Migration `0010_user_analysis_preferences` applies cleanly on staging + prod.
- [ ] `/meeting/{id}` renders the 3-column chat-workbench layout at ≥1200px; collapses correctly at 768px and 375px.
- [ ] Clicking an agenda item appends a user bubble + streams an assistant bubble whose H2 headings match the user's `analysis_sections` (logged-in) or the default list (anon).
- [ ] Typing a follow-up in the composer reuses the same `session_id`; orchestrator receives `prior_messages` with the prior turn.
- [ ] Clicking a second agenda item: thread is preserved, new chip `ap#{seq}` appears above composer, new user bubble references the new item.
- [ ] `/profiel` preferences form round-trips: edit sections → save → reload → values persist → next analyse turn uses them.
- [ ] `@mention` autocomplete works inside the meeting composer identical to `/zoeken`.
- [ ] Anon users see default sections; no 500s when `user_preferences=None`.
- [ ] Existing Bijlagen/Annotaties rendering (WS14 D1) remains accessible (accordion or secondary tab) — no regression.
- [ ] No visible style drift from `/zoeken` chat-workbench (same typography scale, same pill composer, same bubble radii).
- [ ] Playwright smoke green.

---

## Risks

1. **Session_id collision between `/zoeken` and `/meeting`.** Fix: namespace with `meeting:{id}:` prefix when creating sessions from the meeting page; store under a separate `sessionStorage` key `neodemos_meeting_chat_session`.
2. **CSS collision when `.claude-composer` mounts inside `/meeting`.** `templates/meeting.html` currently loads `pages/meeting.css` after `pages/search.css`; any overriding selectors in meeting.css could clobber the composer. Audit during Phase C; factor shared rules into `components.css` if needed.
3. **Loss of current analyse flow if fallback not maintained.** Press users may have a bookmark mid-analyse; keep `/meeting/{id}?view=legacy` rendering the old inline panel for one release. Remove in v0.2.1.
4. **Migration ordering.** 0010 depends on 0009 (`subscription_tier`) which is still deferred behind the long embedding pipeline (WS8f Known limitations). Do not ship 0010 until 0009 is live; otherwise revision chain breaks on fresh dbs.
5. **Single-process `conversation_store`.** Multi-worker gunicorn would split sessions mid-conversation. Same limitation as WS8f. Redis upgrade tracked in WS4 post-ship.
6. **Token budget inflation.** Sending full `prior_messages` + agenda-item text + document titles + user preferences on every click risks hitting per-request token caps. Mitigation: keep MAX_TURNS_PER_SESSION=6; truncate agenda-item description to 2000 chars; surface document titles only, never body.
7. **Per-item vs single-thread confusion.** If Dennis picks Option A after all, the chip UI + "Analyseer agendapunt X" bubble still works — but the visual signal that context resets must be loud (divider row, "Nieuw gesprek gestart" hint). Document clearly in the template.
8. **Profile form security.** `custom_prompts` accept free text — must NOT be injected as a system instruction, only as a user-facing context block. Phase B explicit: render under "Voorkeuren van de gebruiker" header, never inside an instruction block.

---

## Files to be created or modified

**Created:**

- `/Users/dennistak/Documents/Final Frontier/NeoDemos/alembic/versions/20260416_0010_user_analysis_preferences.py`
- `/Users/dennistak/Documents/Final Frontier/NeoDemos/services/analysis_preferences.py`
- `/Users/dennistak/Documents/Final Frontier/NeoDemos/static/js/meeting-chat.js`
- `/Users/dennistak/Documents/Final Frontier/NeoDemos/static/js/chat-workbench.js` (shared helper — extracted if not already)
- `/Users/dennistak/Documents/Final Frontier/NeoDemos/tests/test_analysis_preferences.py`
- `/Users/dennistak/Documents/Final Frontier/NeoDemos/tests/playwright/test_meeting_chat.py`

**Modified:**

- `/Users/dennistak/Documents/Final Frontier/NeoDemos/templates/meeting.html` (restructure)
- `/Users/dennistak/Documents/Final Frontier/NeoDemos/templates/profiel.html` (preferences form)
- `/Users/dennistak/Documents/Final Frontier/NeoDemos/routes/api.py` (`agenda_item_id` on SSE)
- `/Users/dennistak/Documents/Final Frontier/NeoDemos/routes/pages.py` (meeting route + `/profiel/preferences` POST)
- `/Users/dennistak/Documents/Final Frontier/NeoDemos/services/web_intelligence.py` (`user_preferences` + `agenda_item_id` injection)
- `/Users/dennistak/Documents/Final Frontier/NeoDemos/services/storage.py` (`get_agenda_item` if missing)
- `/Users/dennistak/Documents/Final Frontier/NeoDemos/static/css/pages/meeting.css` (3-column layout)
- `/Users/dennistak/Documents/Final Frontier/NeoDemos/static/css/components.css` (shared composer/bubble extract)
- `/Users/dennistak/Documents/Final Frontier/NeoDemos/docs/architecture/EDITOR_AND_WIDGETS.md` (cross-page pattern)

---

## Workstream hand-offs triggered

- **WS8f Phase 8** — the deferred `nd-answer` Web Component becomes more valuable once this pivot exists. The meeting-page analyse bubble is the natural first consumer of the Shadow-DOM-isolated streaming widget; once WS8g ships, Phase 8 can reuse the same `prior_messages` + `attached_context` shape without inventing a new contract.
- **WS14 Phase F2** — F2 envisions a meeting-page inline composer that routes back to `/?q=…`. WS8g supersedes it: the inline composer stays on `/meeting/{id}` and talks to `/api/search/stream` directly. Mark F2 as "superseded by WS8g" once this ships.
- **WS4 post-ship (Redis conversation_store)** — pressure increases: two chat surfaces now depend on the single-process store.
