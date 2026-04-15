# Proposal — Play Tailwind + GrapesJS + Open Source Tooling

> **Status:** Research artifact. Submitted by Dennis on 2026-04-14 during WS8f rejection follow-up.
>
> **Evaluation outcome:** This proposal was reviewed phase-by-phase against the current code state. ~6 small items were adopted as part of WS8f Phase 7 (axe-core, "start from template" UI, three new GrapesJS block types, traits on existing components). The bulk of the proposal — cloning Play Tailwind, swapping palette/fonts, building 11 generic blocks, building a parallel templates API — was not adopted because it conflicts with WS8a–e (palette `#042825`/`#f4efe5`/`#ff751f`, Inter + Instrument Serif self-hosted, Tailwind v4 with `@theme`) and duplicates already-shipped code (`site_pages` table, `/admin/api/page/{slug}` endpoints, ~25 ND-specific GrapesJS components).
>
> **Decisions live in:** [docs/handoffs/WS8f_ADMIN_CMS.md](../handoffs/WS8f_ADMIN_CMS.md) "Phase 7 — Rejection follow-up" section.
>
> The full proposal text follows verbatim, preserved for research history.

---

# NeoDemos Frontend Upgrade — Complete Agent Instructions
## Play Tailwind + GrapesJS + Open Source Tooling

> **For:** Claude Code agent in Antigravity
> **Project:** neodemos.nl — Dutch civic intelligence platform
> **Server:** Hetzner CCX33 (8 vCPU, 32GB RAM, Ubuntu)
> **Existing stack:** FastAPI backend + GrapesJS page editor + custom admin panel
> **Goal:** Professional, warm civic aesthetic — NOT cold tech-SaaS

---

## THE NEODEMOS VISUAL IDENTITY (MUST MATCH)

The current site has a warm, institutional, elegant aesthetic. PRESERVE THIS.

**Typography:**
- Logo/Headings: Serif font (the "NeoDemos" logo uses italic serif)
- Body text: Clean warm sans-serif
- NOT Inter, NOT bold geometric sans. Think newspaper/institutional.

**Colors (warm civic palette):**
```
Primary teal:      #0D9488 (current accent color)
Primary hover:     #0F766E
CTA orange:        #D97706 (the "Zoek" button)
CTA hover:         #B45309
Background:        #FAFAF9 (warm stone-50, NOT pure white)
Surface/cards:     #FFFFFF with border #E7E5E4 (stone-200)
Text primary:      #292524 (warm stone-800)
Text secondary:    #78716C (warm stone-500)
Dark sections:     #1C1917 (warm stone-900)
Dark text on dark: #E7E5E4 (stone-200)
Month headers:     #0D9488 with orange underline (see calendar page)
```

**Shadows:** Soft and warm — `shadow-md shadow-stone-200/50`, NOT harsh blue-gray

**Borders:** `rounded-xl border border-stone-200` (like the admin dashboard cards)

**Overall feel:** Civic authority + warmth. Think: Dutch government meets premium magazine.

---

## PHASE 1: DOWNLOAD ALL RESOURCES

### 1.1 Play Tailwind (template foundation)
```bash
cd /tmp
git clone https://github.com/TailGrids/play-tailwind.git
```

**Files you get:**
| File | Purpose | NeoDemos mapping |
|------|---------|-----------------|
| `index.html` | Landing page (hero, features, pricing, testimonials, blog, team, contact, footer) | → Homepagina |
| `about.html` | About page (team, mission, stats) | → Over NeoDemos |
| `pricing.html` | Pricing tiers with feature comparison | → Prijzen (Gratis/Pro €19/Raadslid €49) |
| `blog-grids.html` | Blog overview with card grid | → Onderzoeken overzicht |
| `blog-details.html` | Single blog post with rich content | → Onderzoek artikel |
| `contact.html` | Contact form | → Contact / Feedback |
| `signin.html` | Login page | → Inloggen |
| `signup.html` | Registration page | → Registreren |
| `404.html` | Error page | → 404 pagina |

