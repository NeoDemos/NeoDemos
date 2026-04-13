"""
MCP Tools — Internal Python Interface
--------------------------------------
Safe-import bridge: exposes MCP tool functions as an async-callable
TOOL_DISPATCH dict for use by WebIntelligenceService (WS9).

Architecture: imports the @logged_tool decorated functions from
mcp_server_v3.py and wraps them with asyncio.to_thread() since
all MCP tools are synchronous. No mcp_server_v3.py refactoring
needed — the FastMCP server object is created at import time but
never started (mcp.run() is guarded by __name__ == '__main__').

WS9 — Web Intelligence: MCP-as-Backend via Sonnet + Tool Use
"""

import asyncio
import logging
from typing import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy import: mcp_server_v3 creates a FastMCP() instance at module level.
# We import lazily to avoid side effects during app startup if the module
# hasn't been loaded yet.
# ---------------------------------------------------------------------------

_mcp_module = None


def _get_mcp_module():
    global _mcp_module
    if _mcp_module is None:
        import mcp_server_v3
        _mcp_module = mcp_server_v3
    return _mcp_module


# ---------------------------------------------------------------------------
# Tool names — all 18 @logged_tool functions exposed to Sonnet.
# get_neodemos_context is handled separately (injected into system prompt).
# ---------------------------------------------------------------------------

TOOL_NAMES = [
    "zoek_raadshistorie",
    "zoek_financieel",
    "zoek_uitspraken",
    "haal_vergadering_op",
    "lijst_vergaderingen",
    "tijdlijn_besluitvorming",
    "analyseer_agendapunt",
    "haal_partijstandpunt_op",
    "zoek_moties",
    "scan_breed",
    "lees_fragment",
    "vat_document_samen",
    "zoek_gerelateerd",
    "zoek_uitspraken_op_rol",
    "traceer_motie",
    "vergelijk_partijen",
    "vraag_begrotingsregel",
    "vergelijk_begrotingsjaren",
]


def _get_tool_fn(name: str) -> Callable:
    """Get the decorated function object from mcp_server_v3."""
    mod = _get_mcp_module()
    fn = getattr(mod, name, None)
    if fn is None:
        raise AttributeError(f"Tool '{name}' not found in mcp_server_v3")
    return fn


async def call_tool(name: str, **kwargs) -> str:
    """
    Call an MCP tool by name with keyword arguments, async.

    All MCP tools are synchronous — this wraps them in asyncio.to_thread()
    to avoid blocking the event loop. Returns the tool's string output.
    """
    fn = _get_tool_fn(name)
    try:
        result = await asyncio.to_thread(fn, **kwargs)
        return str(result) if result is not None else ""
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}", exc_info=True)
        raise


def get_neodemos_context() -> str:
    """
    Call get_neodemos_context synchronously (for system prompt injection).
    Returns the context primer markdown string.
    """
    mod = _get_mcp_module()
    fn = getattr(mod, "get_neodemos_context", None)
    if fn is None:
        return ""
    try:
        return fn() or ""
    except Exception as e:
        logger.warning(f"get_neodemos_context failed: {e}")
        return ""


# ---------------------------------------------------------------------------
# TOOL_DISPATCH: name → async callable for WebIntelligenceService
# ---------------------------------------------------------------------------

async def _make_tool_caller(name: str, **kwargs) -> str:
    return await call_tool(name, **kwargs)


# Build dispatch dict: each value is an async function accepting **kwargs
TOOL_DISPATCH: dict[str, Callable] = {}

for _name in TOOL_NAMES:
    # Create a closure to capture the tool name
    def _make_dispatch(tool_name: str):
        async def _dispatch(**kwargs) -> str:
            return await call_tool(tool_name, **kwargs)
        _dispatch.__name__ = tool_name
        return _dispatch
    TOOL_DISPATCH[_name] = _make_dispatch(_name)
