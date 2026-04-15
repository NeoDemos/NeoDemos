"""Admin routes — dashboard, content CMS, users, tokens, pages, settings.

All routes require admin role (enforced via `require_admin` dependency).
"""
import datetime
import logging
import os
import pathlib
import re
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse

from services.auth_dependencies import auth_service, require_admin

from app_state import templates, content_service, page_service


# ── Reserved slug list ──
# Slugs that may NOT be used for user-created pages at /p/{slug}. Must include every
# top-level route in routes/pages.py, routes/admin.py, routes/auth.py, routes/api.py
# plus static mount points. Imported from routes/pages.py for the dynamic /p/{slug}
# guard — single source of truth.
RESERVED_SLUGS = {
    # Public pages
    "admin", "api", "login", "register", "logout", "settings", "overview",
    "calendar", "meeting", "mcp-installer", "over", "technologie", "methodologie",
    # Dynamic page prefix itself — /p can't be used as a slug
    "p",
    # Static + infra
    "static", "docs", "uploads", "search",
    # OAuth
    "oauth",
    # Home slug is reserved via editor-allowed slugs (maps to '/')
    "home",
}

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

# Matches spec: a-z/0-9 start + end, hyphens allowed in middle, 2-60 chars.
# Used by both the new-page endpoint and the custom-slug editor gate.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$")

# Slugs allowed in the visual editor — must match templates with GrapeJS fallback
# wired in routes/pages.py (Phase 4). 'home' maps to the '/' route.
_EDITOR_ALLOWED_SLUGS = {'home', 'over', 'technologie', 'methodologie', 'abonnement'}
_EDITOR_TITLE_MAP = {
    'home': 'Landingspagina',
    'over': 'Over NeoDemos',
    'technologie': 'Technologie',
    'methodologie': 'Methodologie',
    'abonnement': 'Abonnement',
}


def _slug_allowed_for_editor(slug: str) -> bool:
    """True for canonical editable slugs OR any valid, non-reserved custom slug.

    Custom slugs created via /admin/pages/new go to /p/{slug}. The 4 canonical
    slugs map to dedicated routes. Reserved slugs are never editable here —
    that's how we prevent someone creating a 'login' page that shadows auth.
    """
    if slug in _EDITOR_ALLOWED_SLUGS:
        return True
    if slug in RESERVED_SLUGS:
        return False
    return bool(_SLUG_RE.match(slug))


@router.get("/admin/pages")
async def admin_pages(request: Request, user: dict = Depends(require_admin)):
    """List canonical editable pages plus any user-created custom pages."""
    canonical = [
        {"slug": s, "title": _EDITOR_TITLE_MAP[s]}
        for s in ('home', 'over', 'technologie', 'methodologie', 'abonnement')
    ]
    db_pages = {p["slug"]: p for p in page_service.list_pages()}
    pages = []
    seen = set()
    for c in canonical:
        db = db_pages.get(c["slug"])
        seen.add(c["slug"])
        pages.append({
            "slug": c["slug"],
            "title": c["title"],
            "is_published": db["is_published"] if db else False,
            "has_draft": db is not None,
            "updated_at": db["updated_at"] if db else None,
            "is_custom": False,
        })
    # Append custom pages (created via /admin/pages/new — live at /p/{slug})
    for slug, db in sorted(db_pages.items()):
        if slug in seen:
            continue
        pages.append({
            "slug": slug,
            "title": db.get("title") or slug,
            "is_published": db.get("is_published", False),
            "has_draft": True,
            "updated_at": db.get("updated_at"),
            "is_custom": True,
        })
    return templates.TemplateResponse(name="admin/pages.html", request=request, context={
        "title": "Pagina's", "user": user, "pages": pages,
        "admin_title": "Pagina's", "active": "pages",
    })


