# Virtual Notulen Pipeline — Handoff Document

**Last updated:** 2026-04-08
**Author:** Dennis Tak + Claude Code

---

## 1. What This Is

Rotterdam's committee meetings (commissievergaderingen) only have video recordings — unlike raadsvergaderingen, they have no official written minutes ("notulen"). This pipeline converts video recordings into searchable text transcripts ("virtual notulen"), audits them for quality, and promotes approved transcripts into the production RAG database for retrieval via MCP tools.

**The goal:** Make committee meeting content searchable and retrievable alongside the existing 85K+ meeting documents already in production.

---

## 2. Current State (as of 2026-04-08)

### Production Database
| Category | Documents |
|----------|-----------|
| meeting | 84,911 |
| municipal_doc | 3,192 |
| video_transcript | 280 |
| vision | 12 |
| committee_transcript | **0** (cleaned out, pending re-promotion) |

Production Qdrant: 1,630,523 points in `notulen_chunks`. Zero `virtual_notulen` points.

### Staging Database (210 meetings)
| Status | Count | Meaning |
|--------|-------|---------|
| `auto_approved` | 161 | Score >= 0.7, VTT source — ready to promote |
| `pending` | 15 | Speaker-enriched, need re-audit before promotion |
| `rejected` | 25 | Poor quality or zero speaker attribution |
| `approved` | 9 | Partially promoted (PostgreSQL only, no Qdrant vectors yet) |

**Quality scores:**
- auto_approved: avg 0.945, min 0.797, max 1.000
- pending: avg 0.600 (speaker-enriched, score not recalculated)
- rejected: avg 0.625, min 0.600, max 0.850

### Coverage by Year
| Year | Meetings | Notes |
|------|----------|-------|
| 2018 | 65 | Oldest batch, some with truncated VTT |
| 2019 | 4 | |
| 2023 | 1 | |
| 2025 | 129 | Bulk of the collection |
| 2026 | 11 | Only discovery entries — transcription failed (disk was full) |
| **2020-2022** | **0** | Gap — never processed |
| **2024** | **0** | Gap — never processed |

All 210 meetings have `transcript_source=vtt`. No Whisper-only meetings in staging.

---

## 3. Architecture Overview

### Isolation Design
Production and staging are fully isolated:

```
PostgreSQL: public schema (production) ↔ staging schema (same DB, separate tables)
Qdrant:     notulen_chunks (production)  ↔ committee_transcripts_staging (separate collection)
```

**Why a separate schema (not status columns):** A `pipeline_status` column on production tables is dangerous — one missing `WHERE` clause leaks staging data into live RAG/MCP queries. A separate schema gives SQL-level isolation while sharing the same connection for easy `INSERT INTO ... SELECT` at promotion time.

### Audit-First Architecture
- **No embeddings during pipeline ingestion** — staging uses `chunk_only=True`
- **Embeddings generated only at promotion time** — when data moves to production
- This means staging data is invisible to production RAG queries at all times

### Pipeline Flow (per meeting)

```
1. DISCOVER    → Find committee meetings without official notulen (ORI + iBabs)
2. TRANSCRIBE  → VTT from ConnectLive → Whisper fallback if no VTT
3. ALIGN       → Map iBabs speaker metadata to transcript segments
4. POST-PROCESS → 2-pass LLM correction (Gemini Flash Lite)
5. SCORE       → Compute 5-metric quality score
6. INGEST      → Store in staging schema (PostgreSQL only, no Qdrant)
7. AUDIT       → Structural + LLM hallucination check
8. PROMOTE     → Copy to production + generate embeddings + upsert to Qdrant
```

---

## 4. Key Files

### Pipeline Core
| File | Purpose |
|------|---------|
| `pipeline/committee_notulen_pipeline.py` | Main orchestrator — discovery, processing, scoring, ingestion |
| `pipeline/main_pipeline.py` | Lower-level pipeline — scraping, VTT fetch, Whisper, alignment |
| `pipeline/staging_ingestor.py` | Staging-isolated ingestion (subclass of SmartIngestor) |
| `pipeline/transcript_postprocessor.py` | 2-pass Gemini Flash Lite correction |
| `pipeline/extractor.py` | Whisper transcription, OCR speaker detection, alignment |
| `pipeline/scraper.py` | iBabs/Royalcast scraping, VTT download, metadata extraction |

### Quality & Audit
| File | Purpose |
|------|---------|
| `eval_notulen/audit_runner.py` | Full meeting audit (structural + LLM hallucination check) |
| `eval_notulen/config.py` | Audit configuration (thresholds, judge backend, models) |
| `scripts/batch_audit_staging.py` | Batch audit across all staging meetings |

