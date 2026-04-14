"""Admin routes — dashboard, content CMS, users, tokens, pages, settings.

All routes require admin role (enforced via `require_admin` dependency).
"""
import logging
import os
import re

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse

from services.auth_dependencies import auth_service, require_admin

from app_state import templates, content_service, page_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Admin pages ──

@router.get("/admin")
async def admin_dashboard(request: Request, user: dict = Depends(require_admin)):
    """Dashboard overview with top-line stats across the admin surface."""
    users = auth_service.list_users()
    tokens = auth_service.list_all_tokens()
    pages = page_service.list_pages()

    # Count content items across all known sections
    sections = ('landing', 'over', 'technologie', 'methodologie', 'footer')
    content_items = 0
    for s in sections:
        try:
            content_items += len(content_service.get_section(s))
        except Exception as e:
            logger.warning(f"Failed to count content for section {s}: {e}")

    stats = {
        "users": len(users),
        "active_users": sum(1 for u in users if u["is_active"]),
        "tokens": len(tokens),
        "content_items": content_items,
        "pages": len(pages),
    }
    return templates.TemplateResponse(name="admin/dashboard.html", request=request, context={
        "title": "Beheer", "user": user, "stats": stats, "admin_title": "Dashboard",
    })


@router.get("/admin/content")
async def admin_content(request: Request, user: dict = Depends(require_admin)):
    """Browser-based form editor for all site_content rows grouped by section."""
    sections = {
        'landing': content_service.get_section('landing'),
        'over': content_service.get_section('over'),
        'technologie': content_service.get_section('technologie'),
        'methodologie': content_service.get_section('methodologie'),
        'footer': content_service.get_section('footer'),
    }
    return templates.TemplateResponse(name="admin/content.html", request=request, context={
        "title": "Inhoud", "user": user, "sections": sections,
        "admin_title": "Inhoud beheren",
    })


@router.get("/admin/users")
async def admin_users(request: Request, user: dict = Depends(require_admin)):
    users = auth_service.list_users()
    return templates.TemplateResponse(name="admin/users.html", request=request, context={
        "title": "Gebruikers", "user": user, "users": users,
        "admin_title": "Gebruikers",
    })


@router.get("/admin/tokens")
async def admin_tokens(request: Request, user: dict = Depends(require_admin)):
    tokens = auth_service.list_all_tokens()
    return templates.TemplateResponse(name="admin/tokens.html", request=request, context={
        "title": "API Tokens", "user": user, "tokens": tokens,
        "admin_title": "API Tokens",
    })


# ── Page builder (GrapeJS) ──

# Slugs allowed in the visual editor — must match templates with GrapeJS fallback
# wired in routes/pages.py (Phase 4). 'home' maps to the '/' route.
_EDITOR_ALLOWED_SLUGS = {'home', 'over', 'technologie', 'methodologie'}
_EDITOR_TITLE_MAP = {
    'home': 'Landingspagina',
    'over': 'Over NeoDemos',
    'technologie': 'Technologie',
    'methodologie': 'Methodologie',
}


@router.get("/admin/pages")
async def admin_pages(request: Request, user: dict = Depends(require_admin)):
    """List the 4 canonical editable pages, merged with any existing DB rows."""
    canonical = [
        {"slug": s, "title": _EDITOR_TITLE_MAP[s]}
        for s in ('home', 'over', 'technologie', 'methodologie')
    ]
    db_pages = {p["slug"]: p for p in page_service.list_pages()}
    pages = []
    for c in canonical:
        db = db_pages.get(c["slug"])
        pages.append({
            "slug": c["slug"],
            "title": c["title"],
            "is_published": db["is_published"] if db else False,
            "has_draft": db is not None,
            "updated_at": db["updated_at"] if db else None,
        })
    return templates.TemplateResponse(name="admin/pages.html", request=request, context={
        "title": "Pagina's", "user": user, "pages": pages,
        "admin_title": "Pagina's", "active": "pages",
    })


