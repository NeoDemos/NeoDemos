"""WS11c Phase 6 — single atomic UPDATE to backfill embedded_at.

Runs one UPDATE without the partial index present. synchronous_commit=OFF
reduces WAL sync pressure (safe because the UPDATE is idempotent: rerun if crash).

Writes progress to logs/ws11_phase6_backfill.log.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_PATH = LOG_DIR / "ws11_phase6_backfill.log"


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        log("ERROR: DATABASE_URL not set")
        return 2

    conn = psycopg2.connect(url)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        cur.execute("SET statement_timeout = 0")
        cur.execute("SET synchronous_commit = OFF")
        cur.execute("SET lock_timeout = '30s'")

        cur.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE embedded_at IS NULL"
        )
        null_before = cur.fetchone()[0]
        log(f"before: embedded_at IS NULL = {null_before:,}")

        log("starting UPDATE ...")
        t0 = time.time()
        cur.execute(
            "UPDATE document_chunks SET embedded_at = NOW() WHERE embedded_at IS NULL"
        )
        rows = cur.rowcount
        log(f"UPDATE returned rowcount={rows:,} after {time.time()-t0:.1f}s — committing")
        conn.commit()
        log(f"committed in {time.time()-t0:.1f}s total")

        cur.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE embedded_at IS NULL"
        )
        null_after = cur.fetchone()[0]
        log(f"after:  embedded_at IS NULL = {null_after:,}")

        result = {
            "ok": True,
            "null_before": null_before,
            "rows_updated": rows,
            "null_after": null_after,
            "elapsed_sec": round(time.time() - t0, 1),
        }
        log(f"RESULT: {json.dumps(result)}")
        return 0
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        log(f"ERROR: {type(exc).__name__}: {exc}")
        return 1
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
