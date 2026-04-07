#!/usr/bin/env python3
"""
Structured audit of raadslid_rollen against notulen ground truth.

Layers:
  1. Burgemeesters — voorzitter line in notulen (100% reliable)
  2. Wethouders — benoeming docs + college attendance section
  3. Raadsleden — mededeling docs + oath sections + attendance sampling
  4. Consistency — pure SQL overlap/integrity checks

Produces a markdown report + JSON findings. No auto-corrections.

Usage:
    python scripts/audit_raadslid_rollen.py [--layer 1|2|3|4|all] [--verbose]
"""

import os
import re
import sys
import json
import argparse
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field, asdict

import psycopg2

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/neodemos",
)

COUNCIL_PERIODS = [
    ("2002-03-06", "2006-03-07"),
    ("2006-03-07", "2010-03-03"),
    ("2010-03-03", "2014-03-19"),
    ("2014-03-19", "2018-03-21"),
    ("2018-03-21", "2022-03-16"),
    ("2022-03-16", "2026-03-18"),
]


# ---------------------------------------------------------------------------
# Finding dataclass
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    id: str
    layer: str
    severity: str  # error, warning, info
    finding_type: str
    description: str
    source_truth: dict = field(default_factory=dict)
    current_record: dict = field(default_factory=dict)
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

_TITLES = re.compile(
    r'\b(?:de heer|mevrouw|drs|mr|ir|ing|dr|prof|RA)\b\.?\s*', re.IGNORECASE
)
_INITIALS = re.compile(r'\b[A-Z]\.\s*')


def normalize_surname(name: str) -> str:
    """Extract and normalize the surname from a full name notation."""
    n = _TITLES.sub('', name).strip()
    n = _INITIALS.sub('', n).strip()
    # What remains should be [tussenvoegsels] + surname
    n = re.sub(r'\s+', ' ', n).strip()
    return n.lower()


def surname_key(name: str) -> str:
    """Get the primary surname key for matching (last capitalized part, lowered)."""
    norm = normalize_surname(name)
    parts = norm.split()
    # Skip tussenvoegsels to get the main surname
    for p in reversed(parts):
        if p not in ('van', 'de', 'den', 'het', 'ten', 'der'):
            return p.split('-')[0]
    return parts[-1] if parts else norm


def _name_keys(name: str) -> list[str]:
    """
    Return all surname keys to try for matching.
    Handles "Wijbenga - van Nieuwenhuizen", "Mohamed- Hoesein", "Lansink- Bastemeijer".
    Returns keys for each part of a hyphenated compound surname.
    """
    # Normalize spaces around hyphens: "van Rij- de Groot" → "van Rij-de Groot"
    normalized = re.sub(r'\s*-\s*', '-', name)
    base_key = surname_key(normalized)
    keys = [base_key]

    # Also try each part of a hyphenated surname
    n = normalize_surname(normalized)
    for part in n.split('-'):
        part_key = part.strip().split()[-1] if part.strip().split() else part.strip()
        if part_key and part_key not in keys:
            keys.append(part_key)

    return keys


def match_in_rollen(name: str, rollen_records: list, role: str = None, date_str: str = None) -> dict | None:
    """
    Find a matching record in raadslid_rollen for a given name.
    When date_str is provided, prefer the record whose period best covers that date
    (closest start date), rather than always returning the first match.
    """
    keys = _name_keys(name)

    candidates = []
    for rec in rollen_records:
        rec_key = rec['naam'].lower().split('-')[0]
        rec_volledige = (rec.get('volledige_naam') or '').lower()
        match = any(
            k == rec_key or k in rec_key or rec_key in k or k in rec_volledige
            for k in keys
        )
        if not match:
            continue
        if role and rec['rol'] != role:
            continue
        van = str(rec['periode_van'])
        tot = str(rec['periode_tot']) if rec['periode_tot'] else '9999-12-31'
        if date_str:
            if van <= date_str <= tot:
                candidates.append((0, rec))  # exact cover — highest priority
            else:
                # Not covering; lower priority, keep for fallback
                diff = abs((date_str[:10] > van[:10]) - (date_str[:10] < van[:10]))
                candidates.append((1 + diff, rec))
        else:
            candidates.append((0, rec))

    if not candidates:
        return None

    # Return the best candidate (lowest priority number, then earliest period_van)
    candidates.sort(key=lambda x: (x[0], str(x[1]['periode_van'])))
    # Only return exact-cover matches when date_str is given
    if date_str and candidates[0][0] > 0:
        return None  # No record covers this date
    return candidates[0][1]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_connection():
    return psycopg2.connect(DB_URL)


