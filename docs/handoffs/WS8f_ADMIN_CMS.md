# WS8f — Admin Panel, Content Management & Architecture Hardening

> **Status:** `not started`
> **Owner:** `dennis + claude`
> **Priority:** 2 (launch enhancer — improves admin self-service, does not block press moment)
> **Parallelizable:** yes (independent of WS1-WS7, WS9; builds on WS8a-e)
> **Last updated:** 2026-04-13

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
- [ ] Both migrations run cleanly (`alembic upgrade head`)
- [ ] ContentService.get() returns values from DB or falls back to default
- [ ] Seed script populates ~68 content items with current hardcoded values
- [ ] App runs identically — no visible change

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
- [ ] `npm run build` succeeds
- [ ] `static/dist/main.css` output size is within ±1% of pre-split
- [ ] No visual regression on any page
- [ ] No `static/css/style.css` or `templates/index.html` in repo
- [ ] All custom classes wrapped in `@layer components` or `@layer base`

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
- [ ] All routes respond identically to pre-split
- [ ] `main.py` is ≤250 lines
- [ ] Each router file is self-contained with clear imports
- [ ] No circular import errors

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
- [ ] `/admin/content` shows all 68 items grouped by section
- [ ] Editing a field and saving updates the DB
- [ ] "Herstel" button resets to default_value
- [ ] Changes appear on public pages within 60 seconds

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
- [ ] `/admin/editor/over` loads GrapeJS with our design system styles
- [ ] Pre-built blocks are available in the block panel
- [ ] Saving stores grapes_json + html + css in site_pages
- [ ] Publishing makes the edited content visible on the public page
- [ ] Unpublished pages fall back to the Jinja2 template content

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
- [ ] All 62+ hardcoded content items replaced with `{{ content() }}` calls
- [ ] Every call includes a hardcoded fallback (site works with empty DB)
- [ ] GrapeJS-published pages render their stored HTML
- [ ] Unpublished pages render template content

### Phase 5 — Subscription Flow (0.5 day)

**Goal:** Users get `free_beta` tier, admin sees it, beta messaging visible.

1. Update `services/auth_service.py` — add subscription columns to all queries
2. Update `templates/register.html` — "Gratis tijdens beta" notice
3. Update user settings — show subscription tier
4. Update admin user table — show subscription_tier column

**Acceptance criteria:**
- [ ] New users get `subscription_tier = 'free_beta'` automatically
- [ ] Registration page shows beta messaging
- [ ] Admin user list shows subscription column
- [ ] User settings page shows current tier

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
