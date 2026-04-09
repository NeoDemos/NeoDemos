#!/usr/bin/env python3
"""
Clean Knowledge Graph Entities + Extend document_chunks Schema
==============================================================

Two-part maintenance script:

1. Adds 7 new columns to document_chunks (section_topic, key_entities,
   answerable_questions, indieners, vote_outcome, vote_counts, motion_number).

2. Cleans kg_entities / kg_mentions:
   - Removes noise entities (long names, JSON fragments, generic words)
   - Normalises Person entity name prefixes
   - Reclassifies party-type organisations to type='Party'
   - Merges case-variant party names

Usage:
    python scripts/clean_kg_entities.py            # Execute changes
    python scripts/clean_kg_entities.py --dry-run   # Preview only
"""

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import psycopg2

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/neodemos")

# ── Noise words: generic pronouns / roles / concepts that aren't real entities
NOISE_WORDS = {
    # Generic pronouns/roles
    "hij", "zij", "hen", "haar", "hem", "we", "wij", "u",
    "De wethouder", "de wethouder", "De voorzitter", "de voorzitter",
    "Voorzitter", "De Burgemeester", "de burgemeester", "De Secretaris",
    "burger", "burgers", "cliënt", "cliënten", "klant", "klanten",
    "kind", "kinderen", "jongere", "jongeren", "bewoner", "bewoners",
    "De commissie", "De raad", "Uw raad", "uw Raad", "de heer", "mevrouw",
    # Generic concepts
    "geld", "Geld", "kosten", "Baten", "Lasten",
    "organisaties", "organisatie", "bedrijven", "bedrijf",
    "Project", "bouwproject", "brug", "bouw", "Nieuwbouw",
    "Het programma", "programma's", "aanpak",
    "raden", "gemeenteraden", "Raadsvoorstellen",
    "primaire overheidstaak", "individuele makers",
    "de groep", "starters",
}

# ── Canonical party names
PARTIES = [
    "VVD", "PvdA", "D66", "CDA", "SP", "DENK", "PVV",
    "Leefbaar Rotterdam", "GroenLinks", "GroenLinks-PvdA",
    "Volt", "Bij1", "BIJ1", "NIDA", "Partij voor de Dieren",
    "50PLUS", "ChristenUnie", "ChristenUnie-SGP", "SGP",
]

# Map lowercase → canonical capitalisation
PARTY_CANONICAL = {p.lower(): p for p in PARTIES}


# ── Part 1 ──────────────────────────────────────────────────────────────────

def add_document_chunk_columns(cur, dry_run: bool):
    """Add 7 new columns to document_chunks (idempotent)."""
    columns = [
        ("section_topic",        "TEXT"),
        ("key_entities",         "TEXT[]"),
        ("answerable_questions", "TEXT[]"),
        ("indieners",            "TEXT[]"),
        ("vote_outcome",         "TEXT"),
        ("vote_counts",          "JSONB"),
        ("motion_number",        "TEXT"),
    ]
    print("\n=== Part 1: Add columns to document_chunks ===\n")
    for col_name, col_type in columns:
        stmt = f"ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS {col_name} {col_type};"
        if dry_run:
            print(f"  [dry-run] {stmt}")
        else:
            cur.execute(stmt)
            print(f"  + {col_name} ({col_type})")


# ── Part 2 ──────────────────────────────────────────────────────────────────

