#!/usr/bin/env python3
"""
Populate kg_relationships with deterministic edges (no LLM calls).

Sources:
  1. politician_registry  -> LID_VAN (person -> party)
  2. raadslid_rollen      -> IS_WETHOUDER_VAN (wethouder -> beleidsgebied)
  3. document_chunks      -> STEMT_VOOR / STEMT_TEGEN (party -> motie)
  4. document_chunks      -> AANGENOMEN / VERWORPEN (motie -> vergadering)
  5. document_chunks      -> DIENT_IN (person -> motie)

Usage:
    python scripts/populate_kg_relationships.py
    python scripts/populate_kg_relationships.py --dry-run
    python scripts/populate_kg_relationships.py --limit 500
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import psycopg2
from psycopg2.extras import RealDictCursor, Json

from services.party_utils import extract_parties_from_text, CANONICAL_PARTIES, normalize_party, PARTY_ALIASES

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/neodemos")

BATCH_SIZE = 500

# ── Regexes for vote-party extraction ────────────────────────────────

# "De fracties van VVD, D66, PvdA stemmen voor"
RE_FRACTIES_VOOR = re.compile(
    r"(?:fracties?\s+van\s+)([\w\s,/\-]+?)\s+(?:stemmen?\s+voor|hebben?\s+voor\s+gestemd)",
    re.IGNORECASE,
)
RE_FRACTIES_TEGEN = re.compile(
    r"(?:fracties?\s+van\s+)([\w\s,/\-]+?)\s+(?:stemmen?\s+tegen|hebben?\s+tegen\s+gestemd)",
    re.IGNORECASE,
)

# "voor: VVD, CDA, D66" / "tegen: SP, PvdD"
RE_VOOR_LIJST = re.compile(
    r"voor\s*:\s*([\w\s,/\-]+?)(?:\.|;|\n|tegen|$)", re.IGNORECASE
)
RE_TEGEN_LIJST = re.compile(
    r"tegen\s*:\s*([\w\s,/\-]+?)(?:\.|;|\n|voor|$)", re.IGNORECASE
)

# Portfolio parsing from notities: "Portefeuille: bouwen, wonen, energietransitie"
RE_PORTEFEUILLE = re.compile(
    r"[Pp]ortefeuille\s*:\s*(.+?)(?:\.|$)", re.DOTALL
)


# ── Entity helper ────────────────────────────────────────────────────

def get_or_create_entity(cur, entity_type, entity_name, metadata=None):
    """Get existing kg_entities id or create new entry."""
    cur.execute(
        "SELECT id FROM kg_entities WHERE type = %s AND name = %s",
        (entity_type, entity_name),
    )
    row = cur.fetchone()
    if row:
        return row[0] if isinstance(row, tuple) else row["id"]
    cur.execute(
        "INSERT INTO kg_entities (type, name, metadata) VALUES (%s, %s, %s) RETURNING id",
        (entity_type, entity_name, json.dumps(metadata or {})),
    )
    result = cur.fetchone()
    return result[0] if isinstance(result, tuple) else result["id"]


# ── Dedup helper ─────────────────────────────────────────────────────

def relationship_exists(cur, source_id, target_id, relation_type, document_id=None):
    """Check if a relationship already exists to avoid duplicates."""
    if document_id:
        cur.execute(
            """SELECT 1 FROM kg_relationships
               WHERE source_entity_id = %s AND target_entity_id = %s
                 AND relation_type = %s AND document_id = %s
               LIMIT 1""",
            (source_id, target_id, relation_type, document_id),
        )
    else:
        cur.execute(
            """SELECT 1 FROM kg_relationships
               WHERE source_entity_id = %s AND target_entity_id = %s
                 AND relation_type = %s AND document_id IS NULL
               LIMIT 1""",
            (source_id, target_id, relation_type),
        )
    return cur.fetchone() is not None


# ── Batch insert helper ──────────────────────────────────────────────

def flush_batch(cur, batch, dry_run=False):
    """Insert a batch of relationship rows. Returns count inserted."""
    if not batch or dry_run:
        return 0
    args_str = ",".join(
        cur.mogrify(
            "(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                r["source_entity_id"], r["target_entity_id"], r["relation_type"],
                r.get("document_id"), r.get("chunk_id"), r.get("confidence", 1.0),
                r.get("quote"), Json(r.get("metadata", {})),
            ),
        ).decode()
        for r in batch
    )
    cur.execute(
        f"""INSERT INTO kg_relationships
            (source_entity_id, target_entity_id, relation_type,
             document_id, chunk_id, confidence, quote, metadata)
            VALUES {args_str}"""
    )
    return len(batch)


# ── 1. LID_VAN ──────────────────────────────────────────────────────

def populate_lid_van(cur, dry_run=False):
    """Person -> Party (LID_VAN) from politician_registry."""
    cur.execute("""
        SELECT canonical_name, partij, rol, periode_van, periode_tot
        FROM politician_registry
        WHERE partij IS NOT NULL
    """)
    rows = cur.fetchall()
    batch = []
    skipped = 0

    for canonical_name, partij, rol, periode_van, periode_tot in rows:
        person_id = get_or_create_entity(cur, "Person", canonical_name)
        party_id = get_or_create_entity(cur, "Party", partij)

        if relationship_exists(cur, person_id, party_id, "LID_VAN"):
            skipped += 1
            continue

        meta = {}
        if rol:
            meta["rol"] = rol
        if periode_van:
            meta["periode_van"] = str(periode_van)
        if periode_tot:
            meta["periode_tot"] = str(periode_tot)

        batch.append({
            "source_entity_id": person_id,
            "target_entity_id": party_id,
            "relation_type": "LID_VAN",
            "confidence": 1.0,
            "metadata": meta,
        })

        if len(batch) >= BATCH_SIZE:
            flush_batch(cur, batch, dry_run)
            batch = []

    flush_batch(cur, batch, dry_run)
    total = len(rows)
    created = total - skipped
    print(f"  LID_VAN: {total} registry rows -> {created} new edges ({skipped} already existed)")
    return created


# ── 2. IS_WETHOUDER_VAN ─────────────────────────────────────────────

def populate_wethouder_van(cur, dry_run=False):
    """Wethouder -> Beleidsgebied from raadslid_rollen notities."""
    cur.execute("""
        SELECT volledige_naam, naam, notities, partij, periode_van, periode_tot
        FROM raadslid_rollen
        WHERE LOWER(rol) = 'wethouder' AND notities IS NOT NULL
    """)
    rows = cur.fetchall()
    created = 0
    batch = []

    for volledige_naam, naam, notities, partij, periode_van, periode_tot in rows:
        canonical = volledige_naam or naam
        m = RE_PORTEFEUILLE.search(notities)
        if not m:
            continue

        # Parse portfolio items: "bouwen, wonen, energietransitie"
        raw_portfolio = m.group(1).strip().rstrip(".")
        areas = [a.strip() for a in re.split(r",\s*", raw_portfolio) if a.strip()]

        person_id = get_or_create_entity(cur, "Person", canonical)

        for area in areas:
            if len(area) < 2:
                continue
            area_id = get_or_create_entity(cur, "Beleidsgebied", area)

            if relationship_exists(cur, person_id, area_id, "IS_WETHOUDER_VAN"):
                continue

            meta = {}
            if partij:
                meta["partij"] = partij
            if periode_van:
                meta["periode_van"] = str(periode_van)
            if periode_tot:
                meta["periode_tot"] = str(periode_tot)

            batch.append({
                "source_entity_id": person_id,
                "target_entity_id": area_id,
                "relation_type": "IS_WETHOUDER_VAN",
                "confidence": 0.9,
                "metadata": meta,
            })
            created += 1

            if len(batch) >= BATCH_SIZE:
                flush_batch(cur, batch, dry_run)
                batch = []

    flush_batch(cur, batch, dry_run)
    print(f"  IS_WETHOUDER_VAN: {len(rows)} wethouder rows -> {created} new edges")
    return created


# ── Vote-party extraction helpers ────────────────────────────────────

def _parse_party_list(raw_text):
    """Extract canonical party names from a comma-separated raw string."""
    parties = []
    # Split on comma and "en"
    tokens = re.split(r",\s*|\s+en\s+", raw_text)
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        canonical = normalize_party(token)
        if canonical and canonical not in parties:
            parties.append(canonical)
    return parties


def extract_vote_parties(content):
    """
    Extract parties that voted voor/tegen from chunk content.
    Returns (voor_parties, tegen_parties) lists.
    """
    voor = []
    tegen = []

    # Pattern 1: "fracties van X, Y stemmen voor/tegen"
    for m in RE_FRACTIES_VOOR.finditer(content):
        voor.extend(_parse_party_list(m.group(1)))
    for m in RE_FRACTIES_TEGEN.finditer(content):
        tegen.extend(_parse_party_list(m.group(1)))

    # Pattern 2: "voor: X, Y" / "tegen: X, Y"
    if not voor:
        for m in RE_VOOR_LIJST.finditer(content):
            voor.extend(_parse_party_list(m.group(1)))
    if not tegen:
        for m in RE_TEGEN_LIJST.finditer(content):
            tegen.extend(_parse_party_list(m.group(1)))

    # Fallback: use speaker-party extraction from party_utils
    # (picks up "De heer X (VVD)" patterns near vote language)
    if not voor and not tegen:
        parties = extract_parties_from_text(content)
        # Without explicit voor/tegen context, we cannot assign direction
        # so we skip this fallback for vote edges
        pass

    # Deduplicate
    voor = list(dict.fromkeys(voor))
    tegen = list(dict.fromkeys(tegen))
    return voor, tegen


# ── 3. STEMT_VOOR / STEMT_TEGEN ─────────────────────────────────────

def populate_vote_edges(cur, dry_run=False, limit=None):
    """Party -> Motie (STEMT_VOOR / STEMT_TEGEN) from vote chunks."""
    query = """
        SELECT dc.id AS chunk_id, dc.document_id, dc.content, dc.vote_outcome,
               dc.motion_number, d.name AS doc_name, d.meeting_id
        FROM document_chunks dc
        JOIN documents d ON dc.document_id = d.id
        WHERE dc.vote_outcome IS NOT NULL
        ORDER BY dc.id
    """
    if limit:
        query += f" LIMIT {int(limit)}"

    cur.execute(query)
    rows = cur.fetchall()
    created = 0
    batch = []

    for chunk_id, document_id, content, vote_outcome, motion_number, doc_name, meeting_id in rows:
        if not content:
            continue

        # Determine motie entity name
        motie_label = motion_number or doc_name or f"motie-chunk-{chunk_id}"
        motie_id = get_or_create_entity(cur, "Motie", motie_label)

        voor_parties, tegen_parties = extract_vote_parties(content)

        for party_name in voor_parties:
            party_id = get_or_create_entity(cur, "Party", party_name)
            if relationship_exists(cur, party_id, motie_id, "STEMT_VOOR", document_id):
                continue
            batch.append({
                "source_entity_id": party_id,
                "target_entity_id": motie_id,
                "relation_type": "STEMT_VOOR",
                "document_id": document_id,
                "chunk_id": chunk_id,
                "confidence": 0.8,
                "quote": content[:300] if content else None,
                "metadata": {"vote_outcome": vote_outcome},
            })
            created += 1

        for party_name in tegen_parties:
            party_id = get_or_create_entity(cur, "Party", party_name)
            if relationship_exists(cur, party_id, motie_id, "STEMT_TEGEN", document_id):
                continue
            batch.append({
                "source_entity_id": party_id,
                "target_entity_id": motie_id,
                "relation_type": "STEMT_TEGEN",
                "document_id": document_id,
                "chunk_id": chunk_id,
                "confidence": 0.8,
                "quote": content[:300] if content else None,
                "metadata": {"vote_outcome": vote_outcome},
            })
            created += 1

        if len(batch) >= BATCH_SIZE:
            flush_batch(cur, batch, dry_run)
            batch = []

    flush_batch(cur, batch, dry_run)
    print(f"  STEMT_VOOR/TEGEN: {len(rows)} vote chunks -> {created} new edges")
    return created


# ── 4. AANGENOMEN / VERWORPEN ────────────────────────────────────────

def populate_outcome_edges(cur, dry_run=False, limit=None):
    """Motie -> Vergadering (AANGENOMEN/VERWORPEN) from vote chunks."""
    query = """
        SELECT dc.id AS chunk_id, dc.document_id, dc.vote_outcome,
               dc.motion_number, d.name AS doc_name, d.meeting_id,
               m.name AS meeting_name, m.start_date
        FROM document_chunks dc
        JOIN documents d ON dc.document_id = d.id
        LEFT JOIN meetings m ON d.meeting_id = m.id
        WHERE dc.vote_outcome IS NOT NULL
        ORDER BY dc.id
    """
    if limit:
        query += f" LIMIT {int(limit)}"

    cur.execute(query)
    rows = cur.fetchall()
    created = 0
    batch = []
    seen_motie_vergadering = set()

    for (chunk_id, document_id, vote_outcome, motion_number,
         doc_name, meeting_id, meeting_name, start_date) in rows:

        # Map vote_outcome to relation_type
        if vote_outcome in ("aangenomen",):
            rel_type = "AANGENOMEN"
        elif vote_outcome in ("verworpen",):
            rel_type = "VERWORPEN"
        else:
            # ingetrokken / aangehouden — skip for now
            continue

        motie_label = motion_number or doc_name or f"motie-chunk-{chunk_id}"
        motie_id = get_or_create_entity(cur, "Motie", motie_label)

        # Build vergadering entity from meeting
        if meeting_name and start_date:
            verg_label = f"{meeting_name} ({str(start_date)[:10]})"
        elif meeting_id:
            verg_label = f"vergadering-{meeting_id}"
        else:
            verg_label = f"vergadering-doc-{document_id}"

        # Deduplicate: one AANGENOMEN/VERWORPEN per motie-vergadering pair
        dedup_key = (motie_label, verg_label, rel_type)
        if dedup_key in seen_motie_vergadering:
            continue
        seen_motie_vergadering.add(dedup_key)

        verg_id = get_or_create_entity(cur, "Vergadering", verg_label, {
            "meeting_id": meeting_id,
            "start_date": str(start_date)[:10] if start_date else None,
        })

        if relationship_exists(cur, motie_id, verg_id, rel_type, document_id):
            continue

        meta = {"vote_outcome": vote_outcome}
        if start_date:
            meta["datum"] = str(start_date)[:10]

        batch.append({
            "source_entity_id": motie_id,
            "target_entity_id": verg_id,
            "relation_type": rel_type,
            "document_id": document_id,
            "chunk_id": chunk_id,
            "confidence": 0.95,
            "metadata": meta,
        })
        created += 1

        if len(batch) >= BATCH_SIZE:
            flush_batch(cur, batch, dry_run)
            batch = []

    flush_batch(cur, batch, dry_run)
    print(f"  AANGENOMEN/VERWORPEN: {len(rows)} vote chunks -> {created} new edges")
    return created


# ── 5. DIENT_IN ──────────────────────────────────────────────────────

def populate_dient_in(cur, dry_run=False, limit=None):
    """Person -> Motie (DIENT_IN) from indieners in document_chunks."""
    query = """
        SELECT dc.id AS chunk_id, dc.document_id, dc.indieners,
               dc.motion_number, d.name AS doc_name
        FROM document_chunks dc
        JOIN documents d ON dc.document_id = d.id
        WHERE dc.indieners IS NOT NULL AND array_length(dc.indieners, 1) > 0
        ORDER BY dc.id
    """
    if limit:
        query += f" LIMIT {int(limit)}"

    cur.execute(query)
    rows = cur.fetchall()
    created = 0
    batch = []
    seen = set()

    for chunk_id, document_id, indieners, motion_number, doc_name in rows:
        if not indieners:
            continue

        motie_label = motion_number or doc_name or f"motie-chunk-{chunk_id}"
        motie_id = get_or_create_entity(cur, "Motie", motie_label)

        for indiener_name in indieners:
            if not indiener_name or len(indiener_name) < 2:
                continue

            # Deduplicate within this run
            dedup_key = (indiener_name, motie_label)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            person_id = get_or_create_entity(cur, "Person", indiener_name)

            if relationship_exists(cur, person_id, motie_id, "DIENT_IN", document_id):
                continue

            batch.append({
                "source_entity_id": person_id,
                "target_entity_id": motie_id,
                "relation_type": "DIENT_IN",
                "document_id": document_id,
                "chunk_id": chunk_id,
                "confidence": 0.9,
                "metadata": {},
            })
            created += 1

            if len(batch) >= BATCH_SIZE:
                flush_batch(cur, batch, dry_run)
                batch = []

    flush_batch(cur, batch, dry_run)
    print(f"  DIENT_IN: {len(rows)} chunks with indieners -> {created} new edges")
    return created


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Populate kg_relationships with deterministic edges (no LLM)."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print counts without inserting",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Limit chunks processed (applies to chunk-based extractors)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  POPULATE KG RELATIONSHIPS (deterministic)")
    print(f"  dry_run={args.dry_run}  limit={args.limit or 'none'}")
    print("=" * 60)

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    totals = {}

    # 1. LID_VAN
    print("\n[1/5] LID_VAN (politician -> party)")
    totals["LID_VAN"] = populate_lid_van(cur, args.dry_run)
    if not args.dry_run:
        conn.commit()

    # 2. IS_WETHOUDER_VAN
    print("\n[2/5] IS_WETHOUDER_VAN (wethouder -> beleidsgebied)")
    totals["IS_WETHOUDER_VAN"] = populate_wethouder_van(cur, args.dry_run)
    if not args.dry_run:
        conn.commit()

    # 3. STEMT_VOOR / STEMT_TEGEN
    print("\n[3/5] STEMT_VOOR / STEMT_TEGEN (party -> motie)")
    totals["STEMT_VOOR/TEGEN"] = populate_vote_edges(cur, args.dry_run, args.limit)
    if not args.dry_run:
        conn.commit()

    # 4. AANGENOMEN / VERWORPEN
    print("\n[4/5] AANGENOMEN / VERWORPEN (motie -> vergadering)")
    totals["AANGENOMEN/VERWORPEN"] = populate_outcome_edges(cur, args.dry_run, args.limit)
    if not args.dry_run:
        conn.commit()

    # 5. DIENT_IN
    print("\n[5/5] DIENT_IN (person -> motie)")
    totals["DIENT_IN"] = populate_dient_in(cur, args.dry_run, args.limit)
    if not args.dry_run:
        conn.commit()

    # Summary
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    grand_total = 0
    for rel_type, count in totals.items():
        print(f"  {rel_type:<25} {count:>6} edges")
        grand_total += count
    print(f"  {'TOTAL':<25} {grand_total:>6} edges")
    if args.dry_run:
        print("  (dry-run mode: nothing was written)")
    print("=" * 60)

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
