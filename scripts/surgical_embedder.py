import os
import sys
import psycopg2
import numpy as np
from tqdm import tqdm
from qdrant_client import QdrantClient
from qdrant_client.http import models
from dotenv import load_dotenv
import hashlib

load_dotenv()

# Add project root to sys.path
sys.path.insert(0, os.getcwd())
from services.local_ai_service import LocalAIService

DB_URL = os.getenv("DB_URL") or "postgresql://postgres:postgres@localhost:5432/neodemos"
COLLECTION_NAME = "notulen_chunks"

def surgical_embed(doc_ids):
    """Chunk and embed a specific list of document IDs."""
    print(f"--- SURGICAL EMBEDDER (Targeting {len(doc_ids)} docs) ---")
    
    local_ai = LocalAIService(skip_llm=True)
    qdrant = QdrantClient(url="http://localhost:6333")
    
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    for doc_id in tqdm(doc_ids, desc="Embedding"):
        # 1. Fetch content
        cur.execute("SELECT name, content, url FROM documents WHERE id = %s", (doc_id,))
        row = cur.fetchone()
        if not row: continue
        name, content, url = row
        if not content: continue
        
        # 2. Simple Chunking
        words = content.split()
        chunks = [" ".join(words[i:i+500]) for i in range(0, len(words), 400)]
        
        points = []
        for i, chunk_text in enumerate(chunks):
            embedding = local_ai.generate_embedding(chunk_text)
            if embedding is None: continue
            
            # Handle both list and numpy array return types
            if hasattr(embedding, "tolist"):
                vec = embedding.tolist()
            else:
                vec = list(embedding)
            
            p_id_str = f"surgical_{doc_id}_{i}"
            hash_str = hashlib.md5(p_id_str.encode()).hexdigest()
            point_id = int(hash_str[:15], 16)
            
            points.append(models.PointStruct(
                id=point_id,
                vector=vec,
                payload={
                    "document_id": str(doc_id),
                    "title": name,
                    "content": chunk_text,
                    "chunk_type": "text",
                    "url": url
                }
            ))
        
        if points:
            qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
            # 3. Mark as processed in queue
            cur.execute("UPDATE chunking_queue SET status = 'completed' WHERE document_id = %s", (doc_id,))
            conn.commit()
            
    cur.close()
    conn.close()
    print("✅ Surgical Embedding Complete.")

if __name__ == "__main__":
    targets = ['6126099','6110375','6106672','6105526','6105489','6105490','6105491','6102057','6102060','6102883','6102939']
    surgical_embed(targets)
