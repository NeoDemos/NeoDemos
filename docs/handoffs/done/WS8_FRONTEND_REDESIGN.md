# WS8 — Frontend Redesign: Design System, Landing Page & Calendar

> **Status:** `in progress — WS8a/b/c/d/e done, WS8f planned`
> **Owner:** `dennis + claude`
> **Priority:** 1 (critical path to public launch)
> **Parallelizable:** yes (with WS9; converges at search integration)
> **Last updated:** 2026-04-13

---

## TL;DR

Redesign the NeoDemos frontend from an MCP-first developer tool into a **search-first civic intelligence platform**. Establish a design token system, restructure CSS, rebuild the landing page around search-as-hero, replace the calendar grid with a filterable meeting list, and add marketing/trust content that communicates European sovereignty, founder authority, and AI transparency. The product should feel premium (serif+sans typography, pastel surfaces, civic blue+gold palette) and work beautifully on **both desktop and mobile**.

**Target audience (in priority order):** gemeenteraadsleden and their support staff → political journalists → civil servants → engaged citizens.

**Access model (launch):**
- **Unlimited for everyone.** No rate limiting at launch. Monitor cost, add limits only if needed.
- Free account unlocks: zoekgeschiedenis, partijlens, Word export, vergaderingvolger, MCP

**User journey funnel:** Search (anonymous, unlimited) → Account nudge after 3rd search (pull: save history, export) → MCP nudge after 5th search (power user path)

---

## Dependencies

| Dependency | Type | Notes |
|---|---|---|
| WS9_WEB_INTELLIGENCE | soft | Landing page search benefits from WS9's auto-detect mode; can use current search as placeholder |
| Current `base.html` template | read first | Understand the block structure before refactoring |
| Current `style.css` | read first | Inventory all component styles before token migration |

---

## Cold-start prompt

```
You are picking up WS8_FRONTEND_REDESIGN for the NeoDemos project — a civic intelligence
platform for the Rotterdam municipal council (90,000+ documents, 2002-present).

Read these files first:
- docs/handoffs/done/WS8_FRONTEND_REDESIGN.md (this file — full spec)
- templates/base.html (current template structure)
- templates/search.html (current landing page)
- templates/calendar.html (current calendar)
- static/css/style.css (current styles)
- docs/architecture/MASTER_PLAN.md §1-3 (vision context)

Your job: implement the frontend redesign in phases. Start with Phase 1 (design tokens +
CSS restructure), then Phase 2 (landing page), then Phase 3 (calendar), then Phase 4
(marketing content). Each phase should be deployable independently via Kamal.

Key constraints:
- No build step (no Tailwind, no Node.js, no webpack). Pure CSS + Jinja2 + vanilla JS.
- Respect Kamal deploy: static assets at /static/, cache-bust via ?v={{ version_label }}
- Self-host Inter font (download WOFF2 to /static/fonts/, remove Google Fonts CDN link)
- All text in Dutch unless otherwise specified
- WCAG AA contrast compliance on all text
```

---

## Files to read first

| File | Why |
|---|---|
| `templates/base.html` | Template inheritance structure, nav, footer |
| `templates/search.html` | Current landing page (865 lines, heavy inline styles) |
| `templates/calendar.html` | Current calendar grid (227 lines) |
| `templates/meeting.html` | Meeting detail page (shares components with search) |
| `static/css/style.css` | Current 290-line flat CSS |
| `static/js/app.js` | Current placeholder JS |
| `main.py` | Route definitions, template context variables |
| `services/storage.py` | Meeting/agenda data queries (need agenda_item_count) |

---

## Design Specification

### Typography

**Font pairing:** Instrument Serif (headings) + Inter (body)

- Instrument Serif: subtle editorial feel, evokes Dutch printing tradition. Use at `font-weight: 400` only (serifs carry visual weight through stroke contrast).
- Inter: already in use, stays for body text. Self-host WOFF2 files.
- JetBrains Mono: for meeting IDs, code snippets (optional).

```css
--font-heading: 'Instrument Serif', Georgia, 'Times New Roman', serif;
--font-body:    'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
```

Google Fonts embed (until self-hosted):
```html
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
```

### Color Palette

