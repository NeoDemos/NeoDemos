"""
MCP rate limiter — sliding-window in-memory rate limits for WS4.

Two layers:
  1. Per-IP sliding-window (for the public /public/mcp endpoint) —
     implemented as a Starlette middleware class RateLimitMiddleware.
  2. Per-tool-name global counter (in logged_tool) for expensive tools —
     implemented as check_tool_rate_limit(tool_name) called from the
     logged_tool decorator in mcp_server_v3.py.

Design notes:
  - In-memory only. On multi-process deploys (gunicorn) counts are per-worker.
    This is acceptable for v0.2.0; a Redis-backed limiter is v0.3.0.
  - Threading: uses collections.deque with a threading.Lock per key.
    Deque approach: store timestamps of each call in a capped deque;
    drop entries older than the window; count remaining entries.
  - Never blocks the event loop — all operations are sync and fast (~μs).
"""
from __future__ import annotations

import threading
import time
import logging
from collections import defaultdict, deque
from typing import Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Public endpoint per-IP limits (unauthenticated traffic)
PUBLIC_WINDOW_SECONDS = 60
PUBLIC_DEFAULT_LIMIT = 20   # calls/min for most tools
PUBLIC_EXPENSIVE_LIMIT = 5  # calls/min for expensive tools

# Authenticated endpoint per-tool-name global limits (cheap protection)
AUTH_WINDOW_SECONDS = 60
AUTH_DEFAULT_LIMIT = 60     # calls/min total across all users per tool
AUTH_EXPENSIVE_LIMIT = 10   # calls/min for expensive tools

EXPENSIVE_TOOLS = frozenset({"traceer_motie", "vergelijk_partijen", "vat_dossier_samen"})


# ---------------------------------------------------------------------------
# Sliding-window counter
# ---------------------------------------------------------------------------


class _SlidingWindow:
    """Thread-safe sliding-window call counter for one (key, window) pair."""

    __slots__ = ("_lock", "_window", "_timestamps")

    def __init__(self, window_seconds: int) -> None:
        self._lock = threading.Lock()
        self._window = window_seconds
        self._timestamps: deque[float] = deque()

    def increment_and_check(self, limit: int) -> bool:
        """Record one call. Returns True if the call is allowed (within limit),
        False if it exceeds the limit and should be rejected."""
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            # Evict stale entries at the front
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            if len(self._timestamps) >= limit:
                return False
            self._timestamps.append(now)
            return True


# ---------------------------------------------------------------------------
# Starlette middleware for public endpoint
# ---------------------------------------------------------------------------


_ip_windows: dict[str, _SlidingWindow] = defaultdict(lambda: _SlidingWindow(PUBLIC_WINDOW_SECONDS))
_ip_lock = threading.Lock()


def _get_client_ip(request: Request) -> str:
    """Extract the real client IP, honouring X-Forwarded-For from kamal-proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP sliding-window middleware for the public /public/mcp endpoint.

    Limits are configured via PUBLIC_DEFAULT_LIMIT and PUBLIC_EXPENSIVE_LIMIT.
    Returns 429 JSON when exceeded.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        ip = _get_client_ip(request)

        # Extract tool name from the MCP request body when available.
        # For streamable-http transport the tool name appears in the JSON body;
        # we do a best-effort parse rather than fully decoding the body.
        tool_name: Optional[str] = None
        try:
            body_bytes = await request.body()
            import json as _json
            body = _json.loads(body_bytes)
            # MCP JSON-RPC: {"method": "tools/call", "params": {"name": "zoek_raadshistorie", ...}}
            if isinstance(body, dict) and body.get("method") == "tools/call":
                tool_name = (body.get("params") or {}).get("name")
        except Exception:
            pass

        limit = PUBLIC_EXPENSIVE_LIMIT if tool_name in EXPENSIVE_TOOLS else PUBLIC_DEFAULT_LIMIT
        key = f"{ip}:{tool_name or '_all'}"

        with _ip_lock:
            window = _ip_windows[key]

        allowed = window.increment_and_check(limit)
        if not allowed:
            logger.warning("rate_limit: IP=%s tool=%s limit=%d/min exceeded", ip, tool_name, limit)
            return JSONResponse(
                {"error": "Too many requests", "retry_after_seconds": PUBLIC_WINDOW_SECONDS},
                status_code=429,
                headers={"Retry-After": str(PUBLIC_WINDOW_SECONDS)},
            )

        return await call_next(request)


# ---------------------------------------------------------------------------
# Per-tool global limit (for logged_tool in mcp_server_v3.py)
# ---------------------------------------------------------------------------


_tool_windows: dict[str, _SlidingWindow] = defaultdict(lambda: _SlidingWindow(AUTH_WINDOW_SECONDS))
_tool_lock = threading.Lock()


def check_tool_rate_limit(tool_name: str) -> bool:
    """Check and increment the global per-tool rate limit.

    Returns True if the call is allowed, False if it exceeds the limit.
    Called from logged_tool BEFORE executing the tool.
    """
    limit = AUTH_EXPENSIVE_LIMIT if tool_name in EXPENSIVE_TOOLS else AUTH_DEFAULT_LIMIT
    with _tool_lock:
        window = _tool_windows[tool_name]
    return window.increment_and_check(limit)