def get_all_rollen(cur) -> list[dict]:
    cur.execute("""
        SELECT id, naam, volledige_naam, rol, partij,
               periode_van::text, periode_tot::text, notities
        FROM raadslid_rollen ORDER BY periode_van
    """)
    cols = ['id', 'naam', 'volledige_naam', 'rol', 'partij',
            'periode_van', 'periode_tot', 'notities']
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Layer 1: Burgemeesters
# ---------------------------------------------------------------------------

def audit_burgemeesters(cur, rollen: list, verbose: bool = False) -> list[Finding]:
    findings = []
    burgemeester_rollen = [r for r in rollen if r['rol'] == 'burgemeester']

    # Fetch ALL notulen (first 500 chars only) — extract every unique (year, surname) pair
    # This catches mid-year transitions like Aboutaleb → Schouten in October 2024
    cur.execute(r"""
        SELECT doc_id, doc_date, content
        FROM (
            SELECT
                d.id AS doc_id,
                COALESCE(
                    m.start_date,
                    TO_DATE(
                        SUBSTRING(d.name FROM '^(\d{8})'),
                        'YYYYMMDD'
                    )
                )::text AS doc_date,
                LEFT(d.content, 500) AS content
            FROM documents d
            LEFT JOIN meetings m ON d.meeting_id = m.id
            WHERE LOWER(d.name) LIKE '%notulen%raadsvergadering%'
              AND d.content IS NOT NULL
              AND LENGTH(d.content) > 1000
        ) sub
        WHERE doc_date IS NOT NULL
        ORDER BY doc_date ASC
    """)

    voorzitter_pattern = re.compile(
        r'[Vv]oorzitter:\s*(?:de\s+heer|mevrouw)\s+'
        r'(?:(?:drs|mr|ir|ing|dr)\.\s*)*'
        r'([A-Z][A-Za-z\.\s\-]+?)'
        r'(?:,\s*burgemeester|(?:\s*\n|\s*,\s*met\s|$))',
        re.IGNORECASE | re.MULTILINE
    )

    # Collect all (date, voorzitter) observations, then build yearly_mayors
    # with BOTH first and last observation per year (catches mid-year transitions)
    all_observations = []  # list of (date_str, name, doc_id)
    for doc_id, meeting_date, content in cur.fetchall():
        if not content:
            continue
        m = voorzitter_pattern.search(content)
        if m:
            name = m.group(1).strip()
            all_observations.append((meeting_date[:10], name, doc_id))

    # Group by (year, surname_key) — deduplicate, keep first date for each unique name per year
    # Use a dict of year -> list of {name, first_date, last_date, doc_id}
    year_name_seen: dict[str, dict] = {}  # (year, surname) -> info
    for date_str, name, doc_id in all_observations:
        year = date_str[:4]
        key = (year, surname_key(name))
        if key not in year_name_seen:
            year_name_seen[key] = {'name': name, 'first_date': date_str, 'doc_id': doc_id}

    # yearly_mayors: year -> list of unique voorzitters observed that year
    yearly_mayors: dict[str, list] = {}
    for (year, _), info in sorted(year_name_seen.items()):
        yearly_mayors.setdefault(year, []).append(info)

    if verbose:
        for year in sorted(yearly_mayors.keys()):
            for info in yearly_mayors[year]:
                print(f"  L1: {year} voorzitter = {info['name']} (first: {info['first_date']})")

    # Cross-check: for each (year, voorzitter), does it match a burgemeester record?
    # Only flag as error if the surname is NOT in burgemeester_rollen at all on that date —
    # substitutes (wethouder/raadslid chairing) are expected and should be warnings, not errors.
    known_burgemeester_keys = {r['naam'].lower().split('-')[0] for r in burgemeester_rollen}

    for year in sorted(yearly_mayors.keys()):
        for info in yearly_mayors[year]:
            date_str = info['first_date']
            name_k = surname_key(info['name'])
            matched = match_in_rollen(info['name'], burgemeester_rollen, role='burgemeester', date_str=date_str)
            if not matched:
                if name_k in known_burgemeester_keys:
                    # Known burgemeester, date mismatch
                    findings.append(Finding(
                        id=f"L1-{len(findings)+1:03d}",
                        layer="burgemeester",
                        severity="warning",
                        finding_type="date_mismatch",
                        description=f"Burgemeester '{info['name']}' found in notulen on {date_str} but record dates don't cover this date",
                        source_truth=info,
                        confidence=0.9,
                    ))
                else:
                    # Unknown person as voorzitter — substitute chair, not an error
                    findings.append(Finding(
                        id=f"L1-{len(findings)+1:03d}",
                        layer="burgemeester",
                        severity="info",
                        finding_type="substitute_voorzitter",
                        description=f"Non-burgemeester '{info['name']}' as voorzitter on {date_str} (substitute chair)",
                        source_truth=info,
                        confidence=0.8,
                    ))
            elif verbose:
                print(f"  L1 OK: {year} — {info['name']} matches {matched['naam']}")

    # Detect transitions: walk all observations chronologically
    prev_key = None
    for date_str, name, doc_id in all_observations:
        name_k = surname_key(name)
        if name_k in known_burgemeester_keys and name_k != prev_key:
            if prev_key is not None:
                findings.append(Finding(
                    id=f"L1-{len(findings)+1:03d}",
                    layer="burgemeester",
                    severity="info",
                    finding_type="transition_detected",
                    description=f"Burgemeester changed from '{prev_key}' to '{name_k}' — first seen {date_str}",
                    source_truth={'name': name, 'meeting_date': date_str, 'doc_id': doc_id},
                    confidence=1.0,
                ))
            prev_key = name_k

    # Check: are there burgemeester records not found in any notulen?
    all_observed_keys = {surname_key(name) for _, name, _ in all_observations}
    for rec in burgemeester_rollen:
        rec_key = rec['naam'].lower().split('-')[0]
        if rec_key not in all_observed_keys:
            findings.append(Finding(
                id=f"L1-{len(findings)+1:03d}",
                layer="burgemeester",
                severity="warning",
                finding_type="record_not_in_notulen",
                description=f"Burgemeester record '{rec['naam']}' ({rec['periode_van']} — {rec['periode_tot']}) not found as voorzitter in any notulen",
                current_record=rec,
                confidence=0.9,
            ))

    return findings