| Token | Name | Hex | Usage |
|---|---|---|---|
| `--color-primary` | Civic Blue | `#2B4C7E` | Primary buttons, links, key actions |
| `--color-primary-hover` | | `#234069` | Hover states |
| `--color-primary-light` | Soft Blue | `#DCE8F5` | Selected states, highlights |
| `--color-primary-lighter` | Mist | `#EEF3FA` | Page section tints |
| `--color-secondary` | Deep Navy | `#1B2A4A` | Headings, high-contrast text |
| `--color-accent` | Muted Gold | `#B8963E` | Logo accent, highlights, citations |
| `--color-accent-light` | Warm Sand | `#F5EDD6` | Accent background tint |
| `--color-surface` | | `#FAFBFD` | Page background |
| `--color-surface-raised` | | `#FFFFFF` | Cards, elevated panels |
| `--color-surface-sunken` | | `#F1F4F8` | Table stripes, input backgrounds |
| `--color-border` | | `#E2E7EF` | Default borders |
| `--color-border-strong` | | `#C8D1DE` | Active borders, focus rings |
| `--color-text` | | `#1A1F2E` | Primary body text |
| `--color-text-secondary` | | `#5A6478` | Descriptions (AA: 5.2:1 on white) |
| `--color-text-tertiary` | | `#8B95A8` | Metadata (AA at 18px+: 3.2:1) |
| `--color-success` | | `#2D7A4F` | Approved moties |
| `--color-success-light` | | `#E8F5EE` | Success backgrounds |
| `--color-warning` | | `#9A6C1E` | Pending states |
| `--color-warning-light` | | `#FDF4E3` | Warning backgrounds |
| `--color-error` | | `#C23A3A` | Rejected moties |
| `--color-error-light` | | `#FDE8E8` | Error backgrounds |

### Shadow Scale (Josh Comeau layered approach, navy-tinted)

```css
--shadow-xs: 0 1px 2px rgba(27,42,74,0.04), 0 1px 3px rgba(27,42,74,0.06);
--shadow-sm: 0 2px 4px rgba(27,42,74,0.04), 0 4px 8px rgba(27,42,74,0.06);
--shadow-md: 0 4px 8px rgba(27,42,74,0.04), 0 8px 24px rgba(27,42,74,0.08);
--shadow-lg: 0 8px 16px rgba(27,42,74,0.06), 0 16px 48px rgba(27,42,74,0.1);
```

### Spacing Scale (8px base)

```css
--space-1: 0.25rem; --space-2: 0.5rem; --space-3: 0.75rem; --space-4: 1rem;
--space-5: 1.25rem; --space-6: 1.5rem; --space-8: 2rem; --space-10: 2.5rem;
--space-12: 3rem; --space-16: 4rem; --space-20: 5rem;
```

### Border Radius

```css
--radius-sm: 4px; --radius-md: 8px; --radius-lg: 12px; --radius-xl: 16px; --radius-full: 9999px;
```

---

## Build Tasks

### Phase 0 — Brand Assets & Rate Limiting Backend (Day 0-1)

**Goal:** Create the brand assets that Phase 1 depends on, and the rate limiting backend that search depends on.

#### 0A. Brand Design (Dennis — in Figma)

Dennis designs these in Figma (or with AI assistance via v0.dev screenshots):
- [ ] **NeoDemos logo** (SVG): text-based, Instrument Serif, "Neo" in navy + "Demos" in gold
- [ ] **Favicon** (32x32, 16x16 PNG + SVG): simplified logo mark
- [ ] **Brand color confirmation**: approve or adjust the proposed palette (Civic Blue, Muted Gold, etc.)
- [ ] **Icon style**: confirm outlined icon set (Lucide, Phosphor, or Heroicons outline)

**Note:** The CSS token system and template work can start in parallel with brand design. Logo and favicon can be swapped in later.

#### 0B. Cost Monitoring (no rate limiting at launch)

**Decision (critical review):** No rate limiting at launch. The press moment matters more
than the API bill. At pre-launch volumes, unlimited Sonnet costs <$5/day.

**Implementation:**
1. Add per-query cost logging to `web_intelligence.py` (token counts, model, cost estimate)
2. Add daily cost summary to admin panel (total queries, total cost, avg cost/query)
3. Set up a cost alert (email notification) if daily cost exceeds $10
4. **Prepare** rate limiting code but don't activate (feature flag off):
   - Session-based counter for anonymous users
   - User-based counter for authenticated users
   - Admin toggle for "unlimited" flag per user
5. Rate limiting activates only when: daily cost exceeds $10 for 3+ days, OR abuse detected

**Acceptance criteria:**
- [ ] Per-query cost logged (tokens, model, estimated USD)
- [ ] Admin panel shows daily cost summary
- [ ] Cost alert configured at $10/day threshold
- [ ] Rate limiting code exists but is dormant (feature flag off)
- [ ] All users get unlimited AI searches at launch

---

---

## Implementation Status (2026-04-12)

### WS8a — CSS Architecture ✅ DONE
- Vite + Tailwind v4 build (`static/css/main.css` → `static/dist/main.css`)
- Design tokens in `@theme {}` block: dark green #042825 + beige #f4efe5 + orange #ff751f
- Instrument Serif + Inter variable fonts self-hosted at `static/fonts/`
- All component CSS in single `main.css` (no per-page files — simpler with Tailwind v4)

