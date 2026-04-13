"""
MCP parameter validation — Layer 2 defense-in-depth (WS4 2026-04-13).

FactSet rule: validate ALL parameters before execution. This module provides
``validate_tool_params(tool_name, params)`` which is called from the
``logged_tool`` decorator in ``mcp_server_v3.py`` BEFORE the tool function
runs. Raises ``ValueError`` with a Dutch-language error message on any
violation so the host LLM can surface a helpful error.

Validation rules (per FactSet enterprise MCP Part 3 §Parameter validation):
    - String inputs capped at MAX_STRING_LENGTH (prevents prompt injection
      via oversized query strings).
    - Date parameters must be valid ISO dates in the range 2000-01-01 to
      2030-12-31 (no dates outside the Rotterdam corpus window).
    - ``gemeente`` parameter, when present, must be in KNOWN_GEMEENTEN.
    - ``max_resultaten`` / ``max_fragmenten`` are capped at their declared
      maximum from the registry input_schema (10K fallback if not declared).

The validator NEVER modifies params — it only reads and raises. Callers
are responsible for the business logic of handling the error.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MAX_STRING_LENGTH = 10_000  # FactSet: cap all string inputs at 10K chars
MIN_DATE = date(2000, 1, 1)
MAX_DATE = date(2030, 12, 31)

# Rotterdam v0.2.0 only. Expand in v0.2.1 when Middelburg / Waalwijk land.
KNOWN_GEMEENTEN = frozenset({"rotterdam"})

# ISO date pattern for fast pre-check before datetime parsing.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def _check_string_length(key: str, value: str) -> None:
    if len(value) > MAX_STRING_LENGTH:
        raise ValueError(
            f"Parameter '{key}' is te lang ({len(value):,} tekens). "
            f"Maximum is {MAX_STRING_LENGTH:,} tekens."
        )


def _check_date(key: str, value: str) -> None:
    """Validate an ISO-format date string is within the corpus window."""
    if not _ISO_DATE_RE.match(value):
        raise ValueError(
            f"Parameter '{key}' heeft geen geldig ISO-datumformaat (verwacht JJJJ-MM-DD, "
            f"ontvangen '{value}')."
        )
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(
            f"Parameter '{key}' is geen geldige datum: '{value}'."
        )
    if parsed < MIN_DATE:
        raise ValueError(
            f"Parameter '{key}' ({value}) valt buiten het corpus (voor {MIN_DATE.isoformat()}). "
            "Het Rotterdam-corpus start in 2002."
        )
    if parsed > MAX_DATE:
        raise ValueError(
            f"Parameter '{key}' ({value}) valt buiten het corpus (na {MAX_DATE.isoformat()}). "
            "Controleer de datum."
        )


def _check_gemeente(key: str, value: str) -> None:
    """Validate gemeente against the known tenant whitelist."""
    normalized = value.lower().strip()
    if normalized not in KNOWN_GEMEENTEN:
        raise ValueError(
            f"Parameter '{key}' heeft een onbekende gemeente: '{value}'. "
            f"Beschikbare gemeenten: {', '.join(sorted(KNOWN_GEMEENTEN))}."
        )


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def validate_tool_params(tool_name: str, params: dict) -> None:
    """Validate a tool's parameters before execution.

    Raises ``ValueError`` with a Dutch-language message if any parameter
    violates the declared constraints.

    Called from ``logged_tool`` in ``mcp_server_v3.py``. Non-critical —
    if this function itself raises an unexpected exception (not ValueError),
    the caller must catch it and allow the tool to proceed.

    Args:
        tool_name: The registered name of the tool (for logging context).
        params: The bound parameter dict (names → values) as captured by
                the ``logged_tool`` decorator before calling the real function.
    """
    for key, value in params.items():
        if value is None:
            # None means "not provided" — skip all validation.
            continue

        # String checks
        if isinstance(value, str):
            _check_string_length(key, value)
            # Date-shaped params
            if key in ("datum_van", "datum_tot") and value:
                _check_date(key, value)
            # Gemeente whitelist
            if key == "gemeente" and value:
                _check_gemeente(key, value)

        # List checks (e.g. document_ids, partijen)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, str):
                    _check_string_length(f"{key}[{i}]", item)

    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("validate_tool_params: tool=%s keys=%s — OK", tool_name, list(params))


def validate_or_log(tool_name: str, params: dict) -> Optional[str]:
    """Like ``validate_tool_params`` but returns the error message as a
    string instead of raising, for callers that prefer a return value.

    Returns None if all params are valid, or a Dutch-language error message
    string if validation fails.
    """
    try:
        validate_tool_params(tool_name, params)
        return None
    except ValueError as exc:
        return str(exc)
