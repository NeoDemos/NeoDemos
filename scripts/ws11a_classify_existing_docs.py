"""WS11a — Metadata backfill: set doc_classification on existing documents.

Sets the civic doc_classification on documents already in the DB that have
NULL or incorrect classification, based on name-pattern matching.  No new
ingestion — pure SQL UPDATE.

Targets:
  P0 (retrieval-blocking):
  - initiatiefnotitie    ~111 docs  (name ILIKE '%initiatiefnoti%')
  - initiatiefvoorstel   ~522 docs  (name ILIKE '%initiatiefvoorstel%')
  - schriftelijke_vraag  ~120 docs  (name ILIKE '%schriftelijke%vraag%' etc.)

  P1 (visible retrieval gaps):
  - raadsvoorstel        ~2,768 docs (name ILIKE '%raadsvoorstel%')
  - brief_college        ~2,437 docs (name ILIKE '%brief%college%' etc.)
  - afdoeningsvoorstel   ~1,726 docs (name ILIKE '%afdoening%')
  - toezegging           ~1,603 docs (name ILIKE '%toezegging%')

  P2 (cheap, low retrieval impact):
  - motie                ~12,982 docs (name ILIKE '%motie%')
  - amendement           ~764 docs    (name ILIKE '%amendement%')

Also attempts to derive missing meeting_id for initiatiefnotities and
initiatiefvoorstellen via document_assignments → agenda_items → meetings.

Usage:
    python scripts/ws11a_classify_existing_docs.py           # dry-run (default)
    python scripts/ws11a_classify_existing_docs.py --execute # write changes
    python scripts/ws11a_classify_existing_docs.py --execute --skip-p2  # skip motie/amendement
    python scripts/ws11a_classify_existing_docs.py --execute --only-new # only run P1 additions
"""

import argparse
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import psycopg2
from dotenv import load_dotenv

load_dotenv()

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "ws11a_classification.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Classification targets
# ---------------------------------------------------------------------------

TARGETS = [
    # P0 — retrieval-blocking (Erik Verweij report)
    {
        "doc_classification": "initiatiefnotitie",
        "patterns": ["%initiatiefnoti%"],
        "priority": "P0",
    },
    {
        "doc_classification": "initiatiefvoorstel",
        "patterns": ["%initiatiefvoorstel%"],
        "priority": "P0",
    },
    {
        "doc_classification": "schriftelijke_vraag",
        "patterns": ["%schriftelijke%vraag%", "%schriftelijke%vragen%", "%raadsvraag%"],
        "priority": "P0",
    },
    # P1 — visible retrieval gaps found in 2026-04-13 NULL audit
    {
        "doc_classification": "raadsvoorstel",
        "patterns": ["%raadsvoorstel%"],
        "priority": "P1",
        # Exclude sub-doc types that are more specific
        "exclude_patterns": ["%initiatiefvoorstel%", "%afdoeningsvoorstel%"],
    },
    {
        "doc_classification": "brief_college",
        "patterns": ["%brief%college%", "%collegebrief%", "%wethoudersbrief%"],
        "priority": "P1",
    },
    {
        "doc_classification": "afdoeningsvoorstel",
        "patterns": ["%afdoening%"],
        "priority": "P1",
    },
    {
        "doc_classification": "toezegging",
        "patterns": ["%toezegging%"],
        "priority": "P1",
    },
    # P2 — cheap, low retrieval impact (already covered via meeting bundles)
    {
        "doc_classification": "motie",
        "patterns": ["%motie%"],
        "priority": "P2",
        # Exclude false positives: promotievideo, etc.
        "exclude_patterns": ["%promotie%", "%demotie%"],
    },
    {
        "doc_classification": "amendement",
        "patterns": ["%amendement%"],
        "priority": "P2",
    },
    # P3 — meeting & procedural types (all identifiable docs should have a label)
    {
        "doc_classification": "notulen",
        "patterns": ["%notulen%"],
        "priority": "P3",
    },
    {
        "doc_classification": "verslag",
        "patterns": ["%verslag%"],
        "priority": "P3",
        # notulen are more specific — exclude to avoid overlap
        "exclude_patterns": ["%notulen%"],
    },
    {
        "doc_classification": "agenda",
        "patterns": ["%agenda%"],
        "priority": "P3",
    },
    {
        "doc_classification": "annotatie",
        "patterns": ["%annotatie%"],
        "priority": "P3",
    },
    {
        "doc_classification": "adviezenlijst",
        "patterns": ["%adviezenlijst%", "%advieslijst%"],
        "priority": "P3",
    },
    {
        "doc_classification": "besluitenlijst",
        "patterns": ["%besluitenlijst%"],
        "priority": "P3",
    },
    {
        "doc_classification": "ingekomen_stukken",
        "patterns": ["%ingekomen%stuk%", "%lijst%ingekomen%", "%doorlopende%lijst%"],
        "priority": "P3",
    },
    {
        "doc_classification": "spreektijdentabel",
        "patterns": ["%spreektijd%"],
        "priority": "P3",
    },
    {
        "doc_classification": "transcript",
        "patterns": ["%transcript%"],
        "priority": "P3",
    },
    {
        "doc_classification": "rapport",
        "patterns": ["%rapport%"],
        "priority": "P3",
    },
    {
        "doc_classification": "notitie",
        "patterns": ["%notitie%"],
        "priority": "P3",
        # initiatiefnotitie is more specific — already classified
        "exclude_patterns": ["%initiatiefnoti%"],
    },
    {
        "doc_classification": "presentatie",
        "patterns": ["%presentat%"],
        "priority": "P3",
    },
    {
        "doc_classification": "monitor_rapport",
        "patterns": ["%monitor%"],
        "priority": "P3",
    },
    {
        "doc_classification": "planning",
        "patterns": ["%planning%"],
        "priority": "P3",
    },
    {
        "doc_classification": "bijlage",
        "patterns": ["%bijlage%"],
        "priority": "P3",
    },
    {
        "doc_classification": "memo",
        "patterns": ["%memo%"],
        "priority": "P3",
    },
    # P3 — financial & legal types
    {
        "doc_classification": "begroting",
        "patterns": ["%begroting%"],
        "priority": "P3",
    },
    {
        "doc_classification": "jaarstukken",
        "patterns": ["%jaarstuk%"],
        "priority": "P3",
    },
    {
        "doc_classification": "grondexploitatie",
        "patterns": ["%grondexploitat%"],
        "priority": "P3",
    },
    {
        "doc_classification": "voorbereidingsbesluit",
        "patterns": ["%voorbereidingsbesluit%"],
        "priority": "P3",
    },
    {
        "doc_classification": "rekenkamer",
        "patterns": ["%rekenkamer%"],
        "priority": "P3",
    },
]


