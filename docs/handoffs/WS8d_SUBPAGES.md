# WS8d — Subpages: Over, Technologie, Methodologie

> **Status:** `not started`
> **Owner:** `unassigned`
> **Priority:** 2
> **Depends on:** WS8a (CSS architecture must be complete)
> **Parallelizable:** yes (with WS8b, WS8c — fully independent, no file conflicts)

---

## TL;DR

Create three new subpages (`/over`, `/technologie`, `/methodologie`) for content moved off the landing page. Each page uses photography from `design/photos/`, extends `base.html`, and uses Tailwind CSS from WS8a. These pages are fully independent of WS8b and WS8c — no shared file conflicts.

---

## Cold-start prompt

```
You are picking up WS8d_SUBPAGES for the NeoDemos project — a civic intelligence
platform for the Rotterdam municipal council.

Read these files first:
- docs/handoffs/WS8d_SUBPAGES.md (this file)
- docs/handoffs/WS8_FRONTEND_REDESIGN.md §2D (subpage content spec)
- templates/base.html (updated structure from WS8a)
- static/css/main.css (Tailwind tokens from WS8a)
- design/photos/ (all photos — view them to understand placement)
- main.py (add 3 new routes)

Your job: create 3 subpages with photography and content. These are independent pages —
no conflicts with other WS8 workstreams.

Key constraints:
- Use Tailwind CSS utility classes
- Full-bleed photography sections (inspired by Canva templates: dark overlay + white text)
- All text in Dutch
- Responsive: desktop + mobile native
- Photos must be optimized (WebP, multiple sizes via <picture> element)
```

---

## Pages

### `/over` — About

**Content:**
- Founder quote: _"Als raadslid had ik toegang tot alles — en toch klikte ik me een weg door honderden losse PDF's om één antwoord te vinden. Dat kon beter."_ — Dennis Tak, oud-raadslid Rotterdam
- Democratic ambition statement
- User quote placeholder: _"Dit spaarde me twee uur voorbereiding." — Raadslid, Rotterdam_

**Photography:**
- `dennis-gemeenteraad.webp` — beside founder quote, half-width on desktop, full-width on mobile
- `rotterdam-skyline.webp` — full-bleed section divider with dark overlay + white text

**Layout (inspired by Corporate Report Canva template):**
```
+--------------------------------------------------------------+
| [Nav]                                                        |
+--------------------------------------------------------------+
|                                                              |
|  Over NeoDemos                          (Instrument Serif)   |
|                                                              |
|  [Photo: Dennis at podium]   "Als raadslid had ik            |
|                               toegang tot alles..."          |
|                               — Dennis Tak                   |
|                                                              |
+==============================================================+
|  [Full-bleed: Rotterdam skyline, dark overlay]               |
|                                                              |
|  "Democratie werkt alleen als burgers en raadsleden          |
|   dezelfde informatie hebben."                               |
|                                                              |
+==============================================================+
|                                                              |
|  Wat gebruikers zeggen                                       |
|  "Dit spaarde me twee uur voorbereiding."                    |
|  — Raadslid, Rotterdam                                       |
|                                                              |
+--------------------------------------------------------------+
```

### `/technologie` — Technology

**Content:**
- EU sovereignty details (Hetzner hosting, Nebius inference)
- Local LLM options (Ollama, LM Studio, Open WebUI)
- Security checklist (6 items from design kit)
- "Uw data, uw AI" section
- Model independence: works with Claude, ChatGPT, Cursor, etc.

**Photography:**
- `rotterdam-erasmusbrug.webp` — hero background with dark overlay

**Layout (inspired by Earth Day Canva template — teal hero + content sections):**
```
+--------------------------------------------------------------+
| [Nav]                                                        |
+==============================================================+
|  [Full-bleed: Erasmusbrug at night, dark overlay]            |
|                                                              |
|  Technologie                            (Instrument Serif)   |
|  Uw data, uw AI                                              |
|                                                              |
+==============================================================+
|                                                              |
|  Volledig Europees                                           |
|  ┌──────────┐ ┌──────────┐ ┌──────────┐                    |
|  │ Hetzner  │ │ Nebius   │ │ Geen US  │                    |
|  │ Duitsland│ │ EU AI    │ │CLOUD Act │                    |
|  └──────────┘ └──────────┘ └──────────┘                    |
|                                                              |
|  Veiligheidsgaranties                                        |
|  ✓ Alleen publieke brondata                                  |
|  ✓ Geen tracking                                             |
|  ✓ Lokale AI mogelijk                                        |
|  ✓ Audit trail op elk antwoord                               |
|  ✓ EU AI Act conform                                         |
|  ✓ Uw vragen trainen geen AI                                 |
|                                                              |
|  Compatibel met                                              |
|  [Claude] [ChatGPT] [Cursor] [Ollama] [LM Studio]          |
|                                                              |
+--------------------------------------------------------------+
```