### Tools & Scripts
| File | Purpose |
|------|---------|
| `scripts/promote_committee_notulen.py` | Review + promote staging → production |
| `scripts/enrich_speaker_attribution.py` | Recover speakers for rejected meetings |
| `scripts/recover_2026_meetings.py` | 2026-specific transcription recovery |
| `scripts/create_staging_schema.py` | Create/reset staging schema + Qdrant collection |
| `services/speaker_inference.py` | Dutch parliamentary address pattern → speaker attribution |

### Data
| File | Purpose |
|------|---------|
| `data/lexicons/rotterdam_political_dictionary.json` | Council members, parties, municipal terms (used by Whisper + LLM) |
| `output/transcripts/staging_cache/{uuid}.json` | Cached transcript JSON per meeting |
| `data/pipeline_state/committee_notulen_state.json` | Pipeline crash recovery state |

---

## 5. Transcript Extraction Pipeline

### Source Priority
1. **VTT subtitles** from ConnectLive (`connectlive.ibabs.eu`) — best quality, real-time captioning
2. **Whisper transcription** (mlx-whisper-large-v3-turbo on Apple Silicon) — fallback when no VTT
3. **PDF notulen** — last resort, rarely available for committee meetings

### Speaker Attribution Sources
1. **iBabs website scraping** — extracts speaker names + party from the agenda page HTML. This is the primary and most reliable source (325+ segments per meeting typical).
2. **SpeakerInferenceEnricher** — regex-based state machine that parses Dutch parliamentary address patterns in the transcript text itself. Used for meetings where iBabs scraping returned no speakers. Patterns recognized:
   - "De heer Tak." / "Mevrouw De Jong." (chair calling on speaker)
   - "Dan geef ik het woord aan wethouder Kasmi" (explicit handover)
   - "Mijn naam is Natascha Canta" (self-introduction by inspreker)
   - Procedural phrases → always attributed to Voorzitter
3. **OCR on video frames** — macOS Vision framework, detects speaker overlays. Not used in committee pipeline (iBabs is better).

### LLM Post-Processing (2-pass)
Both passes use `gemini-2.5-flash-lite` (~$0.005/meeting hour):

**Pass 1 — Correction:** Fix punctuation, obvious word errors, capitalize proper nouns, fix abbreviations. Processes in 12-minute chunks with 2-minute overlap.

**Pass 2 — Register conversion:** Convert spoken Dutch to written Dutch, remove disfluencies (eh, uhm, nou, zeg maar), correct names against Rotterdam political dictionary.

### Whisper Configuration
- Model: `mlx-community/whisper-large-v3-turbo`
- `initial_prompt`: Loaded from Rotterdam political dictionary (primes Whisper for proper nouns)
- Silero VAD preprocessing: Strips silence to prevent hallucination
- Chunked transcription for >15min audio (10-min chunks + `gc.collect()` for memory)

---

## 6. Quality Scoring

### Composite Score (0.0 - 1.0)
| Metric | Weight | Calculation |
|--------|--------|-------------|
| Segment count | 15% | `min(segments / 50, 1.0)` — expect 50+ for 2h meeting |
| Speaker attribution | 30% | `segments_with_speaker / total_segments` |
| Text density | 20% | `min(avg_chars_per_segment / 200, 1.0)` |
| Confidence | 20% | Mean segment confidence (VTT=1.0, Whisper from logprob) |
| Agenda coverage | 15% | `min(agenda_items / 3, 1.0)` |

### Auto-Classification
- Score >= 0.7 AND VTT source → `auto_approved`
- Score >= 0.7 AND Whisper source → `pending` (always needs manual review)
- Score 0.4 - 0.7 → `pending`
- Score < 0.4 → `auto_rejected`

---

## 7. Audit System

### Two-Phase Audit

**Phase 1 — Structural (no LLM, fast):**
- Speaker attribution rate (must be >= 80%)
- Named Entity Error Rate (NEER) — checks entity correction accuracy
- Chunk quality (length distribution, boilerplate, duplicates, agenda coverage)
- DB consistency (metadata, speaker presence)

**Phase 2 — LLM Hallucination Check (Gemini API):**
- Samples N chunks per meeting (default 5)
- Runs claim verification against source transcript context
- Computes hallucination rate per chunk

### Verdict Logic
- **REJECT** if: hallucination > 5%, speaker attribution < 50%, empty chunks, duplicates
- **PENDING** if: Whisper source OR warnings present OR quality < threshold
- **APPROVE** if: all thresholds met + VTT source

### Judge Backends (configured in `eval_notulen/config.py`)
| Backend | Model | Cost | Notes |
|---------|-------|------|-------|
| `gemini` (default) | gemini-2.5-flash-lite | ~$0.01/meeting | Cheapest, sufficient for audit |
| `claude` | claude-haiku-4-5 | ~$0.40/meeting | Higher quality, much more expensive |
| `local` | Qwen2.5-7B-Instruct-4bit | Free | Slow on Apple Silicon, already downloaded |

