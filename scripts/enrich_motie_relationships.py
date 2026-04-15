#!/usr/bin/env python3
"""
Motie Relationship Enrichment (WS1 Phase 0.5a)
===============================================

Dedicated Gemini pass targeting motie/amendement chunks that runs BEFORE
the general gemini_semantic_enrichment.py run.

Adds two new edge types to kg_relationships:
  - PROPOSED_BY   Party -> Motie  (proposing party — currently missing entirely)
  - SIGNED_BY     Person -> Motie (LLM-reconciled signatory, confidence-scored;
                                   higher precision than the regex DIENT_IN
                                   baseline at ~75-80%)

Run order:
  1. populate_kg_relationships.py      (DIENT_IN baseline, 57K edges)
  2. THIS SCRIPT                       (PROPOSED_BY + SIGNED_BY)
  3. gemini_semantic_enrichment.py     (general HEEFT_BUDGET / BETREFT_WIJK /
                                        SPREEKT_OVER enrichment)

Acceptance gates (check before proceeding to step 3):
  SELECT COUNT(*) FROM kg_relationships WHERE relation_type = 'PROPOSED_BY';
  -- expect >= 3,000

  SELECT COUNT(*) FROM kg_relationships WHERE relation_type = 'SIGNED_BY';
  -- expect >= 10,000

Advisory lock: key 42 (shared with all KG writers — cannot run concurrently
with populate_kg_relationships.py or gemini_semantic_enrichment.py).

Checkpoint: data/pipeline_state/enrich_motie_checkpoint.json
Rollback:   DELETE FROM kg_relationships WHERE metadata->>'source' = 'gemini_motie_pass';

Cost: ~$2-10 for ~80K motie chunks (Gemini 2.5 Flash-Lite Tier 3).
Default cost-cap: $30.00 (well above expected spend).
"""

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from psycopg2.extras import Json, RealDictCursor
from tqdm import tqdm

load_dotenv()

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)

DB_URL = os.environ["DATABASE_URL"]
ADVISORY_LOCK_KEY = 42
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite-001")
DEFAULT_BATCH_SIZE = 10

CHECKPOINT_DIR = Path("data/pipeline_state")
CHECKPOINT_PATH = CHECKPOINT_DIR / "enrich_motie_checkpoint.json"

COST_INPUT_PER_M = float(os.getenv("GEMINI_COST_INPUT_PER_M", "0.075"))
COST_OUTPUT_PER_M = float(os.getenv("GEMINI_COST_OUTPUT_PER_M", "0.30"))

# Minimum Gemini confidence for a SIGNED_BY edge to be written.
# Below this threshold the signatory is discarded (not a DIENT_IN replacement).
CONFIDENCE_THRESHOLD = 0.85

BACKOFF_SCHEDULE = (2, 4, 8, 16, 32)

# ── Prompt ────────────────────────────────────────────────────────────

PROMPT_SYSTEM = """\
Je bent een annotator voor moties en amendementen van de Rotterdamse gemeenteraad.

Voor elke motie/amendement identificeer je:
1. proposing_party: de VOLLEDIGE partijnaam die de motie heeft ingediend
   (bijv. 'PvdA', 'VVD', 'D66', 'GroenLinks', 'CDA'). Geef null als onduidelijk.
2. signatories: de indieners/ondertekenaars met naam en confidence score 0.0-1.0.

Regels:
- Gebruik known_indieners als hint — normaliseer spelfouten en afkortingen.
- Verzin geen namen die niet in de tekst of in known_indieners voorkomen.
- confidence = 0.95: naam staat volledig en letterlijk in de tekst.
- confidence = 0.85: naam uit known_indieners, bevestigd door context in tekst.
- confidence < 0.85: twijfelgeval — laat weg (wordt niet opgeslagen).
- proposing_party = null als onduidelijk of niet vermeld.
- Retourneer lege signatories lijst als er geen ondertekenaars te identificeren zijn.
Retourneer ALTIJD geldige JSON met een 'results' array.\
"""

