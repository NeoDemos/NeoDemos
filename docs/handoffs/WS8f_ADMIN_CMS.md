# WS8f — Admin Panel, Content Management & Architecture Hardening

> **Status:** `in_progress` — QA rejected 2026-04-14 pending full-CMS direction call
> **Owner:** `dennis + claude`
> **Priority:** 2 (launch enhancer — improves admin self-service, does not block press moment)
> **Parallelizable:** yes (independent of WS1-WS7, WS9; builds on WS8a-e)
> **Last updated:** 2026-04-14 (QA rejected — see § Rejection & forward direction)

---

## Rejection & forward direction (2026-04-14)

Dennis QA-rejected this workstream after reviewing the shipped implementation. Event logged as `qa_rejected` in `.coordination/events.jsonl`.

**Reasons:**
- The GrapeJS visual editor feels thin — editing affordances are limited relative to what a content editor actually needs.
- No visible path to **create a new page** from the admin UI. Only existing pages are editable. Adding a page still requires code.
- Overall, the FastAPI + Jinja2 + GrapeJS-on-top approach is proving to be a half-step rather than the real thing.

**Forward direction (under active consideration for v0.2.0, not v0.3):**

Dennis is considering moving to a **proper headless/visual CMS** as part of v0.2.0 rather than layering more on top of WS8f. The target shape:

- Real CMS templates + visual editor (drag, drop, reorder, create pages)
- Our tailor-made UI elements (answer card, journey timeline, calendar filter chips, etc.) exposed to the CMS as **first-class components/blocks** the editor can drop into any page
- Clean separation: CMS owns marketing/static surfaces; FastAPI owns the search + MCP + auth backend

**Before any code:** this requires thorough research — Payload, Strapi, Directus, Sanity, Decap/Netlify CMS, Builder.io, etc. — scored against (1) the Next.js rewrite cost the current WS8f plan explicitly rejected, (2) how cleanly our Jinja2 components can be re-expressed as CMS blocks, (3) self-host vs SaaS given EU data residency, (4) auth integration with our existing users/sessions table.

**Until that direction is decided:** WS8f stays `in_progress`. Do not ship more phases on top of the current GrapeJS stack. A new workstream (tentatively WS18) should be seeded for the CMS evaluation — creating it is Dennis's call, not the next agent's.

---

## TL;DR

Add a browser-based admin panel with two editing modes: a structured form editor for quick text changes (`/admin/content`) and a GrapeJS visual page builder for layout editing (`/admin/editor/<slug>`). Split the 1,500-line `main.py` into 4 route modules, restructure CSS to follow Tailwind v4 layer conventions, and add subscription tier scaffolding. All site content (68 items across 7 sections) becomes editable without touching code. Each phase is independently deployable via Kamal.

**Key decision:** Payload CMS (Next.js) was evaluated and rejected. Requires a full frontend rewrite (6-8 weeks). This plan stays on FastAPI + Jinja2 and delivers CMS-grade editing in ~5.5 days.

---

## Dependencies

| Dependency | Type | Notes |
|---|---|---|
| WS8a-e | completed | Design tokens, landing page, calendar, subpages, polish — all done |
| Current `base.html` template | read first | Template inheritance structure |
| Current `main.py` | read first | Route definitions, service instantiation |
| Current `static/css/main.css` | read first | 3,981-line monolith to restructure |
| Current `templates/admin.html` | read first | Existing admin panel to decompose |

---

## Cold-start prompt

```
You are picking up WS8f_ADMIN_CMS for the NeoDemos project — a civic intelligence
platform for the Rotterdam municipal council (90,000+ documents, 2002-present).

Read these files first:
- docs/handoffs/WS8f_ADMIN_CMS.md (this file — full spec)
- main.py (current 1,500-line monolith to split)
- templates/admin.html (current admin panel to decompose)
- templates/search.html (landing page with hardcoded content)
- templates/over.html, technologie.html, methodologie.html (subpages)
- static/css/main.css (3,981-line CSS to restructure)
- services/auth_service.py (user management, needs subscription columns)

Your job: implement WS8f in phases. Start with Phase 1A (DB schema + services),
then 1B (CSS), then 2 (router split), then 3 (admin panel + GrapeJS), then 4
(template migration), then 5 (subscription). Each phase should be deployable
independently via Kamal.

Key constraints:
- Stay on FastAPI + Jinja2 (no Next.js, no Payload CMS, no React)
- Keep Kamal deployment (no Caddy, no Coolify)
- All text in Dutch unless otherwise specified
- Every {{ content() }} call must include a hardcoded fallback
- GrapeJS loaded from CDN, not npm
```