@router.get("/admin/editor/{slug}")
async def admin_editor(slug: str, request: Request, user: dict = Depends(require_admin)):
    """Render the GrapeJS visual editor for a page."""
    if slug not in _EDITOR_ALLOWED_SLUGS:
        return JSONResponse({"error": "Slug niet toegestaan"}, status_code=400)
    page = page_service.get_draft(slug)
    page_title = _EDITOR_TITLE_MAP.get(slug, slug)
    return templates.TemplateResponse(name="admin/editor.html", request=request, context={
        "title": f"Bewerken: {page_title}",
        "user": user,
        "active": "pages",
        "slug": slug,
        "page_title": page_title,
        "page": page,  # may be None — editor handles it
    })


@router.get("/admin/api/page/{slug}")
async def admin_get_page(slug: str, user: dict = Depends(require_admin)):
    """Load page data (grapes_json + html + css) for the editor."""
    if slug not in _EDITOR_ALLOWED_SLUGS:
        return JSONResponse({"error": "Slug niet toegestaan"}, status_code=400)
    page = page_service.get_draft(slug)
    if not page:
        return JSONResponse({"grapes_json": None, "is_published": False})
    return JSONResponse({
        "grapes_json": page.get("grapes_json"),
        "html_content": page.get("html_content"),
        "css_content": page.get("css_content"),
        "is_published": page.get("is_published", False),
        "title": page.get("title"),
    })


@router.post("/admin/api/page/{slug}")
async def admin_save_page(slug: str, request: Request, user: dict = Depends(require_admin)):
    """Save page draft. HTML is sanitized via bleach (defense in depth)."""
    import bleach
    if slug not in _EDITOR_ALLOWED_SLUGS:
        return JSONResponse({"error": "Slug niet toegestaan"}, status_code=400)
    body = await request.json()
    title = body.get("title") or _EDITOR_TITLE_MAP.get(slug, slug)
    grapes_json = body.get("grapes_json", "")
    html = body.get("html", "")
    css = body.get("css", "")

    # Permissive allowlist for layout-style admin authoring.
    allowed_tags = [
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'a', 'ul', 'ol', 'li',
        'strong', 'em', 'b', 'i', 'blockquote', 'cite', 'div', 'span',
        'section', 'article', 'img', 'br', 'hr', 'table', 'thead', 'tbody',
        'tr', 'td', 'th', 'button', 'details', 'summary',
    ]
    allowed_attrs = {
        '*': ['class', 'id', 'style', 'data-gjs-type'],
        'a': ['href', 'target', 'rel', 'class', 'id'],
        'img': ['src', 'alt', 'width', 'height', 'class', 'id'],
    }
    safe_html = bleach.clean(html, tags=allowed_tags, attributes=allowed_attrs, strip=True)
    # CSS is scoped to the editor container; bleach has no CSS sanitizer — pass through.
    saved = page_service.save(slug, title, grapes_json, safe_html, css, user["id"])
    logger.info(f"Admin {user['email']} saved page slug={slug}")
    return JSONResponse({"ok": True, "page": saved})


@router.post("/admin/api/page/{slug}/publish")
async def admin_publish_page(slug: str, user: dict = Depends(require_admin)):
    if slug not in _EDITOR_ALLOWED_SLUGS:
        return JSONResponse({"error": "Slug niet toegestaan"}, status_code=400)
    ok = page_service.publish(slug, user["id"])
    if not ok:
        return JSONResponse({"error": "Pagina niet gevonden"}, status_code=404)
    logger.info(f"Admin {user['email']} published page slug={slug}")
    return JSONResponse({"ok": True})


@router.post("/admin/api/page/{slug}/unpublish")
async def admin_unpublish_page(slug: str, user: dict = Depends(require_admin)):
    if slug not in _EDITOR_ALLOWED_SLUGS:
        return JSONResponse({"error": "Slug niet toegestaan"}, status_code=400)
    ok = page_service.unpublish(slug, user["id"])
    if not ok:
        return JSONResponse({"error": "Pagina niet gevonden"}, status_code=404)
    logger.info(f"Admin {user['email']} unpublished page slug={slug}")
    return JSONResponse({"ok": True})


_TEMPLATE_SLUG_MAP = {
    'home': 'search.html',
    'over': 'over.html',
    'technologie': 'technologie.html',
    'methodologie': 'methodologie.html',
}


