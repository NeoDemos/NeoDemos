# WS12 — Virtual Notulen Backfill & Production Hardening

> **Status:** `deferred` — Phase 1+4 (2025+2026) done and live in production; Phase 2+3 (server infra + 2018–2024 backfill) deferred to v0.3/v0.4
> **Owner:** `dennis`
> **Priority:** backlog (was 1; downgraded 2026-04-14)
> **Dependencies:** none (WS12 removed from WS1 blockers 2026-04-14)
> **Created:** 2026-04-13
> **Last updated:** 2026-04-14

---

## Deferral Decision (2026-04-14)

After a conversation with **Erik Verweij** (first NeoDemos user), virtual notulen confirmed as a **nice-to-have**, not a v0.2.0 requirement. Scope is frozen to keep the roadmap stable for the press moment. The 2025+2026 corpus is live and searchable — that is sufficient for the current user need.

**What's done and live:**
- 2025: 213 meetings promoted to production
- 2026: 34 meetings promoted (1,079 chunks in Qdrant + PostgreSQL)
- iBabs URL sync cron hardened (UUID format, dynamic container lookup)
- Killswitch (`INCLUDE_VIRTUAL_NOTULEN=false`) available if needed

**What's deferred to v0.3/v0.4:**
- Phase 2: Server-side pipeline (Docker integration, Whisper API, auto-retry)
- Phase 3: 2018–2024 backfill (661 meetings)
- Known bug fixes (orphan `meeting_id`, `--approve-batch` status filter, Qdrant commit ordering)

**Impact on WS1:** VN provenance (Phase A bis) already shipped in WS1. The KG will only reference 2025+2026 VN until WS12 resumes. WS12 is no longer a WS1 blocker.

---

## TL;DR

The virtual notulen pipeline is validated and working (2025: 214/282 completed, 92% coverage). This workstream:
1. Promotes 2025 to production
2. Backfills 2018–2024 (661 meetings) using server-side processing
3. Switches Whisper to API-based for 10x speed improvement
4. Adds quality controls: killswitch, timestamp linking, audit trail

---

## Current State (as of 2026-04-14)

### Coverage (staging)

| Year | Committee Meetings | Aligned (ibabs_url) | Promoted | Pending | Rejected |
|------|--------------------|---------------------|----------|---------|----------|
| 2018 | 59 | 17 | 52 | 1 | 0 |
| 2019 | 63 | 41 | 0 | 0 | 0 |
| 2020 | 169 | 132 | 0 | 0 | 0 |
| 2021 | 203 | 168 | 0 | 0 | 0 |
| 2022 | 76 | 39 | 0 | 0 | 0 |
| 2023 | 66 | 51 | 0 | 0 | 0 |
| 2024 | 84 | 68 | 0 | 0 | 0 |
| 2025 | 84 | 60 | 213 | 0 | 1 |
| 2026 | 54 | 21 | 34 | 0 | 0 |
| **Total** | **858** | **597** | **299** | **1** | **1** |

> Note: "Promoted" counts exceed "Committee Meetings" for some years because multiple transcript variants per meeting ID can exist in staging (CompanyWebcast vs iBabs agenda UUID — see known bug below).

### 2026-04-14 updates (this session)

- **2026 backfill complete**: 23 meetings processed after iBabs URL migration fix; 10 final `approved`-but-unpromoted meetings promoted manually (1,079 chunks to production Qdrant + DB).
- **iBabs URL format fix**: numeric IDs (`/Agenda/Index/7557375`) permanently deprecated; UUID format is the new standard. `sync_ibabs_urls.py` updated and cron on Hetzner fixed (hardcoded container name + `--all` flag for monthly full-history resolution).
- **Cron hardening**: `/home/deploy/sync-ibabs-urls.sh` now uses dynamic container lookup (`docker ps | grep '^neodemos-web'`) so Kamal hash-suffix renames don't break the job.

### Lessons Learned from 2025 + 2026 Runs