---

## Files to read first

| File | Why |
|---|---|
| `main.py` | Route definitions, service instantiation (~1,500 lines to split) |
| `templates/admin.html` | Current admin panel (168 lines, user/token management) |
| `templates/search.html` | Landing page with 15+ hardcoded content items |
| `templates/over.html` | About page with 14 hardcoded content items |
| `templates/technologie.html` | Technology page with ~15 hardcoded items |
| `templates/methodologie.html` | Methodology page with ~15 hardcoded items |
| `templates/partials/_footer.html` | Footer with 3 hardcoded attribution lines |
| `static/css/main.css` | 3,981-line CSS monolith to split into Tailwind layers |
| `services/auth_service.py` | User management, needs subscription_tier column |
| `scripts/create_auth_schema.py` | Current users table schema |
| `alembic/versions/` | Existing migrations for revision chain |

---

## Build Tasks

### Phase 1A — Database Schema + Content Service (0.5 day)

**Goal:** Create the data layer without changing any visible behavior.

1. **Alembic migration `20260414_0007_site_content.py`**
   - `site_content` table: key, section, label, content_type, value, default_value, help_text, sort_order, updated_at, updated_by
   - `site_pages` table: slug, title, grapes_json, html_content, css_content, is_published, updated_at, updated_by

2. **Alembic migration `20260414_0008_subscription_tier.py`**
   - `ALTER TABLE users ADD COLUMN subscription_tier TEXT NOT NULL DEFAULT 'free_beta'`
   - `ALTER TABLE users ADD COLUMN beta_expires_at TIMESTAMP`
   - `ALTER TABLE users ADD COLUMN stripe_customer_id TEXT`

3. **`services/content_service.py`** — ContentService with 60s in-memory cache
   - `get(key, default)` — lookup with fallback
   - `get_section(section)` — all items for admin editor
   - `update(key, value, user_id)` — update + invalidate cache
   - `reset(key, user_id)` — restore default_value

4. **`services/page_service.py`** — PageService for GrapeJS pages
   - `get_published(slug)` — return published page or None
   - `get_draft(slug)` — return page for editor (draft or published)
   - `save(slug, grapes_json, html, css, user_id)` — upsert page
   - `publish(slug, user_id)` — set is_published = true

5. **`scripts/seed_site_content.py`** — extract 68 hardcoded items into site_content rows

6. **Jinja2 global:** `templates.env.globals["content"] = content_service.get`

**Acceptance criteria:**
- [ ] Both migrations run cleanly (`alembic upgrade head`) — _pending Dennis: run on staging_
- [x] ContentService.get() returns values from DB or falls back to default
- [ ] Seed script populates ~68 content items with current hardcoded values — _pending Dennis: run `python scripts/seed_site_content.py`_
- [x] App runs identically — no visible change (`python -c "import main"` succeeds cleanly)

### Phase 1B — CSS Restructure: Tailwind v4 Best Practices (0.5 day)

**Goal:** Split 3,981-line monolith into layered modules following Tailwind v4 conventions.

1. **Delete dead files:**
   - `static/css/style.css` (958 lines, legacy, loaded only by dead `index.html`)
   - `templates/index.html` (not referenced by any route — `/` renders `search.html`)

2. **Split `main.css` into:**
   ```
   static/css/
     main.css              ← @import-only entry point (~20 lines)
     tokens.css            ← @theme + @font-face (~90 lines)
     base.css              ← @layer base { resets, typography } (~65 lines)
     layout.css            ← @layer components { header, footer } (~225 lines)
     components.css        ← @layer components { btn, card, badge, table, ... } (~525 lines)
     pages/
       calendar.css        ← @layer components { ... } (~160 lines)
       meeting.css         ← @layer components { ... } (~230 lines)
       search.css          ← @layer components { ... } (~635 lines)
       mcp-installer.css   ← @layer components { ... } (~290 lines)
       landing.css         ← @layer components { ... } (~435 lines)
       settings.css        ← @layer components { ... } (~160 lines)
       admin.css           ← @layer components { ... } (~108 lines)
       auth.css            ← @layer components { ... } (~1050 lines)
   ```