### WS8b — Landing Page ✅ DONE
- Full-viewport hero (skyline image + overlay gradient + search on hero)
- Rotating headlines (5 variants, hourly rotation via JS)
- Demo answer card with desktop/mobile split (`<details>` for mobile)
- **Demo answer cache**: `scripts/cache_demo_answers.py` → `data/demo_cache.json`
  - 5 queries pre-rendered: beloftes, klimaat, woningbouw, partijen, armoede
  - Loaded at startup in `main.py`, served as `demo_answer_markdown` + `demo_sources`
  - Rendered client-side via `marked.js` + citation bubbles (same as live search)
  - `DEMO_ANSWER_ID` env var pins a specific demo without redeploy
- Hero stats: pill treatment (glass blur, orange separators, uppercase)
- Example queries with `type="button"` (was causing page reload without it)
- Account nudge after 3rd search, MCP nudge after 5th

### WS8c — Calendar ✅ DONE (WS8c agent)
- Filterable list view as default
- Committee filter chips with `aria-pressed`
- `<details>`/`<summary>` row expansion
- Sticky date group headers
- Grid view preserved as toggle
- URL state sync (`history.replaceState`)

### WS8d — Subpages ✅ DONE
- `/over`: founder quote, democratic ambition, audience grid
- `/technologie`: EU sovereignty, security checklist, local AI options, MCP explanation
- `/methodologie`: 3-step methodology, sources stats, eval scores (0.99 / 4.8 / 2.75)
- All three have full-viewport hero images with blend fade (`::after` gradient)
- Consistent `has-hero` body class on all subpage templates

### WS8e — Polish ✅ DONE (2026-04-12)
- Logout → `/` (was redirecting to `/login`)
- "MCP" renamed to "AI-koppeling" everywhere (nav desktop + mobile + footer)
- Footer restructured: nav links → meta (AI + EU + brondata) → dimmed version number
- Hero image `::after` blend fade into page background (80px gradient)
- Stats pill eye-catching treatment on landing hero
- `DB_HOST` fixed: `localhost` → `127.0.0.1` (SSH tunnel IPv6 issue)

### WS8f — Admin Panel, Content Management & Architecture Hardening (planned 2026-04-13)

**Full spec:** [`WS8f_ADMIN_CMS.md`](WS8f_ADMIN_CMS.md)

**Scope:**
- `site_content` + `site_pages` PostgreSQL tables for CMS-managed content
- Structured form editor at `/admin/content` (68 editable items across 7 sections)
- GrapeJS visual page builder at `/admin/editor/<slug>` (over, technologie, methodologie, landing)
- CSS restructure: split 3,981-line monolith into Tailwind v4 `@layer`-based modules
- Router split: `main.py` (~1,500 lines) → 4 route modules + ~200-line app shell
- Subscription tier scaffolding (`free_beta` → `free` → `pro`)
- Delete dead `static/css/style.css` + `templates/index.html`

**Key architecture decisions:**
- Payload CMS / Next.js evaluated and rejected (6-8 week rewrite, blocks press moment)
- Dual editing: form fields for structured data + GrapeJS for visual layout
- GrapeJS loaded from CDN, stores HTML+CSS+JSON in `site_pages` table
- Templates fall back to hardcoded content if DB is empty

**Estimated effort:** ~5.5 days (6 phases, each independently deployable)

### Remaining (blocked on WS9)
- AI answers in live search (currently keyword-only)
- Demo answer quality depends on WS9 orchestrator quality

---

### Phase 1 — Design Token System & CSS Restructure (Day 1-2)

**Goal:** Establish the design foundation without changing visible UI.

1. **Create `static/css/tokens.css`**
   - Full `:root` block with all color, typography, spacing, shadow, radius, transition, and z-index tokens (see Design Specification above)
   - ~120 lines

2. **Create `static/css/reset.css`**
   - Minimal normalize (~20 lines): box-sizing, margin reset, smooth scroll

3. **Refactor `static/css/style.css` → `static/css/base.css` + `static/css/components.css`**
   - `base.css`: element-level styles (body, headings, paragraphs, links, tables, forms)
   - `components.css`: `.card`, `.btn`, `.btn-primary`, `.btn-secondary`, `.btn-accent`, `.btn-ghost`, `.badge`, `.badge-*`, `.nav-link-button`, `.meetings-table`, `.citation-bubble`, `.glass-panel`
   - Replace all hardcoded color/spacing values with token references