@router.get("/admin/editor/{slug}")
async def admin_editor(slug: str, request: Request, user: dict = Depends(require_admin)):
    """Render the GrapeJS visual editor for a page."""
    if not _slug_allowed_for_editor(slug):
        return JSONResponse({"error": "Slug niet toegestaan"}, status_code=400)
    page = page_service.get_draft(slug)
    page_title = _EDITOR_TITLE_MAP.get(slug, (page or {}).get("title") or slug)
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
    if not _slug_allowed_for_editor(slug):
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
    if not _slug_allowed_for_editor(slug):
        return JSONResponse({"error": "Slug niet toegestaan"}, status_code=400)
    body = await request.json()
    # Title: if the editor didn't send one (current behavior), pass None so
    # page_service.save preserves whatever title the page already has. For
    # canonical slugs with no DB row yet, seed with the map's display label.
    raw_title = body.get("title")
    if raw_title is None or (isinstance(raw_title, str) and raw_title.strip() == ""):
        # Preserve existing title. For a brand-new canonical slug with no row,
        # the INSERT path will fall back to slug — so ensure those seed with
        # their canonical label on first save.
        existing = page_service.get_draft(slug)
        if existing:
            title = None  # keep existing title
        else:
            title = _EDITOR_TITLE_MAP.get(slug)  # may still be None for custom slugs
    else:
        title = raw_title.strip()
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
    if not _slug_allowed_for_editor(slug):
        return JSONResponse({"error": "Slug niet toegestaan"}, status_code=400)
    ok = page_service.publish(slug, user["id"])
    if not ok:
        return JSONResponse({"error": "Pagina niet gevonden"}, status_code=404)
    logger.info(f"Admin {user['email']} published page slug={slug}")
    return JSONResponse({"ok": True})


@router.post("/admin/api/page/{slug}/unpublish")
async def admin_unpublish_page(slug: str, user: dict = Depends(require_admin)):
    if not _slug_allowed_for_editor(slug):
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


class _StubURL:
    """Stub url object for templates that reference request.url.path (e.g. nav active-link highlighting)."""
    path = ""


class _StubRequest:
    """Minimal request stub for template rendering outside an HTTP handler."""
    url = _StubURL()
    headers = {}
    cookies = {}
    query_params = {}


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
            request=_StubRequest(),  # stub — _nav.html uses request.url.path for active state
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
    except Exception as e:
        logger.error(f"Template render failed for slug={slug}: {e}")
        return JSONResponse({"error": f"Sjabloon kon niet worden geladen: {e}"}, status_code=500)
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

    # Strip <script> blocks — GrapeJS shouldn't execute site JS in the editor canvas
    content_html = re.sub(r'<script\b[^>]*>.*?</script>', '', content_html, flags=re.DOTALL | re.IGNORECASE)

    return JSONResponse({"html": content_html, "css": None})


# ── Custom page creation + asset upload (WS8f Phase 7+) ──

@router.post("/admin/pages/new")
async def create_new_page(
    request: Request,
    slug: str = Form(...),
    title: str = Form(...),
    user: dict = Depends(require_admin),
):
    """Create an empty draft page at an arbitrary slug. Redirects to the editor."""
    slug = slug.strip().lower()
    title = (title or "").strip() or slug
    if not _SLUG_RE.match(slug):
        return RedirectResponse("/admin/pages?error=invalid_slug", status_code=303)
    if slug in RESERVED_SLUGS:
        return RedirectResponse("/admin/pages?error=reserved_slug", status_code=303)
    existing = page_service.get_draft(slug)
    if existing:
        return RedirectResponse("/admin/pages?error=exists", status_code=303)
    page_service.save(slug, title, "{}", "", "", user["id"])
    logger.info(f"Admin {user['email']} created new page slug={slug}")
    return RedirectResponse(f"/admin/editor/{slug}", status_code=303)


ALLOWED_UPLOAD_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}
ALLOWED_UPLOAD_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5 MB


def _uploads_root() -> pathlib.Path:
    """Return the absolute path to static/uploads, rooted at the project dir."""
    return pathlib.Path(__file__).resolve().parent.parent / "static" / "uploads"


