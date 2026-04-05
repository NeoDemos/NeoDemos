from qdrant_client import QdrantClient
from qdrant_client.http import models
import os

# Qdrant Optimization Script (Local Mode)
# Purpose: Enable On-Disk HNSW to reduce RAM usage for cloud hosting.

COLLECTION_NAME = "notulen_chunks"
STORAGE_PATH = "./data/qdrant_storage"

def optimize_collection():
    if not os.path.exists(STORAGE_PATH):
        print(f"❌ Storage path not found: {STORAGE_PATH}")
        return

    print(f"🛠️ Connecting to local storage: {STORAGE_PATH}")
    client = QdrantClient(path=STORAGE_PATH)
    
    print(f"🛠️ Optimizing collection: {COLLECTION_NAME}")
    
    try:
        # 1. Update HNSW to be on-disk (Saves 3-5GB RAM)
        # 2. Maintain Int8 Quantization (Already active, but confirming)
        client.update_collection(
            collection_name=COLLECTION_NAME,
            hnsw_config=models.HnswConfigDiff(
                on_disk=True
            ),
            quantization_config=models.ScalarQuantization(
                scalar=models.ScalarQuantizationConfig(
                    type=models.ScalarType.INT8,
                    quantile=0.99,
                    always_ram=True
                )
            )
        )
        print("✅ Optimization parameters updated. Qdrant will now optimize segments in the background.")
        
        # 3. Check current stats
        info = client.get_collection(COLLECTION_NAME)
        print(f"📊 Current status: {info.status}")
        print(f"📊 Vectors: {info.points_count}")
        
    except Exception as e:
        print(f"❌ Error during optimization: {e}")

if __name__ == "__main__":
    optimize_collection()
