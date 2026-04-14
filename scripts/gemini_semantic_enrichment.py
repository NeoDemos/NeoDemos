#!/usr/bin/env python3
"""
Gemini Semantic Enrichment for document_chunks
==============================================

Batched Gemini 2.0/2.5 Flash-Lite enrichment that adds three classes of
metadata to `document_chunks` for NeoDemos v0.2.0 WS1 GraphRAG Phase 0:

    1. answerable_questions  (TEXT[])
       3-5 natural-language Dutch questions each chunk can answer. Populated
       for every chunk that still has NULL answerable_questions.

    2. section_topic         (TEXT, already exists)
       The existing column is pre-populated by scripts/enrich_and_extract.py
       with a rule-based value of the form "<committee> - <truncated doc
       title>". We only OVERWRITE it when Gemini returns a value that is
       strictly more specific than the rule-based one (different text AND
       not a substring match of the existing topic). The rule-based value
       is preserved as metadata.rule_section_topic on any edge created for
       the same chunk, for auditability.

    3. Semantic edges in kg_relationships:

       - HEEFT_BUDGET   Budget entity -> Topic/Document entity
                        Emitted when the chunk mentions a concrete budget
                        line item (bedrag in EUR, begrotingspost, programma).

       - BETREFT_WIJK   Location entity -> Topic/Document entity
                        Emitted when the chunk is geographically scoped to
                        a Rotterdam wijk / buurt / gebied. See BAG RESOLUTION
                        below for the resolution rule against the BAG
                        skeleton written by scripts/import_bag_locations.py.

       - SPREEKT_OVER   Person (speaker) -> Topic entity
                        Emitted when the chunk has a clear speaker attribution
                        (surfaced via the chunk metadata, not via LLM guess).

BAG resolution (why this script must run AFTER import_bag_locations.py)
-----------------------------------------------------------------------
For every BETREFT_WIJK edge Gemini proposes, we try to resolve the
target Location against the BAG-canonical skeleton:

    SELECT id FROM kg_entities
    WHERE type = 'Location'
      AND name = %s
      AND metadata->>'level' IN ('wijk', 'buurt', 'gebied')
    LIMIT 1

Only when that lookup returns zero rows do we UPSERT a generic fallback
Location row (type='Location', name=<as-given>, metadata.level='generic').
This means Gemini output is tied to the authoritative BAG hierarchy
whenever possible, and the fallback rows are visible in audits.

Cost controls
-------------
Gemini 2.5 Flash-Lite pricing (approx, late 2025, USD):

    input : $0.075 per 1M tokens
    output: $0.30  per 1M tokens

Override via env vars if pricing has drifted:

    GEMINI_COST_INPUT_PER_M
    GEMINI_COST_OUTPUT_PER_M

Budget: $90-130 approved for v0.2.0 WS1 Phase 0. This script MUST NOT
exceed --cost-cap (default 130.00 USD). The cap is checked BEFORE each
batch is sent to the API, so a batch that overshoots is never fired —
see `halting strategy` below.

Halting strategy
----------------
End-of-batch cutoff, not mid-batch. Before issuing each Gemini request we
check `stats.total_cost_usd >= cost_cap`. If true we flush any pending DB
writes, save the checkpoint, release the advisory lock, and exit 0. We do
NOT interrupt an in-flight API call, because the SDK gives no refund and
mid-batch teardown would lose the batch's output entirely. This makes the
worst-case overshoot exactly one batch's cost (~$0.02-0.05 at default
settings), which is well within the budget headroom.

--init-schema flag
------------------
The column answerable_questions TEXT[] may not exist on the live DB. We
DO NOT silently run DDL. The operator must pass --init-schema explicitly
to execute:

    ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS answerable_questions text[];

With --init-schema the script runs that one statement, commits, and exits
0 without touching any data or the Gemini API. Without --init-schema and
with the column missing, the script fails fast at startup with a clear
error pointing at the --init-schema flag.

Advisory lock
-------------
Shares lock key 42 with import_bag_locations.py and any other KG writer
that writes to kg_entities/kg_relationships or document_chunks. See
project_embedding_process.md — never write to Qdrant/PostgreSQL while a
background enrichment script holds this lock.

Required dependencies
---------------------
    google-generativeai   # NOT pinned in requirements.txt — install manually
    psycopg2              # already in requirements.txt
    python-dotenv         # already in requirements.txt
    tqdm                  # already in requirements.txt

The google-generativeai import is lazy so that --help and --init-schema
work without the dep installed. You will see a clear ImportError the
first time a real Gemini call is about to fire.

Usage examples
--------------
    # 0. One-time schema bump (requires DB write perms)
    python scripts/gemini_semantic_enrichment.py --init-schema

    # 1. Dry run: estimate cost, print a sample prompt, no API calls
    python scripts/gemini_semantic_enrichment.py --dry-run --limit 100

    # 2. Smoke test: 200 chunks, small batches, low cap
    python scripts/gemini_semantic_enrichment.py --limit 200 \
        --batch-size 10 --cost-cap 2.00

    # 3. Full production run, default cap
    python scripts/gemini_semantic_enrichment.py --resume

Handoff link: docs/handoffs/WS1_GRAPHRAG.md Phase 0
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Project bootstrap ─────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import psycopg2
from psycopg2.extras import Json, RealDictCursor
from tqdm import tqdm


# ── Configuration ─────────────────────────────────────────────────────

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/neodemos")

LOG_PATH = PROJECT_ROOT / "logs" / "gemini_semantic_enrichment.log"
CHECKPOINT_DIR = PROJECT_ROOT / "data" / "pipeline_state"
CHECKPOINT_PATH = CHECKPOINT_DIR / "gemini_enrichment_checkpoint.json"

# Advisory lock shared with other KG writers (import_bag_locations.py,
# Flair NER, etc.). See project_embedding_process.md.
ADVISORY_LOCK_KEY = 42

# Gemini model + pricing
# Pinned to versioned model ID to avoid silent model rollovers mid-run.
# If Google rotates the stable alias, this won't break until we explicitly bump.
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite-001")
# Real 2026-04 prices for gemini-2.5-flash-lite (verified against benchlm.ai + pricepertoken.com).
# Batch API mode would be 50% off ($0.05 / $0.20) but we use interactive for Tier 3 speed.
COST_INPUT_PER_M = float(os.getenv("GEMINI_COST_INPUT_PER_M", "0.10"))
COST_OUTPUT_PER_M = float(os.getenv("GEMINI_COST_OUTPUT_PER_M", "0.40"))
# Cap Gemini output to prevent runaway completions. Each chunk emits ~250-350
# output tokens (3-5 questions + 1-3 edges); 2048 is a generous per-batch-chunk
# cap that gives Gemini room to emit full edge lists without truncating JSON.
MAX_OUTPUT_TOKENS_PER_BATCH_CHUNK = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS_PER_CHUNK", "2048"))

# Allowed edge types + canonical source/target types
ALLOWED_RELATIONS = {"HEEFT_BUDGET", "BETREFT_WIJK", "SPREEKT_OVER"}
ALLOWED_SOURCE_TYPES = {"Budget", "Location", "Person"}
ALLOWED_TARGET_TYPES = {"Topic", "Document"}

# Cap on quote length persisted to kg_relationships.quote
QUOTE_MAX_CHARS = 200


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


# ── Gemini lazy import + structured output schema ────────────────────

def _lazy_import_gemini():
    """
    Import google-generativeai on demand so --help / --init-schema / --dry-run
    don't require the dep. Returns the module.
    """
    try:
        import google.generativeai as genai  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "google-generativeai is not installed. Run "
            "`pip install google-generativeai` and re-run this script."
        ) from exc
    return genai


# JSON schema for Gemini structured output. Kept deliberately flat so the
# SDK's response_schema support works across minor version drift.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "answerable_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "section_topic": {"type": "string"},
                    "edges": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source_name": {"type": "string"},
                                "source_type": {"type": "string"},
                                "target_name": {"type": "string"},
                                "target_type": {"type": "string"},
                                "relation_type": {"type": "string"},
                                "confidence": {"type": "number"},
                                "quote": {"type": "string"},
                            },
                            "required": [
                                "source_name", "source_type",
                                "target_name", "target_type",
                                "relation_type",
                            ],
                        },
                    },
                },
                "required": ["id", "answerable_questions"],
            },
        },
    },
    "required": ["results"],
}


PROMPT_SYSTEM = (
    "Je bent een annotator voor Nederlandse gemeenteraadsdocumenten. "
    "Je analyseert chunks uit Rotterdamse raadsdocumenten (notulen, moties, "
    "amendementen, begrotingen, raadsvoorstellen) en produceert strikt "
    "gestructureerde JSON. Je verzint niets: alleen feiten die letterlijk "
    "in de chunk staan. Elke edge MOET onderbouwd worden met een quote "
    "van maximaal 200 karakters uit het chunk zelf.\n\n"
    "Voor elk chunk genereer je:\n"
    "  - answerable_questions: 3 tot 5 natuurlijke Nederlandse vragen die "
    "dit chunk beantwoordt. Gebruik concrete entiteiten (namen, bedragen, "
    "wijken, jaartallen) in de vragen — geen abstracte vragen zoals "
    "'wat staat er in dit chunk?'.\n"
    "  - section_topic: een specifiek onderwerp van maximaal 80 karakters. "
    "Laat LEEG (empty string of veld weglaten) als het bestaande "
    "rule-based onderwerp al even specifiek is.\n"
    "  - edges: 0 of meer semantische relaties. Gebruik UITSLUITEND de "
    "relatietypes HEEFT_BUDGET, BETREFT_WIJK, SPREEKT_OVER.\n\n"
    "Relatieregels:\n"
    "  - HEEFT_BUDGET: source_type='Budget', target_type='Topic' of "
    "'Document'. source_name is het bedrag + eventueel label (bv. "
    "'EUR 4.5 miljoen jeugdzorg'). Alleen emitten bij expliciete bedragen.\n"
    "  - BETREFT_WIJK: source_type='Location', target_type='Topic' of "
    "'Document'. source_name is de exacte wijk/buurt/gebied-naam zoals "
    "die in Rotterdam bekend is (bv. 'Feijenoord', 'Hillegersberg-Schiebroek'). "
    "GEEN land-, provincie- of stadsnamen.\n"
    "  - SPREEKT_OVER: source_type='Person', target_type='Topic'. "
    "source_name is de volledige naam van de spreker (al aanwezig in het "
    "chunk of in de meegeleverde speaker_hint). target_name is het "
    "onderwerp waar ze over spreken.\n\n"
    "Confidence is een float tussen 0.0 en 1.0, conservatief geschat."
)

PROMPT_USER_TEMPLATE = (
    "Hieronder {n} chunks in JSON. Geef antwoord als een JSON-object met "
    "één veld 'results' dat een array bevat met precies één entry per "
    "chunk (zelfde id terug).\n\n"
    "INPUT:\n{payload}\n\n"
    "OUTPUT FORMAT (strikt):\n"
    "{{\"results\": [{{\"id\": <int>, \"answerable_questions\": [<str>, ...], "
    "\"section_topic\": <str of weggelaten>, \"edges\": "
    "[{{\"source_name\": <str>, \"source_type\": \"Budget\"|\"Location\"|\"Person\", "
    "\"target_name\": <str>, \"target_type\": \"Topic\"|\"Document\", "
    "\"relation_type\": \"HEEFT_BUDGET\"|\"BETREFT_WIJK\"|\"SPREEKT_OVER\", "
    "\"confidence\": <float 0..1>, \"quote\": <str max 200 chars>}}, ...]}}, ...]}}"
)


# ── Stats tracking ───────────────────────────────────────────────────

@dataclass
class Stats:
    processed: int = 0
    batches_done: int = 0
    api_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    total_cost_usd: float = 0.0
    with_new_questions: int = 0
    with_topic_update: int = 0
    edges_inserted: int = 0
    edges_resolved_to_bag: int = 0
    edges_rejected: int = 0
    start_time: float = field(default_factory=time.time)

    def cost_for(self, tokens_in: int, tokens_out: int) -> float:
        return (
            tokens_in * COST_INPUT_PER_M + tokens_out * COST_OUTPUT_PER_M
        ) / 1_000_000

    def record_api_call(self, tokens_in: int, tokens_out: int) -> None:
        self.api_calls += 1
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.total_cost_usd += self.cost_for(tokens_in, tokens_out)

    def report(self) -> str:
        elapsed = time.time() - self.start_time
        rate = self.processed / elapsed if elapsed > 0 else 0.0
        avg_cost = (
            self.total_cost_usd / self.processed if self.processed > 0 else 0.0
        )
        return (
            f"processed={self.processed:,} | "
            f"batches={self.batches_done:,} | "
            f"api_calls={self.api_calls:,} | "
            f"tokens_in={self.tokens_in:,} | "
            f"tokens_out={self.tokens_out:,} | "
            f"cost=${self.total_cost_usd:.4f} | "
            f"avg=${avg_cost:.5f}/chunk | "
            f"new_q={self.with_new_questions:,} | "
            f"topic_upd={self.with_topic_update:,} | "
            f"edges={self.edges_inserted:,} | "
            f"bag={self.edges_resolved_to_bag:,} | "
            f"rej={self.edges_rejected:,} | "
            f"{rate:.1f} chunks/s | "
            f"elapsed={elapsed:.1f}s"
        )


# ── Checkpoint ────────────────────────────────────────────────────────

def load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        try:
            with open(CHECKPOINT_PATH) as f:
                return json.load(f)
        except Exception as exc:
            log.warning(f"Checkpoint unreadable, starting fresh: {exc}")
    return {
        "last_chunk_id": 0,
        "processed": 0,
        "total_cost_usd": 0.0,
        "batches_done": 0,
        "edges_inserted": 0,
        "ts": None,
    }


def save_checkpoint(last_chunk_id: int, stats: Stats) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_chunk_id": last_chunk_id,
        "processed": stats.processed,
        "total_cost_usd": round(stats.total_cost_usd, 6),
        "batches_done": stats.batches_done,
        "edges_inserted": stats.edges_inserted,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    tmp = CHECKPOINT_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    tmp.replace(CHECKPOINT_PATH)


# ── Advisory lock ─────────────────────────────────────────────────────

def acquire_advisory_lock(conn, wait: bool) -> bool:
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


# ── Pre-flight checks (WS1 Phase 1, added 2026-04-14) ────────────────

# Expected BAG state for Rotterdam after a successful import:
#   ~5,000 streets + ~80 buurten + ~40 wijken + 14 gebieden + 1 gemeente ≈ 5,135 rows.
# We check BOTH the full total AND the hierarchy separately so a partial
# import is caught with precise diagnostics:
#   - hierarchy < MIN_BAG_HIERARCHY  → BETREFT_WIJK cannot resolve at all
#   - total < MIN_BAG_TOTAL           → import is incomplete; re-run it
# Streets are "nice-to-have for Gemini specifically" but the pipeline as a
# whole (traceer_motie cites, Flair street-tag canonicalisation, R3
# Heemraadssingel test) needs them. BAG import is ~30 min, idempotent;
# we always require the full state, not the Gemini-minimum state.
MIN_BAG_HIERARCHY = int(os.getenv("GEMINI_MIN_BAG_HIERARCHY", "100"))
MIN_BAG_TOTAL = int(os.getenv("GEMINI_MIN_BAG_TOTAL", "4500"))


def preflight_checks(read_conn, require_bag: bool = True) -> bool:
    """
    Run read-only pre-flight validation before committing to a Gemini run.
    Returns True if all checks pass, False on any failure (with ERROR logs).

    Checks:
      1. BAG location skeleton is imported (>= MIN_BAG_LOCATIONS rows).
         Skippable for scope='all' since we may run experimentally before BAG.
      2. No other active writer on kg_* tables (prevents double-writes).
      3. staging.meetings.quality_score coverage (warns if < 80%).

    Each failure prints a clear "fix this" message.
    """
    log.info("Running pre-flight checks...")
    cur = read_conn.cursor()
    ok = True

    # 1. BAG import completeness — check BOTH hierarchy AND total.
    # Expected post-import: ~5,000 streets + ~80 buurten + ~40 wijken
    # + 14 gebieden + 1 gemeente ≈ 5,135 rows. We want the FULL state
    # because downstream features (traceer_motie cites, Flair street-tag
    # canonicalisation, R3 Heemraadssingel test) all need streets too,
    # not just the hierarchy Gemini strictly requires. BAG import is ~30
    # min + idempotent; a partial state is a bug to fix, not a green light.
    if require_bag:
        cur.execute(
            """
            SELECT
              COUNT(*) AS total_rotterdam,
              COUNT(*) FILTER (WHERE metadata->>'level' = 'straat') AS straten,
              COUNT(*) FILTER (WHERE metadata->>'level' = 'buurt') AS buurten,
              COUNT(*) FILTER (WHERE metadata->>'level' = 'wijk') AS wijken,
              COUNT(*) FILTER (WHERE metadata->>'level' = 'gebied') AS gebieden,
              COUNT(*) FILTER (WHERE metadata->>'level' = 'gemeente') AS gemeenten
            FROM kg_entities
            WHERE type = 'Location'
              AND metadata->>'gemeente' = 'rotterdam'
              AND metadata->>'level' IS NOT NULL
            """
        )
        row = cur.fetchone()
        total = int(row[0])
        straten, buurten, wijken, gebieden, gemeenten = [int(x) for x in row[1:]]
        hier_count = buurten + wijken + gebieden + gemeenten
        detail = (
            f"straten={straten}, buurten={buurten}, wijken={wijken}, "
            f"gebieden={gebieden}, gemeenten={gemeenten}, total={total}"
        )

        if hier_count < MIN_BAG_HIERARCHY:
            log.error(
                f"Pre-flight FAIL: BAG hierarchy rows = {hier_count} "
                f"(need >= {MIN_BAG_HIERARCHY}). {detail}. "
                f"BETREFT_WIJK edges cannot resolve to BAG-canonical IDs. "
                f"Run `python scripts/import_bag_locations.py --gemeente rotterdam`."
            )
            ok = False
        elif total < MIN_BAG_TOTAL:
            log.error(
                f"Pre-flight FAIL: BAG total rows = {total} "
                f"(need >= {MIN_BAG_TOTAL} for full state; expected ~5,135). "
                f"{detail}. The hierarchy is present but streets are missing — "
                f"BAG import was partial. Re-run "
                f"`python scripts/import_bag_locations.py --gemeente rotterdam` "
                f"(idempotent) to finish. Streets are needed for traceer_motie "
                f"cites, Flair NER street-tag canonicalisation, and the R3 "
                f"Heemraadssingel eval-gate test."
            )
            ok = False
        else:
            log.info(f"  [OK] BAG fully imported: {detail}")

    # 2. No other active writers on kg_* tables.
    # Gemini's advisory lock covers other kg-aware scripts, but it does NOT
    # coordinate with the committee_notulen_pipeline, which writes to kg_*
    # without acquiring lock 42. Detect by pg_stat_activity.
    cur.execute(
        """
        SELECT pid, usename, state, LEFT(query, 200) AS query
        FROM pg_stat_activity
        WHERE state = 'active'
          AND pid != pg_backend_pid()
          AND (query ILIKE '%%INSERT INTO kg_%%'
               OR query ILIKE '%%UPDATE kg_%%'
               OR query ILIKE '%%INSERT INTO document_chunks%%'
               OR query ILIKE '%%UPDATE document_chunks%%')
        """
    )
    active_writers = cur.fetchall()
    if active_writers:
        log.error(
            f"Pre-flight FAIL: {len(active_writers)} other active writer(s) on "
            f"kg_* / document_chunks tables. Risk of race. "
            f"Details: {active_writers}"
        )
        ok = False
    else:
        log.info("  [OK] No other active kg_* writers")

    # 3. staging.meetings.quality_score coverage (warn-only).
    # VN provenance confidence multiplier needs this; missing scores default
    # to 0.5 conservative, but low coverage suggests WS12 isn't finished.
    try:
        cur.execute(
            """
            SELECT
              COUNT(*) AS total,
              COUNT(quality_score) AS with_score
            FROM staging.meetings
            """
        )
        row = cur.fetchone()
        total, with_score = int(row[0]), int(row[1])
        if total > 0:
            coverage = 100.0 * with_score / total
            if coverage < 80.0:
                log.warning(
                    f"  [WARN] staging.meetings.quality_score coverage = "
                    f"{coverage:.1f}% ({with_score:,}/{total:,}). "
                    f"Missing scores default to 0.5 conservative. "
                    f"Verify WS12 status if this is unexpected."
                )
            else:
                log.info(
                    f"  [OK] staging.meetings.quality_score coverage: "
                    f"{coverage:.1f}% ({with_score:,}/{total:,})"
                )
    except Exception as exc:
        log.warning(f"  [WARN] Could not check staging.meetings: {exc}")

    cur.close()
    return ok


# ── Schema check / init ───────────────────────────────────────────────

def column_exists(conn, table: str, column: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    found = cur.fetchone() is not None
    cur.close()
    return found


def init_schema(conn) -> None:
    """
    Execute the one-statement schema bump. Idempotent via IF NOT EXISTS.
    Only runs when --init-schema is passed.
    """
    log.info(
        "Running schema init: ALTER TABLE document_chunks ADD COLUMN "
        "IF NOT EXISTS answerable_questions text[];"
    )
    cur = conn.cursor()
    cur.execute(
        "ALTER TABLE document_chunks "
        "ADD COLUMN IF NOT EXISTS answerable_questions text[];"
    )
    conn.commit()
    cur.close()
    log.info("Schema init complete.")


def require_schema(conn) -> None:
    """Fail fast if answerable_questions column is missing."""
    if not column_exists(conn, "document_chunks", "answerable_questions"):
        log.error(
            "document_chunks.answerable_questions column is MISSING. "
            "Re-run with --init-schema to add it (one-time, idempotent)."
        )
        sys.exit(5)


# ── Gemini client ─────────────────────────────────────────────────────

class GeminiClient:
    """
    Thin wrapper around google-generativeai that exposes a single
    `generate_json_batch` method and returns `(parsed_dict, usage)`.

    Uses response_mime_type='application/json' + response_schema when the
    SDK supports it. Falls back to plain JSON prompting if the SDK
    rejects the schema argument (handled at call time with a one-off
    warning).
    """

    def __init__(self, model_name: str):
        genai = _lazy_import_gemini()
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Export it in your shell or .env "
                "before running this script."
            )
        genai.configure(api_key=api_key)
        self._genai = genai
        self.model_name = model_name
        self.model = genai.GenerativeModel(
            model_name, system_instruction=PROMPT_SYSTEM,
        )
        self._schema_supported: bool | None = None

    # Exponential backoff schedule for 429/5xx errors (seconds).
    # Covers rate-limit bursts (first 2 retries) and longer outages (next 3).
    _BACKOFF_SCHEDULE = (2, 4, 8, 16, 32)

    def _parse_json_with_fallback(
        self, text: str, genai, user_prompt: str,
    ) -> dict:
        """
        Parse Gemini's JSON response. Three escalation levels:

          1. Direct ``json.loads`` — happy path, structured output should land here.
          2. Extract the substring between first ``{`` and last ``}`` and retry.
             Handles the occasional leading/trailing stray text even under
             response_mime_type=application/json.
          3. Retry the whole API call once (same prompt). Catches transient
             model-side formatting glitches.

        Raises on exhausting all three. The caller's batch-skip logic catches
        the exception and moves on.
        """
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        lo = text.find("{")
        hi = text.rfind("}")
        if lo >= 0 and hi > lo:
            try:
                return json.loads(text[lo : hi + 1])
            except json.JSONDecodeError:
                pass
        # Final escalation: retry the API call once. The backoff wrapper still
        # handles transient 429s; this retry specifically targets model-side
        # JSON-formatting glitches.
        log.warning(
            "Gemini returned non-JSON output; retrying batch once. "
            "Raw head: %r", text[:200],
        )
        response = self._generate_with_backoff(
            genai, user_prompt,
            use_schema=(self._schema_supported is not False),
        )
        retry_text = (response.text or "").strip()
        if not retry_text:
            raise RuntimeError("Gemini returned empty response on JSON retry")
        try:
            return json.loads(retry_text)
        except json.JSONDecodeError as exc:
            lo2, hi2 = retry_text.find("{"), retry_text.rfind("}")
            if lo2 >= 0 and hi2 > lo2:
                return json.loads(retry_text[lo2 : hi2 + 1])
            raise RuntimeError(
                f"Gemini returned non-JSON on both first try + retry: {exc}\n"
                f"--- first raw ---\n{text[:500]}\n"
                f"--- retry raw ---\n{retry_text[:500]}"
            ) from exc

    def _is_retryable(self, exc: Exception) -> bool:
        """Detect 429s and 5xxs without depending on SDK-internal exception types."""
        msg = str(exc).lower()
        # Common substrings across google-genai SDK error paths:
        # "429", "resource exhausted", "rate limit", "quota", "500", "503",
        # "504", "deadline", "unavailable", "internal error".
        markers = (
            "429", "rate", "quota", "resource exhausted",
            "500", "502", "503", "504",
            "deadline", "unavailable", "internal error", "overloaded",
        )
        return any(m in msg for m in markers)

    def _generate_with_backoff(self, genai, user_prompt: str, use_schema: bool):
        """
        Call generate_content with exponential backoff on transient failures.
        Returns the raw response object. Raises on non-retryable errors or
        after exhausting retries.
        """
        import time as _time

        config_kwargs = {
            "response_mime_type": "application/json",
            "temperature": 0.1,
            "max_output_tokens": MAX_OUTPUT_TOKENS_PER_BATCH_CHUNK * 20,
        }
        if use_schema:
            config_kwargs["response_schema"] = RESPONSE_SCHEMA

        last_exc: Exception | None = None
        for attempt, delay in enumerate((*self._BACKOFF_SCHEDULE, None)):
            try:
                return self.model.generate_content(
                    user_prompt,
                    generation_config=genai.GenerationConfig(**config_kwargs),
                )
            except TypeError:
                # Distinct from retryable: let the caller handle SDK drift.
                raise
            except Exception as exc:
                if not self._is_retryable(exc) or delay is None:
                    raise
                last_exc = exc
                log.warning(
                    f"Gemini API error (attempt {attempt + 1}/{len(self._BACKOFF_SCHEDULE)}): "
                    f"{type(exc).__name__}: {str(exc)[:200]}. "
                    f"Backing off {delay}s…"
                )
                _time.sleep(delay)
        raise RuntimeError(
            f"Gemini API exhausted retries after {len(self._BACKOFF_SCHEDULE)} attempts: {last_exc}"
        )

    def generate_json_batch(
        self, user_prompt: str,
    ) -> tuple[dict, tuple[int, int]]:
        """
        Returns (parsed_json_dict, (tokens_in, tokens_out)).
        Raises on unrecoverable errors.

        2026-04-14: added exponential backoff on 429/5xx via
        _generate_with_backoff, retry-once on malformed JSON, and an explicit
        max_output_tokens cap so the SDK can't silently truncate a long
        response into invalid JSON.
        """
        genai = self._genai

        # First attempt: use structured output.
        if self._schema_supported is not False:
            try:
                response = self._generate_with_backoff(genai, user_prompt, use_schema=True)
                self._schema_supported = True
            except TypeError as exc:
                # Older SDK without response_schema kwarg.
                log.warning(
                    f"SDK rejected response_schema kwarg ({exc}); "
                    f"falling back to JSON-prompt mode for the rest of the run."
                )
                self._schema_supported = False
                response = self._generate_with_backoff(genai, user_prompt, use_schema=False)
        else:
            response = self._generate_with_backoff(genai, user_prompt, use_schema=False)

        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("Gemini returned an empty response")

        parsed = self._parse_json_with_fallback(text, genai, user_prompt)

        # Usage metadata: field names vary across SDK versions.
        usage_meta = getattr(response, "usage_metadata", None)
        tokens_in = int(getattr(usage_meta, "prompt_token_count", 0) or 0)
        tokens_out = int(
            getattr(usage_meta, "candidates_token_count", 0)
            or getattr(usage_meta, "output_token_count", 0)
            or 0
        )
        return parsed, (tokens_in, tokens_out)


# ── Entity resolution ─────────────────────────────────────────────────

def resolve_bag_location(cur, name: str) -> int | None:
    """
    Resolve a wijk/buurt/gebied name to a BAG-canonical Location id from
    kg_entities. Returns None if no BAG row matches — caller decides
    whether to UPSERT a generic fallback.
    """
    cur.execute(
        """
        SELECT id FROM kg_entities
        WHERE type = 'Location'
          AND name = %s
          AND metadata->>'level' IN ('wijk', 'buurt', 'gebied')
        LIMIT 1
        """,
        (name,),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def upsert_entity(
    cur, ent_type: str, name: str, metadata: dict,
) -> int:
    """
    Insert-or-get a kg_entities row by (type, name). Returns the id.
    Matches the convention in scripts/import_bag_locations.py.
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
        return int(row[0])

    cur.execute(
        "SELECT id FROM kg_entities WHERE type = %s AND name = %s",
        (ent_type, name),
    )
    existing = cur.fetchone()
    if existing is None:
        raise RuntimeError(
            f"upsert_entity: conflict said existing but select empty for "
            f"({ent_type!r}, {name!r})"
        )
    return int(existing[0])


