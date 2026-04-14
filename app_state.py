"""Shared application state — service instances used by all route modules.

This module owns service singletons to avoid circular imports between main.py
and routes/*.py. Import from here, never from main.py.
"""
import logging
import os
import json
from datetime import datetime, date

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from services.open_raad import OpenRaadService
from services.storage import StorageService
from services.ai_service import AIService, GEMINI_AVAILABLE
from services.refresh_service import RefreshService
from services.web_intelligence import WebIntelligenceService
from services.content_service import ContentService
from services.page_service import PageService

logger = logging.getLogger(__name__)

# ── Service instances ──
raad_service = OpenRaadService()
storage = StorageService()

try:
    ai_service = AIService()
    logger.info(
        "AIService init complete. GEMINI_AVAILABLE=%s use_llm=%s has_key=%s",
        GEMINI_AVAILABLE, ai_service.use_llm, bool(ai_service.api_key),
    )
except Exception as e:
    logger.warning("AIService init FAILED: %s", e)
    ai_service = None

refresh_service = RefreshService(storage, raad_service, ai_service)

# WS9 — Web Intelligence Service (Sonnet + MCP tool_use)
# Pass ai_service as Gemini fallback when Anthropic is unavailable or errors
try:
    web_intel = WebIntelligenceService(ai_service=ai_service)
    if web_intel.available:
        logger.info(f"WebIntelligenceService ready (model={web_intel.model})")
    else:
        logger.warning(
            "WebIntelligenceService unavailable — ANTHROPIC_API_KEY not set, "
            "Gemini fallback active"
        )
except Exception as e:
    logger.error(f"WebIntelligenceService init failed: {e}")
    web_intel = None

content_service = ContentService()
page_service = PageService()

# ── Party profile / lens caches ──
# Initialize party profile and lens evaluation services
# These are lazy-loaded since they're only used for party lens analysis
try:
    from cachetools import TTLCache
    _party_profile_cache: dict = TTLCache(maxsize=500, ttl=3600)
    _party_lens_cache: dict = TTLCache(maxsize=500, ttl=3600)
except ImportError:
    _party_profile_cache = {}  # fallback if cachetools not installed
    _party_lens_cache = {}

# ── Scheduler (jobs registered in main.py lifespan) ──
scheduler = BackgroundScheduler()

# ── Demo cache (landing page) ──
# Pre-rendered AI answers loaded from data/demo_cache.json at startup.
# Generate with: python scripts/cache_demo_answers.py
# Each entry: {id, question, label, answer (markdown), sources, cached_at}
_DEMO_CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "demo_cache.json")


def _load_demo_cache() -> list:
    try:
        with open(_DEMO_CACHE_PATH) as f:
            entries = json.load(f)
            if entries:
                logger.info(f"Demo cache loaded: {len(entries)} answers")
            return entries
    except FileNotFoundError:
        logger.info("Demo cache not found — run scripts/cache_demo_answers.py")
        return []
    except Exception as e:
        logger.warning(f"Demo cache load failed: {e}")
        return []


DEMO_CACHE: list = _load_demo_cache()


def get_demo_entry() -> dict | None:
    """Return the primary demo entry (first in cache), or None if cache empty."""
    if not DEMO_CACHE:
        return None
    # DEMO_ANSWER_ID env var lets you pin a specific demo by id without redeploy
    pinned_id = os.getenv("DEMO_ANSWER_ID")
    if pinned_id:
        for entry in DEMO_CACHE:
            if entry.get("id") == pinned_id:
                return entry
    return DEMO_CACHE[0]


# ── Landing headline (rotates via LANDING_HEADLINE env var) ──
# Supports literal \n in .env files (converted to real newlines)
_raw_headline = os.getenv(
    "LANDING_HEADLINE",
    "De raadsvergadering was altijd openbaar.\nNu is ze ook begrijpelijk.",
)
LANDING_HEADLINE = _raw_headline.replace("\\n", "\n")


# ── Templates (Jinja2) ──
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
from neodemos_version import VERSION_LABEL, DISPLAY_NAME, STAGE
templates.env.globals["version_label"] = VERSION_LABEL
templates.env.globals["display_name"] = DISPLAY_NAME
templates.env.globals["stage"] = STAGE

# Content CMS: make content() available in all templates
templates.env.globals["content"] = content_service.get
