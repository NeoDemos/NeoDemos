import json
import logging
import os
import sys
from pathlib import Path

# Add the current directory to sys.path so pipeline module can be found
sys.path.append(os.getcwd())

from pipeline.main_pipeline import run_pipeline

# Configure logging to see output
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

RECOVERY_MAP = {
    "gemeenterotterdam_20260113_1": "066d8f58-bd69-42cf-bc2e-9d2919424c5e",
    "gemeenterotterdam_20260114_3": "76f75354-944e-4863-8893-6a380486c67b",
    "gemeenterotterdam_20260121_2": "160868f7-90d2-4309-8488-8cd49ce6d4fd",
}

STATE_FILE = Path("pipeline_state.json")

def update_state(meeting_id, status, error=None):
    if not STATE_FILE.exists():
        return
    with open(STATE_FILE, "r") as f:
        state = json.load(f)
    
    if meeting_id in state["meetings"]:
        state["meetings"][meeting_id]["status"] = status
        state["meetings"][meeting_id]["attempts"] = state["meetings"][meeting_id].get("attempts", 0) + 1
        state["meetings"][meeting_id]["last_error"] = error
        state["meetings"][meeting_id]["last_run"] = "2026-03-13T14:20:00"
        
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def run_recovery():
    for code, m_id in RECOVERY_MAP.items():
        logger.info(f"\n🚀 Retrying {code} (Meeting ID: {m_id})")
        try:
            # We bypass the ibabs_url by passing the specific webcast_code
            run_pipeline(
                webcast_code=code,
                vtt_only=True,
                no_ingest=False,
                heuristic=True
            )
            update_state(m_id, "completed")
            logger.info(f"✅ Successfully recovered {code}")
        except Exception as e:
            logger.error(f"❌ Failed to recover {code}: {str(e)}")
            update_state(m_id, "failed", error=str(e))

if __name__ == "__main__":
    run_recovery()