### `/methodologie` — Methodology

**Content:**
- 3-step "Hoe het werkt" (Zoeken → Analyseren → Verwijzen)
- Data sources and coverage
- Eval scores (precision, faithfulness, completeness)
- Known limitations
- How AI is used (transparency)

**Photography:**
- `kranten-stapel.webp` — in data sources section
- `onderzoek-board.webp` — in methodology section

**Layout (inspired by Charity Gala Canva template — numbered sections):**
```
+--------------------------------------------------------------+
| [Nav]                                                        |
+--------------------------------------------------------------+
|                                                              |
|  Methodologie                           (Instrument Serif)   |
|  Hoe NeoDemos werkt                                          |
|                                                              |
|  ┌─────────┐    ┌─────────┐    ┌─────────┐                 |
|  │ 1       │    │ 2       │    │ 3       │                 |
|  │ Zoeken  │ →  │Analyse- │ →  │Verwij-  │                 |
|  │         │    │ ren     │    │ zen     │                 |
|  └─────────┘    └─────────┘    └─────────┘                 |
|                                                              |
|  Onze bronnen                                                |
|  [Photo: stacked newspapers]                                 |
|  90.000+ documenten van de Rotterdamse gemeenteraad,        |
|  van 2002 tot heden. Moties, amendementen, notulen,         |
|  begrotingen, commissieverslagen.                            |
|                                                              |
|  Evaluatie                                                   |
|  Precisie: 0.99 | Getrouwheid: 4.8/5 | Volledigheid: 2.75/5|
|                                                              |
|  Beperkingen                                                 |
|  - AI kan fouten maken                                       |
|  - Niet alle documenten zijn even goed doorzoekbaar          |
|  - Oudere documenten (pre-2010) hebben lagere OCR-kwaliteit |
|                                                              |
+--------------------------------------------------------------+
```

---

## Photo Placement Summary

| Photo | Page | Section | CSS treatment |
|---|---|---|---|
| `dennis-gemeenteraad.webp` | `/over` | Founder quote | `object-fit: cover`, half-width desktop, full-width mobile, rounded corners |
| `rotterdam-skyline.webp` | `/over` | Section divider | Full-bleed, dark overlay (bg-black/60), white text on top |
| `rotterdam-erasmusbrug.webp` | `/technologie` | Hero | Full-bleed, dark overlay (bg-black/50), white heading on top |
| `kranten-stapel.webp` | `/methodologie` | Data sources | Inline image, rounded corners, max-width 400px |
| `onderzoek-board.webp` | `/methodologie` | How it works | Inline image, rounded corners, max-width 400px |

**Photo optimization:** Use `<picture>` element with srcset for responsive images:
```html
<picture>
  <source srcset="/static/images/photos/rotterdam-erasmusbrug-1920.webp" media="(min-width: 1024px)">
  <source srcset="/static/images/photos/rotterdam-erasmusbrug-960.webp" media="(min-width: 768px)">
  <img src="/static/images/photos/rotterdam-erasmusbrug-480.webp" alt="Erasmusbrug Rotterdam bij nacht">
</picture>
```

---

## Files to create

| File | Purpose |
|---|---|
| `templates/over.html` | About page |
| `templates/technologie.html` | Technology page |
| `templates/methodologie.html` | Methodology page |

## Files to modify

| File | Change |
|---|---|
| `main.py` | Add 3 routes: GET `/over`, `/technologie`, `/methodologie` |

**Note:** This workstream only adds to `main.py` (new routes). It does NOT modify existing routes. No conflicts with WS8b or WS8c.

---

## Acceptance Criteria

- [ ] `/over` renders with founder photo and quote
- [ ] `/technologie` renders with Erasmusbrug hero and security checklist
- [ ] `/methodologie` renders with 3-step process and eval scores
- [ ] Full-bleed photography sections work on desktop and mobile
- [ ] Photos load in appropriate sizes per viewport (picture/srcset)
- [ ] All text in Dutch
- [ ] Pages extend base.html and use Tailwind classes
- [ ] Routes added to main.py without breaking existing routes
- [ ] Mobile: content stacks single-column, photos full-width
