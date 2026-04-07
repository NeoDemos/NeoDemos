#!/usr/bin/env python3
"""
Populate raadslid_rollen from notulen attendance lists and benoeming documents.

Extracts all council members from:
1. Installation notulen (beëdiging ceremony) — full attendance lists
2. Mededeling aanneming benoeming documents — mid-term replacements
3. First notulen of each council period — attendance lists

Covers all council periods from 2002 to 2026.

Usage:
    python scripts/populate_raadslid_rollen.py [--dry-run]
"""

import os
import re
import sys
import psycopg2
from collections import defaultdict

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/neodemos",
)

# Council periods: (election_date, end_date, first_notulen_window_start, first_notulen_window_end)
COUNCIL_PERIODS = [
    ("2002-03-06", "2006-03-07", "2002-03-20", "2002-05-01"),
    ("2006-03-07", "2010-03-03", "2006-03-10", "2006-05-01"),
    ("2010-03-03", "2014-03-19", "2010-03-10", "2010-05-01"),
    ("2014-03-19", "2018-03-21", "2014-03-20", "2014-05-01"),
    ("2018-03-21", "2022-03-16", "2018-03-25", "2018-05-01"),
    ("2022-03-16", "2026-03-18", "2022-03-25", "2022-05-01"),
]


def extract_names_from_attendance(content: str) -> list[tuple[str, str]]:
    """
    Extract (initials+lastname, lastname) from a notulen attendance section.
    Returns list of (full_notation, normalized_lastname).
    """
    lower = content.lower()

    # Find attendance section
    for keyword in ['tegenwoordig zijn de leden', 'tegenwoordig zijn', 'aanwezig zijn de leden', 'aanwezig:']:
        idx = lower.find(keyword)
        if idx >= 0:
            break
    else:
        return []

    # Get section (typically 1000-3000 chars)
    section = content[idx:idx+4000]

    # Find end of attendance list (usually "Griffier:" or "Afwezig" or agenda item "1.")
    for end_marker in ['griffier', 'afwezig', '\n1.', '\n2.', 'De VOORZITTER']:
        end_idx = section.lower().find(end_marker, 100)
        if end_idx > 0:
            section = section[:end_idx]
            break

    # Extract "de heer/mevrouw [TITLES] INITIALS. LASTNAME[-LASTNAME]"
    pattern = re.compile(
        r'(?:de\s+heer(?:en)?|mevrouw)\s+'
        r'(?:(?:drs|mr|ir|ing|dr|prof|RA)\.\s*)*'
        r'([A-Z](?:\.[A-Z])*\.?\s*'  # initials
        r'(?:van\s+(?:de\s+|den\s+|het\s+)?|de\s+|den\s+|ten\s+)?'  # tussenvoegsels
        r'[A-Z][a-zà-üA-Z]+(?:\s*[-–]\s*(?:van\s+)?[A-Za-zà-ü]+)*)',  # lastname
        re.MULTILINE
    )

    results = []
    seen = set()
    for m in pattern.finditer(section):
        full = m.group(1).strip()
        full = re.sub(r'\s+', ' ', full)  # normalize whitespace

        # Extract lastname (last capitalized word, possibly hyphenated)
        parts = full.split()
        # Find the main lastname (skip initials and tussenvoegsels)
        lastname_parts = []
        for p in parts:
            if p[0].isupper() and not re.match(r'^[A-Z]\.$', p) and p.lower() not in ('van', 'de', 'den', 'het', 'ten'):
                lastname_parts.append(p)
            elif p.lower() in ('van', 'de', 'den', 'het', 'ten') and lastname_parts:
                # tussenvoegsel is part of the name
                lastname_parts.append(p)

        lastname = ' '.join(lastname_parts) if lastname_parts else parts[-1]
        # Handle hyphenated
        lastname = re.sub(r'\s*[-–]\s*', '-', lastname)

        key = lastname.lower().split('-')[0]  # normalize on first part of hyphenated name
        if key not in seen and len(key) > 2:
            seen.add(key)
            results.append((full, lastname))

    return results