### 1.2 HyperUI (supplementary components — pick what's missing)
```bash
# No install needed — copy-paste from https://hyperui.dev
# Components to grab:
# - Data tables (for motie/document listings)
# - Badges (for motion status: aangenomen/verworpen/ingetrokken)
# - Stats cards (for admin dashboard numbers)
# - Alerts (for system messages)
# - Accordion/collapsible (for calendar meeting items)
# - Breadcrumbs (for navigation)
# - Tabs (for search result categories)
# - Empty states (for pages with no data yet)
```

### 1.3 GrapesJS plugins (all free/open-source)
```bash
# In your project directory:
npm install grapesjs-preset-webpage
npm install grapesjs-custom-code
npm install grapesjs-plugin-export
npm install grapesjs-touch
npm install grapesjs-tui-image-editor
```

**What each plugin does:**
| Plugin | Purpose | Free? |
|--------|---------|-------|
| `grapesjs-preset-webpage` | Standard webpage builder panels, blocks, and buttons | ✅ MIT |
| `grapesjs-custom-code` | Embed custom HTML/CSS/JS widgets (search, calendar, analyse) | ✅ MIT |
| `grapesjs-plugin-export` | Export pages as ZIP (HTML + CSS + assets) | ✅ MIT |
| `grapesjs-touch` | Touch support for mobile editing | ✅ MIT |
| `grapesjs-tui-image-editor` | Image editing (crop, resize, filters) in asset manager | ✅ MIT |

### 1.4 Google Fonts
```html
<!-- Add to base template <head> -->
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Crimson+Pro:ital,wght@0,400;0,500;0,600;0,700;1,400;1,600&family=DM+Sans:wght@400;500;700&display=swap" rel="stylesheet">
```

**Why Crimson Pro instead of Playfair Display:**
- Crimson Pro is a warm, readable serif — works at ALL sizes (headings AND body)
- Playfair Display is high-contrast, looks great in headlines but struggles in small text
- Crimson Pro better matches the warm institutional NeoDemos identity
- DM Sans as body font: clean, modern, warm (not cold like Inter)

### 1.5 Additional open-source tools
```bash
# Tailwind CSS (if not already installed)
npm install -D tailwindcss @tailwindcss/typography @tailwindcss/forms

# FullCalendar (for enhanced calendar view)
npm install @fullcalendar/core @fullcalendar/daygrid @fullcalendar/list

# Alpine.js (lightweight JS for interactive components — dropdowns, modals, tabs)
# No install — use CDN:
# <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>

# Chart.js (for admin dashboard analytics)
npm install chart.js
```

---

## PHASE 2: RESKIN PLAY TAILWIND TO NEODEMOS IDENTITY

### 2.1 Font replacement (across ALL HTML files)

**Find and add** to the `<head>` of every HTML file:
```html
<link href="https://fonts.googleapis.com/css2?family=Crimson+Pro:ital,wght@0,400;0,500;0,600;0,700;1,400;1,600&family=DM+Sans:wght@400;500;700&display=swap" rel="stylesheet">
```

**In tailwind.config.js:**
```javascript
module.exports = {
  theme: {
    fontFamily: {
      'sans': ['"DM Sans"', 'system-ui', 'sans-serif'],
      'serif': ['"Crimson Pro"', 'Georgia', 'serif'],
    },
    extend: {
      colors: {
        'neodemos': {
          50: '#f0fdfa',
          100: '#ccfbf1',
          200: '#99f6e4',
          300: '#5eead4',
          400: '#2dd4bf',
          500: '#14b8a6',
          600: '#0d9488',  // PRIMARY
          700: '#0f766e',
          800: '#115e59',
          900: '#134e4a',
        },
        'warm': {
          50: '#FAFAF9',   // page background
          100: '#F5F5F4',
          200: '#E7E5E4',  // borders
          300: '#D6D3D1',
          500: '#78716C',  // secondary text
          800: '#292524',  // primary text
          900: '#1C1917',  // dark sections
        },
      }
    }
  }
}
```

