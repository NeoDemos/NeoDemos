# Embedding Pipeline Runbook

> Lessons learned from the April 2026 migration of ~1.3M document chunks into Qdrant.
> Written for future agents and developers picking up this work.

## System Context

- **Machine:** MacBook Pro M5 Pro, 64GB Unified Memory (shared CPU/GPU)
- **Embedding model:** `Qwen3-Embedding-8B-4bit-DWQ` via MLX (Apple Silicon GPU), 4096 dimensions
- **Vector DB:** Qdrant standalone server at `localhost:6333` (NOT Docker, NOT embedded mode)
- **Source DB:** PostgreSQL at `localhost:5432/neodemos`, table `document_chunks` (~1.33M rows)
- **Collection:** `notulen_chunks` — cosine distance, `on_disk=True`, INT8 scalar quantization

## The Golden Rule: Single-Item Embedding

**Never batch-embed mixed-length texts through MLX.** This is the #1 lesson.

When you pad multiple texts to the longest sequence and run them through a single GPU forward pass, the padding tokens pollute mean pooling and produce NaN/Inf vectors. We tried:
1. Naive batch padding + mean pooling -> 81% NaN rate
2. Masked mean pooling (exclude padding) -> correct but slow
3. Token-budget sub-batching (group similar lengths) -> complex, marginal gain
4. Hybrid: naive batch + masked single-item retry for NaN -> still slow due to retry rate

**What works:** `generate_embedding()` one text at a time. No padding, no NaN. The Qdrant upserts are still batched (batch_size=16, `wait=False`), but each embedding is computed individually.

Achievable speed: **3-6 chunks/sec** on M5 Pro with Qwen3-8B-4bit.

## GPU Hang Detection (Critical)

MLX on Apple Silicon will **silently hang** after thousands of sequential GPU operations. The Metal command queue enters `condition_variable::wait` and never returns. The process appears alive (low CPU, stable RSS) but makes zero progress. This happened repeatedly during multi-hour runs.

**How to detect:** The process shows 0% CPU and the checkpoint file stops updating. Use `sample <pid> 3` to confirm — you'll see the main thread stuck in `mlx::core::eval_impl` -> `__psynch_cvwait`.

**Solution implemented in `migrate_embeddings.py`:**

1. **Threaded timeout wrapper** (`safe_generate_embedding`): Runs each embedding call in a daemon thread with a 120-second timeout. If the GPU hangs, `TimeoutError` is raised instead of blocking forever.

2. **Process self-restart** (`os.execv`): On `TimeoutError`, the process closes DB connections, waits 3 seconds for GPU cooldown, and re-execs itself. This gives a completely fresh Metal context. The checkpoint ensures no work is lost.

3. **Proactive GPU flush** (`perform_cleanup`): Every 64 chunks, call `mx.synchronize()` (drain the Metal command queue) followed by `mx.clear_cache()`. This prevents the memory fragmentation that causes the slowdown-then-hang pattern.

Without `mx.synchronize()`, the pattern is:
- Fast for ~128 chunks (3-6 chunks/sec)
- Sudden slowdown to 10-19s/chunk
- Eventually permanent hang

With `mx.synchronize()` every 64 chunks, speed stays at 3-6 chunks/sec with minor fluctuations.

## Checkpoint and Resume

- Checkpoint file: `data/pipeline_state/migration_checkpoint.json`
- Format: `{"last_processed_id": <db_id>}`
- Written after every batch (16 chunks) — max data loss on crash is 16 chunks
- Point IDs are deterministic: `int(md5(f"{doc_id}_{db_id}")[:15], 16)` — re-running is idempotent (upsert overwrites)

### Recovery Mode vs Standard Mode

- **Standard mode** (`python scripts/migrate_embeddings.py`): Streams all chunks with `id > checkpoint` via named cursor. For initial bulk migration.
- **Recovery mode** (`--recovery-mode`): Runs a live audit (`compute_missing_ids()`) to find chunks in Postgres but not in Qdrant, then processes only those. The audit is the source of truth — no checkpoint filtering needed (but checkpoint still saves progress for crash recovery).

## Things That Will Waste Your Time

