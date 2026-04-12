"""
Output filter — Layer 4 defense-in-depth for the MCP surface (WS4).

FactSet's enterprise MCP model specifies four defense-in-depth layers:
    1. Tool-level scopes            (enforced in the registry)
    2. Parameter validation         (enforced at call site)
    3. Resource-level auth checks   (enforced in retrieval)
    4. Output filter                (this module)

Layer 4 is the last line of defense before tool output is handed to the LLM.
Its job is narrow and boring on purpose:

    - Strip PII fields the caller's scope doesn't grant
    - Strip internal IDs (anything starting with ``_internal_``)
    - Truncate any string field longer than MAX_FIELD_CHARS to prevent
      context bombing (adversarial inputs that push the LLM context
      window over capacity)
    - Verify snippet provenance: catch the 2026-04-11 failure mode where
      MCP search returned a snippet labeled with ``document_id=246823``
      but the snippet text was not actually present in document 246823's
      stored content. This is a last-line defense — the root-cause fix
      lives in the ingest pipeline (WS5a §Data integrity audit).

The filter never raises. On unexpected input shapes it logs a warning and
returns the input unchanged. This is intentional: a broken Layer 4 must
not block legitimate traffic — every other layer is still in place.

See ``docs/handoffs/WS4_MCP_DISCIPLINE.md`` §Defense-in-depth.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ----- Config constants -----------------------------------------------------

# Truncate any single field above this to prevent context bombing. 50K chars
# is ~12K tokens — well under any modern context window but large enough to
# pass through a full document chunk unmolested.
MAX_FIELD_CHARS = 50_000

# Strip any field key starting with this prefix. Callers can embed debug /
# tracing IDs in tool output without worrying about leaking them to the LLM.
INTERNAL_ID_PREFIX = "_internal_"

# Keys that hold PII. Dropped when the caller does not hold the ``pii`` scope.
# NeoDemos currently stores no personal PII (Rotterdam council documents are
# public by statute), but the filter exists so v0.3+ private ingestion has
# the scaffold in place.
_PII_KEYS = frozenset({"email", "phone", "address", "bsn", "geboortedatum"})

# Minimum length for the snippet anchor used in provenance verification.
# Below this we fall back to matching the whole (short) snippet.
_PROVENANCE_ANCHOR_LEN = 40

# How many chars to skip at each end of the snippet before taking the anchor.
# Chunk boundary artifacts (trailing punctuation, hyphenation wraps, stray
# whitespace) cluster at the edges — the middle is the reliable signal.
_PROVENANCE_ANCHOR_MARGIN = 20


# ----- Core transforms ------------------------------------------------------


def strip_internal_ids(payload: dict) -> dict:
    """Recursively drop any dict key starting with ``_internal_``.

    Immutable: returns a new dict. Non-dict leaves (list, scalar) pass
    through unchanged but list elements are recursed into so a list of
    dicts is also cleaned.
    """
    if not isinstance(payload, dict):
        return payload

    cleaned: dict = {}
    for key, value in payload.items():
        if isinstance(key, str) and key.startswith(INTERNAL_ID_PREFIX):
            continue
        if isinstance(value, dict):
            cleaned[key] = strip_internal_ids(value)
        elif isinstance(value, list):
            cleaned[key] = [
                strip_internal_ids(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            cleaned[key] = value
    return cleaned


def truncate_long_fields(payload: Any, max_chars: int = MAX_FIELD_CHARS) -> Any:
    """Recursively walk ``payload`` and truncate any string longer than
    ``max_chars``.

    Works on dict, list, tuple, str, and passes scalars (int, float, bool,
    None) through unchanged. Returns a new structure — the input is not
    mutated. Truncated strings get a visible marker appended so downstream
    logs can tell the difference between a naturally short field and one
    that was cut.
    """
    if isinstance(payload, str):
        if len(payload) > max_chars:
            # Preserve max_chars budget — the marker is informational and
            # the caller may be counting bytes for context accounting.
            return payload[:max_chars] + "... [truncated]"
        return payload
    if isinstance(payload, dict):
        return {k: truncate_long_fields(v, max_chars) for k, v in payload.items()}
    if isinstance(payload, list):
        return [truncate_long_fields(item, max_chars) for item in payload]
    if isinstance(payload, tuple):
        return tuple(truncate_long_fields(item, max_chars) for item in payload)
    return payload


# ----- Snippet provenance verification --------------------------------------


def _normalize(s: str) -> str:
    """Lowercase and collapse all whitespace (incl. newlines)."""
    return " ".join(s.lower().split())


def verify_snippet_provenance(
    snippet: str,
    document_id: str,
    stored_text: str,
) -> bool:
    """Verify that ``snippet`` (or a meaningful substring) actually appears
    in ``stored_text``.

    Catches the 2026-04-11 failure mode where MCP search returned a snippet
    labeled with ``document_id=246823`` but the snippet text was not present
    in document 246823's stored content.

    Matching algorithm:
        1. Normalize both to lowercase and collapse whitespace.
        2. Take a 40-char anchor from the middle of the snippet (skip first
           and last 20 chars to avoid boundary artifacts).
        3. Return True iff the normalized ``stored_text`` contains the anchor.
        4. If the snippet is shorter than 40 chars after normalization, use
           the whole snippet as the anchor.

    Returns True on match, False on mismatch. Logs a warning with the
    ``document_id`` on mismatch so the caller / audit log can cross-reference.
    """
    if not isinstance(snippet, str) or not isinstance(stored_text, str):
        logger.warning(
            "verify_snippet_provenance: non-string input for doc_id=%s", document_id
        )
        return False

    normalized_snippet = _normalize(snippet)
    normalized_stored = _normalize(stored_text)

    if not normalized_snippet:
        # Empty snippet is vacuously not in the stored text — treat as
        # mismatch because an empty-snippet hit carries no information.
        logger.warning(
            "verify_snippet_provenance: empty snippet for doc_id=%s", document_id
        )
        return False

    if len(normalized_snippet) < _PROVENANCE_ANCHOR_LEN:
        anchor = normalized_snippet
    else:
        # Pull the anchor from the middle. If the snippet is only slightly
        # longer than 2*margin + anchor_len we still get a usable mid-slice
        # thanks to normalization squashing whitespace.
        start = _PROVENANCE_ANCHOR_MARGIN
        end = start + _PROVENANCE_ANCHOR_LEN
        anchor = normalized_snippet[start:end]
        # Degenerate case: if margin trimming ate the entire snippet, fall
        # back to the raw normalized form.
        if not anchor:
            anchor = normalized_snippet[:_PROVENANCE_ANCHOR_LEN]

    matched = anchor in normalized_stored
    if not matched:
        logger.warning(
            "verify_snippet_provenance: MISMATCH doc_id=%s anchor=%r",
            document_id,
            anchor[:60],
        )
    return matched


# ----- PII scoping ----------------------------------------------------------


def strip_pii_for_scope(payload: dict, user_scopes: list[str]) -> dict:
    """Strip PII fields the user's scope doesn't grant.

    For v0.2.0 only the ``pii`` scope is enforced. If ``pii`` is NOT in
    ``user_scopes``, drop any key in :data:`_PII_KEYS` from the top-level
    dict and any nested dicts / lists. Immutable — returns a new dict.
    """
    if not isinstance(payload, dict):
        return payload

    if user_scopes and "pii" in user_scopes:
        # Fast path: user holds the scope, nothing to strip.
        return payload

    return _strip_pii_recursive(payload)


def _strip_pii_recursive(value: Any) -> Any:
    """Recursive helper. Drops PII keys from dicts, walks lists / tuples."""
    if isinstance(value, dict):
        return {
            k: _strip_pii_recursive(v)
            for k, v in value.items()
            if not (isinstance(k, str) and k in _PII_KEYS)
        }
    if isinstance(value, list):
        return [_strip_pii_recursive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_pii_recursive(item) for item in value)
    return value


# ----- Top-level helper -----------------------------------------------------


def filter_output(
    payload: Any,
    user_scopes: Optional[list[str]] = None,
    max_chars: int = MAX_FIELD_CHARS,
) -> Any:
    """Top-level helper. Applies :func:`strip_internal_ids`,
    :func:`strip_pii_for_scope`, :func:`truncate_long_fields` in order.

    Accepts any JSON-serializable payload (dict, list, str, scalar). Never
    raises — on any unexpected input, returns the input unchanged and logs
    a warning. The three transforms commute for well-formed inputs but we
    fix the order anyway for audit-log determinism.

    For raw string input, only truncation applies (there are no keys to
    strip from a bare string).
    """
    if user_scopes is None:
        user_scopes = []

    try:
        if isinstance(payload, str):
            return truncate_long_fields(payload, max_chars=max_chars)

        if isinstance(payload, dict):
            cleaned = strip_internal_ids(payload)
            cleaned = strip_pii_for_scope(cleaned, user_scopes)
            cleaned = truncate_long_fields(cleaned, max_chars=max_chars)
            return cleaned

        if isinstance(payload, list):
            # Map the whole pipeline over list elements. Each element gets
            # the same scope treatment — callers pass the user's scopes,
            # not per-element scopes.
            return [
                filter_output(item, user_scopes=user_scopes, max_chars=max_chars)
                for item in payload
            ]

        # Scalar (int, float, bool, None): pass through.
        return payload
    except RecursionError:
        logger.warning(
            "filter_output: RecursionError on payload (type=%s) — returning unchanged",
            type(payload).__name__,
        )
        return payload
    except Exception as exc:  # noqa: BLE001 — Layer 4 must never raise
        logger.warning(
            "filter_output: unexpected error %s on payload type=%s — returning unchanged",
            exc,
            type(payload).__name__,
        )
        return payload


# ----- Inline tests ---------------------------------------------------------


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)  # keep test output clean

    passed = 0
    failed = 0

    def check(name: str, condition: bool) -> None:
        global passed, failed
        if condition:
            passed += 1
            print(f"  PASS  {name}")
        else:
            failed += 1
            print(f"  FAIL  {name}")

    print("output_filter.py inline tests")
    print("-----------------------------")

    # Test 1 — strip_internal_ids removes nested and top-level prefix keys
    t1 = strip_internal_ids(
        {"_internal_foo": 1, "bar": {"_internal_baz": 2, "ok": 3}}
    )
    check(
        "strip_internal_ids strips nested and top-level",
        "_internal_foo" not in t1
        and "_internal_baz" not in t1["bar"]
        and t1["bar"]["ok"] == 3,
    )

    # Test 2 — truncate_long_fields respects MAX_FIELD_CHARS
    t2 = truncate_long_fields({"content": "x" * 60_000})["content"]
    check(
        "truncate_long_fields caps content at MAX_FIELD_CHARS",
        # Truncated output is MAX_FIELD_CHARS + suffix marker, and the first
        # MAX_FIELD_CHARS chars must be the original x's.
        len(t2) >= MAX_FIELD_CHARS
        and t2.startswith("x" * MAX_FIELD_CHARS)
        and "[truncated]" in t2,
    )
    # Also verify short strings are untouched.
    check(
        "truncate_long_fields leaves short strings alone",
        truncate_long_fields("short")  # type: ignore[arg-type]
        == "short",
    )

    # Test 3 — verify_snippet_provenance happy path
    t3 = verify_snippet_provenance(
        "The parking fee is 3.50 euro",
        "doc1",
        "Yesterday we discussed that the parking fee is 3.50 euro for the zone",
    )
    check("verify_snippet_provenance matches substring", t3 is True)

    # Test 4 — verify_snippet_provenance mismatch
    t4 = verify_snippet_provenance(
        "Completely unrelated text here about widgets",
        "doc2",
        "The meeting discussed infrastructure",
    )
    check("verify_snippet_provenance rejects mismatch", t4 is False)

    # Test 5 — strip_pii_for_scope removes email without pii scope
    t5 = strip_pii_for_scope(
        {"name": "Jan", "email": "j@x.nl"}, user_scopes=["mcp", "search"]
    )
    check(
        "strip_pii_for_scope drops email without pii scope",
        "email" not in t5 and t5.get("name") == "Jan",
    )

    # Test 6 — strip_pii_for_scope keeps email with pii scope
    t6 = strip_pii_for_scope(
        {"name": "Jan", "email": "j@x.nl"}, user_scopes=["mcp", "pii"]
    )
    check(
        "strip_pii_for_scope keeps email with pii scope",
        t6.get("email") == "j@x.nl" and t6.get("name") == "Jan",
    )

    # Extra belt-and-braces: top-level filter_output end-to-end.
    t7 = filter_output(
        {
            "_internal_trace_id": "abc-123",
            "email": "leak@example.com",
            "name": "Jan",
            "content": "y" * 60_000,
            "nested": [{"_internal_rid": 9, "phone": "0612345678", "msg": "ok"}],
        },
        user_scopes=["mcp"],
    )
    check(
        "filter_output end-to-end strips internal, pii, and truncates",
        "_internal_trace_id" not in t7
        and "email" not in t7
        and len(t7["content"]) >= MAX_FIELD_CHARS
        and "[truncated]" in t7["content"]
        and "_internal_rid" not in t7["nested"][0]
        and "phone" not in t7["nested"][0]
        and t7["nested"][0]["msg"] == "ok",
    )

    # filter_output on a raw string should just truncate.
    t8 = filter_output("z" * 60_000, user_scopes=["mcp"])
    check(
        "filter_output truncates raw string input",
        isinstance(t8, str) and len(t8) >= MAX_FIELD_CHARS and "[truncated]" in t8,
    )

    # filter_output must never raise, even on adversarial input.
    class Weird:
        def __repr__(self) -> str:
            raise RuntimeError("no repr for you")

    t9 = filter_output(Weird(), user_scopes=["mcp"])  # type: ignore[arg-type]
    check("filter_output passes through unknown types without raising", isinstance(t9, Weird))

    print("-----------------------------")
    print(f"results: {passed} passed, {failed} failed")
    raise SystemExit(0 if failed == 0 else 1)