def resolve_source_entity(
    cur, source_type: str, source_name: str, stats: Stats,
) -> int:
    """Resolve a Gemini-proposed source entity into a kg_entities id."""
    if source_type == "Location":
        bag_id = resolve_bag_location(cur, source_name)
        if bag_id is not None:
            stats.edges_resolved_to_bag += 1
            return bag_id
        return upsert_entity(
            cur, "Location", source_name,
            {"level": "generic", "source": "gemini_semantic_enrichment"},
        )
    if source_type == "Budget":
        return upsert_entity(
            cur, "Budget", source_name,
            {"source": "gemini_semantic_enrichment"},
        )
    if source_type == "Person":
        return upsert_entity(
            cur, "Person", source_name,
            {"source": "gemini_semantic_enrichment"},
        )
    raise ValueError(f"Unknown source_type: {source_type}")


def resolve_target_entity(
    cur, target_type: str, target_name: str, document_id: str | None,
) -> int:
    """
    Resolve a Gemini-proposed target entity. Topic/Document rows get
    upserted with a source tag.
    """
    if target_type == "Topic":
        return upsert_entity(
            cur, "Topic", target_name,
            {"source": "gemini_semantic_enrichment"},
        )
    if target_type == "Document":
        # If Gemini echoes back the document name as the target, still
        # upsert a Document-typed kg_entities row so we have a stable id;
        # the document_id column on kg_relationships carries the true
        # provenance back to the documents table.
        return upsert_entity(
            cur, "Document", target_name,
            {
                "source": "gemini_semantic_enrichment",
                "document_id": document_id,
            },
        )
    raise ValueError(f"Unknown target_type: {target_type}")


