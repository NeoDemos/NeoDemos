"""
Shared PostgreSQL connection pool.

Usage:
    from services.db_pool import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ...")
            rows = cur.fetchall()
    # Connection is returned to pool automatically
"""
import os
import logging
import threading
from contextlib import contextmanager
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger(__name__)

_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                db_url = os.getenv("DATABASE_URL", "")
                if not db_url:
                    host = os.getenv("DB_HOST", "localhost")
                    port = os.getenv("DB_PORT", "5432")
                    name = os.getenv("DB_NAME", "neodemos")
                    user = os.getenv("DB_USER", "postgres")
                    password = os.getenv("DB_PASSWORD", "postgres")
                    db_url = f"host={host} port={port} dbname={name} user={user} password={password}"

                pool_size = int(os.getenv("DB_POOL_SIZE", "10"))
                max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "20"))

                _pool = ThreadedConnectionPool(
                    minconn=pool_size,
                    maxconn=pool_size + max_overflow,
                    dsn=db_url,
                )
                logger.info(
                    f"PostgreSQL pool initialized (min={pool_size}, max={pool_size + max_overflow})"
                )
    return _pool


@contextmanager
def get_connection():
    """Get a connection from the pool. Returns it on exit."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def close_pool():
    """Close all connections. Call on shutdown."""
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        logger.info("PostgreSQL pool closed")
