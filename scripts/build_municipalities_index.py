#!/usr/bin/env python3
"""
build_municipalities_index.py — Build a pre-computed registry of all Dutch municipalities
from ORI, including backend system (iBabs/Notubiz/Parlaeus), doc counts, and coverage dates.

Output: data/municipalities_index.json

Usage:
    python scripts/build_municipalities_index.py                    # full build (~3-4 min)
    python scripts/build_municipalities_index.py --force            # ignore existing file
    python scripts/build_municipalities_index.py --gemeente rotterdam  # update single entry
    python scripts/build_municipalities_index.py --output /path/to/out.json

When to run:
    - Once before starting WS13 (Phase 0)
    - After ORI rotates index names (~monthly) — run with --force
    - After onboarding a new gemeente — run with --gemeente <name> to refresh that entry
    - Weekly via nightly cron (WS5a) with --force

Algorithm:
    1. GET _cat/indices → extract all ori_* indices (one API call for all ~342 municipalities)
    2. For each municipality in async batches of 20:
       a. Sample 2 MediaObject docs → read original_url → detect backend system
       b. Date range aggregation on Meeting objects → ori_earliest, ori_latest
       c. Financial keyword count → financial_docs_count + pdf_accessible probe
    3. Compute phase readiness flags from ori_earliest
    4. Write data/municipalities_index.json (atomic write via temp file)

Rate limiting: 1 req/sec per concurrent slot (20 slots = 20 req/sec peak).
Total: ~1026 API calls. Runtime: ~3-4 minutes.

This file is safe to commit — it is reference data, not generated pipeline state.
Do not store it in data/pipeline_state/ (that directory is gitignored).
"""

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_PATH = DATA_DIR / "municipalities_index.json"

ORI_BASE = "https://api.openraadsinformatie.nl/v1/elastic"
TIMEOUT = httpx.Timeout(20.0)
CONCURRENCY = 20          # parallel municipality queries
RATE_LIMIT_DELAY = 0.05   # seconds between requests per slot (~20 req/s total)

FINANCIAL_KEYWORDS = [
    "programmabegroting", "begroting", "jaarstukken",
    "jaarrekening", "voorjaarsnota", "10-maandsrapportage",
]

# Phase 1 covers 2018-present. If ORI starts after this year, flag a gap.
PHASE_1_START_YEAR = 2018
# Phase 2 covers 2000-2018. ORI starting after 2012 means a noticeable pre-ORI gap.
PHASE_2_GAP_THRESHOLD = 2012


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

def detect_backend(original_url: str) -> tuple[str, str | None]:
    """
    Returns (backend, ibabs_site_name).
    backend: 'ibabs' | 'notubiz' | 'parlaeus' | 'go' | 'unknown'
    ibabs_site_name: e.g. 'RotterdamRaad' (from ?site=X), or None
    """
    if not original_url:
        return "unknown", None
    if "ibabs.eu" in original_url or "bestuurlijkeinformatie.nl" in original_url:
        site_name = None
        if "site=" in original_url:
            try:
                site_name = original_url.split("site=")[1].split("&")[0]
            except IndexError:
                pass
        return "ibabs", site_name
    if "notubiz.nl" in original_url:
        return "notubiz", None
    if "parlaeus.nl" in original_url:
        return "parlaeus", None
    if "gemeenteoplossingen.nl" in original_url:
        return "go", None
    return "unknown", None


# ---------------------------------------------------------------------------
# Phase flag computation
# ---------------------------------------------------------------------------