# ── Prompt construction ───────────────────────────────────────────────

def build_chunk_payload(rows: list[dict]) -> str:
    """
    Serialise a batch of chunk rows into the JSON blob the prompt embeds.

    2026-04-14: removed the 4000-char truncation (WS1 Phase 1 guidance).
    Truncation was cutting off motie signatories at document bottom, which
    the rule-based enrichment depends on. Smart chunk selection in the
    SELECT query (build_chunk_selection_clause below) avoids sending
    oversized chunks in the first place, so raw content is passed through.
    P90 chunk length across all doc types is <= 2,545 chars, so batches
    of 20 full-content chunks stay under 15K input tokens per call.
    """
    items = []
    for r in rows:
        items.append({
            "id": int(r["id"]),
            "title": (r.get("title") or "")[:200],
            "doc_name": (r.get("doc_name") or "")[:200],
            "meeting_name": (r.get("meeting_name") or "")[:200],
            "existing_topic": r.get("section_topic") or "",
            "speaker_hint": r.get("speaker_hint") or "",
            "content": r.get("content") or "",
        })
    return json.dumps({"chunks": items}, ensure_ascii=False)


# Max chunk length to feed Gemini. Chunks above this threshold are rare
# (<5% of corpus) and usually represent whole sections or table dumps that
# need a doc-level pass (WS10), not a per-chunk enrichment. Filter them out
# in the SELECT rather than truncating.
MAX_CHUNK_LENGTH_FOR_GEMINI = int(os.getenv("GEMINI_MAX_CHUNK_CHARS", "15000"))
MIN_CHUNK_LENGTH_FOR_GEMINI = int(os.getenv("GEMINI_MIN_CHUNK_CHARS", "200"))


