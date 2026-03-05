#!/usr/bin/env python3
"""
Database Schema Expansion for Party Analysis
Adds tables for storing party programmes, positions, and statements extracted from notulen
"""

import psycopg2
import sys

def create_party_analysis_schema():
    """Create PostgreSQL schema for party analysis"""
    try:
        conn = psycopg2.connect(
            "postgresql://postgres:postgres@localhost:5432/neodemos"
        )
        cursor = conn.cursor()
        
        print("Creating party analysis schema...")
        
        # 1. Topics table - stores topic categories derived from meeting structure
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS topics (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                keywords TEXT[],
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_topics_name ON topics(name);
        """)
        print("✓ Created topics table")
        
        # 2. Party programmes table - stores metadata and content of party programmes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS party_programmes (
                id SERIAL PRIMARY KEY,
                party_name TEXT NOT NULL,
                election_year INTEGER NOT NULL,
                file_path TEXT,
                file_name TEXT,
                source_url TEXT,
                pdf_content TEXT,
                extraction_status TEXT DEFAULT 'pending',  -- pending, extracted, analyzed
                sections_identified INTEGER,
                analysis_date TIMESTAMP,
                ingestion_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(party_name, election_year)
            );
            CREATE INDEX IF NOT EXISTS idx_party_programmes_party ON party_programmes(party_name);
            CREATE INDEX IF NOT EXISTS idx_party_programmes_year ON party_programmes(election_year);
        """)
        print("✓ Created party_programmes table")
        
        # 3. Party positions table - stores extracted positions from programmes and notulen
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS party_positions (
                id SERIAL PRIMARY KEY,
                party_name TEXT NOT NULL,
                topic_id INTEGER REFERENCES topics(id),
                position_text TEXT NOT NULL,
                source_type TEXT NOT NULL,  -- 'programme' or 'notulen'
                source_id INTEGER,  -- references party_programmes.id for programme, documents.id for notulen
                source_date TIMESTAMP,
                confidence_score FLOAT DEFAULT 1.0,  -- AI confidence in the extraction
                analysis_metadata TEXT,  -- JSON string with extraction details
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_party_positions_party ON party_positions(party_name);
            CREATE INDEX IF NOT EXISTS idx_party_positions_topic ON party_positions(topic_id);
            CREATE INDEX IF NOT EXISTS idx_party_positions_source ON party_positions(source_type, source_id);
        """)
        print("✓ Created party_positions table")
        
        # 4. Party statements table - stores individual statements from notulen
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS party_statements (
                id SERIAL PRIMARY KEY,
                party_name TEXT NOT NULL,
                meeting_id TEXT NOT NULL REFERENCES meetings(id),
                document_id TEXT NOT NULL REFERENCES documents(id),
                statement_text TEXT NOT NULL,
                statement_date TIMESTAMP NOT NULL,
                topic_id INTEGER REFERENCES topics(id),
                speaker_name TEXT,
                context_text TEXT,  -- surrounding context from notulen
                confidence_score FLOAT DEFAULT 1.0,
                analysis_metadata TEXT,  -- JSON with extraction details
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_party_statements_party ON party_statements(party_name);
            CREATE INDEX IF NOT EXISTS idx_party_statements_meeting ON party_statements(meeting_id);
            CREATE INDEX IF NOT EXISTS idx_party_statements_document ON party_statements(document_id);
            CREATE INDEX IF NOT EXISTS idx_party_statements_topic ON party_statements(topic_id);
        """)
        print("✓ Created party_statements table")
        
        # 5. Document type classification - helps identify notulen and other important documents
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS document_classifications (
                id SERIAL PRIMARY KEY,
                document_id TEXT NOT NULL UNIQUE REFERENCES documents(id),
                document_type TEXT,  -- 'notulen', 'agenda', 'bijlage', etc.
                is_notulen BOOLEAN DEFAULT FALSE,
                meeting_id TEXT REFERENCES meetings(id),
                extraction_status TEXT DEFAULT 'pending',  -- pending, extracted, analyzed
                extracted_text TEXT,  -- full extracted text from notulen
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_doc_class_type ON document_classifications(document_type);
            CREATE INDEX IF NOT EXISTS idx_doc_class_notulen ON document_classifications(is_notulen);
        """)
        print("✓ Created document_classifications table")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print("\n" + "="*60)
        print("Party analysis schema creation completed successfully!")
        print("="*60)
        return True
        
    except Exception as e:
        print(f"\n✗ Error creating schema: {e}", file=sys.stderr)
        return False

if __name__ == "__main__":
    success = create_party_analysis_schema()
    sys.exit(0 if success else 1)
