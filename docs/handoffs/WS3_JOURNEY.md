# WS3 — Document Journey Timelines

> **Priority:** 3 (the causal-chain feature MAAT cannot deliver)
> **Status:** `not started`
> **Owner:** `unassigned`
> **Target release:** v0.2.0 (backend + MCP tool); UI deferred to v0.2.1
> **Master plan section:** [V0_2_BEAT_MAAT_PLAN.md §5](../architecture/V0_2_BEAT_MAAT_PLAN.md)

## TL;DR
MAAT's "timeline" is a flat chronological list. We ship the *causal chain*: a document arrives in the system → discussed in committee → debated in council → moties/amendementen filed → vote → outcome → linked webcast moments. This workstream computes that journey from existing data + WS1's new motie↔notulen edges, exposes it via `traceer_document` MCP tool in v0.2.0, and adds the UI in v0.2.1.

## Dependencies
- **WS1 phase A finished** — needs `kg_relationships` enriched, especially the cross-document motie↔notulen vote linking
- Soft dependency on **WS5a** (advisory lock) if running large backfills concurrent with nightly
- Memory to read first:
  - [project_motie_signatories.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_motie_signatories.md)
  - [project_notulen_vs_annotaties.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_notulen_vs_annotaties.md)

## Cold-start prompt

> You are picking up Workstream 3 (Document Journey Timelines) of NeoDemos v0.2.0. Self-contained handoff at `docs/handoffs/WS3_JOURNEY.md`.
>
> Read in order: (1) this handoff, (2) `docs/handoffs/WS1_GRAPHRAG.md` to understand what motie↔notulen edges WS1 produces, (3) `mcp_server_v3.py` (especially `tijdlijn_besluitvorming` around line 614 — the existing flat timeline), (4) `pipeline/scraper.py` to understand `meetings`, `agenda_items`, `documents` schema.
>
> Your job: build the `document_journeys` Postgres view that reconstructs the causal chain from documents/meetings/agenda_items/kg_relationships, ship a `traceer_document(document_id)` MCP tool that returns the journey JSON, and stop there for v0.2.0. The UI (`/journey/{id}` route + `templates/journey.html`) is deferred to v0.2.1 — do **not** build it now.
>
> Critical: this workstream blocks until WS1 phase A finishes (cross-document motie↔notulen linking is required). If WS1 is not done, work on the schema design and the view SQL with mock data, then wait for WS1 to backfill the edges.

