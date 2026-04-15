# WS14 Calendar Quality — Baseline Audit (2026-04-15)

Pre-Phase-B baseline. Eight **read-only** queries against the prod DB (via
`./scripts/dev_tunnel.sh --bg`). Numbers below get locked in before any
backfill / schema migration runs, then re-run post-Phase-B to prove the fixes
landed.

Every query is **SELECT-only**. No `UPDATE` / `INSERT` / `DELETE` / `ALTER`.

> Filled in by: agent (WS8f continuation session)
> Captured at: 2026-04-15T10:30Z
> Tunnel PID at run: 12349 (verified `ps aux | grep ssh.*178.104`)

---

## Summary table

| # | Check | Result | Notes |
|---|---|---|---|
| A1 | Meetings with agenda but 0 bijlagen (per year) | **0% missing** across 2023-2026 (545 total meetings) | junction table is healthy for recent years |
| A2 | Duplicate `document_assignments` triples | **3,781 rows to delete** across **2,274 groups** | B2 will clear these; B4 constraint then locks it |
| A3 | Documents with direct FKs but no junction row | **2023: 1,673 · 2024: 1,241 · 2025: 82 · 2026: 35** orphan docs | ~10K+ total across all years; B1 backfill urgently needed |
| A4 | Meetings rendered twice (iBabs vs ORI) | **68 duplicate groups**, **79 extra rows** (2023-2026) | B5 dedupe will merge/reparent these |
| A5 | annotaties vs bijlagen per doc_type | **10,138 bijlage · 2,008 annotatie · 22,480 NULL** (via `doc_classification`) | `category` column ≠ bijlage classifier — see schema note below |
| A6 | Meetings with 0 agenda_items | **2023: 46 · 2024: 137 · 2025: 197 · 2026: 53** | likely partial ingest or schema-only meetings |
| A7 | Logical-duplicate meetings (name+date+committee) | **116 dup groups, 135 rows to merge** (all years) | larger than A4; B7 constraint prevents new ones |
| A8 | Docs classified bijlage / annotatie / NULL per year | 2023: 1712/166/1258 · 2024: 1361/156/3071 · 2025: 41/163/3213 · 2026: 4/39/1391 | 2025-2026 severely under-classified; WS11 B3 expansion needed |

---

## A1 — Meetings with ≥1 agenda_item but 0 bijlagen (by year, 2023–2026)

Identifies meetings where we ingested the agenda but failed to attach any
supporting documents. This is the "empty card" problem on `/meeting/<id>`.

```sql
WITH meetings_with_agenda AS (
    SELECT m.id,
           EXTRACT(YEAR FROM m.start_date)::int AS yr,
           COUNT(ai.id) AS agenda_cnt
    FROM meetings m
    JOIN agenda_items ai ON ai.meeting_id = m.id
    WHERE EXTRACT(YEAR FROM m.start_date) BETWEEN 2023 AND 2026
    GROUP BY m.id, yr
    HAVING COUNT(ai.id) >= 1
),
meetings_with_docs AS (
    SELECT DISTINCT da.meeting_id
    FROM document_assignments da
)
SELECT mwa.yr,
       COUNT(*) FILTER (WHERE mwd.meeting_id IS NULL) AS meetings_missing_bijlagen,
       COUNT(*) AS meetings_with_agenda_total,
       ROUND(
         100.0 * COUNT(*) FILTER (WHERE mwd.meeting_id IS NULL) / NULLIF(COUNT(*), 0),
         1
       ) AS pct_missing
FROM meetings_with_agenda mwa
LEFT JOIN meetings_with_docs mwd ON mwd.meeting_id = mwa.id
GROUP BY mwa.yr
ORDER BY mwa.yr;
```

Expected result shape:

| year | meetings_missing_bijlagen | meetings_with_agenda_total | pct_missing |
|---|---|---|---|
| 2023 | 0 | 221 | 0.0% |
| 2024 | 0 | 139 | 0.0% |
| 2025 | 0 | 154 | 0.0% |
| 2026 | 0 | 31 | 0.0% |

