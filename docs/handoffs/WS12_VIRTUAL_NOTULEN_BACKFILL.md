# WS12 — Virtual Notulen Backfill & Production Hardening

> **Status:** `in progress` — Dennis running as of 2026-04-13
> **Owner:** `dennis`
> **Priority:** 1 (blocks press moment — corpus needs full committee coverage)
> **Dependencies:** WS11 (corpus completeness) runs in parallel
> **Created:** 2026-04-13

---

## TL;DR

The virtual notulen pipeline is validated and working (2025: 214/282 completed, 92% coverage). This workstream:
1. Promotes 2025 to production
2. Backfills 2018–2024 (661 meetings) using server-side processing
3. Switches Whisper to API-based for 10x speed improvement
4. Adds quality controls: killswitch, timestamp linking, audit trail

---

## Current State (as of 2026-04-13)

### Coverage

| Year | Committee Meetings | Aligned (ibabs_url) | Completed | Pending | Failed | Coverage |
|------|--------------------|---------------------|-----------|---------|--------|----------|
| 2018 | 77 | 17 | 0 | 0 | 53 | 0% |
| 2019 | 63 | 41 | 0 | 0 | 0 | 0% |
| 2020 | 169 | 132 | 0 | 0 | 0 | 0% |
| 2021 | 203 | 168 | 0 | 0 | 0 | 0% |
| 2022 | 76 | 39 | 0 | 0 | 0 | 0% |
| 2023 | 66 | 51 | 0 | 0 | 0 | 0% |
| 2024 | 84 | 68 | 0 | 0 | 0 | 0% |
| 2025 | 94 | 60 | 214 | 22 | 0 | 92% |
| 2026 | 54 | 21 | 0 | 21 | 24 | 0% |
| **Total** | **886** | **597** | **214** | **43** | **77** | |

### Lessons Learned from 2025 Run

1. **MLX-Whisper is the bottleneck** — 5–20 min/meeting on MacBook GPU vs ~2–3 min for LLM post-processing
2. **Pipeline is laptop-dependent** — reboots, sleep, tunnel drops kill the run
3. **DB outages cause cascading failures** — 20 meetings re-failed 3x because the state file marks them failed immediately with no auto-retry
4. **Segment retention is excellent** — 99–100% across all completions (small-batch + SEG-NNN anchors work)
5. **VTT captions available for most meetings** — avoids Whisper entirely when present

### Code Fixes Applied (this workstream)

| Fix | File | What |
|-----|------|------|
| Timestamp metadata | `pipeline/ingestion.py` | `start_seconds`, `end_seconds`, `video_url`, `webcast_code` now stored per chunk |
| Video URL passthrough | `pipeline/committee_notulen_pipeline.py` | `ibabs_url` injected into transcript data for fragment linking |
| Quality killswitch | `services/rag_service.py` | `INCLUDE_VIRTUAL_NOTULEN=false` env var excludes all virtual notulen from both Qdrant and BM25 results |
| DB creds fix | `pipeline/normalization.py` | Reads `DATABASE_URL` from env instead of hardcoded `postgres:postgres` |

---

## Execution Plan

### Phase 1: Promote 2025 to Production (1 hour)

```bash
# Verify staging state
python scripts/promote_committee_notulen.py --dry-run --year 2025

# Promote (embeds + upserts to production Qdrant + copies chunks to production DB)
python scripts/promote_committee_notulen.py --year 2025

# Verify in production
python -c "from services.rag_service import RAGService; r = RAGService(); print(r._retrieve_chunks_by_keywords('commissievergadering 2025', top_k=3))"
```

**Rollback:** `INCLUDE_VIRTUAL_NOTULEN=false` hides from RAG immediately. To fully remove: delete from `document_chunks` and Qdrant where `doc_type = 'virtual_notulen'`.

### Phase 2: Server-Side Infrastructure (0.5 day)

Move pipeline execution from laptop to Hetzner:

1. **Add pipeline to Docker image** — currently only web app is containerized; pipeline scripts need to be included in next Kamal deploy
2. **Whisper API integration** — Replace MLX-Whisper with Groq Whisper API for server-side transcription:
   - Groq free tier: 28,800 audio seconds/day = ~8 hours of audio = ~4 meetings/day (free)
   - Groq paid: $0.006/min → ~$0.72/meeting → ~$476 for 661 meetings
   - Alternative: OpenAI Whisper API at $0.006/min (same price, higher reliability)
   - **Fallback:** VTT captions are available for most meetings, so Whisper API is only needed for VTT-missing meetings

3. **Auto-retry logic** — Add exponential backoff + max 3 retries for DB connection errors instead of immediate `failed` state

