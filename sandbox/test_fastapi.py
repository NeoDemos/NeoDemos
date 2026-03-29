import asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import json
import os
from dotenv import load_dotenv

load_dotenv()
from services.ai_service import AIService
from services.storage import StorageService
import sys
import threading

async def test():
    storage = StorageService()
    ai = AIService()
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(None, ai.perform_agentic_debate_prep, "Renovatie Boijmans", storage)
    
    # Simulate fastapi json packaging
    encoded = json.dumps({"results": [], "ai_answer": res.get("answer"), "sources": res.get("sources")})
    print("ENCODED SUCCESS:", len(encoded))

asyncio.run(test())
