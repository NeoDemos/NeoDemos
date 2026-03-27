import asyncio
import os
from dotenv import load_dotenv
load_dotenv()
from services.ai_service import AIService
from services.storage import StorageService

async def main():
    ai = AIService()
    storage = StorageService()
    query = "Wat was de reactie van de raad op het Deloitte rapport over Feyenoord City en hoe verhouden die standpunten zich tot hun eigen verkiezingsprogrammas?"
    print(f"Running query: {query}")
    try:
        result = await ai.perform_deep_search(query, storage)
        print("\n\n--- FINAL ANSWER ---")
        print(result['answer'])
        print("\n\n--- SOURCES ---")
        for i, s in enumerate(result.get('sources', [])):
            print(f"[{i+1}] {s['name']}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