4. **Extract inline `<style>` blocks from templates**
   - `search.html` → `static/css/pages/search.css`
   - `meeting.html` → `static/css/pages/meeting.css`
   - `calendar.html` → `static/css/pages/calendar.css`
   - `mcp_installer.html` → `static/css/pages/mcp-installer.css`
   - Deduplicate shared styles (`.citation-bubble` exists in both search.html and meeting.html)

5. **Update `base.html` to load new CSS structure**
   ```html
   <link rel="stylesheet" href="/static/css/tokens.css?v={{ version_label }}">
   <link rel="stylesheet" href="/static/css/reset.css?v={{ version_label }}">
   <link rel="stylesheet" href="/static/css/base.css?v={{ version_label }}">
   <link rel="stylesheet" href="/static/css/components.css?v={{ version_label }}">
   {% block styles %}{% endblock %}
   ```
   Each child template uses `{% block styles %}` to load its page-specific CSS.

6. **Self-host Inter font**
   - Download Inter WOFF2 (400, 500, 600, 700) to `static/fonts/`
   - Add `@font-face` declarations to `tokens.css`
   - Remove Google Fonts `<link>` from `base.html`
   - Add Instrument Serif (400, 400 italic) — self-host or use Google Fonts initially

7. **Migrate all inline `style=""` attributes** from templates to named CSS classes
   - Priority: `meeting.html` (heavy inline styles), `calendar.html`, `search.html`

**Acceptance criteria:**
- [ ] No inline `<style>` blocks in any template
- [ ] No hardcoded color hex values outside `tokens.css`
- [ ] All templates load correctly with new CSS structure
- [ ] Instrument Serif renders on headings, Inter on body
- [ ] Visual regression: site looks intentionally different (new fonts/colors), not broken

### Phase 2 — Landing Page Redesign (Day 2-3)

**Goal:** The product sells itself. The landing page IS the product.

**Design principle (from critical review):** TheyWorkForYou is just a search box and a
postcode field. If the search works, no marketing copy is needed. If the search doesn't
work, no marketing copy will save it. Keep the landing page to 4 elements.

#### 2A. The Landing Page (4 elements, nothing more)

```
+--------------------------------------------------------------+
|  Neo Demos                                    [Kalender] [->] |
+--------------------------------------------------------------+
|                                                              |
|  De raadsvergadering was altijd openbaar.                    |
|  Nu is ze ook begrijpelijk.               <- Rotating        |
|                                              headline        |
|  [Actual AI answer with citations rendered here as a         |
|   compelling demo showing what NeoDemos can do.              |
|   Cached/pre-computed, no API cost on page load.]            |
|                                                              |
|  ----------------------------------------------------------- |
|                                                              |
|  +----------------------------------------------------+     |
|  |  Stel uw eigen vraag...                      [Zoek] |     |
|  +----------------------------------------------------+     |
|                                                              |
|  Of probeer:                                                 |
|  . Heeft het college haar beloftes waargemaakt?              |
|  . Hoe stemden partijen over woningbouw?                     |
|  . Welke moties over klimaat zijn aangenomen sinds 2020?     |
|                                                              |
|  90.000+ documenten . 24 jaar . Rotterdam . Open brondata             |
|                                                              |
|  Europees gehost . Elke bewering herleidbaar naar            |
|  het brondocument . Werkt met lokale AI                      |
|                                                              |
+--------------------------------------------------------------+
```

**Headline rotation (weekly, manual swap via config/env var):**

| # | Headline | Tone | When to use |
|---|---|---|---|
| 1 | De raadsvergadering was altijd openbaar. Nu is ze ook begrijpelijk. | Insight, frustration-release | **Launch week** |
| 2 | Wat besloot de gemeente? Vraag het gewoon. | Approachable, democratic | Week 2+ |
| 3 | Heeft uw partij haar beloftes waargemaakt? | Provocative, accountability | After first press |
| 4 | 90.000+ besluiten. Eén vraag. Direct antwoord. | Scale, depth | General rotation |
| 5 | Zoek. Analyseer. Bewijs. | Power-user, investigative | After user base established |
| 6 | De raad vergaderde. Wat besloten ze? Zoek het op. | Factual, direct | General rotation |

Implement as a `LANDING_HEADLINE` env var (string). Swap weekly without redeploy.

**Element 1: A live demo answer.**
Pre-render one impressive question+answer at the top of the page. Not a screenshot, not
a mockup — an actual NeoDemos answer with real citations from real documents. This answer
is cached/pre-computed (no API cost on page load).

**Selected demo question (launch):**
> "Heeft het college haar beloftes waargemaakt? Geef me een overzicht."

Why this question: it is the question of democratic accountability itself. Non-partisan
(no party is named, no policy domain chosen). Requires cross-document analysis across
time: coalitieakkoord → actual besluiten → outcomes. Shows NeoDemos's deepest capability.
Every journalist can write "AI checks whether Rotterdam kept its promises."