### 2.2 Color replacement (find-and-replace across ALL files)

Execute these replacements in order across all HTML files:

```
# Background colors
bg-white → bg-warm-50
bg-[#090E34] → bg-warm-900
bg-dark → bg-warm-900

# Text colors
text-dark → text-warm-800
text-body-color → text-warm-500
text-white → text-warm-50  (only in dark sections — review each)

# Primary/accent colors
bg-primary → bg-neodemos-600
hover:bg-primary → hover:bg-neodemos-700
text-primary → text-neodemos-600
border-primary → border-neodemos-600

# Border and surface colors
border-stroke → border-warm-200
border-[#E9ECF8] → border-warm-200

# Shadow warmth
shadow-lg → shadow-md shadow-warm-200/50
shadow-xl → shadow-lg shadow-warm-200/50

# Font for headings — add font-serif class to all h1, h2, h3, h4
# (Keep body text as default sans = DM Sans)
```

### 2.3 Heading font application

Find all heading tags and add `font-serif`:
```
<h1 class="..."> → <h1 class="font-serif ...">
<h2 class="..."> → <h2 class="font-serif ...">
<h3 class="..."> → <h3 class="font-serif ...">
<h4 class="..."> → <h4 class="font-serif ...">
```

### 2.4 Content replacement

```
# All instances of "Play" (the template name) → "NeoDemos"
# Main tagline → "De raad besloot. Wist u ervan?"
# Sub-tagline → "Transparantie in de Rotterdamse gemeentepolitiek"
# Hero description → "Stel een vraag over Rotterdams beleid en krijg direct inzicht in standpunten, stemmingen en debatten."
# CTA button text → "Zoek" (with bg-amber-600 hover:bg-amber-700 rounded-full)
```

---

## PHASE 3: CREATE NEODEMOS-SPECIFIC PAGES

These pages don't exist in Play Tailwind. Build them using Play Tailwind's layout structure (same navbar, same footer, same spacing) with components from HyperUI.

### 3.1 Zoeken / Search page (`search.html`)
```
Layout: Full-width hero with Rotterdam skyline background
Content:
  - Large search input with placeholder "Wat wilt u weten over de Rotterdamse raad?"
  - Orange "Zoek" CTA button
  - Below: search results as cards with:
    - Document title (font-serif)
    - Date + document type badge
    - Relevance snippet (2-3 lines)
    - Source link
  - Sidebar or tabs: filter by partij, periode, document type
```

### 3.2 Analyse page (`analyse.html`)
```
Layout: Two-column on desktop, stacked on mobile
Left column (wide):
  - Question header (font-serif, large)
  - AI-generated explanation (2 paragraphs, warm-50 background card)
  - Source citations with document links
Right column (narrow):
  - Party voting card (color-coded per party: voor=green, tegen=red, onthouding=gray)
  - Related motions list
  - "Dieper graven" expandable section with full debate quotes
```

### 3.3 Kalender page (`calendar.html`)
```
Layout: Keep the EXISTING calendar visual (expandable rows grouped by month)
Styling: Apply NeoDemos color scheme:
  - Month headers: font-serif, text-neodemos-600, with amber underline
  - Meeting rows: warm-50 background, warm-200 border
  - Expanded agenda items: bullet list with warm-500 text
  - Document count badges: bg-neodemos-50 text-neodemos-700 rounded-full
  - Hover state: bg-neodemos-50/50
```

### 3.4 Partijvergelijker page (`parties.html`)
```
Layout: Grid of party cards
Each card:
  - Party name (font-serif) + party color accent bar
  - Key stats: % voor-stemmen, aantal moties ingediend
  - Topic positions as small badges
  - Click to expand: voting history on selected theme
```

