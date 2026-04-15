"""Per-query cost tracking for AI endpoints.

Writes one row per completed (or capped) AI request to ``ai_usage_events``.
Used later for optimisation + subscription-tier pricing calibration.

Fire-and-forget — logging failure must NEVER fail the user's request.
"""
from __future__ import annotations

import json
import logging
from typing import Optional, Sequence

import psycopg2.extras

from services.db_pool import get_connection

logger = logging.getLogger(__name__)


def record_ai_usage(
    *,
    user_id: Optional[int],
    session_id: Optional[str],
    ip: Optional[str],
    endpoint: str,
    model: Optional[str],
    query: Optional[str],
    tools_called: Optional[Sequence[str]],
    rounds: Optional[int],
    capped: bool,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    cost_usd: Optional[float],
    latency_ms: Optional[int],
    status: str,
    attached_context: Optional[dict] = None,
) -> None:
    """Insert one row into ai_usage_events. Silently swallows all errors."""
    try:
        preview = (query or "")[:280]
        q_len = len(query) if query else None
        ctx_json = json.dumps(attached_context) if attached_context else None
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ai_usage_events (
                      user_id, session_id, ip, endpoint, model,
                      query_preview, query_length,
                      tools_called, rounds, capped,
                      input_tokens, output_tokens, cost_usd, latency_ms,
                      status, attached_context
                    ) VALUES (
                      %s, %s, %s, %s, %s,
                      %s, %s,
                      %s, %s, %s,
                      %s, %s, %s, %s,
                      %s, %s
                    )
                    """,
                    (
                        user_id, session_id, ip, endpoint, model,
                        preview, q_len,
                        list(tools_called) if tools_called else None,
                        rounds, capped,
                        input_tokens, output_tokens, cost_usd, latency_ms,
                        status, ctx_json,
                    ),
                )
                conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"usage_tracker insert failed (non-fatal): {e}")
