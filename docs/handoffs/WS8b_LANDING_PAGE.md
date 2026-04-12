# WS8b — Landing Page Redesign

> **Status:** `not started`
> **Owner:** `unassigned`
> **Priority:** 1 (critical path to public launch)
> **Depends on:** WS8a (CSS architecture must be complete)
> **Parallelizable:** yes (with WS8c, WS8d)

---

## TL;DR

Strip `search.html` from 865 lines to ~200 lines. The landing page becomes 4 elements: a pre-rendered demo answer, a search bar, a credibility line, and a trust line. Remove the "Debat Voorbereiding" toggle, about block, and MCP marketing. Add headline rotation via env var, account nudge after 3rd search (sessionStorage), MCP nudge after 5th search.

---

## Cold-start prompt

```
You are picking up WS8b_LANDING_PAGE for the NeoDemos project.

Read these files first:
- docs/handoffs/WS8b_LANDING_PAGE.md (this file)
- docs/handoffs/WS8_FRONTEND_REDESIGN.md §Phase 2 (full landing page spec)
- templates/search.html (current 865-line template — your main target)
- templates/base.html (should already have WS8a changes: Tailwind, new structure)
- main.py (route definitions, especially the / route around line 647)
- static/css/main.css (Tailwind @theme tokens from WS8a)

Your job: redesign the landing page. Strip search.html to 4 elements. Create the search
bar and demo answer partials. Add headline rotation and nudge logic.

Key constraints:
- Use Tailwind CSS utility classes (set up in WS8a)
- Remove "Debat Voorbereiding" toggle entirely
- Pre-rendered demo answer is cached server-side, no API cost on page load
- sessionStorage for search counter (GDPR-safe, no cookies)
- All text in Dutch
- Mobile: search bar above fold on 375px, demo answer collapsed
```

---

## Files to read first

| File | Why |
|---|---|
| `templates/search.html` | Current landing page — 865 lines to simplify |
| `main.py` lines 647-700 | The `/` route handler and search API |
| `templates/base.html` | Updated template structure from WS8a |
| `static/css/main.css` | Tailwind tokens available |

---

## Build Tasks

### 1. Simplify search.html to 4 elements

**Remove:**
- Lines 42-90: `.switch` / `.slider` toggle CSS
- Lines 387-394: "Debat Voorbereiding" toggle HTML
- Lines 435-531: "Wat is NeoDemos?" about block + MCP marketing (moves to /over and /mcp-installer)
- All inline `<style>` blocks (should already be gone from WS8a)

**Keep:**
- Search bar + @mention autocomplete logic
- Search results rendering (keyword + AI answer)
- Citation bubble rendering
- Copy-to-Word functionality

**Add:**
- Hero section with rotating headline
- Pre-rendered demo answer card
- Credibility line
- Trust line
- Account nudge banner (hidden, shown after 3rd search)
- MCP nudge card (hidden, shown after 5th search)

### 2. Create search bar partial

**`templates/partials/_search_bar.html`:**
Extracted from search.html. Contains:
- Search textarea (auto-expanding)
- "Zoek" button
- @mention autocomplete menu
- "Of probeer:" with 3 clickable example queries

### 3. Create demo answer partial

**`templates/partials/_demo_answer.html`:**
- Renders a cached AI answer with citation bubbles
- Data passed from server as template context (`demo_answer` variable)
- Desktop: full card with gold left border, shadow
- Mobile: collapsed behind `<details><summary>Bekijk voorbeeld</summary></details>`

### 4. Add headline rotation

In `main.py`, read `LANDING_HEADLINE` from environment:
```python
LANDING_HEADLINE = os.getenv("LANDING_HEADLINE",
    "De raadsvergadering was altijd openbaar.\nNu is ze ook begrijpelijk.")
```
Pass to template context in the `/` route. The `\n` renders as `<br>` in the template.

### 5. Add search counter + nudge logic

**JavaScript (in search.html or app.js):**
```js
// GDPR-safe: sessionStorage, not cookies
function incrementSearchCount() {
  const count = parseInt(sessionStorage.getItem('neodemos_searches') || '0') + 1;
  sessionStorage.setItem('neodemos_searches', count.toString());
  return count;
}

// After each search completes:
const count = incrementSearchCount();
if (count >= 3 && !user && !sessionStorage.getItem('neodemos_nudge_dismissed')) {
  document.getElementById('account-nudge').hidden = false;
}
if (count >= 5 && !sessionStorage.getItem('neodemos_mcp_dismissed')) {
  document.getElementById('mcp-nudge').hidden = false;
}
```

### 6. Update base.html navigation

Add links for anonymous users: Zoeken, Kalender, Over, Inloggen

---

## Landing Page Layout

```
+--------------------------------------------------------------+
|  NeoDemos                              [Kalender] [Inloggen] |
+--------------------------------------------------------------+
|                                                              |
|  De raadsvergadering was altijd openbaar.                    |
|  Nu is ze ook begrijpelijk.                                  |
|                                                              |
|  [Pre-rendered demo answer with citations]                   |
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
|  90.000+ documenten . 24 jaar . Rotterdam . Open brondata   |
|                                                              |
|  Europees gehost . Elke bewering herleidbaar naar            |
|  het brondocument . Werkt met lokale AI                      |
+--------------------------------------------------------------+
```

---

## Files to create

| File | Purpose |
|---|---|
| `templates/partials/_search_bar.html` | Reusable search input with @mention |
| `templates/partials/_demo_answer.html` | Pre-rendered demo answer card |

## Files to modify

| File | Change |
|---|---|
| `templates/search.html` | Radical simplification: 865 → ~200 lines |
| `main.py` | Add LANDING_HEADLINE env var, demo answer context |
| `templates/base.html` | Update nav for anonymous users |

---

## Acceptance Criteria

- [ ] Landing page has exactly 4 elements (demo, search, credibility, trust)
- [ ] "Debat Voorbereiding" toggle removed entirely
- [ ] Pre-rendered demo answer visible (mobile: behind "Bekijk voorbeeld")
- [ ] Example queries clickable and trigger search
- [ ] Search bar above fold on 375px width
- [ ] Account nudge appears after 3rd search (anonymous only), dismissable
- [ ] MCP nudge appears after 5th search (all users), dismissable
- [ ] sessionStorage used for counter (not cookies)
- [ ] Headline changes when LANDING_HEADLINE env var is set
- [ ] All search/citation/copy-to-Word JS still works