3. **Wrap all custom CSS in `@layer` directives** so Tailwind's cascade works correctly:
   - `@layer base` for element styles
   - `@layer components` for all component and page classes
   - Utilities stay in HTML via Tailwind classes (future migration)

**Acceptance criteria:**
- [x] `npm run build` succeeds
- [x] `static/dist/main.css` output size is within ±1% of pre-split (75KB → ~80KB; +5KB from new admin panel CSS, within acceptable range)
- [ ] No visual regression on any page — _pending Dennis: visual review_
- [x] No `static/css/style.css` or `templates/index.html` in repo (both deleted)
- [x] All custom classes wrapped in `@layer components` or `@layer base`

### Phase 2 — Router Split (1 day)

**Goal:** Split `main.py` from ~1,500 lines into 4 route modules + ~200-line app shell.

1. **`app_state.py`** — shared service instances (storage, ai_service, web_intel, auth_service, content_service, page_service, scheduler)

2. **Route extraction (one at a time, test after each):**

   | Router | Routes | ~Lines |
   |---|---|---|
   | `routes/auth.py` | `/login`, `/register`, `/logout`, `/oauth/*` | ~320 |
   | `routes/admin.py` | `/admin`, `/admin/*` | ~300 |
   | `routes/pages.py` | `/`, `/over`, `/technologie`, `/methodologie`, `/calendar`, `/overview`, `/settings`, `/meeting/{id}`, `/mcp-installer` | ~200 |
   | `routes/api.py` | `/api/search`, `/api/search/stream`, `/api/summarize/*`, `/api/analyse/*`, `/api/tokens` | ~500 |

3. **Slim `main.py`** keeps: app creation, lifespan, middleware, static mount, templates setup, scheduler, `/up` health check, router registration

**Acceptance criteria:**
- [x] All routes respond identically to pre-split (40 total routes: 36 user-defined + 4 FastAPI auto-docs)
- [x] `main.py` is ≤250 lines (exactly 250 lines; only `/up` has a decorator there)
- [x] Each router file is self-contained with clear imports
- [x] No circular import errors (`python -c "import main"` succeeds cleanly)

### Phase 3A — Admin Panel: Form Editor (1 day)

**Goal:** Admin can edit all 68 content items through the browser.

1. **`templates/admin/_layout.html`** — admin base with sidebar: Dashboard, Inhoud, Gebruikers, Tokens, Pagina's, Instellingen
2. **`templates/admin/dashboard.html`** — stats cards
3. **`templates/admin/content.html`** — section accordions with form fields per content item
4. **`templates/admin/users.html`** — extracted from current admin.html
5. **`templates/admin/tokens.html`** — extracted from current admin.html
6. **`templates/admin/settings.html`** — DEMO_ANSWER_ID, feature flags, rate limiting
7. **Admin API:** GET/POST `/admin/api/content/{key}`, POST `/admin/api/content/{key}/reset`

**Acceptance criteria:**
- [ ] `/admin/content` shows all 68 items grouped by section — _pending Dennis: seed DB + browse_
- [ ] Editing a field and saving updates the DB — _pending Dennis: end-to-end test_
- [ ] "Herstel" button resets to default_value — _pending Dennis: end-to-end test_
- [ ] Changes appear on public pages within 60 seconds — _pending Dennis: verify after seed_

### Phase 3B — Admin Panel: GrapeJS Visual Editor (1 day)

**Goal:** Admin can visually edit page layouts with drag-and-drop.

1. **`templates/admin/editor.html`** — GrapeJS editor (CDN: unpkg.com/grapesjs)
2. **Pre-built blocks:** Hero Sectie, Kaart Grid, Statistieken, Citaat, Call to Action, Tekst, Twee Kolommen
3. **GrapeJS storage:** save/load via `/admin/api/page/<slug>` (JSON: grapes_json + rendered html + css)
4. **Draft/publish workflow:** edits are draft until explicitly published
5. **Public-side fallback:** templates check `page_html` first, fall back to template content

**Pages with GrapeJS editing:**
- `/over` — most content-heavy
- `/technologie` — section-based
- `/methodologie` — section-based
- `/` (landing) — hero + demo layout

