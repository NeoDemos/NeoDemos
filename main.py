from fastapi import FastAPI, Request, Depends, Form
from fastapi.responses import StreamingResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import os
import re
from dotenv import load_dotenv
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import asyncio
import json
from datetime import datetime, date
from markupsafe import Markup
from contextlib import asynccontextmanager

# Load environment variables from .env file
load_dotenv()

# dotenv handles .env loading above — no manual fallback needed

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from neodemos_version import VERSION_LABEL, DISPLAY_NAME, STAGE
from services.db_pool import close_pool
from services.open_raad import OpenRaadService
from services.storage import StorageService
from services.ai_service import AIService, GEMINI_AVAILABLE
from services.refresh_service import RefreshService
from services.party_position_profile_service import PartyPositionProfileService
from services.policy_lens_evaluation_service import PolicyLensEvaluationService
from services.auth_dependencies import (
    auth_service, require_login, require_admin, get_api_user,
    get_current_user, sign_session_id, unsign_session_id,
    generate_csrf_token,
)

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
    logger.info("Scheduled refresh triggered (15-min interval)")
    try:
        asyncio.run(refresh_service.check_and_download())
    except Exception as e:
        logger.error(f"Scheduled refresh failed: {e}")

scheduler.add_job(
    scheduled_refresh,
    IntervalTrigger(minutes=15),
    id='interval_refresh',
    name='Check for new documents every 15 minutes',
    max_instances=1,
    coalesce=True,
    misfire_grace_time=300,
)

def cleanup_sessions():
    """Purge expired sessions from the database."""
    try:
        count = auth_service.cleanup_expired_sessions()
        if count:
            logger.info(f"Cleaned up {count} expired sessions")
    except Exception as e:
        logger.error(f"Session cleanup failed: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app lifecycle - start scheduler on startup, shutdown on exit"""
    # Seed admin user from environment
    admin_email = os.getenv("ADMIN_EMAIL")
    admin_password = os.getenv("ADMIN_PASSWORD")
    if admin_email and admin_password:
        try:
            if not auth_service.get_user_by_email(admin_email):
                auth_service.create_user(
                    admin_email, admin_password, display_name="Admin", role="admin"
                )
                logger.info(f"Admin user seeded: {admin_email}")
        except Exception as e:
            logger.error(f"Failed to seed admin user: {e}")

    # Startup
    try:
        scheduler.start()
        logger.info("Refresh scheduler started (15-min interval)")
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")

    # Schedule daily session cleanup
    scheduler.add_job(
        cleanup_sessions,
        IntervalTrigger(hours=24),
        id='session_cleanup',
        name='Purge expired sessions daily',
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    yield

    # Shutdown
    try:
        scheduler.shutdown()
        logger.info("Scheduler shutdown complete")
    except Exception as e:
        logger.error(f"Failed to shutdown scheduler: {e}")

    # Close the shared database connection pool
    try:
        close_pool()
    except Exception as e:
        logger.error(f"Failed to close DB pool: {e}")

# Create FastAPI app with lifespan manager
app = FastAPI(title="NeoDemos", lifespan=lifespan)

# Security headers middleware
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-NeoDemos-Version"] = VERSION_LABEL
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "frame-ancestors 'none'"
        )
        return response

app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://neodemos.nl", "https://www.neodemos.nl"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

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

# Version context available in all templates (footer badge, cache busting)
templates.env.globals["version_label"] = VERSION_LABEL
templates.env.globals["display_name"] = DISPLAY_NAME
templates.env.globals["stage"] = STAGE


# ── Auth routes (public) ──

@app.get("/login")
async def login_page(request: Request, success: str = None):
    # Generate a temporary CSRF token (not session-bound for login page)
    csrf = generate_csrf_token("login-form")
    return templates.TemplateResponse(name="login.html", request=request, context={
        "title": "Inloggen", "csrf_token": csrf, "error": None, "success": success,
    })

@app.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(""),
):
    # Rate limiting
    if not auth_service.check_login_rate_limit(email):
        csrf = generate_csrf_token("login-form")
        return templates.TemplateResponse(name="login.html", request=request, context={
            "title": "Inloggen", "csrf_token": csrf, "email": email,
            "error": "Te veel mislukte pogingen. Probeer het over 15 minuten opnieuw.",
        })

    user = auth_service.authenticate(email, password)
    if not user:
        auth_service.record_failed_login(email)
        csrf = generate_csrf_token("login-form")
        return templates.TemplateResponse(name="login.html", request=request, context={
            "title": "Inloggen", "csrf_token": csrf, "email": email,
            "error": "Ongeldige inloggegevens.",
        })

    if not user["is_active"]:
        csrf = generate_csrf_token("login-form")
        return templates.TemplateResponse(name="login.html", request=request, context={
            "title": "Inloggen", "csrf_token": csrf, "email": email,
            "error": "Uw account is gedeactiveerd. Neem contact op met de beheerder.",
        })

    # Create session
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent", "")[:200]
    session_id = auth_service.create_session(user["id"], ip_address=ip, user_agent=ua)
    signed = sign_session_id(session_id)

    response = RedirectResponse(url="/", status_code=303)
    is_prod = os.getenv("ENVIRONMENT", "").lower() == "production"
    response.set_cookie(
        "session_id", signed,
        httponly=True, secure=is_prod, samesite="lax", path="/",
        max_age=int(os.getenv("SESSION_MAX_AGE", "604800")),
    )
    return response

