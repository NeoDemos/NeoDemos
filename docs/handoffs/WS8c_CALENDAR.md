# WS8c — Calendar Redesign

> **Status:** `done` — shipped 2026-04-12
> **Owner:** `dennis + claude`
> **Priority:** 2
> **Depends on:** WS8a (CSS architecture must be complete)
> **Parallelizable:** yes (with WS8b, WS8d)

---

## TL;DR

Replace the calendar grid with a filterable meeting list as the default view. Add committee filter chips, search within meetings, URL state for shareable filters, and `<details>/<summary>` expansion for agenda items. Keep the calendar grid as an optional toggle. Make the calendar route public (remove login requirement).

---

## Cold-start prompt

```
You are picking up WS8c_CALENDAR for the NeoDemos project.

Read these files first:
- docs/handoffs/WS8c_CALENDAR.md (this file)
- docs/handoffs/done/WS8_FRONTEND_REDESIGN.md §Phase 3 (full calendar spec)
- templates/calendar.html (current 227-line calendar grid)
- services/storage.py (meeting queries — need to add filtered query with counts)
- main.py (calendar route, around line 806)
- templates/base.html (updated structure from WS8a)
- static/css/main.css (Tailwind tokens from WS8a)

Your job: redesign the calendar page. Default view becomes a filterable meeting list.
Add backend query for agenda item counts. Keep grid as toggle.

Key constraints:
- Use Tailwind CSS utility classes
- Client-side filtering on server-passed JSON (no additional API calls for filters)
- URL state via history.replaceState for shareable filtered views
- <details>/<summary> for meeting expansion (no custom accordion JS)
- Mobile: horizontal-scroll filter chips, stacked meeting rows
- Make route public (remove login requirement)
```

---

## Build Tasks

### 1. Backend: Add filtered meetings query

**`services/storage.py`** — add method:

```python
async def get_meetings_with_counts(self, year=None, committee=None, search=None, limit=200):
    """Get meetings with agenda item counts, supporting filters."""
    query = """
        SELECT m.*, COUNT(ai.id) as agenda_item_count
        FROM meetings m
        LEFT JOIN agenda_items ai ON ai.meeting_id = m.id
        WHERE ($1::int IS NULL OR EXTRACT(YEAR FROM m.start_date) = $1)
          AND ($2::text IS NULL OR m.committee ILIKE '%' || $2 || '%')
          AND ($3::text IS NULL OR m.name ILIKE '%' || $3 || '%')
        GROUP BY m.id
        ORDER BY m.start_date DESC
        LIMIT $4
    """
    return await self.pool.fetch(query, year, committee, search, limit)
```

### 2. Backend: Update calendar route

**`main.py`** — modify `/calendar` route:
- Remove `require_login` dependency (make public)
- Use `get_current_user` instead (optional auth)
- Accept `committee` and `search` query params
- Call `get_meetings_with_counts()` instead of current query
- Pass full meetings JSON with agenda_item_count included

### 3. Frontend: Filterable list view

**`templates/calendar.html`** — rewrite:

```html
{% extends "base.html" %}
{% block styles %}{% endblock %}
{% block content %}
<div class="calendar-container">
  <!-- Search bar -->
  <input type="search" id="meeting-search" placeholder="Zoek vergaderingen..."
         class="...tailwind classes...">

  <!-- Committee filter chips -->
  <div class="filter-chips overflow-x-auto whitespace-nowrap">
    <button class="filter-chip filter-chip--active" data-filter="all">Alle</button>
    <button class="filter-chip" data-filter="raadsvergadering">Raadsvergadering</button>
    <button class="filter-chip" data-filter="commissie-bwb">Commissie BWB</button>
    <button class="filter-chip" data-filter="commissie-zocs">Commissie ZOCS</button>
    <button class="filter-chip" data-filter="commissie-abvm">Commissie ABVM</button>
  </div>

  <!-- Time filter chips -->
  <div class="filter-chips">
    <button class="filter-chip" data-time="upcoming">Komend</button>
    <button class="filter-chip" data-time="month">Deze maand</button>
    <button class="filter-chip" data-time="year">Dit jaar</button>
  </div>

  <!-- Meeting list -->
  <div id="meeting-list">
    <!-- JS renders date-grouped meeting rows here -->
  </div>

  <!-- Grid toggle -->
  <button id="view-toggle" aria-label="Toon kalenderrooster">
    <!-- Grid icon SVG -->
  </button>

  <!-- Hidden grid container (shown on toggle) -->
  <div id="calendar-grid-container" hidden>
    <!-- Old calendar grid JS renders here -->
  </div>
</div>
{% endblock %}
```

