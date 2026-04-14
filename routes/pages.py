"""Public + protected HTML page routes.

Landing page, static subpages (over/technologie/methodologie), overview,
calendar, settings, meeting detail, and MCP installer.
"""
import os
import logging

from fastapi import APIRouter, Request, Depends
from markupsafe import Markup

from services.auth_dependencies import auth_service, require_login, get_current_user

from app_state import (
    templates,
    storage,
    raad_service,
    LANDING_HEADLINE,
    get_demo_entry,
    page_service,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── MCP Installer (public) ──

_MCP_SERVER_URL = os.getenv("MCP_BASE_URL", "https://mcp.neodemos.nl") + "/mcp"


@router.get("/mcp-installer")
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


# ── Public subpages ──

@router.get("/over")
async def over_page(request: Request):
    """About page: founder story, democratic ambition, user quotes."""
    user = await get_current_user(request)
    page = page_service.get_published("over")
    return templates.TemplateResponse(name="over.html", request=request, context={
        "title": "Over",
        "user": user,
        "page_html": page["html_content"] if page else None,
    })


@router.get("/technologie")
async def technologie_page(request: Request):
    """Technology page: EU sovereignty, local AI, security, model independence."""
    user = await get_current_user(request)
    page = page_service.get_published("technologie")
    return templates.TemplateResponse(name="technologie.html", request=request, context={
        "title": "Technologie",
        "user": user,
        "page_html": page["html_content"] if page else None,
    })


@router.get("/methodologie")
async def methodologie_page(request: Request):
    """Methodology page: how it works, data sources, eval scores, limitations."""
    user = await get_current_user(request)
    page = page_service.get_published("methodologie")
    return templates.TemplateResponse(name="methodologie.html", request=request, context={
        "title": "Methodologie",
        "user": user,
        "page_html": page["html_content"] if page else None,
    })


# ── Protected page routes ──

@router.get("/")
async def search_page(request: Request):
    """Public landing page: 4-element design (demo, search, credibility, trust).

    Anonymous users see the pre-rendered demo answer and credibility lines.
    Logged-in users see a clean search-only experience.
    """
    user = await get_current_user(request)
    demo = get_demo_entry()
    page = page_service.get_published("home")
    return templates.TemplateResponse(name="search.html", request=request, context={
        "title": "Zoeken",
        "user": user,
        "landing_headline": Markup(LANDING_HEADLINE.replace("\n", "<br>")),
        "demo_question": demo["question"] if demo else "Heeft het college haar beloftes waargemaakt?",
        "demo_answer_markdown": demo["answer"] if demo else None,
        "demo_sources": demo["sources"] if demo else [],
        "demo_label": demo["label"] if demo else None,
        "demo_cached_at": demo["cached_at"] if demo else None,
        "page_html": page["html_content"] if page else None,
    })


@router.get("/overview")
async def overview_page(request: Request, user: dict = Depends(require_login)):
    meetings = storage.get_meetings(limit=500)
    return templates.TemplateResponse(name="overview.html", request=request, context={
        "title": "Overzicht",
        "meetings": meetings,
        "user": user,
    })


@router.get("/calendar")
async def read_calendar(
    request: Request,
    year: int = None,
    month: int = None,
    committee: str = None,
    search: str = None,
    view: str = "list",
    show_empty: bool = False,
):
    """Public calendar page -- no login required.

    By default (show_empty=False), future meetings without documents are hidden.
    The template exposes a toggle so users can reveal them.
    """
    user = await get_current_user(request)
    available_years = storage.get_meeting_years()
    # Filter out numeric-only committee IDs before rendering chips
    committees = [c for c in storage.get_distinct_committees() if c and not c.strip().isdigit()]
    if year is None and available_years:
        year = available_years[0]
    meetings = storage.get_meetings_filtered(
        year=year, committee=committee, search=search, limit=2000,
    )
    return templates.TemplateResponse(name="calendar.html", request=request, context={
        "title": "Raadskalender",
        "meetings": meetings,
        "selected_year": year,
        "selected_month": month,
        "available_years": available_years,
        "committees": committees,
        "selected_committee": committee or "",
        "search_query": search or "",
        "view": view,
        "show_empty": show_empty,
        "user": user,
    })


@router.get("/settings")
async def read_settings(request: Request, user: dict = Depends(require_login)):
    tokens = auth_service.list_user_tokens(user["id"])
    return templates.TemplateResponse(name="settings.html", request=request, context={
        "title": "Instellingen",
        "user": user,
        "tokens": tokens,
    })


@router.get("/meeting/{meeting_id}")
async def read_meeting(request: Request, meeting_id: str, user: dict = Depends(require_login)):
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