### Running an Audit
```bash
# Structural only (fast, no API calls)
python scripts/batch_audit_staging.py --skip-structural --llm-per-committee 0

# Full audit with Gemini (cheapest)
python scripts/batch_audit_staging.py --judge gemini --llm-per-committee 2 --llm-samples 5

# Audit specific status
python scripts/batch_audit_staging.py --status pending --judge gemini

# Single meeting audit
python -m eval_notulen.audit_runner --meeting-id <uuid> --samples 5
```

### Latest Audit Results (April 7, 2026)
- All 210 meetings passed structural audit
- NEER: 0.000 across all meetings (entity correction perfect)
- 119 meetings: APPROVE (promoted to auto_approved)
- 48 meetings: APPROVE with warnings
- 43 meetings: REJECT (40 due to zero speaker attribution, 3 other issues)
- Hallucination rate: typically 0-1% for good meetings

---

## 8. Promotion to Production

### What Promotion Does
1. Upsert meeting record from `staging.meetings` → `public.meetings`
2. Copy documents, document_children, document_chunks, document_assignments
3. **Generate embeddings** using Nebius API (Qwen3-Embedding-8B, 4096D)
4. Upsert vectors to Qdrant `notulen_chunks` collection
5. Mark `staging.meetings.promoted_at = NOW()`

### Running Promotion
```bash
# List staging meetings
python scripts/promote_committee_notulen.py --list

# Preview a meeting
python scripts/promote_committee_notulen.py --preview <meeting_id>

# Promote single meeting
python scripts/promote_committee_notulen.py --approve <meeting_id>

# Batch promote all auto_approved with score >= 0.7
python scripts/promote_committee_notulen.py --approve-batch --min-score 0.7

# Reject a meeting
python scripts/promote_committee_notulen.py --reject <meeting_id> --reason "poor audio"

# Show statistics
python scripts/promote_committee_notulen.py --stats
```

### Qdrant Payload (per chunk)
Each promoted chunk gets these Qdrant payload fields:
- `doc_type`: "virtual_notulen"
- `is_virtual_notulen`: True
- `start_date`: ISO date (enables RAG date filtering)
- `committee`: committee name (enables MCP committee filtering)

---

## 9. Known Issues & Flags

### CRITICAL: Completeness Unknown
**We have no way to verify if a meeting transcript is complete.** The VTT may end mid-meeting due to captioning service dropout. There is no duration-based completeness check in the audit. A meeting could score 0.95 quality but only contain the first 30 minutes of a 3-hour session.

**Proposed fix:** Compare VTT duration span (`max(end_seconds) - min(start_seconds)`) against the meeting's reported duration from Royalcast metadata. Flag meetings where VTT covers < 80% of recorded duration.

### 4 Known Truncated Meetings (pre-2022)
Four pre-2022 meetings were identified as definitively truncated. They contain "TV Gelderland" hallucination text — a Whisper artifact that occurs when recording ends with broadcast audio/silence. These are in the `rejected` pool.

### ConnectLive VTT Availability
2026 meetings return HTTP 500 from ConnectLive VTT endpoints. This may be a temporary issue or a change in the VTT service. When VTT is unavailable, the pipeline falls back to Whisper (lower quality, always flagged as `pending`).

### Events API URL Bug (FIXED)
The events URL was constructed as `BASE_URL + events_path` even when `events_path` already contained the full URL, producing `sdk.companywebcast.comhttps://sdk.companywebcast.com/...`. Fixed in `pipeline/scraper.py:411` on 2026-04-08.

### Year Gaps
- **2020-2022:** Zero meetings in staging. Never processed.
- **2024:** Zero meetings. Never processed.
- These gaps need dedicated pipeline runs.

### 9 Partially Promoted Meetings
9 meetings were promoted to PostgreSQL but failed Qdrant embedding (Qdrant was loading from a dump at the time). These have `promoted_at` set in staging but no vectors in production Qdrant. They need either:
- Re-promotion (which will upsert, not duplicate)
- Or manual embedding generation

### Speaker Attribution Gap
40 of the original 43 rejected meetings had zero speaker attribution. The SpeakerInferenceEnricher recovered 15 of these (2022+ meetings) to >= 50% attribution, moving them to `pending`. The remaining 25 rejected meetings are mostly pre-2022 with fundamentally poor VTT quality.

### Hallucination False Positives
An earlier context cap (3000 chars per agenda item) caused 43.6% false hallucination rates by cutting off relevant context that the LLM judge needed to verify claims. This was reverted — always provide full context to the judge.

---

## 10. Cost Model

