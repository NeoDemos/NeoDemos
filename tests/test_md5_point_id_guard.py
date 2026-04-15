"""CI guard: detect rogue MD5 point-ID literals outside services/embedding.py.

The canonical Qdrant point-ID formula lives in services/embedding.py::compute_point_id().
Any copy of that [:15] slice logic in another file re-introduces the Scheme-B divergence
that caused the 274K-orphan incident (2026-04-14).

Allowed files (cleanup scripts that explicitly reference the legacy formula by design):
  - scripts/repair_scheme_b_points.py
  - scripts/ws10_finalize_embeddings.py
"""

from __future__ import annotations

import re
from pathlib import Path

# Pattern: md5(...).hexdigest()[:15]  (any variant)
_PATTERN = re.compile(r"md5\(.+?\)\.hexdigest\(\)\[:15\]")

_ALLOWED = {
    "services/embedding.py",
    "scripts/repair_scheme_b_points.py",
    "scripts/ws10_finalize_embeddings.py",
    "tests/test_md5_point_id_guard.py",  # this file defines the pattern in a comment
}

_ROOT = Path(__file__).resolve().parents[1]


def _iter_py_files():
    for p in _ROOT.rglob("*.py"):
        # Skip venv, worktrees, archive, __pycache__
        parts = p.relative_to(_ROOT).parts
        if any(
            part in {".venv", "__pycache__", "worktrees", "archive"}
            for part in parts
        ):
            continue
        if ".claude" in parts:
            continue
        yield p


def test_no_rogue_md5_point_id_literals():
    """Fail if any .py file outside the allowed set contains [:15] MD5 slice for point IDs."""
    violations = []
    for path in _iter_py_files():
        rel = path.relative_to(_ROOT).as_posix()
        if rel in _ALLOWED:
            continue
        text = path.read_text(errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if _PATTERN.search(line):
                violations.append(f"{rel}:{lineno}: {line.strip()}")

    assert not violations, (
        "Rogue MD5 point-ID literal(s) found outside allowed files.\n"
        "Use compute_point_id() from services/embedding.py instead.\n\n"
        + "\n".join(violations)
    )
