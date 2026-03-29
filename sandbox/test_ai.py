import asyncio
from services.ai_service import AIService
from services.storage_service import StorageService

async def main():
    storage = StorageService()
    ai = AIService()
    query = "Wat was de reactie van de raad op het Deloitte rapport over Feyenoord City en hoe verhouden die standpunten zich tot hun eigen verkiezingsprogrammas?"
    print("Running deep search...")
    result = await ai.perform_deep_search(query, storage)
    print("\n\n--- FINAL ANSWER ---")
    print(result['answer'])
    print("\n\n--- SOURCES ---")
    for s in result['sources']:
        print(s['name'])

if __name__ == "__main__":
    asyncio.run(main())
