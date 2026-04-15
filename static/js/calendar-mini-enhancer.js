// WS8f Phase 7+ Block 5 — nd-calendar-mini public-page hydration.
// Looks for .nd-calendar-mini elements on the page and fills their list from
// /api/calendar/upcoming. Early-returns when no widgets are present, so it is
// safe to include globally in base.html.
(function () {
  'use strict';
  const els = document.querySelectorAll('.nd-calendar-mini');
  if (!els.length) return;

  // Fetch once with the max limit we might need, then slice per-widget.
  const maxLimit = Math.max.apply(
    null,
    Array.prototype.map.call(els, function (el) {
      const n = parseInt(el.getAttribute('data-limit') || '5', 10);
      return Number.isFinite(n) && n > 0 ? n : 5;
    })
  );

  fetch('/api/calendar/upcoming?limit=' + encodeURIComponent(Math.min(10, maxLimit)))
    .then(function (r) { return r.ok ? r.json() : { meetings: [] }; })
    .then(function (data) {
      const meetings = (data && data.meetings) || [];
      els.forEach(function (el) {
        const list = el.querySelector('.nd-calendar-mini__list');
        if (!list) return;
        const lim = parseInt(el.getAttribute('data-limit') || '5', 10) || 5;
        const slice = meetings.slice(0, lim);
        if (!slice.length) {
          list.innerHTML = '<li>Geen aankomende vergaderingen.</li>';
          return;
        }
        list.innerHTML = slice.map(function (m) {
          const date = (m.date_short || '').replace(/</g, '&lt;');
          const name = (m.name || '').replace(/</g, '&lt;');
          const id = encodeURIComponent(m.id || '');
          return '<li><a href="/meeting/' + id + '">' +
                 '<span class="cal-mini-date">' + date + '</span>' +
                 '<span class="cal-mini-name">' + name + '</span>' +
                 '</a></li>';
        }).join('');
      });
    })
    .catch(function () { /* silent — widget just stays on placeholder */ });
})();
