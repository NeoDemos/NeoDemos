import json
import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, os.getcwd())

from pipeline.ingestion import SmartIngestor

def recover():
    state_file = "pipeline_state_2018.json"
    if not os.path.exists(state_file):
        print(f"State file {state_file} not found.")
        return

    with open(state_file, "r") as f:
        state = json.load(f)

    ingestor = SmartIngestor()
    failed_count = 0
    recovered_count = 0

    for m_id, info in state["meetings"].items():
        if info.get("status") == "failed" and "TranscriptIngestor" in str(info.get("last_error", "")):
            failed_count += 1
            print(f"Recovering {m_id} ({info['name']})...")
            
            # Find the JSON file
            # Most meetings have a webcast_code in the state or we can guess from m_id
            # However, main_pipeline saves to output/transcripts/{webcast_code}.json
            # We need to find which JSON corresponds to this m_id.
            
            # Let's search output/transcripts/ for a JSON containing this m_id
            found_json = None
            transcripts_dir = Path("output/transcripts")
            if not transcripts_dir.exists():
                print("Output directory not found.")
                continue

            for json_file in transcripts_dir.glob("*.json"):
                try:
                    with open(json_file, "r") as jf:
                        data = json.load(jf)
                        if data.get("meeting_id") == m_id or data.get("webcast_code") == m_id:
                            found_json = data
                            break
                except:
                    continue
            
            if found_json:
                try:
                    ingestor.ingest_transcript(found_json, heuristic=True)
                    info["status"] = "completed"
                    info["last_error"] = "Recovered via catch-up script"
                    recovered_count += 1
                    print(f"✅ Successfully re-ingested {m_id}")
                except Exception as e:
                    print(f"❌ Failed to re-ingest {m_id}: {e}")
            else:
                print(f"⚠️ Could not find JSON transcript for {m_id}")

    # Save updated state
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)

    print(f"\nRecovery complete. {recovered_count}/{failed_count} meetings recovered.")

if __name__ == "__main__":
    recover()
