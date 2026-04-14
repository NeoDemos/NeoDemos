"""
Map document relationships:
  1. motie ↔ afdoeningsvoorstel  — via bb-number regex in afdoeningsvoorstel names
  2. motie ↔ raadsvoorstel       — via shared agenda_item_id

Run after alembic migration 20260414_0011_document_relationships.py.

Usage:
    python scripts/map_document_relationships.py [--dry-run]
"""

import os
import re
import sys
import argparse
import psycopg2
import psycopg2.extras

sys.path.insert(0, os.getcwd())

# Matches bare or bracketed bb-numbers: 21bb9673, [21bb014255], 20bb2496
BB_PATTERN = re.compile(r'\b(\d{2}bb\d+)\b', re.IGNORECASE)


def get_conn():
    from dotenv import load_dotenv
    load_dotenv()
    return psycopg2.connect(os.environ['DATABASE_URL'])


def map_afdoening_motie(conn, dry_run: bool) -> int:
    """
    For each afdoeningsvoorstel that contains bb-numbers in its name,
    find matching moties by those bb-numbers and create
    (source=afdoeningsvoorstel, target=motie, relation='afdoening_van') edges.
    """
    cur = conn.cursor()

    # Build motie bb-number → doc id lookup
    print("Loading motie bb-numbers...")
    cur.execute("""
        SELECT id, name
        FROM documents
        WHERE doc_classification = 'motie'
          AND name ~* '\\d{2}bb\\d+'
    """)
    motie_rows = cur.fetchall()

    # A motie can have multiple bb-numbers in its name (rare), index all
    bb_to_motie: dict[str, str] = {}
    for doc_id, name in motie_rows:
        for m in BB_PATTERN.finditer(name):
            bb = m.group(1).lower()
            bb_to_motie[bb] = doc_id
    print(f"  {len(bb_to_motie)} bb-number → motie entries indexed")

    # Load all afdoeningsvooerstellen with bb-numbers
    print("Loading afdoeningsvoorstel documents...")
    cur.execute("""
        SELECT id, name
        FROM documents
        WHERE doc_classification = 'afdoeningsvoorstel'
          AND name ~* '\\d{2}bb\\d+'
    """)
    afd_rows = cur.fetchall()
    print(f"  {len(afd_rows)} afdoeningsvooerstellen to process")

    matched = 0
    unmatched = 0
    pairs: list[tuple] = []

    for afd_id, name in afd_rows:
        bb_numbers = [m.group(1).lower() for m in BB_PATTERN.finditer(name)]
        found_any = False
        for bb in bb_numbers:
            motie_id = bb_to_motie.get(bb)
            if motie_id:
                pairs.append((
                    afd_id,
                    motie_id,
                    'afdoening_van',
                    1.0,
                    'bb_number_match',
                    {'matched_bb': bb, 'afd_name': name[:200]}
                ))
                found_any = True
        if found_any:
            matched += 1
        else:
            unmatched += 1

    print(f"  Matched: {matched}  |  No motie found: {unmatched}")
    print(f"  Total pairs to insert: {len(pairs)}")

    if not dry_run and pairs:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO document_relationships
                (source_doc_id, target_doc_id, relation_type, confidence, method, metadata)
            VALUES %s
            ON CONFLICT (source_doc_id, target_doc_id, relation_type) DO NOTHING
            """,
            [(p[0], p[1], p[2], p[3], p[4], psycopg2.extras.Json(p[5])) for p in pairs],
            template="(%s, %s, %s, %s, %s, %s)"
        )
        conn.commit()
        print(f"  Inserted (or skipped duplicates).")

    return len(pairs)


def map_motie_raadsvoorstel(conn, dry_run: bool) -> int:
    """
    For moties and raadsvoorstellen sharing the same agenda_item_id,
    create (source=motie, target=raadsvoorstel, relation='related_raadsvoorstel') edges.
    """
    cur = conn.cursor()
    print("Mapping motie ↔ raadsvoorstel via agenda_item_id...")

    cur.execute("""
        SELECT DISTINCT m.id AS motie_id, r.id AS rv_id, m.agenda_item_id
        FROM documents m
        JOIN documents r ON m.agenda_item_id = r.agenda_item_id
        WHERE m.doc_classification = 'motie'
          AND r.doc_classification = 'raadsvoorstel'
          AND m.agenda_item_id IS NOT NULL
        ORDER BY m.id
    """)
    pairs = cur.fetchall()
    print(f"  {len(pairs)} motie↔raadsvoorstel pairs found")

    if not dry_run and pairs:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO document_relationships
                (source_doc_id, target_doc_id, relation_type, confidence, method, metadata)
            VALUES %s
            ON CONFLICT (source_doc_id, target_doc_id, relation_type) DO NOTHING
            """,
            [
                (motie_id, rv_id, 'related_raadsvoorstel', 1.0, 'agenda_item_id',
                 psycopg2.extras.Json({'agenda_item_id': agenda_item_id}))
                for motie_id, rv_id, agenda_item_id in pairs
            ],
            template="(%s, %s, %s, %s, %s, %s)"
        )
        conn.commit()
        print(f"  Inserted (or skipped duplicates).")

    return len(pairs)


def print_summary(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT relation_type, COUNT(*) FROM document_relationships
        GROUP BY relation_type ORDER BY COUNT(*) DESC
    """)
    rows = cur.fetchall()
    print("\n=== document_relationships summary ===")
    for rel_type, count in rows:
        print(f"  {rel_type:<30} {count:>6}")
    cur.execute("SELECT COUNT(*) FROM document_relationships")
    print(f"  {'TOTAL':<30} {cur.fetchone()[0]:>6}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='Show counts without writing')
    args = parser.parse_args()

    if args.dry_run:
        print("=== DRY RUN — no writes ===\n")

    conn = get_conn()

    n1 = map_afdoening_motie(conn, args.dry_run)
    print()
    n2 = map_motie_raadsvoorstel(conn, args.dry_run)
    print()

    if not args.dry_run:
        print_summary(conn)

    conn.close()
    print(f"\nDone. afdoening_van={n1}, related_raadsvoorstel={n2}")


if __name__ == '__main__':
    main()