def build_chunk_selection_clause(
    scope: str = "p1_p2",
    year_from: int | None = None,
) -> tuple[str, list]:
    """
    Return (WHERE clause fragment, params list) for the SELECT query that
    chooses which chunks to enrich. Replaces the pre-2026-04-14 "enrich
    everything and truncate" approach with explicit value-based targeting.

    Scopes (set via CLI --scope):
      - 'p1':      must-have — high edge yield (moties, amendementen,
                   initiatiefvoorstel, afdoening, raadsvoorstel, financial,
                   speaker-attributed notulen). ~600K chunks.
      - 'p1_p2':   default — P1 + recent briefs + key_entities-tagged "other".
                   ~885K chunks, ~$130 at Tier 3 interactive.
      - 'all':     full corpus, no filter. ~1.7M chunks, ~$260. Opt-in only.

    Always applies: min/max chunk length, content not null, date filter.
    """
    filters = [
        "dc.content IS NOT NULL",
        "LENGTH(dc.content) >= %s",
        "LENGTH(dc.content) <= %s",
    ]
    params: list = [MIN_CHUNK_LENGTH_FOR_GEMINI, MAX_CHUNK_LENGTH_FOR_GEMINI]

    if scope == "all":
        # No doc-type filter; still respect length bounds.
        pass
    else:
        p1_patterns = [
            "LOWER(d.name) LIKE '%%motie%%'",
            "LOWER(d.name) LIKE '%%amendement%%'",
            "LOWER(d.name) LIKE '%%initiatiefvoorstel%%'",
            "LOWER(d.name) LIKE '%%afdoening%%'",
            "LOWER(d.name) LIKE '%%raadsvoorstel%%'",
            "LOWER(d.name) LIKE '%%fin_%%'",
            "LOWER(d.name) LIKE '%%begroting%%'",
            "LOWER(d.name) LIKE '%%jaarstuk%%'",
            "LOWER(d.name) LIKE '%%voorjaars%%'",
            # Speaker-attributed notulen only (filters out agenda-only chunks)
            "(LOWER(d.name) ~ '(notulen|verslag)' AND dc.content ~ 'De heer|mevrouw|de voorzitter')",
        ]
        conds = [f"({' OR '.join(p1_patterns)})"]

        if scope == "p1_p2":
            # Add P2 filters: recent briefs + chunks already tagged with entities.
            p2_patterns = [
                # Recent briefs (2020+)
                "(LOWER(d.name) LIKE '%%brief%%' AND m.start_date >= '2020-01-01')",
                # Any chunk the Phase 0 gazetteer pass identified as substantive
                "(dc.key_entities IS NOT NULL AND array_length(dc.key_entities, 1) > 0)",
            ]
            conds.append(f"({' OR '.join(p2_patterns)})")
            filters.append(f"({' OR '.join(conds)})")
        else:
            # scope == 'p1'
            filters.append(" AND ".join(conds))

    if year_from is not None:
        filters.append("m.start_date >= %s")
        params.append(f"{int(year_from)}-01-01")

    return " AND ".join(filters), params