**Fallback if the answer quality is thin:** use "Waarom is er een tekort aan betaalbare woningen? Wat heeft de gemeente afgelopen jaren gedaan?" — woningbouw has strong document coverage and always lands with the audience.

**Do NOT use at launch:** asylum/asielopvang questions. The topic is explosive and the
first press moment should be about accountability, not immigration politics.

The demo answer immediately shows: citations, depth, cross-policy connections, party
mentions. It proves NeoDemos works before the visitor types a single character.

**Element 2: The search bar.**
Below the demo. "Stel uw eigen vraag..." with 3 clickable examples underneath.
No toggle. No mode selector. WS9 auto-detects.

**Element 3: One line of credibility.**
`90.000+ documenten . 24 jaar . Rotterdam . Open brondata`

This packs three trust signals into one line: scale, temporal depth, and geographic specificity.
No founder attribution on the landing page. Let the product speak. Just facts.

**Element 4: One line of trust.**
`Europees gehost . Open brondata . Elke bewering herleidbaar naar het brondocument . Werkt met lokale AI`

That's it. The EU sovereignty details, local LLM options, security checkmarks, democratic
ambition statement, audience value props -- all live on dedicated subpages linked from
the footer. The landing page is the product, not a brochure.

#### 2B. Search Results (unlimited at launch)

**No rate limiting at launch.** AI analysis is unlimited for everyone. Monitor cost daily.
Add limits only when abuse or cost exceeds $10/day. At pre-launch volumes (~20-50
visitors/day), unlimited Sonnet queries cost <$5/day. The press moment is worth more
than the API bill.

**Auto-detect pattern:**
- All queries trigger keyword search immediately (fast, free)
- WS9 auto-detects whether AI analysis is needed (questions -> yes, keywords -> no)
- Keyword results appear within 500ms
- AI answer streams in above keyword results via SSE

**Result display:**
- AI answer card: Instrument Serif heading, gold left border, citation bubbles
- Keyword results: clean card grid below (date, committee badge, title, snippet)
- Sources panel: collapsible, all consulted documents with original links

#### 2C. Account Creation Incentives (pull, not push)

**Everything works without login.** Search, AI analysis, citations -- all free, unlimited.
Account creation is incentivized by giving users MORE, not by taking things away.

**What a free account unlocks:**

| Feature | Anonymous | Free account |
|---|---|---|
| Search + AI analysis | Unlimited | Unlimited |
| Zoekgeschiedenis | Lost on close | See all past queries + answers |
| Partijlens | Not available | Configure party for tailored context |
| Exporteren | Not available | Copy analyses to Word |
| Vergaderingvolger | Not available | Bookmark meetings and dossiers |
| MCP toegang | Not available | Connect your own AI assistant |

**Account nudge (shown after 3rd search in a session, subtle banner):**
```
+--------------------------------------------------------------+
|  Wist u dat u uw zoekopdrachten kunt bewaren?                |
|  Maak een gratis account en bewaar uw zoekgeschiedenis,      |
|  stel uw partijlens in, en exporteer analyses naar Word.     |
|                                                              |
|  [Gratis account aanmaken]                    [Niet nu X]   |
+--------------------------------------------------------------+
```

This only appears after the user has demonstrated interest (3+ searches). It's dismissable.
It highlights features the user would naturally want at that point in their journey.

#### 2D. Subpages (content moved off the landing page)

All the content removed from the landing page lives on dedicated subpages accessible
from the footer or nav:

| Page | Content |
|---|---|
| `/over` | Founder story (insight credential, not celebrity claim: "Ik zat zelf in de raadszaal en wist niet wat er drie jaar eerder besloten was. Dat kon beter."), democratic ambition, early user quotes. **Founder quote (approved, use on `/over`):** _"Als raadslid had ik toegang tot alles — en toch klikte ik me een weg door honderden losse PDF's om één antwoord te vinden. Dat kon beter."_ — Dennis Tak, oud-raadslid Rotterdam |
| `/technologie` | EU sovereignty details, local LLM options (Ollama, LM Studio, Open WebUI), model independence, security checklist, "Uw data, uw AI" diagram |
| `/methodologie` | How NeoDemos works (3-step), eval scores, data sources, limitations |
| `/mcp-installer` | MCP setup guide (existing page, enhanced) |
| `/privacy` | Privacy policy, GDPR, data handling |

**Footer:**
```
NeoDemos v0.2.0 . Brondata: Open Raadsinformatie
Over . Technologie . Methodologie . Privacy . MCP
Analyse door AI (Anthropic) . Europees gehost
```

#### 2E. MCP Progressive Nudge

MCP nudge appears in TWO places, both after demonstrated engagement:

1. **In search results** (after 5+ searches or when logged in): small inline card
   ```
   Tip: gebruik NeoDemos rechtstreeks in Claude, ChatGPT of Cursor
   voor diepere analyses. Werkt ook met Ollama. [Meer info ->]
   ```

2. **In the account settings page**: full MCP section with setup guide

NOT on the landing page. NOT before the user has seen value.

#### 2F. Launch Press Kit (non-engineering, Dennis action)

Three pieces needed before launch announcement:

1. **Demo answer rendered and cached** — the "Heeft het college haar beloftes waargemaakt?" answer with real citations. This is the single most important deliverable. If the answer is weak, delay launch.

2. **One raadslid quote** — Erik Verweij (and others this week) should use NeoDemos for real dossier prep. Ask for a single on-record sentence after their first session. Goes on `/over` page. **Placeholder in use until real quote arrives:** _"Dit spaarde me twee uur voorbereiding." — Raadslid, Rotterdam_

3. **Press pitch (two sentences)** — for AD Rotterdam and NRC:
   > "NeoDemos maakt 90.000 Rotterdamse raadsdocumenten doorzoekbaar met AI. Elke vraag over wat de gemeente deed, besloot of beloofde krijgt direct antwoord met bronvermelding."
   No product features. No tech stack. Just the consequence.

**Acceptance criteria:**
- [ ] "Debatvoorbereiding" toggle removed
- [ ] Search works for ALL users without rate limiting (unlimited at launch)
- [ ] Pre-rendered demo answer visible at top of landing page
- [ ] Example queries are clickable and trigger search
- [ ] Landing page has exactly 4 elements (demo, search, credibility, trust)
- [ ] Account nudge appears after 3rd search, is dismissable
- [ ] MCP nudge appears after 5th search or when logged in
- [ ] Subpages exist: `/over`, `/technologie`, `/methodologie`
- [ ] **Mobile: search bar above fold on 375px width**
- [ ] **Mobile: hamburger menu or horizontal-scroll nav**
- [ ] **Mobile: demo answer collapsed behind "Bekijk voorbeeld" tap target**

### Phase 3 — Calendar Redesign (Day 3-4)

**Goal:** Replace calendar grid with filterable meeting list as default view.

#### 3A. Backend: Add filtered meetings query

In `services/storage.py`, add or modify the meetings query to support:
```sql
SELECT m.*, COUNT(ai.id) as agenda_item_count
FROM meetings m
LEFT JOIN agenda_items ai ON ai.meeting_id = m.id
WHERE ($1::text IS NULL OR m.committee ILIKE '%' || $1 || '%')
  AND ($2::text IS NULL OR m.name ILIKE '%' || $2 || '%')
GROUP BY m.id
ORDER BY m.start_date DESC
```

#### 3B. Frontend: Filterable list view

**Top bar:**
```
[🔍 Search meetings...                    ]

[Alle] [Raadsvergadering] [Commissie BWB] [Commissie ZOCS] [Commissie ABVM] ...
[Komend] [Deze maand] [Dit jaar]
```

**Meeting rows (collapsed):**
```
┌──────────────────────────────────────────────────────────────┐
│  12 apr   Raadsvergadering                     [Plenair]    │
│  2026     Gemeenteraad Rotterdam               ▸ 14 items   │
├──────────────────────────────────────────────────────────────┤
│  10 apr   Commissie BWB                        [Commissie]  │
│  2026     Bouwen, Wonen en Buitenruimte        ▸ 8 items    │
└──────────────────────────────────────────────────────────────┘
```

**Meeting rows (expanded via `<details>`/`<summary>`):**
Shows agenda item list with doc counts, link to full meeting page.

**Date group headers (sticky):**
```
── Vandaag ──────────────────
── Deze week ────────────────
── April 2026 ───────────────
```

**Technical implementation:**
- Filter chips: `aria-pressed` toggle, flexbox row, horizontal scroll on mobile
- Search: client-side filter on loaded JSON, 250ms debounce
- URL state: `history.replaceState` for shareable filtered views (`/calendar?committee=BWB&search=wonen`)
- Performance: `content-visibility: auto` on rows (CSS-only, zero JS)
- Progressive disclosure: `<details>` element with CSS animation via `::details-content`
- Keep optional mini calendar toggle (small icon, not default)

**Acceptance criteria:**
- [ ] Default view is filterable list (not calendar grid)
- [ ] Committee filter chips work (client-side)
- [ ] Search within meetings works with 250ms debounce
- [ ] URL reflects current filter state (shareable)
- [ ] Date groups with sticky headers
- [ ] `<details>` expansion shows agenda items + doc count
- [ ] Mobile: horizontal-scroll filter chips, stacked meeting rows
- [ ] Grid view accessible via toggle icon (preserves old behavior)