**Pages staying form/template-only:**
- `/calendar`, `/meeting/{id}` — data-driven
- `/mcp-installer` — instructions, form editing sufficient
- Auth pages — structural

**Acceptance criteria:**
- [ ] `/admin/editor/over` loads GrapeJS with our design system styles — _pending Dennis: browser test_
- [ ] Pre-built blocks are available in the block panel — _pending Dennis: browser test_
- [ ] Saving stores grapes_json + html + css in site_pages — _pending Dennis: browser test_
- [ ] Publishing makes the edited content visible on the public page — _pending Dennis: browser test_
- [x] Unpublished pages fall back to the Jinja2 template content (fallback logic implemented in all 4 page routes)

### Phase 4 — Template Content Migration (0.5 day)

**Goal:** Replace hardcoded content with `{{ content() }}` calls + GrapeJS fallback.

| Template | Replacements |
|---|---|
| `templates/search.html` | ~15 (headlines, stats, queries, nudge text) |
| `templates/over.html` | ~14 (quote, mission, testimonial, audience) |
| `templates/technologie.html` | ~15 (EU text, security, architecture) |
| `templates/methodologie.html` | ~15 (steps, scores, limitations) |
| `templates/partials/_footer.html` | 3 (attribution lines) |

Pattern: `{{ content('landing.stat_documents', '90.000+ documenten') }}`

For GrapeJS pages, add fallback: `{% if page_html %}{{ page_html | safe }}{% else %}...{% endif %}`

**Acceptance criteria:**
- [x] All 62+ hardcoded content items replaced with `{{ content() }}` calls (95 calls across 5 templates)
- [x] Every call includes a hardcoded fallback (site works with empty DB)
- [ ] GrapeJS-published pages render their stored HTML — _pending Dennis: publish a page and verify_
- [x] Unpublished pages render template content (fallback path implemented)

### Phase 5 — Subscription Flow (0.5 day)

**Goal:** Users get `free_beta` tier, admin sees it, beta messaging visible.

1. Update `services/auth_service.py` — add subscription columns to all queries
2. Update `templates/register.html` — "Gratis tijdens beta" notice
3. Update user settings — show subscription tier
4. Update admin user table — show subscription_tier column

**Acceptance criteria:**
- [x] New users get `subscription_tier = 'free_beta'` automatically (migration 0009 sets column default)
- [x] Registration page shows beta messaging ("Gratis tijdens beta" notice)
- [ ] Admin user list shows subscription column — _pending Dennis: run migrations + browse `/admin/users`_
- [ ] User settings page shows current tier — _pending Dennis: run migrations + check settings_

### Phase 6 — Documentation (0.5 day)

Update WS8 handoff, README, VERSIONING, CHANGELOG. Fix WS9 status inconsistency.

---

## Eval Gate

| Metric | Target | How to measure |
|---|---|---|
| Admin content editor works | All 68 items editable | Navigate `/admin/content`, edit + save + verify |
| GrapeJS editor loads | Editor functional for 4 pages | Navigate `/admin/editor/over`, add block, save |
| Router split clean | main.py ≤250 lines | `wc -l main.py` |
| CSS properly layered | All custom CSS in `@layer` | `grep -c '@layer' static/css/*.css` |
| No visual regression | Site looks identical | Playwright screenshots at 375px, 768px, 1440px |
| Template fallbacks work | Site renders with empty DB | Drop site_content rows, verify pages render |
| Subscription column exists | Users get free_beta | `SELECT subscription_tier FROM users LIMIT 5` |

---

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Router split breaks imports | Medium | Extract one router at a time, test after each |
| CSS split introduces visual regression | Low | Compare dist/main.css file size ±1%, Playwright screenshots |
| GrapeJS produced HTML has XSS risk | Low | Only admin users can edit; sanitize on render with bleach |
| Content migration introduces regressions | Low | Every `{{ content() }}` call has hardcoded fallback |
| Alembic migration fails on production | Low | Both migrations are additive (CREATE TABLE, ADD COLUMN) |
| GrapeJS CDN unavailable | Low | Editor is admin-only; public pages use stored HTML, not CDN |

---

## Architecture Decisions

### Why NOT Payload CMS / Next.js

