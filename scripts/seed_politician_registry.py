#!/usr/bin/env python3
"""
Seed the politician_registry table from three sources:

  1. raadslid_rollen      — primary (~50 records, role metadata)
  2. political dictionary  — surname list from Whisper lexicon
  3. kg_entities           — Person entities matched by surname

Idempotent: re-running merges aliases via ON CONFLICT ... DO UPDATE.

Usage:
    python scripts/seed_politician_registry.py
"""

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import psycopg2

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/neodemos")

DICTIONARY_PATH = PROJECT_ROOT / "data" / "lexicons" / "rotterdam_political_dictionary.json"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

CREATE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS politician_registry (
    id SERIAL PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    surname TEXT NOT NULL,
    partij TEXT,
    rol TEXT,
    organisatie TEXT DEFAULT 'rotterdam',
    periode_van DATE,
    periode_tot DATE,
    source TEXT,
    aliases TEXT[] DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(canonical_name, rol, periode_van)
);

CREATE INDEX IF NOT EXISTS idx_polreg_surname ON politician_registry (LOWER(surname));
CREATE INDEX IF NOT EXISTS idx_polreg_partij  ON politician_registry (LOWER(partij));
CREATE INDEX IF NOT EXISTS idx_polreg_periode ON politician_registry (periode_van, periode_tot);
"""

# ---------------------------------------------------------------------------
# Alias generation
# ---------------------------------------------------------------------------

def generate_aliases(surname: str, volledige_naam: str | None, rol: str | None) -> list[str]:
    """Generate common name variations used in notulen and transcripts."""
    aliases: list[str] = []

    # Formal address variants
    aliases.append(f"De heer {surname}")
    aliases.append(f"de heer {surname}")
    aliases.append(f"Mevrouw {surname}")
    aliases.append(f"mevrouw {surname}")

    # Role-based address
    if rol and rol.lower() == "wethouder":
        aliases.append(f"Wethouder {surname}")
        aliases.append(f"wethouder {surname}")
    if rol and rol.lower() == "burgemeester":
        aliases.append(f"Burgemeester {surname}")
        aliases.append(f"burgemeester {surname}")

    # UPPERCASE variant (common in speaker attribution headers)
    aliases.append(surname.upper())

    # Extract bare surname from volledige_naam if it contains initials
    # e.g. "L.K. Geluk" -> also add "Geluk" (which may differ from naam)
    if volledige_naam:
        # Strip initials like "L.K. " or "M.G.T. " from the front
        bare = re.sub(r'^([A-Z]\.)+\s*', '', volledige_naam).strip()
        if bare and bare != surname and bare != volledige_naam:
            aliases.append(bare)

    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for a in aliases:
        if a not in seen:
            seen.add(a)
            deduped.append(a)
    return deduped


# ---------------------------------------------------------------------------
# Upsert helper
# ---------------------------------------------------------------------------

UPSERT_SQL = """
INSERT INTO politician_registry
    (canonical_name, surname, partij, rol, organisatie, periode_van, periode_tot, source, aliases)