def compute_phases(ori_earliest: str | None, ori_backend: str) -> dict[str, Any]:
    """
    Determine which corpus phases are ready for this municipality.

    Phase 1 (2018–present): covered if ORI starts at or before 2018.
    Phase 2 (2000–2018):    covered if ORI starts at or before 2000,
                            partial if ORI starts 2000–2018,
                            blocked if ORI starts after 2018 AND no native adapter.
    Phase 3 (pre-2000):     almost always not_planned.
    """
    if not ori_earliest:
        return {
            "phase_1": {"range": "2018-2026", "sources": ["ori"], "status": "unknown"},
            "phase_2": {"range": "2000-2018", "sources": [], "status": "unknown"},
            "phase_3": {"range": "pre-2000",  "sources": [], "status": "not_planned"},
        }

    try:
        ori_year = int(ori_earliest[:4])
    except (ValueError, IndexError):
        ori_year = 9999

    # Phase 1
    if ori_year <= PHASE_1_START_YEAR:
        p1_status = "ready"
        p1_sources = ["ori", ori_backend] if ori_backend != "unknown" else ["ori"]
    else:
        p1_status = "partial"   # ORI starts mid-2018 or later — some 2018 coverage missing
        p1_sources = ["ori"]

    # Phase 2
    if ori_year <= 2000:
        p2_status = "ready"
        p2_sources = ["ori"]
    elif ori_year <= PHASE_2_GAP_THRESHOLD:
        # ORI covers some of 2000-2018
        p2_status = "partial"
        gap_note = f"ORI covers {ori_earliest[:10]}–2018; pre-{ori_year} needs {ori_backend} adapter"
        p2_sources = ["ori"]
    else:
        # ORI starts after 2012 — large pre-ORI gap
        adapter_version = {
            "ibabs": "notubiz_archive",
            "notubiz": "notubiz_adapter_v0.3.0",
            "parlaeus": "parlaeus_adapter_v0.3.0",
        }.get(ori_backend, "unknown_adapter")
        p2_status = "blocked"
        gap_note = (
            f"ORI starts {ori_earliest[:10]}. Pre-{ori_year} docs require {adapter_version}."
            + (" Rotterdam: check rotterdam.raadsinformatie.nl" if ori_year >= 2018 else "")
        )
        p2_sources = [adapter_version]

    phases: dict[str, Any] = {
        "phase_1": {
            "range": f"{PHASE_1_START_YEAR}-2026",
            "sources": list(dict.fromkeys(p1_sources)),  # dedup while preserving order
            "status": p1_status,
        },
        "phase_2": {
            "range": "2000-2018",
            "sources": p2_sources,
            "status": p2_status,
        },
        "phase_3": {
            "range": "pre-2000",
            "sources": ["unknown"],
            "status": "not_planned",
        },
    }
    if p2_status in ("blocked", "partial") and "gap_note" in dir():
        phases["phase_2"]["note"] = gap_note  # type: ignore[assignment]

    return phases


# ---------------------------------------------------------------------------
# Per-municipality enrichment
# ---------------------------------------------------------------------------

