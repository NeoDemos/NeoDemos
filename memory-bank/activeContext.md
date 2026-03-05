# Active Context

**Current Focus**:
We have successfully implemented the calendar expansion limits, the "Betrekken bij" component grouping, and the "COR" meeting logic bypasses. Background jobs are currently running to ingest all 2024, 2025, and 2026 data.

**Immediate Hurdles**:
- Background ingestion is healthy but sequential; processing a full year of documents takes time (approx. 30-60 mins per year).
- Parallel background jobs have been started for 2024, 2025, and 2026 to accelerate population.

**Handover / Next Steps**:
- Monitor `ingest_2024.log`, `massive_ingestion_24_25.log`, and `ingest_2026.log`.
- Verify the calendar monthly view once the jobs hit the second half of each year.