---

## A2 — Duplicate `document_assignments` triples

Pre-req for the `UNIQUE(document_id, meeting_id, agenda_item_id)` migration
(0011_da_unique). Must show 0 before the ALTER TABLE can go.

```sql
SELECT document_id,
       meeting_id,
       agenda_item_id,
       COUNT(*) AS dup_count
FROM document_assignments
GROUP BY document_id, meeting_id, agenda_item_id
HAVING COUNT(*) > 1
ORDER BY dup_count DESC
LIMIT 100;
```

Plus aggregate:

```sql
SELECT SUM(dup_count - 1) AS rows_to_delete,
       COUNT(*) AS duplicate_groups
FROM (
    SELECT COUNT(*) AS dup_count
    FROM document_assignments
    GROUP BY document_id, meeting_id, agenda_item_id
    HAVING COUNT(*) > 1
) x;
```

Baseline: _rows_to_delete_ = **3,781**, _duplicate_groups_ = **2,274**.

---

## A3 — Documents with direct FKs but no junction row

Docs that have `meeting_id` / `agenda_item_id` columns set (legacy direct
FKs) but no corresponding row in `document_assignments`. These will drop off
the UI once we rewrite `get_meeting_details` to use the junction exclusively
(WS14 Phase C2 LEFT JOIN rewrite).

```sql
SELECT EXTRACT(YEAR FROM m.start_date)::int AS yr,
       COUNT(*) AS orphan_docs
FROM documents d
LEFT JOIN document_assignments da
  ON da.document_id = d.id
 AND ( (da.meeting_id = d.meeting_id) OR (da.meeting_id IS NULL AND d.meeting_id IS NULL) )
 AND ( (da.agenda_item_id = d.agenda_item_id) OR (da.agenda_item_id IS NULL AND d.agenda_item_id IS NULL) )
LEFT JOIN meetings m ON m.id = d.meeting_id
WHERE da.id IS NULL
  AND (d.meeting_id IS NOT NULL OR d.agenda_item_id IS NOT NULL)
GROUP BY yr
ORDER BY yr NULLS LAST;
```

Baseline: **~8,100+ rows** missing across all years (2002-2026). For 2023-2026 scope: **2023: 1,673 · 2024: 1,241 · 2025: 82 · 2026: 35**. Must hit 0 before C2 ships.

---

## A4 — Meetings rendered twice (iBabs + ORI for the same logical meeting)

iBabs and ORI assign different IDs to the same real-world meeting. This
query groups by a logical key and counts distinct `id` values per group.

```sql
SELECT
    COALESCE(lower(committee), '') AS committee_norm,
    COALESCE(lower(regexp_replace(name, '\s+', ' ', 'g')), '') AS name_norm,
    start_date::date AS d,
    COUNT(*) AS row_count,
    array_agg(id ORDER BY id) AS ids
FROM meetings
WHERE start_date IS NOT NULL
  AND EXTRACT(YEAR FROM start_date) BETWEEN 2023 AND 2026
GROUP BY committee_norm, name_norm, d
HAVING COUNT(*) > 1
ORDER BY d DESC
LIMIT 200;
```

Plus aggregate:

```sql
SELECT COUNT(*) AS logical_duplicate_groups,
       SUM(row_count - 1) AS extra_rows_beyond_one_canonical
FROM (
    SELECT COUNT(*) AS row_count
    FROM meetings
    WHERE start_date IS NOT NULL
      AND EXTRACT(YEAR FROM start_date) BETWEEN 2023 AND 2026
    GROUP BY COALESCE(lower(committee), ''),
             COALESCE(lower(regexp_replace(name, '\s+', ' ', 'g')), ''),
             start_date::date
    HAVING COUNT(*) > 1
) x;
```

> NOTE: the `meetings` table has no explicit `source` column; source is
> encoded in the ID format / prefix (iBabs numeric vs ORI uuid-ish). If a
> `source` column is added later, extend the projection to include
> `array_agg(source)` so we can see the iBabs-vs-ORI split directly.

