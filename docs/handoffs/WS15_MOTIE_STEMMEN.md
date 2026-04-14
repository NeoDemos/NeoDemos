# WS15 — Per-Party Voting Data (`motie_stemmen` table + `zoek_stemgedrag` MCP tool)

> **Priority:** 1.5 (press-moment defensive query layer — answers "how did your fractie vote on others' proposals")
> **Status:** `not started`
> **Owner:** `unassigned`
> **Target release:** v0.2.0 *(promoted from v0.3.0 on 2026-04-14 after MCP testing surfaced this as the #1 missing query class)*
> **Depends on:** WS11 (besluitenlijsten classified — done) ✅; WS4 tool registry (shipped) ✅. Does **not** depend on WS1 Phase A.

## TL;DR

Build the structured per-party voting table that turns *"how did D66 vote on restrictive nachtleven proposals 2018-2024"* into one SQL query. Today this requires either KG graph walks (slow + needs WS1) or LLM synthesis from raw besluitenlijst chunks (slow + lossy + non-aggregatable). Source data already exists: 1,077 besluitenlijst documents in the corpus (`doc_classification = 'besluitenlijst'`, classified by WS11a). Extraction is regex-based — besluitenlijsten have a stable enumerated format ("Voor: D66, GroenLinks-PvdA, ... / Tegen: VVD, Leefbaar Rotterdam, ..."), no LLM needed. Result: an aggregatable Postgres table + one new MCP tool `zoek_stemgedrag`.

This is the **defensive** half of the party-position query. The **offensive** half (what a fractie *filed*) is already covered by `zoek_moties`. Together they answer the press-moment question class: *"what did your party say it stood for vs. how did it actually vote when the test came?"*

**Estimated effort:** 3–5 days. KG `STEMT_VOOR/TEGEN` edges in WS1 stay in scope for graph walks; this WS gives the fast aggregatable SQL layer.

---

## Dependencies

- WS11 ✅ — `documents.doc_classification = 'besluitenlijst'` populated for the 1,077 in-corpus besluitenlijsten
- WS4 ✅ — `services/mcp_tool_registry.py` exists; new tool registers there
- `politician_registry.party_aliases` — needed for cross-period party normalization (e.g. PvdA + GroenLinks → GroenLinks-PvdA after 2024 fusie). Verify column exists before extraction; if not, add a small `party_aliases` reference table as part of this WS.
- House rule: `pg_advisory_lock(42)` for batch writes; `--dry-run` first.

Memory to read first:
- [project_motie_signatories.md](../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_motie_signatories.md) — party attribution patterns (text-regex approach OK)
- [project_v0_2_beat_maat_plan.md](../../.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_v0_2_beat_maat_plan.md) — strategic context

## Cold-start prompt

> You are picking up Workstream 15 (Per-party voting data) of NeoDemos v0.2.0. Self-contained handoff at `docs/handoffs/WS15_MOTIE_STEMMEN.md`.
>
> Read in order: (1) this handoff, (2) `pipeline/document_classifier.py` for the besluitenlijst classifier, (3) `scripts/ws11a_classify_existing_docs.py` for how WS11a tagged besluitenlijsten in `documents`, (4) sample 5 actual besluitenlijst PDFs to confirm the regex shape (5 different years — format drifted between iBabs eras), (5) `services/mcp_tool_registry.py` for the WS4 registration contract.
>
> Your job: ship a Postgres `motie_stemmen` table, a regex-based extractor that backfills it from the 1,077 besluitenlijsten already in the corpus, and a new MCP tool `zoek_stemgedrag` that aggregates over it. No LLM in the extraction path. Honour house rules (advisory lock 42, dry-run, cloud-first dev).
>
> The KG `STEMT_VOOR/TEGEN` edges from WS1 are NOT replaced — they coexist. KG handles graph walks ("show me the 5-step trace from this motie to the wijk"); this table handles aggregation ("count D66 'voor' votes on housing motions per year"). Where the same vote is captured in both, the structured table is the source of truth for `uitkomst_per_partij`.

## Files to read first

- [`pipeline/document_classifier.py`](../../pipeline/document_classifier.py) — besluitenlijst classification
- [`scripts/ws11a_classify_existing_docs.py`](../../scripts/ws11a_classify_existing_docs.py) — `besluitenlijst` and `adviezenlijst` patterns
- [`services/mcp_tool_registry.py`](../../services/mcp_tool_registry.py) — tool registration contract (WS4)
- [`mcp_server_v3.py`](../../mcp_server_v3.py) — existing `zoek_moties` for the response-shape convention to mirror
- 5 sample besluitenlijst PDFs from `documents` (pick years 2018, 2020, 2022, 2024, 2025 — formats drift across iBabs eras)
- Postgres: `documents`, `chunks`, `politician_registry`, `kg_relationships` (for the optional KG cross-check in §4 below)

---

## Build tasks

### 1. Schema (~0.5 day)

Alembic migration `0013_motie_stemmen.py`:

```sql
CREATE TABLE motie_stemmen (
    id              BIGSERIAL PRIMARY KEY,
    motie_doc_id    TEXT NOT NULL,                       -- references documents.id, the motion itself
    motie_bb_id     TEXT,                                -- BB-nummer (e.g. "21bb004603") for cluster joins
    besluitenlijst_doc_id TEXT NOT NULL,                 -- references documents.id, the source besluitenlijst
    vergadering_id  TEXT,                                -- references meetings.id when resolvable
    stemming_datum  DATE NOT NULL,
    partij          TEXT NOT NULL,                       -- normalized via party_aliases
    stem            TEXT NOT NULL CHECK (stem IN ('voor','tegen','onthouden','afwezig')),
    raw_party_text  TEXT,                                -- pre-normalization, for audit
    extraction_method TEXT NOT NULL,                     -- 'regex_v1', 'regex_v2_post2022' etc.
    extracted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (motie_doc_id, partij, stemming_datum)        -- one vote per party per motion-vote event
);

CREATE INDEX idx_motie_stemmen_partij_datum  ON motie_stemmen (partij, stemming_datum DESC);
CREATE INDEX idx_motie_stemmen_motie         ON motie_stemmen (motie_doc_id);
CREATE INDEX idx_motie_stemmen_bb            ON motie_stemmen (motie_bb_id) WHERE motie_bb_id IS NOT NULL;
CREATE INDEX idx_motie_stemmen_uitslag       ON motie_stemmen (stem);
```

Optional companion table if `politician_registry.party_aliases` doesn't already exist:
```sql
CREATE TABLE party_aliases (
    canonical TEXT PRIMARY KEY,
    alias     TEXT NOT NULL,
    valid_from DATE,
    valid_to  DATE,
    UNIQUE (alias, valid_from)
);
```

### 2. Extractor (~2 days)

`scripts/ws15_extract_motie_stemmen.py`:

- Iterate `documents WHERE doc_classification = 'besluitenlijst' AND organisatie = 'rotterdam'` (~1,077 rows).
- For each besluitenlijst, read its `chunks.content` (already in DB, no PDF re-parse needed).
- Run two regex passes — handle format drift across years:
  - **`regex_v1` (2018–2021)**: blocks like `Voor:\s*([A-Z][a-zA-Z\- ]+(?:,\s*[A-Z][a-zA-Z\- ]+)*)\s*\n\s*Tegen:\s*(...)` interspersed with motie titles `Motie\s+(\d+\w+\d+)\s+["'"](.+?)["'"]`.
  - **`regex_v2` (2022–present)**: post-iBabs UI change introduced tabular layouts like `Stemming: Voor (D66, GL-PvdA, ...) / Tegen (VVD, LR, ...) / Resultaat: AANGENOMEN`.
  - Patterns confirmed against 5 sample PDFs across years before bulk run.
- Resolve each party token through `party_aliases` (or politician_registry alias table) to canonical form.
- Resolve `motie_doc_id` via `documents.bb_nummer = matched_bb_id` (BB-nummer is the bridge); fall back to fuzzy title match within ±30 days of `stemming_datum` if BB miss.
- Write to `motie_stemmen`. ON CONFLICT DO NOTHING (idempotent re-runs).
- Log per-besluitenlijst: rows extracted, motie_doc_id misses (track for follow-up), unparseable blocks (track for regex iteration).

Flags: `--dry-run`, `--limit N`, `--year YYYY`, `--resume` (resume from last extracted besluitenlijst), `--method regex_v1|regex_v2|both`.

### 3. MCP tool (~1 day)

`mcp_server_v3.py` — new tool:

```python
@mcp.tool()
async def zoek_stemgedrag(
    partij: str,
    onderwerp: str | None = None,
    beleidsgebied: str | None = None,
    datum_van: str | None = None,
    datum_tot: str | None = None,
    stem_filter: str | None = None,    # 'voor' | 'tegen' | None (all)
    indiener_partij: str | None = None,  # NEW: filter to defensive votes (motions filed by OTHER parties)
    max_resultaten: int = 20,
) -> dict:
    """How did <party> vote on motions about <topic> between <dates>?

    Use this when:
    - You need an aggregatable answer ("hoeveel keer stemde D66 tegen restrictieve horeca-moties")
    - You need defensive-side voting (how did X vote on motions THEY did not file)
    - You need cross-period inconsistency-spotting (party flipped between 2018 and 2024)

    Do NOT use this when:
    - You want the qualitative trace of one specific motion (use `traceer_motie`)
    - You want the motion text itself (use `zoek_moties` then `lees_fragment`)
    """
```

Implementation:
- SQL aggregation over `motie_stemmen` JOINed to `documents` (for motie title) and `kg_mentions` or `chunks.beleidsgebied` (for topic filter).
- `indiener_partij` filter: JOIN to KG `DIENT_IN` edges (or fall back to motie text-regex) and exclude motions where `partij = indiener_partij`. This is the defensive-vote query class.
- Response shape:
  ```json
  {
    "partij": "D66",
    "totaal_stemmingen": 47,
    "uitslag_breakdown": {"voor": 12, "tegen": 31, "onthouden": 2, "afwezig": 2},
    "datum_range_in_resultaten": {"earliest": "2018-03-12", "latest": "2025-11-04"},
    "voorbeelden": [{"motie_doc_id": "...", "titel": "...", "stem": "tegen", "datum": "...", "indiener_partij": "..."}, ...]
  }
  ```
- Register in `services/mcp_tool_registry.py` with the WS4 AI-consumption description template.

### 4. Cross-check vs. KG `STEMT_VOOR/TEGEN` edges (~0.5 day)

WS1 v0.1.0 already produced KG `STEMT_VOOR/TEGEN` edges from motie-body text-regex (party signatories). After this WS lands, run a one-time cross-check:

```sql
-- Find KG edges that disagree with motie_stemmen
SELECT m.motie_doc_id, m.partij, m.stem AS stem_table,
       r.relation_type AS stem_kg
  FROM motie_stemmen m
  JOIN kg_relationships r ON r.source_entity_id = (resolve partij to entity) AND r.target_entity_id = (resolve motie to entity)
 WHERE (m.stem = 'voor'  AND r.relation_type = 'STEMT_TEGEN')
    OR (m.stem = 'tegen' AND r.relation_type = 'STEMT_VOOR');
```

Treat `motie_stemmen` as the source of truth (besluitenlijsten are authoritative, motie-body party signatories are indicative-only). For disagreements: re-tag the KG edge `metadata.source = 'motie_body'`, `confidence -= 0.2`, mark superseded. Do NOT delete — KG retains the trace, query layer prefers the table.

### 5. Eval question (~0.5 day)

Add to `eval/data/questions.json`:

> *"Hoe stemde D66 op restrictieve nachtleven-moties (sluitingstijden, geluidsoverlast, vergunningsverkorting) ingediend door andere fracties tussen 2018 en 2025? Geef per stemming: motie, indiener, D66-stem, datum."*

Gold answer requires `zoek_stemgedrag(partij='D66', onderwerp='nachtleven', stem_filter=None, indiener_partij=None)` *minus* motions where D66 was indiener — i.e. uses the `indiener_partij` exclusion path. Pre-WS15 baseline: tool doesn't exist, current best alternative (`zoek_moties` + manual filtering) returns mostly D66-authored motions, missing the defensive-vote answer entirely. This was the trigger session in [`.coordination/FEEDBACK_LOG.md` 2026-04-14](../../.coordination/FEEDBACK_LOG.md).

---

## Acceptance criteria

- [ ] `motie_stemmen` table exists with the schema above; migration applied to staging then prod
- [ ] Extractor populates ≥ **80% of expected vote events** for the 1,077 besluitenlijsten (target = ~50K rows assuming ~10 motions × ~5 voting parties per besluitenlijst on average; revise after first full run)
- [ ] `motie_doc_id` resolution rate ≥ **70%** (besluitenlijsten reference moties by BB-nummer; some old besluitenlijsten only cite titles — fuzzy match handles ~70%, residual stays unlinked)
- [ ] Party-name normalization covers all 14 current Rotterdamse fracties + at least 4 historical aliases (PvdA pre-fusie, GroenLinks pre-fusie, Nida, etc.)
- [ ] `zoek_stemgedrag` MCP tool returns differentiated counts for the eval question; the `indiener_partij` exclusion path returns ≥ 1 example of D66 voting against an LR-filed restrictive motion
- [ ] Cross-check (§4) reports disagreement rate < 5% between `motie_stemmen` and KG `STEMT_VOOR/TEGEN` edges — higher than 5% means regex needs iteration, not a "ship it" signal
- [ ] Tool registered in `services/mcp_tool_registry.py` with full AI-consumption description (Use / Do NOT use blocks)
- [ ] Re-running the extractor is idempotent (UNIQUE constraint enforced; ON CONFLICT DO NOTHING)
- [ ] Sequenced into nightly: WS5a runbook (when shipped) calls the extractor on new besluitenlijsten

## Eval gate

| Metric | Target |
|---|---|
| Extraction coverage (vote events / expected) | ≥ 80% |
| `motie_doc_id` resolution rate | ≥ 70% |
| `zoek_stemgedrag` p95 latency | < 800ms (it's pure SQL, should be fast) |
| KG cross-check disagreement rate | < 5% |
| Eval question answer correctness | manual review by Dennis — must produce a defensible per-vote breakdown for the D66 nachtleven defensive-vote query |

## Risks specific to this workstream

| Risk | Mitigation |
|---|---|
| Besluitenlijst format drift across years breaks regex | Handle two regex passes (v1 = 2018-2021, v2 = 2022-present); confirm against 5 sample years before bulk run; track unparseable rate per-year |
| Stemverklaring-only votes (no per-party roll call) cannot populate `motie_stemmen` | Accept — capture aggregate `uitkomst` only, leave per-party rows empty for those motions; document in tool response when this happens |
| Party alias drift (GL+PvdA fusie, fractie afsplitsingen) misattributes votes | Build `party_aliases` table with `valid_from`/`valid_to`; require the extractor to honour the date window |
| Disagreement with existing KG `STEMT_VOOR/TEGEN` edges confuses callers | Make the structured table the source of truth at query time; KG retains historical trace for graph walks |
| Press-time exposure of an extraction error (we publicly state how a fractie voted, and we're wrong) | (a) Cross-check §4 must pass; (b) `zoek_stemgedrag` response includes `besluitenlijst_doc_id` per row so any claim is one click from the source; (c) source-link rendered in any UI that consumes this tool |

## Future work (do NOT do in this workstream)

- Multi-gemeente extension — Middelburg/Waalwijk besluitenlijsten will need their own format pass when they land in v0.2.1+; do not attempt now
- Vote-prediction model — given a fractie's historical voting + a new motion, predict the vote — out of scope (and out of taste)
- Coalition-coherence dashboard ("how often did the coalition split?") — strategic but builds on this; defer to v0.3 with the public eval scoreboard
- Real-time vote ingestion (sub-hour after a vergadering) — current path runs nightly via WS5a; sub-hour is over-engineered for v0.2

## Outcome

*To be filled in when shipped. Include: extraction coverage, motie resolution rate, KG disagreement rate, the eval question result, surprises around format drift.*
