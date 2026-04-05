from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
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

# ROBUST MANUAL KEY FALLBACK
if not os.getenv("GEMINI_API_KEY"):
    try:
        with open(".env", "r") as f:
            for line in f:
                if "GEMINI_API_KEY" in line:
                    os.environ["GEMINI_API_KEY"] = line.split("=")[1].strip()
                    print("DEBUG: Manually loaded GEMINI_API_KEY from .env")
    except:
        pass

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from services.open_raad import OpenRaadService
from services.storage import StorageService
from services.ai_service import AIService, GEMINI_AVAILABLE
from services.refresh_service import RefreshService
from services.party_position_profile_service import PartyPositionProfileService
from services.policy_lens_evaluation_service import PolicyLensEvaluationService

raad_service = OpenRaadService()
storage = StorageService()
# Initialize services
try:
    ai_service = AIService()
    print(f"DEBUG: AIService init complete. GEMINI_AVAILABLE={GEMINI_AVAILABLE}, use_llm={ai_service.use_llm}, has_key={bool(ai_service.api_key)}")
except Exception as e:
    print(f"DEBUG: AIService init FAILED: {e}")
    ai_service = None
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
async def search_page(request: Request):
    return templates.TemplateResponse("search.html", {"request": request, "title": "Zoeken"})

@app.get("/overview")
async def overview_page(request: Request):
    meetings = storage.get_meetings(limit=500)
    return templates.TemplateResponse("overview.html", {
        "request": request, 
        "title": "Overzicht",
        "meetings": meetings
    })

@app.get("/api/search")
async def api_search(q: str, deep: bool = False, mode: str = None, party: str = "GroenLinks-PvdA", date_from: str = None, date_to: str = None):
    """
    Search for agenda items and documents.
    If deep=True, also performs AI Deep Research.
    """
    if not q or len(q) < 3:
        return {"results": [], "ai_answer": None}

    # Temporal extraction: detect date references in natural language
    # Only runs if no explicit date filters were provided by the frontend
    if not date_from and not date_to:
        try:
            temporal = await ai_service.extract_temporal_filters(q)
            if temporal.get("date_from") or temporal.get("date_to"):
                q = temporal["query"]
                date_from = temporal.get("date_from")
                date_to = temporal.get("date_to")
                logger.info(f"Temporal extraction: query='{q}' date_from={date_from} date_to={date_to}")
        except Exception:
            pass  # Non-critical — proceed with original query

    # 1. Traditional Keyword Search
    from psycopg2.extras import RealDictCursor
    def get_keyword_results():
        with storage._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                try:
                    # Optimized search using CTE and deferred headline generation
                    score_threshold=0.15  # Extremely relaxed recall for long queries
                    search_query = """
                    WITH matches AS (
                        -- 1. Trigram match on agenda item name (Highest priority/rank)
                        SELECT ai.id as agenda_item_id, 1.0 as rank, NULL::int as chunk_id
                        FROM agenda_items ai
                        WHERE ai.name ILIKE %s
                        
                        UNION ALL
                        
                        -- 2. FTS match on document chunks
                        SELECT d.agenda_item_id, ts_rank_cd(dc.text_search, websearch_to_tsquery('dutch', %s), 32) as rank, dc.id as chunk_id
                        FROM document_chunks dc
                        JOIN documents d ON dc.document_id = d.id
                        WHERE dc.text_search @@ websearch_to_tsquery('dutch', %s)
                    ),
                    ranked_items AS (
                        -- Pick the best match (highest rank) for each unique agenda item
                        SELECT DISTINCT ON (agenda_item_id) 
                            agenda_item_id, 
                            rank, 
                            chunk_id
                        FROM matches
                        ORDER BY agenda_item_id, rank DESC
                    ),
                    top_items AS (
                        -- Take the top 50 results overall
                        SELECT * FROM ranked_items
                        ORDER BY rank DESC
                        LIMIT 50
                    )
                    SELECT 
                        t.agenda_item_id,
                        ai.meeting_id,
                        ai.name,
                        m.start_date as meeting_date,
                        m.committee,
                        CASE 
                            WHEN t.chunk_id IS NOT NULL THEN 
                                ts_headline('dutch', dc.content, websearch_to_tsquery('dutch', %s), 
                                            'StartSel=<b>, StopSel=</b>, MaxWords=35, MinWords=15, ShortWord=3, HighlightAll=FALSE, MaxFragments=1, FragmentDelimiter=" ... "')
                            ELSE (
                                SELECT LEFT(content, 200) 
                                FROM documents 
                                WHERE agenda_item_id = t.agenda_item_id 
                                LIMIT 1
                            )
                        END as snippet,
                        t.rank
                    FROM top_items t
                    JOIN agenda_items ai ON t.agenda_item_id = ai.id
                    JOIN meetings m ON ai.meeting_id = m.id
                    LEFT JOIN document_chunks dc ON t.chunk_id = dc.id
                    ORDER BY t.rank DESC, m.start_date DESC;
                    """
                    # Fallback to a broader search if literal pattern fails
                    search_pattern = f"%{q.split()[0]}%" # Match at least the first word
                    cur.execute(search_query, (search_pattern, q, q, q))
                    results = cur.fetchall()
                    
                    # If still empty, try even broader
                    if not results and len(q.split()) > 1:
                        broad_q = " & ".join(q.split()[:3]) # First 3 words ANDed
                        cur.execute(search_query, (f"%{q.split()[0]}%", broad_q, broad_q, broad_q))
                        results = cur.fetchall()
                    
                    return results
                except Exception as e:
                    logger.error(f"Keyword search failed for query '{q}': {e}")
                    return []

    # Determine if we need to run AI research
    run_ai = deep or mode == 'debate'
    
    if run_ai:
        # Run both in parallel if AI is requested
        if mode == 'debate':
            ai_task = ai_service.perform_agentic_debate_prep(q, storage, party=party, date_from=date_from, date_to=date_to)
        else:
            ai_task = ai_service.perform_deep_search(q, storage, date_from=date_from, date_to=date_to)
            
        loop = asyncio.get_running_loop()
        keyword_rows = await loop.run_in_executor(None, get_keyword_results)
        ai_result = await ai_task
    else:
        # Standard fast search
        loop = asyncio.get_running_loop()
        keyword_rows = await loop.run_in_executor(None, get_keyword_results)
        ai_result = {"answer": None, "sources": []}
            
    results = []
    for r in keyword_rows:
        results.append({
            "agenda_item_id": r['agenda_item_id'],
            "meeting_id": r['meeting_id'],
            "name": r['name'],
            "meeting_date": str(r['meeting_date']).split('T')[0].split(' ')[0] if r['meeting_date'] else 'Onbekend',
            "committee": r['committee'],
            "snippet": r['snippet'] + "..." if r['snippet'] else ""
        })
        
    return {
        "results": results, 
        "ai_answer": ai_result.get("answer"),
        "sources": ai_result.get("sources", [])
    }