def clean_kg_entities(cur, dry_run: bool):
    """Remove noise entities, normalise names, reclassify parties."""

    print("\n=== Part 2: Clean kg_entities ===\n")

    # ── Step 1: Counts before ───────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM kg_entities;")
    entities_before = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM kg_mentions;")
    mentions_before = cur.fetchone()[0]
    print(f"  Before: {entities_before:,} entities, {mentions_before:,} mentions")

    # ── Step 2: Delete noisy entities (cascade to kg_mentions) ──────────
    noise_list = list(NOISE_WORDS)
    noise_placeholders = ", ".join(["%s"] * len(noise_list))

    # Build noise condition as a plain string (no f-string to avoid brace issues)
    noise_condition = (
        "LENGTH(name) > 100"
        " OR name LIKE '%%' || chr(10) || '%%'"
        " OR name LIKE '{%%'"
        " OR name IN (" + noise_placeholders + ")"
    )

    if dry_run:
        cur.execute(
            "SELECT COUNT(*) FROM kg_entities WHERE " + noise_condition,
            noise_list,
        )
        noise_count = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM kg_mentions WHERE entity_id IN ("
            "  SELECT id FROM kg_entities WHERE " + noise_condition + ")",
            noise_list,
        )
        noise_mentions = cur.fetchone()[0]
        print(f"  [dry-run] Would delete {noise_count:,} noisy entities and {noise_mentions:,} linked mentions")
    else:
        # Delete mentions first (FK dependency)
        cur.execute(
            "DELETE FROM kg_mentions WHERE entity_id IN ("
            "  SELECT id FROM kg_entities WHERE " + noise_condition + ")",
            noise_list,
        )
        mentions_deleted = cur.rowcount
        print(f"  Deleted {mentions_deleted:,} noisy mentions")

        cur.execute(
            "DELETE FROM kg_entities WHERE " + noise_condition,
            noise_list,
        )
        entities_deleted = cur.rowcount
        print(f"  Deleted {entities_deleted:,} noisy entities")

    # ── Step 3: Normalise Person entity name prefixes ───────────────────
    #
    # When stripping "De heer Buijt" → "Buijt", we may collide with an
    # existing "Buijt" entity. In that case: repoint mentions to the
    # existing entity and delete the prefixed duplicate.
    print()
    prefixes = [
        ("De heer ", 10), ("de heer ", 10),
        ("Mevrouw ", 9), ("mevrouw ", 9),
        ("Wethouder ", 12), ("wethouder ", 12),
        ("Burgemeester ", 15),
    ]

    normalized = 0
    merged_persons = 0
    for prefix, offset in prefixes:
        # Find all Person entities with this prefix
        cur.execute(
            "SELECT id, name FROM kg_entities WHERE type = 'Person' AND name LIKE %s",
            (prefix + "%",),
        )
        rows = cur.fetchall()
        if not rows:
            continue

        for eid, ename in rows:
            stripped = ename[offset - 1:].strip()  # offset is 1-based SQL, 0-based Python
            if not stripped:
                continue

            # Check if the stripped name already exists as a Person entity
            cur.execute(
                "SELECT id FROM kg_entities WHERE type = 'Person' AND name = %s",
                (stripped,),
            )
            existing = cur.fetchone()

            if existing and existing[0] != eid:
                if not dry_run:
                    # Merge: repoint mentions to existing, delete duplicate
                    cur.execute(
                        "UPDATE kg_mentions SET entity_id = %s WHERE entity_id = %s",
                        (existing[0], eid),
                    )
                    cur.execute("DELETE FROM kg_entities WHERE id = %s", (eid,))
                merged_persons += 1
            else:
                if not dry_run:
                    cur.execute(
                        "UPDATE kg_entities SET name = %s WHERE id = %s",
                        (stripped, eid),
                    )
                normalized += 1

    if dry_run:
        print(f"  [dry-run] Would normalise ~{normalized + merged_persons:,} Person name prefixes")
    else:
        print(f"  Normalised {normalized:,} Person names, merged {merged_persons:,} duplicates")

    # ── Step 4: Merge all party name variants across ALL types ──────────
    #
    # Party names exist under both Organization and Party types, and in
    # various casings (VVD, vvd, Vvd). For each canonical party: find ALL
    # entities matching by LOWER(name) regardless of type, keep one
    # survivor with canonical name + type='Party', repoint mentions,
    # delete duplicates.
    print()
    merged = 0
    for canonical in PARTIES:
        cur.execute("""
            SELECT id, name, type FROM kg_entities
            WHERE LOWER(name) = LOWER(%s)
            ORDER BY
                CASE WHEN name = %s AND type = 'Party' THEN 0
                     WHEN name = %s THEN 1
                     WHEN type = 'Party' THEN 2
                     ELSE 3 END,
                id
        """, (canonical, canonical, canonical))
        rows = cur.fetchall()
        if not rows:
            continue

        survivor_id = rows[0][0]
        duplicate_ids = [r[0] for r in rows[1:]]

        if not duplicate_ids:
            # Just ensure correct name + type on the single entity
            if not dry_run:
                cur.execute(
                    "UPDATE kg_entities SET name = %s, type = 'Party' WHERE id = %s",
                    (canonical, survivor_id),
                )
            continue

        if dry_run:
            print(f"  [dry-run] Would merge {len(duplicate_ids)} variant(s) of '{canonical}'")
            merged += len(duplicate_ids)
        else:
            dup_ph = ", ".join(["%s"] * len(duplicate_ids))
            cur.execute(
                "UPDATE kg_mentions SET entity_id = %s WHERE entity_id IN (" + dup_ph + ")",
                [survivor_id] + duplicate_ids,
            )
            repointed = cur.rowcount
            cur.execute(
                "UPDATE kg_entities SET name = %s, type = 'Party' WHERE id = %s",
                (canonical, survivor_id),
            )
            cur.execute(
                "DELETE FROM kg_entities WHERE id IN (" + dup_ph + ")",
                duplicate_ids,
            )
            merged += len(duplicate_ids)
            print(f"  Merged {len(duplicate_ids)} variant(s) of '{canonical}' (repointed {repointed:,} mentions)")

    if merged:
        print(f"  Total party variants merged: {merged}")
    else:
        print(f"  No party variants to merge")

    # ── Step 5: Counts after ────────────────────────────────────────────
    print()
    cur.execute("SELECT COUNT(*) FROM kg_entities;")
    entities_after = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM kg_mentions;")
    mentions_after = cur.fetchone()[0]

    cur.execute("SELECT type, COUNT(*) FROM kg_entities GROUP BY type ORDER BY COUNT(*) DESC;")
    type_dist = cur.fetchall()

    print(f"  After:  {entities_after:,} entities, {mentions_after:,} mentions")
    if not dry_run:
        print(f"  Deleted: {entities_before - entities_after:,} entities, {mentions_before - mentions_after:,} mentions")
    print()
    print("  Entity type distribution:")
    for t, c in type_dist:
        print(f"    {t:<20s} {c:>10,}")


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Add document_chunks columns and clean kg_entities noise"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be changed without executing modifications"
    )
    args = parser.parse_args()

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        add_document_chunk_columns(cur, dry_run=args.dry_run)
        clean_kg_entities(cur, dry_run=args.dry_run)

        if args.dry_run:
            print("\n[dry-run] Rolling back — no changes applied.")
            conn.rollback()
        else:
            conn.commit()
            print("\nAll changes committed.")

    except Exception as e:
        conn.rollback()
        print(f"\nError: {e}")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
