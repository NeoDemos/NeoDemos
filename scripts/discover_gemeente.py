#!/usr/bin/env python3
"""
discover_gemeente.py — Portal detection and document discovery for a new municipality.

Usage:
    python scripts/discover_gemeente.py --gemeente middelburg
    python scripts/discover_gemeente.py --gemeente middelburg --save-config
    python scripts/discover_gemeente.py --gemeente apeldoorn --output json
    python scripts/discover_gemeente.py --gemeente rotterdam --full-scope  # enumerate + dedup all sources

Three first-class sources (all probed concurrently):
    1. iBabs (bestuurlijkeinformatie.nl)   — current docs, rich structure, VTT/webcast
    2. Notubiz direct (notubiz.nl)         — current OR historical archive (if city migrated to iBabs)
    3. ORI (openraadsinformatie.nl)        — aggregated index (Notubiz + others), 1–3 month lag;
                                             role: bulk historical enumeration + gap-fill

  Parlaeus (parlaeus.nl) is also probed but has no native adapter yet (v0.3.0 scope).

Source priority for dedup (when a doc appears in multiple sources):
    iBabs > Notubiz direct > ORI

Strategy matrix (from make_decision()):
    ibabs+notubiz+ori  — all three available (mid-migration or triple-redundant)
    ibabs+ori          — iBabs primary + ORI historical gap-fill (most iBabs cities)
    ibabs+notubiz      — iBabs current + Notubiz historical direct
    ibabs              — iBabs only
    notubiz+ori        — active Notubiz municipality (ORI = lagging verification layer)
    notubiz            — Notubiz only (no ORI index found)
    ori                — ORI only (no direct portal found)
    parlaeus_via_ori   — Parlaeus city; use ORI fallback until v0.3.0
    unknown            — no portal found

--full-scope mode:
    Concurrently paginates ALL available sources, cross-references by canonical URL,
    and writes data/pipeline_state/<gemeente>_scope.json with three buckets:
        primary_only  — docs only in the highest-priority source
        secondary_gap — docs only in secondary source (historical gap-fill)
        overlap       — same doc in multiple sources (ingest once, from primary)
    onboard_gemeente.py consumes this manifest instead of re-discovering at ingest time.

Output:
    - Console: human-readable discovery report with recommended next steps
    - --output json: machine-readable JSON (used by onboard_gemeente.py)
    - --save-config: writes data/tenants/<gemeente>/config.yml

Run this BEFORE onboard_gemeente.py. It is read-only — no DB writes, no Qdrant writes.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
TENANTS_DIR = DATA_DIR / "tenants"

ORI_BASE = "https://api.openraadsinformatie.nl/v1/elastic"
TIMEOUT = httpx.Timeout(15.0)

FINANCIAL_KEYWORDS = [
    "programmabegroting", "begroting", "jaarstukken",
    "jaarrekening", "voorjaarsnota", "10-maandsrapportage",
    "financieel jaarverslag",
]

CIVIC_DOC_KEYWORDS = [
    "motie", "amendement", "raadsvoorstel", "schriftelijke vraag",
    "initiatiefvoorstel", "initiatiefnotitie",
]


# ---------------------------------------------------------------------------
# Portal probes
# ---------------------------------------------------------------------------

def _detect_backend_from_url(url: str) -> str:
    """
    Determine the source portal system from an ORI original_url.

    ORI stores the authoritative download URL for each document. The domain tells
    us exactly which backend system feeds this municipality's ORI index:

        api1.ibabs.eu/publicdownload.aspx?site=<SiteName>  → iBabs
        api.notubiz.nl/document/<id>/...                    → Notubiz
        {gemeente}.parlaeus.nl/...                          → Parlaeus
        {gemeente}.notubiz.nl/...                           → Notubiz (older URL pattern)

    This is the most reliable way to determine a municipality's portal system
    without needing a manually maintained lookup table.
    """
    if not url:
        return "unknown"
    if "ibabs.eu" in url or "bestuurlijkeinformatie.nl" in url:
        return "ibabs"
    if "notubiz.nl" in url:
        return "notubiz"
    if "parlaeus.nl" in url:
        return "parlaeus"
    if "gemeenteoplossingen.nl" in url or "go.nl" in url:
        return "go"
    return "unknown"


async def probe_ori(client: httpx.AsyncClient, gemeente: str) -> dict[str, Any]:
    """
    Check ORI for an index matching the gemeente name.

    Also detects the backend system (iBabs / Notubiz / Parlaeus) by inspecting
    the original_url field on a sample of documents. ORI indexes BOTH iBabs and
    Notubiz municipalities — 342 of ~342 Dutch municipalities are present. The
    backend field tells us which native portal adapter to pair with ORI.
    """
    result: dict[str, Any] = {
        "available": False,
        "index": None,
        "doc_count": 0,
        "document_types": {},
        "date_range": {},
        "backend": "unknown",      # "ibabs" | "notubiz" | "parlaeus" | "go" | "unknown"
        "ibabs_site_name": None,   # extracted from api1.ibabs.eu?site=<SiteName>
        "financial": {"available": False, "count": 0, "pdf_accessible": False, "sample_url": None},
        "civic_docs": {"available": False, "count": 0},
        "error": None,
    }
    try:
        resp = await client.get(f"{ORI_BASE}/_cat/indices?format=json")
        resp.raise_for_status()
        indices = resp.json()

        matches = sorted(
            [i["index"] for i in indices if gemeente.lower() in i["index"].lower() and i["index"].startswith("ori_")],
            reverse=True,
        )
        if not matches:
            return result

        index = matches[0]
        result["available"] = True
        result["index"] = index
        result["all_indices"] = matches

        # Backend detection: sample a few MediaObjects and read their original_url
        # This is the most reliable way to determine which portal system feeds this index.
        backend_resp = await client.post(
            f"{ORI_BASE}/{index}/_search",
            json={
                "size": 3,
                "_source": ["original_url"],
                "query": {"term": {"@type": "MediaObject"}},
            },
        )
        if backend_resp.status_code == 200:
            sample_hits = backend_resp.json()["hits"]["hits"]
            for hit in sample_hits:
                sample_url = hit["_source"].get("original_url", "")
                backend = _detect_backend_from_url(sample_url)
                if backend != "unknown":
                    result["backend"] = backend
                    # Extract iBabs site name from URL query param: ?site=<SiteName>
                    if backend == "ibabs" and "site=" in sample_url:
                        try:
                            site_part = sample_url.split("site=")[1].split("&")[0]
                            result["ibabs_site_name"] = site_part
                        except IndexError:
                            pass
                    break

        # Document type breakdown
        agg_resp = await client.post(
            f"{ORI_BASE}/{index}/_search",
            json={"size": 0, "aggs": {"types": {"terms": {"field": "@type", "size": 20}}}},
        )
        agg_resp.raise_for_status()
        buckets = agg_resp.json()["aggregations"]["types"]["buckets"]
        result["document_types"] = {b["key"]: b["doc_count"] for b in buckets}
        result["doc_count"] = sum(result["document_types"].values())

        # Date range (from Meeting objects)
        date_resp = await client.post(
            f"{ORI_BASE}/{index}/_search",
            json={
                "size": 0,
                "query": {"term": {"@type": "Meeting"}},
                "aggs": {
                    "earliest": {"min": {"field": "start_date"}},
                    "latest": {"max": {"field": "start_date"}},
                },
            },
        )
        if date_resp.status_code == 200:
            d = date_resp.json()["aggregations"]
            result["date_range"] = {
                "earliest": (d["earliest"].get("value_as_string") or "")[:10],
                "latest": (d["latest"].get("value_as_string") or "")[:10],
            }

        # Financial document probe
        fin_should = [{"match": {"name": kw}} for kw in FINANCIAL_KEYWORDS]
        fin_resp = await client.post(
            f"{ORI_BASE}/{index}/_search",
            json={
                "size": 3,
                "_source": ["name", "original_url", "content_type"],
                "query": {
                    "bool": {
                        "must": [{"term": {"@type": "MediaObject"}}],
                        "should": fin_should,
                        "minimum_should_match": 1,
                    }
                },
            },
        )
        if fin_resp.status_code == 200:
            fin_data = fin_resp.json()
            fin_count = fin_data["hits"]["total"]["value"]
            result["financial"]["count"] = fin_count
            result["financial"]["available"] = fin_count > 0

            # Test PDF accessibility on first hit
            hits = fin_data["hits"]["hits"]
            if hits:
                sample_url = hits[0]["_source"].get("original_url", "")
                result["financial"]["sample_url"] = sample_url
                if sample_url:
                    try:
                        head = await client.head(sample_url, follow_redirects=True)
                        result["financial"]["pdf_accessible"] = head.status_code == 200
                    except Exception:
                        result["financial"]["pdf_accessible"] = False

        # Civic document probe (moties etc.)
        civic_should = [{"match": {"name": kw}} for kw in CIVIC_DOC_KEYWORDS]
        civic_resp = await client.post(
            f"{ORI_BASE}/{index}/_search",
            json={
                "size": 0,
                "query": {
                    "bool": {
                        "must": [{"term": {"@type": "MediaObject"}}],
                        "should": civic_should,
                        "minimum_should_match": 1,
                    }
                },
            },
        )
        if civic_resp.status_code == 200:
            civic_count = civic_resp.json()["hits"]["total"]["value"]
            result["civic_docs"] = {"available": civic_count > 0, "count": civic_count}

    except Exception as e:
        result["error"] = str(e)

    return result


async def probe_ibabs(client: httpx.AsyncClient, gemeente: str) -> dict[str, Any]:
    """Check if gemeente has an iBabs portal."""
    result: dict[str, Any] = {"available": False, "domain": None, "error": None}
    domain = f"{gemeente.lower()}.bestuurlijkeinformatie.nl"
    url = f"https://{domain}"
    try:
        resp = await client.head(url, follow_redirects=True)
        result["available"] = resp.status_code in (200, 301, 302, 403)
        result["domain"] = domain if result["available"] else None
        result["status_code"] = resp.status_code
    except Exception as e:
        result["error"] = str(e)
    return result


async def probe_parlaeus(client: httpx.AsyncClient, gemeente: str) -> dict[str, Any]:
    """Check if gemeente has a Parlaeus portal."""
    result: dict[str, Any] = {"available": False, "domain": None, "error": None}
    domain = f"{gemeente.lower()}.parlaeus.nl"
    url = f"https://{domain}"
    try:
        resp = await client.head(url, follow_redirects=True)
        result["available"] = resp.status_code in (200, 301, 302, 403)
        result["domain"] = domain if result["available"] else None
        result["status_code"] = resp.status_code
    except Exception as e:
        result["error"] = str(e)
    return result


async def probe_notubiz(client: httpx.AsyncClient, gemeente: str) -> dict[str, Any]:
    """Check if gemeente has a direct Notubiz portal."""
    result: dict[str, Any] = {"available": False, "domain": None, "error": None}
    domain = f"{gemeente.lower()}.notubiz.nl"
    url = f"https://{domain}"
    try:
        resp = await client.head(url, follow_redirects=True)
        result["available"] = resp.status_code in (200, 301, 302, 403)
        result["domain"] = domain if result["available"] else None
        result["status_code"] = resp.status_code
    except Exception as e:
        result["error"] = str(e)
    return result


# ---------------------------------------------------------------------------
# Decision tree
# ---------------------------------------------------------------------------

def _detect_historical_gap(ori: dict) -> dict[str, Any]:
    """
    Detect whether ORI's coverage start date leaves a historical gap.

    ORI date coverage depends on the backend and when ORI started indexing the municipality:
    - Notubiz/Parlaeus municipalities: ORI typically starts ~2010 (no gap for modern era)
    - iBabs municipalities: ORI starts from when they migrated to iBabs (2012–2018)
      → pre-migration documents exist only in the Notubiz archive, not in ORI

    Rotterdam is the clearest example: ORI starts 2018-01-01 (iBabs migration);
    pre-2018 docs are only in rotterdam.raadsinformatie.nl (Notubiz, not indexed by ORI).

    A gap is flagged when ORI starts after 2012, meaning there are likely pre-ORI
    documents in the Notubiz archive that need separate ingestion (v0.3.0 adapter).
    """
    gap: dict[str, Any] = {
        "detected": False,
        "ori_earliest": None,
        "gap_years": 0,
        "note": None,
    }
    ori_earliest = ori.get("date_range", {}).get("earliest")
    if not ori_earliest:
        return gap

    gap["ori_earliest"] = ori_earliest
    try:
        from datetime import date
        ori_year = int(ori_earliest[:4])
        # Gap threshold: if ORI starts after 2012, likely missing pre-migration Notubiz docs.
        # 2010 is when ORI started indexing Notubiz municipalities — that's the baseline.
        GAP_THRESHOLD_YEAR = 2012
        if ori_year > GAP_THRESHOLD_YEAR:
            gap["detected"] = True
            gap["gap_years"] = ori_year - 2010
            gap["note"] = (
                f"ORI starts {ori_earliest[:10]} — approx. {gap['gap_years']} years of "
                f"pre-ORI documents (2010–{ori_year}) likely exist in this gemeente's "
                f"Notubiz archive and are NOT indexed in ORI. "
                f"Full corpus requires Notubiz adapter (v0.3.0) or manual export. "
                f"For v0.2.1: ORI covers {ori_earliest[:10]}→present; accept the gap."
            )
    except (ValueError, IndexError):
        pass
    return gap


def make_decision(gemeente: str, sources: dict[str, Any]) -> dict[str, Any]:
    """
    Given probe results, produce a recommended ingestion strategy.

    Key architectural facts (verified 2026-04-13):
    - ORI indexes BOTH iBabs and Notubiz municipalities — 342/342 Dutch municipalities present.
    - The ORI `original_url` field reliably identifies the backend:
        api1.ibabs.eu  → iBabs municipality
        api.notubiz.nl → Notubiz municipality
    - ORI lags 1–3 months behind the live portals; use the native portal for freshness.
    - iBabs and Notubiz are COMPLEMENTARY to ORI, not alternatives.
    - Rotterdam's old Notubiz archive (rotterdam.raadsinformatie.nl) is NOT indexed in ORI —
      ORI indexes Rotterdam from iBabs. So the Notubiz archive is a true separate corpus.

    Strategy values (see module docstring for full matrix):
        "ibabs+ori"         — iBabs municipality: native portal for current, ORI for historical
        "notubiz+ori"       — Notubiz municipality: native portal for current, ORI for bulk
        "ibabs+notubiz+ori" — iBabs city with a separate legacy Notubiz archive still live
        "ibabs"             — iBabs only (ORI index not yet built)
        "notubiz"           — Notubiz only (ORI index not yet built)
        "ori"               — ORI only (no direct portal confirmed reachable)
        "parlaeus_via_ori"  — Parlaeus city; use ORI fallback until v0.3.0
        "unknown"           — no portal found

    Decision logic: ORI backend detection takes precedence over HEAD probe results,
    since HEAD probes only confirm portal reachability, not which system ORI indexes from.
    """
    ori = sources.get("ori", {})
    ibabs = sources.get("ibabs", {})
    parlaeus = sources.get("parlaeus", {})
    notubiz = sources.get("notubiz", {})

    ori_ok = ori.get("available", False)
    ibabs_ok = ibabs.get("available", False)
    notubiz_ok = notubiz.get("available", False)
    parlaeus_ok = parlaeus.get("available", False)

    # ORI backend detection is authoritative — it tells us what ORI actually indexes
    ori_backend = ori.get("backend", "unknown")  # "ibabs" | "notubiz" | "parlaeus" | "unknown"
    # Upgrade portal flags based on ORI backend evidence
    if ori_backend == "ibabs" and not ibabs_ok:
        ibabs_ok = True   # ORI proves iBabs exists even if HEAD probe missed it
    if ori_backend == "notubiz" and not notubiz_ok:
        notubiz_ok = True  # ORI proves Notubiz exists even if HEAD probe missed it

    decision: dict[str, Any] = {
        "recommended_strategy": None,
        # Legacy alias kept for backwards-compat with onboard_gemeente.py callers:
        "recommended_path": None,
        "sources": [],           # ordered list: ingest priority (highest first)
        "mode": "search_only",
        "capabilities": {
            "search": False,
            "financial": False,
            "kg": False,
            "virtual_notulen": False,
        },
        "ready_to_ingest": False,
        "blocker": None,
        "historical_gap": {},
        "estimated_doc_count": 0,
        "estimated_financial_docs": 0,
        "estimated_cost_usd": 0.0,
        "next_steps": [],
        "warnings": [],
    }

    def _apply_full_mode(fin_count: int = 0, doc_count: int = 0) -> None:
        decision["mode"] = "full"
        decision["capabilities"].update({"search": True, "financial": True, "virtual_notulen": True})
        decision["ready_to_ingest"] = True
        decision["estimated_doc_count"] = doc_count or 50000
        decision["estimated_financial_docs"] = fin_count
        embed_cost = decision["estimated_doc_count"] * 0.0003
        fin_cost = fin_count * 0.02
        decision["estimated_cost_usd"] = round(embed_cost + fin_cost, 2)

    def _full_scope_steps() -> list[str]:
        return [
            f"Run: python scripts/discover_gemeente.py --gemeente {gemeente} --full-scope"
            "  (enumerate + dedup all sources → writes data/pipeline_state/{gemeente}_scope.json)",
        ]

    # -----------------------------------------------------------------------
    # Triple source: iBabs + separate Notubiz archive + ORI (e.g. Rotterdam)
    # ORI indexes from iBabs; the Notubiz portal is a separate standalone archive.
    # -----------------------------------------------------------------------
    if ibabs_ok and notubiz_ok and ori_ok and ori_backend == "ibabs":
        gap = _detect_historical_gap(ori)
        decision["recommended_strategy"] = "ibabs+notubiz+ori"
        decision["recommended_path"] = "ibabs+notubiz+ori"
        decision["sources"] = ["ibabs", "notubiz", "ori"]
        decision["historical_gap"] = gap
        _apply_full_mode(
            fin_count=ori["financial"].get("count", 0),
            doc_count=ori.get("doc_count", 0),
        )
        decision["warnings"].append(
            "iBabs municipality with a separate Notubiz archive still live (e.g. Rotterdam). "
            "ORI indexes from iBabs. Notubiz holds pre-migration documents not in ORI. "
            "Run --full-scope to enumerate and deduplicate all three sources."
        )
        if gap["detected"]:
            decision["warnings"].append(gap["note"])
        decision["next_steps"] = _full_scope_steps() + [
            f"Run: python scripts/onboard_gemeente.py --gemeente {gemeente} --source ibabs --dry-run",
            f"Run: python scripts/onboard_gemeente.py --gemeente {gemeente} --source ibabs  (current docs)",
            f"Run: python scripts/onboard_gemeente.py --gemeente {gemeente} --source notubiz  (historical archive)",
        ]

    # -----------------------------------------------------------------------
    # iBabs + ORI (most iBabs cities — migrated from Notubiz, ORI has pre-migration docs)
    # -----------------------------------------------------------------------
    # iBabs + ORI (standard iBabs municipality — ORI indexes from ibabs.eu)
    # -----------------------------------------------------------------------
    elif ibabs_ok and ori_ok:
        gap = _detect_historical_gap(ori)
        decision["recommended_strategy"] = "ibabs+ori"
        decision["recommended_path"] = "ibabs+ori"
        decision["sources"] = ["ibabs", "ori"]
        decision["historical_gap"] = gap
        _apply_full_mode(
            fin_count=ori["financial"].get("count", 0),
            doc_count=ori.get("doc_count", 0),
        )
        decision["next_steps"] = _full_scope_steps() + [
            f"Run: python scripts/onboard_gemeente.py --gemeente {gemeente} --source ibabs --dry-run",
            f"Run: python scripts/onboard_gemeente.py --gemeente {gemeente} --source ibabs  (current docs)",
        ]
        if gap["detected"]:
            decision["warnings"].append(gap["note"])
            decision["next_steps"].append(
                f"Run: python scripts/onboard_gemeente.py --gemeente {gemeente} --source ori  (historical gap-fill)"
            )
        if ori["financial"]["available"] and ori["financial"]["pdf_accessible"]:
            decision["next_steps"].append(
                f"Run: python scripts/onboard_gemeente.py --gemeente {gemeente} --source ori --financial-only"
            )

    # -----------------------------------------------------------------------
    # iBabs + Notubiz (iBabs is current, Notubiz is still the live historical archive)
    # -----------------------------------------------------------------------
    elif ibabs_ok and notubiz_ok:
        decision["recommended_strategy"] = "ibabs+notubiz"
        decision["recommended_path"] = "ibabs+notubiz"
        decision["sources"] = ["ibabs", "notubiz"]
        _apply_full_mode(doc_count=50000)
        decision["warnings"].append(
            "Notubiz is available alongside iBabs — likely the historical archive "
            "(city migrated to iBabs but Notubiz still live). "
            "Run --full-scope to determine the date boundary."
        )
        decision["next_steps"] = _full_scope_steps() + [
            f"Run: python scripts/onboard_gemeente.py --gemeente {gemeente} --source ibabs --dry-run",
            f"Run: python scripts/onboard_gemeente.py --gemeente {gemeente} --source ibabs  (current docs)",
            f"Run: python scripts/onboard_gemeente.py --gemeente {gemeente} --source notubiz  (historical archive)",
        ]

    # -----------------------------------------------------------------------
    # iBabs only
    # -----------------------------------------------------------------------
    elif ibabs_ok:
        decision["recommended_strategy"] = "ibabs"
        decision["recommended_path"] = "ibabs"
        decision["sources"] = ["ibabs"]
        _apply_full_mode(doc_count=50000)
        decision["warnings"].append(
            "Neither ORI nor Notubiz found for this gemeente. "
            "Financial PDFs must come directly from iBabs meeting bundles."
        )
        decision["next_steps"] = [
            f"Add tenant config: data/tenants/{gemeente}/config.yml",
            f"Run: python scripts/onboard_gemeente.py --gemeente {gemeente} --source ibabs --dry-run",
            f"Run: python scripts/onboard_gemeente.py --gemeente {gemeente} --source ibabs",
        ]

    # -----------------------------------------------------------------------
    # Notubiz + ORI (active Notubiz municipality; ORI is the lagging aggregate)
    # -----------------------------------------------------------------------
    elif notubiz_ok and ori_ok:
        gap = _detect_historical_gap(ori)
        decision["recommended_strategy"] = "notubiz+ori"
        decision["recommended_path"] = "notubiz+ori"
        decision["sources"] = ["notubiz", "ori"]
        decision["historical_gap"] = gap
        fin_accessible = ori["financial"]["pdf_accessible"]
        decision["mode"] = "full" if fin_accessible else "search_only"
        decision["capabilities"].update({
            "search": True,
            "financial": fin_accessible,
            "virtual_notulen": False,  # Notubiz webcasts not yet supported
        })
        decision["ready_to_ingest"] = True
        decision["estimated_doc_count"] = ori.get("doc_count", 0)
        decision["estimated_financial_docs"] = ori["financial"].get("count", 0)
        embed_cost = decision["estimated_doc_count"] * 0.0003
        fin_cost = decision["estimated_financial_docs"] * 0.02
        decision["estimated_cost_usd"] = round(embed_cost + fin_cost, 2)
        decision["warnings"].append(
            "Notubiz native adapter not yet built (v0.3.0 scope). "
            "Use ORI as primary source for now — it indexes this gemeente's Notubiz content. "
            "Notubiz direct gives lower latency and more complete recent docs when adapter ships."
        )
        if not fin_accessible:
            decision["warnings"].append(
                "Financial PDFs not publicly accessible via ORI — "
                "financial extraction blocked until Notubiz adapter ships (v0.3.0)"
            )
        decision["next_steps"] = _full_scope_steps() + [
            f"Add tenant config: data/tenants/{gemeente}/config.yml",
            f"Run: python scripts/onboard_gemeente.py --gemeente {gemeente} --source ori --dry-run",
            f"Run: python scripts/onboard_gemeente.py --gemeente {gemeente} --source ori",
            "TODO v0.3.0: build pipeline/sources/notubiz.py native adapter",
        ]

    # -----------------------------------------------------------------------
    # Notubiz only (no ORI index found)
    # -----------------------------------------------------------------------
    elif notubiz_ok:
        decision["recommended_strategy"] = "notubiz"
        decision["recommended_path"] = "notubiz"
        decision["sources"] = ["notubiz"]
        decision["mode"] = "search_only"
        decision["capabilities"]["search"] = True
        decision["ready_to_ingest"] = False
        decision["blocker"] = "Notubiz native adapter not yet built (v0.3.0 scope). No ORI index found as fallback."
        decision["next_steps"] = [
            "TODO v0.3.0: build pipeline/sources/notubiz.py native adapter",
            f"Search ORI _cat/indices manually: does '{gemeente}' appear under a different spelling?",
        ]

    # -----------------------------------------------------------------------
    # ORI only (no direct portal found)
    # -----------------------------------------------------------------------
    elif ori_ok:
        decision["recommended_strategy"] = "ori"
        decision["recommended_path"] = "ori"
        decision["sources"] = ["ori"]
        fin_accessible = ori["financial"]["pdf_accessible"]
        decision["mode"] = "full" if fin_accessible else "search_only"
        decision["capabilities"].update({"search": True, "financial": fin_accessible})
        decision["ready_to_ingest"] = True
        decision["estimated_doc_count"] = ori.get("doc_count", 0)
        decision["estimated_financial_docs"] = ori["financial"].get("count", 0)
        embed_cost = decision["estimated_doc_count"] * 0.0003
        fin_cost = decision["estimated_financial_docs"] * 0.02
        decision["estimated_cost_usd"] = round(embed_cost + fin_cost, 2)
        if not fin_accessible:
            decision["warnings"].append(
                "Financial docs found in ORI but PDF URL not publicly accessible — "
                "financial extraction blocked until native adapter ships (v0.3.0)"
            )
        decision["next_steps"] = [
            f"Add tenant config: data/tenants/{gemeente}/config.yml",
            f"Run: python scripts/onboard_gemeente.py --gemeente {gemeente} --source ori --dry-run",
        ]
        if fin_accessible:
            decision["next_steps"].append(
                f"Run: python scripts/onboard_gemeente.py --gemeente {gemeente} --source ori --financial-only"
            )
        decision["next_steps"].append(
            f"Run: python scripts/onboard_gemeente.py --gemeente {gemeente} --source ori"
        )

    # -----------------------------------------------------------------------
    # Parlaeus (no native adapter yet)
    # -----------------------------------------------------------------------
    elif parlaeus_ok:
        decision["recommended_strategy"] = "parlaeus_via_ori"
        decision["recommended_path"] = "parlaeus_via_ori"
        decision["sources"] = ["ori"] if ori_ok else []
        decision["mode"] = "search_only"
        decision["capabilities"]["search"] = ori_ok
        decision["ready_to_ingest"] = ori_ok
        decision["blocker"] = "Native Parlaeus adapter not yet built (v0.3.0 scope)"
        decision["next_steps"] = [
            "Use ORI fallback for search-only in v0.2.1",
            "Native Parlaeus adapter: pipeline/sources/parlaeus.py — v0.3.0 scope",
        ]

    # -----------------------------------------------------------------------
    # Unknown
    # -----------------------------------------------------------------------
    else:
        decision["recommended_strategy"] = "unknown"
        decision["recommended_path"] = "unknown"
        decision["sources"] = []
        decision["ready_to_ingest"] = False
        decision["blocker"] = "No known portal found. Manual investigation needed."
        decision["next_steps"] = [
            f"Search: '{gemeente} raadsinformatie' to find their portal",
            "Check VNG portal registry at vng.nl",
            "Contact gemeente directly for API access",
        ]

    return decision


# ---------------------------------------------------------------------------
# Config generation
# ---------------------------------------------------------------------------

def generate_config(gemeente: str, sources: dict[str, Any], decision: dict[str, Any]) -> dict:
    ori = sources.get("ori", {})
    ibabs = sources.get("ibabs", {})
    notubiz = sources.get("notubiz", {})

    return {
        "gemeente": gemeente,
        "display_name": f"Gemeente {gemeente.title()}",
        "population": None,  # fill manually
        "province": None,    # fill manually

        # Ingestion strategy — set by make_decision(); consumed by pipeline/sources/factory.py
        "recommended_strategy": decision.get("recommended_strategy"),
        "ingestion_sources": decision.get("sources", []),  # ordered: primary first

        "sources": {
            "ibabs": {
                "enabled": ibabs.get("available", False),
                "domain": ibabs.get("domain"),
            },
            "ori": {
                "enabled": ori.get("available", False),
                "index": ori.get("index"),
                "financial_pdfs_via_ori": ori.get("financial", {}).get("pdf_accessible", False),
            },
            "parlaeus": {"enabled": sources.get("parlaeus", {}).get("available", False)},
            "notubiz": {
                "enabled": notubiz.get("available", False),
                "domain": notubiz.get("domain"),
            },
        },

        "capabilities": {
            "mode": decision["mode"],
            "financial": decision["capabilities"]["financial"],
            "kg": False,
            "virtual_notulen": decision["capabilities"].get("virtual_notulen", False),
            "journeys": False,
        },

        "financial_docs": [
            {"type": "programmabegroting", "keywords": ["programmabegroting", "begroting"]},
            {"type": "jaarstukken", "keywords": ["jaarstukken", "jaarrekening"]},
            {"type": "voorjaarsnota", "keywords": ["voorjaarsnota"]},
            {"type": "10-maands", "keywords": ["10-maandsrapportage", "10-maands"]},
        ],

        "corpus": {
            "date_from": ori.get("date_range", {}).get("earliest") or "2018-01-01",
            "date_to": None,
            "doc_count_approx": decision.get("estimated_doc_count", 0),
            "ori_index_verified": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        },
    }


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

TICK = "✅"
CROSS = "❌"
WARN = "⚠️ "


def _yn(val: bool) -> str:
    return TICK if val else CROSS


def print_report(gemeente: str, sources: dict, decision: dict, timestamp: str) -> None:
    ori = sources["ori"]
    ibabs = sources["ibabs"]
    parlaeus = sources["parlaeus"]
    notubiz = sources["notubiz"]

    print(f"\n{'='*60}")
    print(f"  NeoDemos — Gemeente Discovery: {gemeente.upper()}")
    print(f"  {timestamp}")
    print(f"{'='*60}\n")

    print("PORTAL PROBES")
    print(f"  ORI (openraadsinformatie.nl)  {_yn(ori['available'])}", end="")
    if ori["available"]:
        print(f"  index={ori['index']}  docs={ori['doc_count']:,}")
    else:
        print(f"  {'(error: ' + ori.get('error','not found') + ')' if ori.get('error') else '(not found)'}")

    print(f"  iBabs                         {_yn(ibabs['available'])}", end="")
    if ibabs["available"]:
        print(f"  domain={ibabs['domain']}")
    else:
        print()

    print(f"  Parlaeus                      {_yn(parlaeus['available'])}", end="")
    if parlaeus["available"]:
        print(f"  domain={parlaeus['domain']}")
    else:
        print()

    print(f"  Notubiz                       {_yn(notubiz['available'])}", end="")
    if notubiz["available"]:
        print(f"  domain={notubiz['domain']}")
    else:
        print()

    if ori["available"]:
        print(f"\nORI CORPUS DETAIL")
        print(f"  Date range:  {ori['date_range'].get('earliest','?')} → {ori['date_range'].get('latest','?')}")
        print(f"  Doc types:")
        for t, n in sorted(ori["document_types"].items(), key=lambda x: -x[1]):
            print(f"    {t:40s} {n:>8,}")
        print(f"\n  Financial docs in ORI:  {_yn(ori['financial']['available'])}  ({ori['financial']['count']:,} hits)")
        print(f"  PDF publicly accessible:{_yn(ori['financial']['pdf_accessible'])}  {ori['financial'].get('sample_url','')[:70]}")
        print(f"  Civic docs (moties etc):{_yn(ori['civic_docs']['available'])}  ({ori['civic_docs'].get('count',0):,} hits)")

    print(f"\nDECISION")
    print(f"  Recommended path:   {decision['recommended_path']}")
    print(f"  Mode:               {decision['mode']}")
    print(f"  Ready to ingest:    {_yn(decision['ready_to_ingest'])}")
    print(f"\n  Capabilities:")
    for cap, val in decision["capabilities"].items():
        print(f"    {cap:20s}  {_yn(val)}")

    if decision.get("estimated_doc_count"):
        print(f"\n  Estimated docs:     {decision['estimated_doc_count']:,}")
    if decision.get("estimated_financial_docs"):
        print(f"  Financial docs:     {decision['estimated_financial_docs']:,}")
    if decision.get("estimated_cost_usd"):
        print(f"  Estimated cost:     ~${decision['estimated_cost_usd']:.2f} USD")

    if decision.get("blocker"):
        print(f"\n  {WARN} BLOCKER: {decision['blocker']}")

    if decision.get("warnings"):
        print()
        for w in decision["warnings"]:
            print(f"  {WARN} {w}")

    print(f"\nNEXT STEPS")
    for i, step in enumerate(decision["next_steps"], 1):
        print(f"  {i}. {step}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def discover(gemeente: str) -> dict:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        ori, ibabs, parlaeus, notubiz = await asyncio.gather(
            probe_ori(client, gemeente),
            probe_ibabs(client, gemeente),
            probe_parlaeus(client, gemeente),
            probe_notubiz(client, gemeente),
        )

    sources = {"ori": ori, "ibabs": ibabs, "parlaeus": parlaeus, "notubiz": notubiz}
    decision = make_decision(gemeente, sources)

    return {
        "gemeente": gemeente,
        "timestamp": timestamp,
        "sources": sources,
        "decision": decision,
    }


def save_config(gemeente: str, sources: dict, decision: dict) -> Path:
    config = generate_config(gemeente, sources, decision)
    out_dir = TENANTS_DIR / gemeente
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "config.yml"
    with open(out_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Discover portal and document availability for a Dutch municipality.")
    parser.add_argument("--gemeente", required=True, help="Gemeente name (lowercase, e.g. middelburg)")
    parser.add_argument("--output", choices=["text", "json"], default="text", help="Output format (default: text)")
    parser.add_argument("--save-config", action="store_true", help="Write data/tenants/<gemeente>/config.yml")
    args = parser.parse_args()

    result = asyncio.run(discover(args.gemeente))

    if args.output == "json":
        print(json.dumps(result, indent=2, default=str))
    else:
        print_report(
            result["gemeente"],
            result["sources"],
            result["decision"],
            result["timestamp"],
        )

    if args.save_config:
        path = save_config(result["gemeente"], result["sources"], result["decision"])
        print(f"Config written to: {path}")

    # Exit non-zero if not ready to ingest, so shell scripts can gate on this
    if not result["decision"]["ready_to_ingest"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
