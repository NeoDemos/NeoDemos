# NeoDemos Naming & Structure Conventions

Use this skill to enforce consistent naming across code, docs, backups, and project structure. Consistency > cleverness — pick one pattern per category and stick to it.

## 1. Project Folder Structure

```
NeoDemos/
├── .claude/commands/         # Claude Code skills
├── config/                   # YAML/TOML configuration
├── data/                     # Static data files (JSON, CSV)
├── docs/                     # All documentation
│   ├── architecture/         # Design plans and ADRs
│   ├── phases/               # Phase completion reports
│   └── research/             # Research notes
├── eval/                     # Evaluation framework (primary)
├── eval_notulen/             # Domain-specific eval sets
├── eval_v3/                  # Version-tagged eval runs
├── pipeline/                 # Ingestion and processing pipelines
├── scripts/                  # One-off and utility scripts
├── services/                 # Core business logic modules
├── tests/                    # Test suite
├── main.py                   # App entrypoint
├── mcp_server.py             # MCP server entrypoint
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env                      # Secrets (never committed)
└── .env.example              # Template (committed)
```

**Rules:**
- No scripts or temporary files in the project root
- Group by function (services/, pipeline/, scripts/), not by file type
- Eval variants use suffix: `eval/`, `eval_notulen/`, `eval_v3/`

## 2. Python Files (PEP 8)

| Category | Pattern | Examples |
|----------|---------|----------|
| Modules | `snake_case.py` | `rag_service.py`, `party_utils.py` |
| Services | `<domain>_service.py` | `ai_service.py`, `party_profile_service.py` |
| Pipeline stages | `<verb>_<noun>.py` | `staging_ingestor.py`, `transcript_postprocessor.py` |
| Scripts | `<verb>_<object>.py` | `populate_raadslid_rollen.py`, `audit_raadslid_rollen.py` |
| Tests | `test_<module>.py` | `test_party_lens_e2e.py`, `test_llm_scoring_comparison.py` |
| Shell scripts | `snake_case.sh` | `post_embedding_backup.sh`, `daily_backup.sh` |

**Rules:**
- Always `snake_case` — never camelCase or kebab-case for Python/shell files
- Prefix scripts with a verb: `create_`, `populate_`, `audit_`, `batch_`, `recover_`, `enrich_`, `migrate_`
- Services end with `_service.py`
- No `tmp_` prefix files committed — use `.gitignore`

## 3. Documentation Files

| Location | Pattern | Examples |
|----------|---------|----------|
| Project root | `UPPER_CASE.md` | `README.md`, `CHANGELOG.md`, `LICENSE` |
| `docs/` subdirs | `UPPER_SNAKE_CASE.md` | `DEPLOYMENT.md`, `SETUP_GUIDE.md` |
| Architecture plans | `PLAN_<letter>_<TOPIC>.md` | `PLAN_G_CONTEXTUAL_RETRIEVAL.md` |
| Phase reports | `V<n>_COMPLETION_REPORT.md` | `V3_COMPLETION_REPORT.md` |
| Research notes | `<Topic>_<Subtopic>.md` (Title_Case) | `RAG_Optimal_Setup_for_MCP.md` |

**Rules:**
- Root-level docs: UPPERCASE (GitHub convention)
- Inside `docs/`: UPPER_SNAKE_CASE for guides and plans
- Architecture plans use sequential letter prefix: `PLAN_A_`, `PLAN_B_`, etc.
- Never use spaces in filenames

## 4. Backup Files

| Type | Pattern | Example |
|------|---------|---------|
| PostgreSQL | `neodemos_pg_YYYYMMDD_HHMM.sql.gz` | `neodemos_pg_20260407_2158.sql.gz` |
| Qdrant | `qdrant_notulen_YYYYMMDD_HHMM.snapshot` | `qdrant_notulen_20260407_2202.snapshot` |
| Schema-only | `neodemos_schema_YYYYMMDD.sql` | `neodemos_schema_20260407.sql` |
| Excel backups | `<Name>_BACKUP_YYYYMMDD_HHMMSS.xlsx` | `NeoDemos_Tijd_Kosten_BACKUP_20260407_143022.xlsx` |

**Rules:**
- Always use ISO 8601 timestamps: `YYYYMMDD_HHMM` (minutes) or `YYYYMMDD_HHMMSS` (seconds)
- Never use spaces or colons in timestamps
- Format: `<descriptive_name>_<timestamp>.<ext>`
- Store all backups in `~/backups/`, never in project root
- Compress databases with `.gz` — append extension, don't replace

## 5. Configuration Files

| File | Convention | Notes |
|------|-----------|-------|
| `.env` | Flat key-value, `UPPER_SNAKE` keys | Never committed |
| `.env.example` | Same keys, placeholder values | Committed |
| `config/config.yaml` | `snake_case` keys | Main app config |
| `Dockerfile` | Capital D, no extension | Docker convention |
| `docker-compose.yml` | Kebab-case, `.yml` | Docker convention |
| `Caddyfile` | Capital C, no extension | Caddy convention |
| `requirements.txt` | Lowercase | pip convention |

## 6. Output & Reports

| Type | Pattern | Example |
|------|---------|---------|
| Audit reports | `audit_<topic>_YYYY-MM-DD_HHMM.md` | `audit_rollen_2026-04-07_2113.md` |
| Eval runs | `eval/runs/<run-name>/` | `eval/runs/hal-smoke-test/` |
| Logs | `<service>.log` | `mcp_server.log` |
| Archived logs | `logs/archive/<service>_YYYYMMDD.log` | `logs/archive/pipeline_20260405.log` |

**Rules:**
- Output reports go in `output/reports/`, never project root
- Eval run dirs use `kebab-case` names
- Log rotation: current log has no timestamp, archived logs do

## 7. Git Conventions

| Element | Pattern | Example |
|---------|---------|---------|
| Branch names | `kebab-case` | `feature/committee-notulen-pipeline` |
| Commit messages | Imperative mood, `<type>: <description>` | `Add committee notulen pipeline and staging ingestor` |
| Tags | `v<major>.<minor>.<patch>` | `v3.0.0` |

## Quick Reference: Common Mistakes to Avoid

| Wrong | Right | Why |
|-------|-------|-----|
| `myScript.py` | `my_script.py` | PEP 8: snake_case for modules |
| `backup-2026-04-07.sql` | `neodemos_pg_20260407_2158.sql.gz` | Include descriptive name + compact timestamp |
| `docs/deployment guide.md` | `docs/DEPLOYMENT.md` | No spaces; UPPER_SNAKE for docs |
| `PLAN_contextual.md` | `PLAN_G_CONTEXTUAL_RETRIEVAL.md` | Use letter prefix + full UPPER_SNAKE |
| `tmp_fix.py` in repo | Add to `.gitignore` | Never commit temp files |
| `=0.40.0` in root | Delete it | Garbage file, likely from pip |
