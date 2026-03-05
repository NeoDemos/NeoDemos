#!/usr/bin/env python3
"""
Initialize Topic Categories for Political Party Analysis
Topics are derived from Rotterdam's gemeenteraad committee structure
and major policy areas discussed in meetings.
"""

import psycopg2
import sys

# Topic definitions - structured by committee areas and broader policy themes
TOPICS = [
    # Main Committee-Based Topics (from gemeenteraad committee structure)
    {
        "name": "Werk & Inkomen",
        "description": "Employment, income support, social security, and labor market policies",
        "keywords": ["werk", "inkomen", "werkgelegenheid", "uitkering", "arbeid", "werk en inkomen"]
    },
    {
        "name": "Onderwijs",
        "description": "Education policy, school funding, curriculum, and educational institutions",
        "keywords": ["onderwijs", "school", "scholing", "leerling", "educatie", "leerplicht"]
    },
    {
        "name": "Samenleven",
        "description": "Social cohesion, integration, diversity, and community initiatives",
        "keywords": ["samenleven", "integratie", "diversiteit", "samenleving", "burgers"]
    },
    {
        "name": "Schuldhulpverlening & Armoedebestrijding",
        "description": "Debt relief, poverty reduction, and financial support for vulnerable groups",
        "keywords": ["schuld", "armoede", "schuldhulp", "financieel", "ondersteuning", "schulden"]
    },
    {
        "name": "Zorg & Welzijn",
        "description": "Healthcare, welfare programs, elderly care, and social services",
        "keywords": ["zorg", "welzijn", "gezondheid", "ouderen", "ziekenhuis", "health"]
    },
    {
        "name": "Cultuur",
        "description": "Cultural policies, arts, heritage, and cultural institutions",
        "keywords": ["cultuur", "kunst", "erfgoed", "museum", "theater", "cultuur"]
    },
    {
        "name": "Sport",
        "description": "Sports policies, recreational facilities, and physical activity programs",
        "keywords": ["sport", "fitness", "recreatie", "sportieven", "atletiek"]
    },
    {
        "name": "Bouwen & Wonen",
        "description": "Housing, construction, urban development, and building policies",
        "keywords": ["wonen", "huizen", "bouwen", "bouw", "woningmarkt", "huisvestin"]
    },
    {
        "name": "Buitenruimte & Groen",
        "description": "Public space, parks, green areas, and environmental quality",
        "keywords": ["groen", "buitenruimte", "park", "openbare ruimte", "natuur"]
    },
    {
        "name": "Mobiliteit & Verkeer",
        "description": "Transportation, traffic management, cycling infrastructure, and public transit",
        "keywords": ["mobiliteit", "verkeer", "fiets", "openbaar vervoer", "auto", "transport"]
    },
    {
        "name": "Haven & Scheepvaart",
        "description": "Port operations, shipping, waterfront development, and maritime economy",
        "keywords": ["haven", "scheepvaart", "waterfront", "schip", "havengebied"]
    },
    {
        "name": "Economie & Bedrijven",
        "description": "Business development, economic growth, entrepreneurship, and industry",
        "keywords": ["economie", "bedrijven", "ondernemingen", "handel", "handel"]
    },
    {
        "name": "Klimaat & Milieu",
        "description": "Climate action, environmental protection, sustainability, and emissions",
        "keywords": ["klimaat", "milieu", "duurzaam", "emissie", "energie", "groen"]
    },
    {
        "name": "Veiligheid",
        "description": "Public safety, crime prevention, emergency services, and law enforcement",
        "keywords": ["veiligheid", "criminaliteit", "politie", "brandweer", "openbare orde"]
    },
    {
        "name": "Bestuur & Organisatie",
        "description": "Municipal governance, organization, administration, and management",
        "keywords": ["bestuur", "organisatie", "gemeente", "raad", "ambtelijk"]
    },
    {
        "name": "Financiën",
        "description": "Municipal budget, finance, taxation, and financial management",
        "keywords": ["financiën", "budget", "begroting", "belasting", "geld", "financieel"]
    },
    
    # Cross-Cutting Policy Themes
    {
        "name": "Burgerparticipatie",
        "description": "Citizen participation, democracy, civic engagement, and public involvement",
        "keywords": ["participatie", "burgers", "democratie", "inspraak", "engagement"]
    },
    {
        "name": "Digitalisering",
        "description": "Digital transformation, technology adoption, and digital infrastructure",
        "keywords": ["digitaal", "digitalisering", "technologie", "informatica"]
    },
    {
        "name": "Genderbeleid",
        "description": "Gender equality, women's rights, and gender-based violence prevention",
        "keywords": ["gender", "vrouwen", "gelijkheid", "intimitatie"]
    },
    {
        "name": "Migratie & Integratie",
        "description": "Immigration policy, integration programs, and refugee support",
        "keywords": ["migratie", "asiel", "vluchtelingen", "integr"]
    },
    {
        "name": "Ruimtelijke Ordening",
        "description": "Spatial planning, zoning, land use planning, and urban design",
        "keywords": ["bestemmingsplan", "ruimtelijk", "stedenbouw", "landgebruik"]
    }
]

def initialize_topics():
    """Initialize topic categories in the database"""
    try:
        conn = psycopg2.connect(
            "postgresql://postgres:postgres@localhost:5432/neodemos"
        )
        cursor = conn.cursor()
        
        print("Initializing topic categories...")
        print("="*70)
        
        inserted_count = 0
        skipped_count = 0
        
        for topic in TOPICS:
            try:
                cursor.execute("""
                    INSERT INTO topics (name, description, keywords)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (name) DO NOTHING
                    RETURNING id;
                """, (
                    topic["name"],
                    topic["description"],
                    topic["keywords"]
                ))
                
                result = cursor.fetchone()
                if result:
                    inserted_count += 1
                    print(f"✓ {topic['name']:<40} (ID: {result[0]})")
                else:
                    skipped_count += 1
                    print(f"⊘ {topic['name']:<40} (already exists)")
            
            except Exception as e:
                print(f"✗ {topic['name']:<40} ({str(e)})")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print("\n" + "="*70)
        print(f"Topics initialized: {inserted_count} inserted, {skipped_count} already existed")
        print("="*70)
        return True
        
    except Exception as e:
        print(f"\n✗ Error initializing topics: {e}", file=sys.stderr)
        return False

if __name__ == "__main__":
    success = initialize_topics()
    sys.exit(0 if success else 1)
