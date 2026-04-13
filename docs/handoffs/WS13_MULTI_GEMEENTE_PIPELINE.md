# WS13 — Multi-Gemeente Pipeline: Tenant-Aware Ingestion

> **Priority:** 1 (gates Middelburg press launch + all v0.2.1 expansion)
> **Status:** `not started`
> **Owner:** `unassigned`
> **Target release:** v0.2.1
> **Depends on:** WS5a (nightly pipeline stable for Rotterdam), `scripts/discover_gemeente.py` ✅ done

---

## TL;DR

Every service in the current pipeline is hardcoded to Rotterdam. `IBabsService.BASE_URL`, `OpenRaadService` index patterns, `BulkOrchestrator` URL templates — all hardcoded. This workstream:

1. Builds a **municipalities registry** (`data/municipalities_index.json`) — a pre-computed map of all 342 Dutch municipalities with backend system, doc counts, and ORI coverage dates. Costs ~3 minutes to generate. Lets any agent add a city without guessing.
2. Replaces every hardcoded Rotterdam reference with a `gemeente` parameter driven by `data/tenants/<gemeente>/config.yml`.
3. Ships `pipeline/sources/` adapter package + `scripts/onboard_gemeente.py` — run one script on Hetzner, get a new city indexed end-to-end.

---

## Architecture: three sources, not one

ORI aggregates all Dutch municipal portals. Never treat them as alternatives.

| Source | API | Role | Freshness | v0.2.1 adapter |
|---|---|---|---|---|
| **iBabs** | `api1.ibabs.eu` | Current docs, rich structure, VTT/webcast | Live | ✅ `IBabsService` — needs gemeente param |
| **Notubiz** | `api.notubiz.nl` | Current for Notubiz cities; historical archive for iBabs-migrated cities | Live | ❌ v0.3.0 — use ORI as proxy |
| **Parlaeus** | `{gemeente}.parlaeus.nl` | Active for ~25 municipalities (Apeldoorn, Maastricht confirmed) | Live | ❌ v0.3.0 — use ORI as proxy |
| **ORI** | `api.openraadsinformatie.nl` | Aggregates all above; 1–3 month lag; **342/342 municipalities indexed** | Lagging | ✅ `OpenRaadService` — needs gemeente param |

**ORI `original_url` is the authoritative backend detector.** Sample 2–3 docs from any ORI index and read `original_url`:
- `api1.ibabs.eu/publicdownload.aspx?site=<SiteName>` → iBabs; `SiteName` is the iBabs API site key
- `api.notubiz.nl/document/...` → Notubiz
- `{gemeente}.parlaeus.nl/...` → Parlaeus

This is more reliable than HEAD-probing portals directly. HEAD probes to `bestuurlijkeinformatie.nl` gave **false positives** for Parlaeus cities — Apeldoorn and Maastricht both have iBabs subdomains but their ORI index is fed from Parlaeus.

---

## Corpus coverage roadmap

ORI date coverage depends on the backend and when ORI first indexed the municipality.

| Phase | Date range | Source | Status |
|---|---|---|---|
| **Phase 1** (v0.2.1) | 2018–2026 | ORI for all; iBabs native for current | Default scope — most municipalities fully covered |
| **Phase 2** (v0.3.0) | 2000–2018 | Notubiz/Parlaeus native adapters | Needed for iBabs cities with late ORI start dates |
| **Phase 3** (v0.4+) | pre-2000 | Direct municipal archives, manual export | Scope TBD |

**ORI coverage start dates (verified live 2026-04-13):**

| Municipality | ORI backend | ORI starts | Phase 1 ready | Phase 2 gap |
|---|---|---|---|---|
| Rotterdam | iBabs | **2018-01-01** | ✅ | ✅ large — pre-2018 only in `rotterdam.raadsinformatie.nl` |
| Middelburg | Notubiz | 2010-01-05 | ✅ | None |
| Zoetermeer | iBabs | 2014-01-07 | ✅ | Small (~4 years) |
| Enschede | iBabs | 2012-01-09 | ✅ | Small (~2 years) |
| Apeldoorn | **Parlaeus** | 2011-11-03 | ✅ | None |
| Maastricht | **Parlaeus** | 2010-01-11 | ✅ | None |
| Amsterdam | Notubiz | 2010-01-14 | ✅ | None |
| Den Haag | Notubiz | 2010-01-04 | ✅ | None |
| Utrecht | iBabs | 2010-01-05 | ✅ | None (early adopter) |
| Hilversum | iBabs | 2012-03-06 | ✅ | Small |
| Waalwijk | Notubiz | 2010-03-09 | ✅ | None |