async def enrich_municipality(
    client: httpx.AsyncClient,
    gemeente: str,
    index: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """
    Gather backend, date range, and financial doc count for one municipality.
    Three HTTP calls total: backend sample + date range + financial count.
    """
    entry: dict[str, Any] = {
        "ori_index": index,
        "ori_backend": "unknown",
        "ibabs_site_name": None,
        "ori_doc_count": 0,
        "ori_earliest": None,
        "ori_latest": None,
        "financial_docs_count": 0,
        "financial_pdf_accessible": False,
        "phases": {},
        "tenant_config": f"data/tenants/{gemeente}/config.yml",
        "error": None,
    }

    async with semaphore:
        try:
            await asyncio.sleep(RATE_LIMIT_DELAY)

            # ── 1. Backend detection: sample 2 MediaObject docs ──────────────
            backend_resp = await client.post(
                f"{ORI_BASE}/{index}/_search",
                json={
                    "size": 3,
                    "_source": ["original_url"],
                    "query": {"term": {"@type": "MediaObject"}},
                },
            )
            if backend_resp.status_code == 200:
                hits = backend_resp.json()["hits"]["hits"]
                for hit in hits:
                    url = hit["_source"].get("original_url", "")
                    backend, site_name = detect_backend(url)
                    if backend != "unknown":
                        entry["ori_backend"] = backend
                        entry["ibabs_site_name"] = site_name
                        break

            await asyncio.sleep(RATE_LIMIT_DELAY)

            # ── 2. All counts + date range via match_all + filter aggs ───────
            # Use match_all so aggregations span the full index (not a filtered subset).
            # global() agg breaks out of any query scope — belt-and-suspenders here.
            combined_resp = await client.post(
                f"{ORI_BASE}/{index}/_search",
                json={
                    "size": 0,
                    "query": {"match_all": {}},
                    "aggs": {
                        "meeting_earliest": {
                            "filter": {"term": {"@type": "Meeting"}},
                            "aggs": {"min_date": {"min": {"field": "start_date"}}},
                        },
                        "meeting_latest": {
                            "filter": {"term": {"@type": "Meeting"}},
                            "aggs": {"max_date": {"max": {"field": "start_date"}}},
                        },
                        "total_media": {
                            "filter": {"term": {"@type": "MediaObject"}},
                        },
                        "financial_docs": {
                            "filter": {
                                "bool": {
                                    "must": [{"term": {"@type": "MediaObject"}}],
                                    "should": [
                                        {"match": {"name": kw}} for kw in FINANCIAL_KEYWORDS
                                    ],
                                    "minimum_should_match": 1,
                                }
                            },
                        },
                    },
                },
            )

            if combined_resp.status_code == 200:
                aggs = combined_resp.json().get("aggregations", {})

                earliest_val = (
                    aggs.get("meeting_earliest", {})
                    .get("min_date", {})
                    .get("value_as_string", "")
                )
                latest_val = (
                    aggs.get("meeting_latest", {})
                    .get("max_date", {})
                    .get("value_as_string", "")
                )
                entry["ori_earliest"] = (earliest_val or "")[:10] or None
                entry["ori_latest"] = (latest_val or "")[:10] or None
                entry["ori_doc_count"] = aggs.get("total_media", {}).get("doc_count", 0)
                fin_count = aggs.get("financial_docs", {}).get("doc_count", 0)
                entry["financial_docs_count"] = fin_count

            await asyncio.sleep(RATE_LIMIT_DELAY)

            # ── 3. Financial PDF accessibility probe ──────────────────────────
            # Separate call: fetch one financial doc and HEAD its original_url.
            if entry["financial_docs_count"] > 0:
                fin_resp = await client.post(
                    f"{ORI_BASE}/{index}/_search",
                    json={
                        "size": 1,
                        "_source": ["original_url"],
                        "query": {
                            "bool": {
                                "must": [{"term": {"@type": "MediaObject"}}],
                                "should": [
                                    {"match": {"name": kw}} for kw in FINANCIAL_KEYWORDS
                                ],
                                "minimum_should_match": 1,
                            }
                        },
                    },
                )
                if fin_resp.status_code == 200:
                    fin_hits = fin_resp.json()["hits"]["hits"]
                    if fin_hits:
                        pdf_url = fin_hits[0]["_source"].get("original_url", "")
                        if pdf_url:
                            try:
                                head = await client.head(pdf_url, follow_redirects=True)
                                entry["financial_pdf_accessible"] = head.status_code == 200
                            except Exception:
                                entry["financial_pdf_accessible"] = False

        except Exception as exc:
            entry["error"] = str(exc)

    entry["phases"] = compute_phases(entry["ori_earliest"], entry["ori_backend"])
    return entry


# ---------------------------------------------------------------------------
# Main: fetch all indices, enrich in batches
# ---------------------------------------------------------------------------

async def build_index(
    output_path: Path,
    force: bool = False,
    single_gemeente: str | None = None,
) -> None:
    existing: dict[str, Any] = {"municipalities": {}}
    if not force and output_path.exists():
        with open(output_path) as f:
            existing = json.load(f)
        if single_gemeente:
            print(f"Refreshing single entry: {single_gemeente}")
        else:
            print(
                f"Index exists ({len(existing['municipalities'])} entries). "
                "Use --force to rebuild from scratch."
            )
            if not single_gemeente:
                return

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        # Step 1: fetch all ORI indices
        print("Fetching ORI index list...")
        resp = await client.get(f"{ORI_BASE}/_cat/indices?format=json")
        resp.raise_for_status()
        all_indices = resp.json()

        ori_indices: list[tuple[str, str, int]] = []  # (gemeente, index_name, raw_doc_count)
        for idx in all_indices:
            name = idx.get("index", "")
            if not name.startswith("ori_"):
                continue
            # Extract gemeente name: ori_{gemeente}_{timestamp}
            # Some have multi-word names: ori_den_haag_*, ori_amsterdam_centrum_*
            parts = name.split("_")
            # Last part is the timestamp (14 digits), rest is gemeente
            if len(parts) < 3:
                continue
            gemeente = "_".join(parts[1:-1])  # e.g. "den_haag", "rotterdam"
            # Use the most recent index for each gemeente (sort desc by timestamp suffix)
            raw_count = int(idx.get("docs.count", 0) or 0)
            ori_indices.append((gemeente, name, raw_count))

        # Keep only the most recent index per gemeente
        gemeente_to_idx: dict[str, tuple[str, int]] = {}
        for gemeente, index_name, raw_count in sorted(ori_indices, key=lambda x: x[1], reverse=True):
            if gemeente not in gemeente_to_idx:
                gemeente_to_idx[gemeente] = (index_name, raw_count)

        if single_gemeente:
            # Filter to just the requested municipality
            target = single_gemeente.lower().replace("-", "_").replace(" ", "_")
            gemeente_to_idx = {k: v for k, v in gemeente_to_idx.items() if k == target}
            if not gemeente_to_idx:
                print(f"ERROR: '{single_gemeente}' not found in ORI indices", file=sys.stderr)
                sys.exit(1)

        total = len(gemeente_to_idx)
        print(f"Found {total} municipalities to enrich...")

        # Step 2: enrich each municipality
        semaphore = asyncio.Semaphore(CONCURRENCY)
        tasks = {
            gemeente: asyncio.create_task(
                enrich_municipality(client, gemeente, index_name, semaphore)
            )
            for gemeente, (index_name, _raw) in gemeente_to_idx.items()
        }

        municipalities: dict[str, Any] = dict(existing["municipalities"])
        done = 0
        for gemeente, task in tasks.items():
            entry = await task
            # Preserve display_name from existing entry if present
            if gemeente in municipalities and "display_name" in municipalities[gemeente]:
                entry["display_name"] = municipalities[gemeente]["display_name"]
            else:
                entry["display_name"] = gemeente.replace("_", " ").title()
            municipalities[gemeente] = entry
            done += 1
            if done % 20 == 0 or done == total:
                print(f"  {done}/{total} done...")

    # Compute summary
    backend_summary: dict[str, int] = {}
    for entry in municipalities.values():
        b = entry.get("ori_backend", "unknown")
        backend_summary[b] = backend_summary.get(b, 0) + 1

    output: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ori_base": ORI_BASE,
        "total_municipalities": len(municipalities),
        "backend_summary": dict(sorted(backend_summary.items(), key=lambda x: -x[1])),
        "municipalities": dict(sorted(municipalities.items())),
    }

    # Atomic write via temp file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=output_path.parent, suffix=".json.tmp", delete=False
    ) as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
        tmp_path = Path(f.name)
    tmp_path.replace(output_path)

    print(f"\nWrote {output_path}")
    print(f"  Total: {len(municipalities)} municipalities")
    for backend, count in output["backend_summary"].items():
        print(f"  {backend}: {count}")

    # Print phase 1 readiness summary
    phase1_blocked = [
        g for g, e in municipalities.items()
        if e.get("phases", {}).get("phase_1", {}).get("status") != "ready"
    ]
    if phase1_blocked:
        print(f"\nPhase 1 not fully ready ({len(phase1_blocked)} municipalities):")
        for g in phase1_blocked[:10]:
            e = municipalities[g]
            print(f"  {g}: ORI from {e.get('ori_earliest', 'unknown')}")
        if len(phase1_blocked) > 10:
            print(f"  ... and {len(phase1_blocked) - 10} more")

    phase2_gaps = [
        g for g, e in municipalities.items()
        if e.get("phases", {}).get("phase_2", {}).get("status") == "blocked"
    ]
    print(f"\nPhase 2 gaps (need Notubiz/Parlaeus adapter): {len(phase2_gaps)} municipalities")
    if phase2_gaps[:5]:
        for g in phase2_gaps[:5]:
            e = municipalities[g]
            print(f"  {g}: ORI from {e.get('ori_earliest', 'unknown')}, backend={e.get('ori_backend')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--force", action="store_true", help="Rebuild even if output exists")
    parser.add_argument("--gemeente", help="Refresh a single municipality entry only")
    args = parser.parse_args()

    asyncio.run(build_index(args.output, force=args.force, single_gemeente=args.gemeente))


if __name__ == "__main__":
    main()
