#!/usr/bin/env python3
"""
Schema: raadslid_rollen — Role metadata for Rotterdam council members.

Tracks role changes over time (raadslid → wethouder → etc.) so that
MCP search tools can filter by role and date range automatically.

This table is designed to integrate with a future GraphRAG entity layer:
  (Person) --[ROLE:raadslid]--> (Party) {period}
  (Person) --[ROLE:wethouder]--> (Party) {period}

Usage:
    python scripts/create_raadslid_rollen_schema.py
"""

import os
import psycopg2

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/neodemos",
)


def create_schema():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS raadslid_rollen (
            id SERIAL PRIMARY KEY,
            naam TEXT NOT NULL,                   -- e.g. "Buijt"
            volledige_naam TEXT,                   -- e.g. "Ronald Buijt"
            rol TEXT NOT NULL,                     -- raadslid, wethouder, commissielid, burgemeester
            partij TEXT,                           -- e.g. "Leefbaar Rotterdam"
            periode_van DATE NOT NULL,             -- start of this role
            periode_tot DATE,                      -- end (NULL = current)
            commissies TEXT[],                     -- optional: committee memberships during this role
            notities TEXT,                         -- free-text notes
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_rollen_naam ON raadslid_rollen (LOWER(naam));
        CREATE INDEX IF NOT EXISTS idx_rollen_partij ON raadslid_rollen (LOWER(partij));
        CREATE INDEX IF NOT EXISTS idx_rollen_periode ON raadslid_rollen (periode_van, periode_tot);
    """)

    # Seed with known role changes (college 2022-2026)
    cur.execute("SELECT COUNT(*) FROM raadslid_rollen")
    count = cur.fetchone()[0]
    if count == 0:
        print("Seeding initial role data...")
        seed_data = [
            # Full audit against notulen + benoemingsdocumenten (2026-04-07)
            # Covers all colleges 2002-2026 + key raadsleden role changes
            #
            # === College 2002-2006 ===
            ("Pastors", "Marco Pastors", "wethouder", "Leefbaar Rotterdam", "2002-05-30", "2006-03-07", "College 2002-2006"),
            ("Bolsius", "Leonard Bolsius", "wethouder", "Leefbaar Rotterdam", "2002-05-30", "2010-04-29", "College 2002-2006, herbenoemd 2006"),
            ("Janssens", "Nico Janssens", "wethouder", "CDA", "2002-05-30", "2006-03-07", "College 2002-2006"),
            ("Geluk", "Leonard Geluk", "wethouder", "Leefbaar Rotterdam", "2002-05-30", "2010-04-29", "College 2002-2006, herbenoemd 2006"),
            #
            # === College 2006-2010 ===
            ("Karakus", "Hamit Karakus", "wethouder", "PvdA", "2006-03-15", "2014-03-24", "College 2006-2010, herbenoemd 2010. Portefeuille: ruimtelijke ordening, wonen"),
            ("Kriens", "Jantine Kriens", "wethouder", "PvdA", "2006-06-15", "2010-04-29", "College 2006-2010. Portefeuille: sociale zaken, volksgezondheid"),
            ("Grashoff", "Rik Grashoff", "wethouder", "GroenLinks", "2006-06-15", "2010-04-29", "College 2006-2010"),
            ("Lamers", "Hans Lamers", "wethouder", "CDA", "2006-06-15", "2010-04-29", "College 2006-2010"),
            ("Baljeu", "Alexandra Baljeu", "wethouder", "VVD", "2006-06-15", "2014-06-30", "College 2006-2010, herbenoemd 2010. Portefeuille: financiën, verkeer"),
            ("Schrijer", "Dominic Schrijer", "wethouder", "PvdA", "2006-06-15", "2010-04-29", "College 2006-2010"),
            ("Harbers", "Mark Harbers", "wethouder", "VVD", "2006-06-15", "2010-04-29", "College 2006-2010"),
            ("Kaya", "Orhan Kaya", "wethouder", "PvdA", "2006-06-15", "2010-04-29", "College 2006-2010"),
            #
            # === College 2010-2014 ===
            ("Florijn", "Marco Florijn", "wethouder", "Leefbaar Rotterdam", "2011-06-09", "2014-03-18", "Benoemd 2011. Portefeuille: werk en inkomen"),
            ("Laan", "Korrie Laan", "wethouder", "D66", "2010-04-29", "2018-03-20", "College 2010-2014, herbenoemd 2014"),
            ("Louwes", "Jantine Louwes", "wethouder", "PvdA", "2010-04-29", "2014-03-18", "College 2010-2014"),
            ("Hulman", "Stanley Hulman", "wethouder", "CDA", "2010-04-29", "2014-03-18", "College 2010-2014"),
            ("Vervat", "Robert Vervat", "wethouder", "VVD", "2010-04-29", "2014-03-18", "College 2010-2014"),
            ("Moti", "Rik Moti", "wethouder", "PvdA", "2010-04-29", "2022-03-30", "College 2010-2014, herbenoemd 2014 en 2018 (18bb5619)"),
            #
            # === College 2014-2018 ===
            ("Eerdmans", "Joost Eerdmans", "wethouder", "Leefbaar Rotterdam", "2014-07-10", "2018-03-20", "College 2014-2018"),
            ("Langenberg", "Adriaan Langenberg", "wethouder", "Leefbaar Rotterdam", "2014-07-10", "2018-03-20", "College 2014-2018"),
            ("Visser", "Adriaan Visser", "wethouder", "D66", "2014-07-10", "2018-03-20", "College 2014-2018. Portefeuille: financiën, organisatie, haven"),
            ("Visser", "Adriaan Visser", "wethouder", "D66", "2018-07-05", "2019-03-07", "College 2018-2022 (18bb5621). Vertrokken, opgevolgd door Van Gils."),
            #
            # === College 2018-2022 ===
            ("Grauss", "Michel Grauss", "wethouder", "Leefbaar Rotterdam", "2018-07-05", "2022-03-30", "College 2018-2022 (18bb5611)"),
            ("Bonte", "Arno Bonte", "wethouder", "GroenLinks", "2018-07-05", "2022-03-30", "College 2018-2022 (18bb5608). Portefeuille: duurzaamheid, luchtkwaliteit"),
            ("Bokhove", "Judith Bokhove", "wethouder", "GroenLinks", "2018-07-05", "2022-03-30", "College 2018-2022 (18bb5606). Portefeuille: mobiliteit, jeugd"),
            ("Kathmann", "Barbara Kathmann", "wethouder", "PvdA", "2018-07-05", "2022-03-30", "College 2018-2022 (18bb5616). Portefeuille: werk, inkomen, innovatie"),
            ("Kasmi", "Said Kasmi", "wethouder", "D66", "2018-07-05", "2022-03-30", "College 2018-2022 (18bb5613). Portefeuille: onderwijs, cultuur en toerisme"),
            ("De Langen", "Sven de Langen", "wethouder", "PvdA", "2018-07-05", "2022-03-30", "College 2018-2022 (18bb5609). Portefeuille: zorg, ouderen, wijkteams"),
            ("Van Gils", "Arjan van Gils", "wethouder", "D66", "2019-03-07", "2022-03-30", "Opvolger Visser (19bb12798). Portefeuille: financiën, organisatie"),
            ("Vermeij", "Roos Vermeij", "wethouder", "PvdA", "2021-02-18", "2022-03-30", "Benoemd 18 feb 2021 (21bb2043)"),
            ("Eskes", "Edward Eskes", "wethouder", "VVD", "2021-10-14", "2022-03-30", "Benoemd 14 okt 2021 (21bb12907)"),
            ("Wijbenga", "Bert Wijbenga", "wethouder", "VVD", "2018-07-01", "2021-09-01", "Vertrokken als burgemeester Vlaardingen"),
            ("Kurvers", "Bas Kurvers", "wethouder", "VVD", "2018-07-01", "2022-06-16", "Portefeuille: bouwen, wonen, energietransitie gebouwde omgeving"),
            ("Karremans", "Vincent Karremans", "raadslid", "VVD", "2018-03-29", "2021-09-01", None),
            ("Karremans", "Vincent Karremans", "wethouder", "VVD", "2021-09-02", "2024-07-02", "Opvolger Wijbenga. Herbenoemd 16 juni 2022. Vertrokken als staatssecretaris."),
            #
            # === College 2022-2026 ===
            ("Versnel", "Tim Versnel", "raadslid", "VVD", "2018-03-29", "2022-06-16", "Beëdigd 29 maart 2018."),
            ("Versnel", "Tim Versnel", "wethouder", "VVD", "2022-06-16", None, "Beëdigd 16 juni 2022. Portefeuille: werk & inkomen, NPRZ"),
            ("Lansink-Bastemeijer", "Pascal Lansink-Bastemeijer", "raadslid", "VVD", "2018-03-29", "2022-06-16", "Fractievoorzitter VVD."),
            ("Lansink-Bastemeijer", "Pascal Lansink-Bastemeijer", "wethouder", "VVD", "2024-07-11", None, "Opvolger Karremans (24bb004988)"),
            ("Kasmi", "Said Kasmi", "wethouder", "D66", "2022-06-16", None, "Herbenoemd. Portefeuille: onderwijs, cultuur en evenementen"),
            ("Zeegers", "Chantal Zeegers", "raadslid", "D66", "2018-03-29", "2022-06-16", "Beëdigd 29 maart 2018. Lid COR."),
            ("Zeegers", "Chantal Zeegers", "wethouder", "D66", "2022-06-16", None, "Portefeuille: klimaat, bouwen en wonen"),
            ("Achbar", "Faouzi Achbar", "wethouder", "DENK", "2022-06-16", None, "Beëdigd 16 juni 2022."),
            ("Yigit", "Enes Yigit", "wethouder", "DENK", "2022-06-16", "2023-09-25", "Ontslag, opgevolgd door Mohamed-Hoesein."),
            ("Mohamed-Hoesein", "Natasha Mohamed-Hoesein", "wethouder", "DENK", "2023-09-28", None, "Opvolger Yigit (23bb006173)."),
            ("Simons", "Robert Simons", "wethouder", "Leefbaar Rotterdam", "2022-06-16", None, "R.A.C.J. Simons."),
            ("Struijvenberg", "Maarten Struijvenberg", "wethouder", "Leefbaar Rotterdam", "2022-06-16", None, "Portefeuille: organisatie, dienstverlening"),
            ("Vreugdenhil", "Gerben Vreugdenhil", "raadslid", "Leefbaar Rotterdam", "2018-03-29", "2022-06-16", "G.J.M. Vreugdenhil RA."),
            ("Vreugdenhil", "Gerben Vreugdenhil", "wethouder", "Leefbaar Rotterdam", "2022-06-16", "2025-12-18", "Kort wethouder."),
            ("Vreugdenhil", "Gerben Vreugdenhil", "raadslid", "Leefbaar Rotterdam", "2025-12-18", None, "Herintrede (25bb009634)."),
            ("Buijt", "Ronald Buijt", "raadslid", "Leefbaar Rotterdam", "2006-03-15", "2018-03-28", None),
            ("Buijt", "Ronald Buijt", "wethouder", "Leefbaar Rotterdam", "2022-10-31", None, "Portefeuille: zorg, ouderen, jeugdzorg"),
            ("Schneider", "Ronald Schneider", "raadslid", "Leefbaar Rotterdam", "2008-11-06", "2014-05-01", "Vervanging H.C. van Schaik."),
            ("Schneider", "Ronald Schneider", "wethouder", "Leefbaar Rotterdam", "2014-05-01", "2017-06-15", "Afgetreden Waterfront-affaire."),
            #
            # === Burgemeester ===
            ("Aboutaleb", "Ahmed Aboutaleb", "burgemeester", "PvdA", "2009-01-05", "2024-10-01", "Opgevolgd door Carola Schouten"),
            #
            # === Raadsleden met rolwisselingen ===
            ("Tak", "Dennis Tak", "raadslid", "PvdA", "2018-03-29", "2022-03-30", "Eerste stint."),
            ("Tak", "Dennis Tak", "raadslid", "PvdA", "2024-01-10", "2025-09-11", "Tweede stint. Vervanging R. Moti. Afscheid 11 sept 2025 (agenda 1.2). Vertrok voor Tweede Kamer-campagne. Opvolging door J.M. de Bruijn (25bb006188)."),
        ]
        for naam, vol_naam, rol, partij, van, tot, notities in seed_data:
            cur.execute("""
                INSERT INTO raadslid_rollen (naam, volledige_naam, rol, partij, periode_van, periode_tot, notities)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (naam, vol_naam, rol, partij, van, tot, notities))
        print(f"  Seeded {len(seed_data)} role records.")

    conn.commit()
    cur.close()
    conn.close()
    print("✓ raadslid_rollen schema created successfully.")


if __name__ == "__main__":
    create_schema()