@router.get("/admin/api/page/{slug}/template")
async def admin_get_template(slug: str, _user: dict = Depends(require_admin)):
    """Render the Jinja template for a slug with content() defaults, for GrapeJS editor starter.

    Returns just the <main> content — no <html>, <head>, <nav>, <footer>, or <script> tags.
    content() calls resolve to their hardcoded fallback values so the canvas starts with
    the canonical design-time content rather than any DB overrides.
    """
    template_name = _TEMPLATE_SLUG_MAP.get(slug)
    if not template_name:
        return JSONResponse({"error": "Slug niet toegestaan"}, status_code=400)

    # Temporarily override the 'content' global to always return the default fallback.
    # This ensures the editor starts with hardcoded design-time content, not DB values.
    original_content = templates.env.globals.get('content')
    templates.env.globals['content'] = lambda key, default='': default
    try:
        tpl = templates.env.get_template(template_name)
        full_html = tpl.render(
            title="",
            user=None,
            page_html=None,
            # search.html context vars
            demo_question="",
            demo_answer_markdown=None,
            demo_sources=[],
            demo_label=None,
            demo_cached_at=None,
            landing_headline="",
            # overview.html (just in case)
            meetings=[],
        )
    finally:
        # Always restore — never leave the shared env mutated
        if original_content is not None:
            templates.env.globals['content'] = original_content

    # Extract the <main>...</main> content block (base.html wraps {% block content %} in <main>)
    match = re.search(r'<main[^>]*>(.*?)</main>', full_html, re.DOTALL)
    if match:
        content_html = match.group(1).strip()
    else:
        content_html = full_html.strip()

    return JSONResponse({"html": content_html, "css": None})


@router.get("/admin/settings")
async def admin_settings(request: Request, user: dict = Depends(require_admin)):
    settings = {
        'demo_answer_id': os.getenv('DEMO_ANSWER_ID', ''),
        'landing_headline': os.getenv('LANDING_HEADLINE', ''),
    }
    return templates.TemplateResponse(name="admin/settings.html", request=request, context={
        "title": "Instellingen", "user": user, "settings": settings,
        "admin_title": "Instellingen",
    })


# ── User management API ──

@router.get("/admin/api/users")
async def admin_list_users(request: Request, user: dict = Depends(require_admin)):
    users = auth_service.list_users()
    return JSONResponse(users)


@router.post("/admin/api/users/{user_id}/update")
async def admin_update_user(request: Request, user_id: int, user: dict = Depends(require_admin)):
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


@router.post("/admin/api/users/{user_id}/toggle-active")
async def admin_toggle_active(request: Request, user_id: int, user: dict = Depends(require_admin)):
    if user_id == user["id"]:
        return JSONResponse({"error": "Kan eigen account niet deactiveren"}, status_code=400)
    target = auth_service.get_user_by_id(user_id)
    if not target:
        return JSONResponse({"error": "Gebruiker niet gevonden"}, status_code=404)
    updated = auth_service.update_user(user_id, is_active=not target["is_active"])
    logger.info(f"Admin {user['email']} toggled active for user {user_id}: {updated['is_active']}")
    return JSONResponse(updated)


@router.delete("/admin/api/tokens/{token_id}")
async def admin_revoke_token(request: Request, token_id: int, user: dict = Depends(require_admin)):
    revoked = auth_service.revoke_api_token(token_id)
    if not revoked:
        return JSONResponse({"error": "Token niet gevonden"}, status_code=404)
    logger.info(f"Admin {user['email']} revoked token {token_id}")
    return JSONResponse({"ok": True})


# ── Content CMS API ──

@router.post("/admin/api/content/{key}")
async def admin_update_content(key: str, request: Request, user: dict = Depends(require_admin)):
    body = await request.json()
    value = body.get('value', '')
    ok = content_service.update(key, value, user['id'])
    if not ok:
        return JSONResponse({"error": "Content item niet gevonden"}, status_code=404)
    logger.info(f"Admin {user['email']} updated content key={key}")
    return JSONResponse({"ok": True})


@router.post("/admin/api/content/{key}/reset")
async def admin_reset_content(key: str, request: Request, user: dict = Depends(require_admin)):
    ok = content_service.reset(key, user['id'])
    if not ok:
        return JSONResponse({"error": "Content item niet gevonden"}, status_code=404)
    logger.info(f"Admin {user['email']} reset content key={key}")
    return JSONResponse({"ok": True})
