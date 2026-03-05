from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os
from dotenv import load_dotenv
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import asyncio
import json
from datetime import datetime, date
from markupsafe import Markup
from contextlib import asynccontextmanager

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from services.open_raad import OpenRaadService
from services.storage import StorageService
from services.ai_service import AIService
from services.refresh_service import RefreshService
from services.party_position_profile_service import PartyPositionProfileService
from services.policy_lens_evaluation_service import PolicyLensEvaluationService

raad_service = OpenRaadService()
storage = StorageService()
ai_service = AIService()
refresh_service = RefreshService(storage, raad_service, ai_service)

# Initialize party profile and lens evaluation services
# These are lazy-loaded since they're only used for party lens analysis
_party_profile_cache = {}
_party_lens_cache = {}

# Initialize scheduler for daily auto-refresh at 8 AM
scheduler = BackgroundScheduler()

def scheduled_refresh():
    """Wrapper for async refresh to run in scheduler"""
    try:
        asyncio.run(refresh_service.check_and_download())
    except Exception as e:
        logger.error(f"Scheduled refresh failed: {e}")

scheduler.add_job(
    scheduled_refresh,
    CronTrigger(hour=8, minute=0),
    id='daily_refresh',
    name='Daily data refresh at 8 AM'
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app lifecycle - start scheduler on startup, shutdown on exit"""
    # Startup
    try:
        scheduler.start()
        logger.info("Daily refresh scheduler started (8 AM UTC)")
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")
    
    yield
    
    # Shutdown
    try:
        scheduler.shutdown()
        logger.info("Scheduler shutdown complete")
    except Exception as e:
        logger.error(f"Failed to shutdown scheduler: {e}")

# Create FastAPI app with lifespan manager
app = FastAPI(title="NeoDemos", lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Setup templates
templates = Jinja2Templates(directory="templates")

# Add tojson filter for Jinja2 templates with datetime support
# Must use Markup() to prevent HTML entity escaping of JSON in <script> tags
def tojson_filter(obj):
    def default_handler(o):
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")
    
    # Return Markup to prevent Jinja2 auto-escaping of the JSON string
    return Markup(json.dumps(obj, default=default_handler))

templates.env.filters['tojson'] = tojson_filter

@app.get("/")
async def read_root(request: Request):
    meetings = storage.get_meetings(limit=500)
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "title": "Welcome to NeoDemos",
        "meetings": meetings
    })

@app.get("/calendar")
async def read_calendar(request: Request, year: int = None):
    available_years = storage.get_meeting_years()
    # Default to most recent year if no year specified
    if year is None and available_years:
        year = available_years[0]
    meetings = storage.get_meetings(limit=500, year=year)
    return templates.TemplateResponse("calendar.html", {
        "request": request,
        "title": "Raadskalender",
        "meetings": meetings,
        "selected_year": year,
        "available_years": available_years
    })

@app.get("/settings")
async def read_settings(request: Request):
    return templates.TemplateResponse("settings.html", {
        "request": request, 
        "title": "Instellingen"
    })

@app.get("/meeting/{meeting_id}")
async def read_meeting(request: Request, meeting_id: str):
    # Pre-2018 meetings have rotterdam_raad_ prefix — serve from Postgres only
    if meeting_id.startswith("rotterdam_raad_"):
        meeting = storage.get_meeting_details(meeting_id)
        if not meeting:
            return templates.TemplateResponse("meeting.html", {
                "request": request,
                "title": "Vergadering niet gevonden",
                "meeting": {"name": "Vergadering niet gevonden", "agenda": []}
            })
    else:
        meeting = storage.get_meeting_details(meeting_id)
        # Fall back to live API only if not found in DB
        if not meeting or not meeting.get("name"):
            meeting = await raad_service.get_meeting_details(meeting_id)
    
    # Mark which agenda items are substantive (should be analyzed)
    if meeting.get("agenda"):
        meeting_name = meeting.get("name", "")
        committee = meeting.get("committee", "")
        combined_name = f"{meeting_name} {committee}"
        for item in meeting["agenda"]:
            item["is_substantive"] = storage.is_substantive_item(item, combined_name)
    
    return templates.TemplateResponse("meeting.html", {
        "request": request, 
        "title": meeting.get("name", "Meeting"),
        "meeting": meeting
    })

@app.get("/api/summarize/{doc_id}")
async def api_summarize(doc_id: str):
    # Fetch doc from storage
    from psycopg2.extras import RealDictCursor
    with storage._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT content FROM documents WHERE id = %s", (doc_id,))
            row = cur.fetchone()
            content = row['content'] if row else "Document content not available."

    party_vision = "Wij staan voor een groen en autoluw Rotterdam met focus op sociale cohesie."
    summary = await ai_service.summarize_document(content, party_vision)
    return summary

@app.get("/api/analyse/agenda/{agenda_item_id}")
async def api_analyse_agenda_item(agenda_item_id: str):
    """
    Perform a general AI analysis of the agenda item.
    """
    logger.info(f"GENERAL AI ANALYSIS ENDPOINT CALLED for agenda item {agenda_item_id}")
    # Fetch agenda item name, meeting info, and all documents
    from psycopg2.extras import RealDictCursor
    with storage._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get agenda item and meeting
            cur.execute(
                "SELECT name, meeting_id FROM agenda_items WHERE id = %s",
                (agenda_item_id,)
            )
            item_row = cur.fetchone()
            if not item_row:
                return {
                    "error": f"Agendapunt {agenda_item_id} niet gevonden",
                }
            
            item_name = item_row['name']
            
            # Get all documents for this agenda item - NO TRUNCATION
            cur.execute(
                "SELECT name, content FROM documents WHERE agenda_item_id = %s AND content IS NOT NULL ORDER BY id",
                (agenda_item_id,)
            )
            doc_rows = cur.fetchall()

    if not doc_rows:
        return {
            "summary": f"Geen documenten beschikbaar voor agendapunt: {item_name}",
            "key_points": [],
            "conflicts": [],
            "decision_points": [],
            "controversial_topics": [],
            "questions": [],
            "party_alignment": None
        }

    # Prepare documents for analysis (FULL CONTENT, not truncated)
    documents = [
        {
            "name": row['name'],
            "content": row['content']
        }
        for row in doc_rows
    ]

    analysis = await ai_service.analyze_agenda_item(item_name, documents)
    return analysis

def _get_party_lens_service(party_name: str = "GroenLinks-PvdA"):
    """
    Get or create a PolicyLensEvaluationService for the specified party.
    Caches services to avoid reloading party profiles repeatedly.
    """
    cache_key = party_name.lower()
    
    # Temporarily disable cache and force reload for verification of fixes
    if True:
        import importlib
        import services.policy_lens_evaluation_service
        import services.llm_alignment_scorer
        importlib.reload(services.llm_alignment_scorer)
        importlib.reload(services.policy_lens_evaluation_service)
        from services.policy_lens_evaluation_service import PolicyLensEvaluationService
        
    if True or cache_key not in _party_lens_cache:
        service = PolicyLensEvaluationService(party_name=party_name)
        
        # Load party profile - searches in data/profiles/ directory
        profile_path = f"data/profiles/party_profile_{cache_key.replace(' ', '_').replace('-', '_')}.json"
        
        # Also try the standard naming convention
        if not os.path.exists(profile_path):
            profile_path = f"data/profiles/party_profile_{cache_key.replace('-', '_')}.json"
        
        if os.path.exists(profile_path):
            service.load_party_profile(profile_path)
        else:
            logger.warning(f"Party profile not found: {profile_path}")
        
        _party_lens_cache[cache_key] = service
    
    return _party_lens_cache[cache_key]

@app.get("/api/analyse/party-lens/{agenda_item_id}")
async def api_analyse_party_lens(agenda_item_id: str, party: str = "GroenLinks-PvdA"):
    """
    Analyze an agenda item through a party's perspective.
    
    Parameters:
    - party: The party to analyze through (default: GroenLinks-PvdA)
    """
    logger.info(f"PARTY LENS ENDPOINT CALLED: party={party}")
    from psycopg2.extras import RealDictCursor
    with storage._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get agenda item and meeting
            cur.execute(
                "SELECT name, meeting_id FROM agenda_items WHERE id = %s",
                (agenda_item_id,)
            )
            item_row = cur.fetchone()
            if not item_row:
                return {
                    "error": f"Agendapunt {agenda_item_id} niet gevonden",
                    "alignment_score": None
                }
            
            item_name = item_row['name']
            meeting_id = item_row['meeting_id']
            
            # Get meeting name
            cur.execute("SELECT name FROM meetings WHERE id = %s", (meeting_id,))
            meeting_row = cur.fetchone()
            meeting_name = meeting_row['name'] if meeting_row else "Onbekende vergadering"
            
            # Get all documents for this agenda item
            cur.execute(
                "SELECT name, content FROM documents WHERE agenda_item_id = %s AND content IS NOT NULL ORDER BY id",
                (agenda_item_id,)
            )
            doc_rows = cur.fetchall()

    if not doc_rows:
        return {
            "error": f"Geen documenten beschikbaar voor agendapunt: {item_name}",
            "alignment_score": None
        }

    documents = [
        {
            "name": row['name'],
            "content": row['content']
        }
        for row in doc_rows
    ]
    
    # Use party lens service for through-party-lens analysis
    lens_service = _get_party_lens_service(party)
    
    # Combine documents into agenda item text
    agenda_text = f"{meeting_name} - {item_name}\n\n"
    for doc in documents:
        agenda_text += f"Document: {doc['name']}\n{doc['content']}\n\n"
    
    # Evaluate through the party's lens
    result = lens_service.evaluate_agenda_item(agenda_text)
    
    if result.get('analyse'):
        analysis = result['analyse']
        return {
            "agenda_item_id": agenda_item_id,
            "agenda_item_name": item_name,
            "meeting_name": meeting_name,
            "party": party,
            "alignment_score": analysis.get('afstemming_score', 0.5),
            "interpretation": analysis.get('afstemming_interpretatie', 'Geen interpretatie beschikbaar'),
            "analysis": analysis.get('gedetailleerde_analyse', ''),
            "strong_points": analysis.get('sterke_punten', []),
            "critical_points": analysis.get('kritische_punten', []),
            "recommendations": result.get('aanbevelingen', []),
            "source": "party_lens_analysis"
        }

@app.get("/api/analyse/unified/{agenda_item_id}")
async def api_analyse_unified(agenda_item_id: str, party: str = "GroenLinks-PvdA"):
    """
    Perform both general AI analysis and party-specific lens analysis concurrently.
    """
    logger.info(f"UNIFIED ANALYSIS ENDPOINT CALLED for agenda item {agenda_item_id}, party {party}")
    from psycopg2.extras import RealDictCursor
    with storage._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get agenda item and meeting
            cur.execute(
                "SELECT name, meeting_id FROM agenda_items WHERE id = %s",
                (agenda_item_id,)
            )
            item_row = cur.fetchone()
            if not item_row:
                return {"error": f"Agendapunt {agenda_item_id} niet gevonden"}
            
            item_name = item_row['name']
            meeting_id = item_row['meeting_id']
            
            # Get meeting name
            cur.execute("SELECT name FROM meetings WHERE id = %s", (meeting_id,))
            meeting_row = cur.fetchone()
            meeting_name = meeting_row['name'] if meeting_row else "Onbekende vergadering"
            
            # Get all documents for this agenda item
            cur.execute(
                "SELECT name, content FROM documents WHERE agenda_item_id = %s AND content IS NOT NULL ORDER BY id",
                (agenda_item_id,)
            )
            doc_rows = cur.fetchall()

    if not doc_rows:
        return {
            "error": f"Geen documenten beschikbaar voor agendapunt: {item_name}"
        }

    documents = [
        {
            "name": row['name'],
            "content": row['content']
        }
        for row in doc_rows
    ]
    
    # Text for party lens
    agenda_text = f"{meeting_name} - {item_name}\n\n"
    for doc in documents:
        agenda_text += f"Document: {doc['name']}\n{doc['content']}\n\n"
        
    lens_service = _get_party_lens_service(party)
    
    # Run both concurrently
    # ai_service.analyze_agenda_item is async, lens_service.evaluate_agenda_item is synchronous
    loop = asyncio.get_running_loop()
    general_task = ai_service.analyze_agenda_item(item_name, documents)
    party_task = loop.run_in_executor(None, lens_service.evaluate_agenda_item, agenda_text)
    
    try:
        general_result, party_result = await asyncio.gather(general_task, party_task)
    except Exception as e:
        logger.error(f"Error during unified analysis: {e}")
        return {"error": f"Analyse mislukt: {str(e)}"}
        
    # Combine results
    combined_result = dict(general_result)
    
    if party_result and party_result.get('analyse'):
        analysis = party_result['analyse']
        combined_result['party_lens'] = {
            "party": party,
            "alignment_score": analysis.get('afstemming_score', 0.5),
            "interpretation": analysis.get('afstemming_interpretatie', 'Geen interpretatie beschikbaar'),
            "analysis": analysis.get('gedetailleerde_analyse', ''),
            "strong_points": analysis.get('sterke_punten', []),
            "critical_points": analysis.get('kritische_punten', []),
            "recommendations": party_result.get('aanbevelingen', []),
            "party_vision": analysis.get('partij_visie', 'Algemeen beleid')
        }
    else:
        combined_result['party_lens'] = None
        
    return combined_result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
