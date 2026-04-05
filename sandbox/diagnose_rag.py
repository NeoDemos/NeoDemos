import asyncio
import os
from services.rag_service import RAGService
from services.storage import StorageService
from services.local_ai_service import LocalAIService

async def diagnose_search(query):
    print(f"--- DIAGNOSING QUERY: {query} ---")
    rag = RAGService()
    storage = StorageService()
    local_ai = LocalAIService()
    
    # 1. Generate Embedding
    emb = local_ai.generate_embedding(query)
    print(f"Embedding generated (dim={len(emb) if emb else 'None'})")
    
    # 2. Parallel Retrieval
    print("\nExecuting Parallel Context Retrieval...")
    # Matches the distribution used in perform_deep_search
    chunks = await rag.retrieve_parallel_context(
        query_text=query,
        query_embedding=emb,
        distribution={"financial": 10, "debate": 10, "fact": 10, "vision": 5}
    )
    
    print(f"Found {len(chunks)} unique chunks.")
    
    # 3. Analyze Stream Distribution
    streams = {}
    for c in chunks:
        streams[c.stream_type] = streams.get(c.stream_type, 0) + 1
    print(f"Stream Distribution: {streams}")
    
    # 4. Detail top hits
    print("\nTop 15 Chunks Found:")
    for i, c in enumerate(chunks[:15]):
        print(f"[{i+1}] Score: {c.similarity_score:.4f} | Stream: {c.stream_type} | Source: {c.title}")
        print(f"    Snippet: {c.content[:200]}...")
        # Check if it has table data
        if "[FINANCIAL] TABEL DATA" in c.content:
            print("    !!! CONTAINS TABLE DATA !!!")
        print("-" * 40)

    # 5. Test Hierarchical Expansion
    print("\nTesting Hierarchical Expansion...")
    context, sources, raw = rag.expand_to_hierarchical_context(chunks, storage)
    print(f"Final Context Length: {len(context)} characters")
    print(f"Identified Sources: {[s['name'] for s in sources]}")
    
    # Check if 'budget' or 'zorg' or 'miljoen' appears in context
    keywords = ['budget', 'miljoen', 'euro', 'kosten', 'zorg', 'bestuursopdracht']
    for kw in keywords:
        count = context.lower().count(kw)
        print(f"Keyword '{kw}' count: {count}")

if __name__ == "__main__":
    asyncio.run(diagnose_search("Bestuursopdracht zorg"))
