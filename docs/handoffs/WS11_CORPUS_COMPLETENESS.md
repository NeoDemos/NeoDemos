# WS11 — Corpus Completeness 2018–2026

> **Status:** `not started`
> **Owner:** `unassigned`
> **Priority:** 1 (blocks eval quality, MCP usefulness, and press readiness)
> **Parallelizable:** yes (WS11a metadata backfill can run in parallel with WS11b ingestion prep)

---

## TL;DR

Our 2018–2026 corpus is incomplete. The ORI (Open Raadsinformatie) API contains **~3,279 schriftelijke vragen** for Rotterdam in this period; we have ~120 by name (4% coverage). Other formal document types (initiatiefnotities, initiatiefvoorstellen) are partially present but lack `doc_classification` metadata, making them invisible to MCP tools that filter by type. This workstream makes the corpus 100% complete and correctly classified for the target window.

**Trigger:** First external test user (Erik Verweij) reported he could not find initiatiefnotities or schriftelijke vragen via the MCP connector. Audit confirmed the gaps on 2026-04-13.

---

## Scope

### What we have vs. what ORI has (2018–2026)

| Doc type | In DB (by name) | ORI count | Gap | Priority |
|---|---|---|---|---|
| schriftelijke_vraag | ~120 | ~3,279 | ~3,159 (96%) | **P0** |
| initiatiefnotitie | 42 | unknown | high | **P0** |
| initiatiefvoorstel | 231 | unknown | medium | P1 |
| motie | 9,398 | covered via meeting docs | low | P2 |
| amendement | 469 | covered via meeting docs | low | P2 |
| raadsvoorstel | 1,849 | covered | — | — |
| toezegging | 2,058 | covered | — | — |

See `docs/ws11_scope.json` for machine-readable per-year counts, API query templates, and field mappings.

---

## Architecture

### Two sub-workstreams

#### WS11a — Metadata backfill (no new ingestion)
Fix `doc_classification` on documents already in our DB. All three types are present but have `doc_classification = NULL`, making them invisible to classification-based retrieval.

**Target:**
- 111 initiatiefnotities → `doc_classification = 'initiatiefnotitie'`
- 522 initiatiefvoorstellen → `doc_classification = 'initiatiefvoorstel'`
- ~120 schriftelijke_vraag docs → `doc_classification = 'schriftelijke_vraag'`
- Fix 41 initiatiefnotities with no `meeting_id` (derive from `agenda_item_id` linkage)

**Script:** `scripts/ws11a_classify_existing_docs.py`
- Name-pattern matching (same patterns as audit query)
- Dry-run mode first, then `--execute`
- Log all changes to `logs/ws11a_classification.log`

**Estimated time:** 2–4 hours. No pipeline needed — pure SQL UPDATE.

---

#### WS11b — ORI ingestion of missing documents

Fetch missing schriftelijke vragen (and other gaps) from ORI API, upsert into documents table, embed, and index in Qdrant.

**Data source priority:**
1. **ORI API** — `https://api.openraadsinformatie.nl/v1/elastic/ori_rotterdam_20250629013104/_search` — authoritative, full 2018–2025 coverage
2. **iBabs** — `rotterdam.raadsinformatie.nl` — fills ORI's 1–3 month lag for 2025–2026 recents

**Document types to ingest (in order):**
1. Schriftelijke vragen (MediaObject + Report/Raadsvragen) — 2018–2026
2. Initiatiefnotities — 2018–2026 (gap size TBD from ORI)
3. Initiatiefvoorstellen — verify coverage, fill gaps

**Pipeline:** Use the existing hardened document ingestion pipeline (`services/document_processor.py` + `DocumentClassifier`). New documents enter as `doc_classification = 'schriftelijke_vraag'` etc., bypassing the OCR classification step if ORI already provides extracted text via `text[]`.

**Script:** `scripts/ws11b_ori_ingestion.py`

