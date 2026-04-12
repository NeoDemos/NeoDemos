# WS3 — Document Journey Timelines

> **Priority:** 3 (the causal-chain feature MAAT cannot deliver)
> **Status:** `not started`
> **Owner:** `unassigned`
> **Target release:** v0.2.0 (backend + MCP tool); UI deferred to v0.2.1
> **Master plan section:** [V0_2_BEAT_MAAT_PLAN.md §5](../architecture/V0_2_BEAT_MAAT_PLAN.md)

## TL;DR
MAAT's "timeline" is a flat chronological list. We ship the *causal chain*: a document arrives in the system → discussed in committee → debated in council ��� moties/amendementen filed → vote → outcome → afdoening → linked webcast moments. This workstream computes that journey from existing data + WS1's new motie↔notulen edges, exposes it via `traceer_document` MCP tool in v0.2.0, and adds the UI in v0.2.1.

The journey is NOT a flat timeline. Dutch municipal decision-making is an iterative, branching process — documents bounce between commissie and raad, get revised ("herzien voorstel"), spawn amendementen that mutate them, and trigger afdoeningscycli that can run for months. The schema must model this.

## Dependencies
- **WS1 phase A finished** — needs `kg_relationships` enriched, especially the cross-document motie↔notulen vote linking (DISCUSSED_IN / VOTED_IN edges)
- Soft dependency on **WS5a** (advisory lock) if running large backfills concurrent with nightly
- Memory to read first:
  - [project_motie_signatories.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_motie_signatories.md)
  - [project_notulen_vs_annotaties.md](../../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_notulen_vs_annotaties.md)

## Cold-start prompt