PROMPT_USER_TEMPLATE = """\
Analyseer {n} moties/amendementen. Retourneer voor elk de proposing_party en signatories.

Chunks:
{payload}
"""


# ── Stats ─────────────────────────────────────────────────────────────

@dataclass
class Stats:
    processed: int = 0
    batches_done: int = 0
    proposed_by_inserted: int = 0
    signed_by_inserted: int = 0
    edges_rejected: int = 0
    total_cost_usd: float = 0.0


# ── Checkpoint ────────────────────────────────────────────────────────

def load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        try:
            with open(CHECKPOINT_PATH) as f:
                return json.load(f)
        except Exception as exc:
            log.warning(f"Checkpoint unreadable, starting fresh: {exc}")
    return {"last_chunk_id": 0, "processed": 0, "total_cost_usd": 0.0,
            "batches_done": 0, "ts": None}


def save_checkpoint(last_chunk_id: int, stats: Stats) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_chunk_id": last_chunk_id,
        "processed": stats.processed,
        "total_cost_usd": round(stats.total_cost_usd, 6),
        "batches_done": stats.batches_done,
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


# ── KG entity / relationship helpers ──────────────────────────────────

def get_or_create_entity(cur, entity_type: str, entity_name: str) -> int:
    cur.execute(
        "SELECT id FROM kg_entities WHERE type = %s AND name = %s",
        (entity_type, entity_name),
    )
    row = cur.fetchone()
    if row:
        return row[0] if isinstance(row, tuple) else row["id"]
    cur.execute(
        "INSERT INTO kg_entities (type, name, metadata) VALUES (%s, %s, %s) RETURNING id",
        (entity_type, entity_name, Json({})),
    )
    result = cur.fetchone()
    return result[0] if isinstance(result, tuple) else result["id"]


def edge_exists(cur, source_id: int, target_id: int,
                relation_type: str, document_id) -> bool:
    cur.execute(
        """SELECT 1 FROM kg_relationships
           WHERE source_entity_id = %s AND target_entity_id = %s
             AND relation_type = %s AND document_id = %s
           LIMIT 1""",
        (source_id, target_id, relation_type, document_id),
    )
    return cur.fetchone() is not None


# ── Gemini helpers ────────────────────────────────────────────────────

def _call_gemini_with_backoff(model, user_prompt: str):
    import google.generativeai as genai
    last_exc = None
    for attempt, delay in enumerate((*BACKOFF_SCHEDULE, None)):
        try:
            return model.generate_content(
                user_prompt,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                    max_output_tokens=2048,
                ),
            )
        except Exception as exc:
            msg = str(exc).lower()
            retryable = any(m in msg for m in (
                "429", "rate", "quota", "resource exhausted",
                "503", "504", "unavailable", "internal error",
            ))
            if not retryable or delay is None:
                raise
            last_exc = exc
            log.warning(
                f"Gemini API error (attempt {attempt + 1}): {str(exc)[:160]}. "
                f"Retry in {delay}s…"
            )
            time.sleep(delay)
    raise RuntimeError(f"Exhausted retries: {last_exc}")