| Operation | Cost | Notes |
|-----------|------|-------|
| LLM post-processing (2-pass) | ~$0.01/meeting hour | Gemini Flash Lite |
| LLM audit (5 samples) | ~$0.01/meeting | Gemini Flash Lite |
| Embedding generation | ~$0.002/meeting | Nebius API (Qwen3-Embedding-8B) |
| Full 210-meeting batch | ~$5-8 total | Post-processing + audit + embedding |
| Whisper transcription | Free | Local mlx-whisper on Apple Silicon |

Previous Anthropic Claude usage was ~40x more expensive. Switched to Gemini Flash Lite for all audit and post-processing.

---

## 11. How to Continue

### Immediate Next Steps

1. **Promote the 161 auto_approved meetings:**
   ```bash
   python scripts/promote_committee_notulen.py --approve-batch --min-score 0.7
   ```
   This will embed + upsert ~18K chunks to production Qdrant.

2. **Audit the 15 pending (speaker-enriched) meetings:**
   ```bash
   python scripts/batch_audit_staging.py --status pending --judge gemini --llm-per-committee 0
   ```

3. **Fix the 9 partially promoted meetings** (PG done, Qdrant missing):
   Re-run promotion — the script uses upserts, so it's safe to re-promote.

4. **Retry 2026 transcription** (disk space freed to 83GB):
   ```bash
   python scripts/recover_2026_meetings.py --apply --limit 34
   ```
   Note: ConnectLive VTT may still return 500 for 2026 meetings. Pipeline will fall back to Whisper.

### Medium-Term

5. **Add completeness check** to audit runner — compare VTT duration span vs. meeting recorded duration.

6. **Process 2020-2024 gap** — discover and transcribe committee meetings for missing years:
   ```bash
   python -m pipeline.committee_notulen_pipeline --year 2022 --limit 10
   python -m pipeline.committee_notulen_pipeline --year 2023 --limit 10
   python -m pipeline.committee_notulen_pipeline --year 2024 --limit 10
   ```

7. **Server deployment** — APScheduler job in `main.py` for weekly automated processing. MCP tool for on-demand triggering.

### Key Commands Reference

```bash
# === DISCOVERY ===
python -m pipeline.committee_notulen_pipeline --year 2026 --discover-only

# === PROCESSING ===
python -m pipeline.committee_notulen_pipeline --year 2026 --limit 5
python -m pipeline.committee_notulen_pipeline --reprocess <meeting_id>

# === SPEAKER ENRICHMENT (for rejected meetings) ===
python scripts/enrich_speaker_attribution.py                    # dry-run
python scripts/enrich_speaker_attribution.py --apply --min-date 2022-01-01

# === AUDIT ===
python scripts/batch_audit_staging.py --judge gemini            # full audit
python scripts/batch_audit_staging.py --llm-per-committee 0     # structural only
python -m eval_notulen.audit_runner --meeting-id <uuid>         # single meeting

# === PROMOTION ===
python scripts/promote_committee_notulen.py --list
python scripts/promote_committee_notulen.py --stats
python scripts/promote_committee_notulen.py --approve <id>
python scripts/promote_committee_notulen.py --approve-batch --min-score 0.7
python scripts/promote_committee_notulen.py --reject <id> --reason "..."

# === STAGING RESET (DESTRUCTIVE) ===
python scripts/create_staging_schema.py --drop
```

---

## 12. Environment Requirements

- **Python 3.13** with venv at `.venv/`
- **PostgreSQL** at localhost:5432/neodemos (user: postgres/postgres)
- **Qdrant** at localhost:6333 (82GB storage)
- **macOS** with Apple Silicon (for mlx-whisper and Vision OCR)
- **API keys in `.env`:**
  - `GEMINI_API_KEY` — for LLM post-processing and audit judge
  - `NEBIUS_API_KEY` — for embedding generation (Qwen3-Embedding-8B)
  - `ANTHROPIC_API_KEY` — optional, for Claude judge backend
- **Models on disk:**
  - `mlx-community/whisper-large-v3-turbo` — Whisper model
  - `mlx-community/Qwen2.5-7B-Instruct-4bit` — local LLM judge (optional)
- **Rotterdam political dictionary** at `data/lexicons/rotterdam_political_dictionary.json`

---

## 13. Staging Schema Reference

```sql
-- Core tables (mirror production with staging-specific columns)
staging.meetings          -- + transcript_source, quality_score, review_status, promoted_at
staging.documents
staging.document_children
staging.document_chunks
staging.document_assignments

-- Pipeline tracking (staging-only)
staging.pipeline_runs           -- run metadata, status, error_log
staging.pipeline_meeting_log    -- per-meeting log with quality_metrics JSONB
```

Qdrant staging collection: `committee_transcripts_staging` (4096D, Cosine).

Reset with: `python scripts/create_staging_schema.py --drop`
