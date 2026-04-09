"""
OAuth 2.1 Authorization Server for NeoDemos MCP.

Implements the full OAuthAuthorizationServerProvider protocol
required by the MCP SDK for authenticated tool access.
"""

import os
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional, Union
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    OAuthAuthorizationServerProvider,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    AccessToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from services.db_pool import get_connection
from services.auth_service import AuthService

logger = logging.getLogger(__name__)

ACCESS_TOKEN_TTL = 3600          # 1 hour
REFRESH_TOKEN_TTL = 30 * 86400   # 30 days
AUTH_CODE_TTL = 600              # 10 minutes


class NeodemosOAuthProvider:
    """
    Full OAuth 2.1 Authorization Server for NeoDemos MCP.

    Implements all methods required by the MCP SDK's
    OAuthAuthorizationServerProvider protocol.
    """

    def __init__(self):
        self.base_url = os.getenv("NEODEMOS_BASE_URL", "https://neodemos.nl")
        self._auth_service = AuthService()

    # ── 1. Client lookup ──

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        """Look up an OAuth client by its client_id."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, secret, name, redirect_uris, grant_types, "
                    "response_types, scope, token_endpoint_auth_method "
                    "FROM oauth_clients WHERE id = %s",
                    (client_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return OAuthClientInformationFull(
            client_id=row[0],
            client_secret=row[1],
            client_name=row[2],
            redirect_uris=list(row[3]),
            grant_types=list(row[4]),
            response_types=list(row[5]),
            scope=row[6],
            token_endpoint_auth_method=row[7],
        )

    # ── 2. Dynamic client registration ──

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """Register a new OAuth client (dynamic registration)."""
        client_id = secrets.token_urlsafe(32)
        client_secret = (
            secrets.token_urlsafe(48)
            if client_info.token_endpoint_auth_method != "none"
            else None
        )
        name = client_info.client_name or "Unnamed Client"
        redirect_uris = list(client_info.redirect_uris)
        grant_types = list(client_info.grant_types or ["authorization_code"])
        response_types = list(client_info.response_types or ["code"])
        scope = client_info.scope or "mcp search"
        auth_method = client_info.token_endpoint_auth_method or "client_secret_post"

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO oauth_clients "
                    "(id, secret, name, redirect_uris, grant_types, "
                    "response_types, scope, token_endpoint_auth_method) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        client_id,
                        client_secret,
                        name,
                        redirect_uris,
                        grant_types,
                        response_types,
                        scope,
                        auth_method,
                    ),
                )

        # Set generated credentials on the client_info object so the caller
        # can return them to the registrant.
        client_info.client_id = client_id
        client_info.client_secret = client_secret

    # ── 3. Authorization (redirect to login/consent page) ──

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Return the URL for our login/consent page."""
        query = urlencode({
            "client_id": client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "state": params.state or "",
            "scope": " ".join(params.scopes or []),
            "code_challenge": params.code_challenge,
        })
        return f"{self.base_url}/oauth/authorize?{query}"

    # ── 4. Load authorization code ──

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> Optional[AuthorizationCode]:
        """Load and validate an authorization code."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT code, client_id, user_id, redirect_uri, scope, "
                    "code_challenge, expires_at "
                    "FROM oauth_authorization_codes "
                    "WHERE code = %s AND client_id = %s",
                    (authorization_code, client.client_id),
                )
                row = cur.fetchone()
        if not row:
            return None
        expires_at = row[6]
        if datetime.utcnow() > expires_at:
            logger.warning("Authorization code %s has expired", authorization_code[:8])
            return None
        scopes = row[4].split() if row[4] else []
        return AuthorizationCode(
            code=row[0],
            client_id=row[1],
            redirect_uri=row[3],
            scopes=scopes,
            code_challenge=row[5],
            expires_at=expires_at,
            redirect_uri_provided_explicitly=True,
        )

    # ── 5. Exchange authorization code for tokens ──

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        """Exchange a valid authorization code for access + refresh tokens."""
        access_token = secrets.token_urlsafe(48)
        refresh_token = secrets.token_urlsafe(48)
        access_expires = datetime.utcnow() + timedelta(seconds=ACCESS_TOKEN_TTL)
        scope_str = " ".join(authorization_code.scopes) if authorization_code.scopes else "mcp search"

        # Retrieve user_id from the authorization code row
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id FROM oauth_authorization_codes WHERE code = %s",
                    (authorization_code.code,),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError("Authorization code not found in database")
                user_id = row[0]

        # Insert access token and delete the used auth code (one-time use)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO oauth_access_tokens "
                    "(token, client_id, user_id, scope, expires_at, refresh_token) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        access_token,
                        client.client_id,
                        user_id,
                        scope_str,
                        access_expires,
                        refresh_token,
                    ),
                )
                cur.execute(
                    "DELETE FROM oauth_authorization_codes WHERE code = %s",
                    (authorization_code.code,),
                )

        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL,
            scope=scope_str,
            refresh_token=refresh_token,
        )

    # ── 6. Load refresh token ──

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> Optional[RefreshToken]:
        """Load a refresh token for validation."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT refresh_token, client_id, scope, expires_at "
                    "FROM oauth_access_tokens "
                    "WHERE refresh_token = %s AND client_id = %s",
                    (refresh_token, client.client_id),
                )
                row = cur.fetchone()
        if not row:
            return None
        scopes = row[2].split() if row[2] else []
        return RefreshToken(
            token=row[0],
            client_id=row[1],
            scopes=scopes,
            expires_at=row[3],
        )

    # ── 7. Exchange refresh token for new tokens ──

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Rotate tokens: issue new access + refresh tokens, invalidate old."""
        new_access = secrets.token_urlsafe(48)
        new_refresh = secrets.token_urlsafe(48)
        new_expires = datetime.utcnow() + timedelta(seconds=ACCESS_TOKEN_TTL)
        scope_str = " ".join(scopes) if scopes else " ".join(refresh_token.scopes)

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE oauth_access_tokens "
                    "SET token = %s, refresh_token = %s, expires_at = %s, scope = %s "
                    "WHERE refresh_token = %s AND client_id = %s",
                    (
                        new_access,
                        new_refresh,
                        new_expires,
                        scope_str,
                        refresh_token.token,
                        client.client_id,
                    ),
                )

        return OAuthToken(
            access_token=new_access,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL,
            scope=scope_str,
            refresh_token=new_refresh,
        )

    # ── 8. Load access token (for request authentication) ──

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        """Validate an access token. Checks expiry and user permissions."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT t.token, t.client_id, t.scope, t.expires_at "
                    "FROM oauth_access_tokens t "
                    "JOIN users u ON t.user_id = u.id "
                    "WHERE t.token = %s "
                    "  AND u.is_active = TRUE "
                    "  AND u.mcp_access = TRUE",
                    (token,),
                )
                row = cur.fetchone()
        if not row:
            return None
        expires_at = row[3]
        if datetime.utcnow() > expires_at:
            logger.warning("Access token expired for client %s", row[1])
            return None
        scopes = row[2].split() if row[2] else []
        return AccessToken(
            token=row[0],
            client_id=row[1],
            scopes=scopes,
            expires_at=expires_at,
        )

    # ── 9. Revoke token ──

    async def revoke_token(
        self, token: Union[AccessToken, RefreshToken]
    ) -> None:
        """Revoke an access or refresh token."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM oauth_access_tokens "
                    "WHERE token = %s OR refresh_token = %s",
                    (token.token, token.token),
                )

    # ── Additional: create authorization code (called by consent page) ──

    async def create_authorization_code(
        self,
        client_id: str,
        user_id: int,
        redirect_uri: str,
        scope: str,
        code_challenge: str,
        code_challenge_method: str = "S256",
    ) -> str:
        """
        Create an authorization code after the user authenticates
        on the consent page. Returns the code string.
        """
        code = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(seconds=AUTH_CODE_TTL)

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO oauth_authorization_codes "
                    "(code, client_id, user_id, redirect_uri, scope, "
                    "code_challenge, code_challenge_method, expires_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        code,
                        client_id,
                        user_id,
                        redirect_uri,
                        scope,
                        code_challenge,
                        code_challenge_method,
                        expires_at,
                    ),
                )

        logger.info(
            "Created authorization code for client=%s user=%s (expires %s)",
            client_id, user_id, expires_at,
        )
        return code