### 3.5 Motie tracker page (`motions.html`)
```
Layout: Data table with filters
Columns: Datum | Titel | Ingediend door | Status | Stemverhouding
Filters: Periode, Partij, Beleidsgebied, Status
Status badges:
  - Aangenomen: bg-emerald-100 text-emerald-700
  - Verworpen: bg-red-100 text-red-700
  - Ingetrokken: bg-warm-100 text-warm-600
  - Aangehouden: bg-amber-100 text-amber-700
Use HyperUI data table component as base
```

---

## PHASE 4: GRAPESJS INTEGRATION

### 4.1 Fix the existing editor

Debug why `/admin/editor/home` shows a blank canvas:
```
1. Check browser console for JavaScript errors
2. Verify grapesjs is loading (window.grapesjs should exist)
3. Check if the storage backend endpoint returns page JSON
4. Verify grapesjs-preset-webpage is initialized
5. Test: manually add a component via console:
   editor.addComponents('<div class="p-8 bg-warm-50">Test block</div>')
   If this renders, the issue is storage/loading, not the editor itself.
```

### 4.2 Register Play Tailwind sections as GrapesJS blocks

For each `<section>` in the reskinned template files, register as a block:

```javascript
// Category: Layout Secties
const sections = [
  { id: 'nd-navbar', label: 'Navigatie', file: 'index.html', selector: 'header' },
  { id: 'nd-hero', label: 'Hero + Zoekbalk', file: 'index.html', section: 1 },
  { id: 'nd-features', label: 'Functies', file: 'index.html', section: 2 },
  { id: 'nd-about', label: 'Over Ons', file: 'index.html', section: 3 },
  { id: 'nd-pricing', label: 'Prijzen', file: 'pricing.html', section: 1 },
  { id: 'nd-testimonials', label: 'Testimonials', file: 'index.html', section: 4 },
  { id: 'nd-team', label: 'Team', file: 'index.html', section: 5 },
  { id: 'nd-blog-preview', label: 'Blog Preview', file: 'index.html', section: 6 },
  { id: 'nd-newsletter', label: 'Nieuwsbrief', file: 'index.html', section: 7 },
  { id: 'nd-contact', label: 'Contact Formulier', file: 'contact.html', section: 1 },
  { id: 'nd-footer', label: 'Footer', file: 'index.html', selector: 'footer' },
];

// Category: Pagina Templates (full pages)
const templates = [
  { id: 'tpl-landing', label: 'Landingspagina', file: 'index.html' },
  { id: 'tpl-about', label: 'Over NeoDemos', file: 'about.html' },
  { id: 'tpl-pricing', label: 'Prijzen', file: 'pricing.html' },
  { id: 'tpl-blog', label: 'Blog Overzicht', file: 'blog-grids.html' },
  { id: 'tpl-article', label: 'Blog Artikel', file: 'blog-details.html' },
  { id: 'tpl-contact', label: 'Contact', file: 'contact.html' },
  { id: 'tpl-login', label: 'Inloggen', file: 'signin.html' },
  { id: 'tpl-register', label: 'Registreren', file: 'signup.html' },
];

// Category: NeoDemos Custom (interactive widgets)
const customBlocks = [
  { id: 'nd-search', label: 'Zoekbalk', description: 'Search bar + API integration' },
  { id: 'nd-analyse', label: 'Analyse Output', description: 'MCP analysis result display' },
  { id: 'nd-calendar', label: 'Raadskalender', description: 'Meeting calendar embed' },
  { id: 'nd-voting', label: 'Stemkaart', description: 'Party voting visualization' },
  { id: 'nd-motion', label: 'Motie Kaart', description: 'Single motion with status' },
  { id: 'nd-party-grid', label: 'Partij Grid', description: 'Party comparison cards' },
];
```

### 4.3 Build template save/load (replaces paid Templates Manager)

