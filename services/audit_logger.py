"""
MCP audit logger — write-through INSERT into mcp_audit_log.

Used by the MCP server's logged_tool decorator (see mcp_server_v3.py)
to record every tool invocation. Belongs to WS4 MCP discipline.

CRITICAL RULES (see docs/handoffs/WS4_MCP_DISCIPLINE.md §Audit log):
  * NEVER log raw param values — only a sha256 hash via _hash_params.
  * NEVER log raw results — only the byte size of the stringified result.
  * NEVER crash the tool — audit failure must fall back to logger.exception
    and return. The tool call must always succeed regardless of audit state.
  * Must complete in <5ms p50 (per WS4 eval gate). Simple INSERT, no ORM,
    no JOINs, no batching. Write-through.
  * Stand-alone module — do NOT import from mcp_server_v3.py (circular risk).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from services.db_pool import get_connection

logger = logging.getLogger(__name__)


_INSERT_SQL = """
    INSERT INTO mcp_audit_log (
        user_id,
        api_token_id,
        tool_name,
        params_hash,
        scope_used,
        latency_ms,
        result_size_bytes,
        status_code,
        ip,
        error_class
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _hash_params(params: dict) -> str:
    """sha256 of json-serialized params — NEVER log raw params."""
    try:
        serialized = json.dumps(params, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    except Exception:
        return "unhashable"


def log_tool_call(
    tool_name: str,
    params: dict,
    latency_ms: int,
    result_size_bytes: int,
    status_code: int = 200,
    user_id: Optional[str] = None,
    api_token_id: Optional[int] = None,
    scope_used: Optional[list[str]] = None,
    ip: Optional[str] = None,
    error_class: Optional[str] = None,
) -> None:
    """
    Log a single MCP tool call to the mcp_audit_log table.
    Never raises — audit logging must not crash the tool. On DB failure,
    falls back to logging.error with the structured context.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _INSERT_SQL,
                    (
                        user_id,
                        api_token_id,
                        tool_name,
                        _hash_params(params),
                        scope_used,
                        latency_ms,
                        result_size_bytes,
                        status_code,
                        ip,
                        error_class,
                    ),
                )
    except Exception:
        logger.exception(
            "mcp_audit_log insert failed — tool=%s status=%s",
            tool_name,
            status_code,
        )
        return


def audit_log_sync(
    tool_name: str,
    params: dict,
    result: Any,
    latency_ms: int,
    user_id: Optional[str] = None,
    error: Optional[Exception] = None,
) -> None:
    """
    Convenience wrapper — computes result_size_bytes and status_code from
    the result/exception and delegates to log_tool_call.
    """
    if error is not None:
        result_size = 0
        status = 500
        error_class: Optional[str] = type(error).__name__
    else:
        result_size = len(str(result).encode("utf-8")) if result is not None else 0
        status = 200
        error_class = None
    log_tool_call(
        tool_name=tool_name,
        params=params,
        latency_ms=latency_ms,
        result_size_bytes=result_size,
        status_code=status,
        user_id=user_id,
        error_class=error_class,
    )


def flush() -> None:
    """
    No-op flush hook. Write-through for now; may become a buffered
    flush in v0.3 if the <5ms p50 gate fails under load.
    """
    return None
