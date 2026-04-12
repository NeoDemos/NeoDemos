"""
Alembic environment configuration for NeoDemos.

Reads DB connection from environment variables, matching the fallback
pattern in services/db_pool.py:
  1. DATABASE_URL  (SQLAlchemy URL format: postgresql+psycopg2://…)
  2. Individual DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD vars.
"""

import os
from logging.config import fileConfig
from urllib.parse import quote_plus

from alembic import context
from sqlalchemy import engine_from_config, pool

# ── Alembic Config object ──────────────────────────────────────
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# ── Build database URL from environment ────────────────────────

def _resolve_database_url() -> str:
    """Return a SQLAlchemy-compatible PostgreSQL URL."""
    url = os.environ.get("DATABASE_URL", "")
    if url:
        # Ensure the URL uses the psycopg2 driver prefix.
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
        elif not url.startswith("postgresql+psycopg2://"):
            # Assume it's a psycopg2 key=value DSN — rebuild as URL.
            url = ""
    if not url:
        host = os.environ.get("DB_HOST", "localhost")
        port = os.environ.get("DB_PORT", "5432")
        name = os.environ.get("DB_NAME", "neodemos")
        user = os.environ.get("DB_USER", "postgres")
        password = quote_plus(os.environ.get("DB_PASSWORD", "postgres"))
        url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"
    return url


# Override the ini-file placeholder with the real URL.
config.set_main_option("sqlalchemy.url", _resolve_database_url())

# ── No target_metadata — Alembic does not autogenerate from models.
# Existing tables are managed by legacy scripts/create_*.py.
target_metadata = None


# ── Offline migrations (emit SQL to stdout) ────────────────────

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL and not an Engine,
    so that calls to context.execute() emit literal SQL.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online migrations (connect to live DB) ─────────────────────

def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Creates an Engine and associates a connection with the context.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
