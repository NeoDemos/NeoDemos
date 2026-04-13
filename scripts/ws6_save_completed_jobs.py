#!/usr/bin/env python3
"""
Incrementally download completed Gemini batch job results to JSONL.

Checks all 17 jobs from the current WS6 backfill run, downloads any
that are SUCCEEDED but not yet in the checkpoint file, and appends
their results. Safe to run repeatedly — skips already-downloaded docs.

Usage:
    python scripts/ws6_save_completed_jobs.py
"""
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_REPO_ROOT, ".env"))

import google.genai as genai
from google.genai.types import JobState

CHECKPOINT = os.path.join(_REPO_ROOT, "logs", "ws6_results_8completed.jsonl")

JOB_NAMES = [
    "batches/91ff1el92ia5oq2rmnvssw15qza2jb15az0x",
    "batches/0bucs8l729rsslafjt7htq64e67ism2w83vz",
    "batches/303j9wq15p5sr385o9t1v0fjvrb0o9la2qyd",
    "batches/mhyxjzohz0osify3thv8umwqtt894xaexmq1",
    "batches/61nohnh5bdt10ziyd8hgyhv5o1c985b9kcup",
    "batches/9dgkn9o2441zurftuffr0mper521c6lbkmn6",
    "batches/7hnrl779tzqm20k7xjcq6feiou4dic0s4zyf",
    "batches/pfehhb8djls6ppwlad169cbdqc5qy851nq2c",
    "batches/t9w24o79werk3aj0i1ir313cbub2hnae88xf",
    "batches/v18jpuhjyayiuk07hbwmjn4ikwyd4sdflpzz",
    "batches/5l3jk4xldog0yolvfikuczxdf4tcx9cqgho7",
    "batches/ey762hg53enkb68nm8kn10aghi2tsfs46qhx",
    "batches/jtl5skad8bb3hrgdh8e9vu2er815ptim7mal",
    "batches/s3f3v0w6ihf1cxupv2p4oeurddmajr4qqef9",
    "batches/f9ozw6rgd13mxznwre293sxafldo6fwo603y",
    "batches/bu1z79rucbysov1rpy6r6r1prm9tke3nh1vt",
    "batches/85htvn30hnl3ldfq0vpitdstemc4pncwr7su",
]


def load_existing(path: str) -> set:
    """Return set of doc_ids already in the checkpoint file."""
    existing = set()
    if not os.path.exists(path):
        return existing
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    existing.add(json.loads(line)["doc_id"])
                except Exception:
                    pass
    return existing


def main():
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    existing = load_existing(CHECKPOINT)
    print(f"Checkpoint has {len(existing)} existing results.")

    new_results = {}
    pending = []
    failed = []

    for name in JOB_NAMES:
        job = client.batches.get(name=name)
        state = job.state
        if state == JobState.JOB_STATE_SUCCEEDED:
            count = 0
            for resp in job.dest.inlined_responses:
                req_key = resp.metadata.get("key", "?") if resp.metadata else "?"
                if req_key in existing:
                    continue
                if resp.response and resp.response.text:
                    new_results[req_key] = resp.response.text
                    count += 1
            print(f"  {name}: SUCCEEDED — {count} new results")
        elif state in (JobState.JOB_STATE_FAILED, JobState.JOB_STATE_CANCELLED,
                       JobState.JOB_STATE_EXPIRED):
            failed.append(name)
            print(f"  {name}: {state} (terminal failure)")
        else:
            pending.append(name)
            print(f"  {name}: {state} (still pending)")

    if new_results:
        with open(CHECKPOINT, "a", encoding="utf-8") as f:
            for doc_id, raw_text in new_results.items():
                f.write(json.dumps({"doc_id": doc_id, "raw_text": raw_text},
                                   ensure_ascii=False) + "\n")
        print(f"\nAppended {len(new_results)} new results → {CHECKPOINT}")
    else:
        print("\nNo new results to save.")

    total = len(existing) + len(new_results)
    print(f"Checkpoint total: {total} | Pending jobs: {len(pending)} | Failed: {len(failed)}")


if __name__ == "__main__":
    main()
