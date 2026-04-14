"""Auth + OAuth routes.

Public authentication (login/register/logout) plus the OAuth 2.1 consent flow
used by MCP clients (Claude, ChatGPT, Perplexity).
"""
import os
import re
import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse

from services.auth_dependencies import (
    auth_service,
    get_current_user,
    sign_session_id,
    unsign_session_id,
    generate_csrf_token,
)
from services.mcp_oauth_provider import NeodemosOAuthProvider

from app_state import templates

logger = logging.getLogger(__name__)

router = APIRouter()

_oauth_provider = NeodemosOAuthProvider()


# ── Auth routes (public) ──

@router.get("/login")
async def login_page(request: Request, success: str = None):
    # Generate a temporary CSRF token (not session-bound for login page)
    csrf = generate_csrf_token("login-form")
    return templates.TemplateResponse(name="login.html", request=request, context={
        "title": "Inloggen", "csrf_token": csrf, "error": None, "success": success,
    })


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(""),
):
    # Rate limiting
    if not auth_service.check_login_rate_limit(email):
        csrf = generate_csrf_token("login-form")
        return templates.TemplateResponse(name="login.html", request=request, context={
            "title": "Inloggen", "csrf_token": csrf, "email": email,
            "error": "Te veel mislukte pogingen. Probeer het over 15 minuten opnieuw.",
        })

    user = auth_service.authenticate(email, password)
    if not user:
        auth_service.record_failed_login(email)
        csrf = generate_csrf_token("login-form")
        return templates.TemplateResponse(name="login.html", request=request, context={
            "title": "Inloggen", "csrf_token": csrf, "email": email,
            "error": "Ongeldige inloggegevens.",
        })

    if not user["is_active"]:
        csrf = generate_csrf_token("login-form")
        return templates.TemplateResponse(name="login.html", request=request, context={
            "title": "Inloggen", "csrf_token": csrf, "email": email,
            "error": "Uw account is gedeactiveerd. Neem contact op met de beheerder.",
        })

    # Create session
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent", "")[:200]
    session_id = auth_service.create_session(user["id"], ip_address=ip, user_agent=ua)
    signed = sign_session_id(session_id)

    response = RedirectResponse(url="/", status_code=303)
    is_prod = os.getenv("ENVIRONMENT", "").lower() == "production"
    response.set_cookie(
        "session_id", signed,
        httponly=True, secure=is_prod, samesite="lax", path="/",
        max_age=int(os.getenv("SESSION_MAX_AGE", "604800")),
    )
    return response


@router.get("/register")
async def register_page(request: Request):
    csrf = generate_csrf_token("register-form")
    return templates.TemplateResponse(name="register.html", request=request, context={
        "title": "Registreren", "csrf_token": csrf, "error": None,
    })