VALUES
    (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (canonical_name, rol, periode_van)
DO UPDATE SET
    aliases = (
        SELECT ARRAY(
            SELECT DISTINCT unnest(
                politician_registry.aliases || EXCLUDED.aliases
            )
        )
    ),
    partij      = COALESCE(EXCLUDED.partij, politician_registry.partij),
    periode_tot = COALESCE(EXCLUDED.periode_tot, politician_registry.periode_tot),
    source      = COALESCE(EXCLUDED.source, politician_registry.source)
RETURNING (xmax = 0) AS inserted
"""


def upsert_record(cur, canonical_name, surname, partij, rol, periode_van,
                   periode_tot, source, aliases):
    """Insert or merge a single registry record. Returns ('inserted' | 'updated')."""
    cur.execute(UPSERT_SQL, (
        canonical_name, surname, partij, rol, 'rotterdam',
        periode_van, periode_tot, source, aliases,
    ))
    row = cur.fetchone()
    return "inserted" if row[0] else "updated"


# ---------------------------------------------------------------------------
# Source 1: raadslid_rollen
# ---------------------------------------------------------------------------

def seed_from_raadslid_rollen(cur):
    """Seed from the raadslid_rollen table (primary source)."""
    cur.execute("""
        SELECT naam, volledige_naam, rol, partij, periode_van, periode_tot, notities
        FROM raadslid_rollen
    """)
    rows = cur.fetchall()
    stats = {"inserted": 0, "updated": 0}

    for naam, volledige_naam, rol, partij, periode_van, periode_tot, _notities in rows:
        canonical = volledige_naam if volledige_naam else naam
        aliases = generate_aliases(naam, volledige_naam, rol)

        result = upsert_record(
            cur,
            canonical_name=canonical,
            surname=naam,
            partij=partij,
            rol=rol,
            periode_van=periode_van,
            periode_tot=periode_tot,
            source="raadslid_rollen",
            aliases=aliases,
        )
        stats[result] += 1

    return rows, stats


# ---------------------------------------------------------------------------
# Source 2: rotterdam_political_dictionary.json
# ---------------------------------------------------------------------------

def seed_from_dictionary(cur, existing_surnames: set[str]):
    """Add dictionary surnames not already present from raadslid_rollen."""
    if not DICTIONARY_PATH.exists():
        print(f"  WARNING: dictionary not found at {DICTIONARY_PATH}, skipping source 2.")
        return {"inserted": 0, "updated": 0}

    with open(DICTIONARY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    surnames = data.get("council_members", {}).get("surnames", [])
    stats = {"inserted": 0, "updated": 0}

    # Normalize existing surnames for comparison (case-insensitive)
    existing_lower = {s.lower() for s in existing_surnames}

    for surname in surnames:
        if surname.lower() in existing_lower:
            continue

        aliases = generate_aliases(surname, None, None)

        result = upsert_record(
            cur,
            canonical_name=surname,
            surname=surname,
            partij=None,
            rol=None,
            periode_van=None,
            periode_tot=None,
            source="dictionary",
            aliases=aliases,
        )
        stats[result] += 1

    return stats


# ---------------------------------------------------------------------------
# Source 3: kg_entities Person matches
# ---------------------------------------------------------------------------

def extract_surname_from_entity(name: str) -> str | None:
    """
    Extract a plausible surname from a kg_entities Person name.

    Handles patterns like:
      - "De heer Buijt"  -> "Buijt"
      - "Mevrouw Zeegers" -> "Zeegers"
      - "Wethouder Kasmi" -> "Kasmi"
      - "Ronald Buijt"   -> "Buijt"
      - "Buijt"          -> "Buijt"
    """
    prefixes = [
        r"(?:De |de )?heer\s+",
        r"(?:M|m)evrouw\s+",
        r"(?:W|w)ethouder\s+",
        r"(?:B|b)urgemeester\s+",
    ]
    for pat in prefixes:
        m = re.match(pat + r"(.+)$", name)
        if m:
            return m.group(1).strip().split()[-1]

    # Fall back to last word
    parts = name.strip().split()
    if parts:
        return parts[-1]
    return None


def seed_from_kg_entities(cur, surname_to_ids: dict[str, list[int]]):
    """
    Match high-frequency Person entities from kg_entities against existing
    registry entries by surname. Adds matched names as aliases.
    """
    # Check if kg_entities and kg_mentions tables exist
    cur.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'kg_entities'
        ) AND EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'kg_mentions'
        )
    """)
    if not cur.fetchone()[0]:
        print("  INFO: kg_entities/kg_mentions tables not found, skipping source 3.")
        return {"matched": 0, "skipped": 0}

    cur.execute("""
        SELECT DISTINCT e.name, COUNT(m.id) AS mentions
        FROM kg_entities e
        JOIN kg_mentions m ON e.id = m.entity_id
        WHERE e.type = 'Person'
          AND LENGTH(e.name) BETWEEN 3 AND 50
          AND e.name NOT LIKE '{%%}'
        GROUP BY e.name
        HAVING COUNT(m.id) >= 10
        ORDER BY mentions DESC
    """)
    entities = cur.fetchall()
    stats = {"matched": 0, "skipped": 0}

    # Build lowercase lookup: surname_lower -> list of registry row ids
    surname_lower_map: dict[str, list[int]] = {}
    for surname, ids in surname_to_ids.items():
        surname_lower_map[surname.lower()] = ids

    for entity_name, _mentions in entities:
        extracted = extract_surname_from_entity(entity_name)
        if not extracted:
            stats["skipped"] += 1
            continue

        matching_ids = surname_lower_map.get(extracted.lower())
        if not matching_ids:
            stats["skipped"] += 1
            continue

        # Add this entity name as an alias to all matching registry entries
        for reg_id in matching_ids:
            cur.execute("""
                UPDATE politician_registry
                SET aliases = (
                    SELECT ARRAY(
                        SELECT DISTINCT unnest(aliases || ARRAY[%s])
                    )
                )
                WHERE id = %s
                  AND NOT (%s = ANY(aliases))
            """, (entity_name, reg_id, entity_name))

        stats["matched"] += 1

    return stats


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(cur):
    """Print a human-readable summary of the registry contents."""
    cur.execute("SELECT COUNT(*) FROM politician_registry")
    total = cur.fetchone()[0]

    cur.execute("""
        SELECT source, COUNT(*) FROM politician_registry
        GROUP BY source ORDER BY COUNT(*) DESC
    """)
    by_source = cur.fetchall()

    cur.execute("""
        SELECT COALESCE(rol, '(none)'), COUNT(*) FROM politician_registry
        GROUP BY rol ORDER BY COUNT(*) DESC
    """)
    by_role = cur.fetchall()

    print(f"\n{'='*50}")
    print(f"  politician_registry: {total} total records")
    print(f"{'='*50}")
    print("  By source:")
    for source, count in by_source:
        print(f"    {source or '(unknown)':<25} {count:>4}")
    print("  By role:")
    for role, count in by_role:
        print(f"    {role:<25} {count:>4}")
    print(f"{'='*50}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    # 1. Create schema
    print("Creating politician_registry schema...")
    cur.execute(CREATE_SCHEMA_SQL)
    conn.commit()
    print("  Schema ready.")

    # 2. Source 1: raadslid_rollen
    print("\nSource 1: raadslid_rollen")
    try:
        rollen_rows, s1_stats = seed_from_raadslid_rollen(cur)
        conn.commit()
        print(f"  Processed {len(rollen_rows)} rows -> "
              f"{s1_stats['inserted']} inserted, {s1_stats['updated']} updated")

        # Collect unique surnames for source 2 filtering
        existing_surnames = {row[0] for row in rollen_rows}  # naam column
    except Exception as e:
        conn.rollback()
        print(f"  ERROR: {e}")
        existing_surnames = set()
        rollen_rows = []

    # 3. Source 2: dictionary surnames
    print("\nSource 2: rotterdam_political_dictionary.json")
    try:
        s2_stats = seed_from_dictionary(cur, existing_surnames)
        conn.commit()
        print(f"  {s2_stats['inserted']} inserted, {s2_stats['updated']} updated")
    except Exception as e:
        conn.rollback()
        print(f"  ERROR: {e}")

    # 4. Build surname -> id map for source 3
    cur.execute("SELECT id, surname FROM politician_registry")
    surname_to_ids: dict[str, list[int]] = defaultdict(list)
    for reg_id, surname in cur.fetchall():
        surname_to_ids[surname].append(reg_id)

    # 5. Source 3: kg_entities
    print("\nSource 3: kg_entities Person matches")
    try:
        s3_stats = seed_from_kg_entities(cur, surname_to_ids)
        conn.commit()
        print(f"  {s3_stats['matched']} entity names matched as aliases, "
              f"{s3_stats['skipped']} skipped (no surname match)")
    except Exception as e:
        conn.rollback()
        print(f"  ERROR: {e}")

    # 6. Summary
    print_summary(cur)

    cur.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