def _build_db_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if url:
        return url
    h = os.getenv("DB_HOST", "localhost")
    p = os.getenv("DB_PORT", "5432")
    d = os.getenv("DB_NAME", "neodemos")
    u = os.getenv("DB_USER", "postgres")
    pw = os.getenv("DB_PASSWORD", "")
    return f"postgresql://{u}:{pw}@{h}:{p}/{d}"


def _build_pattern_where(patterns: list[str], exclude_patterns: list[str] | None = None) -> tuple[str, list]:
    """Build WHERE clause for ILIKE patterns (OR logic) with optional exclusions."""
    params: list = []
    include_clauses = " OR ".join("name ILIKE %s" for _ in patterns)
    params.extend(patterns)

    if exclude_patterns:
        exclude_clauses = " AND ".join("name NOT ILIKE %s" for _ in exclude_patterns)
        params.extend(exclude_patterns)
        return f"({include_clauses}) AND ({exclude_clauses})", params

    return f"({include_clauses})", params


def run_backfill(
    conn,
    execute: bool = False,
    skip_p2: bool = False,
    only_new: bool = False,
    only_p3: bool = False,
) -> dict:
    """Run classification backfill. Returns counts per type."""
    stats: dict[str, int] = {}

    # P1 types added in 2026-04-13 audit expansion
    NEW_TYPES = {"raadsvoorstel", "brief_college", "afdoeningsvoorstel", "toezegging"}
    P3_TYPES = {
        "notulen", "verslag", "agenda", "annotatie", "adviezenlijst", "besluitenlijst",
        "ingekomen_stukken", "spreektijdentabel", "transcript", "rapport", "notitie",
        "presentatie", "monitor_rapport", "planning", "bijlage", "memo",
        "begroting", "jaarstukken", "grondexploitatie", "voorbereidingsbesluit", "rekenkamer",
    }

    for target in TARGETS:
        civic_type = target["doc_classification"]
        priority = target["priority"]

        if skip_p2 and priority == "P2":
            logger.info("[WS11a] Skipping %s (P2, --skip-p2 flag)", civic_type)
            continue

        if only_new and civic_type not in NEW_TYPES:
            logger.info("[WS11a] Skipping %s (not a new P1 type, --only-new flag)", civic_type)
            continue

        if only_p3 and civic_type not in P3_TYPES:
            logger.info("[WS11a] Skipping %s (not a P3 type, --only-p3 flag)", civic_type)
            continue

        patterns = target["patterns"]
        exclude_patterns = target.get("exclude_patterns")

        where_clause, params = _build_pattern_where(patterns, exclude_patterns)

        # Count how many are already correctly classified vs. need updating
        count_sql = f"""
            SELECT
                COUNT(*) FILTER (WHERE doc_classification IS NULL) AS needs_set,
                COUNT(*) FILTER (WHERE doc_classification = %s) AS already_correct,
                COUNT(*) FILTER (WHERE doc_classification IS NOT NULL AND doc_classification != %s) AS different
            FROM documents
            WHERE {where_clause}
        """
        cur = conn.cursor()
        cur.execute(count_sql, [civic_type, civic_type] + params)
        row = cur.fetchone()
        cur.close()

        needs_set, already_correct, different = row
        total = needs_set + already_correct + different
        logger.info(
            "[WS11a] %s: total=%d  needs_set=%d  already_correct=%d  different=%d",
            civic_type, total, needs_set, already_correct, different,
        )

        if execute and needs_set > 0:
            update_sql = f"""
                UPDATE documents
                SET doc_classification = %s
                WHERE {where_clause}
                  AND doc_classification IS NULL
            """
            cur = conn.cursor()
            cur.execute(update_sql, [civic_type] + params)
            updated = cur.rowcount
            conn.commit()
            cur.close()
            logger.info("[WS11a] SET doc_classification=%s on %d docs", civic_type, updated)
            stats[civic_type] = updated
        else:
            stats[civic_type] = 0 if execute else needs_set  # in dry-run report what would change

    return stats