### Phase 4 — Template Architecture & Polish (Day 4-5)

**Goal:** Ensure template consistency, responsive behavior, and self-service editability.

1. **Enhance `base.html` template blocks**
   ```html
   {% block meta %}{% endblock %}        <!-- page-specific meta tags -->
   {% block styles %}{% endblock %}      <!-- page-specific CSS -->
   {% block hero %}{% endblock %}        <!-- optional hero section -->
   {% block content %}{% endblock %}     <!-- main content -->
   {% block extra_scripts %}{% endblock %}
   ```

2. **Create shared partials** (Jinja2 includes)
   - `_search_bar.html` — reusable search input with @mention
   - `_citation_bubble.html` — citation rendering snippet
   - `_trust_strip.html` — EU/security badges (used on landing + footer)
   - `_meeting_card.html` — meeting list row (used in calendar + overview)

3. **Update navigation**
   - Replace text-only nav with consistent component
   - Active page indicator (bottom border or background)
   - Mobile: hamburger menu or horizontal scroll

4. **Footer update**
   ```
   NeoDemos v0.2.0 | Brondata: Open Raadsinformatie
   Analyse door AI (Claude, Anthropic) | Privacy | Methodologie | Contact
   ```

5. **Responsive design (mobile is a hard requirement)**
   - Desktop: >1024px (current max-width: 1200px)
   - Tablet: 768-1024px
   - Mobile: <768px (raadsleden check on phones between meetings)
   - **Mobile critical requirements:**
     - Search bar + "Zoek" button above fold on 375px
     - Trust badges as compact icons below search
     - Navigation: hamburger menu (not horizontal overflow)
     - Calendar filter chips: horizontal scroll, not wrap
     - Meeting list rows: stacked layout (date left, info right)
     - AI answer: full-width card, sources collapsible
     - Rate limit CTA: sticky bottom bar on mobile
   - Test on: iPhone SE (375px), iPhone 15 (393px), iPad (768px)

**Acceptance criteria:**
- [ ] All pages use `{% block styles %}` instead of inline `<style>`
- [ ] Shared partials exist for search bar, citation, trust strip, meeting card
- [ ] Navigation has active page indicator
- [ ] Footer shows AI transparency disclosure
- [ ] Mobile responsive on all pages (search, calendar, meeting detail)
- [ ] Template editing guide exists as code comment in `base.html`

---

## Eval Gate

| Metric | Target | How to measure |
|---|---|---|
| Lighthouse Performance | >90 | `lighthouse https://neodemos.nl --only-categories=performance` |
| Lighthouse Accessibility | >95 | WCAG AA on all text, proper ARIA |
| First Contentful Paint | <1.5s | Lighthouse metric (Hetzner EU) |
| CSS file count | ≤8 | `ls static/css/` |
| Inline style attributes | 0 in base.html | `grep 'style=' templates/base.html` |
| Inline `<style>` blocks | 0 in any template | `grep '<style>' templates/*.html` |
| Mobile search usable | yes | Search bar + CTA above fold on 375px |

---

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Instrument Serif doesn't render well at small sizes | Low | Only use for h1-h3 (≥20px); fallback to Georgia |
| CSS restructure breaks inline-JS style references | Medium | Search for `style.` and `classList.` in all `<script>` blocks before refactoring |
| Calendar list view needs server-side agenda counts | Medium | Add LEFT JOIN COUNT to storage query; test on production data volume |
| Self-hosting fonts increases Docker image size | Low | WOFF2 files are ~50KB total; negligible |
| Landing page copy needs Dutch native review | High | Flag for Dennis review before deploy |

---

## Design Tool Workflow

**For the designer (Dennis):**
1. Use **Figma** (free plan) to refine layouts, try color variations, test typography
2. Screenshot Figma frames → paste into Claude Code → get HTML/CSS implementation
3. Use **v0.dev** for quick visual inspiration (describe a component, screenshot the result)
4. All CSS changes flow through the token system — change `tokens.css` to update all pages
5. Test locally: `python main.py` → open browser → iterate

**CSS is structured so that editing `tokens.css` alone updates the entire site's look and feel.**

---

## Skills & Tooling (installed)

The following skills are installed in `.agents/skills/` (symlinked to Claude Code):

| Skill | Invoke with | Purpose |
|---|---|---|
| `copywriting` | `/copywriting` | General copywriting principles for Dutch civic audience |
| `ogilvy-copywriting` | `/ogilvy-copywriting` | Ogilvy-style persuasion: headlines, body copy, calls to action |
| `stop-slop` | `/stop-slop` | Anti-filler pass — removes marketing clichés and AI-sounding prose |
| `frontend-design` | `/frontend-design` | Visual design guidance: layout, spacing, typography, color |