**Rotterdam is the only city with a large Phase 2 gap** — it migrated to iBabs ~2018 and ORI only covers the iBabs era. The pre-2018 Notubiz archive at `rotterdam.raadsinformatie.nl` is a separate corpus not indexed by ORI.

---

## Municipalities registry: `data/municipalities_index.json`

A pre-computed snapshot of all 342 municipalities. Generated in ~3–4 minutes using async batch queries to ORI. Agents read this before starting work on a new city — no live API calls needed.

### JSON schema

```json
{
  "generated_at": "2026-04-13T20:34:25Z",
  "ori_base": "https://api.openraadsinformatie.nl/v1/elastic",
  "total_municipalities": 309,
  "backend_summary": {"ibabs": 152, "notubiz": 119, "unknown": 36, "parlaeus": 2},
  "municipalities": {
    "rotterdam": {
      "display_name": "Rotterdam",
      "ori_index": "ori_rotterdam_20250629013104",
      "ori_backend": "ibabs",
      "ibabs_site_name": "RotterdamRaad",
      "ori_doc_count": 49724,
      "ori_earliest": "2018-01-01",
      "ori_latest": "2026-04-01",
      "financial_docs_count": 2331,
      "financial_pdf_accessible": true,
      "phases": {
        "phase_1": {"range": "2018-2026", "sources": ["ori", "ibabs"], "status": "ready"},
        "phase_2": {"range": "2000-2018", "sources": ["notubiz_archive"], "status": "blocked",
                    "note": "Pre-2018 only at rotterdam.raadsinformatie.nl — needs notubiz adapter"},
        "phase_3": {"range": "pre-2000", "sources": ["unknown"], "status": "not_planned"}
      },
      "tenant_config": "data/tenants/rotterdam/config.yml"
    },
    "middelburg": {
      "display_name": "Middelburg",
      "ori_index": "ori_middelburg_20250426193224",
      "ori_backend": "notubiz",
      "ibabs_site_name": null,
      "ori_doc_count": 28434,
      "ori_earliest": "2010-01-05",
      "ori_latest": "2026-04-01",
      "financial_docs_count": 2361,
      "financial_pdf_accessible": true,
      "phases": {
        "phase_1": {"range": "2018-2026", "sources": ["ori", "notubiz"], "status": "ready"},
        "phase_2": {"range": "2010-2018", "sources": ["ori"], "status": "partial",
                    "note": "ORI covers 2010+; pre-2010 needs notubiz adapter"},
        "phase_3": {"range": "pre-2010", "sources": ["unknown"], "status": "not_planned"}
      },
      "tenant_config": "data/tenants/middelburg/config.yml"
    }
  }
}
```

**Notes on the generated data:**
- `unknown` (36 entries): ORI indices with no `MediaObject` docs having `original_url` — typically stub indices for very small municipalities or recent mergers. Run `--gemeente <name>` to re-probe.
- `parlaeus` (2 detected): sampling artefact — the script samples 3 MediaObjects per index; Parlaeus indices with mostly `Meeting` objects miss detection. True Parlaeus count is higher (~25 estimated). The two confirmed are Apeldoorn and Maastricht.
- Phase 1 not ready (25): post-2018 merger municipalities (Westerkwartier 2019, West Betuwe 2019, Voorne aan Zee 2023) — their ORI start date is their founding date, not a data gap.
- Phase 2 blocked (117): iBabs municipalities whose ORI coverage starts after 2012. Pre-ORI docs live in each municipality's old Notubiz archive — requires Notubiz adapter (v0.3.0).

The full file lives at `data/municipalities_index.json` (committed to repo, 315KB).

Phase status values: `ready` | `partial` | `blocked` | `not_planned`

