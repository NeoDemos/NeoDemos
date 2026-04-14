"""
MCP tool-description uniqueness check (WS4 2026-04-11).

FactSet pattern: embed every tool's ai_description at startup and compute
pairwise cosine similarity. Two tools with very high cosine similarity
are likely to confuse the host LLM at tool-selection time.

Thresholds (recalibrated 2026-04-12 from FactSet defaults):
  - WARN at 0.92 (log warning, continue boot)
  - FAIL at 0.95 (raise RuntimeError, refuse to boot)

The original FactSet article used 0.85 for WARN, but that assumes tools
spanning diverse financial domains (market data, ESG, portfolio, news).
NeoDemos has 15 tools in a single Dutch municipal-politics domain — all
descriptions share "documenten", "vergaderingen", "partij", "raadslid"
etc. Empirical measurement shows 49/105 pairs > 0.85 even with well-
differentiated descriptions. Real LLM disambiguation relies on the
`get_neodemos_context()` primer + "Do NOT use when" cross-references,
not embedding distance alone.

Usage (in mcp_server_v3.py __main__ before mcp.run()):
    from services.mcp_tool_uniqueness import check_tool_uniqueness
    check_tool_uniqueness()
"""

from __future__ import annotations

import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

WARN_THRESHOLD = 0.92
FAIL_THRESHOLD = 0.95


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length float vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def check_tool_uniqueness(skip_if_no_embedder: bool = True) -> dict:
    """
    Embed every tool's ai_description and compute pairwise cosine similarity.
    Returns a dict {"warnings": [...], "failures": [...], "max_pair": (name_a, name_b, score), "checked": int}.

    Raises RuntimeError if any pair >= FAIL_THRESHOLD.
    Logs a WARNING for every pair >= WARN_THRESHOLD (and < FAIL_THRESHOLD).

    If skip_if_no_embedder=True and the embedder cannot be created (e.g. no
    NEBIUS_API_KEY in local dev), logs a notice and returns {} — does NOT raise.
    """
    try:
        from services.mcp_tool_registry import REGISTRY
    except Exception as e:
        logger.error("mcp_tool_uniqueness: failed to import tool registry: %s", e)
        return {}

    if not REGISTRY:
        logger.info("mcp_tool_uniqueness: registry empty, skipping check")
        return {}

    # Soft tool-budget cap — LLMs degrade with too many tools in context.
    # Le Chat / Cursor hard-cap at 40; keep NeoDemos <= 25 through v0.2.
    _TOOL_BUDGET_WARN = 25
    if len(REGISTRY) > _TOOL_BUDGET_WARN:
        logger.warning(
            "mcp_tool_uniqueness: registry has %d tools (> %d soft cap). "
            "Each tool eats 550-1400 context tokens. Merge or deprecate before adding more.",
            len(REGISTRY), _TOOL_BUDGET_WARN,
        )

    try:
        from services.embedding import create_embedder
        embedder = create_embedder()
    except Exception as e:
        if skip_if_no_embedder:
            logger.info(
                "mcp_tool_uniqueness: embedder unavailable (%s) — skipping tool-collision check",
                e,
            )
            return {}
        raise

    names = list(REGISTRY.keys())
    descriptions = [REGISTRY[n].ai_description for n in names]

    # Batch embed to amortize API latency.
    # LocalEmbedder (dev/MLX) only has .embed(), not .embed_batch().
    # Fall back to sequential calls when batch isn't available.
    if hasattr(embedder, "embed_batch"):
        vectors = embedder.embed_batch(descriptions, batch_size=64)
    else:
        logger.info(
            "mcp_tool_uniqueness: embedder lacks embed_batch — falling back to sequential embed()"
        )
        vectors = []
        for desc in descriptions:
            try:
                vectors.append(embedder.embed(desc))
            except Exception as _e:
                logger.warning("mcp_tool_uniqueness: embed() failed for one tool: %s", _e)
                vectors.append(None)

    # Guard against None entries (failed embeddings for some descriptions)
    valid_pairs = [(name, vec) for name, vec in zip(names, vectors) if vec is not None]
    if len(valid_pairs) < 2:
        logger.warning("mcp_tool_uniqueness: < 2 valid embeddings, cannot compare")
        return {}

    warnings_list: list[tuple[str, str, float]] = []
    failures: list[tuple[str, str, float]] = []
    max_pair: Optional[tuple[str, str, float]] = None

    for i in range(len(valid_pairs)):
        for j in range(i + 1, len(valid_pairs)):
            name_a, vec_a = valid_pairs[i]
            name_b, vec_b = valid_pairs[j]
            score = _cosine(vec_a, vec_b)
            if max_pair is None or score > max_pair[2]:
                max_pair = (name_a, name_b, score)
            if score >= FAIL_THRESHOLD:
                failures.append((name_a, name_b, score))
            elif score >= WARN_THRESHOLD:
                warnings_list.append((name_a, name_b, score))

    # Log every warning
    for name_a, name_b, score in warnings_list:
        logger.warning(
            "mcp_tool_uniqueness: tools '%s' and '%s' have cosine %.3f (> %.2f) — "
            "consider rewording descriptions to disambiguate",
            name_a, name_b, score, WARN_THRESHOLD,
        )

    # Fail boot on any >= FAIL_THRESHOLD
    if failures:
        pairs = ", ".join(f"{a}<->{b} ({s:.3f})" for a, b, s in failures)
        raise RuntimeError(
            f"mcp_tool_uniqueness: tool-description collision detected (>= {FAIL_THRESHOLD}): {pairs}. "
            f"Rename or reword one of the colliding tools before shipping."
        )

    if max_pair:
        logger.info(
            "mcp_tool_uniqueness: %d tools checked, max pair cosine = %.3f (%s <-> %s)",
            len(valid_pairs), max_pair[2], max_pair[0], max_pair[1],
        )

    return {
        "warnings": warnings_list,
        "failures": failures,
        "max_pair": max_pair,
        "checked": len(valid_pairs),
    }


