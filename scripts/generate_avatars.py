#!/usr/bin/env python3
"""Generate civic avatars via Gemini 2.5 Flash Image ("nanobanana").

Replaces the 8 hand-drawn SVG civic-archetype avatars with 20 diverse,
warm, editorial-illustration portraits in the locked NeoDemos palette.

Palette: cream #f4efe5, dark green #042825, orange accent #ff751f.
Style: flat-vector editorial illustration (The Economist / Oatmeal kit),
head-and-shoulders only, no props, no text, no stereotyping.

Usage:
    # Load .env first, or export manually
    export $(grep -v '^#' .env | xargs)
    python3 scripts/generate_avatars.py --dry-run
    python3 scripts/generate_avatars.py --count 20

Prereqs: pip install google-genai Pillow
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time
from dataclasses import dataclass


@dataclass
class AvatarSpec:
    slug: str
    label: str
    descriptor: str


# 20 varied specs. Descriptors are neutral, respectful, Rotterdam-representative.
# NO real names, NO caricature, NO religious overt markers unless natural dress.
AVATAR_SPECS: list[AvatarSpec] = [
    AvatarSpec("01-amira",    "Amira",    "30s Moroccan-Dutch woman with warm brown eyes, short dark curly hair, soft gentle smile"),
    AvatarSpec("02-jeroen",   "Jeroen",   "40s Dutch man with a friendly smile, light stubble beard, short blond hair, warm expression"),
    AvatarSpec("03-prem",     "Prem",     "50s Surinamese-Dutch man with close-cropped greying hair, kind thoughtful eyes, calm expression"),
    AvatarSpec("04-fatima",   "Fatima",   "20s Turkish-Dutch young woman with long dark hair, bright approachable smile, natural warm skin tone"),
    AvatarSpec("05-hendrik",  "Hendrik",  "60s elder Dutch man with silver hair, round wire glasses, gentle thoughtful expression, soft smile"),
    AvatarSpec("06-rashida",  "Rashida",  "40s Surinamese-Dutch woman with shoulder-length natural curls, warm confident smile, friendly eyes"),
    AvatarSpec("07-kees",     "Kees",     "30s Dutch man with glasses, short brown hair, modest beard, calm friendly neutral expression"),
    AvatarSpec("08-linh",     "Linh",     "20s Chinese-Dutch young woman with straight black hair in a low ponytail, soft smile, kind eyes"),
    AvatarSpec("09-naima",    "Naima",    "30s Moroccan-Dutch woman wearing a soft draped scarf in muted green, gentle approachable smile"),
    AvatarSpec("10-marcus",   "Marcus",   "50s Antillean-Dutch man with short greying hair, broad warm smile, relaxed friendly expression"),
    AvatarSpec("11-sanne",    "Sanne",    "30s Dutch woman with shoulder-length auburn hair, subtle smile, thoughtful warm expression"),
    AvatarSpec("12-aisha",    "Aisha",    "40s Somali-Dutch woman with a softly draped light scarf, calm confident gentle smile"),
    AvatarSpec("13-tarek",    "Tarek",    "20s Syrian-Dutch young man with short dark hair, neat beard, soft warm neutral-friendly expression"),
    AvatarSpec("14-elsbeth",  "Elsbeth",  "70s elder Dutch woman with short silver-white hair, small warm smile, kind crinkled eyes"),
    AvatarSpec("15-dewi",     "Dewi",     "30s Indonesian-Dutch woman with a short pixie cut, bright eyes, cheerful gentle smile"),
    AvatarSpec("16-joao",     "João",     "40s Cape Verdean-Dutch man with close-cropped hair, small neat beard, warm relaxed smile"),
    AvatarSpec("17-mateusz",  "Mateusz",  "30s Polish-Dutch man with short sandy-brown hair, light beard, thoughtful calm expression"),
    AvatarSpec("18-priya",    "Priya",    "40s Hindustani-Surinamese-Dutch woman with long dark hair pulled back, serene warm smile"),
    AvatarSpec("19-robin",    "Robin",    "20s androgynous young Dutch person with short tousled hair, gentle soft smile, warm inclusive look"),
    AvatarSpec("20-yusuf",    "Yusuf",    "60s elder Turkish-Dutch man with silver hair, neat silver beard, kind thoughtful eyes, soft smile"),
]


STYLE_CLAUSE = (
    "Friendly cute character portrait, warm and slightly whimsical, "
    "modern flat vector illustration with rounded soft shapes and gentle curves, "
    "style reminiscent of the Tailwind Plus Oatmeal kit mixed with the warmth of "
    "New Yorker spot illustrations — editorial but playful, never a caricature. "
    "Slightly exaggerated friendly features (a little bigger eyes, soft rounded face) "
    "but absolutely no stereotyping, no racial caricature, no grotesque exaggeration. "
    "Expressive, charming, human, lovable. "
    "Solid warm cream background (#f4efe5), dark forest green (#042825) for line-work "
    "and hair accents, occasional warm orange (#ff751f) accent on clothing only. "
    "Natural respectful skin tones, diverse, dignified. "
    "Square 1:1 composition, head-and-shoulders only, subject looking slightly off-camera, "
    "relaxed friendly approachable expression — think 'cute colleague' not 'serious pundit'. "
    "Absolutely no props, no civic objects, no gavel, no microphone, no ballot, no megaphone, "
    "no magnifier, no books, no text, no lettering, no logos, no borders. "
    "Just the face and shoulders on the solid cream background."
)


def build_prompt(descriptor: str) -> str:
    return (
        f"A warm editorial illustrated portrait of a {descriptor}. "
        f"{STYLE_CLAUSE}"
    )


def generate_one(client, spec: AvatarSpec, out_dir: pathlib.Path):
    from google.genai import types

    response = client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=[build_prompt(spec.descriptor)],
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )

    # Safety-filter / no-candidate guard
    if not getattr(response, "candidates", None):
        pf = getattr(response, "prompt_feedback", None)
        raise RuntimeError(f"no candidates (prompt_feedback={pf})")

    candidate = response.candidates[0]
    content = getattr(candidate, "content", None)
    if content is None or not getattr(content, "parts", None):
        fr = getattr(candidate, "finish_reason", None)
        raise RuntimeError(f"empty content (finish_reason={fr})")

    for part in content.parts:
        inline = getattr(part, "inline_data", None)
        if inline and getattr(inline, "data", None):
            data = inline.data
            mime = getattr(inline, "mime_type", None) or "image/png"
            ext = "webp" if "webp" in mime else ("jpg" if "jpeg" in mime else "png")
            out_path = out_dir / f"{spec.slug}.{ext}"
            out_path.write_bytes(data)
            return out_path

    raise RuntimeError("no inline_data image part in response")


def archive_svgs(out_dir: pathlib.Path, fallback: pathlib.Path) -> list[str]:
    fallback.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for svg in out_dir.glob("*.svg"):
        target = fallback / svg.name
        if not target.exists():
            svg.rename(target)
            moved.append(svg.name)
        else:
            svg.unlink()
            moved.append(f"{svg.name} (dup, deleted)")
    return moved


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sleep", type=float, default=1.0,
                        help="Seconds to sleep between calls")
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        sys.exit("ERROR: GEMINI_API_KEY not in env. Export it or source .env first.")

    from google import genai
    client = genai.Client(api_key=api_key)

    repo_root = pathlib.Path(__file__).parent.parent.resolve()
    out_dir = repo_root / "static" / "images" / "avatars"
    fallback = out_dir / "fallback"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Always archive existing SVGs to fallback/ so we have a safety net.
    moved = archive_svgs(out_dir, fallback)
    if moved:
        print(f"Archived {len(moved)} SVG(s) to {fallback.relative_to(repo_root)}/:")
        for name in moved:
            print(f"  - {name}")

    if args.dry_run:
        print("\nDRY RUN — would generate:")
        for s in AVATAR_SPECS[: args.count]:
            print(f"  {s.slug:14s} | {s.label:10s} | {s.descriptor[:70]}")
        print(f"\nPrompt sample for {AVATAR_SPECS[0].slug}:")
        print(build_prompt(AVATAR_SPECS[0].descriptor))
        return

    succeeded: list[tuple[AvatarSpec, pathlib.Path]] = []
    skipped: list[tuple[AvatarSpec, str]] = []

    for i, spec in enumerate(AVATAR_SPECS[: args.count], 1):
        print(f"[{i:2d}/{args.count}] {spec.slug} …", flush=True)
        try:
            path = generate_one(client, spec, out_dir)
            if path:
                size_kb = path.stat().st_size // 1024
                succeeded.append((spec, path))
                print(f"         ok {path.name} ({size_kb} KB)")
            else:
                skipped.append((spec, "no image returned"))
                print(f"         skip (no image)")
        except Exception as e:
            msg = str(e)[:200]
            skipped.append((spec, msg))
            print(f"         skip: {msg}")
        time.sleep(args.sleep)

    print(f"\nDone: {len(succeeded)}/{args.count} avatars generated, {len(skipped)} skipped")
    if skipped:
        print("\nSkipped:")
        for spec, reason in skipped:
            print(f"  - {spec.slug}: {reason}")

    if succeeded:
        print("\nSnippet for services/avatars.py AVATARS list:")
        print("AVATARS: list[dict] = [")
        for spec, path in succeeded:
            ext = path.suffix.lstrip(".")
            print(f'    {{"slug": "{spec.slug}", "label": "{spec.label}", "ext": "{ext}"}},')
        print("]")


if __name__ == "__main__":
    main()