### Generation script

**`scripts/build_municipalities_index.py`** — see that file for full implementation.

```bash
# Generate (takes ~3-4 min; run from laptop or Hetzner)
python scripts/build_municipalities_index.py

# Refresh a single entry (e.g. after onboarding)
python scripts/build_municipalities_index.py --gemeente middelburg

# Force full refresh (e.g. after ORI index rotation)
python scripts/build_municipalities_index.py --force

# Output to custom path (default: data/municipalities_index.json)
python scripts/build_municipalities_index.py --output data/municipalities_index.json
```

The script queries ORI concurrently in batches of 20 (respecting 1 req/sec per batch). Three calls per municipality:
1. `_cat/indices` → index name + raw doc count (one call for all 342)
2. 2-doc sample → backend detection from `original_url`
3. Date range aggregation + financial keyword count (combined query)

Refresh this file when: ORI rotates its index names (happens after their re-index runs, indicated by the timestamp in the index name), or when adding a new expansion city.

---

## v0.2.1 expansion cities

| Gemeente | ORI backend | Strategy | v0.2.1 feasibility | Notes |
|---|---|---|---|---|
| **Middelburg** | Notubiz | `ibabs+ori` | ✅ Ready | iBabs for current; ORI complete from 2010 |
| Zoetermeer | iBabs | `ibabs+ori` | ✅ Ready | ORI from 2014; small pre-2014 gap acceptable |
| Enschede | iBabs | `ibabs+ori` | ✅ Ready | ORI from 2012 |
| Apeldoorn | **Parlaeus** | `parlaeus_via_ori` | ⚠️ ORI-only | No iBabs — ORI fallback covers 2011+; native adapter v0.3.0 |
| Maastricht | **Parlaeus** | `parlaeus_via_ori` | ⚠️ ORI-only | No iBabs — ORI fallback covers 2010+; native adapter v0.3.0 |

**Revised v0.2.1 scope (3 iBabs + 2 Parlaeus-via-ORI):**
- Middelburg, Zoetermeer, Enschede: full iBabs + ORI pipeline
- Apeldoorn, Maastricht: ORI search-only (still valuable, no native adapter needed)

---

## Dependencies

- `data/municipalities_index.json` ← generated by `scripts/build_municipalities_index.py` (run first)
- `data/tenants/<gemeente>/config.yml` ← generated by `discover_gemeente.py --save-config`
- WS5a nightly pipeline stable (advisory lock 42 pattern established)
- WS11 `municipality` + `source` columns ✅ in DB schema

## Cold-start prompt

> You are picking up WS13 (Multi-Gemeente Pipeline) of NeoDemos v0.2.1.
>
> Read in order:
> 1. This file top-to-bottom
> 2. `data/municipalities_index.json` — pre-computed registry of all 342 Dutch municipalities (backend system, doc counts, ORI coverage dates). If it doesn't exist yet, run `python scripts/build_municipalities_index.py` first (~4 min).
> 3. `data/tenants/rotterdam/config.yml` — reference config structure
> 4. `data/tenants/middelburg/config.yml` — first target gemeente
> 5. `services/ibabs_service.py` — PRIMARY refactor target: hardcoded `BASE_URL`
> 6. `services/open_raad.py` — hardcoded `"rotterdam"` in index filter
> 7. `pipeline/bulk_orchestrator.py` — hardcoded iBabs URL templates + DB queries
>
> Your job: make the pipeline gemeente-configurable. Start with Phase 0 (build the registry) if it doesn't exist, then Phase 1 (config loader + IBabsService refactor). Get Middelburg search-only working before touching BulkOrchestrator.
>
> House rules: advisory lock 42 on all writes; `--dry-run` first; `--no-recreate` on docker compose; never write to Qdrant while a background job is running.

## Files to read first