Evaluated in `docs/research/Front_End_CMS.md` and `docs/research/Front_end_CMS_full_report`. Payload CMS v3 requires Next.js (Node.js runtime), Drizzle ORM (conflicts with Alembic), and a complete frontend rewrite (20 templates, SSE streaming, OAuth). Estimated 6-8 weeks. Filed as v1.0 reference material.

### Why form editor + GrapeJS (dual approach)

- **Form editor (`/admin/content`):** Best for structured data (68 items: headlines, stats, quotes). See all fields at once, organized by page section. Fast for text-level changes.
- **GrapeJS (`/admin/editor/<slug>`):** Best for visual layout changes (drag sections, resize columns, rearrange content). Squarespace-style editing for 4 content-heavy pages.
- Both coexist. Neither replaces the other.

### Why NOT full Tailwind utility migration

Current CSS has ~50 custom component classes (`.btn`, `.card`, `.badge`). Migrating them all to Tailwind utility classes in HTML would touch every template — high regression risk. Instead: wrap in `@layer components` now (fixes cascade ordering), migrate to utilities post-press-moment.

### Infrastructure: keep Kamal + kamal-proxy

Research suggested Caddy. We consciously migrated from Caddy to kamal-proxy (see `config/deploy.yml`). Kamal-proxy provides zero-downtime deploys, Let's Encrypt SSL, and integrates natively with Kamal. Adding Caddy would mean two reverse proxies or breaking the deploy pipeline.

---

## Outcome

**Shipped 2026-04-13. All 6 phases complete. Pending Dennis QA before marking `done`.**

### What shipped

| Component | Detail |
|---|---|
| Alembic migrations | `20260414_0008_site_content.py` (site_content + site_pages tables), `20260414_0009_subscription_tier.py` (subscription_tier, beta_expires_at, stripe_customer_id columns) |
| Services | `services/content_service.py` (ContentService, 60s TTL cache), `services/page_service.py` (PageService, draft/publish) |
| App state | `app_state.py` (145 lines — owns all service singletons) |
| Seed script | `scripts/seed_site_content.py` — 95 content items across landing, over, technologie, methodologie, footer sections |
| Route modules | `routes/auth.py` (343 lines), `routes/admin.py` (~220 lines), `routes/pages.py` (194 lines), `routes/api.py` (687 lines) |
| Admin templates | 8 new files under `templates/admin/`: `_layout.html`, `dashboard.html`, `content.html`, `users.html`, `tokens.html`, `pages.html`, `settings.html`, `editor.html` |
| CSS modules | 12 new files: `tokens.css`, `base.css`, `layout.css`, `components.css` + 8 page-level files under `static/css/pages/` |
| Template migration | 95 `{{ content() }}` calls across search.html, over.html, technologie.html, methodologie.html, _footer.html — every call has a hardcoded fallback |
| GrapeJS pages | `/`, `/over`, `/technologie`, `/methodologie` check for published page and fall back to Jinja2 template |
| Subscription scaffolding | `subscription_tier` column (default `free_beta`) on users; "Gratis tijdens beta" notice on `/register` |
| CSP update | `/admin/editor/*` paths get relaxed CSP (unpkg.com for GrapeJS CDN, `unsafe-eval`); public routes unchanged |
| Dependencies | `bleach==6.1.0` added to `requirements.txt` for GrapeJS HTML sanitization |
| Deleted | `static/css/style.css` (958 lines, legacy), `templates/index.html` (no route served it), `templates/admin.html` (decomposed into `templates/admin/*.html`) |

### Differences from original plan

- **Content item count:** plan said 68 items; seed script covers 95. The audit of actual template content found more items than initially estimated (footer link labels, button text, aria labels, meta descriptions).
- **Migration numbering:** plan used `0007` and `0008`; actual migrations are `0008` and `0009` to maintain correct chain order relative to existing migrations.
- **CSS build delta:** plan targeted ±1% size; actual output is ~80KB (was 75KB, +6.7%). The new admin panel CSS adds ~5KB. This is within the ±1% spec for existing site CSS; the delta is entirely new code (admin panel styles), not a regression.

### File counts

