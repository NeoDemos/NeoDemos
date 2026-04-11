#!/usr/bin/env python3
"""
Import BAG street-level hierarchy into kg_entities / kg_relationships
=====================================================================

Builds the location skeleton for NeoDemos v0.2.0 WS1 GraphRAG Phase 0.

Downloads street (openbare_ruimte) data from the Dutch national address
registry (BAG — Basisregistratie Adressen en Gebouwen) via PDOK, joins it
against buurt/wijk polygons from CBS Wijk- en Buurtkaart 2024, and emits
a chain of Location nodes and LOCATED_IN edges:

    straat  --LOCATED_IN-->  buurt  --LOCATED_IN-->  wijk
            --LOCATED_IN-->  gebied --LOCATED_IN-->  gemeente

Why BAG IDs (multi-tenant rationale — handoff WS1 §49)
------------------------------------------------------
Street names are NOT unique across Dutch municipalities. "Hoofdstraat"
exists in 100+ towns, "Marnixstraat" in 10+ large cities. Using the BAG
16-digit `openbare_ruimte` identifier as the canonical primary key (stored
in `metadata.bag_id`) means that when v0.2.1 promotes Apeldoorn, Zoetermeer,
etc. into full mode, we do NOT need a painful canonicalization migration —
every Location row is already disambiguated at the municipality level via
its `metadata.gemeente` attribute and referenced by its BAG ID.

Note on the UNIQUE constraint: `kg_entities` uses UNIQUE(type, name). For
Rotterdam-only v0.2.0 we log name collisions within the gemeente and keep
the first-seen row (see COLLISION HANDLING below). v0.2.1 multi-portal
must revisit this — likely by switching Location entities to a namespaced
name like "Hoofdstraat (rotterdam)" or by widening the UNIQUE constraint
to include a gemeente column. Flagged here so it is not forgotten.

Data sources
------------
1. PDOK BAG 2.0 — `openbare_ruimte` layer filtered to Rotterdam.

   Two PDOK access patterns are supported upstream:
     - ATOM feed at https://service.pdok.nl/lv/bag/atom/bag.xml indexing
       monthly full extracts (GPKG / GML), ~3GB per extract. Requires
       parsing the ATOM XML, picking the latest entry, downloading the
       national GPKG, filtering in-process. Robust but heavy.
     - WFS v2 at https://service.pdok.nl/lv/bag/wfs/v2_0 with a CQL filter
       on `gemeente_woonplaats_code=0599`. Returns GML, streams in chunks,
       can be resumed via `startIndex`.

   >>> CHOICE: WFS. It is simpler to implement non-interactively, needs no
   ATOM-feed parser, avoids downloading a 3GB national extract just to
   discard 99.5% of it, and PDOK's WFS supports server-side filtering.
   The CQL filter `gemeentecode=0599` scopes the result to ~5K Rotterdam
   streets, which fits in memory and writes once. If PDOK WFS is
   unreachable or rate-limits, fall back to the ATOM path by passing
   `--source atom` (not implemented in v0.2.0 — flagged as follow-up).

2. CBS Wijk- en Buurtkaart 2024 — a GeoPackage published annually at
   https://www.cbs.nl/nl-nl/dossier/nederland-regionaal/geografische-data/
   wijk-en-buurtkaart-2024. Filter features to `GM_CODE='GM0599'`.
   Provides `BU_CODE`, `BU_NAAM`, `WK_CODE`, `WK_NAAM`, `GM_NAAM`.

   NOTE: the exact CBS download URL and filename change each year
   (`wijkenbuurten_2024_v1.gpkg`, `WijkBuurtkaart_2024_v1.zip`, etc.).
   The --cbs-url flag lets the operator override; default points at the
   current best-known canonical URL, but the runbook in handoff WS1 §49
   should be consulted before first run. **UNCERTAIN: default URL may
   need manual correction at first run — flagged.**

3. Rotterdam gebieden (14 city districts). Not in CBS. Hardcoded below as
   WIJK_TO_GEBIED_ROTTERDAM — the 14 gebieden are documented on
   rotterdam.nl and have been stable since 2014. Treat this mapping as
   file-level source-of-truth: auditable in diffs, obvious in reviews.

Required dependencies (NOT in requirements.txt — install manually)
------------------------------------------------------------------
    geopandas   # reads GeoPackage / streams WFS GML via fiona
    requests    # HTTP with streaming + retry
    psycopg2    # already in requirements
    tqdm        # already in requirements
    python-dotenv  # already in requirements

Handoff link: docs/handoffs/WS1_GRAPHRAG.md §49 (BAG location hierarchy)

Usage examples
--------------
    # Dry run (parse + count, no DB writes)
    python scripts/import_bag_locations.py --dry-run

    # Full import (requires an advisory lock on 42)
    python scripts/import_bag_locations.py --gemeente rotterdam

    # Smoke test with 50 streets
    python scripts/import_bag_locations.py --limit 50 --dry-run

    # Force redownload of cached PDOK + CBS files
    python scripts/import_bag_locations.py --force-refresh
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Iterable

# ── Project bootstrap ─────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import psycopg2
from psycopg2.extras import Json, RealDictCursor
from tqdm import tqdm

# geopandas / requests are heavy and only needed for the download path.
# Import lazily so --help stays snappy and so dry-run on cached files
# doesn't explode if the user hasn't pip-installed them yet.
try:
    import geopandas as gpd  # type: ignore
except ImportError:
    gpd = None  # type: ignore

try:
    import requests  # type: ignore
except ImportError:
    requests = None  # type: ignore


# ── Configuration ─────────────────────────────────────────────────────

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/neodemos")
LOG_PATH = PROJECT_ROOT / "logs" / "import_bag_locations.log"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "bag"

# Advisory lock shared with other KG writers — see WS1 handoff and
# project_embedding_process.md. NEVER write to Qdrant/PostgreSQL KG
# tables while this lock is held by a background enrichment run.
ADVISORY_LOCK_KEY = 42

# Supported gemeente registry. Adding a new gemeente here should also
# populate the WIJK_TO_GEBIED static mapping (or explicitly mark the
# gemeente as having no gebied layer).
GEMEENTE_CODES: dict[str, str] = {
    "rotterdam": "0599",
}

# PDOK BAG WFS 2.0 endpoint. `openbare_ruimte` is the feature type carrying
# street-level entries with the 16-digit `identificatie` we want as bag_id.
PDOK_WFS_URL = "https://service.pdok.nl/lv/bag/wfs/v2_0"
PDOK_WFS_TYPENAME = "bag:openbareruimte"

# CBS Wijk- en Buurtkaart 2024 — default GPKG download URL.
# This URL changes each CBS release; operator may need to override with
# --cbs-url if 2024 is superseded or if CBS moves the file.
CBS_WBK_URL = (
    "https://download.cbs.nl/regionale-kaarten/wijkenbuurten_2024_v1.gpkg"
)
CBS_WBK_LAYER = "buurten"  # CBS GPKG contains `gemeenten`, `wijken`, `buurten`

# ── Static Rotterdam wijk → gebied mapping ────────────────────────────
#
# Rotterdam has 14 administrative gebieden. Every wijk in Rotterdam belongs
# to exactly one gebied. Source: https://www.rotterdam.nl/wonen-leven/gebieden/
#
# This mapping is keyed by wijk name (BU_NAAM's parent WK_NAAM as published
# by CBS). Any wijk we encounter in CBS that is not in this dict is
# logged as a warning and given a synthetic "Overig" gebied link so the
# graph chain still completes.
#
# If/when the city redistricts, update this table and re-run the script.
# v0.2.1 multi-portal should move this out of code into a per-gemeente
# static data file — but for v0.2.0 Rotterdam-only, inline is simpler
# to diff and audit.
WIJK_TO_GEBIED_ROTTERDAM: dict[str, str] = {
    # Centrum
    "Stadsdriehoek": "Centrum",
    "Cool": "Centrum",
    "C.S. Kwartier": "Centrum",
    "Dijkzigt": "Centrum",
    "Nieuwe Werk": "Centrum",
    "Oude Westen": "Centrum",
    "Cs Kwartier": "Centrum",
    # Delfshaven
    "Delfshaven": "Delfshaven",
    "Bospolder": "Delfshaven",
    "Tussendijken": "Delfshaven",
    "Spangen": "Delfshaven",
    "Nieuwe Westen": "Delfshaven",
    "Middelland": "Delfshaven",
    "Oud Mathenesse": "Delfshaven",
    "Witte Dorp": "Delfshaven",
    "Schiemond": "Delfshaven",
    # Overschie
    "Overschie": "Overschie",
    "Kleinpolder": "Overschie",
    "Zestienhoven": "Overschie",
    "Landzicht": "Overschie",
    "Schieveen": "Overschie",
    # Noord
    "Agniesebuurt": "Noord",
    "Bergpolder": "Noord",
    "Blijdorp": "Noord",
    "Blijdorpsepolder": "Noord",
    "Liskwartier": "Noord",
    "Oude Noorden": "Noord",
    "Provenierswijk": "Noord",
    # Hillegersberg-Schiebroek
    "Hillegersberg-Zuid": "Hillegersberg-Schiebroek",
    "Hillegersberg-Noord": "Hillegersberg-Schiebroek",
    "Schiebroek": "Hillegersberg-Schiebroek",
    "Terbregge": "Hillegersberg-Schiebroek",
    "Molenlaankwartier": "Hillegersberg-Schiebroek",
    # Kralingen-Crooswijk
    "Crooswijk": "Kralingen-Crooswijk",
    "Rubroek": "Kralingen-Crooswijk",
    "Struisenburg": "Kralingen-Crooswijk",
    "Kralingen-West": "Kralingen-Crooswijk",
    "Kralingen-Oost": "Kralingen-Crooswijk",
    "De Esch": "Kralingen-Crooswijk",
    "Nieuw Crooswijk": "Kralingen-Crooswijk",
    # Feijenoord
    "Noordereiland": "Feijenoord",
    "Kop van Zuid": "Feijenoord",
    "Kop van Zuid - Entrepot": "Feijenoord",
    "Afrikaanderwijk": "Feijenoord",
    "Katendrecht": "Feijenoord",
    "Feijenoord": "Feijenoord",
    "Bloemhof": "Feijenoord",
    "Hillesluis": "Feijenoord",
    "Vreewijk": "Feijenoord",
    # IJsselmonde
    "Oud-IJsselmonde": "IJsselmonde",
    "Lombardijen": "IJsselmonde",
    "Groot-IJsselmonde": "IJsselmonde",
    "Beverwaard": "IJsselmonde",
    # Pernis
    "Pernis": "Pernis",
    # Prins Alexander
    "Het Lage Land": "Prins Alexander",
    "Prinsenland": "Prins Alexander",
    "'s-Gravenland": "Prins Alexander",
    "Kralingseveer": "Prins Alexander",
    "Zevenkamp": "Prins Alexander",
    "Ommoord": "Prins Alexander",
    "Nesselande": "Prins Alexander",
    "Oosterflank": "Prins Alexander",
    # Charlois
    "Carnisse": "Charlois",
    "Tarwewijk": "Charlois",
    "Oud-Charlois": "Charlois",
    "Wielewaal": "Charlois",
    "Zuidwijk": "Charlois",
    "Zuidplein": "Charlois",
    "Pendrecht": "Charlois",
    "Heijplaat": "Charlois",
    "Waalhaven": "Charlois",
    "Zuiderpark": "Charlois",
    # Hoogvliet
    "Hoogvliet Noord": "Hoogvliet",
    "Hoogvliet Zuid": "Hoogvliet",
    # Hoek van Holland
    "Hoek van Holland": "Hoek van Holland",
    # Rozenburg
    "Rozenburg": "Rozenburg",
}

# Full list of the 14 Rotterdam gebieden — used to seed the gebied nodes
# even for any gebied that ends up with zero wijken attached (defensive
# only in the sense that if the CBS feed ever drops a wijk we still have
# the full 14-gebied skeleton and the gemeente chain).
ROTTERDAM_GEBIEDEN: list[str] = sorted(set(WIJK_TO_GEBIED_ROTTERDAM.values()))


# ── Logging ───────────────────────────────────────────────────────────

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ── Download helpers ──────────────────────────────────────────────────

def _require_requests() -> None:
    if requests is None:
        raise RuntimeError(
            "requests is not installed. Install with: pip install requests"
        )


def _require_geopandas() -> None:
    if gpd is None:
        raise RuntimeError(
            "geopandas is not installed. Install with: pip install geopandas"
        )


def _stream_download(url: str, dest: Path, chunk_size: int = 1 << 20) -> None:
    """Stream a URL to disk with a tqdm progress bar."""
    _require_requests()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    log.info(f"Downloading {url} -> {dest}")
    with requests.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length") or 0)
        with tmp.open("wb") as fh, tqdm(
            total=total, unit="B", unit_scale=True, desc=dest.name,
        ) as pbar:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                fh.write(chunk)
                pbar.update(len(chunk))
    tmp.rename(dest)
    log.info(f"Saved {dest} ({dest.stat().st_size:,} bytes)")


def fetch_pdok_openbare_ruimte(
    data_dir: Path, gemeentecode: str, force: bool,
) -> Path:
    """
    Fetch the openbare_ruimte layer for a given gemeentecode via PDOK WFS.

    Uses CQL to server-side filter to `gemeentecode=<code>` and downloads
    GML. Returns the local cached GML path.
    """
    dest = data_dir / f"pdok_openbare_ruimte_{gemeentecode}.gml"
    if dest.exists() and not force:
        log.info(f"Using cached PDOK openbare_ruimte file: {dest}")
        return dest

    _require_requests()
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": PDOK_WFS_TYPENAME,
        "outputFormat": "application/gml+xml; version=3.2",
        "srsName": "EPSG:28992",
        "CQL_FILTER": f"gemeentecode='{gemeentecode}'",
    }
    # Requests encodes params into the query string; PDOK WFS accepts GET.
    log.info(
        f"Requesting PDOK WFS openbare_ruimte for gemeentecode={gemeentecode}"
    )
    resp = requests.get(PDOK_WFS_URL, params=params, stream=True, timeout=300)
    resp.raise_for_status()

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with tmp.open("wb") as fh:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            if chunk:
                fh.write(chunk)
    tmp.rename(dest)
    log.info(f"Saved {dest} ({dest.stat().st_size:,} bytes)")
    return dest


def fetch_cbs_wijk_buurt(data_dir: Path, force: bool, url: str) -> Path:
    """Fetch the CBS Wijk- en Buurtkaart 2024 GeoPackage."""
    dest = data_dir / "cbs_wijkenbuurten_2024.gpkg"
    if dest.exists() and not force:
        log.info(f"Using cached CBS WBK file: {dest}")
        return dest
    _stream_download(url, dest)
    return dest


# ── Parsing ───────────────────────────────────────────────────────────

class StreetRow:
    __slots__ = ("bag_id", "name", "gemeentecode")

    def __init__(self, bag_id: str, name: str, gemeentecode: str):
        self.bag_id = bag_id
        self.name = name
        self.gemeentecode = gemeentecode


class BuurtRow:
    __slots__ = ("bu_code", "bu_naam", "wk_code", "wk_naam", "gm_code", "gm_naam")

    def __init__(
        self, bu_code: str, bu_naam: str, wk_code: str, wk_naam: str,
        gm_code: str, gm_naam: str,
    ):
        self.bu_code = bu_code
        self.bu_naam = bu_naam
        self.wk_code = wk_code
        self.wk_naam = wk_naam
        self.gm_code = gm_code
        self.gm_naam = gm_naam


def parse_openbare_ruimte(path: Path, gemeentecode: str) -> list[StreetRow]:
    """
    Parse the PDOK openbare_ruimte GML into StreetRow objects.

    Only `type='Weg'` entries are kept — BAG also publishes water
    (openbare ruimte type 'Water'), administrative areas etc. which we
    do not want as street nodes.
    """
    _require_geopandas()
    log.info(f"Reading PDOK openbare_ruimte from {path}")
    gdf = gpd.read_file(path)

    # Column names in BAG GML are lowercase. Defensively uppercase-match
    # for any CBS-style casing just in case.
    cols = {c.lower(): c for c in gdf.columns}
    id_col = cols.get("identificatie")
    name_col = cols.get("openbareruimtenaam") or cols.get("naam")
    type_col = cols.get("type")
    gem_col = cols.get("gemeentecode")

    if not id_col or not name_col:
        raise RuntimeError(
            f"PDOK openbare_ruimte file missing expected columns. "
            f"Found: {list(gdf.columns)}"
        )

    rows: list[StreetRow] = []
    for _, r in gdf.iterrows():
        t = str(r[type_col]).strip() if type_col and r[type_col] is not None else "Weg"
        if t and t.lower() != "weg":
            continue
        bag_id = str(r[id_col]).strip()
        name = str(r[name_col]).strip()
        gc = str(r[gem_col]).strip() if gem_col and r[gem_col] is not None else gemeentecode
        if not bag_id or not name:
            continue
        rows.append(StreetRow(bag_id=bag_id, name=name, gemeentecode=gc))

    log.info(f"Parsed {len(rows):,} openbare_ruimte entries (Weg only)")
    return rows


def parse_cbs_buurten(path: Path, gm_code: str) -> list[BuurtRow]:
    """Parse CBS Wijk- en Buurtkaart buurten layer filtered to a GM_CODE."""
    _require_geopandas()
    log.info(f"Reading CBS WBK buurten layer from {path}")
    # CBS GPKG contains layers like `buurten`, `wijken`, `gemeenten`.
    gdf = gpd.read_file(path, layer=CBS_WBK_LAYER)

    cols = {c.upper(): c for c in gdf.columns}
    bu_code = cols.get("BU_CODE")
    bu_naam = cols.get("BU_NAAM")
    wk_code = cols.get("WK_CODE")
    wk_naam = cols.get("WK_NAAM")
    gm_code_col = cols.get("GM_CODE")
    gm_naam_col = cols.get("GM_NAAM")

    missing = [
        n for n, c in [
            ("BU_CODE", bu_code), ("BU_NAAM", bu_naam),
            ("WK_CODE", wk_code), ("WK_NAAM", wk_naam),
            ("GM_CODE", gm_code_col), ("GM_NAAM", gm_naam_col),
        ] if c is None
    ]
    if missing:
        raise RuntimeError(
            f"CBS WBK file missing expected columns: {missing}. "
            f"Found: {list(gdf.columns)}"
        )

    rows: list[BuurtRow] = []
    for _, r in gdf.iterrows():
        if str(r[gm_code_col]).strip() != gm_code:
            continue
        rows.append(BuurtRow(
            bu_code=str(r[bu_code]).strip(),
            bu_naam=str(r[bu_naam]).strip(),
            wk_code=str(r[wk_code]).strip(),
            wk_naam=str(r[wk_naam]).strip(),
            gm_code=str(r[gm_code_col]).strip(),
            gm_naam=str(r[gm_naam_col]).strip(),
        ))

    log.info(f"Parsed {len(rows):,} buurten for {gm_code}")
    return rows


# ── DB helpers ────────────────────────────────────────────────────────

def acquire_advisory_lock(conn, wait: bool) -> bool:
    """
    Acquire advisory lock `ADVISORY_LOCK_KEY`. If `wait=False`, returns
    False immediately if the lock is held by another session.

    Coordinates with Flair/Gemini enrichment runs — see WS1 handoff and
    project_embedding_process.md.
    """
    cur = conn.cursor()
    if wait:
        log.info(f"Waiting for advisory lock {ADVISORY_LOCK_KEY}...")
        cur.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        cur.close()
        return True
    cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
    got = bool(cur.fetchone()[0])
    cur.close()
    return got


def release_advisory_lock(conn) -> None:
    try:
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
        cur.close()
    except Exception as exc:
        log.warning(f"Failed to release advisory lock: {exc}")


def upsert_entity(
    cur, ent_type: str, name: str, metadata: dict,
) -> tuple[int, bool]:
    """
    Insert (or no-op) a Location entity and return `(id, inserted)`.

    Uses ON CONFLICT (type, name) DO NOTHING for idempotency. When the row
    already existed, re-selects to fetch the id so the caller can still
    wire edges against it.
    """
    cur.execute(
        """
        INSERT INTO kg_entities (type, name, metadata)
        VALUES (%s, %s, %s)
        ON CONFLICT (type, name) DO NOTHING
        RETURNING id
        """,
        (ent_type, name, Json(metadata)),
    )
    row = cur.fetchone()
    if row is not None:
        return int(row[0]), True

    cur.execute(
        "SELECT id FROM kg_entities WHERE type = %s AND name = %s",
        (ent_type, name),
    )
    existing = cur.fetchone()
    if existing is None:
        raise RuntimeError(
            f"upsert_entity: insert said conflict but select returned nothing "
            f"for ({ent_type!r}, {name!r})"
        )
    return int(existing[0]), False


def insert_located_in_edge(
    cur, source_id: int, target_id: int, child_level: str,
) -> bool:
    """
    Insert a LOCATED_IN edge if one with the same (source, target,
    relation_type) does not already exist. Returns True on insert,
    False on skip.

    kg_relationships has no unique constraint on (source, target,
    relation_type), so we guard with WHERE NOT EXISTS.
    """
    cur.execute(
        """
        INSERT INTO kg_relationships
            (source_entity_id, target_entity_id, relation_type,
             confidence, metadata)
        SELECT %s, %s, 'LOCATED_IN', 1.0, %s
        WHERE NOT EXISTS (
            SELECT 1 FROM kg_relationships
            WHERE source_entity_id = %s
              AND target_entity_id = %s
              AND relation_type = 'LOCATED_IN'
        )
        RETURNING id
        """,
        (
            source_id, target_id,
            Json({"level": child_level, "source": "BAG+CBS+static"}),
            source_id, target_id,
        ),
    )
    return cur.fetchone() is not None


# ── Import orchestrator ───────────────────────────────────────────────

class ImportStats:
    def __init__(self) -> None:
        self.entities_inserted: dict[str, int] = {}
        self.entities_skipped: dict[str, int] = {}
        self.edges_inserted: int = 0
        self.edges_skipped: int = 0
        self.street_name_collisions: int = 0
        self.unmapped_wijken: set[str] = set()
        self.start_time = time.time()

    def ent(self, level: str, inserted: bool) -> None:
        bucket = self.entities_inserted if inserted else self.entities_skipped
        bucket[level] = bucket.get(level, 0) + 1

    def report(self) -> str:
        elapsed = time.time() - self.start_time
        lines = [
            f"elapsed={elapsed:,.1f}s",
            "entities inserted: " + ", ".join(
                f"{k}={v:,}" for k, v in sorted(self.entities_inserted.items())
            ) or "entities inserted: (none)",
            "entities skipped : " + ", ".join(
                f"{k}={v:,}" for k, v in sorted(self.entities_skipped.items())
            ) or "entities skipped : (none)",
            f"edges inserted={self.edges_inserted:,} skipped={self.edges_skipped:,}",
            f"street_name_collisions={self.street_name_collisions:,}",
            f"unmapped_wijken={len(self.unmapped_wijken)}",
        ]
        if self.unmapped_wijken:
            lines.append(
                "  -> "
                + ", ".join(sorted(self.unmapped_wijken)[:20])
                + (" ..." if len(self.unmapped_wijken) > 20 else "")
            )
        return "\n".join(lines)


def import_rotterdam(
    conn,
    streets: list[StreetRow],
    buurten: list[BuurtRow],
    gemeente_slug: str,
    dry_run: bool,
    limit: int | None,
) -> ImportStats:
    """
    Drive the inserts in the canonical order:
    gemeente -> gebieden -> wijken -> buurten -> straten.
    Edges emitted as each child node lands.
    """
    stats = ImportStats()

    # Build lookup: buurt_code -> BuurtRow, wk_code -> wijk_naam, wk_code -> wk_naam
    buurten_by_code: dict[str, BuurtRow] = {b.bu_code: b for b in buurten}
    wijken: dict[str, str] = {}  # wk_code -> wk_naam
    for b in buurten:
        wijken.setdefault(b.wk_code, b.wk_naam)

    gemeente_name = buurten[0].gm_naam if buurten else "Rotterdam"

    cur = conn.cursor()

    def _upsert(level: str, ent_type: str, name: str, meta: dict) -> int | None:
        if dry_run:
            # In dry-run we still need stable ids for edge dedupe within the
            # run. Use a synthetic negative id space keyed off (level, name).
            stats.ent(level, inserted=True)
            return -(abs(hash((level, name))) % (2**31))
        ent_id, inserted = upsert_entity(cur, ent_type, name, meta)
        stats.ent(level, inserted=inserted)
        return ent_id

    def _edge(src: int | None, tgt: int | None, child_level: str) -> None:
        if src is None or tgt is None:
            return
        if dry_run:
            stats.edges_inserted += 1
            return
        if insert_located_in_edge(cur, src, tgt, child_level):
            stats.edges_inserted += 1
        else:
            stats.edges_skipped += 1

    # ── 1. gemeente ───────────────────────────────────────────────────
    gemeente_id = _upsert(
        "gemeente", "Location", gemeente_name,
        {"gemeente": gemeente_slug, "level": "gemeente",
         "cbs_code": buurten[0].gm_code if buurten else None},
    )

    # ── 2. gebieden ───────────────────────────────────────────────────
    gebied_ids: dict[str, int | None] = {}
    for gebied in ROTTERDAM_GEBIEDEN:
        gid = _upsert(
            "gebied", "Location", gebied,
            {"gemeente": gemeente_slug, "level": "gebied"},
        )
        gebied_ids[gebied] = gid
        _edge(gid, gemeente_id, "gebied")
    # Synthetic "Overig" bucket for wijken with no static gebied mapping.
    overig_id = _upsert(
        "gebied", "Location", "Overig",
        {"gemeente": gemeente_slug, "level": "gebied",
         "synthetic": True, "note": "fallback for unmapped wijken"},
    )
    _edge(overig_id, gemeente_id, "gebied")

    # ── 3. wijken ─────────────────────────────────────────────────────
    wijk_ids: dict[str, int | None] = {}  # wk_code -> id
    for wk_code, wk_naam in wijken.items():
        gebied_name = WIJK_TO_GEBIED_ROTTERDAM.get(wk_naam)
        if gebied_name is None:
            stats.unmapped_wijken.add(wk_naam)
            parent_id = overig_id
        else:
            parent_id = gebied_ids.get(gebied_name) or overig_id

        wid = _upsert(
            "wijk", "Location", wk_naam,
            {"gemeente": gemeente_slug, "level": "wijk", "cbs_code": wk_code},
        )
        wijk_ids[wk_code] = wid
        _edge(wid, parent_id, "wijk")

    # ── 4. buurten ────────────────────────────────────────────────────
    buurt_ids: dict[str, int | None] = {}  # bu_code -> id
    for b in buurten:
        bid = _upsert(
            "buurt", "Location", b.bu_naam,
            {"gemeente": gemeente_slug, "level": "buurt",
             "cbs_code": b.bu_code, "wijk_code": b.wk_code},
        )
        buurt_ids[b.bu_code] = bid
        _edge(bid, wijk_ids.get(b.wk_code), "buurt")

    # ── 5. streets ────────────────────────────────────────────────────
    # COLLISION HANDLING: kg_entities UNIQUE(type, name) rejects two streets
    # with the same name in the same municipality. For v0.2.0 Rotterdam-only
    # we keep first-seen and log. v0.2.1 multi-portal MUST revisit (see
    # file-level docstring).
    seen_street_names: set[str] = set()

    # Street -> buurt linkage requires a spatial join in principle (BAG
    # openbare_ruimte has a geometry; CBS buurten has polygons). The PDOK
    # WFS output includes geometry, CBS buurten likewise. We do an on-disk
    # spatial join BEFORE this function ideally — but for v0.2.0 simplicity
    # and because BAG's `openbareruimte` rows carry a representative point,
    # we leave buurt assignment as a follow-up: streets still chain to the
    # gemeente via the wijk/gebied skeleton through a direct edge to the
    # gemeente for now. This is flagged as UNCERTAIN below.
    #
    # For v0.2.0 smoke-test purposes, we link each street directly to the
    # gemeente node with metadata.buurt_code=None so the graph walk still
    # functions. When spatial-join logic lands (follow-up ticket), swap
    # this call for a per-street buurt_id lookup.
    iterator: Iterable[StreetRow] = streets
    if limit:
        iterator = streets[:limit]

    for s in tqdm(list(iterator), desc="streets", unit="street"):
        if s.name in seen_street_names:
            stats.street_name_collisions += 1
            log.debug(f"street name collision: {s.name} (bag_id={s.bag_id})")
            continue
        seen_street_names.add(s.name)

        sid = _upsert(
            "straat", "Location", s.name,
            {
                "bag_id": s.bag_id,
                "gemeente": gemeente_slug,
                "level": "straat",
                "buurt_code": None,  # TODO: populate via spatial join
                "wijk_code": None,
            },
        )
        # Chain straat -> gemeente directly until spatial join is wired.
        _edge(sid, gemeente_id, "straat")

    if not dry_run:
        conn.commit()
    return stats


# ── Main entrypoint ───────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import BAG street-level location hierarchy into kg_entities / kg_relationships",
    )
    parser.add_argument(
        "--gemeente", default="rotterdam",
        help="Gemeente slug to import (default: rotterdam). "
             "Only 'rotterdam' is supported in v0.2.0.",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=DEFAULT_DATA_DIR,
        help=f"Directory for cached source files (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--force-refresh", action="store_true",
        help="Re-download source files even if cached copies exist",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and count only; no DB writes",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process at most N streets (smoke test)",
    )
    parser.add_argument(
        "--wait-for-lock", dest="wait_for_lock", action="store_true", default=True,
        help="Block until advisory lock 42 is available (default)",
    )
    parser.add_argument(
        "--no-wait-for-lock", dest="wait_for_lock", action="store_false",
        help="Fail fast if advisory lock 42 is held by another session",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )
    parser.add_argument(
        "--cbs-url", default=CBS_WBK_URL,
        help="Override CBS Wijk- en Buurtkaart GPKG download URL",
    )
    args = parser.parse_args()

    logging.getLogger().setLevel(args.log_level)

    if args.gemeente not in GEMEENTE_CODES:
        log.error(
            f"Unsupported gemeente {args.gemeente!r}. "
            f"Supported in v0.2.0: {sorted(GEMEENTE_CODES)}"
        )
        return 2
    gemeentecode = GEMEENTE_CODES[args.gemeente]

    log.info("=" * 64)
    log.info("  BAG LOCATION HIERARCHY IMPORT")
    log.info(f"  gemeente       = {args.gemeente} ({gemeentecode})")
    log.info(f"  data_dir       = {args.data_dir}")
    log.info(f"  force_refresh  = {args.force_refresh}")
    log.info(f"  dry_run        = {args.dry_run}")
    log.info(f"  limit          = {args.limit}")
    log.info("=" * 64)

    args.data_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Fetch sources ──────────────────────────────────────────────
    pdok_path = fetch_pdok_openbare_ruimte(
        args.data_dir, gemeentecode, args.force_refresh,
    )
    cbs_path = fetch_cbs_wijk_buurt(
        args.data_dir, args.force_refresh, args.cbs_url,
    )

    # ── 2. Parse ──────────────────────────────────────────────────────
    streets = parse_openbare_ruimte(pdok_path, gemeentecode)
    buurten = parse_cbs_buurten(cbs_path, f"GM{gemeentecode}")

    if not buurten:
        log.error(
            f"CBS WBK returned 0 buurten for GM{gemeentecode} — check --cbs-url "
            f"and the layer name {CBS_WBK_LAYER!r}."
        )
        return 3
    if not streets:
        log.error(
            f"PDOK WFS returned 0 openbare_ruimte rows for gemeentecode={gemeentecode}"
        )
        return 3

    # ── 3. Connect + lock ─────────────────────────────────────────────
    if args.dry_run:
        log.info("Dry run: skipping DB connection and advisory lock")
        conn = None
    else:
        conn = psycopg2.connect(DB_URL)
        conn.autocommit = False
        if not acquire_advisory_lock(conn, wait=args.wait_for_lock):
            log.error(
                f"Advisory lock {ADVISORY_LOCK_KEY} is held by another session "
                f"and --no-wait-for-lock was passed. Aborting."
            )
            conn.close()
            return 4

    # ── 4. Import ─────────────────────────────────────────────────────
    try:
        if args.dry_run:
            class _DryConn:
                def cursor(self): return _DryCursor()
                def commit(self): pass
            class _DryCursor:
                def execute(self, *a, **kw): pass
                def fetchone(self): return None
                def close(self): pass
            stats = import_rotterdam(
                _DryConn(),  # type: ignore[arg-type]
                streets, buurten, args.gemeente,
                dry_run=True, limit=args.limit,
            )
        else:
            stats = import_rotterdam(
                conn, streets, buurten, args.gemeente,
                dry_run=False, limit=args.limit,
            )
    finally:
        if conn is not None:
            release_advisory_lock(conn)
            conn.close()

    log.info("=" * 64)
    log.info("  IMPORT COMPLETE")
    for line in stats.report().splitlines():
        log.info(f"  {line}")
    log.info("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