def extract_names_from_oath(content: str) -> list[tuple[str, str]]:
    """
    Extract names from the beëdiging/oath section specifically.
    These are the "De heer LASTNAME (party)." patterns.
    """
    lower = content.lower()

    # Find oath section
    for keyword in ['beëdiging van de leden', 'beëdiging van leden', 'installatierede', 'zweer dat ik']:
        idx = lower.find(keyword)
        if idx >= 0:
            break
    else:
        return []

    section = content[max(0, idx - 500):idx + 5000]

    # Extract "De heer/Mevrouw LASTNAME (PARTY)" from oath responses
    pattern = re.compile(
        r'(?:De heer|Mevrouw)\s+'
        r'([A-Z][A-Za-zà-ü\s\-]+?)\s*'
        r'\(([^)]+)\)',
        re.MULTILINE
    )

    results = []
    seen = set()
    for m in pattern.finditer(section):
        name = m.group(1).strip()
        party = m.group(2).strip()
        key = name.lower()
        if key not in seen and len(name) > 2 and key not in ('voorzitter',):
            seen.add(key)
            results.append((name, party))

    return results


def get_existing_records(cur) -> set[tuple[str, str, str]]:
    """Get (naam_lower, rol, periode_van) tuples for dedup."""
    cur.execute("SELECT LOWER(naam), rol, periode_van::text FROM raadslid_rollen")
    return {(r[0], r[1], r[2]) for r in cur.fetchall()}


def find_installation_notulen(cur, period_start: str, window_start: str, window_end: str) -> list[tuple[str, str]]:
    """Find notulen documents that contain installation ceremonies."""
    cur.execute("""
        SELECT d.id, d.name, m.start_date, d.content
        FROM documents d LEFT JOIN meetings m ON d.meeting_id = m.id
        WHERE LOWER(d.name) LIKE '%%notulen%%raadsvergadering%%'
          AND m.start_date >= %s AND m.start_date <= %s
          AND d.content IS NOT NULL
        ORDER BY m.start_date
    """, (window_start, window_end))
    return cur.fetchall()


def find_midterm_benoemingen(cur, period_start: str, period_end: str) -> list[dict]:
    """Find mid-term raadslid replacements from mededeling documents."""
    cur.execute("""
        SELECT d.name, m.start_date, LEFT(d.content, 800)
        FROM documents d LEFT JOIN meetings m ON d.meeting_id = m.id
        WHERE (LOWER(d.name) LIKE '%%mededeling aanneming benoeming%%lid%%raad%%'
           OR LOWER(d.name) LIKE '%%mededeling%%aanneming%%benoeming%%kieswet%%')
          AND m.start_date >= %s AND m.start_date <= %s
        ORDER BY m.start_date
    """, (period_start, period_end))

    results = []
    for name, dt, content in cur.fetchall():
        date_str = str(dt)[:10] if dt else None
        # Extract person name from title
        m = re.search(r'(?:V\s*2|V2)\s*[-–—]?\s*(?:dhr\.\s*|mw\.\s*|mevrouw\s*)?(.+?)(?:\s*$|\s*per\s|\d{3,})', name, re.IGNORECASE)
        if not m:
            m = re.search(r'Raad.*?[-–—]\s*(?:dhr\.\s*|mw\.\s*)?(.+?)(?:\s*$)', name)
        if m:
            person = m.group(1).strip().rstrip('.')
            # Clean up
            person = re.sub(r'\s+', ' ', person)
            if len(person) > 3:
                results.append({"name": person, "date": date_str, "source": name[:80]})

    return results