def fix_missing_meeting_ids(conn, execute: bool = False) -> dict:
    """Derive meeting_id for initiatiefnotities and initiatiefvoorstellen
    that have no direct meeting_id but can be linked via document_assignments."""

    stats: dict[str, int] = {}

    for civic_type in ("initiatiefnotitie", "initiatiefvoorstel"):
        # Find docs of this type with no meeting_id but an assignment to an agenda_item
        count_sql = """
            SELECT COUNT(*)
            FROM documents d
            JOIN document_assignments da ON da.document_id = d.id
            JOIN agenda_items ai ON ai.id = da.agenda_item_id
            WHERE d.doc_classification = %s
              AND d.meeting_id IS NULL
              AND ai.meeting_id IS NOT NULL
        """
        cur = conn.cursor()
        cur.execute(count_sql, (civic_type,))
        count = cur.fetchone()[0]
        cur.close()
        logger.info("[WS11a] %s: %d docs missing meeting_id but resolvable via assignments", civic_type, count)

        if execute and count > 0:
            update_sql = """
                UPDATE documents d
                SET meeting_id = ai.meeting_id
                FROM document_assignments da
                JOIN agenda_items ai ON ai.id = da.agenda_item_id
                WHERE da.document_id = d.id
                  AND d.doc_classification = %s
                  AND d.meeting_id IS NULL
                  AND ai.meeting_id IS NOT NULL
            """
            cur = conn.cursor()
            cur.execute(update_sql, (civic_type,))
            updated = cur.rowcount
            conn.commit()
            cur.close()
            logger.info("[WS11a] Fixed meeting_id for %d %s docs", updated, civic_type)
            stats[civic_type] = updated
        else:
            stats[civic_type] = 0 if execute else count

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="WS11a: backfill doc_classification on existing docs")
    parser.add_argument("--execute", action="store_true",
                        help="Write changes to DB (default: dry-run, print only)")
    parser.add_argument("--skip-p2", action="store_true",
                        help="Skip motie/amendement (P2 types)")
    parser.add_argument("--only-new", action="store_true",
                        help="Only run P1 additions: raadsvoorstel, brief_college, afdoeningsvoorstel, toezegging")
    parser.add_argument("--only-p3", action="store_true",
                        help="Only run P3 additions: all meeting/procedural/financial doc types")
    args = parser.parse_args()

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    logger.info("=" * 60)
    logger.info("[WS11a] Starting classification backfill — mode: %s", mode)
    logger.info("=" * 60)

    conn = psycopg2.connect(_build_db_url())
    try:
        classify_stats = run_backfill(
            conn,
            execute=args.execute,
            skip_p2=args.skip_p2,
            only_new=args.only_new,
            only_p3=args.only_p3,
        )
        meeting_stats = fix_missing_meeting_ids(conn, execute=args.execute)

        logger.info("=" * 60)
        logger.info("[WS11a] Summary (%s):", mode)
        for k, v in classify_stats.items():
            action = "updated" if args.execute else "would update"
            logger.info("  %s: %s %d docs", k, action, v)
        for k, v in meeting_stats.items():
            action = "fixed meeting_id for" if args.execute else "would fix meeting_id for"
            logger.info("  %s: %s %d docs", k, action, v)

        if not args.execute:
            logger.info("")
            logger.info("Run with --execute to apply changes.")
        logger.info("=" * 60)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
