---
description: Dennis rejects a claimed WS after review. Appends qa_rejected event with reasons; WS stays in_progress.
---

**Dennis's reject verdict for a workstream that the agent says is done but fails QA.**

Usage: `/ws-reject WS7 "BM25 hit rate only 82%, target was 95% — OCR on scanned PDFs still failing"`.

This does NOT move the file or change anything in the code. It just logs the rejection in events.jsonl so:
- Anyone running `/ws-status` sees the rejection reasons
- The next agent picking up the WS can read what needs fixing
- The audit trail is preserved

```bash
python scripts/coord/append_event.py \
    --event qa_rejected \
    --ws $1 \
    --agent "Dennis" \
    --reason "$2"

python scripts/coord/rebuild_state.py
```

After running, the WS stays in `in_progress` status. The agent (or Dennis) picks it up again, addresses the reasons, and — when satisfied — Dennis re-runs `/ws-complete`.

No commit needed for a reject — the event log is the durable record, and it's already written.
