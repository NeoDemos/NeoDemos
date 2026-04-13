# WS2b — IV3 Taakveld FK Backfill

> **Priority:** 2 (follow-up to WS2 — needed for multi-year comparisons and future multi-city financials)
> **Status:** `not started`
> **Owner:** `unassigned`
> **Target release:** v0.2.0
> **Depends on:** WS2 shipped ✅ — reads `financial_lines` + `programma_aliases`

---

## TL;DR

WS2 shipped 61,182 `financial_lines` rows with `iv3_taakveld = NULL` for all of them. The mapping table exists (`programma_aliases` — 2,628 rows seeded by `scripts/seed_programma_aliases.py`) but the extractor's `_assign_iv3` step was never wired to use it. This workstream: (1) wires the lookup into `pipeline/financial_ingestor.py`, (2) backfills all existing rows, (3) verifies coverage. Without this, cross-year aggregation by IV3 taakveld (the national standard for municipal budget categories) is impossible, and the Waalwijk counter-demo (v0.2.1) will produce incorrect multi-municipality comparisons.

**This is a one-day task.** The hard design work (IV3 schema, alias table, FK column) is already done.

---

## Dependencies

- WS2 done ✅ — `financial_lines`, `programma_aliases`, `iv3_taakvelden` tables all exist
- Advisory lock 42 required for any batch writes (house rule)
- Do **not** run while a financial backfill or nightly pipeline is actively writing

## Cold-start prompt

> You are picking up Workstream 2b (IV3 Taakveld FK Backfill) of NeoDemos v0.2.0.
>
> Read in order: (1) this file, (2) `docs/handoffs/WS2_FINANCIAL.md` §Outcome (especially the "IV3 taakveld mapping" gap note), (3) `pipeline/financial_ingestor.py` (the `_assign_iv3` method and `_write_lines` batch writer), (4) `scripts/seed_programma_aliases.py` (to understand the alias table structure).
>
> Your job: wire the existing `programma_aliases` lookup into `_assign_iv3` so new extractions populate `iv3_taakveld`, then run a one-time SQL UPDATE backfill for the 61,182 existing rows. Verify coverage reaches ≥ 80% (some programma names will never match a taakveld — that is expected and acceptable).
>
> Honor house rules: advisory lock 42 for all writes. Use `--dry-run` before executing the backfill.

## Files to read first

- [`docs/handoffs/WS2_FINANCIAL.md`](WS2_FINANCIAL.md) — §Outcome "IV3 taakveld mapping" gap note at the bottom
- [`pipeline/financial_ingestor.py`](../../pipeline/financial_ingestor.py) — `_assign_iv3` method (search for this name), `_write_lines` batch writer
- [`scripts/seed_programma_aliases.py`](../../scripts/seed_programma_aliases.py) — shows structure of `programma_aliases` (programma_name → iv3_taakveld_id)
- Postgres: `financial_lines`, `programma_aliases`, `iv3_taakvelden` tables

---

## Build tasks

### 1. Wire lookup in extractor (~2 hours)

The `_assign_iv3` method in `pipeline/financial_ingestor.py` exists but returns `None` unconditionally (or is a stub). Fix it:

- [ ] In `_assign_iv3(self, programma: str, gemeente: str) -> str | None`:
  - Query `programma_aliases` for an exact match on `programma_name ILIKE programma AND gemeente = gemeente` (case-insensitive)
  - If no exact match, try normalised match: strip leading digits/dots (`"1.2 - Openbare orde en veiligheid"` → `"Openbare orde en veiligheid"`) then re-query
  - Return the `iv3_taakveld_id` FK value, or `None` if no alias found (do not error — unmatched rows are expected)
- [ ] Verify `_assign_iv3` is called during `_write_lines` before the INSERT and the result is bound to the `iv3_taakveld` column
- [ ] Test manually on 5 known programma names (e.g. `"Veilig"`, `"Mobiliteit"`, `"Wonen"`, `"Onderwijs"`, `"Bestuur"`) — each should return a non-NULL taakveld

### 2. Backfill existing rows (~2 hours)

- [ ] Write `scripts/ws2b_backfill_iv3.py` with `--dry-run` / `--limit` / `--resume` flags:
  ```python
  # Pseudocode
  rows = SELECT id, programma, gemeente FROM financial_lines WHERE iv3_taakveld IS NULL
  for row in rows:
      taakveld = _assign_iv3(row.programma, row.gemeente)
      if taakveld:
          UPDATE financial_lines SET iv3_taakveld = taakveld WHERE id = row.id
  ```
  Use `pg_advisory_lock(42)` before the batch and release in `finally`.
- [ ] Run `--dry-run` first; inspect the match log — verify programma names look correct and no cross-gemeente contamination
- [ ] Run `--limit 100` as a smoke test; check the 100 updated rows in Postgres
- [ ] Run full backfill

### 3. Verify coverage (~30 minutes)

- [ ] Run the coverage query:
  ```sql
  SELECT
    COUNT(*) FILTER (WHERE iv3_taakveld IS NOT NULL) AS matched,
    COUNT(*) AS total,
    ROUND(100.0 * COUNT(*) FILTER (WHERE iv3_taakveld IS NOT NULL) / COUNT(*), 1) AS pct
  FROM financial_lines;
  ```
  Target: **≥ 80% matched**. Expected unmatched: GR/DCMR/VRR programma names with no alias seeded, plus jaarstukken-specific subtotals. Do not force-match these — NULL is correct for unidentifiable programma strings.

- [ ] Spot-check 5 rows in the financial MCP tools — `vraag_begrotingsregel(gemeente='rotterdam', jaar=2025, programma='Veilig')` should return a row with a non-NULL `iv3_taakveld`

---

## Acceptance criteria

- [ ] `_assign_iv3` returns a non-NULL FK for at least the top 20 Rotterdam programma names (by frequency in `financial_lines`)
- [ ] `financial_lines.iv3_taakveld` coverage ≥ 80% after backfill
- [ ] All new extractions (re-running `financial_ingestor.py`) produce populated `iv3_taakveld` on first write — no separate backfill needed going forward
- [ ] Backfill script is idempotent — re-running it does not create duplicates or overwrite already-correct values
- [ ] `vraag_begrotingsregel` MCP tool response includes `iv3_taakveld` in the returned row

---

## What NOT to do

- Do not re-seed `programma_aliases` — the 2,628 rows seeded by WS2 are correct. Only wire the lookup.
- Do not extend the alias table with programma names from other gemeenten — that is Waalwijk/WS5b work.
- Do not change the `iv3_taakvelden` reference table — it is locked as the national IV3 standard.
- Do not re-run the full `financial_ingestor.py` batch to get the fix — the backfill script is cheaper and less risky.

---

## Outcome

*To be filled in when shipped. Include: backfill coverage %, matched programma names, any unexpected NULLs and why.*