@router.post("/admin/api/uploads")
async def upload_asset(
    file: UploadFile = File(...),
    user: dict = Depends(require_admin),
):
    """Upload an image asset for use in the visual editor. Stores under
    static/uploads/{yyyy}/{mm}/{uuid}{ext} and returns the public URL."""
    if file.content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Toegestaan: PNG, JPG, WebP, SVG.")
    ext = pathlib.Path(file.filename or "").suffix.lower()
    # Normalize .jpg (content-type image/jpeg accepts both extensions)
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Ongeldige bestandsextensie.")
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="Bestand te groot (max 5MB).")
    # SVG: minimal sanitize — reject any <script> tag. A full SVG sanitizer would be
    # better but bleach doesn't cover SVG; this at least blocks the obvious XSS vector.
    if ext == ".svg" and b"<script" in content.lower():
        raise HTTPException(status_code=400, detail="SVG met <script> niet toegestaan.")
    now = datetime.datetime.utcnow()
    rel_dir = _uploads_root() / f"{now.year:04d}" / f"{now.month:02d}"
    rel_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex}{ext}"
    (rel_dir / fname).write_bytes(content)
    url = f"/static/uploads/{now.year:04d}/{now.month:02d}/{fname}"
    logger.info(f"Admin {user['email']} uploaded asset {url} ({len(content)} bytes)")
    return JSONResponse({"url": url, "size": len(content), "content_type": file.content_type})


@router.get("/admin/api/uploads")
async def list_uploads(user: dict = Depends(require_admin)):
    """List the 50 most recently uploaded assets. Powers the editor asset picker."""
    root = _uploads_root()
    items: list[dict] = []
    if root.exists():
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in ALLOWED_UPLOAD_EXTENSIONS:
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            rel = path.relative_to(root.parent.parent)  # relative to project root
            items.append({
                "url": "/" + rel.as_posix(),
                "uploaded_at": datetime.datetime.utcfromtimestamp(mtime).isoformat() + "Z",
                "mtime": mtime,
            })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    # Drop the sort key from the response
    return JSONResponse([{"url": i["url"], "uploaded_at": i["uploaded_at"]} for i in items[:50]])


# ── Pipeline observability (WS5a Phase A — read-only) ──

# Known job names to always render on the dashboard, even when no pipeline_runs
# row exists yet. Keeps operators oriented: a missing job renders as "no runs"
# rather than silently vanishing. Names mirror:
#   - APScheduler wrappers in main.py (scheduled_*) → pipeline_runs.job_name
#   - Nightly cron scripts under scripts/nightly/
# If these job_names drift, fix the insert site; do NOT silently rename here.
_PIPELINE_JOB_CATALOG = [
    # (job_name as written to pipeline_runs.job_name, human label, category)
    ("document_processor",            "Document processor",       "apscheduler"),
    ("financial_sweep",               "Financial sweep",          "apscheduler"),
    ("ws2_financial_lines_extraction", "WS2 financial extraction", "apscheduler"),
    ("scheduled_refresh",             "Refresh (new documents)",  "apscheduler"),
    ("scheduled_summarization",       "WS6 summarization",        "apscheduler"),
    ("06b_compute_summaries",         "Nightly: compute summaries", "cron"),
    ("07a_enrich_new_chunks",         "Nightly: KG enrichment",   "cron"),
]


def _classify_run(run: dict) -> str:
    """Map a pipeline_runs row to a traffic-light color.

    green:  most recent run succeeded
    yellow: most recent run was skipped (advisory lock held, nothing to do, etc.)
    red:    most recent run failed
    gray:   no runs recorded yet
    """
    if run is None:
        return "gray"
    status = (run.get("status") or "").lower()
    if status == "success":
        return "green"
    if status == "skipped":
        return "yellow"
    if status == "running":
        # Hasn't finished; surface as yellow so operators notice long-runners.
        return "yellow"
    return "red"


