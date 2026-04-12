# NeoDemos nightly pipeline — cron schedule

Run these on the **Hetzner server** as the `app` user (or whichever user owns `/app`).
Add via `crontab -e` or drop into `/etc/cron.d/neodemos`.

All scripts log to `/app/logs/`.  Redirect cron output to a separate file so
stdout/stderr from individual scripts don't interleave.

```cron
# NeoDemos nightly pipeline (Hetzner server, /app)

# WS6 — per-document summarization (Gemini Batch API, lock key 7_640_601)
0 3 * * * cd /app && python scripts/nightly/06b_compute_summaries.py --max-docs 500 >> logs/cron_summaries.log 2>&1

# WS7 — OCR recovery chunk re-enrichment (Flair NER, advisory lock 42)
# Staggered 30 min after 06b.  Locks are different (42 vs 7_640_601) so
# they cannot conflict, but the offset avoids overlapping Gemini + GPU load.
30 3 * * * cd /app && python scripts/nightly/07a_enrich_new_chunks.py --max-chunks 5000 >> logs/cron_enrich.log 2>&1
```

## Lock keys

| Script | Advisory lock key | Notes |
|--------|-------------------|-------|
| `06b_compute_summaries.py` | `7_640_601` | WS6 summarization |
| `07a_enrich_new_chunks.py` | `42` (via `run_flair_ner.py`) | Flair NER / WS1 enrichment |

The two locks are independent — 06b and 07a can overlap safely, but the
30-minute stagger keeps the nightly window cleaner and avoids combined
GPU + Gemini API burst charges.

## Manual one-off runs

```bash
# Dry-run: see how many chunks need enrichment without doing anything
python scripts/nightly/07a_enrich_new_chunks.py --dry-run

# Cap to 500 chunks for a quick smoke test
python scripts/nightly/07a_enrich_new_chunks.py --max-chunks 500 --log-level DEBUG

# Full backfill (no cap)
python scripts/nightly/07a_enrich_new_chunks.py --max-chunks 999999
```
