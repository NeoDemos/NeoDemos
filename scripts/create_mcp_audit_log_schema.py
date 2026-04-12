#!/usr/bin/env python3
"""
Schema: mcp_audit_log — Audit trail for every MCP tool invocation.

Records metadata about each tool call (user, tool name, latency, size,
status, error class) for the WS4 MCP discipline workstream. Raw params
are NEVER stored — only a sha256 hash. Raw results are NEVER stored —
only byte size.

See docs/handoffs/WS4_MCP_DISCIPLINE.md §Audit log for the canonical
schema definition and rules.

Usage:
    python scripts/create_mcp_audit_log_schema.py
"""

import logging
import sys

from services.db_pool import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


DDL = """
CREATE TABLE IF NOT EXISTS mcp_audit_log (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ DEFAULT NOW(),
    user_id TEXT,                          -- from OAuth subject or "api_token:<id>" or "anonymous"
    api_token_id INTEGER,                  -- FK to api_tokens.id when applicable, NULL for OAuth
    tool_name TEXT NOT NULL,
    params_hash TEXT,                      -- sha256 of json-serialized params, never raw params
    scope_used TEXT[],
    latency_ms INTEGER,
    result_size_bytes INTEGER,
    status_code INTEGER,                   -- 200 success, 4xx client error, 5xx server error
    ip TEXT,
    error_class TEXT                       -- e.g. "empty_chunk", "snippet_provenance_mismatch", "rate_limited"
);

CREATE INDEX IF NOT EXISTS mcp_audit_log_ts_idx
    ON mcp_audit_log (ts DESC);
CREATE INDEX IF NOT EXISTS mcp_audit_log_user_ts_idx
    ON mcp_audit_log (user_id, ts DESC);
CREATE INDEX IF NOT EXISTS mcp_audit_log_tool_ts_idx
    ON mcp_audit_log (tool_name, ts DESC);
"""


def main() -> None:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
        print("Created mcp_audit_log table and indexes")
    except Exception as exc:
        logger.exception("Failed to create mcp_audit_log schema: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
