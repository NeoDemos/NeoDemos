import os
from qdrant_client import QdrantClient
from qdrant_client.http import models

QDRANT_PATH = "./data/qdrant_storage"
COLLECTION_NAME = "notulen_chunks_local"

def patch_collection():
    print(f"Connecting to Qdrant at {QDRANT_PATH}...")
    client = QdrantClient(path=QDRANT_PATH)
    
    print(f"Patching collection {COLLECTION_NAME} to on_disk=True...")
    try:
        # Step 1: Check existing status
        info = client.get_collection(COLLECTION_NAME)
        print(f"Current vectors config: {info.config.params.vectors}")
        
        # Step 2: Update collection
        # In Qdrant 1.4+, we can update the vectors_config to on_disk=True
        client.update_collection(
            collection_name=COLLECTION_NAME,
            vectors_config={
                "": models.VectorParamsDiff(on_disk=True)
            }
        )
        
        # Step 3: Verify
        new_info = client.get_collection(COLLECTION_NAME)
        print(f"Updated vectors config: {new_info.config.params.vectors}")
        print("✅ Success! The collection is now configured for on-disk storage.")
        
    except Exception as e:
        print(f"❌ Failed to patch collection: {e}")
        print("Note: If 'on_disk' is not in the schema for VectorParamsDiff, it might not be supported in this SDK version.")

if __name__ == "__main__":
    patch_collection()
