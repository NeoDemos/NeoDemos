# WS5b — Multi-Portal Connectors (Search-Only First)

> **Priority:** 6 (parity feature; expands TAM beyond Rotterdam)
> **Status:** `deferred — do not start until WS5a has 14 days of clean nightly runs`
> **Owner:** `unassigned`
> **Target release:** v0.2.1 (NOT v0.2.0)
> **Master plan section:** [V0_2_BEAT_MAAT_PLAN.md §7.2](../architecture/V0_2_BEAT_MAAT_PLAN.md)

## TL;DR
Stop being Rotterdam-only on the source side. Refactor `pipeline/scraper.py` into one adapter per portal (iBabs, Notubiz, GO GemeenteOplossingen, plus an ORI fallback that uses `api.openraadsinformatie.nl`'s Elasticsearch endpoint to cover any gemeente). Land **search-only mode** for 5 ORI-fallback gemeenten on day 1 (Apeldoorn, Zoetermeer, Maastricht, Enschede, Bodegraven). The user's explicit instruction: **search-only first, AI tooling later** — do NOT enable GraphRAG/financial/journey for new gemeenten in v0.2.1.

## Dependencies
- **WS5a stable for 14 consecutive nightly runs** (hard gate)
- WS4 tool registry must support `mode='search_only'` flag per tool per gemeente
- Memory to read first:
  - [project_pipeline_hardening.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_pipeline_hardening.md)
  - [project_embedding_process.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_embedding_process.md)

## Cold-start prompt

> You are picking up Workstream 5b (Multi-Portal Connectors) of NeoDemos v0.2.1. Self-contained handoff at `docs/handoffs/WS5b_MULTI_PORTAL.md`.
>
> **DO NOT START THIS WORKSTREAM until WS5a has reported 14 consecutive clean nightly runs.** Verify by querying the `pipeline_runs` table or checking the `/admin/pipeline` dashboard. If you're not sure, ask the owner.
>
> Read in order: (1) this handoff, (2) `docs/handoffs/WS5a_NIGHTLY_PIPELINE.md` to understand the job graph you're plugging into, (3) `pipeline/scraper.py` (current Rotterdam iBabs logic), (4) the ORI integration guide at `docs/investigations/ORI_INTEGRATION_GUIDE.md`.
>
> Your job: refactor scraping into per-portal adapters, add a `gemeente` column to all tables, ship the ORI fallback adapter that gives us search-only coverage for 5 new gemeenten on day 1, and ensure the new gemeenten are clearly badged "Beperkte modus" in the UI and via tool responses. The user's explicit instruction: **search-only first, AI tooling later**. New gemeenten do NOT get GraphRAG, financial line-items, or document journeys in v0.2.1 — those tools must return `{mode: 'search_only', available: false, gemeente: '...'}`.
>
> Honor the project house rules in `docs/handoffs/README.md`. Especially the no-scope-creep rule: do NOT promote any gemeente to full mode in this workstream — that's v0.3.0 work.

## Files to read first
- [`docs/handoffs/WS5a_NIGHTLY_PIPELINE.md`](WS5a_NIGHTLY_PIPELINE.md)
- [`pipeline/scraper.py`](../../pipeline/scraper.py) — current Rotterdam iBabs logic
- [`docs/investigations/ORI_INTEGRATION_GUIDE.md`](../investigations/ORI_INTEGRATION_GUIDE.md)
- [`docs/investigations/ROTTERDAM_RIS_ASSESSMENT.md`](../investigations/ROTTERDAM_RIS_ASSESSMENT.md)
- [`mcp_server_v3.py`](../../mcp_server_v3.py) — to understand which tools need `mode='search_only'` handling
- External: `api.openraadsinformatie.nl/v1/elastic/` ORI Elasticsearch API ([API docs](https://github.com/openstate/open-raadsinformatie/blob/master/API-docs.md))

## Build tasks

### Source adapter refactor (~3 days)

- [ ] **Create `pipeline/sources/`** package
  - [ ] `pipeline/sources/__init__.py` — exports `SourceAdapter` Protocol + `get_adapter(portal_name)` factory
  - [ ] `pipeline/sources/base.py` — `SourceAdapter` Protocol:
    ```python
    class SourceAdapter(Protocol):
        portal_name: str             # "ibabs" | "notubiz" | "go" | "ori_fallback"
        gemeente: str

        def list_meetings(self, since: date) -> Iterator[MeetingRef]: ...
        def fetch_documents(self, meeting_id: str) -> list[DocumentRef]: ...
        def fetch_webcast(self, meeting_id: str) -> WebcastRef | None: ...
    ```
  - [ ] `pipeline/sources/ibabs.py` — refactor existing Rotterdam logic from [`pipeline/scraper.py`](../../pipeline/scraper.py); generalize the iBabs URL templates so the gemeente is a parameter, not hard-coded
  - [ ] `pipeline/sources/ori_fallback.py` — queries `api.openraadsinformatie.nl/v1/elastic/ori_*` with a `gemeente_naam` filter; maps the Popolo schema to our `documents` table shape
  - [ ] `pipeline/sources/notubiz.py` — **stub only in v0.2.1**; raises `NotImplementedError`. Real implementation lands in v0.3+ when a customer asks
  - [ ] `pipeline/sources/go.py` — same: stub
- [ ] **Update WS5a's `01_discover_meetings.py`** to loop over all enabled tenants in `data/tenants/*/config.yml` and call the right adapter for each

### Multi-tenant schema (~2 days)

- [ ] **Alembic migration** adding `gemeente TEXT NOT NULL DEFAULT 'rotterdam'` to:
  - `documents`, `chunks`, `meetings`, `agenda_items`
  - `kg_relationships`, `kg_entities`, `chunk_entities`
  - `committee_transcripts_staging`
  - `pipeline_runs` (already in WS5a, but confirm)
  - `mcp_audit_log` (from WS4)
  - `financial_lines` (from WS2)
  - `document_journeys` (from WS3)
- [ ] Backfill all existing rows with `'rotterdam'`
- [ ] **Single Qdrant collection with mandatory `gemeente` payload filter** (NOT per-gemeente collections — simpler reranker pooling)
- [ ] Update [`services/rag_service.py`](../../services/rag_service.py) so every retrieve call requires a `gemeente` parameter; default resolved from request host header (`zoetermeer.neodemos.nl` → `zoetermeer`)
- [ ] FastAPI middleware in [`main.py`](../../main.py) resolves `host → gemeente` and injects into request state

### Tenant config (~1 day)

- [ ] **`data/tenants/<gemeente>/config.yml`** structure:
  ```yaml
  gemeente: zoetermeer
  display_name: Gemeente Zoetermeer
  portal: ori_fallback                    # or "ibabs" | "notubiz" | "go"
  portal_config:
    ori_search_filter: "Zoetermeer"
  mode: search_only                       # or "full"
  date_from: "2018-01-01"
  date_to: null                           # null = ongoing
  political_dictionary: data/tenants/zoetermeer/political_dictionary.json
  parties: data/tenants/zoetermeer/parties.json
  theme:
    primary_color: "#003876"
    logo: data/tenants/zoetermeer/logo.svg
  ```
- [ ] Move existing Rotterdam config to `data/tenants/rotterdam/config.yml` (mode: `full`)
- [ ] Day-1 search-only configs: Apeldoorn, Zoetermeer, Maastricht, Enschede, Bodegraven

### Search-only mode enforcement (~1.5 days)

- [ ] **Tool registry** (WS4) gains a `requires_mode: list[Literal['full', 'search_only']]` field per tool
- [ ] Search tools (`zoek_raadshistorie`, `zoek_uitspraken`, `lijst_vergaderingen`, `lees_fragment`, `haal_vergadering_op`, `zoek_gerelateerd`, `scan_breed`, `tijdlijn_besluitvorming` flat-mode) → `requires_mode: ['full', 'search_only']` (work for both)
- [ ] AI/structured tools (`traceer_motie`, `vergelijk_partijen`, `vraag_begrotingsregel`, `vergelijk_begrotingsjaren`, `traceer_document`, `analyseer_agendapunt`, `vat_document_samen`) → `requires_mode: ['full']`
- [ ] When a `search_only` gemeente call hits a `full`-only tool, return:
  ```json
  {
    "error": "tool_not_available_in_search_only_mode",
    "gemeente": "zoetermeer",
    "available_in": ["rotterdam"],
    "alternative_tools": ["zoek_raadshistorie"],
    "message": "Deze functie is nog niet beschikbaar voor Zoetermeer (beperkte modus)."
  }
  ```
- [ ] **`get_neodemos_context()`** primer (from WS4) lists each gemeente's `mode` and which tools are available

### UI badging (~1 day)

- [ ] In [`templates/search.html`](../../templates/search.html), header shows `gemeente.display_name` and a yellow "Beperkte modus" badge if `mode == 'search_only'`
- [ ] Per-result snippet shows the gemeente name when not Rotterdam (so users don't get confused in cross-gemeente results)
- [ ] Footer always shows current `gemeente` for transparency

### Initial backfill (~1 day per gemeente, can run in parallel)

- [ ] For each of the 5 day-1 gemeenten, run the WS5a 7-step pipeline once for the historical window (e.g. 2018-01-01 → today). Steps 06 (KG enrich) and 07 (promote) are skipped — search-only mode goes from staging directly to the searchable collection without enrichment.
- [ ] Verify each gemeente: `SELECT COUNT(*) FROM documents WHERE gemeente='X'`; `SELECT COUNT(*) FROM chunks WHERE gemeente='X'`
- [ ] Test search end-to-end on each gemeente subdomain

### BAG location skeleton per gemeente (~0.5 day total) *(added 2026-04-11)*

WS1 already shipped the BAG-native location layer schema with the Rotterdam dataset. WS5b only needs to **rerun the import for each new gemeente** — no schema work, no canonicalization, no street-name disambiguation, because WS1 locked the design constraints (BAG `openbare_ruimte` IDs as PK, mandatory `gemeente` attribute, `LOCATED_IN` edge `level` attribute).

- [ ] **Inherited design constraint check** — read [WS1_GRAPHRAG.md §Phase A "BAG-based location hierarchy"](WS1_GRAPHRAG.md) before starting; do NOT re-key Location nodes by name; do NOT add per-gemeente schema fields. If the constraints feel wrong, escalate — fixing them in WS5b means rewriting WS1's location KG.
- [ ] **For each of the 5 day-1 gemeenten**, run `scripts/import_bag_locations.py --gemeente <name>` (the script WS1 ships) to fetch the relevant PDOK BAG subset + CBS Wijk- en Buurtkaart slice and emit `LOCATED_IN` edges into `kg_relationships`. Estimated volume per gemeente: a few thousand street nodes + buurt/wijk/gemeente edges. Trivial compared to the document backfill.
- [ ] **Verify**: `SELECT COUNT(*) FROM kg_entities WHERE type='Location' AND gemeente='X'` returns expected order of magnitude per gemeente; `SELECT COUNT(*) FROM kg_relationships WHERE relationship='LOCATED_IN' AND ...` matches.
- [ ] **Per-gemeente sub-municipal level** — Apeldoorn uses wijken, Zoetermeer uses wijken, Maastricht uses buurten, Enschede uses stadsdelen + wijken, Bodegraven-Reeuwijk has no formal sub-municipal level. The `level` attribute on `LOCATED_IN` edges absorbs this variation without code changes; just verify each tenant's hierarchy looks sensible vs. the gemeente's own published structure. Document any edge cases in `data/tenants/<gemeente>/notes.md` for future reference.
- [ ] **Search-only consequence**: even though the GraphRAG MCP tools (`traceer_motie`, `vergelijk_partijen`) stay disabled for these gemeenten, the location KG itself is *available* — meaning future v0.3.0 promotion of any of these gemeenten to full mode is location-skeleton-ready out of the box.

### DNS / Caddy (~0.5 day)

- [ ] Add subdomain wildcards to Caddy: `*.neodemos.nl` → same upstream as `neodemos.nl`
- [ ] DNS records for `apeldoorn.neodemos.nl`, `zoetermeer.neodemos.nl`, `maastricht.neodemos.nl`, `enschede.neodemos.nl`, `bodegraven.neodemos.nl`

## Acceptance criteria

- [ ] WS5a has reported 14 consecutive clean nightly runs (verify before starting)
- [ ] `pipeline/sources/` package exists with `base.py`, `ibabs.py` (refactored), `ori_fallback.py` (working)
- [ ] `notubiz.py` and `go.py` stubs raise `NotImplementedError` with TODO link
- [ ] `gemeente` column added to all listed tables and backfilled with `'rotterdam'`
- [ ] Single Qdrant collection with mandatory `gemeente` payload filter; reranker still works
- [ ] FastAPI middleware resolves host → gemeente
- [ ] `data/tenants/` populated for `rotterdam` (full) + 5 search-only gemeenten
- [ ] `requires_mode` enforced in WS4 registry; full-only tools return clean error for search-only gemeenten
- [ ] Initial backfill complete for all 5 gemeenten via ORI fallback (≥10K documents each)
- [ ] DNS + Caddy serving `*.neodemos.nl`
- [ ] UI shows "Beperkte modus" badge on search-only gemeenten
- [ ] `get_neodemos_context()` accurately reports each gemeente's mode and capabilities
- [ ] **BAG location skeleton imported for all 5 search-only gemeenten** *(added 2026-04-11)* — `kg_entities` contains Location nodes keyed by BAG `openbare_ruimte` IDs with the correct `gemeente` attribute, and `kg_relationships` contains the matching `LOCATED_IN` edges

## Eval gate

| Metric | Target |
|---|---|
| Search latency p50 on search-only gemeenten | < 800ms (matching Rotterdam) |
| Documents per gemeente after backfill | ≥ 10K |
| Cross-gemeente leakage (Rotterdam user retrieves Zoetermeer chunks) | **0** (verified by audit test) |
| Tool availability matrix matches `get_neodemos_context()` | 100% accurate |
| Days WS5a remained clean while WS5b backfill ran | 0 regression |

## Risks specific to this workstream

| Risk | Mitigation |
|---|---|
| ORI Elasticsearch endpoint shape changes | Pin against current schema; smoke test daily; fall back to last known good cache |
| Backfill blocks Qdrant for hours | Run via WS5a advisory lock; prefer per-gemeente staging collections during backfill, then merge |
| `gemeente` column migration is risky on 1.6M chunks | Run on staging first; full backup before; downtime window booked |
| Cross-gemeente leakage in retrieval | Layer 3 of WS4 defense-in-depth + dedicated test corpus that asserts zero leakage |
| Notubiz/GO stub not implemented stalls customer asks | Document NotImplementedError with link to GitHub issue tracker; promote to v0.3+ when first customer needs it |
| User badging confusion ("why are some tools missing?") | `get_neodemos_context()` primer + UI banner explain it once per session |
| Subdomain TLS provisioning fails | Caddy auto-provisions Let's Encrypt; test rollout one subdomain at a time |

## Future work (do NOT do in this workstream)
- Native Notubiz adapter — **v0.3+** when first customer needs it
- Native GO adapter — **v0.3+**
- Promoting any of the 5 gemeenten from search-only → full mode — **v0.3.0**
- `vergelijk_gemeenten` cross-gemeente comparison MCP tool — **v0.4.0**
- Per-gemeente theming beyond colors/logo — **v0.4+**
- Multi-language UI — **v0.5+**

## Outcome
*To be filled in when shipped. Include: documents per gemeente, ORI fallback edge cases, Notubiz/GO stub linked issues, leakage test results.*
