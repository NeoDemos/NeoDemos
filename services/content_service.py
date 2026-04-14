"""
Content CMS service: read/write site_content rows with in-memory TTL cache.

Uses the shared db_pool — same pattern as auth_service.py.
"""

import time
import logging
from typing import Optional

from services.db_pool import get_connection

logger = logging.getLogger(__name__)


class ContentService:
    def __init__(self):
        self._cache: dict[str, str] = {}  # key -> value
        self._cache_ts: float = 0          # last refresh epoch
        self._ttl: int = 60               # seconds

    def get(self, key: str, default: str = '') -> str:
        """Get content value by key. Returns DB value, or fallback default."""
        self._maybe_refresh()
        if key in self._cache:
            return self._cache[key]
        return default

    def get_section(self, section: str) -> list[dict]:
        """Get all content items for a section (for admin editor)."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, key, section, label, content_type, value, "
                    "default_value, help_text, sort_order, updated_at, updated_by "
                    "FROM site_content WHERE section = %s ORDER BY sort_order",
                    (section,),
                )
                rows = cur.fetchall()
        return [self._row_to_dict(r) for r in rows]

    def update(self, key: str, value: str, user_id: int) -> bool:
        """Update content value, invalidate cache."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE site_content SET value = %s, updated_at = CURRENT_TIMESTAMP, "
                    "updated_by = %s WHERE key = %s",
                    (value, user_id, key),
                )
                changed = cur.rowcount > 0
        if changed:
            self._invalidate()
        return changed

    def reset(self, key: str, user_id: int) -> bool:
        """Reset to default_value, invalidate cache."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE site_content SET value = default_value, "
                    "updated_at = CURRENT_TIMESTAMP, updated_by = %s WHERE key = %s",
                    (user_id, key),
                )
                changed = cur.rowcount > 0
        if changed:
            self._invalidate()
        return changed

    def _maybe_refresh(self):
        """Refresh cache if TTL expired."""
        now = time.time()
        if now - self._cache_ts > self._ttl:
            self._load_cache()

    def _load_cache(self):
        """Load all key->value pairs from DB."""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT key, COALESCE(value, default_value) FROM site_content"
                    )
                    rows = cur.fetchall()
            self._cache = {row[0]: row[1] for row in rows}
            self._cache_ts = time.time()
        except Exception:
            # If DB is not available (e.g. table not yet migrated), use empty cache
            logger.debug("site_content table not available, using empty cache")
            self._cache = {}
            self._cache_ts = time.time()

    def _invalidate(self):
        """Force cache reload on next get()."""
        self._cache_ts = 0

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "id": row[0],
            "key": row[1],
            "section": row[2],
            "label": row[3],
            "content_type": row[4],
            "value": row[5],
            "default_value": row[6],
            "help_text": row[7],
            "sort_order": row[8],
            "updated_at": str(row[9]) if row[9] else None,
            "updated_by": row[10],
        }
