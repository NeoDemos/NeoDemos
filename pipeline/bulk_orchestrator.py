import json
import re
import logging
import os
import sys
import time
import requests
import gc
from pathlib import Path
import psycopg2
import subprocess
from typing import List, Dict, Any, Optional

# Constants
ORIENTATION_API_URL = "https://api.openraadsinformatie.nl/v1/elastic/_search"

# Add project root to sys.path
sys.path.insert(0, os.getcwd())

from pipeline.main_pipeline import run_pipeline
from pipeline.exceptions import (
    MeetingCancelledError,
    MeetingUnavailableError,
    WebcastCodeExtractionError,
    VideoUnavailableError,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bulk_pipeline.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("bulk_orchestrator")

STATE_FILE = Path("pipeline_state.json")
DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"

class BulkOrchestrator:
    def __init__(self, state_file: str = "pipeline_state.json", reset_state: bool = False):
        self.state_file = Path(state_file)
        self.state = self._load_state(reset_state)

    def _log_system_memory(self):
        try:
            # Simple vm_stat parser for macOS
            vm = subprocess.check_output(["vm_stat"]).decode()
            pages_free = int(re.search(r"Pages free:\s+(\d+)", vm).group(1))
            pages_active = int(re.search(r"Pages active:\s+(\d+)", vm).group(1))
            # page size is usually 4096 on Intel, 16384 on Apple Silicon
            page_size = int(subprocess.check_output(["pagesize"]).decode())
            free_gb = (pages_free * page_size) / (1024**3)
            active_gb = (pages_active * page_size) / (1024**3)
            logger.info(f"Memory Status: Free: {free_gb:.2f}GB | Active: {active_gb:.2f}GB")
        except Exception as e:
            logger.debug(f"Could not log memory: {e}")

    def _load_state(self, reset: bool) -> Dict:
        if self.state_file.exists() and not reset:
            with open(self.state_file, "r") as f:
                state = json.load(f)
            # Reset any meetings stuck in_progress (likely from a crash)
            for m_id, info in state.get("meetings", {}).items():
                if info.get("status") == "in_progress":
                    logger.info(f"Resetting {m_id} from in_progress to pending")
                    info["status"] = "failed"
                    info["last_error"] = "Interrupted by crash (recovered)"
            return state
        return {"meetings": {}}

    def _resolve_guid(self, numeric_id: str) -> Optional[str]:
        """Fetch the public portal GUID for a numeric ORI meeting ID."""
        try:
            payload = {
                "query": { "match": { "_id": numeric_id } }
            }
            resp = requests.post(ORIENTATION_API_URL, json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                hits = data.get("hits", {}).get("hits", [])
                if hits:
                    source = hits[0].get("_source", {})
                    # The GUID is usually in was_generated_by.original_identifier
                    was_gen = source.get("was_generated_by", {})
                    if isinstance(was_gen, dict):
                        return was_gen.get("original_identifier")
            return None
        except Exception as e:
            logger.error(f"Error resolving GUID for {numeric_id}: {e}")
            return None

    def _save_state(self):
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2)

    def discover_meetings(self, year: int = 2026):
        """Find committee meetings for a specific year and add to state if not present."""
        logger.info(f"Discovering {year} committee meetings...")
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        
        start_date = f"{year}-01-01"
        end_date = f"{year + 1}-01-01"
        
        query = """
            SELECT id, name, start_date 
            FROM meetings 
            WHERE start_date >= %s 
              AND start_date < %s
              AND name ILIKE '%%Commissie%%' 
            ORDER BY start_date ASC
        """
        cur.execute(query, (start_date, end_date))
        rows = cur.fetchall()
        
        new_count = 0
        for row in rows:
            m_id = str(row[0])
            name = row[1]
            date = str(row[2])
            
            if m_id not in self.state["meetings"]:
                url = f"https://rotterdamraad.bestuurlijkeinformatie.nl/Agenda/Index/{m_id}"
                self.state["meetings"][m_id] = {
                    "name": name,
                    "date": date,
                    "url": url,
                    "status": "pending",
                    "attempts": 0,
                    "last_error": None
                }
                new_count += 1
        
        cur.close()
        conn.close()
        self._save_state()
        logger.info(f"Discovery complete. Added {new_count} new meetings. Total: {len(self.state['meetings'])}")

    def run_sequential(self, limit: int = None, split_video: bool = False, download_only: bool = False):
        """Process pending meetings one by one."""
        count = 0
        for m_id, info in self.state["meetings"].items():
            if info["status"] == "completed":
                continue
            
            if limit and count >= limit:
                logger.info(f"Reached limit of {limit} meetings. Stopping.")
                break
            
            logger.info(f"\n🚀 Processing [{count+1}]: {info['name']} ({info['date']})")
            
            # --- GUID RESOLUTION FOR NUMERIC IDs ---
            current_url = info['url']
            if m_id.isdigit():
                logger.info(f"Numeric ID {m_id} detected. Resolving GUID via ORI API...")
                guid = self._resolve_guid(m_id)
                if guid:
                    current_url = f"https://rotterdamraad.bestuurlijkeinformatie.nl/Agenda/Index/{guid}"
                    logger.info(f"✅ Resolved GUID: {guid} | New URL: {current_url}")
                    # Update state with the working URL for future attempts
                    info['url'] = current_url
                else:
                    logger.warning(f"⚠️ Could not resolve GUID for numeric ID {m_id}. Proceeding with original URL (likely to fail).")
            
            logger.info(f"🔗 URL: {current_url}")
            
            info["status"] = "in_progress"
            info["attempts"] += 1
            self._save_state()
            self._log_system_memory()
            
            try:
                try:
                    # Run the pipeline with heuristic mode (Zero-API)
                    run_pipeline(
                        ibabs_url=current_url,
                        heuristic=True,
                        split_video=split_video,
                        numeric_id=m_id,
                        download_only=download_only
                    )

                    info["status"] = "completed"
                    info["last_error"] = None
                    logger.info(f"✅ Successfully processed {m_id}")
                    count += 1
                except (MeetingCancelledError, MeetingUnavailableError, VideoUnavailableError) as skip_err:
                    # These are expected conditions — not pipeline bugs. Skip without retry.
                    info["status"] = "skipped"
                    info["last_error"] = str(skip_err)
                    logger.info(f"⏭️  Skipped {m_id}: {skip_err}")
                except WebcastCodeExtractionError as wce:
                    # No Royalcast player on this iBabs page — retry won't help.
                    info["status"] = "skipped"
                    info["last_error"] = str(wce)
                    logger.warning(f"⏭️  No webcast code found for {m_id} — marking as skipped: {wce}")
                except Exception as e:
                    logger.warning(f"⚠️  Heuristic pipeline failed for {m_id}, retrying with use_whisper=True: {e}")
                    try:
                        # Retry with Whisper fallback explicitly enabled
                        run_pipeline(
                            ibabs_url=current_url,
                            heuristic=True,
                            use_whisper=True,
                            split_video=False,
                            numeric_id=m_id
                        )
                        info["status"] = "completed"
                        info["last_error"] = "Completed via Whisper fallback"
                        logger.info(f"✅ Successfully processed {m_id} (Whisper fallback)")
                        count += 1
                    except Exception as e2:
                        logger.warning(f"⚠️  Full pipeline failed even with Whisper, retrying with vtt_only=True: {e2}")
                        try:
                            # Final retry with vtt_only=True
                            run_pipeline(
                                ibabs_url=current_url,
                                heuristic=True,
                                vtt_only=True,
                                split_video=False,
                                numeric_id=m_id
                            )
                            info["status"] = "completed"
                            info["last_error"] = "Completed via VTT-only fallback"
                            logger.info(f"✅ Successfully processed {m_id} (VTT-only fallback)")
                            count += 1
                        except Exception as e3:
                            info["status"] = "failed"
                            info["last_error"] = str(e3)
                            logger.error(f"❌ Failed to process {m_id} even with VTT-only: {e3}")

            finally:
                # Always save state and clean up memory
                self._save_state()
                gc.collect()
                try:
                    import mlx.core as mx
                    mx.metal.clear_cache()
                except Exception:
                    pass
                time.sleep(5) # 5-second cooldown to avoid overheating/RAM spikes

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Limit number of meetings to process")
    parser.add_argument("--reset", action="store_true", help="Reset pipeline state")
    parser.add_argument("--split-video", action="store_true", help="Extract video clips")
    parser.add_argument("--year", type=int, default=2026, help="Year to process")
    parser.add_argument("--state-file", type=str, help="Custom state file path")
    parser.add_argument("--all-years", action="store_true", help="Process from --year down to 2018")
    args = parser.parse_args()

    if args.all_years:
        years_to_process = list(range(args.year, 2017, -1))
    else:
        years_to_process = [args.year]

    for y in years_to_process:
        logger.info(f"\n{'='*60}\n=== Starting processing for year {y} ===\n{'='*60}")
        # Default 2026 to 'pipeline_state.json', other years to 'pipeline_state_YYYY.json'
        state_file = args.state_file or (f"pipeline_state_{y}.json" if y != 2026 else "pipeline_state.json")
    
        orchestrator = BulkOrchestrator(state_file=state_file, reset_state=args.reset)
        
        # We only discover if the state is empty (brand new year)
        if not orchestrator.state["meetings"]:
            orchestrator.discover_meetings(year=y)
            
        orchestrator.run_sequential(limit=args.limit, split_video=args.split_video)
        
        # If a limit was hit in this year and we haven't finished the year, we should stop
        # to respect the user limit across the whole run.
        if args.limit:
            completed_count = sum(1 for m in orchestrator.state["meetings"].values() if m["status"] == "completed")
            pending_count = sum(1 for m in orchestrator.state["meetings"].values() if m["status"] != "completed")
            if pending_count > 0:
                logger.info(f"Limit of {args.limit} reached during year {y}. Stopping.")
                break
