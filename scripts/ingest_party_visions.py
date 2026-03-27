import psycopg2
from psycopg2.extras import execute_values
import os
import sys
import io
import re
from pypdf import PdfReader

# DB Connection
DB_URL = 'postgresql://dennistak:@localhost/neodemos'
PROGRAMS_DIR = '/Users/dennistak/Documents/Final Frontier/Rotterdam Stemwijzer AI/Programmas/'

PARTY_MAP = {
    "JOU_Lijst_Verkoelen": "50PLUS (JOU)",
    "RotterdamBIJ1": "BIJ1",
    "Volt": "Volt",
    "glpvda": "GroenLinks-PvdA",
    "CDA": "CDA",
    "D66": "D66",
    "Leefbaar": "Leefbaar Rotterdam",
    "ChristenUnie": "ChristenUnie",
    "sp_rotterdam": "SP",
    "DENK": "DENK",
    "vvd": "VVD",
    "GR26-beleidsversie": "Partij voor de Dieren" # Guessing or will verify
}

def get_party(filename):
    for key, val in PARTY_MAP.items():
        if key.lower() in filename.lower():
            return val
    return "Onbekend"

def ingest_visions():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    files = [f for f in os.listdir(PROGRAMS_DIR) if f.endswith('.pdf')]
    print(f"Found {len(files)} party programmes.")

    for filename in files:
        file_path = os.path.join(PROGRAMS_DIR, filename)
        party = get_party(filename)
        print(f"Processing {filename} for {party}...")

        # 1. Extract Text
        try:
            reader = PdfReader(file_path)
            full_text = ""
            for page in reader.pages:
                full_text += (page.extract_text() or "") + "\n"
            full_text = full_text.strip()
        except Exception as e:
            print(f"Error reading {filename}: {e}")
            continue

        if not full_text:
            print(f"No text extracted from {filename}. Skipping.")
            continue

        # 2. Insert into documents table
        doc_id = f"vision_{party.replace(' ', '_')}_2026"
        cur.execute("""
            INSERT INTO documents (id, name, content, category, url)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                content = EXCLUDED.content,
                category = EXCLUDED.category
        """, (doc_id, f"Verkiezingsprogramma {party} 2026-2030", full_text, 'vision', filename))
        
        # 3. Create 8K Children (Parents)
        # Clear existing children for this doc_id to avoid duplicates on re-run
        cur.execute("DELETE FROM document_children WHERE document_id = %s", (doc_id,))
        
        chunk_size = 8000
        overlap = 500
        child_ids = []
        for i, start in enumerate(range(0, len(full_text), chunk_size - overlap)):
            content = full_text[start:start + chunk_size]
            cur.execute(
                "INSERT INTO document_children (document_id, chunk_index, content) VALUES (%s, %s, %s) RETURNING id",
                (doc_id, i, content)
            )
            child_ids.append((cur.fetchone()[0], content))

        # 4. Create 1K Grandchildren (Vectors Placeholder - Vectorization happens in a separate pipeline)
        # However, for RAG to work, we at least need the document_chunks entries
        cur.execute("DELETE FROM document_chunks WHERE document_id = %s", (doc_id,))
        
        grand_size = 1000
        grand_overlap = 100
        for i, start in enumerate(range(0, len(full_text), grand_size - grand_overlap)):
            gc_content = full_text[start:start + grand_size]
            # Link to the 8K child that contains this
            child_id = None
            for c_id, c_content in child_ids:
                if gc_content.strip() in c_content:
                    child_id = c_id
                    break
            
            cur.execute("""
                INSERT INTO document_chunks (document_id, chunk_index, content, child_id, title, chunk_type)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (doc_id, i, gc_content, child_id, f"Visie {party} deel {i+1}", 'vision'))

        print(f"✓ Ingested {party}: {len(full_text)} chars, {len(child_ids)} parents.")
        conn.commit()

    cur.close()
    conn.close()
    print("Ingestion complete.")

if __name__ == "__main__":
    ingest_visions()