```
for each target doc type:
  1. fetch from ORI API (paginated, 500/page)
  2. for each doc: check if already in DB by ORI @id or URL match
  3. if missing: upsert into documents + agenda_items linkage
  4. if ORI text[] available: use directly (skip OCR)
  5. if no text: queue for DocumentProcessor OCR pass
  6. after upsert: chunk + embed into Qdrant (batch 50)
  7. set doc_classification
```

**Checkpointing:** Resume from last processed ORI `@id` — same pattern as WS7 OCR recovery.

---

## ORI API Reference

```
Base: https://api.openraadsinformatie.nl/v1/elastic/ori_rotterdam_20250629013104/_search
Auth: none required (public)
Rate: no documented limit; use 1 req/sec conservatively

Key date field: last_discussed_at (NOT date_modified — ORI indexes 1-3 months late)

Fetch schriftelijke vragen by year:
POST /.../_search
{
  "size": 500,
  "query": {
    "bool": {
      "must": [
        {"term": {"@type.keyword": "MediaObject"}},
        {"range": {"last_discussed_at": {"gte": "2023-01-01", "lte": "2023-12-31"}}}
      ],
      "should": [
        {"match": {"name": "schriftelijke vraag"}},
        {"match": {"name": "raadsvraag"}}
      ],
      "minimum_should_match": 1
    }
  },
  "_source": ["@id", "name", "url", "original_url", "last_discussed_at", "text", "content_type"]
}

Fetch by ORI Report classification:
POST /.../_search
{
  "size": 500,
  "query": {"term": {"classification.keyword": "Raadsvragen"}},
  "sort": [{"start_date": {"order": "asc"}}],
  "_source": ["@id", "name", "classification", "start_date", "attachment", "description"]
}
```

See `docs/ws11_scope.json` for all query templates.

---

## DB Schema

New documents land in `documents` with:
- `id`: ORI `@id` (use as stable external key, e.g. `ori_<hash>`)
- `name`: ORI `name`
- `url`: ORI `url` or `original_url`
- `content`: ORI `text[]` joined, or OCR output
- `category`: `'municipal_doc'`
- `doc_classification`: type-specific value
- `meeting_id`: derived from ORI `was_generated_by` → `Meeting` @id match
- `agenda_item_id`: derived from ORI `AgendaItem` parent linkage

---

## WS4 / WS9 Items (not WS11 scope but triggered by same audit)

| Task | Workstream | Owner | Effort |
|---|---|---|---|
| MCP installer: "Connectors" fix | WS4 | — | ✅ Done 2026-04-13 |
| MCP installer: "Wat kun je vragen?" section | WS4 | unassigned | 0.5 day |
| fast_mode in MCP tool calls (skip Jina reranking) | WS9 | unassigned | 1 day |
| Speed guidance in installer (Sonnet recommended) | WS4 | unassigned | 0.5 day |

---

## Success Criteria

- [ ] All 111 initiatiefnotities have `doc_classification = 'initiatiefnotitie'`
- [ ] All 522 initiatiefvoorstellen have `doc_classification = 'initiatiefvoorstel'`
- [ ] Schriftelijke vragen: ≥ 90% ORI coverage for 2018–2026 (target: ~3,000 docs)
- [ ] All new docs embedded in Qdrant `notulen_chunks`
- [ ] MCP tool `zoek_moties` returns schriftelijke vragen when asked
- [ ] Erik Verweij re-test: can retrieve initiatiefnotities and schriftelijke vragen by topic

## Estimated Timeline

| Phase | Effort | Blocker |
|---|---|---|
| WS11a metadata backfill | 0.5 day | none |
| WS11b ORI fetch + upsert | 2 days | ORI API exploration |
| WS11b embed + index | 1 day (GPU run) | WS11b upsert complete |
| QA + re-test | 0.5 day | embed complete |
| **Total** | **~4 days** | |