if __name__ == "__main__":
    # Direct-run debugging entry point.
    # Usage: python -m services.mcp_tool_uniqueness
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    print("Running MCP tool-description uniqueness check...")
    print(f"  WARN_THRESHOLD = {WARN_THRESHOLD}")
    print(f"  FAIL_THRESHOLD = {FAIL_THRESHOLD}")
    print()

    try:
        result = check_tool_uniqueness(skip_if_no_embedder=True)
    except RuntimeError as e:
        print(f"FAIL: {e}")
        raise SystemExit(1)

    if not result:
        print("Check skipped (empty registry, missing embedder, or import error).")
        raise SystemExit(0)

    checked = result.get("checked", 0)
    warnings_list = result.get("warnings") or []
    failures = result.get("failures") or []
    max_pair = result.get("max_pair")

    print(f"Tools checked: {checked}")
    if max_pair:
        a, b, s = max_pair
        print(f"Max pair cosine: {s:.3f} ({a} <-> {b})")
    else:
        print("Max pair cosine: n/a")

    print()
    print(f"Warnings ({len(warnings_list)}) — pairs in [{WARN_THRESHOLD}, {FAIL_THRESHOLD}):")
    if warnings_list:
        for a, b, s in sorted(warnings_list, key=lambda p: p[2], reverse=True):
            print(f"  {s:.3f}  {a} <-> {b}")
    else:
        print("  (none)")

    print()
    print(f"Failures ({len(failures)}) — pairs >= {FAIL_THRESHOLD}:")
    if failures:
        for a, b, s in sorted(failures, key=lambda p: p[2], reverse=True):
            print(f"  {s:.3f}  {a} <-> {b}")
    else:
        print("  (none)")

    print()
    if failures:
        print("Result: FAIL (would refuse server boot)")
        raise SystemExit(1)
    elif warnings_list:
        print("Result: WARN (boot would continue, but disambiguate soon)")
    else:
        print("Result: OK")
