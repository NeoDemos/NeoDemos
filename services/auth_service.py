"""
Authentication service: users, sessions, API tokens.

Uses the shared db_pool and passlib for password hashing.
"""

import hashlib
import os
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional

import bcrypt

from services.db_pool import get_connection

logger = logging.getLogger(__name__)

SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", "604800"))  # 7 days


def _hash_token(raw: str) -> str:
    """sha256 of raw token — these are already cryptographically random strings."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


class AuthService:
    # ── Users ──

    def create_user(
        self,
        email: str,
        password: str,
        display_name: str = None,
        role: str = "user",
        mcp_access: bool = True,
    ) -> dict:
        hashed = _hash_password(password)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO users (email, password_hash, display_name, role, mcp_access)
                       VALUES (%s, %s, %s, %s, %s)
                       RETURNING id, email, password_hash, display_name, role, is_active,
                                 mcp_access, db_access_level, created_at,
                                 subscription_tier, beta_expires_at, stripe_customer_id""",
                    (email.lower().strip(), hashed, display_name, role, mcp_access),
                )
                row = cur.fetchone()
        return self._user_row_to_dict(row)

    def authenticate(self, email: str, password: str) -> Optional[dict]:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, email, password_hash, display_name, role, is_active, "
                    "mcp_access, db_access_level, created_at, "
                    "subscription_tier, beta_expires_at, stripe_customer_id "
                    "FROM users WHERE email = %s",
                    (email.lower().strip(),),
                )
                row = cur.fetchone()
        if not row:
            # Run hash anyway to prevent timing attacks
            _hash_password("dummy")
            return None
        if not _verify_password(password, row[2]):
            return None
        return self._user_row_to_dict(row)

    def get_user_by_id(self, user_id: int) -> Optional[dict]:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, email, password_hash, display_name, role, is_active, "
                    "mcp_access, db_access_level, created_at, "
                    "subscription_tier, beta_expires_at, stripe_customer_id "
                    "FROM users WHERE id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
        return self._user_row_to_dict(row) if row else None

    def get_user_by_email(self, email: str) -> Optional[dict]:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, email, password_hash, display_name, role, is_active, "
                    "mcp_access, db_access_level, created_at, "
                    "subscription_tier, beta_expires_at, stripe_customer_id "
                    "FROM users WHERE email = %s",
                    (email.lower().strip(),),
                )
                row = cur.fetchone()
        return self._user_row_to_dict(row) if row else None

    def list_users(self) -> list:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, email, password_hash, display_name, role, is_active, "
                    "mcp_access, db_access_level, created_at, "
                    "subscription_tier, beta_expires_at, stripe_customer_id "
                    "FROM users ORDER BY created_at"
                )
                rows = cur.fetchall()
        return [self._user_row_to_dict(r) for r in rows]

    def update_user(self, user_id: int, **fields) -> Optional[dict]:
        allowed = {"display_name", "role", "is_active", "mcp_access", "db_access_level", "subscription_tier"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_user_by_id(user_id)
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        values = list(updates.values()) + [user_id]
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE users SET {set_clause}, updated_at = CURRENT_TIMESTAMP "
                    f"WHERE id = %s "
                    f"RETURNING id, email, password_hash, display_name, role, is_active, "
                    f"mcp_access, db_access_level, created_at, "
                    f"subscription_tier, beta_expires_at, stripe_customer_id",
                    values,
                )
                row = cur.fetchone()
        return self._user_row_to_dict(row) if row else None

    def update_password(self, user_id: int, new_password: str) -> bool:
        password_hash = _hash_password(new_password)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET password_hash = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (password_hash, user_id),
                )
                return cur.rowcount > 0

    # ── Sessions ──

    def create_session(
        self, user_id: int, ip_address: str = None, user_agent: str = None
    ) -> str:
        session_id = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(seconds=SESSION_MAX_AGE)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sessions (id, user_id, expires_at, ip_address, user_agent) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (session_id, user_id, expires_at, ip_address, user_agent),
                )
        return session_id

    def validate_session(self, session_id: str) -> Optional[dict]:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT s.user_id, s.expires_at, "
                    "u.id, u.email, u.password_hash, u.display_name, u.role, u.is_active, "
                    "u.mcp_access, u.db_access_level, u.created_at, "
                    "u.subscription_tier, u.beta_expires_at, u.stripe_customer_id "
                    "FROM sessions s JOIN users u ON s.user_id = u.id "
                    "WHERE s.id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        expires_at = row[1]
        if datetime.utcnow() > expires_at:
            self.delete_session(session_id)
            return None
        # Return user dict (columns 2-10)
        return self._user_row_to_dict(row[2:])

    def delete_session(self, session_id: str) -> None:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sessions WHERE id = %s", (session_id,))

    def cleanup_expired_sessions(self) -> int:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM sessions WHERE expires_at < CURRENT_TIMESTAMP"
                )
                return cur.rowcount

    # ── API Tokens ──

    def create_api_token(
        self, user_id: int, name: str = "Default", scopes: str = "search,mcp"
    ) -> dict:
        """Create a new API token. Returns dict with raw token (shown once)."""
        raw_token = secrets.token_urlsafe(48)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO api_tokens (user_id, token, name, scopes) "
                    "VALUES (%s, %s, %s, %s) "
                    "RETURNING id, name, scopes, created_at",
                    (user_id, _hash_token(raw_token), name, scopes),
                )
                row = cur.fetchone()
        return {
            "id": row[0],
            "name": row[1],
            "scopes": row[2],
            "created_at": str(row[3]),
            "token": raw_token,  # only returned at creation time
        }

    def validate_api_token(
        self, token: str, required_scope: str = None
    ) -> Optional[dict]:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT t.id, t.user_id, t.scopes, t.is_active, t.expires_at, "
                    "u.id, u.email, u.password_hash, u.display_name, u.role, u.is_active, "
                    "u.mcp_access, u.db_access_level, u.created_at, "
                    "u.subscription_tier, u.beta_expires_at, u.stripe_customer_id "
                    "FROM api_tokens t JOIN users u ON t.user_id = u.id "
                    "WHERE t.token = %s",
                    (_hash_token(token),),
                )
                row = cur.fetchone()
        if not row:
            return None
        token_is_active = row[3]
        token_expires = row[4]
        user_is_active = row[10]
        if not token_is_active or not user_is_active:
            return None
        if token_expires and datetime.utcnow() > token_expires:
            return None
        if required_scope:
            scopes = row[2].split(",")
            if required_scope not in scopes and "all" not in scopes:
                return None
        # Update last_used_at
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE api_tokens SET last_used_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (row[0],),
                )
        return self._user_row_to_dict(row[5:])

    def list_user_tokens(self, user_id: int) -> list:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, scopes, is_active, created_at, last_used_at, expires_at "
                    "FROM api_tokens WHERE user_id = %s ORDER BY created_at DESC",
                    (user_id,),
                )
                rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "scopes": r[2],
                "is_active": r[3],
                "created_at": str(r[4]),
                "last_used_at": str(r[5]) if r[5] else None,
                "expires_at": str(r[6]) if r[6] else None,
            }
            for r in rows
        ]

    def list_all_tokens(self) -> list:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT t.id, t.name, t.scopes, t.is_active, t.created_at, "
                    "t.last_used_at, t.expires_at, t.user_id, u.email "
                    "FROM api_tokens t JOIN users u ON t.user_id = u.id "
                    "ORDER BY t.created_at DESC"
                )
                rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "scopes": r[2],
                "is_active": r[3],
                "created_at": str(r[4]),
                "last_used_at": str(r[5]) if r[5] else None,
                "expires_at": str(r[6]) if r[6] else None,
                "user_id": r[7],
                "user_email": r[8],
            }
            for r in rows
        ]

    def revoke_api_token(self, token_id: int) -> bool:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE api_tokens SET is_active = FALSE WHERE id = %s",
                    (token_id,),
                )
                return cur.rowcount > 0

    # ── Rate limiting (in-memory) ──

    _login_attempts: dict = {}

    def check_login_rate_limit(self, email: str) -> bool:
        """Returns True if login is allowed, False if rate-limited."""
        key = email.lower().strip()
        now = datetime.utcnow()
        attempts = self._login_attempts.get(key, [])
        # Keep only attempts within the last 15 minutes
        attempts = [t for t in attempts if (now - t).total_seconds() < 900]
        self._login_attempts[key] = attempts
        return len(attempts) < 5

    def record_failed_login(self, email: str) -> None:
        key = email.lower().strip()
        attempts = self._login_attempts.get(key, [])
        attempts.append(datetime.utcnow())
        self._login_attempts[key] = attempts

    _register_attempts: dict = {}

    def check_register_rate_limit(self, ip: str) -> bool:
        """Returns True if registration is allowed, False if rate-limited."""
        now = datetime.utcnow()
        attempts = self._register_attempts.get(ip, [])
        attempts = [t for t in attempts if (now - t).total_seconds() < 3600]
        self._register_attempts[ip] = attempts
        return len(attempts) < 3

    def record_registration(self, ip: str) -> None:
        attempts = self._register_attempts.get(ip, [])
        attempts.append(datetime.utcnow())
        self._register_attempts[ip] = attempts

    # ── Helpers ──

    @staticmethod
    def _user_row_to_dict(row) -> dict:
        return {
            "id": row[0],
            "email": row[1],
            # row[2] = password_hash, never exposed
            "display_name": row[3],
            "role": row[4],
            "is_active": row[5],
            "mcp_access": row[6],
            "db_access_level": row[7],
            "created_at": str(row[8]),
            "subscription_tier": row[9],
            "beta_expires_at": str(row[10]) if row[10] else None,
            "stripe_customer_id": row[11],
        }
