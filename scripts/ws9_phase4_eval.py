#!/usr/bin/env python3
"""
WS9 Phase 4 Eval — run 20 prompts through WebIntelligenceService directly
(no HTTP, no rate limit, same code path as production).

Output: scripts/ws9_eval_results.md
Usage:  python scripts/ws9_phase4_eval.py [--start N] [--only N]
"""
import argparse
import asyncio
import sys
import time
from datetime import date
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from services.web_intelligence import WebIntelligenceService

PROMPTS = [
    # Single-tool / retrieval
    "Wat heeft de gemeente Rotterdam besloten over de aanpak van dakloosheid in 2023?",
    "Welke moties zijn ingediend over de Hoekse Lijn in de afgelopen drie jaar?",
    "Wat zijn de standpunten van GroenLinks-PvdA over woningbouw in Rotterdam?",
    "Welke uitspraken heeft de VVD gedaan over veiligheid in de Rotterdamse haven?",
    "Hoeveel heeft Rotterdam uitgegeven aan jeugdzorg in de begroting 2024?",
    # Temporal filtering
    "Wat heeft de raad besloten over klimaatadaptatie tussen 2020 en 2022?",
    "Welke moties over betaalbaar wonen zijn aangenomen in 2023?",
    "Hoe is het debat over de Omgevingsvisie verlopen tussen 2021 en 2024?",
    # Party comparison
    "Vergelijk de standpunten van D66 en PvdA over openbaar vervoer in Rotterdam.",
    "Wat zijn de verschillen tussen SP en VVD in hun aanpak van armoede?",
    "Hoe staan Denk en Leefbaar Rotterdam tegenover het Feyenoord City-project?",
    # Decision timeline
    "Wat is de besluitvormingsgeschiedenis rond het Centraal District?",
    "Hoe heeft de raad zich over de jaren heen uitgesproken over de Maasvlakte?",
    # Financial
    "Wat is de financiële situatie van Rotterdam ten aanzien van de algemene reserve in de jaarstukken 2023?",
    "Welke begrotingsposten zijn in de voorjaarsnota 2024 het meest gewijzigd?",
    # Complex / multi-tool
    "Welke partijen hebben moties ingediend over stikstof, en wat was het stemresultaat?",
    "Wat is het standpunt van het college over de sluiting van zwembaden, en hoe heeft de raad daarop gereageerd?",
    "Hoe is de discussie over cameratoezicht in Rotterdam verlopen, en welke moties zijn daarbij ingediend?",
    "Vergelijk hoe CDA en SP hebben gestemd op moties over energie-armoede tussen 2021 en 2024.",
    # Edge case
    "Wat weet je over Piet Jansen en zijn rol in de Rotterdamse gemeenteraad?",
]

OUTPUT = ROOT / "scripts" / "ws9_eval_results.md"


async def run_query(svc: WebIntelligenceService, idx: int, prompt: str) -> dict:
    t0 = time.monotonic()
    status_msgs = []
    chunks = []
    tools_called = []
    rounds = 0
    error = None

    try:
        async for event in svc.stream(prompt):
            if event.get("type") == "status":
                status_msgs.append(event["message"])
            elif event.get("type") == "chunk":
                chunks.append(event["text"])
            elif event.get("type") == "done":
                tools_called = event.get("tools_called", [])
                rounds = event.get("rounds", 0)
                error = event.get("error")
    except Exception as e:
        error = str(e)

    elapsed = time.monotonic() - t0
    answer = "".join(chunks)
    return {
        "idx": idx,
        "prompt": prompt,
        "answer": answer,
        "tools_called": tools_called,
        "rounds": rounds,
        "status_msgs": status_msgs,
        "elapsed_s": round(elapsed, 1),
        "error": error,
    }


def render_md(results: list[dict]) -> str:
    lines = [
        f"# WS9 Phase 4 Eval — {date.today().isoformat()}",
        "",
        f"**Model:** claude-sonnet-4-6  **Queries:** {len(results)}",
        "",
        "---",
        "",
    ]
    for r in results:
        lines += [
            f"## Q{r['idx']:02d} — {r['prompt']}",
            "",
            f"**Tools:** {', '.join(r['tools_called']) or '(none)'}  "
            f"**Rounds:** {r['rounds']}  **Time:** {r['elapsed_s']}s",
            "",
        ]
        if r["error"]:
            lines += [f"> ERROR: {r['error']}", ""]
        elif r["answer"]:
            lines += [r["answer"], ""]
        else:
            lines += ["> (geen antwoord)", ""]
        lines += ["---", ""]
    return "\n".join(lines)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=1, help="Start at query N (1-based)")
    parser.add_argument("--only", type=int, default=None, help="Run only query N")
    args = parser.parse_args()

    svc = WebIntelligenceService()
    if not svc.available:
        print("ERROR: WebIntelligenceService not available — check ANTHROPIC_API_KEY")
        sys.exit(1)

    prompts = PROMPTS
    if args.only:
        idx = args.only
        prompts = [(idx, PROMPTS[idx - 1])]
    else:
        prompts = list(enumerate(PROMPTS[args.start - 1:], start=args.start))

    results = []
    for idx, prompt in prompts:
        print(f"\n[{idx:02d}/20] {prompt[:60]}...")
        r = await run_query(svc, idx, prompt)
        results.append(r)
        tools_str = ", ".join(r["tools_called"]) or "none"
        status = "ERROR" if r["error"] else f"{len(r['answer'])} chars"
        print(f"       tools={tools_str}  rounds={r['rounds']}  {r['elapsed_s']}s  {status}")

    # Append to output file (so --start / --only can accumulate)
    if OUTPUT.exists() and args.start > 1:
        existing = OUTPUT.read_text()
    else:
        existing = None

    md = render_md(results)
    if existing and args.start > 1:
        OUTPUT.write_text(existing + "\n" + "\n".join(md.splitlines()[4:]))
    else:
        OUTPUT.write_text(md)

    print(f"\nResults written to {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
