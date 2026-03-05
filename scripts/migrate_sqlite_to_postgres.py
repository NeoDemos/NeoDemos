#!/usr/bin/env python3
"""
Migration script: SQLite → PostgreSQL
Migrates all NeoDemos data from SQLite to PostgreSQL with schema conversion.
"""

import sqlite3
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime
import sys

# Configuration
SQLITE_DB = 'data/neodemos.db'
PG_CONNECTION = 'postgresql://postgres:postgres@localhost:5432/neodemos'

def create_postgres_schema(pg_conn):
    """Create PostgreSQL schema"""
    with pg_conn.cursor() as cur:
        # Create meetings table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS meetings (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                start_date TIMESTAMP,
                committee TEXT,
                location TEXT,
                organization_id TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create agenda_items table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agenda_items (
                id TEXT PRIMARY KEY,
                meeting_id TEXT NOT NULL REFERENCES meetings(id),
                number TEXT,
                name TEXT NOT NULL
            )
        """)
        
        # Create documents table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                agenda_item_id TEXT NOT NULL REFERENCES agenda_items(id),
                meeting_id TEXT NOT NULL REFERENCES meetings(id),
                name TEXT NOT NULL,
                url TEXT,
                content TEXT,
                summary_json TEXT
            )
        """)
        
        # Create ingestion_log table (new)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ingestion_log (
                id SERIAL PRIMARY KEY,
                run_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                date_range_start DATE,
                date_range_end DATE,
                meetings_found INTEGER,
                meetings_inserted INTEGER,
                meetings_updated INTEGER,
                documents_downloaded INTEGER,
                errors TEXT
            )
        """)
        
        # Create indexes for performance
        cur.execute("CREATE INDEX IF NOT EXISTS idx_meetings_start_date ON meetings(start_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_agenda_items_meeting_id ON agenda_items(meeting_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_agenda_item_id ON documents(agenda_item_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_meeting_id ON documents(meeting_id)")
        
        pg_conn.commit()
        print("✓ PostgreSQL schema created")

def migrate_data(sqlite_conn, pg_conn):
    """Migrate all data from SQLite to PostgreSQL"""
    
    sqlite_cursor = sqlite3.connect(SQLITE_DB).cursor()
    
    # Migrate meetings
    print("\nMigrating meetings...")
    sqlite_cursor.execute("SELECT * FROM meetings")
    meetings = sqlite_cursor.fetchall()
    
    with pg_conn.cursor() as pg_cursor:
        for meeting in meetings:
            # meeting tuple: (id, name, start_date, committee, location, organization_id, last_updated)
            pg_cursor.execute("""
                INSERT INTO meetings (id, name, start_date, committee, location, organization_id, last_updated)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, tuple(meeting))
        pg_conn.commit()
    print(f"✓ Migrated {len(meetings)} meetings")
    
    # Migrate agenda items
    print("Migrating agenda items...")
    sqlite_cursor.execute("SELECT * FROM agenda_items")
    agenda_items = sqlite_cursor.fetchall()
    
    with pg_conn.cursor() as pg_cursor:
        for item in agenda_items:
            pg_cursor.execute("""
                INSERT INTO agenda_items (id, meeting_id, number, name)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, tuple(item))
        pg_conn.commit()
    print(f"✓ Migrated {len(agenda_items)} agenda items")
    
    # Migrate documents
    print("Migrating documents...")
    sqlite_cursor.execute("SELECT * FROM documents")
    documents = sqlite_cursor.fetchall()
    
    with pg_conn.cursor() as pg_cursor:
        for doc in documents:
            # Clean up NUL characters in content and summary_json
            doc_list = list(doc)
            if doc_list[5]:  # content field
                doc_list[5] = doc_list[5].replace('\x00', '')
            if doc_list[6]:  # summary_json field
                doc_list[6] = doc_list[6].replace('\x00', '')
            
            pg_cursor.execute("""
                INSERT INTO documents (id, agenda_item_id, meeting_id, name, url, content, summary_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, tuple(doc_list))
        pg_conn.commit()
    print(f"✓ Migrated {len(documents)} documents")
    
    sqlite_cursor.close()

def validate_migration(sqlite_conn, pg_conn):
    """Validate that all data was migrated correctly"""
    print("\n" + "="*50)
    print("DATA VALIDATION REPORT")
    print("="*50)
    
    sqlite_cursor = sqlite3.connect(SQLITE_DB).cursor()
    
    checks = [
        ("meetings", "meetings"),
        ("agenda_items", "agenda_items"),
        ("documents", "documents"),
    ]
    
    all_pass = True
    for sqlite_table, pg_table in checks:
        sqlite_cursor.execute(f"SELECT COUNT(*) FROM {sqlite_table}")
        sqlite_count = sqlite_cursor.fetchone()[0]
        
        with pg_conn.cursor() as pg_cursor:
            pg_cursor.execute(f"SELECT COUNT(*) FROM {pg_table}")
            pg_count = pg_cursor.fetchone()[0]
        
        status = "✓" if sqlite_count == pg_count else "✗"
        print(f"{status} {pg_table}: SQLite={sqlite_count}, PostgreSQL={pg_count}")
        
        if sqlite_count != pg_count:
            all_pass = False
    
    sqlite_cursor.close()
    
    print("\n" + "="*50)
    if all_pass:
        print("MIGRATION: SUCCESS ✓")
    else:
        print("MIGRATION: FAILED ✗")
    print("="*50)
    
    return all_pass

def main():
    try:
        print("NeoDemos: SQLite → PostgreSQL Migration")
        print("="*50)
        
        # Connect to PostgreSQL
        print("\nConnecting to PostgreSQL...")
        pg_conn = psycopg2.connect(PG_CONNECTION)
        print("✓ Connected to PostgreSQL")
        
        # Create schema
        print("\nCreating PostgreSQL schema...")
        create_postgres_schema(pg_conn)
        
        # Migrate data
        print("\nMigrating data...")
        migrate_data(sqlite3.connect(SQLITE_DB), pg_conn)
        
        # Validate
        success = validate_migration(sqlite3.connect(SQLITE_DB), pg_conn)
        
        pg_conn.close()
        
        if success:
            print("\n✓ Migration complete! NeoDemos is ready for PostgreSQL.")
            return 0
        else:
            print("\n✗ Migration validation failed. Please check the database.")
            return 1
            
    except Exception as e:
        print(f"\n✗ Error during migration: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
