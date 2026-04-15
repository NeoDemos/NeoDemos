"""Deterministic user-avatar assignment.

Maps any stable user identifier (email, user id, anon session id) to one of
N Gemini-generated editorial portraits in static/images/avatars/. Same input
→ same avatar forever, so users see a consistent visual identity across
sessions.

Avatars are warm editorial illustrations of a diverse cast of imagined
civic participants — not real people — in the locked NeoDemos palette
(#042825 / #f4efe5 / #ff751f). Faces only (head-and-shoulders), no props,
no stereotyping.

When a generated portrait file is missing (e.g. pre-generation, or a
specific slug's API call failed), we transparently fall back to the
hand-drawn civic-archetype SVG set preserved in
`static/images/avatars/fallback/`.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

# Repo-relative path to the avatar directory on disk (for existence checks).
_AVATAR_DIR = Path(__file__).resolve().parent.parent / "static" / "images" / "avatars"
_FALLBACK_DIR = _AVATAR_DIR / "fallback"

# Extensions the generated portraits can land as (Gemini returns PNG by
# default; WebP possible if the model negotiates it). Checked in order.
_GENERATED_EXTS = ("webp", "png", "jpg", "jpeg")

# Ordered list — slug + display label (shown on Profiel page).
# Keep the slug stable forever; new avatars append to the END so hashes
# keep their meaning. Renaming a slug re-shuffles everyone's avatar.
#
# These 20 slugs correspond to scripts/generate_avatars.py AVATAR_SPECS.
# The labels are first names (imagined, not real council members).
AVATARS: list[dict] = [
    {"slug": "01-amira",    "label": "Amira"},
    {"slug": "02-jeroen",   "label": "Jeroen"},
    {"slug": "03-prem",     "label": "Prem"},
    {"slug": "04-fatima",   "label": "Fatima"},
    {"slug": "05-hendrik",  "label": "Hendrik"},
    {"slug": "06-rashida",  "label": "Rashida"},
    {"slug": "07-kees",     "label": "Kees"},
    {"slug": "08-linh",     "label": "Linh"},
    {"slug": "09-naima",    "label": "Naima"},
    {"slug": "10-marcus",   "label": "Marcus"},
    {"slug": "11-sanne",    "label": "Sanne"},
    {"slug": "12-aisha",    "label": "Aisha"},
    {"slug": "13-tarek",    "label": "Tarek"},
    {"slug": "14-elsbeth",  "label": "Elsbeth"},
    {"slug": "15-dewi",     "label": "Dewi"},
    {"slug": "16-joao",     "label": "João"},
    {"slug": "17-mateusz",  "label": "Mateusz"},
    {"slug": "18-priya",    "label": "Priya"},
    {"slug": "19-robin",    "label": "Robin"},
    {"slug": "20-yusuf",    "label": "Yusuf"},
]

# Legacy civic-archetype SVG slugs preserved in fallback/ — used if a
# generated portrait is missing. Hash maps into the full AVATARS list
# first; fallback is picked on a second hash if needed.
_FALLBACK_SLUGS: list[str] = [
    "01-laurel", "02-mic", "03-scales", "04-book",
    "05-megaphone", "06-magnifier", "07-column", "08-ballot",
]


def avatar_slug_for(seed: Optional[str]) -> str:
    """Return a stable avatar slug for a given seed (email / user id / session id)."""
    if not seed:
        return AVATARS[0]["slug"]
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    idx = int(digest, 16) % len(AVATARS)
    return AVATARS[idx]["slug"]


def _generated_file_for(slug: str) -> Optional[Path]:
    """Return the on-disk path to the generated portrait for slug, if any."""
    for ext in _GENERATED_EXTS:
        p = _AVATAR_DIR / f"{slug}.{ext}"
        if p.exists():
            return p
    return None


def _fallback_url_for(seed: Optional[str]) -> str:
    """Pick a deterministic SVG from the legacy civic-archetype set."""
    if not seed:
        slug = _FALLBACK_SLUGS[0]
    else:
        digest = hashlib.md5(f"fb:{seed}".encode("utf-8")).hexdigest()
        slug = _FALLBACK_SLUGS[int(digest, 16) % len(_FALLBACK_SLUGS)]
    # Fallback SVGs live in static/images/avatars/fallback/
    return f"/static/images/avatars/fallback/{slug}.svg"


def avatar_url_for(seed: Optional[str]) -> str:
    """Return the public /static/ URL for the avatar assigned to seed.

    Prefers the Gemini-generated portrait at the seed's slug; if that file
    is not present (e.g. pre-generation or API failure for this slug),
    transparently falls back to one of the legacy civic-archetype SVGs.
    """
    slug = avatar_slug_for(seed)
    path = _generated_file_for(slug)
    if path is not None:
        return f"/static/images/avatars/{path.name}"
    return _fallback_url_for(seed)


def is_valid_slug(slug: Optional[str]) -> bool:
    """Whitelist guard: only accept slugs we actually ship in AVATARS."""
    if not slug:
        return False
    return any(a["slug"] == slug for a in AVATARS)


def avatar_url_for_slug(slug: Optional[str]) -> Optional[str]:
    """Return the public /static/ URL for a specific slug, or None if the
    slug is unknown or its on-disk file is missing.
    """
    if not is_valid_slug(slug):
        return None
    path = _generated_file_for(slug)
    if path is None:
        return None
    return f"/static/images/avatars/{path.name}"


def user_avatar_url(user: Optional[dict]) -> str:
    """Resolve the avatar URL for a user dict.

    Priority:
      1. user['avatar_slug'] if set AND the slug is valid AND the portrait
         file exists on disk.
      2. Deterministic hash of user['email'] (falls back to user['id']).
      3. Legacy civic-archetype SVG (via avatar_url_for's internal fallback).

    Safe to call with None / missing keys — always returns a usable URL.
    """
    if not user:
        return avatar_url_for(None)
    slug = user.get("avatar_slug") if isinstance(user, dict) else None
    picked = avatar_url_for_slug(slug)
    if picked:
        return picked
    seed = None
    if isinstance(user, dict):
        seed = user.get("email") or (str(user.get("id")) if user.get("id") is not None else None)
    return avatar_url_for(seed)


def avatar_gallery() -> list[dict]:
    """All available avatars — each {slug, label, url}. For the Profiel picker.

    Each entry's url reflects what is actually on disk: the generated
    portrait if present, otherwise a fallback SVG (derived from the slug).
    """
    gallery: list[dict] = []
    for i, a in enumerate(AVATARS):
        path = _generated_file_for(a["slug"])
        if path is not None:
            url = f"/static/images/avatars/{path.name}"
        else:
            # Stable fallback per index so the picker still shows 20 tiles.
            fb = _FALLBACK_SLUGS[i % len(_FALLBACK_SLUGS)]
            url = f"/static/images/avatars/fallback/{fb}.svg"
        gallery.append({"slug": a["slug"], "label": a["label"], "url": url})
    return gallery