```javascript
// Add to editor initialization
const templateManager = {
  async save(name) {
    const data = {
      html: editor.getHtml(),
      css: editor.getCss(),
      components: JSON.stringify(editor.getComponents()),
      styles: JSON.stringify(editor.getStyle()),
    };
    await fetch('/api/templates', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, data })
    });
  },

  async load(name) {
    const res = await fetch(`/api/templates/${name}`);
    const { data } = await res.json();
    editor.setComponents(JSON.parse(data.components));
    editor.setStyle(JSON.parse(data.styles));
  },

  async list() {
    const res = await fetch('/api/templates');
    return await res.json();
  }
};

// Add "Kies template" button to editor toolbar
editor.Panels.addButton('options', {
  id: 'template-select',
  className: 'fa fa-file-o',
  command: 'open-templates',
  attributes: { title: 'Kies template' }
});

editor.Commands.add('open-templates', {
  run(editor) {
    // Show modal with template list
    // On select: templateManager.load(name)
  }
});
```

### 4.4 Backend API for template storage

```python
# Add to your FastAPI app
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/templates")

class TemplateData(BaseModel):
    name: str
    data: dict

@router.post("")
async def save_template(template: TemplateData):
    # Save to PostgreSQL or filesystem
    pass

@router.get("")
async def list_templates():
    # Return list of saved templates
    pass

@router.get("/{name}")
async def get_template(name: str):
    # Return template data
    pass
```

---

## PHASE 5: ADMIN DASHBOARD ENHANCEMENT

Keep your existing `/admin` structure but apply NeoDemos styling:

### 5.1 Dashboard cards
```html
<!-- Replace current plain cards with warm styled cards -->
<div class="bg-white rounded-xl border border-warm-200 shadow-md shadow-warm-200/50 p-6">
  <p class="text-4xl font-serif font-bold text-warm-800">95</p>
  <p class="text-sm text-warm-500 mt-1 uppercase tracking-wide">Inhoudsitems</p>
</div>
```

### 5.2 Sidebar navigation
```html
<!-- Warm sidebar matching admin screenshot -->
<nav class="bg-warm-50 border-r border-warm-200 w-64 p-6">
  <h3 class="font-serif text-sm uppercase tracking-wider text-warm-500 mb-4">Beheer</h3>
  <a class="block px-4 py-2 rounded-lg bg-neodemos-50 text-neodemos-700 font-medium">Dashboard</a>
  <a class="block px-4 py-2 rounded-lg text-warm-600 hover:bg-warm-100">Inhoud</a>
  <a class="block px-4 py-2 rounded-lg text-warm-600 hover:bg-warm-100">Gebruikers</a>
  <a class="block px-4 py-2 rounded-lg text-warm-600 hover:bg-warm-100">Tokens</a>
  <a class="block px-4 py-2 rounded-lg text-warm-600 hover:bg-warm-100">Pagina's</a>
  <a class="block px-4 py-2 rounded-lg text-warm-600 hover:bg-warm-100">Instellingen</a>
</nav>
```

---

## PHASE 6: RESPONSIVE AND ACCESSIBILITY

### 6.1 Mobile-first verification
Test all pages at these breakpoints:
- 375px (iPhone SE)
- 390px (iPhone 14)
- 768px (iPad)
- 1024px (iPad landscape)
- 1440px (desktop)
- 1920px (large desktop)

### 6.2 WCAG 2.1 AA compliance
```bash
# Install axe-core for automated accessibility testing
npm install -D @axe-core/cli
npx axe http://localhost:3000 --rules wcag2aa
```

Check:
- Color contrast ratio ≥ 4.5:1 for text (verify warm-500 on warm-50 passes)
- All images have alt text
- All form inputs have labels
- Keyboard navigation works on all interactive elements
- Skip-to-content link present
- Language attribute: `<html lang="nl">`

### 6.3 Dark mode
Play Tailwind includes dark mode. Verify it works with warm colors:
```
Dark backgrounds: bg-warm-900 (not cold slate/gray)
Dark text: text-warm-200
Dark borders: border-warm-800
Dark cards: bg-warm-800/50
Toggle in navbar: preserve existing behavior
```

---

## COMPLETE DOWNLOAD CHECKLIST