@app.get("/register")
async def register_page(request: Request):
    csrf = generate_csrf_token("register-form")
    return templates.TemplateResponse(name="register.html", request=request, context={
        "title": "Registreren", "csrf_token": csrf, "error": None,
    })

@app.post("/register")
async def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    display_name: str = Form(""),
    csrf_token: str = Form(""),
):
    ip = request.client.host if request.client else "unknown"

    # Rate limiting
    if not auth_service.check_register_rate_limit(ip):
        csrf = generate_csrf_token("register-form")
        return templates.TemplateResponse(name="register.html", request=request, context={
            "title": "Registreren", "csrf_token": csrf, "email": email,
            "display_name": display_name,
            "error": "Te veel registraties. Probeer het later opnieuw.",
        })

    # Validation
    email_clean = email.lower().strip()
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email_clean):
        csrf = generate_csrf_token("register-form")
        return templates.TemplateResponse(name="register.html", request=request, context={
            "title": "Registreren", "csrf_token": csrf, "email": email,
            "display_name": display_name,
            "error": "Ongeldig e-mailadres.",
        })

    if len(password) < 8:
        csrf = generate_csrf_token("register-form")
        return templates.TemplateResponse(name="register.html", request=request, context={
            "title": "Registreren", "csrf_token": csrf, "email": email,
            "display_name": display_name,
            "error": "Wachtwoord moet minimaal 8 tekens bevatten.",
        })

    if password != password_confirm:
        csrf = generate_csrf_token("register-form")
        return templates.TemplateResponse(name="register.html", request=request, context={
            "title": "Registreren", "csrf_token": csrf, "email": email,
            "display_name": display_name,
            "error": "Wachtwoorden komen niet overeen.",
        })

    # Check if email already exists
    if auth_service.get_user_by_email(email_clean):
        csrf = generate_csrf_token("register-form")
        return templates.TemplateResponse(name="register.html", request=request, context={
            "title": "Registreren", "csrf_token": csrf, "email": email,
            "display_name": display_name,
            "error": "Dit e-mailadres is al in gebruik.",
        })

    auth_service.create_user(email_clean, password, display_name=display_name.strip() or None)
    auth_service.record_registration(ip)

    return RedirectResponse(url="/login?success=Account+aangemaakt.+U+kunt+nu+inloggen.", status_code=303)

@app.post("/logout")
async def logout(request: Request):
    signed = request.cookies.get("session_id")
    if signed:
        session_id = unsign_session_id(signed)
        if session_id:
            auth_service.delete_session(session_id)
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_id", path="/")
    return response


# ── OAuth 2.1 Consent Flow (for MCP clients: Claude, ChatGPT, Perplexity) ──

from services.mcp_oauth_provider import NeodemosOAuthProvider
from urllib.parse import urlencode

_oauth_provider = NeodemosOAuthProvider()


@app.get("/oauth/authorize")
async def oauth_authorize_page(
    request: Request,
    client_id: str = "",
    redirect_uri: str = "",
    state: str = "",
    scope: str = "mcp search",
    code_challenge: str = "",
):
    """
    OAuth 2.1 consent page. The MCP SDK redirects here during the authorization flow.
    If the user is already logged in (session cookie), show consent screen.
    If not, show login form that returns here after authentication.
    """
    # Check if user is already logged in via session cookie
    user = await get_current_user(request)

    if not user:
        # Store OAuth params in query string, redirect to login with return URL
        oauth_params = urlencode({
            "client_id": client_id, "redirect_uri": redirect_uri,
            "state": state, "scope": scope, "code_challenge": code_challenge,
        })
        return RedirectResponse(
            url=f"/oauth/login?{oauth_params}",
            status_code=303,
        )

    # User is logged in — check mcp_access
    if not user.get("mcp_access"):
        return templates.TemplateResponse(name="oauth_error.html", request=request, context={
            "title": "Geen MCP-toegang",
            "error": "Uw account heeft geen MCP-toegang. Neem contact op met de beheerder.",
        })

    # Show consent page
    client = await _oauth_provider.get_client(client_id)
    csrf = generate_csrf_token("oauth-consent")
    return templates.TemplateResponse(name="oauth_consent.html", request=request, context={
        "title": "Toestemming verlenen",
        "user": user,
        "client_name": client.client_name if client else client_id,
        "scope": scope,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "csrf_token": csrf,
    })


