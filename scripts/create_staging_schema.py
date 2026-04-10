#!/usr/bin/env python3
"""
Staging Schema for Committee Meeting Virtual Notulen Pipeline
=============================================================

Creates an isolated `staging` schema in PostgreSQL that mirrors the production
tables, plus pipeline-tracking tables. Also creates a separate Qdrant collection
for staging embeddings.

This ensures zero risk of contaminating production data during the
video-to-text transcription pipeline.

Usage:
    python scripts/create_staging_schema.py
    python scripts/create_staging_schema.py --drop   # Drop and recreate
"""

import argparse
import os
import sys
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/neodemos")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
STAGING_COLLECTION = "committee_transcripts_staging"


def create_staging_schema(drop_first: bool = False):
    """Create the staging schema and Qdrant collection."""
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        if drop_first:
            print("Dropping existing staging schema...")
            cur.execute("DROP SCHEMA IF EXISTS staging CASCADE;")
            conn.commit()

        # ── 1. Create schema ────────────────────────────────────────────
        cur.execute("CREATE SCHEMA IF NOT EXISTS staging;")
        print("+ Created schema: staging")

        # ── 2. Core tables (mirror production) ──────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS staging.meetings (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                start_date TIMESTAMP,
                committee TEXT,
                location TEXT,
                organization_id TEXT,
                category TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                -- Staging-specific columns
                transcript_source TEXT,
                quality_score FLOAT,
                review_status TEXT DEFAULT 'pending',
                promoted_at TIMESTAMP
            );
        """)
        print("+ Created staging.meetings")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS staging.agenda_items (
                id TEXT PRIMARY KEY,
                meeting_id TEXT NOT NULL REFERENCES staging.meetings(id) ON DELETE CASCADE,
                number TEXT,
                name TEXT NOT NULL
            );
        """)
        print("+ Created staging.agenda_items")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS staging.documents (
                id TEXT PRIMARY KEY,
                agenda_item_id TEXT REFERENCES staging.agenda_items(id) ON DELETE SET NULL,
                meeting_id TEXT REFERENCES staging.meetings(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                url TEXT,
                content TEXT,
                summary_json TEXT,
                category TEXT
            );
        """)
        print("+ Created staging.documents")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS staging.document_children (
                id SERIAL PRIMARY KEY,
                document_id TEXT REFERENCES staging.documents(id) ON DELETE CASCADE,
                content TEXT NOT NULL,
                chunk_index INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata JSONB
            );
        """)
        print("+ Created staging.document_children")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS staging.document_chunks (
                id SERIAL PRIMARY KEY,
                document_id TEXT REFERENCES staging.documents(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                title TEXT,
                content TEXT NOT NULL,
                chunk_type TEXT,
                table_json TEXT,
                tokens_estimated INTEGER,
                child_id INTEGER REFERENCES staging.document_children(id) ON DELETE CASCADE,
                embedding vector(3072),
                similarity_score_cache FLOAT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(document_id, chunk_index)
            );
        """)
        print("+ Created staging.document_chunks")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS staging.document_assignments (
                id SERIAL PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES staging.documents(id) ON DELETE CASCADE,
                meeting_id TEXT REFERENCES staging.meetings(id) ON DELETE SET NULL,
                agenda_item_id TEXT REFERENCES staging.agenda_items(id) ON DELETE SET NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(document_id, meeting_id, agenda_item_id)
            );
        """)
        print("+ Created staging.document_assignments")

        # ── 3. Pipeline tracking tables (staging-only) ──────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS staging.pipeline_runs (
                id TEXT PRIMARY KEY,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                status TEXT DEFAULT 'running',
                meetings_total INTEGER DEFAULT 0,
                meetings_completed INTEGER DEFAULT 0,
                meetings_failed INTEGER DEFAULT 0,
                config JSONB,
                error_log TEXT
            );
        """)
        print("+ Created staging.pipeline_runs")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS staging.pipeline_meeting_log (
                id SERIAL PRIMARY KEY,
                pipeline_run_id TEXT REFERENCES staging.pipeline_runs(id) ON DELETE CASCADE,
                meeting_id TEXT,
                status TEXT DEFAULT 'pending',
                phase TEXT,
                transcript_source TEXT,
                quality_metrics JSONB,
                error_message TEXT,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                UNIQUE(pipeline_run_id, meeting_id)
            );
        """)
        print("+ Created staging.pipeline_meeting_log")

        # ── 3b. Financial document tracking ─────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS staging.financial_documents (
                id TEXT PRIMARY KEY,
                doc_type TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                source_url TEXT,
                source TEXT DEFAULT 'watdoetdegemeente',
                pdf_path TEXT,
                page_count INTEGER,
                docling_tables_found INTEGER,
                docling_chunks_created INTEGER,
                review_status TEXT DEFAULT 'pending',
                quality_score FLOAT,
                promoted_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        print("+ Created staging.financial_documents")

        # ── 4. Indexes ──────────────────────────────────────────────────
        cur.execute("CREATE INDEX IF NOT EXISTS idx_stg_meetings_date ON staging.meetings(start_date);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_stg_meetings_committee ON staging.meetings(committee);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_stg_meetings_review ON staging.meetings(review_status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_stg_agenda_meeting ON staging.agenda_items(meeting_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_stg_docs_meeting ON staging.documents(meeting_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_stg_doc_children_doc ON staging.document_children(document_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_stg_doc_chunks_doc ON staging.document_chunks(document_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_stg_doc_chunks_child ON staging.document_chunks(child_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_stg_assign_doc ON staging.document_assignments(document_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_stg_assign_meeting ON staging.document_assignments(meeting_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_stg_pml_run ON staging.pipeline_meeting_log(pipeline_run_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_stg_pml_meeting ON staging.pipeline_meeting_log(meeting_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_stg_findocs_type ON staging.financial_documents(doc_type);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_stg_findocs_status ON staging.financial_documents(review_status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_stg_findocs_year ON staging.financial_documents(fiscal_year);")
        print("+ Created indexes")

        conn.commit()
        print("\nPostgreSQL staging schema ready.")

    except Exception as e:
        conn.rollback()
        print(f"Error creating staging schema: {e}")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()

    # ── 5. Qdrant staging collection ────────────────────────────────────
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        qdrant = QdrantClient(url=QDRANT_URL)
        collections = [c.name for c in qdrant.get_collections().collections]

        if STAGING_COLLECTION in collections:
            print(f"\nQdrant collection '{STAGING_COLLECTION}' already exists.")
        else:
            qdrant.create_collection(
                collection_name=STAGING_COLLECTION,
                vectors_config=VectorParams(size=4096, distance=Distance.COSINE),
            )
            print(f"\n+ Created Qdrant collection: {STAGING_COLLECTION} (4096-dim, Cosine)")

        print(f"\nStaging infrastructure ready.")
        print(f"  PostgreSQL: staging.* tables")
        print(f"  Qdrant:     {STAGING_COLLECTION}")

    except ImportError:
        print("\nqdrant-client not installed. Skipping Qdrant collection creation.")
        print("Run: pip install qdrant-client")
    except Exception as e:
        print(f"\nWarning: Could not create Qdrant collection: {e}")
        print("You may need to start Qdrant first: docker-compose up -d qdrant")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create staging schema for committee notulen pipeline")
    parser.add_argument("--drop", action="store_true", help="Drop and recreate staging schema")
    args = parser.parse_args()
    create_staging_schema(drop_first=args.drop)