# ---------------------------------------------------------------------------
# Layer 2: Wethouders
# ---------------------------------------------------------------------------

def audit_wethouders(cur, rollen: list, verbose: bool = False) -> list[Finding]:
    findings = []
    wethouder_rollen = [r for r in rollen if r['rol'] == 'wethouder']

    # 2a: Extract all benoeming documents
    cur.execute("""
        SELECT d.id, d.name, m.start_date::text, LEFT(d.content, 1500)
        FROM documents d
        LEFT JOIN meetings m ON d.meeting_id = m.id
        WHERE LOWER(d.name) LIKE '%%benoeming%%wethouder%%'
          AND d.content IS NOT NULL
          AND LOWER(d.name) NOT LIKE '%%lid%%algemeen%%'
          AND LOWER(d.name) NOT LIKE '%%gemeenschappelijke%%'
          AND LOWER(d.name) NOT LIKE '%%voordracht%%'
          AND LOWER(d.name) NOT LIKE '%%fractie%%'
        ORDER BY m.start_date NULLS LAST
    """)

    benoemingen = []
    for doc_id, doc_name, meeting_date, content in cur.fetchall():
        # Extract wethouder name from doc title
        m = re.search(
            r'benoeming\s+(?:wethouder\s+)?(.+?)(?:\s+tot\s+wethouder|\s+als\s+lid|\s+in\s+gemeen|\s*$)',
            doc_name, re.IGNORECASE
        )
        if m:
            name = m.group(1).strip()
            # Skip generic/noise
            if any(skip in name.lower() for skip in ['onderwijs', 'lid', 'algemeen', 'raadsvoorstel', 'over', 'curriculum']):
                continue
            benoemingen.append({
                'name': name,
                'date': (meeting_date or '')[:10],
                'doc_id': doc_id,
                'doc_name': doc_name[:80],
            })

    if verbose:
        print(f"  L2: Found {len(benoemingen)} wethouder benoeming documents")

    # Cross-check: every benoeming should have a matching record
    for ben in benoemingen:
        if not ben['date'] or ben['date'] == '':
            continue
        matched = match_in_rollen(ben['name'], wethouder_rollen, role='wethouder')
        if not matched:
            findings.append(Finding(
                id=f"L2-{len(findings)+1:03d}",
                layer="wethouder",
                severity="error",
                finding_type="benoeming_without_record",
                description=f"Benoeming document for '{ben['name']}' ({ben['date']}) has no matching wethouder record",
                source_truth=ben,
                confidence=0.95,
            ))
        else:
            # Check date proximity (within 30 days)
            if ben['date'] and matched['periode_van']:
                try:
                    ben_date = datetime.strptime(ben['date'], '%Y-%m-%d').date()
                    rec_date = datetime.strptime(matched['periode_van'], '%Y-%m-%d').date()
                    diff = abs((ben_date - rec_date).days)
                    if diff > 30:
                        findings.append(Finding(
                            id=f"L2-{len(findings)+1:03d}",
                            layer="wethouder",
                            severity="warning",
                            finding_type="date_mismatch",
                            description=f"Wethouder '{ben['name']}' benoeming date {ben['date']} differs from record start {matched['periode_van']} by {diff} days",
                            source_truth=ben,
                            current_record=matched,
                            confidence=0.9,
                        ))
                except ValueError:
                    pass

    # 2b: Check for wethouder records without any benoeming document
    for rec in wethouder_rollen:
        found_benoeming = False
        for ben in benoemingen:
            if surname_key(ben['name']) == rec['naam'].lower().split('-')[0]:
                found_benoeming = True
                break
        if not found_benoeming:
            findings.append(Finding(
                id=f"L2-{len(findings)+1:03d}",
                layer="wethouder",
                severity="warning",
                finding_type="record_without_benoeming",
                description=f"Wethouder record '{rec['naam']}' ({rec['periode_van']} — {rec['periode_tot']}) has no matching benoeming document",
                current_record=rec,
                confidence=0.85,
            ))

    # 2c: Sample "Namens het college" from notulen (2 per year)
    cur.execute("""
        SELECT m.start_date::text, LEFT(d.content, 3500)
        FROM documents d
        JOIN meetings m ON d.meeting_id = m.id
        WHERE LOWER(d.name) LIKE '%%notulen%%raadsvergadering%%'
          AND d.content IS NOT NULL
          AND LENGTH(d.content) > 2000
          AND (EXTRACT(MONTH FROM m.start_date) IN (1, 2, 6, 7))
        ORDER BY m.start_date
    """)

    college_pattern = re.compile(
        r'(?:Namens\s+het\s+college|[Vv]an\s+de\s+zijde\s+van\s+het\s+college)\s+'
        r'(?:van\s+burgemeester\s+en\s+wethouders\s+)?'
        r'(?:zijn\s+|is\s+)?(?:aanwezig\s+)?'
        r'(?:de\s+heer|mevrouw|de\s+heren)?\s*'
        r'(.+?)(?:\.\s*\n|\n\s*(?:Griffier|Secretaris|Opening|De\s+VOORZITTER))',
        re.IGNORECASE | re.DOTALL
    )

    college_samples = 0
    college_names_by_year = defaultdict(set)
    for meeting_date, content in cur.fetchall():
        if not content:
            continue
        m = college_pattern.search(content)
        if m:
            section = m.group(1)
            # Extract individual names
            names = re.findall(
                r'(?:de\s+heer|mevrouw|de\s+heren)?\s*'
                r'(?:(?:drs|mr|ir|ing)\.\s*)?'
                r'([A-Z][a-zà-ü]+(?:\s*[-–]\s*[A-Za-zà-ü]+)?)',
                section
            )
            year = meeting_date[:4]
            for n in names:
                if len(n) > 2 and n.lower() not in ('van', 'een', 'het', 'den', 'ten', 'als', 'zijn'):
                    college_names_by_year[year].add(n)
            college_samples += 1

    if verbose:
        print(f"  L2: Sampled {college_samples} notulen for college attendance")
        for year in sorted(college_names_by_year.keys()):
            print(f"    {year}: {sorted(college_names_by_year[year])}")

    return findings