- [`data/municipalities_index.json`](../../data/municipalities_index.json) — registry (generate if missing)
- [`data/tenants/rotterdam/config.yml`](../../data/tenants/rotterdam/config.yml) — reference tenant config
- [`data/tenants/middelburg/config.yml`](../../data/tenants/middelburg/config.yml) — first target
- [`scripts/build_municipalities_index.py`](../../scripts/build_municipalities_index.py) — registry builder
- [`scripts/discover_gemeente.py`](../../scripts/discover_gemeente.py) — per-city probe + config writer
- [`services/ibabs_service.py`](../../services/ibabs_service.py) — PRIMARY refactor target
- [`services/open_raad.py`](../../services/open_raad.py) — hardcoded Rotterdam index
- [`pipeline/bulk_orchestrator.py`](../../pipeline/bulk_orchestrator.py) — hardcoded URLs + glob patterns

---

## Build tasks

### Phase 0 — Municipalities registry (~0.5 day)

**Goal:** `data/municipalities_index.json` exists and is accurate. Every subsequent phase reads from it.

- [ ] Run `python scripts/build_municipalities_index.py` — generates `data/municipalities_index.json`
  - If the script doesn't exist yet, implement it (spec in `scripts/build_municipalities_index.py` header)
  - Verify: 340+ municipalities present, rotterdam/middelburg entries look correct
