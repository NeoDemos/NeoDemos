---
name: Frontend Redesign Design Decisions
description: WS8 frontend redesign — color palette (Canva dark green+beige), Vite+Tailwind v4 build, sessionStorage for GDPR, parallel workstream structure
type: project
---

## Color Direction
Follow Canva templates: **dark green + beige/cream** palette (not the WS8 spec's civic blue + gold). Orange accent from logo SVG (#ff751f) for CTAs. Instrument Serif headings + Inter body.

**Why:** Dennis chose the Canva template aesthetic over the original WS8 spec palette. The three templates (Corporate Report, Charity Gala, Earth Day) all use dark green + warm neutrals, giving an editorial/civic feel.

**How to apply:** Extract exact hex values from the Canva template SVGs for the token system. Use orange sparingly for primary CTAs only.

## Build Tooling
Upgraded from "no build step" to **Vite + Tailwind CSS v4**. Dennis explicitly requested future-proofing and QoL.

**Why:** Original "no build step" constraint was for deployment simplicity, but Dennis values maintainability and easier editing more.

**How to apply:** Dockerfile gets `RUN npm ci && npm run build`, serve from `static/dist/`.

## GDPR / Cookie Consent
Use **sessionStorage** for search counter (Option A) — no cookie consent banner needed. Only essential cookies: session auth + CSRF.

**Why:** Dennis said "I hate these cookie messages that fill your entire screen." sessionStorage avoids the legal requirement entirely since it's not a cookie and doesn't persist.

**How to apply:** `sessionStorage.setItem('neodemos_searches', count)` instead of a cookie. No consent UI needed.

## Calendar Access
Decision pending: make calendar public (currently login-required)?

## Workstream Structure
5 parallel workstreams: WS8a (foundation) → WS8b (landing) + WS8c (calendar) + WS8d (subpages) in parallel → WS8e (polish/convergence). Handoff .md files for each to enable multi-agent execution.