## Files to read first
- [`docs/handoffs/WS1_GRAPHRAG.md`](WS1_GRAPHRAG.md) — for the cross-document edge schema you'll consume
- [`mcp_server_v3.py`](../../mcp_server_v3.py) — `tijdlijn_besluitvorming` tool around line 614 (the *flat* version we're improving on)
- [`pipeline/scraper.py`](../../pipeline/scraper.py) — meeting/agenda/document schema relationships
- [`pipeline/staging_ingestor.py`](../../pipeline/staging_ingestor.py) — committee transcript schema with timestamps
- Postgres: `documents`, `meetings`, `agenda_items`, `kg_relationships`, `committee_transcripts_staging`

## Build tasks

### Schema (~1 day)

- [ ] **Alembic migration creating `document_journeys` materialized view** (refresh nightly via WS5a):
  ```sql
  CREATE MATERIALIZED VIEW document_journeys AS
  SELECT
    root.id              AS root_document_id,
    root.gemeente,
    root.title,
    jsonb_agg(
      jsonb_build_object(
        'type', event.type,
        'date', event.date,
        'meeting_id', event.meeting_id,
        'agenda_item_id', event.agenda_item_id,
        'motie_id', event.motie_id,
        'amendement_id', event.amendement_id,
        'outcome', event.outcome,
        'voor', event.voor,
        'tegen', event.tegen,
        'per_partij', event.per_partij,
        'webcast_url', event.webcast_url,
        'start_seconds', event.start_seconds,
        'citation_chain', event.citation_chain
      ) ORDER BY event.date
    ) AS events
  FROM documents root
  JOIN LATERAL (
    -- arrival event
    SELECT 'arrival' AS type, root.created_at AS date, ...
    UNION ALL
    -- committee discussions (via agenda_items.committee_meeting_id)
    SELECT 'committee' AS type, ai.date, ai.meeting_id, ai.id AS agenda_item_id, ...
    UNION ALL
    -- council debates
    SELECT 'council' AS type, ...
    UNION ALL
    -- moties filed (via kg_relationships DIENT_IN where target = root)
    SELECT 'motie' AS type, m.date, m.id AS motie_id, m.indieners, ...
    UNION ALL
    -- amendementen
    SELECT 'amendement' AS type, ...
    UNION ALL
    -- votes (via STEMT_VOOR/STEMT_TEGEN aggregated per motie)
    SELECT 'vote' AS type, v.date, v.uitkomst, v.voor, v.tegen, ...
    UNION ALL
    -- webcast moments (via committee_transcripts_staging where speaker discusses doc)
    SELECT 'webcast' AS type, t.start_date AS date, t.webcast_url, t.start_seconds, ...
  ) event ON TRUE
  GROUP BY root.id, root.gemeente, root.title;
  CREATE INDEX ON document_journeys (root_document_id);
  CREATE INDEX ON document_journeys (gemeente);
  ```
- [ ] If a materialized view becomes too slow to refresh, fall back to a `services/journey_service.py` function that builds the journey on demand and caches in `journey_cache (document_id, journey_json, computed_at)`.

### Service & MCP tool (~2 days)

- [ ] **`services/journey_service.py`** — new file:
  - `build_journey(root_document_id: str, gemeente: str = 'rotterdam') -> dict` — query the view (or compute on demand)
  - `events_in_window(root_document_id, start_date, end_date) -> list[Event]` — filter helper
  - `verify_journey(root_document_id) -> ValidationReport` — used by tests
- [ ] **MCP tool `traceer_document(document_id: str) -> dict`** in [`mcp_server_v3.py`](../../mcp_server_v3.py)
  - Returns the journey JSON, ordered chronologically
  - Each event includes citation IDs the LLM can pass to `lees_fragment`
  - Tool description for AI: "Use this when the user asks how a document moved through the council process, what moties were filed against it, how it was voted, or wants a chronological case file. Do NOT use for general date filtering — use `lijst_vergaderingen` for that."
- [ ] **Replace `tijdlijn_besluitvorming` internals** ([`mcp_server_v3.py:614`](../../mcp_server_v3.py#L614)) — the existing flat timeline becomes a thin wrapper around `traceer_document` for backward compatibility, but new clients are pointed at `traceer_document`.
- [ ] **Coordinate with WS1**: confirm that the `kg_relationships` edges populated by WS1 phase A include `(motie_id, notulen_chunk_id, relationship='DISCUSSED_IN' | 'VOTED_IN')`. If they don't, this workstream is blocked until they do.

### Validation (~1 day)

- [ ] **20-journey hand-validation set** — pick 20 documents with known-good journeys (e.g. Feyenoord stadion raadsvoorstel, Boijmans depot raadsvoorstel, recent warmtenetten moties). For each, manually list expected events and assert `traceer_document` captures them.
- [ ] **Acceptance**: 100% of expected events captured, ≤2 false positives across all 20 journeys.

## Acceptance criteria

- [ ] `document_journeys` view (or `journey_cache` + service) shipped via Alembic
- [ ] `services/journey_service.py` exists with `build_journey`, `events_in_window`, `verify_journey`
- [ ] `traceer_document` MCP tool returns structured journey JSON for any document_id
- [ ] `tijdlijn_besluitvorming` re-implemented as a thin wrapper (or marked deprecated)
- [ ] 20-journey hand-validation passes (100% expected events, ≤2 false positives total)
- [ ] Tool registered in WS4 registry with AI-consumption description
- [ ] Refresh strategy decided (materialized view + nightly refresh via WS5a, OR on-demand cache)
- [ ] **UI work explicitly deferred to v0.2.1** — no `templates/journey.html` in this workstream

## Eval gate

| Metric | Target |
|---|---|
| Hand-validated journey precision | 100% expected events captured |
| Hand-validated journey false positives | ≤ 2 across 20 journeys |
| `traceer_document` p95 latency | < 800ms (cached) / < 3s (uncached) |
| View refresh time (if materialized) | < 5 min nightly |

## Risks specific to this workstream

| Risk | Mitigation |
|---|---|
| WS1 cross-doc edges not populated yet | Build view + service against mock edges; merge once WS1 phase A lands; coordinate Slack/email update with WS1 owner |
| Materialized view refresh too slow | Fall back to on-demand `journey_cache` table populated lazily |
| Some documents have no clean "arrival" event (legacy data) | Default arrival = `min(meeting_date for documents.id IN agenda)`; flag for manual review |
| Webcast events depend on Royalcast scraping which can fail | Webcast events are optional; absence is not a journey error |
| Same document attached to multiple journeys (e.g. raadsbrief referenced in multiple raadsvoorstellen) | Journey is rooted at a single `document_id`; cross-references are surfaced as `related_documents`, not as separate journey roots |

## Future work (do NOT do in this workstream)
- `/journey/{id}` UI route + `templates/journey.html` — **WS3 v0.2.1**
- Side-panel `vraag_aan_dossier` constrained to journey events — needs WS6 dossier work, defer
- Press / news event integration (out of scope; future WS)
- Cross-municipality journey comparison — defer to v0.4

## Outcome
*To be filled in when shipped. Include: refresh strategy chosen, hand-validation results, edge cases hit.*