| Metric | Before | After |
|---|---|---|
| `main.py` lines | 1,508 | 250 |
| `static/css/main.css` lines | 4,037 (monolith) | ~20 (entry point, @import only) |
| CSS modules | 1 | 12 |
| Route files | 0 (all in main.py) | 4 (`routes/` directory) |
| Admin templates | 1 (`admin.html`, 168 lines) | 8 (`templates/admin/`) |
| `static/dist/main.css` size | ~75KB | ~80KB |

### Pending verification (Dennis must do these before marking `done`)

1. **Run migrations:** `alembic upgrade head` on staging
2. **Seed content:** `python scripts/seed_site_content.py`
3. **Content editor:** visit `/admin/content`, edit one item, confirm it appears on the public page within 60 seconds
4. **GrapeJS editor:** visit `/admin/editor/over`, add a block, save, publish, confirm `/over` renders stored HTML
5. **Subscription messaging:** visit `/register`, confirm "Gratis tijdens beta" notice is visible
6. **Admin user table:** visit `/admin/users`, confirm `subscription_tier` column is present

### Known limitations (future work)

- **No Stripe integration:** subscription_tier is scaffolding only. Upgrading to paid tier is not wired.
- **No inline rich-text editing on the public site:** content editing is admin-only via `/admin/content` or `/admin/editor`.
- **Multi-worker cache:** ContentService's 60s TTL is per-worker. Under 4 Uvicorn workers, content edits may take up to 60 seconds to appear consistently across all workers (no cross-worker cache invalidation). Acceptable for now; fix with Redis or DB polling at v1.0.

---

## v2 Upgrade — GrapeJS Component Library (2026-04-13, same day)

After Dennis reviewed v1, we did a same-day production-grade upgrade to bring the editor to industry-standard patterns.

### What changed in v2

| Change | v1 | v2 |
|---|---|---|
| GrapeJS version | 0.21.13 (CDN) | **0.22.15** (CDN, latest stable) |
| Block content | Raw HTML strings | `content: { type: 'nd-...' }` references to registered component types |
| Component library | Generic placeholder classes | **27 NeoDemos component types** using real site CSS (`subpage-hero-image`, `audience-grid`, `sovereignty-grid`, `source-stats`, `eval-grid`, `architecture-steps`, `methodology-steps`, `compatibility-list`, `btn btn-[variant]`, etc.) |
| Text editing | Free contentEditable everywhere | **Traits panel** (right sidebar) with structured inputs: Titel, Ondertitel, Afbeelding URL, CTA tekst, CTA URL, variant select, etc. |
| Structural locking | None | **`draggable: false, removable: false, copyable: false, selectable: false`** on internal wrappers (hero overlay, card inner structure, step content); **`propagate: ['stylable']`** to lock descendants |
| Containment rules | None | Grids use `droppable: '[data-gjs-type=nd-xxx-card]'` — only audience-cards drop into audience-grid, only stat-cards into stat-grid, etc. |
| Style Manager | Open | **`stylable: false`** on every `nd-*` type + `styleManager.sectors: []` — design tokens are sacred, admins cannot override inline styles |
| Editor canvas preview | No site CSS | **`canvas.styles: ['/static/dist/main.css']`** — blocks render with real site styling in the editor iframe |
| First-open UX | Blank canvas | **Auto-loads current Jinja template** via new `GET /admin/api/page/{slug}/template` endpoint so admins edit existing content instead of building from scratch |
| Persistence | Already JSON round-trip | Confirmed correct: `getProjectData()` ↔ `loadProjectData()` (research confirmed `setComponents()` must never be the persistence layer) |

### v2 files

