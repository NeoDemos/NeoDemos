#!/usr/bin/env python3
"""
RAG Infrastructure: Document Chunks Schema with Vector Embeddings

Creates tables for semantic chunking and vector-based retrieval:
- document_chunks: Semantic chunks extracted from full documents with embeddings
- chunk_questions: Hypothetical questions per chunk for improved RAG retrieval
"""

import psycopg2
import sys

def create_chunks_schema():
    """Create PostgreSQL schema for document chunks and RAG"""
    try:
        conn = psycopg2.connect(
            "postgresql://postgres:postgres@localhost:5432/neodemos"
        )
        cursor = conn.cursor()
        
        print("Creating RAG document chunks schema...")
        
        # Enable pgvector extension if not already enabled
        try:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            print("✓ pgvector extension enabled")
        except Exception as e:
            print(f"✓ pgvector extension (already enabled or unavailable)")
        
        conn.commit()
        
        # 1. Document chunks table - stores semantic chunks of documents with embeddings
        # Note: pgvector's indexed types (HNSW, IVFFlat) only support up to 2000 dimensions
        # For 3072-dim gemini-embedding-001, we store the vector but use sequential scan for similarity
        # For production with very large datasets, consider dimensionality reduction (PCA to 512-1024 dims)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS document_chunks (
                id SERIAL PRIMARY KEY,
                document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                title TEXT,
                content TEXT NOT NULL,
                embedding vector(3072),  -- gemini-embedding-001 produces 3072-dim vectors
                similarity_score_cache FLOAT,  -- Cache for last similarity search result
                tokens_estimated INTEGER,  -- Rough token count for context planning
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(document_id, chunk_index)
            );
            CREATE INDEX IF NOT EXISTS idx_document_chunks_doc ON document_chunks(document_id);
        """)
        print("✓ Created document_chunks table (no vector index due to 3072-dim limitation)")
        
        # 2. Chunk questions table - hypothetical questions for improved retrieval
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chunk_questions (
                id SERIAL PRIMARY KEY,
                chunk_id INTEGER REFERENCES document_chunks(id) ON DELETE CASCADE,
                question_text TEXT NOT NULL,
                embedding vector(3072),  -- Also embed questions for retrieval
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_chunk_questions_chunk ON chunk_questions(chunk_id);
        """)
        print("✓ Created chunk_questions table (no vector index due to 3072-dim limitation)")
        
        # 3. Chunk metadata table - store metadata about chunking process
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chunking_metadata (
                id SERIAL PRIMARY KEY,
                document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
                chunking_method TEXT NOT NULL,  -- e.g., 'gemini-semantic-chunking'
                model_used TEXT,  -- e.g., 'gemini-3-flash-preview'
                chunks_count INTEGER,
                total_tokens_estimated INTEGER,
                chunking_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(document_id)
            );
            CREATE INDEX IF NOT EXISTS idx_chunking_metadata_doc ON chunking_metadata(document_id);
        """)
        print("✓ Created chunking_metadata table")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print("\n✅ RAG schema created successfully!")
        print("   - document_chunks: Semantic chunks with vector embeddings (3072 dims, sequential scan)")
        print("   - chunk_questions: Hypothetical questions for retrieval (3072 dims, sequential scan)")
        print("   - chunking_metadata: Metadata about chunking process")
        print("\n   Note: pgvector index types limited to 2000 dims. For production, consider")
        print("         dimensionality reduction (PCA) or dedicated vector DB (Weaviate, Pinecone)")
        
    except psycopg2.Error as e:
        print(f"❌ Database error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    create_chunks_schema()