def parse_gemini_response(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    lo, hi = text.find("{"), text.rfind("}")
    if lo >= 0 and hi > lo:
        try:
            return json.loads(text[lo:hi + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Cannot parse Gemini JSON: {text[:200]!r}")


# ── Batch helpers ─────────────────────────────────────────────────────

def build_batch_payload(rows: list[dict]) -> str:
    items = []
    for r in rows:
        items.append({
            "id": int(r["id"]),
            "doc_name": (r.get("doc_name") or "")[:200],
            "motion_number": r.get("motion_number") or "",
            "known_indieners": r.get("indieners") or [],
            "content": r.get("content") or "",
        })
    return json.dumps({"chunks": items}, ensure_ascii=False)


def apply_batch_results(
    write_cur,
    rows_in_batch: list[dict],
    parsed: dict,
    stats: Stats,
    model_name: str,
    dry_run: bool,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    results_by_id = {
        int(r.get("id")): r
        for r in parsed.get("results", [])
        if isinstance(r, dict) and "id" in r
    }

    for row in rows_in_batch:
        chunk_id = int(row["id"])
        result = results_by_id.get(chunk_id)
        if result is None:
            log.debug(f"chunk {chunk_id} missing from Gemini response")
            stats.processed += 1
            continue

        document_id = row.get("document_id")
        doc_name = row.get("doc_name") or ""
        motion_number = row.get("motion_number") or ""
        motie_label = motion_number or doc_name or f"motie-chunk-{chunk_id}"

        base_meta = {
            "source": "gemini_motie_pass",
            "extractor": "gemini_motie_pass",
            "extracted_at": now_iso,
            "gemini_model": model_name,
        }

        # 1. PROPOSED_BY: Party -> Motie
        proposing_party = (result.get("proposing_party") or "").strip()
        if proposing_party and len(proposing_party) >= 2:
            if dry_run:
                stats.proposed_by_inserted += 1
            else:
                motie_id = get_or_create_entity(write_cur, "Motie", motie_label)
                party_id = get_or_create_entity(write_cur, "Party", proposing_party)
                if not edge_exists(write_cur, party_id, motie_id, "PROPOSED_BY", document_id):
                    write_cur.execute(
                        """INSERT INTO kg_relationships
                           (source_entity_id, target_entity_id, relation_type,
                            document_id, chunk_id, confidence, quote, metadata)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (party_id, motie_id, "PROPOSED_BY",
                         document_id, chunk_id, 0.92, None, Json(base_meta)),
                    )
                    stats.proposed_by_inserted += 1

        # 2. SIGNED_BY: Person -> Motie (high-confidence only; coexists with DIENT_IN)
        for sig in result.get("signatories") or []:
            if not isinstance(sig, dict):
                stats.edges_rejected += 1
                continue
            name = (sig.get("name") or "").strip()
            try:
                confidence = float(sig.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0

            if not name or len(name) < 3 or confidence < CONFIDENCE_THRESHOLD:
                if name and confidence > 0:
                    log.debug(
                        f"  skip signatory {name!r} confidence={confidence:.2f} "
                        f"< threshold {CONFIDENCE_THRESHOLD}"
                    )
                stats.edges_rejected += 1
                continue

            if dry_run:
                stats.signed_by_inserted += 1
                continue

            motie_id = get_or_create_entity(write_cur, "Motie", motie_label)
            person_id = get_or_create_entity(write_cur, "Person", name)
            if not edge_exists(write_cur, person_id, motie_id, "SIGNED_BY", document_id):
                write_cur.execute(
                    """INSERT INTO kg_relationships
                       (source_entity_id, target_entity_id, relation_type,
                        document_id, chunk_id, confidence, quote, metadata)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (person_id, motie_id, "SIGNED_BY",
                     document_id, chunk_id, confidence, None, Json(base_meta)),
                )
                stats.signed_by_inserted += 1

        stats.processed += 1


# ── Main run ──────────────────────────────────────────────────────────

def run(
    limit: int | None,
    batch_size: int,
    resume: bool,
    cost_cap: float,
    model_name: str,
    wait_for_lock: bool,
    dry_run: bool,
) -> int:
    log.info("=" * 64)
    log.info("  MOTIE RELATIONSHIP ENRICHMENT  (WS1 Phase 0.5a)")
    log.info(f"  model      = {model_name}")
    log.info(f"  batch_size = {batch_size}")
    log.info(f"  limit      = {limit or 'unlimited'}")
    log.info(f"  cost_cap   = ${cost_cap:.2f}")
    log.info(f"  dry_run    = {dry_run}")
    log.info("=" * 64)

    read_conn = psycopg2.connect(DB_URL)
    read_conn.autocommit = False
    write_conn = psycopg2.connect(DB_URL)
    write_conn.autocommit = False

    if not dry_run:
        if not acquire_advisory_lock(write_conn, wait=wait_for_lock):
            log.error(
                f"Advisory lock {ADVISORY_LOCK_KEY} is held by another session. "
                "Use --wait-for-lock or wait for the other writer to finish."
            )
            read_conn.close()
            write_conn.close()
            return 4

    model = None
    if not dry_run:
        try:
            import google.generativeai as genai
        except ImportError:
            log.error(
                "google-generativeai not installed. "
                "Run: pip install google-generativeai"
            )
            read_conn.close()
            write_conn.close()
            return 2
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            log.error("GEMINI_API_KEY not set. Export it or use --dry-run.")
            read_conn.close()
            write_conn.close()
            return 2
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name, system_instruction=PROMPT_SYSTEM)

    checkpoint = load_checkpoint() if resume else {
        "last_chunk_id": 0, "processed": 0, "total_cost_usd": 0.0, "batches_done": 0,
    }
    start_id = int(checkpoint.get("last_chunk_id") or 0)

    stats = Stats()
    stats.total_cost_usd = float(checkpoint.get("total_cost_usd") or 0.0)
    stats.batches_done = int(checkpoint.get("batches_done") or 0)

    if resume and start_id > 0:
        log.info(
            f"Resuming from chunk_id > {start_id} "
            f"(${stats.total_cost_usd:.4f} spent so far)"
        )

    motie_filter = """(
        LOWER(d.name) LIKE '%%motie%%'
        OR LOWER(d.name) LIKE '%%amendement%%'
        OR LOWER(d.name) LIKE '%%amendment%%'
    )"""

    count_cur = read_conn.cursor()
    count_cur.execute(
        f"""SELECT COUNT(*) FROM document_chunks dc
            JOIN documents d ON dc.document_id = d.id
            WHERE {motie_filter}
              AND char_length(dc.content) BETWEEN 200 AND 50000
              AND dc.id > %s""",
        (start_id,),
    )
    total = int(count_cur.fetchone()[0])
    count_cur.close()
    if limit:
        total = min(total, limit)
    log.info(f"Motie chunks to process: {total:,}")

    read_cur = read_conn.cursor("motie_reader", cursor_factory=RealDictCursor)
    read_cur.itersize = max(batch_size * 4, 100)
    read_cur.execute(
        f"""SELECT dc.id, dc.document_id, dc.content, dc.indieners, dc.motion_number,
                   d.name AS doc_name
            FROM document_chunks dc
            JOIN documents d ON dc.document_id = d.id
            WHERE {motie_filter}
              AND char_length(dc.content) BETWEEN 200 AND 50000
              AND dc.id > %s
            ORDER BY dc.id""",
        (start_id,),
    )

    write_cur = write_conn.cursor()
    pbar = tqdm(total=total, desc="Motie enrichment", unit="chunk")

    rows_seen = 0
    last_chunk_id = start_id
    batch: list[dict] = []
    exit_code = 0

    try:
        while True:
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

            if stats.total_cost_usd >= cost_cap:
                log.warning(
                    f"Cost cap ${cost_cap:.2f} reached "
                    f"(${stats.total_cost_usd:.4f} spent). Halting cleanly."
                )
                break

            user_prompt = PROMPT_USER_TEMPLATE.format(
                n=len(batch),
                payload=build_batch_payload(batch),
            )

            if dry_run:
                tokens_in = len(user_prompt) // 4
                tokens_out = len(batch) * 30
                stats.total_cost_usd += (
                    (tokens_in / 1e6) * COST_INPUT_PER_M
                    + (tokens_out / 1e6) * COST_OUTPUT_PER_M
                )
                apply_batch_results(
                    write_cur, batch, {"results": []},
                    stats, model_name, dry_run=True,
                )
            else:
                try:
                    response = _call_gemini_with_backoff(model, user_prompt)
                    text = (response.text or "").strip()
                    usage = response.usage_metadata
                    tokens_in = getattr(usage, "prompt_token_count", 0) or 0
                    tokens_out = getattr(usage, "candidates_token_count", 0) or 0
                    cost = (
                        (tokens_in / 1e6) * COST_INPUT_PER_M
                        + (tokens_out / 1e6) * COST_OUTPUT_PER_M
                    )
                    stats.total_cost_usd += cost

                    parsed = parse_gemini_response(text)
                    apply_batch_results(
                        write_cur, batch, parsed,
                        stats, model_name, dry_run=False,
                    )
                    write_conn.commit()
                except Exception as exc:
                    log.warning(
                        f"Batch skipped (chunk_ids {batch[0]['id']}-{batch[-1]['id']}): "
                        f"{str(exc)[:200]}"
                    )
                    write_conn.rollback()

            stats.batches_done += 1
            pbar.update(len(batch))
            batch = []

            if stats.batches_done % 50 == 0:
                save_checkpoint(last_chunk_id, stats)
                log.info(
                    f"  checkpoint: {stats.processed:,} processed, "
                    f"PROPOSED_BY={stats.proposed_by_inserted:,}, "
                    f"SIGNED_BY={stats.signed_by_inserted:,}, "
                    f"cost=${stats.total_cost_usd:.4f}"
                )

    except KeyboardInterrupt:
        log.info("Interrupted — saving checkpoint.")
        exit_code = 1
    finally:
        pbar.close()
        save_checkpoint(last_chunk_id, stats)
        if not dry_run:
            release_advisory_lock(write_conn)
        read_conn.close()
        write_conn.close()

    log.info("=" * 64)
    log.info("MOTIE ENRICHMENT COMPLETE")
    log.info(f"  processed       = {stats.processed:,}")
    log.info(f"  PROPOSED_BY     = {stats.proposed_by_inserted:,}")
    log.info(f"  SIGNED_BY       = {stats.signed_by_inserted:,}")
    log.info(f"  edges_rejected  = {stats.edges_rejected:,}")
    log.info(f"  total_cost_usd  = ${stats.total_cost_usd:.4f}")
    log.info("=" * 64)

    # Print acceptance gate SQL for operator to run
    log.info(
        "\nPost-run acceptance gate SQL:\n"
        "  SELECT COUNT(*) FROM kg_relationships WHERE relation_type = 'PROPOSED_BY';\n"
        "  -- expect >= 3,000\n"
        "  SELECT COUNT(*) FROM kg_relationships WHERE relation_type = 'SIGNED_BY';\n"
        "  -- expect >= 10,000"
    )

    return exit_code


# ── CLI ───────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gemini motie pre-enrichment — WS1 Phase 0.5a. "
                    "Adds PROPOSED_BY (Party→Motie) and SIGNED_BY (Person→Motie) edges.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Estimate cost without Gemini calls or DB writes.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process at most N chunks (smoke test).",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from checkpoint in data/pipeline_state/.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
        help=f"Chunks per Gemini call (default {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--cost-cap", type=float, default=30.00,
        help="Halt cleanly when total_cost_usd >= cap (default 30.00).",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Gemini model id (default {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--wait-for-lock", dest="wait_for_lock",
        action="store_true", default=True,
        help="Block until advisory lock 42 is available (default).",
    )
    parser.add_argument(
        "--no-wait-for-lock", dest="wait_for_lock", action="store_false",
        help="Fail fast if advisory lock 42 is held.",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()
    logging.getLogger().setLevel(args.log_level)

    if not args.dry_run and not os.getenv("GEMINI_API_KEY"):
        log.error("GEMINI_API_KEY not set. Export it or use --dry-run.")
        return 2

    return run(
        limit=args.limit,
        batch_size=args.batch_size,
        resume=args.resume,
        cost_cap=args.cost_cap,
        model_name=args.model,
        wait_for_lock=args.wait_for_lock,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
