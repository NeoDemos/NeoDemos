# Editor & Widgets — architecture reference

> **Status:** living document.
> **Scope:** admin visual editor (GrapesJS 0.22.15, manual panel mount), chat-workbench landing, drop-in widget blocks, design-token policy.
> **Last updated:** 2026-04-15 (WS8f Phase 7+).

## Visual north-star: Oatmeal, not palette

We adopt the **structural** discipline of Tailwind Plus `oatmeal/olive-instrument`:

- Typography scale: H1 48 / H2 30 / H3 24 / body 16 / small 14 with line-heights 1.15 / 1.3 / 1.3 / 1.6 / 1.6 and `letter-spacing: -0.025em` on display headings.
- Spacing base 4px (`--space-1 = 0.25rem`), 1280px max container (`--container-2xl`), pill-rounded primary CTAs (`border-radius: var(--radius-full)`).
- Subtle dividers via `color-mix(in oklab, var(--color-primary) 10%, transparent)` — defined as `--color-border-subtle`.

We do **NOT** adopt Oatmeal's palette — WS8a locked ours: `--color-primary: #042825` (dark green) + `--color-surface: #f7f4ef` (beige) + `--color-accent: #ff751f` (orange). Changing palette requires a conscious re-lock, not a drive-by.

Fonts are Inter Variable (body) + Instrument Serif (headings) — **self-hosted WOFF2**, never Google Fonts. This is part of the EU sovereignty story on `/technologie`.

## Admin visual editor — state of play

| Feature | Status (post Phase 7+) | Notes |
|---|---|---|
| Drag-drop | ✅ | All ND components draggable at top level; grid cards locked inside parents |
| Block library | ✅ | 32 ND types across 6 categories + 5 new Phase 7+ blocks (nd-image, nd-two-column, nd-faq-accordion, nd-search-widget, nd-calendar-mini) |
| Traits (props) | ✅ | text / textarea / select / number / button types wired; each component has editable props |
| Style manager | ❌ deliberate | `stylable: false` on all ND types — tokens are sacred; no inline overrides |
| Layers panel | ✅ | Native GrapesJS DOM tree |
| Undo/redo | ✅ | Ctrl+Z native + ↶/↷ UI buttons |
| Autosave | ✅ | 2s debounce; status chip "Opgeslagen zojuist" |
| Responsive preview | ✅ | 🖥/📱/📱 toggle (1440/768/375) |
| Asset upload | ✅ | `POST /admin/api/uploads`; 5MB; PNG/JPG/WebP/SVG; `<script>` rejection on SVG |
| Asset picker in editor | 🟡 partial | Traits invoke `open-asset-picker` command; falls back to `prompt()` if no AssetManager hook. Phase 8. |
| Page creation from UI | ✅ | `POST /admin/pages/new` + reserved-slug guard + dynamic `/p/{slug}` rendering |
| Start-from-template | ✅ | Modal surfaces existing `GET /admin/api/page/{slug}/template` |
| Version history | ❌ | Phase 8 — needs `site_pages_history` table |
| Code view | ❌ | Not planned — editing raw HTML defeats typed-component model |
| Collaboration | ❌ | Solo admin for v0.2.0; revisit only if content team scales |

**Upgrade roadmap (post-Phase 7+):**

- **Phase 8** (next session): Web Component wrappers for `nd-answer` + `nd-analyse` with Shadow DOM + SSE auto-reconnect; asset-picker wired into GrapesJS AssetManager; optionally `grapesjs-tui-image-editor` plugin for in-browser crop/resize.
- **Phase 9** (v0.2.1): version history (snapshot grapes_json per publish), Redis-backed conversation store, per-user authenticated chat sessions.
- **Only if needed**: migration away from GrapesJS. Ceiling is real-time collaboration, not feature count. At 3+ editors we'd evaluate Puck / Builder.io / GrapesJS Studio SDK. Single-editor use case never hits the ceiling.

## Chat workbench — contract

Landing (`/`) is now a **chat workbench**, not a search bar.

### Two states

State 1 — **initial**: centered input over skyline hero; quick-action chips (📅 / 📄 / 🏛️) open the sidebar pickers. Hero headline + example queries + demo-answer card visible.

State 2 — **chat**: hero hidden; sidebar available via left edge; thread scrolls as user + assistant bubbles accumulate; composer pinned to bottom with chip rail, quick-action icons, and "↺ Nieuw" to reset.

Transition is CSS-driven via `.chat-workbench[data-state="chat"]` attribute + `body.chat-active` class. No navigation, no SPA framework. One template, two layouts.

### Attached context chips

Three coarse filters, additive, dismissible:

| Chip | Source | Backend param | Effect on orchestrator |
|---|---|---|---|
| 📅 Meeting | `GET /api/calendar/upcoming?limit=50` → sidebar list | `meeting_id` | Appends `[context: vergadering_id=...]` to user turn; orchestrator biases retrieval |
| 📄 Doc type | Static list in sidebar (7 types) | `doc_type` | Appends `[context: documenttype=...]` hint |
| 🏛️ Partij | Static list in sidebar (11 parties) | `partij_ctx` | Overrides saved user partij for this turn only |

The existing `@mention` autocomplete is **preserved verbatim** and **stacks with** these chips. A query can be scoped to "12 feb meeting" + "moties" + `@motie-123` simultaneously.

### Backend session state

```python
# services/conversation_store.py
conversation_store: ConversationStore  # singleton
# Store keeps last 6 turns per session_id, 1h TTL, 5-min background sweep
```

```python
# routes/api.py /api/search/stream contract (Phase 7+)
GET /api/search/stream
  ?q=<query>
  &session_id=<anon-...>        # optional; server creates if missing and emits 'session' event
  &meeting_id=<id>              # optional chip
  &doc_type=<type>              # optional chip
  &partij_ctx=<party>           # optional chip (overrides saved partij)
```

SSE events:

- `{type: "session", session_id}` — emitted upfront so client persists
- `{type: "status", message}` — tool call progress
- `{type: "chunk", text}` — streaming answer text
- `{type: "done", ...metadata}` — final
- `{type: "error", message}` — fault

After `done`, the server appends both user + assistant turns to `ConversationState` so the next turn can pass them as `prior_messages` to the Sonnet orchestrator.

**Single-process caveat:** the store lives in process memory. Multi-worker gunicorn splits sessions. For v0.2.0 deploy single-worker. Redis upgrade is v0.2.1 scope.

## Widget-as-block contract

When a widget is registered as a GrapesJS block, it must follow these rules:

1. **No SSE inside the canvas.** Streaming widgets hang the editor when it enters preview mode (connection drops, no auto-reconnect in canvas). Solution: drop-in blocks link OUT to the canonical page (`nd-search-widget` redirects to `/?q=...`; `nd-calendar-mini` links to `/calendar`).
2. **Self-hydration on public pages.** Widgets that need runtime data (like `nd-calendar-mini`) render stub markup in the editor and are populated by a standalone script (`static/js/*-enhancer.js`) loaded from `base.html`. The enhancer early-returns if no widget elements present — zero-cost on pages without widgets.
3. **Style isolation plan.** Today widgets rely on global CSS scoped by `.nd-*` class names. When we add `nd-answer` and `nd-analyse` (Phase 8), those use broad `.ai-content h1/h2/p/table` selectors that can collide with canvas CSS. Those two widgets get **Web Components with Shadow DOM** — not the pattern for every widget, only those with broad selector surfaces.
4. **Traits bound to model attrs, not HTML attributes.** GrapesJS patterns: use `this.get('foo')` / `this.set('foo', v)` and listen on `change:foo`. (Earlier briefs suggested `change:attributes:foo`; that's wrong for our codebase.)
5. **Class-swap for variants.** Use the existing `swapClass(model, oldCls, newCls)` helper. No inline `style="..."` overrides — tokens stay authoritative.

## Files of record

- [static/admin-editor/components.js](../../static/admin-editor/components.js) — all ND component types + commands
- [templates/admin/editor.html](../../templates/admin/editor.html) — editor shell + block registrations + autosave/undo/device/modal JS
- [services/conversation_store.py](../../services/conversation_store.py) — chat session state
- [services/web_intelligence.py](../../services/web_intelligence.py) `stream()` — accepts `prior_messages` + `attached_context`
- [routes/api.py](../../routes/api.py) — `/api/search/stream`, `/api/calendar/upcoming`
- [routes/admin.py](../../routes/admin.py) — page creation, asset uploads, asset list
- [routes/pages.py](../../routes/pages.py) — dynamic `/p/{slug}` with reserved-slug guard
- [templates/custom_page.html](../../templates/custom_page.html) — shell for custom pages
- [templates/search.html](../../templates/search.html) — chat workbench (two-state)
- [static/css/tokens.css](../../static/css/tokens.css) — expanded design tokens (Oatmeal-aligned scales)
- [static/css/pages/search.css](../../static/css/pages/search.css) — chat/sidebar/composer/bubble styles
- [static/js/calendar-mini-enhancer.js](../../static/js/calendar-mini-enhancer.js) — public-side hydration of `nd-calendar-mini`

## Non-goals

- Dark mode. WS8a palette not designed for it; separate workstream if ever.
- Pricing page. `free_beta` tier until launch — explicit anti-goal.
- Blog / Onderzoeken surfaces. v0.2.1 press launch.
- Party comparison + motie tracker pages. Press-launch sprint, not v0.2.0.
- SaaS CMS (Payload, Builder.io, Strapi, Directus, Sanity, GrapesJS Studio SDK). Rejected 2026-04-14 on cost + EU data residency grounds. Revisit only if editor count scales past 2.
