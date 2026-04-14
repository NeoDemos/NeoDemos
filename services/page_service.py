"""
Page CMS service: GrapeJS page builder CRUD.

Uses the shared db_pool — same pattern as auth_service.py.
"""

import logging
from typing import Optional

import psycopg2.errors

from services.db_pool import get_connection

logger = logging.getLogger(__name__)


class PageService:
    def get_published(self, slug: str) -> Optional[dict]:
        """Return published page or None. Returns None if site_pages table is missing (pre-migration)."""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, slug, title, grapes_json, html_content, css_content, "
                        "is_published, updated_at, updated_by "
                        "FROM site_pages WHERE slug = %s AND is_published = TRUE",
                        (slug,),
                    )
                    row = cur.fetchone()
            return self._row_to_dict(row) if row else None
        except psycopg2.errors.UndefinedTable:
            logger.debug("site_pages table missing; returning None (migration 0008 not applied)")
            return None

    def get_draft(self, slug: str) -> Optional[dict]:
        """Return page for editor (any state). Returns None if site_pages table is missing."""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, slug, title, grapes_json, html_content, css_content, "
                        "is_published, updated_at, updated_by "
                        "FROM site_pages WHERE slug = %s",
                        (slug,),
                    )
                    row = cur.fetchone()
            return self._row_to_dict(row) if row else None
        except psycopg2.errors.UndefinedTable:
            logger.debug("site_pages table missing; returning None (migration 0008 not applied)")
            return None

    def save(self, slug: str, title: str, grapes_json: str,
             html: str, css: str, user_id: int) -> dict:
        """Upsert page."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO site_pages (slug, title, grapes_json, html_content, "
                    "css_content, updated_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (slug) DO UPDATE SET "
                    "title = EXCLUDED.title, grapes_json = EXCLUDED.grapes_json, "
                    "html_content = EXCLUDED.html_content, css_content = EXCLUDED.css_content, "
                    "updated_at = CURRENT_TIMESTAMP, updated_by = EXCLUDED.updated_by "
                    "RETURNING id, slug, title, grapes_json, html_content, css_content, "
                    "is_published, updated_at, updated_by",
                    (slug, title, grapes_json, html, css, user_id),
                )
                row = cur.fetchone()
        return self._row_to_dict(row)

    def publish(self, slug: str, user_id: int) -> bool:
        """Set is_published = true."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE site_pages SET is_published = TRUE, "
                    "updated_at = CURRENT_TIMESTAMP, updated_by = %s WHERE slug = %s",
                    (user_id, slug),
                )
                return cur.rowcount > 0

    def unpublish(self, slug: str, user_id: int) -> bool:
        """Set is_published = false."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE site_pages SET is_published = FALSE, "
                    "updated_at = CURRENT_TIMESTAMP, updated_by = %s WHERE slug = %s",
                    (user_id, slug),
                )
                return cur.rowcount > 0

    def list_pages(self) -> list[dict]:
        """List all pages for admin. Returns empty list if site_pages table is missing."""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, slug, title, grapes_json, html_content, css_content, "
                        "is_published, updated_at, updated_by "
                        "FROM site_pages ORDER BY slug"
                    )
                    rows = cur.fetchall()
            return [self._row_to_dict(r) for r in rows]
        except psycopg2.errors.UndefinedTable:
            logger.debug("site_pages table missing; returning [] (migration 0008 not applied)")
            return []

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "id": row[0],
            "slug": row[1],
            "title": row[2],
            "grapes_json": row[3],
            "html_content": row[4],
            "css_content": row[5],
            "is_published": row[6],
            "updated_at": str(row[7]) if row[7] else None,
            "updated_by": row[8],
        }