@app.get("/calendar")
async def read_calendar(request: Request, year: int = None, month: int = None):
    available_years = storage.get_meeting_years()
    # Default to most recent year if no year specified
    if year is None and available_years:
        year = available_years[0]
    meetings = storage.get_meetings(limit=2000, year=year)
    return templates.TemplateResponse("calendar.html", {
        "request": request,
        "title": "Raadskalender",
        "meetings": meetings,
        "selected_year": year,
        "selected_month": month,
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
        
        def mark_substantive(items):
            for item in items:
                item["is_substantive"] = storage.is_substantive_item(item, combined_name)
                if item.get("sub_items"):
                    mark_substantive(item["sub_items"])
        
        mark_substantive(meeting["agenda"])
    
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
    # Fetch agenda item and recursively collect documents from its sub-items
    item_data = storage.get_agenda_item_with_sub_documents(agenda_item_id)
    if not item_data:
        return {"error": f"Agendapunt {agenda_item_id} niet gevonden"}
    
    item_name = item_data['name']
    doc_rows = item_data['documents']

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

    seen_doc_ids = set()
    documents = []
    for row in doc_rows:
        unique_key = (row.get('name'), row.get('url'))
        if unique_key not in seen_doc_ids:
            documents.append({
                "name": row['name'],
                "content": row['content'],
                "url": row.get('url', '#')
            })
            seen_doc_ids.add(unique_key)
    
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
            "analysis": analysis.get('gedetailleerde_analyse', ''),
            "positieve_punten": analysis.get('positieve_punten', []),
            "kritische_punten": analysis.get('kritische_punten', []),
            "vraag_suggesties": analysis.get('vraag_suggesties', []),
            "tegenvoorstel_suggesties": analysis.get('tegenvoorstel_suggesties', []),
            "recommendations": result.get('aanbevelingen', []),
            "source": "party_lens_analysis"
        }

@app.get("/api/analyse/unified/{agenda_item_id}")
async def api_analyse_unified(agenda_item_id: str, party: str = "GroenLinks-PvdA"):
    """
    Streams the agentic meeting analysis as Server-Sent Events (SSE).
    Events: {type: "status"|"chunk"|"done"|"error", ...}
    """
    logger.info(f"UNIFIED ANALYSIS ENDPOINT CALLED for agenda item {agenda_item_id}, party {party}")
    item_data = storage.get_agenda_item_with_sub_documents(agenda_item_id)
    if not item_data:
        async def _not_found():
            yield f"data: {json.dumps({'type': 'error', 'message': f'Agendapunt {agenda_item_id} niet gevonden'})}\n\n"
        return StreamingResponse(_not_found(), media_type="text/event-stream")

    item_name = item_data['name']
    doc_rows = item_data['documents']

    seen_doc_ids = set()
    documents = []
    for row in doc_rows:
        unique_key = (row.get('name'), row.get('url'))
        if unique_key not in seen_doc_ids:
            documents.append({
                "name": row['name'],
                "content": row['content'],
                "url": row.get('url', '#')
            })
            seen_doc_ids.add(unique_key)

    async def event_stream():
        try:
            async for event in ai_service.stream_agentic_meeting_analysis(item_name, documents, party=party):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"Streaming unified analysis error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@app.get("/api/analyse/speech/{agenda_item_id}")
async def api_analyse_speech(agenda_item_id: str, party: str = "GroenLinks-PvdA"):
    """
    Generate a draft speech (bijdrage) for a councillor based on the agenda item analysis.
    """
    logger.info(f"SPEECH GENERATION ENDPOINT CALLED for agenda item {agenda_item_id}, party {party}")
    from psycopg2.extras import RealDictCursor
    with storage._get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT name, meeting_id FROM agenda_items WHERE id = %s", (agenda_item_id,))
            item_row = cur.fetchone()
            if not item_row: return {"error": "Agendapunt niet gevonden"}
            item_name = item_row['name']
            
            cur.execute("SELECT name, content FROM documents WHERE agenda_item_id = %s AND content IS NOT NULL", (agenda_item_id,))
            doc_rows = cur.fetchall()

    if not doc_rows: return {"error": "Geen documenten beschikbaar"}
    
    documents = [{"name": r['name'], "content": r['content']} for r in doc_rows]
    lens_service = _get_party_lens_service(party)
    
    # We use the existing analysis but request a speech format
    speech = await ai_service.generate_speech_draft(item_name, documents, lens_service.party_vision)
    return {"speech": speech}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