@router.post("/register")
async def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    display_name: str = Form(""),
    csrf_token: str = Form(""),
):
    ip = request.client.host if request.client else "unknown"

    # Rate limiting
    if not auth_service.check_register_rate_limit(ip):
        csrf = generate_csrf_token("register-form")
        return templates.TemplateResponse(name="register.html", request=request, context={
            "title": "Registreren", "csrf_token": csrf, "email": email,
            "display_name": display_name,
            "error": "Te veel registraties. Probeer het later opnieuw.",
        })

    # Validation
    email_clean = email.lower().strip()
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email_clean):
        csrf = generate_csrf_token("register-form")
        return templates.TemplateResponse(name="register.html", request=request, context={
            "title": "Registreren", "csrf_token": csrf, "email": email,
            "display_name": display_name,
            "error": "Ongeldig e-mailadres.",
        })

    if len(password) < 8:
        csrf = generate_csrf_token("register-form")
        return templates.TemplateResponse(name="register.html", request=request, context={
            "title": "Registreren", "csrf_token": csrf, "email": email,
            "display_name": display_name,
            "error": "Wachtwoord moet minimaal 8 tekens bevatten.",
        })

    if password != password_confirm:
        csrf = generate_csrf_token("register-form")
        return templates.TemplateResponse(name="register.html", request=request, context={
            "title": "Registreren", "csrf_token": csrf, "email": email,
            "display_name": display_name,
            "error": "Wachtwoorden komen niet overeen.",
        })

    # Check if email already exists
    if auth_service.get_user_by_email(email_clean):
        csrf = generate_csrf_token("register-form")
        return templates.TemplateResponse(name="register.html", request=request, context={
            "title": "Registreren", "csrf_token": csrf, "email": email,
            "display_name": display_name,
            "error": "Dit e-mailadres is al in gebruik.",
        })

    auth_service.create_user(email_clean, password, display_name=display_name.strip() or None)
    auth_service.record_registration(ip)

    return RedirectResponse(url="/login?success=Account+aangemaakt.+U+kunt+nu+inloggen.", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    signed = request.cookies.get("session_id")
    if signed:
        session_id = unsign_session_id(signed)
        if session_id:
            auth_service.delete_session(session_id)
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("session_id", path="/")
    return response


# ── OAuth 2.1 Consent Flow (for MCP clients: Claude, ChatGPT, Perplexity) ──

@router.get("/oauth/authorize")
async def oauth_authorize_page(
    request: Request,
    client_id: str = "",
    redirect_uri: str = "",
    state: str = "",
    scope: str = "mcp search",
    code_challenge: str = "",
):
    """
    OAuth 2.1 consent page. The MCP SDK redirects here during the authorization flow.
    If the user is already logged in (session cookie), show consent screen.
    If not, show login form that returns here after authentication.
    """
    # Check if user is already logged in via session cookie
    user = await get_current_user(request)

    if not user:
        # Store OAuth params in query string, redirect to login with return URL
        oauth_params = urlencode({
            "client_id": client_id, "redirect_uri": redirect_uri,
            "state": state, "scope": scope, "code_challenge": code_challenge,
        })
        return RedirectResponse(
            url=f"/oauth/login?{oauth_params}",
            status_code=303,
        )

    # User is logged in — check mcp_access
    if not user.get("mcp_access"):
        return templates.TemplateResponse(name="oauth_error.html", request=request, context={
            "title": "Geen MCP-toegang",
            "error": "Uw account heeft geen MCP-toegang. Neem contact op met de beheerder.",
        })

    # Show consent page
    client = await _oauth_provider.get_client(client_id)
    csrf = generate_csrf_token("oauth-consent")
    return templates.TemplateResponse(name="oauth_consent.html", request=request, context={
        "title": "Toestemming verlenen",
        "user": user,
        "client_name": client.client_name if client else client_id,
        "scope": scope,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "csrf_token": csrf,
    })


@router.get("/oauth/login")
async def oauth_login_page(
    request: Request,
    client_id: str = "", redirect_uri: str = "", state: str = "",
    scope: str = "mcp search", code_challenge: str = "",
):
    """Login page during OAuth flow — after login, returns to consent page."""
    csrf = generate_csrf_token("oauth-login")
    return templates.TemplateResponse(name="oauth_login.html", request=request, context={
        "title": "Inloggen — MCP Autorisatie",
        "csrf_token": csrf, "error": None,
        "client_id": client_id, "redirect_uri": redirect_uri,
        "state": state, "scope": scope, "code_challenge": code_challenge,
    })


@router.post("/oauth/login")
async def oauth_login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    client_id: str = Form(""),
    redirect_uri: str = Form(""),
    state: str = Form(""),
    scope: str = Form("mcp search"),
    code_challenge: str = Form(""),
    csrf_token: str = Form(""),
):
    """Process login during OAuth flow, then redirect to consent."""
    user = auth_service.authenticate(email, password)
    if not user:
        csrf = generate_csrf_token("oauth-login")
        return templates.TemplateResponse(name="oauth_login.html", request=request, context={
            "title": "Inloggen — MCP Autorisatie",
            "csrf_token": csrf, "email": email, "error": "Ongeldige inloggegevens.",
            "client_id": client_id, "redirect_uri": redirect_uri,
            "state": state, "scope": scope, "code_challenge": code_challenge,
        })

    if not user["is_active"]:
        csrf = generate_csrf_token("oauth-login")
        return templates.TemplateResponse(name="oauth_login.html", request=request, context={
            "title": "Inloggen — MCP Autorisatie",
            "csrf_token": csrf, "email": email,
            "error": "Uw account is gedeactiveerd.",
            "client_id": client_id, "redirect_uri": redirect_uri,
            "state": state, "scope": scope, "code_challenge": code_challenge,
        })

    # Create session cookie so the consent page knows the user
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent", "")[:200]
    session_id = auth_service.create_session(user["id"], ip_address=ip, user_agent=ua)
    signed = sign_session_id(session_id)

    # Redirect back to consent page with OAuth params
    oauth_params = urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri,
        "state": state, "scope": scope, "code_challenge": code_challenge,
    })
    response = RedirectResponse(url=f"/oauth/authorize?{oauth_params}", status_code=303)
    is_prod = os.getenv("ENVIRONMENT", "").lower() == "production"
    response.set_cookie(
        "session_id", signed,
        httponly=True, secure=is_prod, samesite="lax", path="/",
        max_age=int(os.getenv("SESSION_MAX_AGE", "604800")),
    )
    return response


@router.post("/oauth/consent")
async def oauth_consent_submit(
    request: Request,
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(""),
    scope: str = Form("mcp search"),
    code_challenge: str = Form(...),
    csrf_token: str = Form(""),
):
    """User approved consent — generate auth code and redirect back to client."""
    user = await get_current_user(request)
    if not user:
        return RedirectResponse(url="/oauth/login", status_code=303)

    if not user.get("mcp_access"):
        return templates.TemplateResponse(name="oauth_error.html", request=request, context={
            "title": "Geen MCP-toegang", "error": "Geen MCP-toegang voor dit account.",
        })

    # Generate authorization code
    code = await _oauth_provider.create_authorization_code(
        client_id=client_id,
        user_id=user["id"],
        redirect_uri=redirect_uri,
        scope=scope,
        code_challenge=code_challenge,
    )

    # Build the full callback URL with code + state
    params = {"code": code}
    if state:
        params["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    full_redirect = f"{redirect_uri}{separator}{urlencode(params)}"

    # Look up the client's display name so the success page can name it
    client = await _oauth_provider.get_client(client_id)
    client_name = client.client_name if client and client.client_name else client_id

    # Show an interstitial success page instead of redirecting straight away.
    # The template has a 2-second meta-refresh to `full_redirect`, plus manual
    # fallback buttons in case the auto-refresh is blocked. This gives the user
    # explicit visual confirmation that NeoDemos accepted the consent before
    # handing control back to the MCP client.
    return templates.TemplateResponse(name="oauth_success.html", request=request, context={
        "title": "Autorisatie geslaagd",
        "client_name": client_name,
        "redirect_url": full_redirect,
    })
