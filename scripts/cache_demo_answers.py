"""
cache_demo_answers.py — Pre-render demo answers for the landing page.

Runs the most compelling queries through the full AI pipeline and saves
the results to data/demo_cache.json. The landing page loads this at startup
and rotates through the answers without any API cost per page load.

Usage:
    python scripts/cache_demo_answers.py

Outputs:
    data/demo_cache.json   — array of {question, answer, sources, cached_at}

Run this whenever you want to refresh the demo content. Typical cadence:
after major data ingests or when the current answer feels stale.
"""

import asyncio
import json
import sys
import os
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from services.ai_service import AIService
from services.storage import StorageService

# ── Queries to pre-render ──────────────────────────────────────────────────
# Each becomes a demo card on the landing page. Ordered by priority:
# 1 = primary launch demo (shown first), rest rotate.
DEMO_QUERIES = [
    {
        "id": "beloftes",
        "question": "Heeft het college haar beloftes waargemaakt? Geef me een overzicht.",
        "label": "Coalitieakkoord vs. uitvoering",
    },
    {
        "id": "klimaat",
        "question": "Welke moties over klimaat en duurzaamheid zijn aangenomen door de Rotterdamse gemeenteraad sinds 2020?",
        "label": "Klimaatbeleid en moties",
    },
    {
        "id": "woningbouw",
        "question": "Wat heeft Rotterdam de afgelopen jaren gedaan om betaalbare woningbouw te realiseren?",
        "label": "Woningbouw en betaalbaarheid",
    },
    {
        "id": "partijen_woningbouw",
        "question": "Hoe stemden de Rotterdamse partijen over woningbouw en sociale huur?",
        "label": "Stemgedrag woningbouw",
    },
    {
        "id": "armoede",
        "question": "Welke maatregelen heeft Rotterdam genomen tegen armoede en schulden?",
        "label": "Armoede en schuldhulpverlening",
    },
]

OUTPUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "demo_cache.json")


async def run_query(ai_service: AIService, storage: StorageService, query_def: dict) -> dict | None:
    print(f"\n{'─' * 60}")
    print(f"Query: {query_def['question']}")
    print(f"{'─' * 60}")

    try:
        result = await ai_service.perform_deep_search(
            query=query_def["question"],
            storage=storage,
        )

        answer = result.get("answer", "")
        sources = result.get("sources", [])

        if not answer or len(answer) < 100:
            print(f"  ⚠ Short or empty answer ({len(answer)} chars) — skipping")
            return None

        print(f"  ✓ Answer: {len(answer)} chars, {len(sources)} sources")
        if sources:
            print(f"  First source: {sources[0].get('name', '?')[:60]}")

        return {
            "id": query_def["id"],
            "question": query_def["question"],
            "label": query_def["label"],
            "answer": answer,          # raw markdown — rendered by marked.js on client
            "sources": sources,
            "cached_at": datetime.utcnow().isoformat() + "Z",
        }

    except Exception as e:
        print(f"  ✗ Error: {e}")
        return None


async def main():
    print("NeoDemos — Demo Answer Cache Builder")
    print("=" * 60)

    ai_service = AIService()
    if not ai_service.use_llm:
        print("ERROR: AI service not available. Check GEMINI_API_KEY in .env")
        sys.exit(1)

    storage = StorageService()
    print(f"DB connected. Running {len(DEMO_QUERIES)} queries...\n")

    # Load existing cache to preserve answers we're not re-running
    existing = {}
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH) as f:
                for entry in json.load(f):
                    existing[entry["id"]] = entry
            print(f"Loaded {len(existing)} existing cached answers")
        except Exception:
            pass

    results = []
    for q in DEMO_QUERIES:
        result = await run_query(ai_service, storage, q)
        if result:
            results.append(result)
        else:
            # Keep the existing cached answer if re-run fails
            if q["id"] in existing:
                print(f"  → Keeping existing cached answer for '{q['id']}'")
                results.append(existing[q["id"]])

    if not results:
        print("\nERROR: No answers generated. Aborting.")
        sys.exit(1)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n{'=' * 60}")
    print(f"✓ Saved {len(results)} demo answers to {OUTPUT_PATH}")
    for r in results:
        print(f"  • {r['id']}: {len(r['answer'])} chars, {len(r['sources'])} sources")
    print("\nDeploy with: /opt/homebrew/lib/ruby/gems/4.0.0/bin/kamal deploy")


if __name__ == "__main__":
    asyncio.run(main())