def build_user_prompt(rows: list[dict]) -> str:
    return PROMPT_USER_TEMPLATE.format(
        n=len(rows),
        payload=build_chunk_payload(rows),
    )


# ── Per-batch processing ─────────────────────────────────────────────

def is_topic_more_specific(new: str, old: str | None) -> bool:
    """
    A Gemini topic overrides the rule-based one only when:
      - it is non-empty
      - it differs from the existing value (case-insensitive)
      - it is not a substring of the existing value (i.e. strictly more
        specific, not just a truncation)
    """
    if not new:
        return False
    new = new.strip()
    if not new:
        return False
    if not old:
        return True
    old = old.strip()
    if new.lower() == old.lower():
        return False
    if new.lower() in old.lower():
        return False
    return True


def apply_results_to_db(
    write_cur,
    rows_in_batch: list[dict],
    parsed: dict,
    stats: Stats,
    model_name: str,
    dry_run: bool,
) -> None:
    """
    Write Gemini results to document_chunks + kg_relationships.
    Any row missing from `parsed["results"]` is silently skipped (logged).
    """
    results_by_id = {
        int(r.get("id")): r
        for r in parsed.get("results", [])
        if isinstance(r, dict) and "id" in r
    }

    now_iso = datetime.now(timezone.utc).isoformat()

    for row in rows_in_batch:
        chunk_id = int(row["id"])
        result = results_by_id.get(chunk_id)
        if result is None:
            log.debug(f"chunk {chunk_id} missing from Gemini response")
            continue

        # 1. answerable_questions
        questions = result.get("answerable_questions") or []
        questions = [
            q.strip() for q in questions
            if isinstance(q, str) and q.strip()
        ][:5]

        # 2. section_topic
        proposed_topic = result.get("section_topic")
        existing_topic = row.get("section_topic")
        new_topic: str | None = None
        if isinstance(proposed_topic, str) and is_topic_more_specific(
            proposed_topic, existing_topic,
        ):
            new_topic = proposed_topic.strip()[:200]
            stats.with_topic_update += 1

        if questions:
            stats.with_new_questions += 1

        if not dry_run:
            if new_topic is not None:
                write_cur.execute(
                    """
                    UPDATE document_chunks
                    SET answerable_questions = %s,
                        section_topic = %s
                    WHERE id = %s
                    """,
                    (questions, new_topic, chunk_id),
                )
            else:
                write_cur.execute(
                    """
                    UPDATE document_chunks
                    SET answerable_questions = %s
                    WHERE id = %s
                    """,
                    (questions, chunk_id),
                )

        # 3. edges
        #
        # VN provenance layer (WS1 Phase A bis): every kg_relationships row
        # we write must carry source_quality metadata so the graph walk can
        # filter / downweight VN-derived edges at query time. We compute
        # source_quality ONCE per chunk (not per edge) because it's a
        # property of the source document:
        #
        #   - documents.is_virtual_notulen = false  -> 1.0  (authoritative)
        #   - is_virtual_notulen = true, quality_score present  -> the score
        #   - is_virtual_notulen = true, quality_score NULL     -> 0.5
        #
        # The multiplication `gemini_confidence * source_quality` happens
        # BEFORE the INSERT, so VN edges rank lower in graph walk path
        # scoring automatically without any reader-side logic.
        is_vn = bool(row.get("is_virtual_notulen"))
        raw_qs = row.get("vn_quality_score")
        if not is_vn:
            source_quality = 1.0
        elif raw_qs is None:
            source_quality = 0.5
        else:
            try:
                source_quality = float(raw_qs)
            except (TypeError, ValueError):
                source_quality = 0.5
        # Clamp into [0,1] defensively in case staging ever stores
        # out-of-range values.
        source_quality = max(0.0, min(1.0, source_quality))
        source_meeting_id = row.get("source_meeting_id")

        edges = result.get("edges") or []
        for edge in edges:
            if not isinstance(edge, dict):
                stats.edges_rejected += 1
                continue
            relation_type = edge.get("relation_type")
            source_type = edge.get("source_type")
            target_type = edge.get("target_type")
            source_name = (edge.get("source_name") or "").strip()
            target_name = (edge.get("target_name") or "").strip()

            if (
                relation_type not in ALLOWED_RELATIONS
                or source_type not in ALLOWED_SOURCE_TYPES
                or target_type not in ALLOWED_TARGET_TYPES
                or not source_name
                or not target_name
            ):
                stats.edges_rejected += 1
                continue

            # Pairing check: every relation type has an expected source shape
            if relation_type == "HEEFT_BUDGET" and source_type != "Budget":
                stats.edges_rejected += 1
                continue
            if relation_type == "BETREFT_WIJK" and source_type != "Location":
                stats.edges_rejected += 1
                continue
            if relation_type == "SPREEKT_OVER" and source_type != "Person":
                stats.edges_rejected += 1
                continue

            try:
                gemini_confidence = float(edge.get("confidence") or 0.5)
            except (TypeError, ValueError):
                gemini_confidence = 0.5
            gemini_confidence = max(0.0, min(1.0, gemini_confidence))
            # VN provenance: multiply raw Gemini confidence by the source
            # quality BEFORE the INSERT so the persisted value reflects both
            # extractor certainty and source trust in one scalar. Graph walk
            # path scoring consumes kg_relationships.confidence directly, so
            # this makes VN-derived edges rank lower without any extra
            # reader-side plumbing.
            confidence = max(0.0, min(1.0, gemini_confidence * source_quality))

            quote = (edge.get("quote") or "").strip()[:QUOTE_MAX_CHARS]

            if dry_run:
                stats.edges_inserted += 1
                continue

            src_id = resolve_source_entity(
                write_cur, source_type, source_name, stats,
            )
            tgt_id = resolve_target_entity(
                write_cur, target_type, target_name,
                row.get("document_id"),
            )

            # Provenance metadata contract (WS1 Phase A bis). Keys:
            #   source               — legacy script-level tag (preserved)
            #   source_quality       — VN quality multiplier applied above
            #   source_meeting_id    — staging meeting id, or null for non-VN
            #   source_doc_id        — documents.id for this chunk, or null
            #   extractor            — which component produced the edge
            #   extracted_at         — ISO-8601 UTC of THIS batch
            #   gemini_model         — model id for this run
            #   gemini_ts            — same as extracted_at, kept for
            #                          backwards compat with earlier edges
            #   rule_section_topic   — preserved (pre-existing key)
            metadata = {
                "source": "gemini_flash_lite",
                "source_quality": source_quality,
                "source_meeting_id": source_meeting_id,
                "source_doc_id": row.get("document_id"),
                "extractor": "gemini_flash_lite",
                "extracted_at": now_iso,
                "gemini_model": model_name,
                "gemini_ts": now_iso,
                "rule_section_topic": existing_topic,
            }

            write_cur.execute(
                """
                INSERT INTO kg_relationships
                    (source_entity_id, target_entity_id, relation_type,
                     document_id, chunk_id, confidence, quote, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    src_id, tgt_id, relation_type,
                    row.get("document_id"), chunk_id,
                    confidence, quote, Json(metadata),
                ),
            )
            stats.edges_inserted += 1

        stats.processed += 1


# ── Main run loop ─────────────────────────────────────────────────────

def run(
    limit: int | None,
    batch_size: int,
    resume: bool,
    cost_cap: float,
    model_name: str,
    wait_for_lock: bool,
    skip_enriched: bool,
    dry_run: bool,
    scope: str = "p1_p2",
    year_from: int | None = None,
) -> int:
    log.info("=" * 64)
    log.info("  GEMINI SEMANTIC ENRICHMENT")
    log.info(f"  model         = {model_name}")
    log.info(f"  batch_size    = {batch_size}")
    log.info(f"  limit         = {limit or 'unlimited'}")
    log.info(f"  resume        = {resume}")
    log.info(f"  cost_cap      = ${cost_cap:.2f}")
    log.info(f"  skip_enriched = {skip_enriched}")
    log.info(f"  dry_run       = {dry_run}")
    log.info(f"  cost rates    = ${COST_INPUT_PER_M}/M in, ${COST_OUTPUT_PER_M}/M out")
    log.info("=" * 64)

    # ── Connect ──────────────────────────────────────────────────────
    read_conn = psycopg2.connect(DB_URL)
    read_conn.autocommit = False
    write_conn = psycopg2.connect(DB_URL)
    write_conn.autocommit = False

    # Schema gate (fail-fast, no DDL)
    require_schema(read_conn)

    # ── WS1 Phase 1 pre-flight (added 2026-04-14) ───────────────────
    # These checks catch predictable failure modes BEFORE any budget is
    # spent. Each failure returns a distinct exit code so the runbook can
    # give a precise "fix this" message.
    if not dry_run:
        if not preflight_checks(read_conn, require_bag=(scope != "all")):
            read_conn.close()
            write_conn.close()
            return 5

    # Advisory lock on the write connection (so commits and lock live on
    # the same session).
    if not dry_run:
        if not acquire_advisory_lock(write_conn, wait=wait_for_lock):
            log.error(
                f"Advisory lock {ADVISORY_LOCK_KEY} is held by another session "
                f"and --no-wait-for-lock was passed. Aborting."
            )
            read_conn.close()
            write_conn.close()
            return 4

    # ── Gemini client ────────────────────────────────────────────────
    client: GeminiClient | None = None
    if not dry_run:
        client = GeminiClient(model_name)

    # ── Checkpoint ───────────────────────────────────────────────────
    checkpoint = load_checkpoint() if resume else {
        "last_chunk_id": 0, "processed": 0, "total_cost_usd": 0.0,
        "batches_done": 0, "edges_inserted": 0, "ts": None,
    }
    start_id: int = int(checkpoint.get("last_chunk_id") or 0)

    stats = Stats()
    stats.total_cost_usd = float(checkpoint.get("total_cost_usd") or 0.0)
    stats.batches_done = int(checkpoint.get("batches_done") or 0)
    stats.edges_inserted = int(checkpoint.get("edges_inserted") or 0)

    if resume and start_id > 0:
        log.info(
            f"Resuming from chunk id > {start_id} "
            f"({checkpoint.get('processed', 0):,} chunks processed, "
            f"${stats.total_cost_usd:.4f} spent so far)"
        )

    # ── Smart chunk selection (WS1 Phase 1, 2026-04-14) ──────────────
    # Replaces the old "all chunks, truncate content" strategy with explicit
    # value-based targeting. See build_chunk_selection_clause() for scope defs.
    selection_clause, selection_params = build_chunk_selection_clause(
        scope=scope, year_from=year_from,
    )
    where = ["dc.id > %s", selection_clause]
    params: list[Any] = [start_id, *selection_params]
    if skip_enriched:
        where.append("dc.answerable_questions IS NULL")

    log.info(f"  scope         = {scope}")
    if year_from is not None:
        log.info(f"  year_from     = {year_from}")

    # ── Count for progress bar ───────────────────────────────────────
    count_cur = read_conn.cursor()
    count_cur.execute(
        f"""SELECT COUNT(*) FROM document_chunks dc
            JOIN documents d ON dc.document_id = d.id
            LEFT JOIN meetings m ON d.meeting_id = m.id
            WHERE {' AND '.join(where)}""",
        params,
    )
    total = int(count_cur.fetchone()[0])
    count_cur.close()
    if limit:
        total = min(total, limit)
    log.info(f"Chunks to process: {total:,}")

    # ── Server-side reader ───────────────────────────────────────────
    read_cur = read_conn.cursor(
        "gemini_reader", cursor_factory=RealDictCursor,
    )
    read_cur.itersize = max(batch_size * 4, 200)
    read_cur.execute(
        f"""
        SELECT dc.id, dc.document_id, dc.title, dc.content, dc.section_topic,
               d.name AS doc_name,
               d.is_virtual_notulen AS is_virtual_notulen,
               d.meeting_id AS source_meeting_id,
               m.name AS meeting_name,
               sm.quality_score AS vn_quality_score
        FROM document_chunks dc
        JOIN documents d ON dc.document_id = d.id
        LEFT JOIN meetings m ON d.meeting_id = m.id
        LEFT JOIN staging.meetings sm ON sm.id = d.meeting_id
        WHERE {' AND '.join(where)}
        ORDER BY dc.id
        """,
        params,
    )

    write_cur = write_conn.cursor()
    pbar = tqdm(total=total, desc="Gemini enrichment", unit="chunk")

    rows_seen = 0
    last_chunk_id = start_id
    batch: list[dict] = []
    halted_for_cost = False
    exit_code = 0

    try:
        while True:
            # Fill one batch
            while len(batch) < batch_size:
                if limit and rows_seen >= limit:
                    break
                row = read_cur.fetchone()
                if row is None:
                    break
                batch.append(dict(row))
                rows_seen += 1
                last_chunk_id = int(row["id"])

            if not batch:
                break

            # End-of-batch cost-cap check (see halting strategy in docstring)
            if stats.total_cost_usd >= cost_cap:
                log.warning(
                    f"Cost cap reached (${stats.total_cost_usd:.4f} >= "
                    f"${cost_cap:.2f}). Flushing and halting cleanly."
                )
                halted_for_cost = True
                break

            user_prompt = build_user_prompt(batch)

            if dry_run:
                # Rough estimate: 1 token ~= 4 chars. Assume ~500 output
                # tokens per chunk (questions + edges + topic).
                approx_in = len(user_prompt) // 4
                approx_out = 500 * len(batch)
                est_cost = stats.cost_for(approx_in, approx_out)
                stats.record_api_call(approx_in, approx_out)
                log.info(
                    f"[dry-run] batch={len(batch)} approx_in={approx_in} "
                    f"approx_out={approx_out} est_cost=${est_cost:.5f}"
                )
                if stats.batches_done == 0:
                    log.info(
                        f"[dry-run] sample prompt (first 800 chars):\n"
                        f"{user_prompt[:800]}"
                    )
                # Fake an empty results payload so apply_results_to_db
                # still counts chunks as processed.
                fake = {
                    "results": [
                        {"id": r["id"], "answerable_questions": []}
                        for r in batch
                    ]
                }
                apply_results_to_db(
                    write_cur, batch, fake, stats,
                    model_name, dry_run=True,
                )
            else:
                assert client is not None
                try:
                    parsed, usage = client.generate_json_batch(user_prompt)
                except Exception as exc:
                    log.error(
                        f"Gemini call failed on batch of {len(batch)} chunks "
                        f"(last_id={last_chunk_id}): {exc}"
                    )
                    # Skip this batch, commit whatever we had, keep going.
                    write_conn.commit()
                    save_checkpoint(
                        last_chunk_id - len(batch), stats,
                    )
                    batch = []
                    continue

                stats.record_api_call(usage[0], usage[1])
                apply_results_to_db(
                    write_cur, batch, parsed, stats,
                    model_name, dry_run=False,
                )
                write_conn.commit()

            stats.batches_done += 1
            pbar.update(len(batch))
            save_checkpoint(last_chunk_id, stats)

            log.info(
                f"[batch {stats.batches_done:>5}] {stats.report()}"
            )

            # Rejection-rate threshold: if > 20% of attempted edges are
            # being dropped (schema mismatch, bad pairing, ...), the prompt
            # or model is off and we're burning budget. Log a WARNING every
            # time we cross the threshold on a 500-edge window.
            _total_edges = stats.edges_inserted + stats.edges_rejected
            if _total_edges > 500:
                _rej_rate = stats.edges_rejected / _total_edges
                if _rej_rate > 0.20 and stats.batches_done % 10 == 0:
                    log.warning(
                        f"Edge rejection rate {_rej_rate*100:.1f}% "
                        f"({stats.edges_rejected:,}/{_total_edges:,}) "
                        f"exceeds 20% threshold. Prompt or schema may be off — "
                        f"inspect recent edges_rejected in logs."
                    )

            if limit and rows_seen >= limit:
                break

            batch = []

    except KeyboardInterrupt:
        log.warning("Interrupted! Flushing and saving checkpoint...")
        if not dry_run:
            try:
                write_conn.commit()
            except Exception as exc:
                log.warning(f"Commit on interrupt failed: {exc}")
        save_checkpoint(last_chunk_id, stats)
        exit_code = 130
    except Exception:
        log.exception("Fatal error during enrichment")
        save_checkpoint(last_chunk_id, stats)
        exit_code = 1
    finally:
        pbar.close()
        read_cur.close()
        read_conn.close()
        try:
            if not dry_run:
                release_advisory_lock(write_conn)
        finally:
            write_cur.close()
            write_conn.close()

    # ── Summary ──────────────────────────────────────────────────────
    log.info("=" * 64)
    log.info("  ENRICHMENT COMPLETE" + ("  (cost-cap halt)" if halted_for_cost else ""))
    log.info(f"  {stats.report()}")
    log.info(f"  last_chunk_id={last_chunk_id}")
    log.info("=" * 64)
    return exit_code


# ── CLI ───────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gemini semantic enrichment for document_chunks "
                    "(answerable_questions, section_topic, semantic edges).",
    )
    parser.add_argument(
        "--init-schema", action="store_true",
        help="Run the one-time ADD COLUMN IF NOT EXISTS answerable_questions "
             "text[] on document_chunks, then exit.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Build prompts and estimate cost; no Gemini calls, no DB writes.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process at most N chunks (smoke test).",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from last checkpoint in data/pipeline_state/.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=20,
        help="Chunks per Gemini call (default 20).",
    )
    parser.add_argument(
        "--cost-cap", type=float, default=130.00,
        help="Halt cleanly when total_cost_usd >= cap (default 130.00).",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Gemini model id (default {DEFAULT_MODEL}, "
             f"override with GEMINI_MODEL env var).",
    )
    parser.add_argument(
        "--wait-for-lock", dest="wait_for_lock",
        action="store_true", default=True,
        help="Block until advisory lock 42 is available (default).",
    )
    parser.add_argument(
        "--no-wait-for-lock", dest="wait_for_lock", action="store_false",
        help="Fail fast if advisory lock 42 is held by another session.",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default INFO).",
    )
    parser.add_argument(
        "--skip-enriched", dest="skip_enriched",
        action="store_true", default=True,
        help="Skip chunks where answerable_questions is already populated "
             "(default).",
    )
    parser.add_argument(
        "--no-skip-enriched", dest="skip_enriched", action="store_false",
        help="Re-enrich chunks even if answerable_questions is already set.",
    )
    parser.add_argument(
        "--scope", choices=["p1", "p1_p2", "all"], default="p1_p2",
        help="Chunk-selection scope (WS1 Phase 1): 'p1' = must-have "
             "(moties/amendementen/financial/speaker-notulen, ~600K chunks), "
             "'p1_p2' = default (P1 + recent briefs + key_entities-tagged, "
             "~885K chunks, ~$130 at Tier 3), 'all' = full corpus opt-in "
             "(~1.7M chunks, ~$260).",
    )
    parser.add_argument(
        "--year-from", dest="year_from", type=int, default=None,
        help="Optional year floor (e.g. 2018). Chunks from meetings before "
             "this year are skipped. Applied on top of --scope.",
    )
    args = parser.parse_args()

    logging.getLogger().setLevel(args.log_level)

    # ── --init-schema short-circuit ──────────────────────────────────
    if args.init_schema:
        conn = psycopg2.connect(DB_URL)
        try:
            init_schema(conn)
        finally:
            conn.close()
        return 0

    # ── Fail-fast on missing API key (unless dry-run) ────────────────
    if not args.dry_run and not os.getenv("GEMINI_API_KEY"):
        log.error(
            "GEMINI_API_KEY is not set. Export it in your shell or .env, "
            "or re-run with --dry-run to skip API calls."
        )
        return 2

    if args.cost_cap <= 0:
        log.error(f"--cost-cap must be > 0 (got {args.cost_cap})")
        return 2

    return run(
        limit=args.limit,
        batch_size=args.batch_size,
        resume=args.resume,
        cost_cap=args.cost_cap,
        model_name=args.model,
        wait_for_lock=args.wait_for_lock,
        skip_enriched=args.skip_enriched,
        dry_run=args.dry_run,
        scope=args.scope,
        year_from=args.year_from,
    )


if __name__ == "__main__":
    sys.exit(main())
