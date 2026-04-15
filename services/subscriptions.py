"""Subscription / tier resolution for NeoDemos.

Two tiers (locked spec from Dennis, WS8f Profiel upgrade):

  gratis — 3 website searches / month. No AI-koppeling (MCP disabled).
  pro    — €49/mo normally; €0 during beta. 20× more website searches than
           Gratis, and UNLIMITED searches via AI-koppeling (MCP).

Effective tier resolution order:

  1. users.subscription_tier_override if set (user self-picked on /settings)
  2. users.subscription_tier (legacy column from migration 0009; 'free_beta'
     is treated as 'gratis')
  3. default 'gratis'

During the beta a user who picks Pro gets pro_expires_at stamped to the
current BETA_END_DATE. No payment flow yet — see docs/handoffs/WS8f.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from services.db_pool import get_connection

logger = logging.getLogger(__name__)


# Public tier catalogue. Keep the slugs stable — they land in the DB.
TIERS = {
    "gratis": {
        "slug": "gratis",
        "label": "Gratis",
        "price_eur_month": 0,
        "price_strike": None,
        "quota_month": 3,
        "quota_copy": "3 zoekopdrachten per maand",
        "pages_limit": 50,
        "mcp_unlimited": False,
        "mcp_access": False,
        "mcp_copy": "Geen AI-koppeling",
        "selectable": True,
    },
    "pro": {
        "slug": "pro",
        "label": "Pro",
        "price_eur_month": 29,
        "price_strike": 29,
        "quota_month": 50,
        "quota_copy": "50 zoekopdrachten per maand",
        "pages_limit": 500,
        "mcp_unlimited": True,
        "mcp_access": True,
        "mcp_copy": "Onbeperkt via AI-koppeling",
        "selectable": True,
    },
    "premium": {
        "slug": "premium",
        "label": "Premium",
        "price_eur_month": 49,
        "price_strike": None,
        "quota_month": None,
        "quota_copy": "Onbeperkt zoekopdrachten per maand",
        "pages_limit": 2000,
        "mcp_unlimited": True,
        "mcp_access": True,
        "mcp_copy": "Onbeperkt via AI-koppeling",
        "selectable": False,
    },
}

VALID_SLUGS = set(TIERS.keys())


def _beta_end_date() -> str:
    """Read BETA_END_DATE at call time (not import time) so env overrides work."""
    return os.getenv("BETA_END_DATE", "2026-12-31")


def _normalise_tier_value(raw: Optional[str]) -> str:
    """Map legacy/stored tier strings onto the new two-tier slug set."""
    if not raw:
        return "gratis"
    v = raw.strip().lower()
    if v in VALID_SLUGS:
        return v
    # Legacy values we've seen:
    #   'free_beta' (migration 0009 default) → gratis
    #   'raadslid', 'publiek' → gratis (pre-pivot names)
    if v in {"free_beta", "free", "publiek", "raadslid"}:
        return "gratis"
    return "gratis"


def tier_for(user: Optional[dict]) -> dict:
    """Return the effective tier definition for a user dict.

    Adds `pro_expires_at` (ISO string or None) and `beta_end_date` so the
    template can show "Betaling actief vanaf …" without a second query.
    """
    if not user:
        tier = dict(TIERS["gratis"])
        tier["pro_expires_at"] = None
        tier["beta_end_date"] = _beta_end_date()
        return tier

    override = user.get("subscription_tier_override") if isinstance(user, dict) else None
    legacy = user.get("subscription_tier") if isinstance(user, dict) else None
    slug = _normalise_tier_value(override or legacy)

    tier = dict(TIERS[slug])
    tier["pro_expires_at"] = user.get("pro_expires_at") if isinstance(user, dict) else None
    tier["beta_end_date"] = _beta_end_date()
    return tier


def set_tier(user_id: int, slug: str) -> None:
    """Persist the user's self-selected tier.

    Writes BOTH `subscription_tier_override` (new) and `subscription_tier`
    (legacy) for compatibility with services that still read the old column.
    When a user picks Pro during beta, stamp `pro_expires_at` to BETA_END_DATE
    so we can auto-revert at beta end (script deferred — see WS8f report).
    """
    if slug not in VALID_SLUGS:
        raise ValueError(f"Unknown tier slug: {slug}")
    if not TIERS[slug].get("selectable", True):
        raise ValueError("tier is niet selecteerbaar")

    beta_end = _beta_end_date()
    pro_expires = beta_end if slug == "pro" else None

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '3s'")
            cur.execute(
                """UPDATE users
                   SET subscription_tier_override = %s,
                       subscription_tier = %s,
                       pro_expires_at = CASE WHEN %s::date IS NULL THEN NULL ELSE %s::date END,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = %s""",
                (slug, slug, pro_expires, pro_expires, user_id),
            )
    logger.info(
        "tier changed user=%s → %s (pro_expires_at=%s)", user_id, slug, pro_expires
    )


def pro_is_beta_free(user: Optional[dict]) -> bool:
    """Convenience: True if this user's Pro status is currently free (beta)."""
    if not user:
        return False
    t = tier_for(user)
    if t["slug"] != "pro":
        return False
    expires = t.get("pro_expires_at")
    if not expires:
        return True
    # Accept either datetime or ISO string
    try:
        if isinstance(expires, str):
            expires_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        else:
            expires_dt = expires
        return expires_dt.replace(tzinfo=None) >= datetime.utcnow()
    except Exception:
        return True