- [ ] Commit `data/municipalities_index.json` to the repo (it's a reference document, not generated state)
- [ ] Add weekly refresh to nightly cron (WS5a): `python scripts/build_municipalities_index.py --force`
  - ORI rotates index names ~monthly; stale index names in the registry cause 404s

### Phase 1 — Config loader + IBabsService refactor (~1 day)

**Goal:** Middelburg meetings discoverable via iBabs. No ingest yet.

- [ ] **`services/tenant_config.py`** — new file:
  ```python
  import yaml
  from pathlib import Path
  from functools import lru_cache

  TENANTS_DIR = Path(__file__).parent.parent / "data" / "tenants"
  MUNICIPALITIES_INDEX = Path(__file__).parent.parent / "data" / "municipalities_index.json"

  @lru_cache(maxsize=32)
  def load_tenant(gemeente: str) -> dict:
      path = TENANTS_DIR / gemeente / "config.yml"
      if not path.exists():
          raise FileNotFoundError(
              f"No tenant config for '{gemeente}'. "
              f"Run: python scripts/discover_gemeente.py --gemeente {gemeente} --save-config"
          )
      with open(path) as f:
          return yaml.safe_load(f)

  def get_ibabs_domain(gemeente: str) -> str:
      cfg = load_tenant(gemeente)
      ibabs = cfg["sources"]["ibabs"]
      if not ibabs.get("enabled"):
          raise ValueError(f"Gemeente '{gemeente}' does not use iBabs")
      return ibabs["domain"]

  def get_ori_index(gemeente: str) -> str:
      cfg = load_tenant(gemeente)
      ori = cfg["sources"]["ori"]
      if not ori.get("enabled") or not ori.get("index"):
          raise ValueError(f"Gemeente '{gemeente}' has no ORI index configured")
      return ori["index"]

  def get_ori_backend(gemeente: str) -> str:
      """Returns 'ibabs' | 'notubiz' | 'parlaeus' | 'unknown'."""
      cfg = load_tenant(gemeente)
      return cfg["sources"]["ori"].get("backend", "unknown")

  def list_tenants() -> list[str]:
      return [p.name for p in TENANTS_DIR.iterdir() if (p / "config.yml").exists()]

  def lookup_municipality(gemeente: str) -> dict:
      """Fast lookup from municipalities_index.json — no API calls."""
      import json
      if not MUNICIPALITIES_INDEX.exists():
          raise FileNotFoundError(
              "data/municipalities_index.json not found. "
              "Run: python scripts/build_municipalities_index.py"
          )
      with open(MUNICIPALITIES_INDEX) as f:
          idx = json.load(f)
      entry = idx["municipalities"].get(gemeente.lower())
      if not entry:
          raise KeyError(f"'{gemeente}' not found in municipalities index. "
                         f"Run discover_gemeente.py or check spelling.")
      return entry
  ```

- [ ] **`services/ibabs_service.py`** — replace hardcoded `BASE_URL`:
  - Change constructor to `__init__(self, gemeente: str = "rotterdam")`
  - Load domain from `get_ibabs_domain(gemeente)` in `__init__`
  - Replace `BASE_URL = "https://rotterdamraad.bestuurlijkeinformatie.nl"` with `self.base_url = f"https://{domain}"`
  - Replace all `BASE_URL` references with `self.base_url`
  - **Do not change any method signatures** — the rest of the pipeline is unchanged

- [ ] **`services/open_raad.py`** — replace hardcoded Rotterdam index:
  - Accept optional `gemeente: str = "rotterdam"` in constructor
  - `ensure_index()` loads preferred index from `get_ori_index(gemeente)` if config exists
  - Fallback to name-pattern match in `_cat/indices` (existing behaviour)

- [ ] **Smoke test:**
  ```bash
  python -c "from services.ibabs_service import IBabsService; s = IBabsService('middelburg'); print(s.base_url)"
  # → https://middelburg.bestuurlijkeinformatie.nl
  python -c "from services.tenant_config import lookup_municipality; import json; print(json.dumps(lookup_municipality('rotterdam'), indent=2))"
  # → rotterdam entry from municipalities_index.json
  ```

### Phase 1.5 — `discover_gemeente.py --full-scope` (~0.5 day)

**Goal:** Before bulk ingestion, enumerate ALL available sources concurrently, cross-reference by canonical URL, produce a deduped scope manifest.

```python
async def full_scope_audit(gemeente: str, sources: dict) -> dict:
    """
    Paginates ALL available sources concurrently (asyncio.gather).
    Dedup key: normalize_url(doc.original_url or doc.url)
    Priority order for dedup: ibabs > notubiz > ori

    Buckets written to data/pipeline_state/{gemeente}_scope.json:
      primary_only   — doc only in highest-priority source
      secondary_gap  — doc in lower-priority source only (historical gap)
      overlap        — same doc in multiple sources (ingest once from primary)

    Rotterdam note: ORI original_url → ibabs.eu, Notubiz original_url → notubiz.nl.
    These are different URL namespaces so they never match — Rotterdam correctly gets
    ibabs_only + notubiz_only buckets (two separate corpora, not duplicates).
    """
```

- [ ] Implement `full_scope_audit()` in `discover_gemeente.py` (read-only)
- [ ] Add `--full-scope` CLI flag; output manifest to `data/pipeline_state/{gemeente}_scope.json`
- [ ] `onboard_gemeente.py` checks for scope manifest; uses `--use-manifest` to skip re-discovery

### Phase 2 — `pipeline/sources/` adapter package (~1.5 days)

**Goal:** Clean adapter interface consumed by `onboard_gemeente.py`.

- [ ] **`pipeline/sources/__init__.py`** — empty
- [ ] **`pipeline/sources/base.py`** — abstract base:
  ```python
  from abc import ABC, abstractmethod

  class SourceAdapter(ABC):
      def __init__(self, gemeente: str):
          self.gemeente = gemeente

      @abstractmethod
      async def discover_meetings(self, year: int) -> list[dict]:
          """Return [{id, name, date, url, doc_count}]"""

      @abstractmethod
      async def fetch_documents(self, meeting_id: str) -> list[dict]:
          """Return [{id, name, url, content_type, doc_type}]"""

      @abstractmethod
      async def download_pdf(self, url: str) -> bytes:
          """Download document PDF. Returns raw bytes."""

      @abstractmethod
      async def list_all_documents(self, year_from: int, year_to: int) -> list[dict]:
          """Full enumeration for --full-scope audit. Returns [{id, url, name, date, doc_type}]"""

      @property
      @abstractmethod
      def source_name(self) -> str:
          """'ibabs' | 'notubiz' | 'ori' | 'parlaeus'"""
  ```

- [ ] **`pipeline/sources/ibabs.py`** — wraps `IBabsService`:
  - `discover_meetings(year)` → `IBabsService(gemeente).get_meetings_for_year(year)`
  - `fetch_documents(meeting_id)` → `IBabsService(gemeente).get_agenda_documents(meeting_id)`
  - `list_all_documents(year_from, year_to)` → iterates years, collects all doc refs
  - `download_pdf(url)` → `httpx.get(url)` (iBabs URLs are public)

- [ ] **`pipeline/sources/ori_fallback.py`** — ORI as primary or gap-fill:
  - `discover_meetings(year)` → ORI `Meeting` objects for gemeente index
  - `fetch_documents(meeting_id)` → ORI `MediaObject` linked to meeting
  - `list_all_documents(year_from, year_to)` → paginated scan, 500/page; used by `--full-scope`
  - `download_pdf(url)` → `httpx.get(url)` (ORI `original_url` values are public)
  - Uses `get_ori_index(gemeente)` — falls back to `lookup_municipality(gemeente)["ori_index"]`

- [ ] **`pipeline/sources/notubiz.py`** — v0.3.0 stub:
  ```python
  class NotubizAdapter(SourceAdapter):
      async def discover_meetings(self, year):
          raise NotImplementedError("Notubiz native adapter ships in v0.3.0 — use ORI as proxy")
      # ... all methods raise NotImplementedError
      @property
      def source_name(self): return "notubiz"
  ```

- [ ] **`pipeline/sources/parlaeus.py`** — v0.3.0 stub (same pattern as Notubiz)

- [ ] **`pipeline/sources/factory.py`**:
  ```python
  def get_adapters(gemeente: str) -> list[SourceAdapter]:
      """Return ordered list per recommended_strategy. Primary source first."""
      cfg = load_tenant(gemeente)
      strategy = cfg.get("recommended_strategy", "ori")
      adapters = []
      if cfg["sources"]["ibabs"].get("enabled"):
          adapters.append(IBabsAdapter(gemeente))
      if cfg["sources"]["notubiz"].get("enabled"):
          adapters.append(NotubizAdapter(gemeente))   # NotImplementedError in v0.2.1
      if cfg["sources"]["parlaeus"].get("enabled"):
          adapters.append(ParlaeusAdapter(gemeente))  # NotImplementedError in v0.2.1
      if cfg["sources"]["ori"].get("enabled"):
          adapters.append(ORIFallbackAdapter(gemeente))
      if not adapters:
          raise ValueError(f"No enabled source adapter for gemeente '{gemeente}'")
      return adapters

  def get_primary_adapter(gemeente: str) -> SourceAdapter:
      return get_adapters(gemeente)[0]
  ```

### Phase 3 — BulkOrchestrator gemeente parameter (~1 day)

**Goal:** `BulkOrchestrator` works for any gemeente.

- [ ] **`pipeline/bulk_orchestrator.py`**:
  - Constructor: `__init__(self, gemeente: str = "rotterdam", year: int = ...)`
  - Replace `https://rotterdamraad.bestuurlijkeinformatie.nl/Agenda/Index/{guid}` with `f"https://{get_ibabs_domain(gemeente)}/Agenda/Index/{guid}"`
  - Replace glob `output/transcripts/gemeenterotterdam_*.json` with `output/transcripts/{gemeente}_*.json`
  - Replace hardcoded `meetings` DB query with `WHERE municipality = %s` filter
  - State file: `data/pipeline_state/pipeline_state_{gemeente}_{year}.json`

- [ ] **Smoke test:** `BulkOrchestrator(gemeente="middelburg", year=2025).discover_meetings()` returns Middelburg meetings, not Rotterdam

### Phase 4 — `scripts/onboard_gemeente.py` (~1 day)

**Goal:** Single command on Hetzner adds a new municipality end-to-end.

```
Usage:
  python scripts/onboard_gemeente.py --gemeente middelburg [--dry-run] [--financial-only] [--phase 1]

Steps (in order):
  1. Load municipalities_index.json → verify gemeente known
  2. Load or generate tenant config (runs discover_gemeente.py if config missing)
  3. Run --full-scope to produce scope manifest (skip with --use-manifest if exists)
  4. For each year in phase range (default Phase 1: current-2 → current):
     a. Discover meetings via primary SourceAdapter
     b. For each meeting: fetch doc list, download PDFs, run SmartIngestor
     c. Financial docs: run financial_ingestor.py on keyword-matching PDFs
  5. Embed + upsert to Qdrant (with advisory lock 42)
  6. Smoke test: BM25 search for gemeente name → ≥ 5 results
  7. Print onboarding report (doc count, financial_lines, embedding cost, elapsed)

Flags:
  --phase 1|2|3     corpus phase (controls date range; default 1 = 2018-present)
  --dry-run         discover + print; no DB/Qdrant writes
  --financial-only  only financial extraction (skip council docs)
  --year YYYY       restrict to single year
  --resume          skip docs already in DB (by source_url)
  --limit N         process at most N documents (smoke test mode)
  --use-manifest    skip full-scope re-enumeration; use existing scope.json
```

```bash
# Server-side execution (Hetzner, no laptop needed)
docker exec neodemos-web python scripts/build_municipalities_index.py --gemeente middelburg
docker exec neodemos-web python scripts/discover_gemeente.py --gemeente middelburg --save-config
docker exec neodemos-web python scripts/onboard_gemeente.py --gemeente middelburg --phase 1 --dry-run
docker exec neodemos-web python scripts/onboard_gemeente.py --gemeente middelburg --phase 1
```

### Phase 5 — ORI ingestion gemeente-aware (~0.5 day)

- [ ] `scripts/ws11b_ori_ingestion.py` — add `--gemeente` flag; default `"rotterdam"`; use `get_ori_index(gemeente)` instead of hardcoded index name
- [ ] `services/open_raad.py` — `OpenRaadService` constructor accepts `gemeente`; `ensure_index()` uses it

---

## Acceptance criteria

- [ ] `data/municipalities_index.json` exists with 340+ entries; rotterdam + middelburg entries are correct
- [ ] `python scripts/build_municipalities_index.py --gemeente rotterdam` refreshes only rotterdam entry
- [ ] `python scripts/discover_gemeente.py --gemeente middelburg` produces correct report (Notubiz ORI backend, 2010 start, financial ✅)
- [ ] `IBabsService("middelburg")` connects to Middelburg iBabs, not Rotterdam
- [ ] `BulkOrchestrator(gemeente="middelburg", year=2025).discover_meetings()` returns Middelburg meetings
- [ ] `onboard_gemeente.py --gemeente middelburg --limit 10 --dry-run` prints 10 Middelburg docs, no writes
- [ ] `onboard_gemeente.py --gemeente middelburg --financial-only` ingests begroting → `financial_lines` with `gemeente='middelburg'`
- [ ] `onboard_gemeente.py --gemeente rotterdam` works identically to before (no regression)
- [ ] All scripts run inside Docker on Hetzner: `docker exec neodemos-web python ...`

---

## What NOT to do

- Do not build a native Notubiz or Parlaeus adapter — v0.3.0 scope; stubs only
- Do not add KG enrichment for new municipalities — WS1 is Rotterdam-only for now
- Do not change `SmartIngestor` chunking/embedding logic — it is already gemeente-agnostic
- Do not add a web UI for municipality management — onboarding is a CLI-only operation
- Do not hard-code the ORI index name in any script — always load from tenant config or municipalities_index.json (index names rotate when ORI re-indexes)

---

## Risks

| Risk | Mitigation |
|---|---|
| ORI index names rotate — `ori_rotterdam_20250629013104` becomes stale | Run `build_municipalities_index.py --force` weekly; `tenant_config.py` validates index exists before using |
| iBabs API response shapes differ per gemeente | Test `IBabsService("middelburg").get_meetings_for_year(2025)` in Phase 1 before bulk run; log raw JSON on first call |
| HEAD probe false positives (Apeldoorn, Maastricht) | Trust ORI `original_url` backend detection over HEAD probes; HEAD probes are now only for reachability confirmation |
| `municipality` column not propagated through all ingest paths | WS11 added it to `documents` + `chunks` + Qdrant payload — verify `SmartIngestor.ingest_document()` passes it before bulk run |
| Advisory lock contention during Rotterdam nightly | `onboard_gemeente.py` must acquire lock 42; print clear error and exit if held |
| Rotterdam pre-2018 gap | Explicitly out of scope for v0.2.1; documented in `data/tenants/rotterdam/config.yml` as `gap_pre_2018: true` |

---

## Outcome

*To be filled in when shipped. Include: municipalities onboarded, doc counts per gemeente, financial_lines count, first working `vraag_begrotingsregel(gemeente='middelburg')` MCP query, onboarding time per city.*