**Recommended workflow for landing page copy:**
1. Draft Dutch copy for each element
2. Run `/stop-slop` pass to remove filler
3. Run `/ogilvy-copywriting` pass to sharpen the headline and CTA
4. Run `/frontend-design` pass when implementing the HTML/CSS

**Playwright MCP** is installed (`claude mcp add playwright`) for screenshot-based visual testing.
Use it to verify mobile layouts on 375px and 768px viewports before marking phases complete.

---

## Outcome

**Shipped 2026-04-12.** The frontend pivoted from an MCP-first developer surface to a search-first civic intelligence platform. WS8a–e shipped in sequence: Vite + Tailwind v4 foundation with self-hosted fonts and a dark-green/beige/orange token palette; a stripped landing page built around a rotating headline, cached demo answer, and search hero; a filterable meeting list calendar with URL state and grid fallback; three marketing subpages (`/over`, `/technologie`, `/methodologie`) with full-bleed photography; and a polish pass covering footer restructure, nav renaming, and a hero-into-page blend fade. WS8f (admin CMS + GrapeJS) is tracked separately and is not part of this Outcome.

### What shipped (per sub-workstream)

- **WS8a — CSS architecture:** Vite + Tailwind CSS v4 build (`static/css/main.css` → `static/dist/main.css`), design tokens in `@theme {}` (dark green `#042825` + beige `#f4efe5` + orange `#ff751f`), Inter + Instrument Serif WOFF2 self-hosted to `static/fonts/`. All component CSS consolidated into a single `main.css` rather than per-page files (simpler on Tailwind v4).
- **WS8b — Landing page:** `search.html` simplified to hero + demo + search + credibility/trust. Full-viewport hero with skyline image + overlay gradient, rotating headlines (5 variants, hourly JS rotation), demo answer with desktop/mobile `<details>` split, pre-rendered cache via `scripts/cache_demo_answers.py` → `data/demo_cache.json` (5 queries, loaded at startup, `DEMO_ANSWER_ID` env pin), account nudge after 3rd search, MCP nudge after 5th.
- **WS8c — Calendar:** Default view is filterable list with committee filter chips (`aria-pressed`), `<details>`/`<summary>` row expansion, sticky date group headers, URL state sync via `history.replaceState`; grid view preserved as a toggle.
- **WS8d — Subpages:** `/over` (founder quote, democratic ambition, audience grid), `/technologie` (EU sovereignty, security checklist, local AI options, MCP explanation), `/methodologie` (3-step methodology, sources stats, eval scores 0.99 / 4.8 / 2.75). All three use full-viewport hero images with a `::after` blend fade and a consistent `has-hero` body class.
- **WS8e — Polish:** Logout redirect changed to `/` (was `/login`); "MCP" renamed to "AI-koppeling" across nav and footer; footer restructured (nav → meta → dimmed version); hero image 80px blend fade into page background; stats pill treatment on the landing hero (glass blur, orange separators, uppercase); `DB_HOST` fixed from `localhost` to `127.0.0.1` (SSH tunnel IPv6 issue).

### Eval gate results

| Metric | Target | Actual |
|---|---|---|
| Lighthouse Performance | ≥ 90 | Pass (per README Track C table) |
| Lighthouse Accessibility | ≥ 95 (WCAG AA) | Pass (per README Track C table) |
| Mobile search above fold @ 375px | Pass | Pass (per README Track C table) |
| Landing headline rotation wired | Pass | Pass (`LANDING_HEADLINE` env var) |

Lighthouse numeric scores not recorded in the handoff files — only pass/fail against targets in `docs/handoffs/README.md` Track C table.

### Diffs from original plan

- Color palette shifted from the original "Civic Blue + Muted Gold" spec to dark green + beige + orange (`#ff751f`) after Dennis's Canva design direction landed in WS8a.
- CSS architecture consolidated into a single `main.css` under Tailwind v4 `@theme` instead of the originally-planned split across `tokens.css` / `reset.css` / `base.css` / `components.css` + per-page files.
- Demo answer caching implemented as a pre-computed JSON cache (`data/demo_cache.json`, 5 queries, `DEMO_ANSWER_ID` env pin) rather than a single hardcoded answer.

### Known gaps / follow-ups

- WS8f (Admin CMS + GrapeJS editor) tracked in its own handoff file — not part of this Outcome.
- Demo answer quality depends on WS9 orchestrator quality; WS9 shipped 2026-04-13 and demo/live answers now flow through Sonnet + tool_use.
- Phase 4 manual eval (20 MCP-replay queries side-by-side) pending Dennis — tracked under WS9.
- Demo cache prod verification (`GET /` < 200ms) still listed as "verify in prod" in the README Track C table.
