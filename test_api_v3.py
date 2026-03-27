import asyncio
import sys
sys.path.append('.')

from services.ai_service import AIService
from services.storage import StorageService

async def test():
    storage = StorageService()
    ai = AIService(storage)
    try:
        res = await ai.perform_agentic_debate_prep("Renovatie Boijmans", storage)
        print("SUCCESS")
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(test())