### 4. JavaScript (~150 lines)

```js
const meetings = JSON.parse(document.getElementById('meetings-data').textContent);
let activeFilter = 'all';
let activeTime = null;
let searchQuery = '';

function filterMeetings() {
  return meetings.filter(m => {
    const matchesFilter = activeFilter === 'all' ||
      m.committee?.toLowerCase().includes(activeFilter);
    const matchesSearch = !searchQuery ||
      m.name.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesTime = !activeTime || checkTimeFilter(m, activeTime);
    return matchesFilter && matchesSearch && matchesTime;
  });
}

function renderMeetingList() {
  const filtered = filterMeetings();
  const grouped = groupByDate(filtered);
  // Render date groups with sticky headers
  // Each meeting is a <details> element
  updateURLState();
}

function updateURLState() {
  const params = new URLSearchParams();
  if (activeFilter !== 'all') params.set('committee', activeFilter);
  if (searchQuery) params.set('search', searchQuery);
  history.replaceState(null, '', '?' + params.toString());
}

// 250ms debounce on search
let searchTimeout;
document.getElementById('meeting-search').addEventListener('input', (e) => {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => {
    searchQuery = e.target.value;
    renderMeetingList();
  }, 250);
});
```

### 5. Meeting row component

Each meeting renders as:
```html
<details class="meeting-row">
  <summary class="flex items-center gap-4 p-4 ...">
    <div class="meeting-row-date">
      <span class="text-xl font-bold">12 apr</span>
      <span class="text-sm text-secondary">2026</span>
    </div>
    <div class="meeting-row-info flex-1">
      <span class="font-semibold">Raadsvergadering</span>
      <span class="text-sm text-secondary">Gemeenteraad Rotterdam</span>
    </div>
    <span class="badge badge-plenair">Plenair</span>
    <span class="text-sm">14 items</span>
  </summary>
  <div class="meeting-row-expanded p-4 border-t">
    <!-- Agenda items with doc counts, link to full meeting page -->
  </div>
</details>
```

### 6. Create meeting card partial

**`templates/partials/_meeting_card.html`:**
Reusable meeting row component. Used by both calendar and overview pages.

---

## Files to create

| File | Purpose |
|---|---|
| `templates/partials/_meeting_card.html` | Reusable meeting row |

## Files to modify

| File | Change |
|---|---|
| `services/storage.py` | Add `get_meetings_with_counts()` |
| `main.py` | Modify calendar route: public, new query, filter params |
| `templates/calendar.html` | Rewrite as filterable list + grid toggle |

---

## Acceptance Criteria

- [ ] Default view is filterable meeting list (not calendar grid)
- [ ] Committee filter chips toggle correctly (client-side)
- [ ] Search within meetings works with 250ms debounce
- [ ] URL reflects current filter state (shareable)
- [ ] Date groups with sticky headers (Vandaag, Deze week, month names)
- [ ] `<details>` expansion shows agenda items + doc count
- [ ] Grid view accessible via toggle icon
- [ ] Calendar route is public (no login required)
- [ ] Mobile: horizontal-scroll filter chips, stacked meeting rows
- [ ] agenda_item_count displays correctly per meeting