# ---------------------------------------------------------------------------
# Layer 3: Raadsleden
# ---------------------------------------------------------------------------

def audit_raadsleden(cur, rollen: list, verbose: bool = False) -> list[Finding]:
    findings = []
    raadslid_rollen = [r for r in rollen if r['rol'] == 'raadslid']

    # 3a: Mid-term replacements — mededeling documents
    cur.execute("""
        SELECT d.name, m.start_date::text, LEFT(d.content, 800)
        FROM documents d
        LEFT JOIN meetings m ON d.meeting_id = m.id
        WHERE (LOWER(d.name) LIKE '%%mededeling aanneming benoeming%%lid%%raad%%'
           OR LOWER(d.name) LIKE '%%mededeling%%aanneming%%benoeming%%kieswet%%')
        ORDER BY m.start_date NULLS LAST
    """)

    mededelingen = []
    for doc_name, meeting_date, content in cur.fetchall():
        m = re.search(
            r'(?:V\s*2|V2)\s*[-–—]?\s*(?:dhr\.\s*|mw\.\s*|mevrouw\s*)?(.+?)(?:\s*$|\s*per\s|\d{3,})',
            doc_name, re.IGNORECASE
        )
        if not m:
            m = re.search(r'Raad.*?[-–—]\s*(?:dhr\.\s*|mw\.\s*)?(.+?)(?:\s*$)', doc_name)
        if m:
            name = m.group(1).strip().rstrip('.')
            name = re.sub(r'\s+', ' ', name)
            if len(name) > 3:
                mededelingen.append({
                    'name': name,
                    'date': (meeting_date or '')[:10],
                    'doc_name': doc_name[:80],
                })

    if verbose:
        print(f"  L3: Found {len(mededelingen)} mid-term raadslid benoemingen")

    for med in mededelingen:
        if not med['date']:
            continue
        matched = match_in_rollen(med['name'], raadslid_rollen, role='raadslid')
        if not matched:
            findings.append(Finding(
                id=f"L3-{len(findings)+1:03d}",
                layer="raadslid",
                severity="warning",
                finding_type="mededeling_without_record",
                description=f"Mededeling for '{med['name']}' ({med['date']}) has no matching raadslid record",
                source_truth=med,
                confidence=0.85,
            ))

    # 3b: Post-election oath sections
    election_windows = [
        ('2002', '2002-03-20', '2002-05-01'),
        ('2006', '2006-03-10', '2006-05-01'),
        ('2010', '2010-03-10', '2010-05-01'),
        ('2014', '2014-03-20', '2014-05-01'),
        ('2018', '2018-03-25', '2018-05-01'),
        ('2022', '2022-03-25', '2022-05-01'),
    ]

    for label, win_start, win_end in election_windows:
        cur.execute("""
            SELECT d.id, d.name, m.start_date::text, LEFT(d.content, 8000)
            FROM documents d
            JOIN meetings m ON d.meeting_id = m.id
            WHERE LOWER(d.name) LIKE '%%notulen%%raadsvergadering%%'
              AND m.start_date >= %s AND m.start_date <= %s
              AND d.content IS NOT NULL
              AND LENGTH(d.content) > 5000
            ORDER BY m.start_date
            LIMIT 3
        """, (win_start, win_end))

        oath_names = set()
        for doc_id, doc_name, meeting_date, content in cur.fetchall():
            if not content:
                continue
            # Extract names from oath: "De heer LASTNAME (party)"
            for m in re.finditer(
                r'(?:De heer|Mevrouw)\s+([A-Z][A-Za-zà-ü\s\-]+?)\s*\(([^)]+)\)',
                content
            ):
                name = m.group(1).strip()
                party = m.group(2).strip()
                if name.lower() not in ('voorzitter',) and len(name) > 2:
                    oath_names.add((name, party))

        if oath_names and verbose:
            print(f"  L3: {label} oath — {len(oath_names)} names extracted")

    # 3c: Attendance sampling (2 notulen per year)
    cur.execute("""
        SELECT m.start_date::text, LEFT(d.content, 6000)
        FROM documents d
        JOIN meetings m ON d.meeting_id = m.id
        WHERE LOWER(d.name) LIKE '%%notulen%%raadsvergadering%%'
          AND d.content IS NOT NULL
          AND LENGTH(d.content) > 3000
          AND EXTRACT(MONTH FROM m.start_date) IN (1, 2, 9, 10)
        ORDER BY m.start_date
    """)

    attendance_pattern = re.compile(
        r'[Tt]egenwoordig\s+zijn\s+(\d+)\s+leden',
    )

    samples_checked = 0
    for meeting_date, content in cur.fetchall():
        if not content:
            continue
        m = attendance_pattern.search(content)
        if m:
            expected_count = int(m.group(1))
            year = meeting_date[:4]

            # Extract individual names from attendance
            attend_section = content[m.start():m.start() + 3000]
            names = re.findall(
                r'(?:de\s+heer(?:en)?|mevrouw)\s+'
                r'(?:(?:drs|mr|ir|ing|dr)\.\s*)*'
                r'([A-Z](?:\.[A-Z])*\.?\s*'
                r'(?:van\s+(?:de\s+|den\s+|het\s+)?|de\s+|den\s+|ten\s+)?'
                r'[A-Z][a-zà-üA-Z]+(?:\s*[-–]\s*(?:van\s+)?[A-Za-zà-ü]+)*)',
                attend_section
            )

            # Check each attendee against rollen
            for full_name in names:
                key = surname_key(full_name)
                if len(key) < 3:
                    continue
                matched = match_in_rollen(full_name, rollen, date_str=meeting_date[:10])
                if not matched:
                    findings.append(Finding(
                        id=f"L3-{len(findings)+1:03d}",
                        layer="raadslid",
                        severity="info",
                        finding_type="attendee_without_record",
                        description=f"'{full_name}' present in notulen {meeting_date[:10]} but no active record found",
                        source_truth={'name': full_name, 'meeting_date': meeting_date[:10]},
                        confidence=0.80,
                    ))

            samples_checked += 1

    if verbose:
        print(f"  L3: Checked {samples_checked} attendance samples")

    return findings