Baseline: **68 duplicate groups**, **79 extra rows** (2023-2026 scope). Drives the B5 dedupe script scope.

---

## A5 — annotaties vs bijlagen distribution per `doc_type`

Sanity check that the WS11 classifier is actually producing both kinds and
that the ratios are plausible (annotaties are a minority — only committee
meetings have them; notulen belong only to raadsvergaderingen).

```sql
SELECT COALESCE(doc_classification, '(null)') AS doc_type,
       COUNT(*) FILTER (WHERE category = 'bijlage')    AS bijlagen,
       COUNT(*) FILTER (WHERE category = 'annotatie')  AS annotaties,
       COUNT(*) FILTER (WHERE category IS NULL)        AS uncategorized,
       COUNT(*) AS total
FROM documents
GROUP BY doc_classification
ORDER BY total DESC
LIMIT 50;
```

Baseline (top rows by doc_classification total — note: `category` column is NOT the bijlage/annotatie field; `doc_classification` is — see schema caveat below):

| doc_classification | total |
|---|---|
| (null) | 22,480 |
| motie | 10,796 |
| bijlage | 10,138 |
| brief_college | 5,741 |
| schriftelijke_vraag | 3,987 |
| agenda | 3,709 |
| raadsvoorstel | 3,673 |
| toezegging | 3,657 |
| verslag | 3,560 |
| rapport | 3,294 |
| begroting | 2,401 |
| annotatie | 2,008 |
| adviezenlijst | 1,565 |
| notitie | 1,294 |
| notulen | 1,177 |

**IMPORTANT schema caveat:** `documents.category` defaults to `'meeting'` and is used as document TYPE (meeting/municipal_doc/financial/committee_transcript) — NOT as bijlage/annotatie. The Phase D1 UI split and all A5/A8 queries must use `doc_classification` instead. Queries in this doc that reference `category='bijlage'` should be updated to `doc_classification='bijlage'`.

---

## A6 — Meetings with 0 agenda_items (likely data issues)

```sql
SELECT EXTRACT(YEAR FROM m.start_date)::int AS yr,
       COUNT(*) AS zero_agenda_meetings
FROM meetings m
LEFT JOIN agenda_items ai ON ai.meeting_id = m.id
WHERE m.start_date IS NOT NULL
  AND EXTRACT(YEAR FROM m.start_date) BETWEEN 2023 AND 2026
GROUP BY m.id, yr
HAVING COUNT(ai.id) = 0
ORDER BY yr;
```

Aggregate form:

```sql
SELECT yr, COUNT(*) AS zero_agenda_meetings
FROM (
    SELECT m.id, EXTRACT(YEAR FROM m.start_date)::int AS yr, COUNT(ai.id) AS a_cnt
    FROM meetings m
    LEFT JOIN agenda_items ai ON ai.meeting_id = m.id
    WHERE EXTRACT(YEAR FROM m.start_date) BETWEEN 2023 AND 2026
    GROUP BY m.id, yr
) x
WHERE a_cnt = 0
GROUP BY yr
ORDER BY yr;
```

Baseline:

| yr | zero_agenda_meetings |
|---|---|
| 2023 | 46 |
| 2024 | 137 |
| 2025 | 197 |
| 2026 | 53 |

---

## A7 — Logical-duplicate meetings groupable by (municipality, name, start_date, committee)

Superset of A4. Uses only the fields planned for the `0012_meeting_logical_unique`
unique constraint (`NULLS NOT DISTINCT`). A7 == 0 is a hard precondition for
that migration.

```sql
SELECT
    -- municipality not yet a column pre-WS13; coerce to 'rotterdam'
    'rotterdam' AS municipality,
    name,
    start_date::date AS d,
    committee,
    COUNT(*) AS copies,
    array_agg(id ORDER BY id) AS ids
FROM meetings
WHERE start_date IS NOT NULL
GROUP BY 1, 2, 3, 4
HAVING COUNT(*) > 1
ORDER BY d DESC, copies DESC
LIMIT 500;
```

