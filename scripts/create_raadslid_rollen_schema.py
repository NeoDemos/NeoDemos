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
            # Synced from DB 2026-04-07 — audited against notulen + benoemingsdocumenten.
            # Covers all colleges 2002-2026, all burgemeesters, key raadsleden with role changes,
            # brief raadslid stints before wethouder benoeming, and tussentijdse benoemingen.
            #
            # === Burgemeesters ===
            ("Opstelten", "Ivo Opstelten", "burgemeester", "VVD", "1999-01-01", "2009-01-05", "Burgemeester Rotterdam 1999-2009. In DB vanaf 2002. Opgevolgd door Aboutaleb."),
            ("Aboutaleb", "Ahmed Aboutaleb", "burgemeester", "PvdA", "2009-01-05", "2024-10-01", "Opgevolgd door Carola Schouten"),
            ("Schouten", "Carola Schouten", "burgemeester", "ChristenUnie", "2024-10-01", None, "Opvolger Aboutaleb. Eerste vrouwelijke burgemeester Rotterdam."),
            #
            # === College 2002-2006 ===
            # Brief raadslid stints (elected March 2002, sworn in as wethouder May 2002)
            ("Geluk", "L.K. Geluk", "raadslid", "Leefbaar Rotterdam", "2002-03-06", "2002-05-29", "Raadslid voor benoeming wethouder (attendance)."),
            ("Pastors", "M.G.T. Pastors", "raadslid", "Leefbaar Rotterdam", "2002-03-06", "2002-05-29", "Raadslid voor benoeming wethouder (attendance)."),
            ("Pastors", "Marco Pastors", "wethouder", "Leefbaar Rotterdam", "2002-05-30", "2006-03-07", "College 2002-2006. Portefeuille: Grote Stedenbeleid, integratie"),
            ("Bolsius", "Leonard Bolsius", "wethouder", "Leefbaar Rotterdam", "2002-05-30", "2010-04-29", "College 2002-2006, herbenoemd 2006. Portefeuille: jeugd, onderwijs, sport"),
            ("Janssens", "Nico Janssens", "wethouder", "CDA", "2002-05-30", "2006-03-07", "College 2002-2006"),
            ("Geluk", "Leonard Geluk", "wethouder", "Leefbaar Rotterdam", "2002-05-30", "2010-04-29", "College 2002-2006, herbenoemd 2006. Portefeuille: jeugd, onderwijs"),
            #
            # === College 2006-2010 ===
            ("Karakus", "Hamit Karakus", "wethouder", "PvdA", "2006-03-15", "2014-03-24", "College 2006-2010, herbenoemd 2010. Portefeuille: ruimtelijke ordening, wonen, vastgoed, stedelijke economie. Afgetreden na verkiezingsverlies PvdA."),
            ("Baljeu", "Alexandra Baljeu", "wethouder", "VVD", "2006-06-15", "2014-06-30", "College 2006-2010, herbenoemd 2010. Portefeuille: financiën, verkeer, vervoer"),
            ("Harbers", "Mark Harbers", "wethouder", "VVD", "2006-06-15", "2010-04-29", "College 2006-2010"),
            ("Kaya", "Orhan Kaya", "wethouder", "PvdA", "2006-06-15", "2008-09-18", "College 2006-2010. Ontslag sept 2008, opgevolgd door Grashoff (GroenLinks)."),
            ("Kriens", "Jantine Kriens", "wethouder", "PvdA", "2006-06-15", "2014-03-18", "College 2006-2010, herbenoemd 2010. Portefeuille: sociale zaken, volksgezondheid"),
            ("Lamers", "Hans Lamers", "wethouder", "CDA", "2006-06-15", "2014-03-18", "College 2006-2010, herbenoemd 2010"),
            ("Schrijer", "Dominic Schrijer", "wethouder", "PvdA", "2006-06-15", "2014-03-18", "College 2006-2010, herbenoemd 2010"),
            ("Grashoff", "Rik Grashoff", "wethouder", "GroenLinks", "2008-09-18", "2010-04-29", "Opvolger Kaya. College 2006-2010. GroenLinks niet in coalitie 2010-2014."),
            ("Schneider", "Ronald Schneider", "raadslid", "Leefbaar Rotterdam", "2008-11-06", "2014-05-01", "Benoemd als raadslid 6 nov 2008 (vervanging H.C. van Schaik). Raadslid tot benoeming wethouder."),
            #
            # === College 2010-2014 ===
            # Brief raadslid stint (elected March 2010, wethouder from April 2010)
            ("Laan", "A.J.M. Laan", "raadslid", "D66", "2010-03-03", "2010-04-28", "Raadslid voor benoeming wethouder (attendance)."),
            ("Hulman", "Stanley Hulman", "wethouder", "CDA", "2010-04-29", "2014-03-18", "College 2010-2014"),
            ("Laan", "Korrie Laan", "wethouder", "D66", "2010-04-29", "2018-03-20", "College 2010-2014, herbenoemd 2014. Portefeuille: sport, recreatie"),
            ("Louwes", "Jantine Louwes", "wethouder", "PvdA", "2010-04-29", "2014-03-18", "College 2010-2014"),
            ("Moti", "Richard Moti", "wethouder", "PvdA", "2010-04-29", "2014-07-10", "College 2010-2014 (PvdA+VVD+D66+CDA). Portefeuille: financiën. Demissionair tot installatie Leefbaar-college."),
            ("Vervat", "Robert Vervat", "wethouder", "VVD", "2010-04-29", "2014-03-18", "College 2010-2014"),
            ("Florijn", "Marco Florijn", "wethouder", "Leefbaar Rotterdam", "2011-06-09", "2014-03-18", "Benoemd 2011 (benoemingsdoc). Portefeuille: werk en inkomen"),
            #
            # === College 2014-2018 ===
            ("Schneider", "Ronald Schneider", "wethouder", "Leefbaar Rotterdam", "2014-05-01", "2017-06-15", "Afgetreden wegens Waterfront-affaire. Portefeuille: stedelijke ontwikkeling en integratie"),
            # Brief raadslid stint (elected March 2014, wethouder from July 2014)
            ("Struijvenberg", "M.J.W. Struijvenberg", "raadslid", "Leefbaar Rotterdam", "2014-03-19", "2014-07-09", "Raadslid voor benoeming wethouder (attendance)."),
            ("Eerdmans", "Joost Eerdmans", "wethouder", "Leefbaar Rotterdam", "2014-07-10", "2018-03-20", "College 2014-2018"),
            ("Langenberg", "Adriaan Langenberg", "wethouder", "Leefbaar Rotterdam", "2014-07-10", "2018-03-20", "College 2014-2018"),
            ("Moti", "Richard Moti", "raadslid", "PvdA", "2014-07-10", "2018-07-05", "Raadslid tijdens Leefbaar-college 2014-2018."),
            ("Struijvenberg", "Maarten Struijvenberg", "wethouder", "Leefbaar Rotterdam", "2014-07-10", "2018-03-20", "College 2014-2018. Portefeuille: participatie, maatregelen, handhaving"),
            ("Visser", "Adriaan Visser", "wethouder", "D66", "2014-07-10", "2018-03-20", "College 2014-2018. Portefeuille: financiën, organisatie, haven"),
            ("Simons", "Robert Simons", "wethouder", "Leefbaar Rotterdam", "2017-06-15", "2018-03-20", "College 2014-2018. Vermoedelijk opvolger Schneider na Waterfront-affaire."),
            #
            # === College 2018-2022 ===
            ("Karremans", "Vincent Karremans", "raadslid", "VVD", "2018-03-29", "2021-09-01", "Raadslid tot benoeming als wethouder (opvolger Wijbenga)."),
            ("Lansink-Bastemeijer", "Pascal Lansink-Bastemeijer", "raadslid", "VVD", "2018-03-29", "2022-06-16", "Beëdigd 29 maart 2018. Fractievoorzitter VVD na vertrek Karremans naar college."),
            ("Tak", "Dennis Tak", "raadslid", "PvdA", "2018-03-29", "2022-03-30", "Eerste stint. Beëdigd 29 maart 2018. Niet herkozen 2022."),
            ("Versnel", "Tim Versnel", "raadslid", "VVD", "2018-03-29", "2022-06-16", "Beëdigd 29 maart 2018. Raadslid tot benoeming wethouder."),
            ("Vreugdenhil", "Gerben Vreugdenhil", "raadslid", "Leefbaar Rotterdam", "2018-03-29", "2022-06-16", "G.J.M. Vreugdenhil RA. Beëdigd 29 maart 2018."),
            ("Zeegers", "Chantal Zeegers", "raadslid", "D66", "2018-03-29", "2022-06-16", "Beëdigd 29 maart 2018. Lid COR. Raadslid tot benoeming wethouder."),
            ("Kurvers", "Bas Kurvers", "wethouder", "VVD", "2018-07-01", "2022-06-16", "Portefeuille: bouwen, wonen, energietransitie gebouwde omgeving"),
            ("Wijbenga", "Bert Wijbenga", "wethouder", "VVD", "2018-07-01", "2021-09-01", "Vertrokken als burgemeester Vlaardingen"),
            ("Bokhove", "Judith Bokhove", "wethouder", "GroenLinks", "2018-07-05", "2022-03-30", "College 2018-2022 (18bb5606). Portefeuille: mobiliteit, jeugd"),
            ("Bonte", "Arno Bonte", "wethouder", "GroenLinks", "2018-07-05", "2022-03-30", "College 2018-2022 (18bb5608). Portefeuille: duurzaamheid, luchtkwaliteit"),
            ("De Langen", "Sven de Langen", "wethouder", "PvdA", "2018-07-05", "2022-03-30", "College 2018-2022 (18bb5609). Portefeuille: zorg, ouderen, wijkteams"),
            ("Grauss", "Michel Grauss", "wethouder", "Leefbaar Rotterdam", "2018-07-05", "2022-03-30", "College 2018-2022 (18bb5611)"),
            ("Kasmi", "Said Kasmi", "wethouder", "D66", "2018-07-05", "2022-03-30", "Eerste college. Benoemd 5 juli 2018 (18bb5613). Portefeuille: onderwijs, cultuur en toerisme"),
            ("Kathmann", "Barbara Kathmann", "wethouder", "PvdA", "2018-07-05", "2022-03-30", "College 2018-2022 (18bb5616). Portefeuille: werk, inkomen, innovatie"),
            ("Moti", "Richard Moti", "wethouder", "PvdA", "2018-07-05", "2022-03-30", "College 2018-2022 (18bb5619). Portefeuille: financiën, sport, evenementen."),
            ("Visser", "Adriaan Visser", "wethouder", "D66", "2018-07-05", "2019-03-07", "College 2018-2022 (18bb5621). Vertrokken, opgevolgd door Van Gils."),
            ("Van Gils", "Arjan van Gils", "wethouder", "D66", "2019-03-07", "2022-03-30", "Opvolger Visser (19bb12798). Portefeuille: financiën, organisatie"),
            ("Vermeij", "Roos Vermeij", "wethouder", "PvdA", "2021-02-18", "2022-03-30", "Benoemd 18 feb 2021 (21bb2043)"),
            ("Karremans", "Vincent Karremans", "wethouder", "VVD", "2021-09-02", "2024-07-02", "Opvolger Wijbenga sep 2021. Herbenoemd in college 2022-2026 op 16 juni 2022. Portefeuille: handhaving, buitenruimte, mobiliteit. Vertrokken als staatssecretaris jul 2024."),
            ("Eskes", "Edward Eskes", "wethouder", "VVD", "2021-10-14", "2022-03-30", "Benoemd 14 okt 2021 (21bb12907)"),
            #
            # === College 2022-2026 ===
            # Brief raadslid stints (elected March 2022, sworn in as wethouder June 2022)
            ("Achbar", "F. Achbar", "raadslid", "DENK", "2022-03-16", "2022-06-15", "Raadslid voor benoeming wethouder (attendance)."),
            ("Mohamed-Hoesein", "N.D.Z.R. Mohamed-Hoesein", "raadslid", "DENK", "2022-03-16", "2023-09-27", "Raadslid voor benoeming wethouder (attendance). Wethouder v.a. 2023-09-28."),
            ("Simons", "R.A.C.J. Simons", "raadslid", "Leefbaar Rotterdam", "2022-03-16", "2022-06-15", "Raadslid voor benoeming wethouder (attendance)."),
            ("Yigit", "E. Yigit", "raadslid", "DENK", "2022-03-16", "2022-06-15", "Raadslid voor benoeming wethouder (attendance)."),
            ("Moti", "Richard Moti", "raadslid", "PvdA", "2022-03-30", "2023-11-28", "Raadslid 2022-2026. Ontslag 28 nov 2023, opgevolgd door D.P.A. Tak."),
            ("Achbar", "Faouzi Achbar", "wethouder", "DENK", "2022-06-16", None, "Beëdigd 16 juni 2022."),
            ("Kasmi", "Said Kasmi", "wethouder", "D66", "2022-06-16", None, "Tweede college. Beëdigd 16 juni 2022. Portefeuille: onderwijs, cultuur en evenementen"),
            ("Simons", "Robert Simons", "wethouder", "Leefbaar Rotterdam", "2022-06-16", None, "R.A.C.J. Simons. Beëdigd 16 juni 2022."),
            ("Struijvenberg", "Maarten Struijvenberg", "wethouder", "Leefbaar Rotterdam", "2022-06-16", None, "Beëdigd 16 juni 2022. Portefeuille: zorg, ouderen (tot benoeming Buijt okt 2022), daarna organisatie en dienstverlening"),
            ("Versnel", "Tim Versnel", "wethouder", "VVD", "2022-06-16", None, "Beëdigd als wethouder 16 juni 2022. Portefeuille: werk & inkomen, NPRZ, EU-arbeidsmigranten"),
            ("Vreugdenhil", "Gerben Vreugdenhil", "wethouder", "Leefbaar Rotterdam", "2022-06-16", "2025-12-18", "Beëdigd 16 juni 2022. Kort wethouder, daarna terug als raadslid."),
            ("Yigit", "Enes Yigit", "wethouder", "DENK", "2022-06-16", "2023-09-25", "Beëdigd 16 juni 2022. Ontslag genomen, opgevolgd door Mohamed-Hoesein."),
            ("Zeegers", "Chantal Zeegers", "wethouder", "D66", "2022-06-16", None, "Beëdigd als wethouder 16 juni 2022. Portefeuille: klimaat, bouwen en wonen"),
            ("Buijt", "Ronald Buijt", "wethouder", "Leefbaar Rotterdam", "2022-10-31", None, "Portefeuille: zorg, ouderen, jeugdzorg, organisatie en dienstverlening"),
            ("Mohamed-Hoesein", "Natasha Mohamed-Hoesein", "wethouder", "DENK", "2023-09-28", None, "Opvolger Yigit (23bb006173). Benoemd 25 sep 2023."),
            ("Tak", "Dennis Tak", "raadslid", "PvdA", "2024-01-10", "2025-09-11", "Tweede stint. Benoemd 10 jan 2024 ter vervanging van R. Moti. Afscheid raadsvergadering 11 sept 2025 (agenda 1.2). Vertrok voor Tweede Kamer-campagne. Opvolging door J.M. de Bruijn (25bb006188)."),
            ("Lansink-Bastemeijer", "Pascal Lansink-Bastemeijer", "wethouder", "VVD", "2024-07-11", None, "Opvolger Karremans (24bb004988). Beëdigd 11 juli 2024. Portefeuille: handhaving, buitenruimte, mobiliteit"),
            ("Van Rij", "Bart-Joost van Rij", "wethouder", "Leefbaar Rotterdam", "2025-02-20", "2025-06-12", "Tijdelijk vervanger Struijvenberg (gezondheidsredenen). 16 weken v.a. 20 feb 2025 (25bb001007)."),
            ("Vreugdenhil", "Gerben Vreugdenhil", "raadslid", "Leefbaar Rotterdam", "2025-12-18", None, "Herintrede als raadslid (25bb009634)."),
            ("Van Rij", "Bart-Joost van Rij", "wethouder", "Leefbaar Rotterdam", "2025-10-02", "2026-01-22", "Tijdelijk vervanger Struijvenberg (verlof). 16 weken v.a. 2 okt 2025 (25bb006992)."),
            #
            # === Raadsleden Buijt (long-serving, role change) ===
            ("Buijt", "Ronald Buijt", "raadslid", "Leefbaar Rotterdam", "2006-03-15", "2018-03-28", "Raadslid 2006-2018."),
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