@router.get("/admin/pipeline")
async def admin_pipeline(request: Request, user: dict = Depends(require_admin)):
    """Read-only pipeline observability dashboard.

    Issues only SELECT queries against `pipeline_runs`, `pipeline_failures`,
    and `document_events`. No writes. "Run now" triggers are deferred to
    Phase B (see docs/handoffs/WS5a_NIGHTLY_PIPELINE.md).

    Also surfaces the latest `qa_digest` JSON artefact from
    `reports/qa_digest/YYYY-MM-DD.json` and a 24-hour `00_smoke_test` strip.
    Both are best-effort: missing data renders a "waiting for first run"
    placeholder rather than erroring.
    """
    from services.db_pool import get_connection
    from psycopg2.extras import RealDictCursor
    import glob
    import json

    latest_by_job: dict = {}
    aggregates = {
        "runs_24h": 0, "runs_7d": 0,
        "failures_24h": 0, "skipped_24h": 0,
        "failures_queue": 0,
    }
    recent_failures: list = []
    recent_events: list = []
    qa_digest: dict | None = None
    qa_digest_run: dict | None = None
    qa_digest_json_path: str | None = None
    smoke_test_runs: list = []

    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1) Latest run per job_name. Uses idx_pr_job_started.
                cur.execute("""
                    SELECT DISTINCT ON (job_name)
                           job_name, started_at, finished_at, status,
                           items_discovered, items_processed, items_failed,
                           error_message, triggered_by
                      FROM pipeline_runs
                     ORDER BY job_name, started_at DESC
                """)
                for row in cur.fetchall():
                    latest_by_job[row["job_name"]] = dict(row)

                # 2) Aggregates across all jobs (24h window + 7d window).
                #    Uses idx_pr_status_started for status-specific counts.
                cur.execute("""
                    SELECT
                      COUNT(*) FILTER (WHERE started_at > NOW() - INTERVAL '24 hours')            AS runs_24h,
                      COUNT(*) FILTER (WHERE started_at > NOW() - INTERVAL '7 days')              AS runs_7d,
                      COUNT(*) FILTER (WHERE started_at > NOW() - INTERVAL '24 hours'
                                          AND status = 'failure')                                 AS failures_24h,
                      COUNT(*) FILTER (WHERE started_at > NOW() - INTERVAL '24 hours'
                                          AND status = 'skipped')                                 AS skipped_24h
                      FROM pipeline_runs
                """)
                agg = cur.fetchone() or {}
                for k in ("runs_24h", "runs_7d", "failures_24h", "skipped_24h"):
                    aggregates[k] = int(agg.get(k) or 0)

                # 3) Failures queue depth (last 7 days).
                cur.execute("""
                    SELECT COUNT(*) AS n
                      FROM pipeline_failures
                     WHERE failed_at > NOW() - INTERVAL '7 days'
                """)
                aggregates["failures_queue"] = int((cur.fetchone() or {}).get("n") or 0)

                # 4) Last 20 failures for the failures table.
                cur.execute("""
                    SELECT job_name, item_id, item_type, failed_at,
                           retry_count, error_class, error_message
                      FROM pipeline_failures
                     ORDER BY failed_at DESC
                     LIMIT 20
                """)
                recent_failures = [dict(r) for r in cur.fetchall()]

                # 5) Last 50 document_events for the activity ticker.
                #    Index idx_docevents_type_at covers (event_type, event_at DESC);
                #    a bare ORDER BY event_at DESC still scans recently-written rows
                #    efficiently because this is append-only and we LIMIT 50.
                cur.execute("""
                    SELECT document_id, event_type, event_at, triggered_by, details
                      FROM document_events
                     ORDER BY event_at DESC
                     LIMIT 50
                """)
                recent_events = [dict(r) for r in cur.fetchall()]

                # 6) Latest qa_digest pipeline_runs row.
                #    Uses idx_pr_job_started — filtered scan on job_name + DESC.
                cur.execute("""
                    SELECT job_name, started_at, finished_at, status,
                           items_discovered, items_processed, items_failed,
                           error_message, triggered_by
                      FROM pipeline_runs
                     WHERE job_name = 'qa_digest'
                     ORDER BY started_at DESC
                     LIMIT 1
                """)
                row = cur.fetchone()
                qa_digest_run = dict(row) if row else None

                # 7) Last 24 smoke-test runs (one per hour when healthy).
                #    Same index; filtered scan capped at 24 rows.
                cur.execute("""
                    SELECT started_at, finished_at, status,
                           items_processed, items_failed, error_message
                      FROM pipeline_runs
                     WHERE job_name = '00_smoke_test'
                     ORDER BY started_at DESC
                     LIMIT 24
                """)
                smoke_test_runs = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        # Surface a readable error in the template rather than 500ing the whole page.
        logger.error(f"admin_pipeline query failed: {e}")

    # Load latest qa_digest JSON artefact, if any.
    # Contract with scripts/nightly/qa_digest.py: writes reports/qa_digest/YYYY-MM-DD.json
    # with shape {overall_status, checks: [{name, status, value, threshold, details}], generated_at}.
    # We pick the most recently modified .json in that directory so manual re-runs surface.
    try:
        reports_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports", "qa_digest")
        if os.path.isdir(reports_dir):
            candidates = sorted(
                glob.glob(os.path.join(reports_dir, "*.json")),
                key=lambda p: os.path.getmtime(p),
                reverse=True,
            )
            if candidates:
                qa_digest_json_path = candidates[0]
                with open(qa_digest_json_path, "r", encoding="utf-8") as fh:
                    parsed = json.load(fh)
                # Normalise shape defensively — missing keys become benign defaults.
                qa_digest = {
                    "overall_status": (parsed.get("overall_status") or "unknown").lower(),
                    "checks": parsed.get("checks") or [],
                    "generated_at": parsed.get("generated_at"),
                    "filename": os.path.basename(qa_digest_json_path),
                }
    except Exception as e:
        logger.error(f"admin_pipeline qa_digest JSON load failed: {e}")
        qa_digest = None

    # Build the job cards in a stable order: catalog entries first, then any
    # unknown job_names that have appeared in the table (so drift is visible,
    # not hidden).
    known = {name for name, _label, _cat in _PIPELINE_JOB_CATALOG}
    jobs = []
    for name, label, category in _PIPELINE_JOB_CATALOG:
        run = latest_by_job.get(name)
        jobs.append({
            "job_name": name,
            "label": label,
            "category": category,
            "status_color": _classify_run(run),
            "run": run,
        })
    for name, run in sorted(latest_by_job.items()):
        if name in known:
            continue
        jobs.append({
            "job_name": name,
            "label": name,
            "category": "unknown",
            "status_color": _classify_run(run),
            "run": run,
        })

    # Derive smoke-test summary metrics for the template header label.
    smoke_summary = None
    if smoke_test_runs:
        total = len(smoke_test_runs)
        passed = sum(1 for r in smoke_test_runs if (r.get("status") or "").lower() == "success")
        smoke_summary = {
            "total": total,
            "passed": passed,
            "all_green": passed == total,
        }

    context = {
        "title": "Pipeline",
        "user": user,
        "admin_title": "Pipeline-status",
        "jobs": jobs,
        "aggregates": aggregates,
        "recent_failures": recent_failures,
        "recent_events": recent_events,
        "qa_digest": qa_digest,
        "qa_digest_run": qa_digest_run,
        "qa_digest_json_filename": os.path.basename(qa_digest_json_path) if qa_digest_json_path else None,
        "smoke_test_runs": smoke_test_runs,
        "smoke_summary": smoke_summary,
    }
    return templates.TemplateResponse(name="admin/pipeline.html", request=request, context=context)


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
