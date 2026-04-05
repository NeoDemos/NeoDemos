import time
import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.http import models

# Initialize client in local disk mode
client = QdrantClient(path="./data/qdrant_storage")
COLLECTION_NAME = "notulen_chunks"

def run_benchmark():
    print(f"--- Benchmarking Collection: {COLLECTION_NAME} ---")
    
    # 1. Get collection info
    collection_info = client.get_collection(COLLECTION_NAME)
    vector_count = collection_info.points_count
    print(f"Total Vectors: {vector_count}")
    print(f"Quantization Config: {collection_info.config.quantization_config}")
    
    # 2. Pull a random sample for testing
    limit = 1000
    points, _ = client.scroll(
        collection_name=COLLECTION_NAME,
        limit=limit,
        with_vectors=True
    )
    
    if not points:
        print("No points found in collection.")
        return

    # Extract vectors for searching
    test_vectors = [p.vector for p in points[:10]] # Use first 10 for search queries
    
    # 3. Benchmark Search with Quantization (Current Setup)
    # By default, search uses the quantized index
    q_latencies = []
    q_results = []
    
    print("\nRunning Quantized Searches...")
    for vec in test_vectors:
        start = time.perf_counter()
        res = client.search(
            collection_name=COLLECTION_NAME,
            query_vector=vec,
            limit=10
        )
        q_latencies.append(time.perf_counter() - start)
        q_results.append([r.id for r in res])
    
    avg_q_latency = np.mean(q_latencies) * 1000
    print(f"Average Quantized Latency: {avg_q_latency:.2f} ms")
    
    # 4. Benchmark Search with EXACT (Brute-Force 32-bit)
    # search_params with exact=True ignores the quantized index
    exact_latencies = []
    exact_results = []
    
    print("Running Exact (32-bit) Searches...")
    for vec in test_vectors:
        start = time.perf_counter()
        res = client.search(
            collection_name=COLLECTION_NAME,
            query_vector=vec,
            limit=10,
            search_params=models.SearchParams(exact=True)
        )
        exact_latencies.append(time.perf_counter() - start)
        exact_results.append([r.id for r in res])
        
    avg_exact_latency = np.mean(exact_latencies) * 1000
    print(f"Average Exact Latency: {avg_exact_latency:.2f} ms")
    
    # 5. Calculate Recall@10
    recalls = []
    for q_res, ex_res in zip(q_results, exact_results):
        intersection = set(q_res).intersection(set(ex_res))
        recalls.append(len(intersection) / 10.0)
    
    avg_recall = np.mean(recalls) * 100
    print(f"\n--- Results ---")
    print(f"Average Recall@10: {avg_recall:.1f}%")
    print(f"Speedup Factor: {avg_exact_latency / avg_q_latency:.1f}x")

if __name__ == "__main__":
    run_benchmark()
