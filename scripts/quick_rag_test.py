import os
import sys
import psycopg2
from google import genai

# Setup
DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
EMBED_MODEL = "models/gemini-embedding-001" # Corrected to available 3072-dim model

# Load the API key
from dotenv import load_dotenv
load_dotenv("/Users/dennistak/Documents/Final Frontier/NeoDemos/.env")
API_KEY = os.environ.get("GEMINI_API_KEY", "")
client = genai.Client(api_key=API_KEY)

def query_rag(question: str, top_k: int = 5):
    print(f"\n\n\033[1m🔎 Searching database for:\033[0m '{question}'\n")
    
    # 1. Embed the question using Gemini API (Zero local dependencies)
    print("Generating embedding via Gemini API...")
    try:
        res = client.models.embed_content(
            model=EMBED_MODEL,
            contents=question
        )
        q_emb = res.embeddings[0].values
    except Exception as e:
        print(f"❌ Embedding failed: {e}")
        return

    # 2. Connect to DB and search Qdrant
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    from qdrant_client import QdrantClient
    qdrant = QdrantClient(url="http://localhost:6333")
    
    print("Querying Qdrant vector database...")
    try:
        # Use newer query_points API which is present in this environment
        res = qdrant.query_points(
            collection_name="notulen_chunks",
            query=q_emb,
            limit=top_k
        )
        hits = res.points
    except Exception as e:
        print(f"❌ Qdrant search failed: {e}")
        return
    
    if not hits:
        print("⚠️ No results found in the vector database.")
        return

    context_parts = []
    for i, hit in enumerate(hits, 1):
        title = hit.payload.get('title', 'Unknown Section')
        text = hit.payload.get('text', '')
        doc_id = hit.payload.get('document_id', '')
        
        # Fetch doc name
        cur.execute("SELECT name FROM documents WHERE id = %s", (doc_id,))
        doc_row = cur.fetchone()
        doc_name = doc_row[0] if doc_row else f"Doc {doc_id}"
        
        print(f"\033[36m[{i}] \033[35m{doc_name}\033[0m")
        print(f"\033[33m{title}\033[0m (Score: {hit.score:.3f})")
        print(f"{text[:300]}...\n")
        
        context_parts.append(f"Document: {doc_name}\nSection: {title}\nText: {text}")
        
    print("-" * 50)
    
    context = "\n\n".join(context_parts)
    prompt = f"""You are an assistant answering questions based STRICTLY on the provided context from the Rotterdam city council.
    
Context:
{context}

Question: {question}

Answer in Dutch. If the context does not contain the answer, say so clearly."""

    print("🤖 Generating AI Response...")
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt
        )
        print(f"\n\033[32m{response.text}\033[0m\n")
    except Exception as e:
        print(f"❌ Generation failed: {e}")

    cur.close()
    conn.close()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        query_rag(query)
    else:
        print("Usage: python quick_rag_test.py 'Uw vraag hier'")