Aggregate:

```sql
SELECT COUNT(*) AS logical_dup_groups,
       SUM(copies - 1) AS rows_to_merge
FROM (
    SELECT COUNT(*) AS copies
    FROM meetings
    WHERE start_date IS NOT NULL
    GROUP BY name, start_date::date, committee
    HAVING COUNT(*) > 1
) x;
```

Baseline: **116 logical_dup_groups**, **135 rows_to_merge** (all years). This is the number B5 must bring to 0.

---

## A8 — Docs classified `bijlage` vs `annotatie` vs NULL per year

WS11 classifier coverage by year. Untyped docs render badly in the new UI
(Phase D1 visual split) — this tells us how big the backfill population is.

```sql
SELECT EXTRACT(YEAR FROM COALESCE(m.start_date, d.created_at))::int AS yr,
       COUNT(*) FILTER (WHERE d.category = 'bijlage')    AS bijlagen,
       COUNT(*) FILTER (WHERE d.category = 'annotatie')  AS annotaties,
       COUNT(*) FILTER (WHERE d.category IS NULL)        AS uncategorized,
       COUNT(*)                                          AS total
FROM documents d
LEFT JOIN document_assignments da ON da.document_id = d.id
LEFT JOIN meetings m ON m.id = da.meeting_id
WHERE EXTRACT(YEAR FROM COALESCE(m.start_date, d.created_at)) BETWEEN 2023 AND 2026
GROUP BY yr
ORDER BY yr;
```

Baseline:

| year | bijlagen | annotaties | uncategorized | total |
|---|---|---|---|---|
| 2023 | 1,712 | 166 | 1,258 | 8,918 |
| 2024 | 1,361 | 156 | 3,071 | 9,695 |
| 2025 | 41 | 163 | 3,213 | 6,474 |
| 2026 | 4 | 39 | 1,391 | 1,760 |

> **Note:** uses `doc_classification` (corrected from `category`). 2025-2026 severely under-classified — WS11 B3 expansion is the fix.

---

## Run instructions (for Dennis)

```bash
# 0. Confirm tunnel up
ps aux | grep "ssh.*178.104" | grep -v grep

# 1. psql read-only session
PGPASSWORD="$(grep ^DB_PASSWORD .env | cut -d= -f2)" \
    psql -h 127.0.0.1 -p 5432 -U postgres -d neodemos \
         -v ON_ERROR_STOP=1 \
         --set AUTOCOMMIT=on \
         -c "SET default_transaction_read_only = on;"

# 2. Or via python read-only:
python3 - <<'PY'
import os, psycopg2
from dotenv import load_dotenv
load_dotenv()
conn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'),
                        dbname=os.getenv('DB_NAME'),
                        user=os.getenv('DB_USER'),
                        password=os.getenv('DB_PASSWORD'))
conn.set_session(readonly=True)
cur = conn.cursor()
# paste A1..A8 SQL above
PY
```

Paste results back into this file, then commit. Phase B planning reads these
baselines directly (see `docs/handoffs/WS14_CALENDAR_QUALITY.md` §Phase B).

---

## Schema caveats noticed while writing these queries

- `meetings` has no explicit `source` column today; iBabs-vs-ORI provenance
  is only discoverable via the id format. If WS5a later adds `source`,
  update A4 to use it directly (cleaner than `array_agg(id)` inspection).
- `documents.category` is the `bijlage`/`annotatie` classifier column (WS11).
  If WS11 renamed it, remap A5/A8 accordingly before running.
- `municipality` is a `documents` column but **not** on `meetings` yet —
  WS13 adds it. A7 currently hardcodes `'rotterdam'` in the projection.
- `document_assignments` uses `(document_id, meeting_id, agenda_item_id)` as
  the logical triple. NULL is a valid value for `meeting_id` or
  `agenda_item_id` — A2/A3 account for this with `IS NULL` predicates.
