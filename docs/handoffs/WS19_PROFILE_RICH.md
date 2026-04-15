# WS19 — Profile Enrichment: Partij + Commissies Re-introduction

## TL;DR

On 2026-04-15 we removed the Politieke Partij picker + Commissies picker from /settings because they were client-only (localStorage) and not data-backed. They need to come back as proper profile fields, stored per-user, and wired into system-prompt context — but only after we decide on the taxonomy (current partij list is stale, commissie list is Rotterdam-only). This workstream reintroduces them properly.

## Status

`available` — v0.2.x, low priority (Erik's feedback 2026-04-15 is that profile customization is "opsmuk" compared to corpus reliability)

## Owner

unassigned

## Dependencies

- WS18 (pricing analysis) — defer until usage tracking is in so we know whether anyone uses profile context
- WS13 (multi-gemeente) — commissie list is city-specific, needs tenant-aware config

## Cold-start prompt

```
You are picking up WS19. Dennis wants partij + commissies back on /settings,
but as server-side profile fields that feed into LLM context. Read this file,
templates/settings.html (to see the old shape, removed 2026-04-15),
services/web_intelligence.py:_build_system_blocks (where partij was injected
pre-removal), and services/subscriptions.py (current TIERS). Your job: design
the data model + UI + injection path, then ship it. The old `users.party`
column already exists — reuse it.
```

## Files to read first

- [templates/settings.html](../../templates/settings.html) — current state (partij + commissies removed 2026-04-15)
- Git log for `templates/settings.html` around 2026-04-15 — see the removed HTML blocks for the old UI
- [services/web_intelligence.py](../../services/web_intelligence.py) `_build_system_blocks` — how partij was injected (currently set to None in routes/api.py per WS18 spec)
- [routes/api.py](../../routes/api.py) — `partij = None` hardcode at ~line 356 (temporary)

## Scope

### Partij
- Dropdown on /settings, server-side persisted (column `users.party` already exists)
- Current Rotterdam partijen (2022-2026 raad): VVD, D66, DENK, Leefbaar Rotterdam, GroenLinks-PvdA, PvdA, CDA, CU, PvdD, SP, NIDA, Volt — live from `get_neodemos_context()` coalition-history; do not hardcode
- Restore injection in `_build_system_blocks(partij=...)` — the code path is dormant, just reverse the hardcoded `partij = None` in routes/api.py

### Commissies
- Multi-select on /settings
- Server-side persisted. NEW: `users.followed_commissies TEXT[]` column via Alembic
- Rotterdam list (2022-2026): Bestuur/Organisatie/Financiën/Veiligheid, Mobiliteit/Haven/Economie/Klimaat, Bouwen/Wonen/Buitenruimte, Zorg/Welzijn/Cultuur/Sport, WIOS (Werk/Inkomen/Onderwijs/Samenleven/Schuldhulp/NPRZ), Welzijn/Wijken/Democratie, Actualiteitenraad, Gemeenteraad
- Inject into system prompt alongside topic_description: "De gebruiker volgt vooral: [Commissie Bouwen & Wonen, Commissie Mobiliteit]"

### Integration with topic_description (shipped 2026-04-15)
- topic_description is free-form text; partij + commissies are structured
- Merge all three in one "user context" block in `_build_system_blocks`:
  ```
  De gebruiker is lid van {partij}.
  Volgt: {commissies joined by ,}.
  Richt zich op: {topic_description}.
  ```
- Inject only what's set; if all three are None, no block

### MCP injection
- Currently MCP has `# WS-pricing TODO` stub for topic injection (shipped 2026-04-15, not wired)
- Solution path: ASGI ContextVar middleware reading OAuth token → user dict → (partij, commissies, topic_description) → concatenated into tool-level context primer
- Alternative: FastMCP `Context` parameter with `load_access_token`, requires per-tool opt-in

## Build tasks

1. Alembic migration — `users.followed_commissies TEXT[] NULL` (lock_timeout=3s)
2. Restore commissie + partij forms on /settings as server-rendered `<select>` + `<input type=checkbox>` groups
3. Add POST routes: `/settings/party`, `/settings/commissies` in routes/pages.py
4. Extend `_build_system_blocks` — unified user-context block (partij + commissies + topic_description)
5. Reverse the `partij = None` hardcode in routes/api.py; read `user.get("party")` again
6. Implement MCP injection path (pick one of the two options above, implement, remove `# WS-pricing TODO` stub)
7. Manual eval: same question asked with and without partij context — does the answer differ in a way Dennis finds useful?

## Acceptance criteria

- [ ] partij + commissies persist across sessions (not localStorage)
- [ ] `_build_system_blocks` injects all three profile fields when set
- [ ] MCP injection works for authenticated tool calls (remove TODO stub)
- [ ] Manual eval on 10 queries: partij-aware answers measurably different (judge: Dennis, not LLM)
- [ ] Settings page smoke tested on staging with 3 personas (none set / party only / all three set)

## Eval gate

| Metric | Target |
|---|---|
| Session retention | partij setters have ≥ 2× longer session count than non-setters |
| Prompt differential | measurable context delta (not just cosmetic copy change) |
| MCP quality parity | topic_description injection does not degrade MCP answer quality on the 20-Q benchmark |

## Future work

- Party voting pattern context — "Je partij stemde in 87% van de moties mee met de coalitie" as an automatic context signal
- Fractie-sharing: shared corpus + shared party/commissie profile for raadsleden on one account

## Related

- [WS18](WS18_PRICING_ANALYSIS.md) — pricing analysis (precursor; profile usage data feeds pricing decisions)
- [feedback_erik_priorities](../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/feedback_erik_priorities.md) — primary user calls these "opsmuk"; confirm need before rebuilding