@app.get("/oauth/login")
async def oauth_login_page(
    request: Request,
    client_id: str = "", redirect_uri: str = "", state: str = "",
    scope: str = "mcp search", code_challenge: str = "",
):
    """Login page during OAuth flow — after login, returns to consent page."""
    csrf = generate_csrf_token("oauth-login")
    return templates.TemplateResponse(name="oauth_login.html", request=request, context={
        "title": "Inloggen — MCP Autorisatie",
        "csrf_token": csrf, "error": None,
        "client_id": client_id, "redirect_uri": redirect_uri,
        "state": state, "scope": scope, "code_challenge": code_challenge,
    })


@app.post("/oauth/login")
async def oauth_login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    client_id: str = Form(""),
    redirect_uri: str = Form(""),
    state: str = Form(""),
    scope: str = Form("mcp search"),
    code_challenge: str = Form(""),
    csrf_token: str = Form(""),
):
    """Process login during OAuth flow, then redirect to consent."""
    user = auth_service.authenticate(email, password)
    if not user:
        csrf = generate_csrf_token("oauth-login")
        return templates.TemplateResponse(name="oauth_login.html", request=request, context={
            "title": "Inloggen — MCP Autorisatie",
            "csrf_token": csrf, "email": email, "error": "Ongeldige inloggegevens.",
            "client_id": client_id, "redirect_uri": redirect_uri,
            "state": state, "scope": scope, "code_challenge": code_challenge,
        })

    if not user["is_active"]:
        csrf = generate_csrf_token("oauth-login")
        return templates.TemplateResponse(name="oauth_login.html", request=request, context={
            "title": "Inloggen — MCP Autorisatie",
            "csrf_token": csrf, "email": email,
            "error": "Uw account is gedeactiveerd.",
            "client_id": client_id, "redirect_uri": redirect_uri,
            "state": state, "scope": scope, "code_challenge": code_challenge,
        })

    # Create session cookie so the consent page knows the user
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent", "")[:200]
    session_id = auth_service.create_session(user["id"], ip_address=ip, user_agent=ua)
    signed = sign_session_id(session_id)

    # Redirect back to consent page with OAuth params
    oauth_params = urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri,
        "state": state, "scope": scope, "code_challenge": code_challenge,
    })
    response = RedirectResponse(url=f"/oauth/authorize?{oauth_params}", status_code=303)
    is_prod = os.getenv("ENVIRONMENT", "").lower() == "production"
    response.set_cookie(
        "session_id", signed,
        httponly=True, secure=is_prod, samesite="lax", path="/",
        max_age=int(os.getenv("SESSION_MAX_AGE", "604800")),
    )
    return response


