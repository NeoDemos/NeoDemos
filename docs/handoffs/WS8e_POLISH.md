# WS8e — Template Polish, Responsive & Convergence

> **Status:** `done` — shipped 2026-04-12
> **Owner:** `dennis + claude`
> **Priority:** 3
> **Depends on:** WS8b, WS8c, WS8d (all must be complete)
> **Parallelizable:** no (convergence workstream)

---

## TL;DR

Final polish pass: deduplicate shared components into Jinja2 partials, ensure responsive behavior on both desktop and mobile feels native (not just "responsive"), verify navigation and footer consistency across all pages, clean up orphaned templates, and run Lighthouse/Playwright verification.

---

## Cold-start prompt

```
You are picking up WS8e_POLISH for the NeoDemos project — the convergence workstream
for the frontend redesign.

Read these files first:
- docs/handoffs/WS8e_POLISH.md (this file)
- docs/handoffs/WS8_FRONTEND_REDESIGN.md §Phase 4 (full polish spec)
- templates/base.html (current state after WS8a-d)
- templates/partials/_nav.html (from WS8a)
- templates/partials/_footer.html (from WS8a)
- templates/search.html (from WS8b)
- templates/calendar.html (from WS8c)
- templates/meeting.html (needs responsive pass)
- templates/over.html, technologie.html, methodologie.html (from WS8d)

Your job: polish everything. Deduplicate shared code into partials, verify responsive
behavior, test on 375px/768px/1440px viewports, clean up orphaned files.

Key constraints:
- Citation bubble code is duplicated in search.html and meeting.html — extract to partial
- Navigation must have active page indicator
- Mobile hamburger menu must work on all pages
- Touch targets: 44px minimum on mobile
- Run Playwright screenshots for visual verification
```

---

## Build Tasks

### 1. Extract shared partials

| Partial | Source (duplicated in) | Purpose |
|---|---|---|
| `templates/partials/_citation_bubble.html` | `search.html`, `meeting.html` | Citation rendering (gold numbered circles) |
| `templates/partials/_trust_strip.html` | `search.html` (landing), `_footer.html` | EU/security trust badges |

**Citation bubble extraction:**
The `.citation-bubble` CSS and the `renderCitations()` JS function are near-identical in both search.html and meeting.html. Extract:
- CSS: into Tailwind component or `@layer components` in main.css
- JS: into a shared function in `static/js/citations.js`

### 2. Navigation polish

**`templates/partials/_nav.html`** — ensure:
- Active page indicator (bottom border in accent color)
- Detect current page via `request.url.path` in Jinja2:
  ```html
  <a href="/" class="nav-link {% if request.url.path == '/' %}nav-link--active{% endif %}">Zoeken</a>
  ```
- Mobile hamburger: verify it works on all pages, not just landing
- Touch targets: 44px minimum height on mobile nav links

### 3. Footer consistency

**`templates/partials/_footer.html`** — verify:
- Shows on all pages
- Links to `/over`, `/technologie`, `/methodologie`, `/mcp-installer`
- Version badge from `{{ version_label }}`
- AI transparency: "Analyse door AI (Anthropic)"

### 4. Responsive verification

**Desktop (1024px+):**
- Max-width 1200px container
- Generous whitespace
- Hover states on interactive elements
- Multi-column layouts where appropriate

**Tablet (768-1024px):**
- 2-column layouts collapse appropriately
- Touch-friendly target sizes

**Mobile (375px+):**
- [ ] Search bar + "Zoek" above fold on 375px
- [ ] Hamburger menu works
- [ ] Demo answer collapsed behind "Bekijk voorbeeld"
- [ ] Calendar filter chips: horizontal scroll
- [ ] Meeting rows: stacked layout
- [ ] AI answer: full-width, sources collapsible
- [ ] Touch targets: 44px minimum
- [ ] No horizontal overflow on any page

### 5. Meeting page responsive pass

`templates/meeting.html` needs specific attention:
- The 2-column layout (agenda + AI sidebar) should stack vertically on mobile
- The sticky AI sidebar should become a non-sticky section below the agenda
- Agenda item accordion should work with touch (no hover-dependent interactions)
- Copy-to-Word button should be full-width on mobile

### 6. Cleanup

- Delete `templates/index.html` (144 lines, orphaned standalone page)
- Update `templates/overview.html` to use `_meeting_card.html` partial from WS8c
- Verify no orphaned CSS classes or unused component styles

### 7. Verification

**Playwright MCP screenshots** (use installed Playwright MCP):
- Every page at 375px (iPhone SE), 768px (iPad), 1440px (desktop)
- Pages to test: `/`, `/over`, `/technologie`, `/methodologie`, `/calendar`, `/meeting/{id}`, `/login`, `/mcp-installer`

**Lighthouse audit:**
```bash
lighthouse https://localhost:8000 --only-categories=performance,accessibility
```
Targets: Performance >90, Accessibility >95

**CSS audit:**
```bash
grep '<style>' templates/*.html  # Should return nothing
grep -c 'style=' templates/*.html  # Should be near-zero
```

---

## Files to create

| File | Purpose |
|---|---|
| `templates/partials/_citation_bubble.html` | Shared citation rendering |
| `templates/partials/_trust_strip.html` | Shared trust badges |
| `static/js/citations.js` | Shared citation JS (extracted from search + meeting) |

## Files to modify

| File | Change |
|---|---|
| `templates/search.html` | Use `_citation_bubble.html` partial, remove duplicate code |
| `templates/meeting.html` | Use `_citation_bubble.html` partial, add responsive styles |
| `templates/overview.html` | Use `_meeting_card.html` partial |
| `templates/partials/_nav.html` | Add active page indicator |
| `templates/partials/_footer.html` | Verify link completeness |

## Files to delete

| File | Reason |
|---|---|
| `templates/index.html` | Orphaned, not served by any route |
| `static/css/style.css` | Replaced by Tailwind (should already be deleted in WS8a) |

---

## Acceptance Criteria

- [ ] Shared partials exist for citation bubble and trust strip
- [ ] No duplicated CSS or JS across templates
- [ ] Navigation has active page indicator on all pages
- [ ] Hamburger menu works on all pages (mobile)
- [ ] Footer consistent across all pages
- [ ] Meeting page responsive on mobile (stacked layout)
- [ ] Playwright screenshots captured at 375px, 768px, 1440px
- [ ] Lighthouse Performance >90, Accessibility >95
- [ ] No orphaned templates or CSS files
- [ ] Touch targets 44px minimum on all interactive mobile elements