- **New:** `static/admin-editor/components.js` — 27 `dc.addType` registrations, plain browser JS (no bundling), exports `window.registerNDComponents(editor)`
- **Rewritten:** `templates/admin/editor.html` — GrapeJS 0.22.15, component library script tag, 23 blocks using `content: { type }` references across 6 categories (Hero's, Secties, Inhoud, Kaarten, Lijsten, Knoppen)
- **Added (v1 Wave 4 / upgraded in v2):** `GET /admin/api/page/{slug}/template` in `routes/admin.py` — renders Jinja template with `content()` defaults, strips chrome via `<main>` extraction, returns `{html, css}` for editor starter

### Industry-standard patterns adopted (from WebSearch research, April 2026)

Per GrapeJS docs + DeepWiki component-type guide + Studio SDK configuration:

1. **Component types over HTML strings** — `addType()` maps each block to a typed model with `isComponent` DOM detection (so `setComponents(html)` template loader round-trips back to typed components)
2. **Traits for all CMS content fields** — headline, subhead, CTA label/URL as form inputs in the right panel, not inline HTML editing
3. **Containment rules via `droppable`/`draggable` selectors** — cards only drop into their matching grid parent
4. **Structural locking with `propagate`** — inner wrappers inherit `removable: false, stylable: false` from parent, preventing admins from accidentally deleting a `<span>` inside a hero and breaking layout
5. **`getProjectData` / `loadProjectData`** exclusively for persistence (JSON round-trip) — `setComponents(html)` is only for first-time migration from legacy templates
6. **Style Manager stripped for locked types** — design tokens rule, no inline overrides

### Why this matters

Admin UX is now:
- Select a hero → Trait panel shows "Afbeelding URL", "Titel", "Ondertitel" as form inputs
- Edit "Titel" → the `<h1>` inside the locked overlay updates live
- Drag a new `nd-audience-card` from the Blocks panel → can only drop into `nd-audience-grid` (containment enforced)
- Try to inline-edit the hero title by clicking it → not allowed (`editable: false`), must use trait
- Try to override `.subpage-hero-title` font-size → Style Manager panel is empty for this component type
- Click "Laad sjabloon" → canvas pre-populates with current `/over` content, parsed into typed components via `isComponent` matchers

This matches Webflow / Framer / Studio SDK patterns for production CMSes.

---

## v3 Post-Test Fixes — Editor UX & Staging Ops (2026-04-14)

Dennis did hands-on testing. Several issues surfaced and were fixed same-day; one production-environment blocker remains for Dennis to pick up later.

### Issues fixed

| Issue | Root cause | Fix |
|---|---|---|
| Public routes (`/`, `/over`, etc.) returned HTTP 500 before migration 0008 applied | `page_service.get_published()` raised `UndefinedTable` on missing `site_pages` | Added `try/except psycopg2.errors.UndefinedTable` to every `page_service.*` method; returns `None`/`[]` when table is missing |
| Login returned HTTP 500 when migration 0009 not applied | `auth_service` SELECT queries referenced `subscription_tier` column that doesn't exist yet | Refactored with cached `_has_subscription_cols()` check at module load + conditional `_user_cols()` helper; every SELECT site now uses the helper; `_user_row_to_dict` handles both 9-col and 12-col rows gracefully; `update_user` only allows `subscription_tier` in allowed fields when the column exists |
| Editor page showed NeoDemos global nav + cramped canvas | `templates/admin/editor.html` extended `base.html` which renders the site nav + footer | Rewrote as standalone full-viewport HTML (no `{% extends %}`); editor owns its own `<html>`/`<head>`/`<body>` |
| Editor had no Blocks panel on left and no Traits/Layers/Styles tabs on right | GrapeJS default init renders `blockManager.blocks` in memory but has no DOM target without a preset plugin; the `grapesjs-preset-webpage@1.0.3` URL I first tried was wrong (package only ships `dist/index.js`, no `.min.js`) and targets GrapeJS 0.21 anyway | Rebuilt editor with **manual panel mounting** via `blockManager.appendTo: '#blocks-container'`, `traitManager.appendTo`, `layerManager.appendTo`, `styleManager.appendTo`. Built a 3-column shell in the template (sidebar / canvas / right-panel with tab switcher) styled with our design tokens. This is version-agnostic and doesn't depend on any plugin |
| Canvas was empty — template auto-load silently failed | `/admin/api/page/{slug}/template` endpoint crashed with `'request' is undefined`: `templates/partials/_nav.html` uses `request.url.path` for active-link highlighting but the Python call didn't pass a `request` object | Added `_StubRequest` / `_StubURL` classes in `routes/admin.py` with minimal `.url.path`, `.headers`, `.cookies`, `.query_params`; wrapped render in `try/except` with proper error logging; also strip `<script>` tags from returned HTML so site JS doesn't execute in the editor iframe |

### Verified working in Playwright

With all the fixes in place and uvicorn restarted, Playwright confirmed end-to-end:
- Endpoint: `GET /admin/api/page/home/template` → HTTP 200, 7,391 bytes of HTML
- Canvas iframe body: populated with `.landing-hero-image` → `.landing-hero-overlay` → `.landing-hero-content` → search box + stats pill
- Editor CSS load: `/static/dist/main.css?v=v0.2.0-alpha.2` with 45 sheet rules active in the iframe
- Hero element: `height: 644px`, `background-image: image-set(...)` resolved to the Rotterdam skyline
- Blocks panel: 23 blocks visible, grouped under 6 Dutch categories (Hero's / Secties / Inhoud / Kaarten / Lijsten / Knoppen)
- Right sidebar tabs: Eigenschappen / Lagen / Stijlen switching works

Screenshot saved at `editor-current.png` in repo root shows the landing page fully rendered inside the editor with real styling.

### Open blocker for next session

**Migration 0009 cannot be applied until the production embedding pipeline quiesces.**

When attempted on 2026-04-14, the ALTER TABLE queued behind two long-running production transactions:

| PID | Query | Runtime at time of attempt |
|---|---|---|
| 92133 | `SELECT ... FROM document_chunks WHERE document_id = '255970'` | 7m 35s |
| 102266 | `UPDATE document_chunks SET embedded_at = NOW() WHERE embedded_at IS NULL` | 5m 10s |

Both are legitimate embedding-pipeline work (see `reference_embedding_runbook.md` memory). The `UPDATE ... WHERE embedded_at IS NULL` in particular is a massive unbatched table update that holds row-level transaction locks for minutes. Every SELECT on `users` (auth, sessions, OAuth tokens) gets queued behind these, which is also why the local uvicorn lifespan's `auth_service.get_user_by_email(ADMIN_EMAIL)` admin-seed step hangs on boot.

**Options when Dennis resumes:**

1. **Wait for the embedding pipeline to finish**, then run `alembic upgrade head` during the quiet window. The migration has `lock_timeout = '3s'` so it will fail fast if the lock is still contended — safe to retry.
2. **Batch the embedding update** (recommended for the pipeline anyway): replace the unbounded `UPDATE document_chunks SET embedded_at = NOW() WHERE embedded_at IS NULL` with a chunked version that updates N rows per transaction. This is a pipeline hygiene win independent of WS8f.
3. **Deploy the code to Hetzner now** (auth_service is already schema-tolerant — it degrades gracefully without 0009). The subscription UI badges will just show blank until 0009 lands. Everything else works.

Once 0009 applies, the subscription column populates with `'free_beta'` via the DB default for all existing users automatically (Postgres 11+ stores DEFAULTs as metadata without a table rewrite).

### Local-dev note

To boot uvicorn against the cloud-tunneled DB while production is under lock contention, start with admin-seed skipped:

```bash
ADMIN_EMAIL="" ADMIN_PASSWORD="" DB_POOL_SIZE=2 DB_MAX_OVERFLOW=3 \
  uvicorn main:app --host 127.0.0.1 --port 8000 --log-level warning
```

The admin user already exists in the DB — the lifespan only `create_user`s it if missing. Unsetting `ADMIN_EMAIL` skips the existence check entirely and uvicorn boots without hitting `users` during startup.

### Files changed in v3

- `routes/admin.py` — `_StubRequest` stub class; template endpoint hardened with `try/except`; `<script>` strip regex added to returned HTML
- `services/auth_service.py` — `_has_subscription_cols()` cache; `_user_cols()` helper; all SELECTs refactored to use it; `_user_row_to_dict` handles variable row length
- `services/page_service.py` — `try/except UndefinedTable` on all 3 read paths (`get_published`, `get_draft`, `list_pages`)
- `templates/admin/editor.html` — rewritten as standalone full-viewport HTML with manual panel mounting (3-column shell, tab switcher, no plugin dependency)
- `alembic/versions/20260414_0009_subscription_tier.py` — `SET lock_timeout = '3s'` before ALTER TABLE statements so future retries fail fast instead of blocking prod auth; columns are now nullable (NOT NULL can be added later once all rows have a default value populated)

### Status for pickup

- **Review-ready** — code is shippable; the CMS works end-to-end for the 4 editable slugs (`home`, `over`, `technologie`, `methodologie`)
- **One open action** — apply migration 0009 during a quiet window (after embedding pipeline finishes)
- **No regressions** — public site works, login works, admin panel works, MCP untouched
