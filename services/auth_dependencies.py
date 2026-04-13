"""
FastAPI auth dependencies for route protection.

Usage:
    from services.auth_dependencies import require_login, require_admin, get_api_user

    @app.get("/protected")
    async def protected(request: Request, user: dict = Depends(require_login)):
        ...
"""

import os
import logging
from typing import Optional

from fastapi import Request, Depends, HTTPException
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from services.auth_service import AuthService, SESSION_MAX_AGE

logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
_serializer = URLSafeTimedSerializer(SECRET_KEY)

auth_service = AuthService()


def sign_session_id(session_id: str) -> str:
    return _serializer.dumps(session_id)


def unsign_session_id(signed: str) -> Optional[str]:
    try:
        return _serializer.loads(signed, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def generate_csrf_token(session_id: str) -> str:
    return _serializer.dumps(f"csrf-{session_id}")


def validate_csrf_token(token: str, session_id: str) -> bool:
    try:
        value = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        return value == f"csrf-{session_id}"
    except (BadSignature, SignatureExpired):
        return False


async def get_current_user(request: Request) -> Optional[dict]:
    """Extract user from session cookie. Returns None if not logged in."""
    signed = request.cookies.get("session_id")
    if not signed:
        return None
    session_id = unsign_session_id(signed)
    if not session_id:
        return None
    return auth_service.validate_session(session_id)


async def require_login(request: Request) -> dict:
    """Dependency that redirects to /login if not authenticated."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    if not user["is_active"]:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


async def require_admin(request: Request) -> dict:
    """Dependency that requires admin role."""
    user = await require_login(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=303, headers={"Location": "/"})
    return user


async def get_api_user(request: Request) -> Optional[dict]:
    """For API endpoints: accepts either Bearer token OR session cookie."""
    # Try Bearer token first
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        user = auth_service.validate_api_token(token, required_scope="search")
        if user:
            return user

    # Fall back to session cookie
    user = await get_current_user(request)
    if user and user["is_active"]:
        return user

    return None
