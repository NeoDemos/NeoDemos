"""Public + protected HTML page routes.

Landing page, static subpages (over/technologie/methodologie), overview,
calendar, settings, meeting detail, and MCP installer.
"""
import json
import os
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from markupsafe import Markup

from services.auth_dependencies import auth_service, require_login, get_current_user
from services.avatars import AVATARS, is_valid_slug, user_avatar_url
from services.subscriptions import TIERS, VALID_SLUGS as VALID_TIERS, set_tier, tier_for

from app_state import (
    templates,
    storage,
    raad_service,
    LANDING_HEADLINE,
    get_demo_entry,
    page_service,
)
from routes.admin import RESERVED_SLUGS

logger = logging.getLogger(__name__)

router = APIRouter()


# ── MCP Installer (public) ──

_MCP_BASE = os.getenv("MCP_BASE_URL", "https://mcp.neodemos.nl")
_MCP_SERVER_URL = _MCP_BASE + "/mcp"
_MCP_PUBLIC_URL = _MCP_BASE + "/public/mcp"


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
        "public_mcp_url": _MCP_PUBLIC_URL,
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


@router.get("/abonnement")
async def abonnement_page(request: Request):
    """Subscription / pricing page. Editor-editable via page_service (admin can
    override the default template content). Falls back to abonnement.html when
    no published override exists."""
    user = await get_current_user(request)
    current_tier = tier_for(user) if user else tier_for(None)
    page = page_service.get_published("abonnement")
    return templates.TemplateResponse(name="abonnement.html", request=request, context={
        "title": "Abonnement",
        "user": user,
        "current_tier": current_tier,
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


_MUNICIPALITIES_INDEX_PATH = Path(__file__).parent.parent / "data" / "municipalities_index.json"

def _load_municipalities_index() -> dict:
    """Load the municipalities registry, returning an empty dict on any error."""
    try:
        with open(_MUNICIPALITIES_INDEX_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


@router.get("/calendar")
async def read_calendar(
    request: Request,
    year: int = None,
    month: int = None,
    committee: str = None,
    search: str = None,
    view: str = "list",
    show_empty: bool = False,
    gemeente: str = Query(default="rotterdam"),
):
    """Public calendar page -- no login required.

    By default (show_empty=False), future meetings without documents are hidden.
    The template exposes a toggle so users can reveal them.

    Args:
        gemeente: municipality slug validated against data/municipalities_index.json.
                  Falls back to 'rotterdam' if unknown. Passed through to storage
                  layer for forward-compat with WS13 multi-gemeente support.
    """
    # C5: validate gemeente against the index; fall back to 'rotterdam' if unknown
    municipalities = _load_municipalities_index()
    if gemeente not in municipalities:
        gemeente = "rotterdam"

    user = await get_current_user(request)
    available_years = storage.get_meeting_years()
    # Filter out numeric-only committee IDs before rendering chips
    committees = [c for c in storage.get_distinct_committees() if c and not c.strip().isdigit()]
    if year is None and available_years:
        year = available_years[0]
    meetings = storage.get_meetings_filtered(
        year=year, committee=committee, search=search, limit=2000,
        municipality=gemeente,
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
        "gemeente": gemeente,
        "user": user,
    })


@router.get("/settings")
async def read_settings(
    request: Request,
    user: dict = Depends(require_login),
    tier: str = None,
):
    tokens = auth_service.list_user_tokens(user["id"])
    current_tier = tier_for(user)
    return templates.TemplateResponse(name="settings.html", request=request, context={
        "title": "Instellingen",
        "user": user,
        "tokens": tokens,
        "avatars": AVATARS,  # slug + label list, for the picker
        "current_avatar_slug": user.get("avatar_slug"),
        "current_avatar_url": user_avatar_url(user),
        "current_tier": current_tier,
        "tier_change_notice": tier if tier in VALID_TIERS else None,
    })


@router.post("/settings/avatar")
async def settings_set_avatar(
    request: Request,
    user: dict = Depends(require_login),
    slug: str = Form(...),
):
    """Persist the user's picked portrait slug. Whitelist-only (AVATARS)."""
    if not is_valid_slug(slug):
        return JSONResponse({"ok": False, "error": "onbekende_slug"}, status_code=400)
    try:
        auth_service.update_user(user["id"], avatar_slug=slug)
    except Exception as e:
        logger.exception("avatar update failed for user=%s slug=%s: %s", user.get("id"), slug, e)
        return JSONResponse({"ok": False, "error": "opslaan_mislukt"}, status_code=500)
    refreshed = auth_service.get_user_by_id(user["id"]) or user
    return JSONResponse({"ok": True, "slug": slug, "url": user_avatar_url(refreshed)})


@router.post("/settings/tier")
async def settings_set_tier(
    request: Request,
    user: dict = Depends(require_login),
    slug: str = Form(...),
):
    """Self-service tier switch. Only `selectable` tiers are accepted."""
    if slug not in VALID_TIERS:
        raise HTTPException(status_code=400, detail="onbekende tier")
    if not TIERS[slug].get("selectable", True):
        raise HTTPException(status_code=400, detail="tier is niet selecteerbaar")
    try:
        set_tier(user["id"], slug)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("tier switch failed for user=%s slug=%s: %s", user.get("id"), slug, e)
        raise HTTPException(status_code=500, detail="opslaan_mislukt")
    return RedirectResponse(url=f"/settings?tier={slug}#abonnement", status_code=303)


@router.post("/settings/topics")
async def settings_set_topics(
    request: Request,
    user: dict = Depends(require_login),
    topic_description: str = Form(default=""),
):
    """Persist the user's topic-focus free-text (max 500 chars)."""
    topics = (topic_description or "").strip()
    if len(topics) > 500:
        raise HTTPException(status_code=400, detail="max 500 tekens")
    try:
        auth_service.update_user(user["id"], topic_description=topics or None)
    except Exception as e:
        logger.exception("topics update failed for user=%s: %s", user.get("id"), e)
        raise HTTPException(status_code=500, detail="opslaan_mislukt")
    return RedirectResponse(url="/settings?saved=topics#onderwerpen", status_code=303)


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


# ── Dynamic user-created pages (WS8f Phase 7+) ──

@router.get("/p/{slug}")
async def render_custom_page(slug: str, request: Request):
    """Render a user-created published site_pages row at /p/{slug}.

    Returns 404 for reserved slugs and unpublished/missing pages. Reserved slugs
    must never be routable here — they already have dedicated handlers elsewhere.
    """
    slug_lower = slug.strip().lower()
    if slug_lower in RESERVED_SLUGS:
        raise HTTPException(status_code=404)
    page = page_service.get_published(slug_lower)
    if not page:
        raise HTTPException(status_code=404)
    user = await get_current_user(request)
    return templates.TemplateResponse(name="custom_page.html", request=request, context={
        "title": page.get("title") or slug_lower,
        "user": user,
        "page_title": page.get("title") or slug_lower,
        "page_html": page.get("html_content") or "",
        "page_css": page.get("css_content") or "",
    })
