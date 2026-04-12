"""WS2 — Create financial_lines, financial_entities, iv3_taakvelden,
programma_aliases, and gr_member_contributions tables.

Workstream: WS2 (Trustworthy Financial Analysis)
Handoff: docs/handoffs/WS2_FINANCIAL.md
Seed data: data/financial/iv3_taakvelden.json, data/financial/financial_entities_seed.json

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-12
"""
from typing import Sequence, Union
import json
import os

from alembic import op
import sqlalchemy as sa

revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ── Path to seed data files (relative to project root) ────────
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _load_json(filename: str) -> dict:
    path = os.path.join(_PROJECT_ROOT, 'data', 'financial', filename)
    with open(path) as f:
        return json.load(f)


def upgrade() -> None:
    # ── 1. financial_entities (must be created before financial_lines FK) ──
    op.execute("""
        CREATE TABLE financial_entities (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            kind TEXT NOT NULL,
            host_gemeente TEXT,
            member_gemeenten TEXT[],
            website TEXT,
            wgr_type TEXT
        );
    """)

    # ── 2. iv3_taakvelden (must be created before financial_lines FK) ──
    op.execute("""
        CREATE TABLE iv3_taakvelden (
            code TEXT PRIMARY KEY,
            hoofdtaakveld TEXT NOT NULL,
            omschrijving TEXT NOT NULL
        );
    """)

    # ── 3. financial_lines ──
    # Note: bron_chunk_id is INTEGER to match document_chunks.id (SERIAL).
    op.execute("""
        CREATE TABLE financial_lines (
            id BIGSERIAL PRIMARY KEY,
            gemeente TEXT NOT NULL DEFAULT 'rotterdam',
            entity_id TEXT NOT NULL DEFAULT 'rotterdam'
                REFERENCES financial_entities(id),
            scope TEXT NOT NULL DEFAULT 'gemeente'
                CHECK (scope IN (
                    'gemeente',
                    'gemeenschappelijke_regeling',
                    'regio',
                    'nationaal'
                )),
            document_id TEXT NOT NULL REFERENCES documents(id),
            page INT NOT NULL,
            table_id TEXT NOT NULL,
            row_idx INT NOT NULL,
            col_idx INT NOT NULL,
            programma TEXT,
            sub_programma TEXT,
            iv3_taakveld TEXT REFERENCES iv3_taakvelden(code),
            jaar INT NOT NULL,
            bedrag_eur NUMERIC(18,2) NOT NULL,
            bedrag_label TEXT,
            bron_chunk_id INTEGER REFERENCES document_chunks(id),
            source_pdf_url TEXT,
            sha256 TEXT NOT NULL,
            extracted_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX idx_fl_gemeente_jaar_programma
            ON financial_lines (gemeente, jaar, programma);
        CREATE INDEX idx_fl_gemeente_jaar_sub_programma
            ON financial_lines (gemeente, jaar, sub_programma);
        CREATE INDEX idx_fl_gemeente_jaar_iv3
            ON financial_lines (gemeente, jaar, iv3_taakveld);
        CREATE INDEX idx_fl_scope_entity_jaar
            ON financial_lines (scope, entity_id, jaar);
        CREATE INDEX idx_fl_document_id
            ON financial_lines (document_id);
    """)

    # ── 4. programma_aliases ──
    op.execute("""
        CREATE TABLE programma_aliases (
            id BIGSERIAL PRIMARY KEY,
            gemeente TEXT NOT NULL,
            jaar INT NOT NULL,
            programma_label TEXT NOT NULL,
            iv3_taakveld TEXT NOT NULL REFERENCES iv3_taakvelden(code),
            confidence NUMERIC(3,2),
            source TEXT
        );

        CREATE UNIQUE INDEX idx_pa_gemeente_jaar_label
            ON programma_aliases (gemeente, jaar, programma_label);
    """)

    # ── 5. gr_member_contributions ──
    op.execute("""
        CREATE TABLE gr_member_contributions (
            id BIGSERIAL PRIMARY KEY,
            entity_id TEXT NOT NULL REFERENCES financial_entities(id),
            jaar INT NOT NULL,
            member_gemeente TEXT NOT NULL,
            bijdrage_eur NUMERIC(18,2) NOT NULL,
            bijdrage_pct NUMERIC(5,4),
            bron_chunk_id INTEGER REFERENCES document_chunks(id),
            document_id TEXT NOT NULL REFERENCES documents(id),
            sha256 TEXT NOT NULL
        );

        CREATE UNIQUE INDEX idx_grmc_entity_jaar_gemeente
            ON gr_member_contributions (entity_id, jaar, member_gemeente);
    """)

    # ── 6. pipeline_runs + pipeline_failures (WS5a observability) ──
    # Created here because WS2 backfill needs to log pipeline runs.
    # WS5a will own these tables going forward but needs them to exist.
    op.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id BIGSERIAL PRIMARY KEY,
            job_name TEXT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMPTZ,
            status TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'success', 'failure', 'skipped')),
            items_discovered INT DEFAULT 0,
            items_processed INT DEFAULT 0,
            items_failed INT DEFAULT 0,
            error_message TEXT,
            error_traceback TEXT,
            triggered_by TEXT DEFAULT 'manual'
                CHECK (triggered_by IN ('cron', 'manual', 'smoke_test'))
        );

        CREATE INDEX IF NOT EXISTS idx_pr_job_started
            ON pipeline_runs (job_name, started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_pr_status_started
            ON pipeline_runs (status, started_at DESC);
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_failures (
            id BIGSERIAL PRIMARY KEY,
            job_name TEXT NOT NULL,
            item_id TEXT,
            item_type TEXT,
            failed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            retry_count INT DEFAULT 0,
            error_class TEXT,
            error_message TEXT,
            raw_payload JSONB
        );
    """)

    # ── 7. Seed reference data ──────────────────────────────────────
    _seed_iv3_taakvelden()
    _seed_financial_entities()


def _seed_iv3_taakvelden() -> None:
    data = _load_json('iv3_taakvelden.json')
    conn = op.get_bind()
    for tv in data['taakvelden']:
        conn.execute(
            sa.text(
                "INSERT INTO iv3_taakvelden (code, hoofdtaakveld, omschrijving) "
                "VALUES (:code, :hoofdtaakveld, :omschrijving) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {
                'code': tv['code'],
                'hoofdtaakveld': tv['hoofdtaakveld'],
                'omschrijving': tv['omschrijving'],
            },
        )


def _seed_financial_entities() -> None:
    data = _load_json('financial_entities_seed.json')
    conn = op.get_bind()
    for ent in data['entities']:
        members = ent.get('member_gemeenten')
        members_literal = (
            '{' + ','.join(members) + '}' if members else None
        )
        conn.execute(
            sa.text(
                "INSERT INTO financial_entities "
                "(id, display_name, kind, host_gemeente, member_gemeenten, website, wgr_type) "
                "VALUES (:id, :display_name, :kind, :host_gemeente, :member_gemeenten, :website, :wgr_type) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                'id': ent['id'],
                'display_name': ent['display_name'],
                'kind': ent['kind'],
                'host_gemeente': ent.get('host_gemeente'),
                'member_gemeenten': members_literal,
                'website': ent.get('website'),
                'wgr_type': ent.get('wgr_type'),
            },
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS gr_member_contributions CASCADE;")
    op.execute("DROP TABLE IF EXISTS programma_aliases CASCADE;")
    op.execute("DROP TABLE IF EXISTS financial_lines CASCADE;")
    op.execute("DROP TABLE IF EXISTS iv3_taakvelden CASCADE;")
    op.execute("DROP TABLE IF EXISTS financial_entities CASCADE;")
    # pipeline_runs/pipeline_failures are shared with WS5a — leave them.
