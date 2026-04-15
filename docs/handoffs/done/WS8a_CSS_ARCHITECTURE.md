# WS8a — CSS Architecture: Vite + Tailwind + Design Tokens

> **Status:** `done` — shipped 2026-04-12
> **Owner:** `dennis + claude`
> **Priority:** 0 (prerequisite for WS8b, WS8c, WS8d)
> **Parallelizable:** no (must complete before other WS8 workstreams)

---

## TL;DR

Set up Vite + Tailwind CSS v4 as the build tooling foundation, create the design token system, self-host fonts, extract all inline styles from templates into Tailwind utility classes or component CSS, and update `base.html` with the new architecture. This is the foundation that all other WS8 workstreams depend on.

---

## Cold-start prompt

```
You are picking up WS8a_CSS_ARCHITECTURE for the NeoDemos project — a civic intelligence
platform for the Rotterdam municipal council.

Read these files first:
- docs/handoffs/done/WS8a_CSS_ARCHITECTURE.md (this file — full spec)
- docs/handoffs/done/WS8_FRONTEND_REDESIGN.md (parent spec — design tokens, colors, typography)
- templates/base.html (current template structure)
- static/css/style.css (current styles — will be decomposed)
- design/design_kit/neodemos-design-kit.svg (component library with token values)
- design/canva_designs/*.png (3 Canva templates — color direction: dark green + beige)
- design/canva_designs/NeoDemos.svg (logo — orange accent #ff751f)
- Dockerfile (needs npm build step added)

Your job: implement the CSS architecture foundation. This is a prerequisite for all other
WS8 workstreams (WS8b landing page, WS8c calendar, WS8d subpages).

Key constraints:
- Vite + Tailwind CSS v4 (NOT v3 — v4 uses @theme, not tailwind.config.js)
- Color palette: dark green + beige/cream from Canva templates, orange (#ff751f) for CTAs
- Self-host Inter + Instrument Serif (WOFF2 to /static/fonts/)
- Extract ALL inline <style> blocks and style="" attributes from templates
- Refactor JS-generated inline styles (calendar.html, meeting.html, search.html) to CSS classes
- All existing JS functionality must keep working (SSE, @mention, citations, copy-to-Word)
- Kamal deploy: static assets at /static/, cache-bust via ?v={{ version_label }}
```

---

## Dependencies

| Dependency | Type | Notes |
|---|---|---|
| None | — | This is the foundation workstream |

## Blocks

| Blocked workstream | Why |
|---|---|
| WS8b (Landing page) | Needs tokens + Tailwind setup |
| WS8c (Calendar) | Needs tokens + Tailwind setup |
| WS8d (Subpages) | Needs tokens + Tailwind setup |

---

## Build Tasks

### 1. Initialize Vite + Tailwind CSS v4

```bash
npm create vite@latest . -- --template vanilla
npm install -D tailwindcss@4 @tailwindcss/vite
```

**`vite.config.js`:**
```js
import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [tailwindcss()],
  build: {
    outDir: 'static/dist',
    rollupOptions: {
      input: {
        main: 'static/css/main.css',
        app: 'static/js/app.js',
      },
      output: {
        assetFileNames: '[name].[ext]',
        entryFileNames: '[name].js',
      }
    }
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/': 'http://localhost:8000',
    }
  }
});
```

### 2. Create design tokens

**`static/css/main.css`** — Tailwind entry point:

```css
@import "tailwindcss";

/* Self-hosted fonts */
@font-face {
  font-family: 'Inter';
  src: url('/static/fonts/Inter-Regular.woff2') format('woff2');
  font-weight: 400; font-style: normal; font-display: swap;
}
/* ... repeat for 500, 600, 700 weights */

@font-face {
  font-family: 'Instrument Serif';
  src: url('/static/fonts/InstrumentSerif-Regular.woff2') format('woff2');
  font-weight: 400; font-style: normal; font-display: swap;
}
@font-face {
  font-family: 'Instrument Serif';
  src: url('/static/fonts/InstrumentSerif-Italic.woff2') format('woff2');
  font-weight: 400; font-style: italic; font-display: swap;
}

@theme {
  /* === Colors (derived from Canva dark green + beige templates) === */
  /* Extract exact hex values from the Canva template SVGs */
  --color-primary: /* dark green */;
  --color-primary-hover: /* darker green */;
  --color-primary-light: /* soft green */;
  --color-secondary: /* deep forest/navy */;
  --color-accent: #ff751f; /* orange from logo SVG */
  --color-accent-light: /* light orange/peach */;
  --color-surface: /* cream/beige background */;
  --color-surface-raised: #ffffff;
  --color-surface-sunken: /* light beige */;
  --color-border: /* warm grey */;
  --color-text: /* near-black */;
  --color-text-secondary: /* medium grey */;
  --color-text-tertiary: /* light grey */;
  --color-success: #2D7A4F;
  --color-success-light: #E8F5EE;
  --color-warning: #9A6C1E;
  --color-warning-light: #FDF4E3;
  --color-error: #C23A3A;
  --color-error-light: #FDE8E8;

  /* === Typography === */
  --font-heading: 'Instrument Serif', Georgia, 'Times New Roman', serif;
  --font-body: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;

  /* === Spacing (8px base) === */
  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --space-5: 1.25rem;
  --space-6: 1.5rem;
  --space-8: 2rem;
  --space-10: 2.5rem;
  --space-12: 3rem;
  --space-16: 4rem;
  --space-20: 5rem;

  /* === Shadows (navy-tinted, layered) === */
  --shadow-xs: 0 1px 2px rgba(27,42,74,0.04), 0 1px 3px rgba(27,42,74,0.06);
  --shadow-sm: 0 2px 4px rgba(27,42,74,0.04), 0 4px 8px rgba(27,42,74,0.06);
  --shadow-md: 0 4px 8px rgba(27,42,74,0.04), 0 8px 24px rgba(27,42,74,0.08);
  --shadow-lg: 0 8px 16px rgba(27,42,74,0.06), 0 16px 48px rgba(27,42,74,0.1);

  /* === Border radius === */
  --radius-sm: 4px;
  --radius-md: 8px;
  --radius-lg: 12px;
  --radius-xl: 16px;
  --radius-full: 9999px;
}
```