> You are picking up Workstream 3 (Document Journey Timelines) of NeoDemos v0.2.0. Self-contained handoff at `docs/handoffs/WS3_JOURNEY.md`.
>
> Read in order: (1) this handoff, (2) `docs/handoffs/WS1_GRAPHRAG.md` to understand what motie↔notulen edges WS1 produces, (3) `mcp_server_v3.py` (especially `tijdlijn_besluitvorming` around line 614 — the existing flat timeline), (4) `pipeline/scraper.py` to understand `meetings`, `agenda_items`, `documents` schema.
>
> **CRITICAL — do the research phase first.** Before writing any code, research the formal Dutch municipal decision-making process (Gemeentewet, VNG model-Reglement van Orde, Rotterdam's own Reglement van Orde). The journey schema must be robust against the full range of procedural paths, not just the common happy-path. See the "Phase 0 — Process research" section below.
>
> Your job is then to: build the journey edge types into `kg_relationships`, create `services/journey_service.py`, ship a `traceer_document(document_id)` MCP tool, and stop there for v0.2.0. The UI (`/journey/{id}` route + `templates/journey.html`) is deferred to v0.2.1.
>
> Critical: this workstream blocks until WS1 phase A finishes (cross-document motie↔notulen linking is required). If WS1 is not done, work on Phase 0 (research) and the schema design with mock data, then wait for WS1 to backfill the edges.

## Files to read first
- [`docs/handoffs/WS1_GRAPHRAG.md`](WS1_GRAPHRAG.md) — for the cross-document edge schema you'll consume
- [`mcp_server_v3.py`](../../mcp_server_v3.py) — `tijdlijn_besluitvorming` tool around line 614 (the *flat* version we're improving on)
- [`pipeline/scraper.py`](../../pipeline/scraper.py) — meeting/agenda/document schema relationships
- [`pipeline/staging_ingestor.py`](../../pipeline/staging_ingestor.py) — committee transcript schema with timestamps
- Postgres: `documents`, `meetings`, `agenda_items`, `kg_relationships`, `committee_transcripts_staging`

## Build tasks

### Phase 0 — Process research (MUST DO FIRST, ~2 days)

The Dutch municipal decision-making process has formal procedural rules. Building a journey model without understanding these rules will produce a schema that breaks on edge cases. **Do this research before writing any code.**

- [ ] **Research the Gemeentewet** (specifically Titel III — De raad, and Titel IV — Het college). Key articles:
  - Art. 147-155: raadsbesluiten, recht van initiatief, recht van amendement
  - Art. 160-169: bevoegdheden college, verantwoording aan de raad
  - Identify the formal lifecycle of a raadsvoorstel, motie, amendement, and initiatiefvoorstel as prescribed by law.

- [ ] **Research the VNG model-Reglement van Orde** (the national template that most Dutch municipalities base their procedural rules on). Source: VNG website or ask.vng.nl. Key chapters:
  - Agenderingsprocedure (hoe komen stukken op de agenda?)
  - Commissiebehandeling (voorbereidende bespreking, terugverwijzing)
  - Raadsbehandeling (amendering, stemming, besluitvorming)
  - Afdoening van moties en toezeggingen
  - Heropening / herzien voorstel
  - Identify which procedural steps are **mandatory** (present in every gemeente) vs **optional** (Rotterdam-specific or gemeente-dependent).

- [ ] **Research Rotterdam's Reglement van Orde** specifically. This is the actual rule set that governs the documents in our corpus. Check:
  - https://decentrale.regelgeving.overheid.nl for Rotterdam's RvO
  - Or ask via the iBabs API / council secretariat
  - Note any Rotterdam-specific procedures that deviate from the VNG model (Rotterdam uses "gebiedscommissies" which have their own advisory role, and Rotterdam has a specific "aangehouden motie" procedure).

- [ ] **Produce a journey state diagram** (Mermaid or text-based) for each document type:
  - **Raadsvoorstel journey:** ingekomen stuk → commissie (eerste bespreking) → [raad | terugverwijzing naar commissie | herzien voorstel → commissie] → raad (stemming) → [aangenomen (evt. geamendeerd) | verworpen | aangehouden]
  - **Motie journey:** ingediend op raadsvergadering → [aangenomen | verworpen | ingetrokken | aangehouden] → (if aangenomen:) afdoeningsvoorstel van college → commissie bespreekt afdoening → [afgedaan | niet-afgedaan → nieuw afdoeningsvoorstel → ...]
  - **Amendement journey:** ingediend bij raadsvoorstel → stemming → [aangenomen (wijzigt raadsvoorstel) | verworpen]
  - **Initiatiefvoorstel journey:** ingediend door raadslid(en) → commissie → raad → same as raadsvoorstel from here
  - **Raadsbrief / Collegebrief journey:** incoming → commissie (voor kennisgeving aangenomen | besproken) → evt. debat in raad
  - **Others** that surface from the RvO research (toezegging, schriftelijke vragen, interpellatieverzoek, etc.)

- [ ] **Map the state diagram to edge types.** Propose concrete `relation_type` values for `kg_relationships` that cover all transitions in the diagrams. Draft proposal (to be validated against research):

  | Edge type | From → To | Example |
  |---|---|---|
  | `INGEKOMEN_BIJ` | document → meeting (agenda) | raadsvoorstel arrives on agenda |
  | `BESPROKEN_IN` | document → meeting (commissie) | commissie discusses raadsvoorstel |
  | `BEHANDELD_IN` | document → meeting (raad) | raad debates raadsvoorstel |
  | `TERUGVERWEZEN_NAAR` | document → meeting (commissie) | raad sends it back to commissie |
  | `HERZIEN_DOOR` | document → revised document | herzien raadsvoorstel replaces original |
  | `GEWIJZIGD_DOOR` | raadsvoorstel → amendement | amendement modifies raadsvoorstel |
  | `INGEDIEND_BIJ` | motie/amendement → meeting (raad) | motie filed during raadsvergadering |
  | `AFGEDAAN_DOOR` | motie → afdoeningsvoorstel | college responds to aangenomen motie |
  | `DISCUSSED_IN` | motie → notulen chunk | (already built by WS1) |
  | `VOTED_IN` | motie → notulen chunk | (already built by WS1) |

  This list is a STARTING POINT — the research will likely surface more. The final list must cover every transition in every state diagram without gaps.

- [ ] **Validate the edge type list** against 10 real document journeys from the Rotterdam corpus. Pick documents with known complex journeys (terugverwijzing, herzien voorstel, lange afdoeningscyclus). For each, manually trace the journey in iBabs and verify the proposed edge types can represent it completely.

- [ ] **Document multi-municipality portability.** Different municipalities have different procedural structures (Amsterdam uses "stadsdelen" with their own decision process; Utrecht has a different committee structure; small gemeenten have a simpler two-step process). The edge types and state diagram must be generalizable — Rotterdam-specific procedures should be a specialization, not hardcoded. Note which parts of the schema are universal (Gemeentewet-mandated) vs municipality-specific (RvO-dependent). This matters for WS5b multi-portal.

### Phase 1 — Edge extraction (~3 days)

- [ ] **`scripts/extract_journey_edges.py`** — new file. For each document in the corpus, trace its journey through the `meetings`, `agenda_items`, and `documents` tables and emit the appropriate edge types into `kg_relationships`. Strategy:
  - iBabs agenda structure: `meetings` → `agenda_items` → `documents`. A document's presence on an agenda is the primary signal for `INGEKOMEN_BIJ`, `BESPROKEN_IN`, `BEHANDELD_IN`.
  - Meeting type detection: `meetings.committee` + `meetings.name` distinguish commissie vs raadsvergadering.
  - Document-to-document links: iBabs sometimes stores parent-child document relationships (raadsvoorstel → amendement, motie → afdoeningsvoorstel). Check `documents.source_document_id` or the ORI API `relations` endpoint.
  - Fallback for missing structural links: use document name matching (e.g. "Herzien raadsvoorstel <title>" → match to original "Raadsvoorstel <title>") + date windowing.
  - WS1's DISCUSSED_IN / VOTED_IN edges are consumed as-is — do not recompute.
  - Advisory lock 42, `--dry-run`, `--limit`, `--resume`.

- [ ] **Handle the afdoeningscyclus.** Afdoeningsvoorstellen are a specific document type in iBabs. They reference the original motie (often by number or title). Build `AFGEDAAN_DOOR` edges by:
  - Matching afdoeningsvoorstel documents to the motie they reference (via `motion_number`, title match, or iBabs document relations)
  - Tracking whether the afdoening was accepted ("afgedaan") or rejected ("niet-afgedaan") in the commissie where it was discussed
  - Emitting the appropriate edge (AFGEDAAN_DOOR with `metadata.status = 'afgedaan' | 'niet-afgedaan'`)

- [ ] **Handle herzien voorstel.** When a raadsvoorstel is sent back and revised, the new version appears as a separate document. Link via `HERZIEN_DOOR` by:
  - Name matching: "Herzien <original title>" or "Gewijzigd <original title>"
  - Same meeting_id lineage
  - iBabs parent-child relations if available

### Phase 2 — Service & MCP tool (~2 days)

- [ ] **`services/journey_service.py`** — new file:
  - `build_journey(root_document_id: str, gemeente: str = 'rotterdam') -> dict` — walks the KG edges from the root document, assembles the journey as an ordered event list with branching.
  - `events_in_window(root_document_id, start_date, end_date) -> list[Event]` — filter helper
  - `verify_journey(root_document_id) -> ValidationReport` — used by tests
  - The journey output should represent the **graph structure**, not just a flat list — if a document bounces back to commissie, the output shows the loop, not a misleading linear progression.
  - Output format: `{"root": {...}, "events": [...], "branches": [...], "current_status": "afgedaan|in behandeling|aangenomen|verworpen|..."}`. The `current_status` field answers "where is this document now in the process?" without the user having to read the full event chain.

- [ ] **MCP tool `traceer_document(document_id: str) -> dict`** in [`mcp_server_v3.py`](../../mcp_server_v3.py)
  - Returns the journey JSON, ordered chronologically with branching preserved
  - Each event includes citation IDs the LLM can pass to `lees_fragment`
  - Tool description for AI: "Use this when the user asks how a document moved through the council process, what moties were filed against it, how it was voted, or wants a chronological case file. Do NOT use for general date filtering — use `lijst_vergaderingen` for that."

- [ ] **Replace `tijdlijn_besluitvorming` internals** ([`mcp_server_v3.py:614`](../../mcp_server_v3.py#L614)) — the existing flat timeline becomes a thin wrapper around `traceer_document` for backward compatibility, but new clients are pointed at `traceer_document`.

### Phase 3 — Validation (~1 day)

- [ ] **20-journey hand-validation set** — pick 20 documents with known-good journeys covering all document types and procedural paths:
  - At least 3 raadsvoorstellen (including 1 with terugverwijzing, 1 with herzien voorstel, 1 with amendementen)
  - At least 3 moties with afdoeningscycli (including 1 where afdoening was rejected first, then re-submitted)
  - At least 2 amendementen
  - At least 1 initiatiefvoorstel
  - At least 1 raadsbrief
  - The Feyenoord stadion and Tweebosbuurt dossiers specifically (complex, well-documented journeys)
  
  For each, manually list expected events and assert `traceer_document` captures them.

- [ ] **Acceptance**: 100% of expected events captured, ≤2 false positives across all 20 journeys, correct `current_status` for all 20.

## Acceptance criteria

- [ ] Phase 0 research document produced with state diagrams for all document types, validated against Gemeentewet + VNG RvO + Rotterdam RvO
- [ ] Edge type list finalized and validated against 10 real corpus journeys
- [ ] `scripts/extract_journey_edges.py` exists with `--dry-run`, `--limit`, `--resume`, advisory lock 42
- [ ] Journey edges populated in `kg_relationships` for all documents in the corpus
- [ ] `services/journey_service.py` exists with `build_journey`, `events_in_window`, `verify_journey`
- [ ] `traceer_document` MCP tool returns structured journey JSON for any document_id
- [ ] `tijdlijn_besluitvorming` re-implemented as a thin wrapper (or marked deprecated)
- [ ] 20-journey hand-validation passes (100% expected events, ≤2 false positives total)
- [ ] Tool registered in WS4 registry with AI-consumption description
- [ ] Multi-municipality portability documented: which edge types are universal (Gemeentewet) vs gemeente-specific (RvO)
- [ ] **UI work explicitly deferred to v0.2.1** — no `templates/journey.html` in this workstream

## Eval gate

| Metric | Target |
|---|---|
| Hand-validated journey precision | 100% expected events captured |
| Hand-validated journey false positives | ≤ 2 across 20 journeys |
| Afdoeningscyclus coverage | ≥ 80% of aangenomen moties have an AFGEDAAN_DOOR edge |
| `traceer_document` p95 latency | < 800ms (cached) / < 3s (uncached) |
| `current_status` accuracy | 100% correct on 20 hand-validated journeys |

## Risks specific to this workstream

| Risk | Mitigation |
|---|---|
| WS1 cross-doc edges not populated yet | Build view + service against mock edges; merge once WS1 phase A lands; coordinate update with WS1 owner |
| Process research takes too long | Time-box Phase 0 to 2 days. The Gemeentewet and VNG RvO are publicly available. Rotterdam's RvO is on decentrale.regelgeving.overheid.nl. If stuck, ask the user (sitting raadslid) directly. |
| iBabs document relations incomplete | Some journeys can only be reconstructed via name matching + date windowing. Accept lower confidence on these and flag them in metadata. |
| Afdoeningsvoorstellen not reliably linked to original moties | Fallback: motion_number match + title similarity. Log unlinked afdoeningsvoorstellen for manual review. |
| Different municipalities have different RvO procedures | Design edge types as universal (Gemeentewet-level) with municipality-specific metadata. Rotterdam specializations are stored in `metadata.rvo_procedure`. |
| Some documents have no clean "arrival" event (legacy data) | Default arrival = `min(meeting_date for documents.id IN agenda)`; flag for manual review |
| Webcast events depend on Royalcast scraping which can fail | Webcast events are optional; absence is not a journey error |
| Same document attached to multiple journeys (e.g. raadsbrief referenced in multiple raadsvoorstellen) | Journey is rooted at a single `document_id`; cross-references are surfaced as `related_documents`, not as separate journey roots |

## Reference sources for Phase 0 research

- **Gemeentewet:** https://wetten.overheid.nl/BWBR0005416 (Titel III and IV)
- **VNG model-Reglement van Orde:** https://vng.nl/rubrieken/onderwerpen/reglement-van-orde (or search vng.nl for "model-reglement van orde gemeenteraad")
- **Rotterdam Reglement van Orde:** https://decentrale.regelgeving.overheid.nl — search for "Reglement van Orde gemeenteraad Rotterdam"
- **VNG Handboek besluitvorming gemeenteraad:** practical guide to the decision-making process
- **iBabs API documentation:** for understanding document-to-document relations and agenda structure
- The user (Dennis Tak) is a sitting Rotterdam raadslid — ask him directly for process clarifications rather than guessing

## Future work (do NOT do in this workstream)
- `/journey/{id}` UI route + `templates/journey.html` — **WS3 v0.2.1**
- Side-panel `vraag_aan_dossier` constrained to journey events — needs WS6 dossier work, defer
- Press / news event integration (out of scope; future WS)
- Cross-municipality journey comparison — defer to v0.4
- Automatic detection of "stuck" documents (aangenomen motie with no afdoening after 6+ months) — interesting for civic accountability but out of v0.2 scope

## Outcome
*To be filled in when shipped. Include: edge types finalized, research findings, hand-validation results, edge cases hit, multi-municipality portability assessment.*