@app.post("/oauth/consent")
async def oauth_consent_submit(
    request: Request,
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(""),
    scope: str = Form("mcp search"),
    code_challenge: str = Form(...),
    csrf_token: str = Form(""),
):
    """User approved consent — generate auth code and redirect back to client."""
    user = await get_current_user(request)
    if not user:
        return RedirectResponse(url="/oauth/login", status_code=303)

    if not user.get("mcp_access"):
        return templates.TemplateResponse(name="oauth_error.html", request=request, context={
            "title": "Geen MCP-toegang", "error": "Geen MCP-toegang voor dit account.",
        })

    # Generate authorization code
    code = await _oauth_provider.create_authorization_code(
        client_id=client_id,
        user_id=user["id"],
        redirect_uri=redirect_uri,
        scope=scope,
        code_challenge=code_challenge,
    )

    # Redirect back to the MCP client with the auth code
    params = {"code": code}
    if state:
        params["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(url=f"{redirect_uri}{separator}{urlencode(params)}", status_code=303)


# ── MCP Installer (public) ──

_MCP_SERVER_URL = os.getenv("MCP_BASE_URL", "https://mcp.neodemos.nl") + "/mcp"


@app.get("/mcp-installer")
async def mcp_installer_page(request: Request):
    user = await get_current_user(request)
    token_data = None
    existing_tokens = []
    if user:
        existing_tokens = auth_service.list_user_tokens(user["id"])
    return templates.TemplateResponse(name="mcp_installer.html", request=request, context={
        "title": "MCP Installer",
        "user": user,
        "mcp_url": _MCP_SERVER_URL,
        "token_data": token_data,
        "existing_tokens": existing_tokens,
    })


@app.post("/api/mcp/generate-token")
async def generate_mcp_token(request: Request):
    """Auto-generate an API token for MCP use. Returns JSON with token and config snippets."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Niet ingelogd"}, status_code=401)
    if not user.get("is_active"):
        return JSONResponse({"error": "Account is gedeactiveerd"}, status_code=403)

    # Grant mcp_access if not already set
    if not user.get("mcp_access"):
        auth_service.update_user(user["id"], mcp_access=True)

    # Generate token
    token_data = auth_service.create_api_token(
        user_id=user["id"],
        name="MCP Auto-Install",
        scopes="search,mcp",
    )

    return JSONResponse({
        "token": token_data["token"],
        "mcp_url": _MCP_SERVER_URL,
        "claude_desktop_config": {
            "mcpServers": {
                "neodemos": {
                    "url": _MCP_SERVER_URL,
                    "headers": {
                        "Authorization": f"Bearer {token_data['token']}"
                    }
                }
            }
        },
        "claude_code_command": (
            f"claude mcp add neodemos"
            f" --transport streamable-http"
            f" --url {_MCP_SERVER_URL}"
            f' --header "Authorization: Bearer {token_data["token"]}"'
        ),
    })


# ── Protected page routes ──

@app.get("/")
async def search_page(request: Request):
    """Public landing page: search box + about + MCP section.

    Anonymous users see the explainer + MCP teaser below the search.
    Logged-in users see a clean search-only experience.
    """
    user = await get_current_user(request)
    return templates.TemplateResponse(name="search.html", request=request, context={
        "title": "Zoeken", "user": user,
    })

@app.get("/overview")
async def overview_page(request: Request, user: dict = Depends(require_login)):
    if isinstance(user, RedirectResponse):
        return user
    meetings = storage.get_meetings(limit=500)
    return templates.TemplateResponse(name="overview.html", request=request, context={
        "title": "Overzicht",
        "meetings": meetings,
        "user": user,
    })

@app.get("/api/search")
async def api_search(request: Request, q: str, deep: bool = False, mode: str = None, party: str = "GroenLinks-PvdA", date_from: str = None, date_to: str = None, user: dict = Depends(get_api_user)):
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
async def read_calendar(request: Request, year: int = None, month: int = None, user: dict = Depends(require_login)):
    if isinstance(user, RedirectResponse):
        return user
    available_years = storage.get_meeting_years()
    if year is None and available_years:
        year = available_years[0]
    meetings = storage.get_meetings(limit=2000, year=year)
    return templates.TemplateResponse(name="calendar.html", request=request, context={
        "title": "Raadskalender",
        "meetings": meetings,
        "selected_year": year,
        "selected_month": month,
        "available_years": available_years,
        "user": user,
    })

@app.get("/settings")
async def read_settings(request: Request, user: dict = Depends(require_login)):
    if isinstance(user, RedirectResponse):
        return user
    tokens = auth_service.list_user_tokens(user["id"])
    return templates.TemplateResponse(name="settings.html", request=request, context={
        "title": "Instellingen",
        "user": user,
        "tokens": tokens,
    })

@app.get("/meeting/{meeting_id}")
async def read_meeting(request: Request, meeting_id: str, user: dict = Depends(require_login)):
    if isinstance(user, RedirectResponse):
        return user
    # Pre-2018 meetings have rotterdam_raad_ prefix — serve from Postgres only
    if meeting_id.startswith("rotterdam_raad_"):
        meeting = storage.get_meeting_details(meeting_id)
        if not meeting:
            return templates.TemplateResponse(name="meeting.html", request=request, context={
                "title": "Vergadering niet gevonden",
                "meeting": {"name": "Vergadering niet gevonden", "agenda": []},
                "user": user,
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
    
    return templates.TemplateResponse(name="meeting.html", request=request, context={
        "title": meeting.get("name", "Meeting"),
        "meeting": meeting,
        "user": user,
    })

@app.get("/api/summarize/{doc_id}")
async def api_summarize(request: Request, doc_id: str, user: dict = Depends(get_api_user)):
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
async def api_analyse_agenda_item(request: Request, agenda_item_id: str, user: dict = Depends(get_api_user)):
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
    
    if cache_key not in _party_lens_cache:
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
async def api_analyse_party_lens(request: Request, agenda_item_id: str, party: str = "GroenLinks-PvdA", user: dict = Depends(get_api_user)):
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

    return {
        "error": f"Partijlensanalyse leverde geen resultaat op voor agendapunt: {item_name}",
        "alignment_score": None
    }

@app.get("/api/analyse/unified/{agenda_item_id}")
async def api_analyse_unified(request: Request, agenda_item_id: str, party: str = "GroenLinks-PvdA", user: dict = Depends(get_api_user)):
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
async def api_analyse_speech(request: Request, agenda_item_id: str, party: str = "GroenLinks-PvdA", user: dict = Depends(get_api_user)):
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

# ── Admin routes ──

@app.get("/admin")
async def admin_page(request: Request, user: dict = Depends(require_admin)):
    if isinstance(user, RedirectResponse):
        return user
    users = auth_service.list_users()
    tokens = auth_service.list_all_tokens()
    return templates.TemplateResponse(name="admin.html", request=request, context={
        "title": "Beheer", "user": user, "users": users, "tokens": tokens,
    })

@app.get("/admin/api/users")
async def admin_list_users(request: Request, user: dict = Depends(require_admin)):
    if isinstance(user, RedirectResponse):
        return user
    users = auth_service.list_users()
    return JSONResponse(users)

@app.post("/admin/api/users/{user_id}/update")
async def admin_update_user(request: Request, user_id: int, user: dict = Depends(require_admin)):
    if isinstance(user, RedirectResponse):
        return user
    body = await request.json()
    allowed = {"is_active", "mcp_access", "db_access_level", "role"}
    updates = {k: v for k, v in body.items() if k in allowed}
    # Prevent admin from demoting themselves
    if user_id == user["id"] and updates.get("role") == "user":
        return JSONResponse({"error": "Kan eigen admin-rol niet verwijderen"}, status_code=400)
    updated = auth_service.update_user(user_id, **updates)
    if not updated:
        return JSONResponse({"error": "Gebruiker niet gevonden"}, status_code=404)
    logger.info(f"Admin {user['email']} updated user {user_id}: {updates}")
    return JSONResponse(updated)

@app.post("/admin/api/users/{user_id}/toggle-active")
async def admin_toggle_active(request: Request, user_id: int, user: dict = Depends(require_admin)):
    if isinstance(user, RedirectResponse):
        return user
    if user_id == user["id"]:
        return JSONResponse({"error": "Kan eigen account niet deactiveren"}, status_code=400)
    target = auth_service.get_user_by_id(user_id)
    if not target:
        return JSONResponse({"error": "Gebruiker niet gevonden"}, status_code=404)
    updated = auth_service.update_user(user_id, is_active=not target["is_active"])
    logger.info(f"Admin {user['email']} toggled active for user {user_id}: {updated['is_active']}")
    return JSONResponse(updated)

@app.delete("/admin/api/tokens/{token_id}")
async def admin_revoke_token(request: Request, token_id: int, user: dict = Depends(require_admin)):
    if isinstance(user, RedirectResponse):
        return user
    revoked = auth_service.revoke_api_token(token_id)
    if not revoked:
        return JSONResponse({"error": "Token niet gevonden"}, status_code=404)
    logger.info(f"Admin {user['email']} revoked token {token_id}")
    return JSONResponse({"ok": True})


# ── Token management (for logged-in users) ──

@app.get("/api/tokens")
async def list_tokens(request: Request, user: dict = Depends(require_login)):
    if isinstance(user, RedirectResponse):
        return user
    tokens = auth_service.list_user_tokens(user["id"])
    return JSONResponse(tokens)

@app.post("/api/tokens")
async def create_token(request: Request, user: dict = Depends(require_login)):
    if isinstance(user, RedirectResponse):
        return user
    body = await request.json()
    name = body.get("name", "Default")[:50]
    scopes = body.get("scopes", "search,mcp")
    token_data = auth_service.create_api_token(user["id"], name=name, scopes=scopes)
    return JSONResponse(token_data)

@app.delete("/api/tokens/{token_id}")
async def revoke_token(request: Request, token_id: int, user: dict = Depends(require_login)):
    if isinstance(user, RedirectResponse):
        return user
    # Verify the token belongs to this user
    tokens = auth_service.list_user_tokens(user["id"])
    if not any(t["id"] == token_id for t in tokens):
        return JSONResponse({"error": "Token niet gevonden"}, status_code=404)
    auth_service.revoke_api_token(token_id)
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
