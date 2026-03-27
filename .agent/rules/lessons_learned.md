# NeoDemos Lessons Learned

## Technical Gotchas

### 2026-03-25: `loky` Semaphore Leaks after Crash
- **Symptom**: `UserWarning: resource_tracker: There appear to be 1 leaked semaphore objects to clean up at shutdown: {'/loky-...'}`.
- **Context**: Occurs in `migrate_embeddings.py` (and potentially other scripts using `LocalAIService` or `sentence-transformers`).
- **Cause**: A previous crash or unclean exit prevented the `multiprocessing` resource tracker from cleaning up its named semaphores. When a *new* process starts and then exits, the tracker identifies these leftovers and cleans them up, issuing a warning.
- **Resolution**: Harmless. It indicates the system successfully cleaned up the residue. No manual intervention is needed if RAM is healthy.
- **Prevention**: Ensure graceful shutdowns where possible, though crashes are sometimes unavoidable on large bulk tasks.