| # | Item | Source | Cost | Status |
|---|------|--------|------|--------|
| 1 | Play Tailwind template | github.com/TailGrids/play-tailwind | €0 | ☐ Clone |
| 2 | HyperUI components | hyperui.dev | €0 | ☐ Copy-paste as needed |
| 3 | grapesjs-preset-webpage | npm (official) | €0 | ☐ npm install |
| 4 | grapesjs-custom-code | npm (official) | €0 | ☐ npm install |
| 5 | grapesjs-plugin-export | npm (official) | €0 | ☐ npm install |
| 6 | grapesjs-touch | npm (official) | €0 | ☐ npm install |
| 7 | grapesjs-tui-image-editor | npm (official) | €0 | ☐ npm install |
| 8 | Crimson Pro font | Google Fonts | €0 | ☐ Add to <head> |
| 9 | DM Sans font | Google Fonts | €0 | ☐ Add to <head> |
| 10 | @tailwindcss/typography | npm | €0 | ☐ npm install |
| 11 | @tailwindcss/forms | npm | €0 | ☐ npm install |
| 12 | Alpine.js | CDN | €0 | ☐ Add script tag |
| 13 | Chart.js | npm | €0 | ☐ npm install |
| 14 | FullCalendar | npm | €0 | ☐ npm install |
| 15 | axe-core | npm (dev) | €0 | ☐ npm install -D |
| **TOTAL** | | | **€0** | |

---

## PAGE MAPPING SUMMARY

| NeoDemos page | Source | Notes |
|--------------|--------|-------|
| Homepagina (/) | Play Tailwind `index.html` reskinned | Hero + search + features + pricing preview |
| Over (/over) | Play Tailwind `about.html` reskinned | Mission, team, how it works |
| Prijzen (/pricing) | Play Tailwind `pricing.html` reskinned | Gratis / Pro €19 / Raadslid €49 |
| Onderzoeken (/blog) | Play Tailwind `blog-grids.html` reskinned | Investigation article cards |
| Onderzoek detail (/blog/:slug) | Play Tailwind `blog-details.html` reskinned | Single investigation |
| Contact (/contact) | Play Tailwind `contact.html` reskinned | Feedback + press inquiries |
| Inloggen (/signin) | Play Tailwind `signin.html` reskinned | Login flow |
| Registreren (/signup) | Play Tailwind `signup.html` reskinned | New user signup |
| 404 | Play Tailwind `404.html` reskinned | Error page |
| **Zoeken (/search)** | **NEW — build from HyperUI components** | Search + results |
| **Analyse (/analyse)** | **NEW — build from HyperUI components** | AI output + voting cards |
| **Kalender (/calendar)** | **KEEP EXISTING — restyle only** | Meeting calendar |
| **Partijen (/parties)** | **NEW — build from HyperUI components** | Party comparison grid |
| **Moties (/motions)** | **NEW — build from HyperUI data table** | Motion tracker |
| **Beheer (/admin)** | **KEEP EXISTING — restyle only** | Admin dashboard |
| **Editor (/admin/editor)** | **FIX + enhance with new blocks** | GrapesJS page editor |
| **AI-koppeling** | **KEEP EXISTING — restyle only** | MCP connector settings |
| **Instellingen** | **KEEP EXISTING — restyle only** | User settings |

**9 pages from template + 5 new pages built + 4 existing pages restyled = 18 total pages**

---

## EXECUTION ORDER

```
Day 1: Phase 1 (download everything) + Phase 2 (reskin Play Tailwind)
Day 2: Phase 3 (build new NeoDemos pages: search, analyse, parties, motions)
Day 3: Phase 4 (GrapesJS integration — fix editor, register blocks, template manager)
Day 4: Phase 5 (admin dashboard enhancement) + Phase 6 (responsive + accessibility)
Day 5: Testing, polish, deploy
```

---

*Total cost: €0. All tools are MIT/open-source licensed for commercial use.*
*Generated from NeoDemos planning session, April 14, 2026.*
