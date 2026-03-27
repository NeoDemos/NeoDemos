import requests

# Qdrant Optimization Script
# Purpose: Enable Binary Quantization and On-Disk Memmap to keep RAM usage < 2GB for the vector index.

BASE_URL = "http://localhost:6333"
COLLECTION_NAME = "notulen_chunks_local"

def optimize_collection():
    print(f"🛠️ Optimizing collection: {COLLECTION_NAME}")
    
    # 1. Update Collection to enable Binary Quantization (32x compression)
    # This keeps high accuracy while significantly reducing RAM/Disk overhead.
    optim_data = {
        "quantization_config": {
            "binary": {
                "always_ram": False # Keep quantized vectors on disk
            }
        },
        "hnsw_config": {
            "on_disk": True # Store the HNSW index on disk, not RAM
        }
    }
    
    resp = requests.patch(f"{BASE_URL}/collections/{COLLECTION_NAME}", json=optim_data)
    if resp.status_code == 200:
        print("✅ Binary Quantization and On-Disk index enabled.")
    else:
        print(f"❌ Failed to optimize: {resp.text}")

if __name__ == "__main__":
    optimize_collection()