### 3. Self-host fonts

Download WOFF2 files to `static/fonts/`:
- `Inter-Regular.woff2` (400)
- `Inter-Medium.woff2` (500)
- `Inter-SemiBold.woff2` (600)
- `Inter-Bold.woff2` (700)
- `InstrumentSerif-Regular.woff2` (400)
- `InstrumentSerif-Italic.woff2` (400i)

Source: Google Fonts API or github.com/rsms/inter and github.com/Instrument/instrument-serif

### 4. Extract inline styles from templates

**Priority order** (by inline style volume):

| Template | `style=""` count | `<style>` lines | Action |
|---|---|---|---|
| `search.html` | 73 | ~350 | Replace with Tailwind utilities + component CSS |
| `meeting.html` | 58 | ~40 | Replace with Tailwind utilities |
| `mcp_installer.html` | 22 | ~150 | Replace with Tailwind utilities |
| `calendar.html` | 21 | ~10 | Replace with Tailwind utilities |
| `settings.html` | 10 | ~60 | Replace with Tailwind utilities |
| `admin.html` | 4 | ~40 | Replace with Tailwind utilities |

**Critical: JS-generated inline styles** must be refactored to CSS classes:

- `calendar.html` lines 111, 130, 144, 154: `element.style.cssText = ...`
  → Replace with `.calendar-day`, `.calendar-day--has-meetings`, `.calendar-day--today`, `.calendar-meeting-link` classes
  → JS uses `element.classList.add()` instead of `element.style.cssText`

- `meeting.html` `renderFinalAnalysis()`: generates HTML with inline styles
  → Replace with `.cited-sources-container`, `.cited-source-item` classes

- `search.html` mention menu (lines 812-828): creates elements with inline styles
  → Replace with `.mention-item`, `.mention-icon`, `.mention-label` classes

### 5. Update `base.html`

```html
<!DOCTYPE html>
<html lang="nl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {% block meta %}{% endblock %}
    <title>{% block title %}{{ title }}{% endblock %} | NeoDemos</title>
    <link rel="icon" type="image/svg+xml" href="/static/images/favicon.svg">
    <link rel="stylesheet" href="/static/dist/main.css?v={{ version_label }}">
    {% block styles %}{% endblock %}
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
</head>
<body>
    {% include 'partials/_nav.html' %}
    {% block hero %}{% endblock %}
    <main>
        {% block content %}{% endblock %}
    </main>
    {% include 'partials/_footer.html' %}
    <script src="/static/dist/main.js?v={{ version_label }}"></script>
    {% block extra_scripts %}{% endblock %}
</body>
</html>
```

### 6. Create nav and footer partials

**`templates/partials/_nav.html`:**
- Desktop: horizontal nav, logo left, links right
- Mobile: hamburger menu (CSS + vanilla JS)
- Active page indicator

**`templates/partials/_footer.html`:**
```
NeoDemos v0.2.0 . Brondata: Open Raadsinformatie
Over . Technologie . Methodologie . Privacy . MCP
Analyse door AI (Anthropic) . Europees gehost
```

### 7. Update Dockerfile

Add npm build step before the Python app starts:
```dockerfile
# Install Node.js for Vite build
RUN apk add --no-cache nodejs npm
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build
```

### 8. Delete old files

- `static/css/style.css` (contents distributed to Tailwind)

---

## Acceptance Criteria

- [ ] `npm run build` produces `static/dist/main.css` and `static/dist/main.js`
- [ ] `npm run dev` starts Vite dev server with hot reload
- [ ] Zero `<style>` blocks in templates (`grep '<style>' templates/*.html` returns nothing)
- [ ] Near-zero inline `style=""` attributes (only unavoidable JS-generated ones)
- [ ] No hardcoded hex colors outside Tailwind @theme config
- [ ] Instrument Serif renders on all headings (h1-h3)
- [ ] Inter renders on body text (self-hosted, no Google Fonts CDN)
- [ ] All existing JS works: SSE streaming, @mention autocomplete, citation bubbles, copy-to-Word, calendar grid
- [ ] Dockerfile builds successfully with npm step
- [ ] Visual regression: site looks intentionally different (new fonts), not broken

---

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| JS-generated inline styles break when refactored to classes | Medium | Test each template's JS after refactoring; keep old style as CSS class fallback |
| Vite build output path conflicts with existing static file serving | Low | Use `static/dist/` subfolder, update base.html link to `/static/dist/main.css` |
| Tailwind v4 API differences from v3 docs | Medium | Use official v4 docs only; v4 uses @theme, not tailwind.config.js |
| Font WOFF2 download adds to Docker image size | Low | ~420KB total, negligible |
