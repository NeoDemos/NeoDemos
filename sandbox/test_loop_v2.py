import asyncio
import sys
import os
from dotenv import load_dotenv

load_dotenv()
sys.path.append('.')

from services.ai_service import AIService
from services.storage import StorageService

async def test():
    storage = StorageService()
    ai = AIService()
    try:
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, ai.perform_agentic_debate_prep, "Renovatie Boijmans", storage)
        print("SUCCESS")
        print(res.keys())
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(test())