### 1. "Let me add batching for speed"
Don't. Single-item embedding at 3-6 chunks/sec is the proven stable rate for Qwen3-8B on M5 Pro. Batch approaches either produce NaN or don't meaningfully improve throughput because the GPU is already saturated.

### 2. Calling `manual_memory_reset()` before migration
This wipes `_GLOBAL_EMBED_CACHE` in `local_ai_service.py`, forcing a full 4.7GB model reload from disk (~30 minutes). The model is cached in module-level globals for a reason. Never call this before a migration run.

### 3. Calling `perform_cleanup()` with `manual_memory_reset()`
An earlier version had cleanup trigger a full model reset. This caused the model to reload from disk every 256 chunks. Cleanup must ONLY do `mx.synchronize()` + `mx.clear_cache()` + `gc.collect()`. Never touch the model weights.

### 4. Large batch sizes for Qdrant upserts
`batch_size=100` works but means losing up to 100 chunks on crash. `batch_size=16` is a good balance — frequent checkpoints, minimal overhead.

### 5. Reading stale gap audit files
An older approach wrote missing IDs to a JSON file and read it back later. By the time the migration ran, the file was stale. Always run the audit live at startup (as `compute_missing_ids()` does now).

### 6. Using `fetchall()` for the audit
Loading 1.33M rows into Python at once spikes RAM by several GB. Use a streaming/server-side cursor with `itersize=500-1000`.

## RAM Guidelines

- Qwen3-8B-4bit model: ~4.7GB in unified memory
- Process RSS during migration: ~5GB steady state
- Peak (during model load + first batch): up to 12GB, then drops
- RAM guard threshold: 40GB of 64GB total (measured via `vm_stat`, not `top` — `top` includes reclaimable cache)
- If running with the 24B Mistral LLM loaded simultaneously: budget ~17GB for both models. Use `skip_llm=True` when only embedding.

## Key Files

| File | Purpose |
|---|---|
| `scripts/migrate_embeddings.py` | Main migration script with GPU hang protection |
| `services/local_ai_service.py` | `generate_embedding()` (single-item), `generate_embeddings_batch()` (DO NOT USE for migration) |
| `scripts/audit_vector_gaps.py` | `compute_missing_ids()` — cross-references Qdrant vs Postgres |
| `data/pipeline_state/migration_checkpoint.json` | Resume checkpoint |
| `logs/migration_errors.log` | NaN/Inf skips, upsert failures, GPU hang detections |

## Metadata vs Vector Embedding for Dates

Document dates (`start_date` from the `meetings` table) are stored as **Qdrant payload metadata**, not embedded in the vector. This is correct because:
- Temporal queries ("what happened in 2023?") should use metadata filtering (exact match, range queries) — fast and precise
- Embedding dates into vector text would create weak semantic signal that degrades retrieval quality
- The RAG pipeline has temporal extraction (`ai_service.extract_temporal_filters()`) that converts natural language dates to `datum_van`/`datum_tot` filter parameters

## Advice for Future Agents

1. **Check if a migration process is running before touching Qdrant.** `ps aux | grep migrate_embeddings`. Read-only queries are safe; writes are not.

2. **Don't trust speed memories.** "It used to run at 3 chunks/sec" doesn't mean it will now. The speed depends on chunk content length, GPU thermal state, and what else is using unified memory. Benchmark on real data before making promises.

3. **When the process looks stuck, sample it.** `sample <pid> 3` tells you exactly where it's blocked. Don't guess — the call stack tells you if it's a GPU hang, a DB lock, or just a slow chunk.

4. **Don't over-engineer the solution.** We went through 4 batching strategies, token-budget grouping, masked pooling, and hybrid retry — all to avoid the simple answer: single-item embedding works, is fast enough, and never produces NaN. The original script had it right from the start.

5. **Respect the checkpoint.** Never reset or delete `migration_checkpoint.json` without understanding what it means. At 3 chunks/sec, losing 10,000 chunks of progress = 1 hour of wasted GPU time.

6. **The user monitors remotely.** They want visible progress in the terminal and concise status updates. Don't flood with debug output. tqdm progress bar + error log file is the right split.

7. **Read this runbook and `project_embedding_process.md` (Claude memory) before starting any embedding work.** They exist because we learned these lessons the hard way.