# ---------------------------------------------------------------------------
# Layer 4: Consistency checks
# ---------------------------------------------------------------------------

def audit_consistency(cur, rollen: list, verbose: bool = False) -> list[Finding]:
    findings = []

    # 4a: Raadslid + wethouder overlap (>1 day)
    cur.execute("""
        SELECT a.id, a.naam, a.rol, a.periode_van::text, a.periode_tot::text,
               b.id, b.naam, b.rol, b.periode_van::text, b.periode_tot::text
        FROM raadslid_rollen a
        JOIN raadslid_rollen b ON LOWER(a.naam) = LOWER(b.naam)
        WHERE a.id < b.id
          AND a.rol != b.rol
          AND a.rol IN ('raadslid', 'wethouder')
          AND b.rol IN ('raadslid', 'wethouder')
          AND a.periode_van < COALESCE(b.periode_tot, CURRENT_DATE)
          AND COALESCE(a.periode_tot, CURRENT_DATE) > b.periode_van
          AND (LEAST(COALESCE(a.periode_tot, CURRENT_DATE), COALESCE(b.periode_tot, CURRENT_DATE))
               - GREATEST(a.periode_van, b.periode_van)) > 1
    """)
    for row in cur.fetchall():
        findings.append(Finding(
            id=f"L4-{len(findings)+1:03d}",
            layer="consistency",
            severity="error",
            finding_type="role_overlap",
            description=f"'{row[1]}' has overlapping {row[2]} ({row[3]}—{row[4]}) and {row[7]} ({row[8]}—{row[9]})",
            confidence=1.0,
        ))

    # 4b: Same role overlap
    cur.execute("""
        SELECT a.id, a.naam, a.rol, a.periode_van::text, a.periode_tot::text,
               b.id, b.periode_van::text, b.periode_tot::text
        FROM raadslid_rollen a
        JOIN raadslid_rollen b ON LOWER(a.naam) = LOWER(b.naam) AND a.rol = b.rol
        WHERE a.id < b.id
          AND a.periode_van < COALESCE(b.periode_tot, CURRENT_DATE)
          AND COALESCE(a.periode_tot, CURRENT_DATE) > b.periode_van
          AND (LEAST(COALESCE(a.periode_tot, CURRENT_DATE), COALESCE(b.periode_tot, CURRENT_DATE))
               - GREATEST(a.periode_van, b.periode_van)) > 1
    """)
    for row in cur.fetchall():
        findings.append(Finding(
            id=f"L4-{len(findings)+1:03d}",
            layer="consistency",
            severity="error",
            finding_type="same_role_overlap",
            description=f"'{row[1]}' has overlapping {row[2]} periods: ({row[3]}—{row[4]}) and ({row[6]}—{row[7]})",
            confidence=1.0,
        ))

    # 4c: Date integrity
    cur.execute("""
        SELECT id, naam, rol, periode_van::text, periode_tot::text
        FROM raadslid_rollen
        WHERE periode_tot IS NOT NULL AND periode_van > periode_tot
    """)
    for row in cur.fetchall():
        findings.append(Finding(
            id=f"L4-{len(findings)+1:03d}",
            layer="consistency",
            severity="error",
            finding_type="invalid_dates",
            description=f"'{row[1]}' {row[2]}: start ({row[3]}) is after end ({row[4]})",
            confidence=1.0,
        ))

    # 4d: Duplicate records (same naam, same rol, same periode_van)
    cur.execute("""
        SELECT naam, rol, periode_van::text, COUNT(*)
        FROM raadslid_rollen
        GROUP BY naam, rol, periode_van
        HAVING COUNT(*) > 1
    """)
    for row in cur.fetchall():
        findings.append(Finding(
            id=f"L4-{len(findings)+1:03d}",
            layer="consistency",
            severity="error",
            finding_type="duplicate_record",
            description=f"'{row[0]}' has {row[3]} duplicate {row[1]} records starting {row[2]}",
            confidence=1.0,
        ))

    if verbose:
        print(f"  L4: {len(findings)} consistency issues found")

    return findings


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(all_findings: list[Finding], rollen: list, output_dir: str):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y-%m-%d_%H%M')
    total = len(rollen)
    unique = len({r['naam'].lower() for r in rollen})

    # Summary
    by_layer = defaultdict(int)
    by_severity = defaultdict(int)
    for f in all_findings:
        by_layer[f.layer] += 1
        by_severity[f.severity] += 1

    # JSON
    json_data = {
        'audit_timestamp': timestamp,
        'summary': {
            'total_findings': len(all_findings),
            'by_layer': dict(by_layer),
            'by_severity': dict(by_severity),
            'current_records': total,
            'current_persons': unique,
        },
        'findings': [asdict(f) for f in all_findings],
    }
    json_path = output_path / f'audit_rollen_{timestamp}.json'
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False, default=str)

    # Markdown
    md_lines = [
        f"# Audit Report — raadslid_rollen",
        f"**Date**: {timestamp} | **Records**: {total} | **Persons**: {unique}",
        "",
        "## Summary",
        "",
        "| Layer | Findings | Errors | Warnings | Info |",
        "|---|---|---|---|---|",
    ]

    for layer_name in ['burgemeester', 'wethouder', 'raadslid', 'consistency']:
        layer_findings = [f for f in all_findings if f.layer == layer_name]
        errors = sum(1 for f in layer_findings if f.severity == 'error')
        warnings = sum(1 for f in layer_findings if f.severity == 'warning')
        infos = sum(1 for f in layer_findings if f.severity == 'info')
        md_lines.append(f"| {layer_name} | {len(layer_findings)} | {errors} | {warnings} | {infos} |")

    md_lines.append("")

    # Findings by layer
    for layer_name in ['burgemeester', 'wethouder', 'raadslid', 'consistency']:
        layer_findings = [f for f in all_findings if f.layer == layer_name]
        if not layer_findings:
            continue

        md_lines.append(f"## Layer: {layer_name}")
        md_lines.append("")

        # Group by severity
        for severity in ['error', 'warning', 'info']:
            sev_findings = [f for f in layer_findings if f.severity == severity]
            if not sev_findings:
                continue

            md_lines.append(f"### {severity.upper()}S ({len(sev_findings)})")
            md_lines.append("")

            for f in sev_findings:
                md_lines.append(f"**{f.id}** [{f.finding_type}] (confidence: {f.confidence:.0%})")
                md_lines.append(f"> {f.description}")
                if f.source_truth:
                    md_lines.append(f"> Source: {json.dumps(f.source_truth, ensure_ascii=False, default=str)[:200]}")
                if f.current_record:
                    md_lines.append(f"> Record: {f.current_record.get('naam', '')} ({f.current_record.get('periode_van', '')} — {f.current_record.get('periode_tot', '')})")
                md_lines.append("")

    md_path = output_path / f'audit_rollen_{timestamp}.md'
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(md_lines))

    return json_path, md_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Audit raadslid_rollen against notulen")
    parser.add_argument("--layer", choices=["1", "2", "3", "4", "all"], default="all")
    parser.add_argument("--output-dir", default="output/reports")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    conn = get_connection()
    cur = conn.cursor()
    rollen = get_all_rollen(cur)

    print(f"Auditing {len(rollen)} records ({len({r['naam'].lower() for r in rollen})} persons)")

    all_findings = []
    layers = args.layer

    if layers in ("1", "all"):
        print("\n=== Layer 1: Burgemeesters ===")
        findings = audit_burgemeesters(cur, rollen, args.verbose)
        all_findings.extend(findings)
        print(f"  → {len(findings)} findings")

    if layers in ("4", "all"):
        print("\n=== Layer 4: Consistency ===")
        findings = audit_consistency(cur, rollen, args.verbose)
        all_findings.extend(findings)
        print(f"  → {len(findings)} findings")

    if layers in ("2", "all"):
        print("\n=== Layer 2: Wethouders ===")
        findings = audit_wethouders(cur, rollen, args.verbose)
        all_findings.extend(findings)
        print(f"  → {len(findings)} findings")

    if layers in ("3", "all"):
        print("\n=== Layer 3: Raadsleden ===")
        findings = audit_raadsleden(cur, rollen, args.verbose)
        all_findings.extend(findings)
        print(f"  → {len(findings)} findings")

    # Generate report
    json_path, md_path = generate_report(all_findings, rollen, args.output_dir)

    print(f"\n{'='*60}")
    print(f"  AUDIT COMPLETE")
    print(f"{'='*60}")
    errors = sum(1 for f in all_findings if f.severity == 'error')
    warnings = sum(1 for f in all_findings if f.severity == 'warning')
    infos = sum(1 for f in all_findings if f.severity == 'info')
    print(f"  Findings: {len(all_findings)} total ({errors} errors, {warnings} warnings, {infos} info)")
    print(f"  Report:   {md_path}")
    print(f"  JSON:     {json_path}")

    cur.close()
    conn.close()

    return 1 if errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