def main():
    dry_run = '--dry-run' in sys.argv

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    existing = get_existing_records(cur)
    print(f"Existing records: {len(existing)}")

    inserts = []

    for period_start, period_end, win_start, win_end in COUNCIL_PERIODS:
        year = int(period_start[:4])
        print(f"\n{'='*60}")
        print(f"  COUNCIL PERIOD {year}-{int(period_end[:4])}")
        print(f"{'='*60}")

        # 1. Find installation notulen
        notulen = find_installation_notulen(cur, period_start, win_start, win_end)
        period_members = {}  # lastname -> {initials, party, source}

        for doc_id, doc_name, doc_date, content in notulen:
            if not content:
                continue

            # Try oath section first (most reliable — has party names)
            oath_names = extract_names_from_oath(content)
            if oath_names:
                print(f"\n  Oath names from {doc_name[:60]}:")
                for name, party in oath_names:
                    key = name.lower().split()[-1] if name.split() else name.lower()
                    key = key.split('-')[0]
                    if key not in period_members:
                        period_members[key] = {"name": name, "party": party, "source": "oath"}
                        print(f"    {name:30s} ({party})")

            # Also try attendance list
            attend_names = extract_names_from_attendance(content)
            if attend_names:
                new_count = 0
                for full, lastname in attend_names:
                    key = lastname.lower().split('-')[0].split()[-1]
                    if key not in period_members:
                        period_members[key] = {"name": full, "party": "?", "source": "attendance"}
                        new_count += 1
                if new_count:
                    print(f"\n  +{new_count} from attendance list in {doc_name[:60]}")
                    for full, lastname in attend_names:
                        key = lastname.lower().split('-')[0].split()[-1]
                        if period_members.get(key, {}).get("source") == "attendance":
                            print(f"    {full:30s} (party unknown)")

        print(f"\n  Total for {year}: {len(period_members)} members")

        # 2. Find mid-term replacements
        midterms = find_midterm_benoemingen(cur, period_start, period_end)
        if midterms:
            print(f"\n  Mid-term replacements:")
            for mt in midterms:
                print(f"    {mt['date'] or '?':12s} {mt['name']:30s}")

        # 3. Prepare inserts
        for key, info in period_members.items():
            # Extract just lastname for the naam field
            name_parts = info["name"].split()
            # Find the main lastname
            lastname = name_parts[-1] if name_parts else info["name"]
            # Handle initials
            initials = ' '.join(p for p in name_parts if re.match(r'^[A-Z]\.', p))

            check_key = (key, "raadslid", period_start)
            if check_key not in existing:
                inserts.append({
                    "naam": lastname,
                    "volledige_naam": info["name"],
                    "rol": "raadslid",
                    "partij": info["party"],
                    "periode_van": period_start,
                    "periode_tot": period_end,
                    "notities": f"Bron: {info['source']}. Periode {year}-{int(period_end[:4])}.",
                })

        for mt in midterms:
            name_parts = mt["name"].split()
            lastname = name_parts[-1] if name_parts else mt["name"]
            check_key = (lastname.lower(), "raadslid", mt["date"] or period_start)
            if check_key not in existing and mt["date"]:
                inserts.append({
                    "naam": lastname,
                    "volledige_naam": mt["name"],
                    "rol": "raadslid",
                    "partij": "?",
                    "periode_van": mt["date"],
                    "periode_tot": period_end,
                    "notities": f"Tussentijdse benoeming. Bron: {mt['source'][:60]}.",
                })

    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"  New records to insert: {len(inserts)}")

    if dry_run:
        print("\n  [DRY RUN — no changes made]")
        for rec in inserts[:20]:
            print(f"    {rec['naam']:20s} {rec['rol']:12s} {rec['partij']:25s} {rec['periode_van']}")
        if len(inserts) > 20:
            print(f"    ... and {len(inserts) - 20} more")
    else:
        for rec in inserts:
            cur.execute("""
                INSERT INTO raadslid_rollen (naam, volledige_naam, rol, partij, periode_van, periode_tot, notities)
                VALUES (%(naam)s, %(volledige_naam)s, %(rol)s, %(partij)s, %(periode_van)s, %(periode_tot)s, %(notities)s)
            """, rec)
        conn.commit()
        print(f"\n  Inserted {len(inserts)} records.")

    # Final count
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT naam) FROM raadslid_rollen")
    total, unique = cur.fetchone()
    print(f"  Total records: {total}, unique persons: {unique}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