### Phase 3: 2018–2024 Backfill (3–5 days server-side)

**Processing order (most recent first):**

| Priority | Year | Meetings | Aligned | Estimated Time (server) | Notes |
|----------|------|----------|---------|------------------------|-------|
| 1 | 2024 | 84 | 68 | ~8h | Most politically relevant |
| 2 | 2023 | 66 | 51 | ~6h | Current council period |
| 3 | 2022 | 76 | 39 | ~5h | Current council period start |
| 4 | 2021 | 203 | 168 | ~20h | COVID era, high volume |
| 5 | 2020 | 169 | 132 | ~15h | COVID era, high volume |
| 6 | 2019 | 63 | 41 | ~5h | Previous council period |
| 7 | 2018 | 77 | 17 | ~2h | Re-run (53 failed on old creds) |

**Execution per year:**
```bash
# On Hetzner via docker exec
docker exec neodemos-web python -m pipeline.committee_notulen_pipeline --year 2024
docker exec neodemos-web python -m pipeline.committee_notulen_pipeline --year 2023
# etc.
```

**Time estimates assume:**
- VTT available: ~3 min/meeting (download + LLM post-processing only)
- VTT missing + Whisper API: ~8 min/meeting (API transcription + LLM)
- VTT missing + local Whisper: ~20 min/meeting (current local speed)

### Phase 4: 2026 Recovery (ongoing)

- **24 failed meetings:** iBabs returning HTTP 500 for post-Feb-12 meetings. These self-heal when `sync_ibabs_urls.py` cron picks up new UUIDs after iBabs resolves the server issue.
- **21 pending:** waiting for `ibabs_url` alignment. Same cron resolves this.
- No manual action needed — automated via nightly cron on Hetzner.

---

## Quality Controls

### Killswitch

Set `INCLUDE_VIRTUAL_NOTULEN=false` in `.env` (or container env) to instantly hide all virtual notulen from RAG results. No data deletion, no reindexing. Affects:
- Qdrant vector search: filters on `is_virtual_notulen` payload field
- PostgreSQL BM25: excludes `documents.category = 'committee_transcript'`

### Timestamp & Source Linking

Each chunk now stores:
- `start_seconds` / `end_seconds` — time range within the video
- `video_url` — iBabs meeting page URL for the video player
- `webcast_code` — CompanyWebcast identifier for direct stream access

This enables future UX: "Click to hear this fragment" → deeplink to `{video_url}#t={start_seconds}`

### Staging Gate

All virtual notulen go through `staging.*` schema first. Only `promote_committee_notulen.py` moves them to production. Staging can be wiped/reprocessed independently.

### Quality Scoring

Each meeting gets a quality score (0–1) computed from:
- Segment count and density
- Speaker identification rate
- Agenda item detection
- Transcription confidence
- Completeness (gaps, incomplete segments)

Score stored in `staging.meetings.quality_score` with `review_status` (approved/pending/rejected).

---

## Cost Estimate

| Item | Cost |
|------|------|
| Gemini Flash Lite (LLM post-processing) | ~$0.02/meeting → ~$13 for 661 meetings |
| Groq Whisper API (if needed) | ~$0.72/meeting → max ~$476 (only for VTT-missing) |
| Qwen3 Embedding (promotion) | ~$0.01/meeting → ~$7 |
| **Total (VTT available)** | **~$20** |
| **Total (all Whisper API)** | **~$500** |

Most meetings have VTT captions, so actual cost will be closer to $50–100.

---

## Risks

| Risk | Mitigation |
|------|------------|
| VTT quality varies | Two-pass LLM cleanup + quality scoring catches worst cases |
| iBabs UUID rotation breaks links | Nightly `sync_ibabs_urls.py` cron auto-heals; `meeting_url_history` audit table tracks all changes |
| Whisper hallucination on poor audio | Pre-cleaner strips `*** ***`, `ED ED ED`, repetition loops; guard rail skips LLM if artifact_rate > 40% |
| Server-side Whisper slower than GPU | Switch to API-based; VTT fallback covers most meetings |
| Quality regression noticed post-promote | Killswitch hides instantly; re-promote after fix |

---

## Files Modified

- `pipeline/ingestion.py` — timestamp + video URL metadata in `ingest_transcript()`
- `pipeline/committee_notulen_pipeline.py` — `ibabs_url` passthrough to transcript data
- `services/rag_service.py` — `INCLUDE_VIRTUAL_NOTULEN` killswitch (Qdrant + BM25)
- `pipeline/normalization.py` — `DATABASE_URL` env var instead of hardcoded creds