1. **MLX-Whisper is faster than expected** — ~16× realtime on MacBook (not 3×). 12h40m meeting transcribed in 47 min.
2. **Pipeline is laptop-dependent** — reboots, sleep, tunnel drops kill the run
3. **DB outages cause cascading failures** — 20 meetings re-failed 3x because the state file marks them failed immediately with no auto-retry
4. **Segment retention is excellent** — 99–100% across all completions (small-batch + SEG-NNN anchors work)
5. **VTT captions available for most meetings** — avoids Whisper entirely when present
6. **`--approve-batch` misses `review_status = 'approved'`** — the batch filter only picks up `auto_approved` and `pending`; manually-approved (or status-migrated) meetings are silently skipped. Worked around with direct `promote_meeting()` loop.
7. **Promotion tunnel hiccups leak Qdrant upserts** — Qdrant upsert happens before `prod_conn.commit()`; a dropped tunnel mid-UPDATE leaves chunks in Qdrant without matching PostgreSQL rows. Retry is safe (Qdrant point IDs are stable MD5 hashes → overwrite).

### Known Bugs (open)

| Bug | Location | Impact | Fix |
|-----|----------|--------|-----|
| Orphan `meeting_id` | `pipeline/committee_notulen_pipeline.py:371-381` | Chunks saved with `meeting_id = NULL` when CompanyWebcast UUID ≠ iBabs agenda UUID; breaks meeting→transcript link | One-line: `transcript["meeting_id"] = m_id` before `ingest_transcript()` call |
| `--approve-batch` status filter | `scripts/promote_committee_notulen.py:cmd_approve_batch` | Skips `review_status='approved'` meetings | Add `'approved'` to IN clause |
| Promotion commit ordering | `scripts/promote_committee_notulen.py:promote_meeting` | Qdrant upsert before prod commit → orphan points on tunnel drop | Move Qdrant upsert after prod commit, OR add post-failure cleanup |

### Code Fixes Applied (this workstream)

| Fix | File | What |
|-----|------|------|
| Timestamp metadata | `pipeline/ingestion.py` | `start_seconds`, `end_seconds`, `video_url`, `webcast_code` now stored per chunk |
| Video URL passthrough | `pipeline/committee_notulen_pipeline.py` | `ibabs_url` injected into transcript data for fragment linking |
| Quality killswitch | `services/rag_service.py` | `INCLUDE_VIRTUAL_NOTULEN=false` env var excludes all virtual notulen from both Qdrant and BM25 results |
| DB creds fix | `pipeline/normalization.py` | Reads `DATABASE_URL` from env instead of hardcoded `postgres:postgres` |
| iBabs URL sync cron | `/home/deploy/sync-ibabs-urls.sh` (Hetzner) | Dynamic container lookup + monthly `--all` for historical backfill |

---

## Execution Plan

### Phase 1: Promote 2025 + 2026 to Production ✅ DONE (2026-04-14)

- 2025: 213 meetings promoted to production (earlier batch, scoring ≥ 0.7 VTT)
- 2026: 34 meetings promoted (24 via `--approve-batch`; 10 via direct `promote_meeting()` loop to work around the `approved`-status filter bug)
- Final 2026 batch added 1,079 chunks to production Qdrant + PostgreSQL

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

### Phase 4: 2026 Recovery ✅ DONE (2026-04-14)

- **Root cause:** iBabs permanently migrated from numeric IDs (`/Agenda/Index/7557375`) to UUIDs (`/Agenda/Index/f5b29e97-...`). The 24 "HTTP 500" failures were not server issues — the URL format was dead.
- **Fix:** `sync_ibabs_urls.py` now pulls UUID iBabs URLs from ORI `was_generated_by.original_identifier`. Hetzner cron fixed (dynamic container name + `--all` for history).
- **Result:** 23 meetings re-processed, 0 failed, all 34 final 2026 staged meetings promoted.

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
