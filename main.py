"""NeoDemos FastAPI application shell.

Routes live in `routes/*.py`. Service singletons live in `app_state.py`.
This file owns: app construction, middleware, lifespan, the scheduler
definitions, static-mount, and the Kamal liveness probe.
"""
import os
import asyncio
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

# Load environment variables from .env file before importing anything that reads them
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from neodemos_version import VERSION_LABEL
from services.db_pool import close_pool
from services.auth_dependencies import auth_service

from app_state import scheduler, refresh_service


# ── Scheduled job functions ──

def scheduled_refresh():
    """Wrapper for async refresh to run in scheduler"""
    logger.info("Scheduled refresh triggered (15-min interval)")
    try:
        asyncio.run(refresh_service.check_and_download())
    except Exception as e:
        logger.error(f"Scheduled refresh failed: {e}")


def scheduled_document_processor():
    """Process unchunked documents: chunk + build BM25 tsvector."""
    try:
        from services.document_processor import process_documents
        result = process_documents(limit=50, triggered_by="apscheduler")
        if result["chunked"] or result["tsvectors_built"]:
            logger.info(
                "Document processor: %d chunked, %d errors, %d tsvectors",
                result["chunked"], result["chunk_errors"], result["tsvectors_built"],
            )
    except Exception as e:
        logger.error(f"Document processor failed: {e}")


def scheduled_financial_sweep():
    """Hourly sweep: extract financial_lines from any doc with table_json chunks."""
    try:
        from services.financial_sweep import run_sweep
        result = run_sweep(triggered_by="apscheduler")
        if result["discovered"]:
            logger.info(
                "Financial sweep: %d discovered, %d processed, %d failed",
                result["discovered"], result["processed"], result["failed"],
            )
    except Exception as e:
        logger.error(f"Financial sweep failed: {e}")


def cleanup_sessions():
    """Purge expired sessions from the database."""
    try:
        count = auth_service.cleanup_expired_sessions()
        if count:
            logger.info(f"Cleaned up {count} expired sessions")
    except Exception as e:
        logger.error(f"Session cleanup failed: {e}")


# WS6 advisory lock key — shared with scripts/nightly/06b_compute_summaries.py
# so the scheduled job skips cleanly while a manual backfill holds the lock.
_WS6_SUMMARIES_LOCK_KEY = 7_640_601


def scheduled_summarization():
    """WS6 — compute per-document summaries for docs where summary_short IS NULL.

    Processes a small batch per firing via the real-time Summarizer path.
    The big historical backfill uses Gemini Batch API separately; this job
    only keeps new/updated docs current.
    """
    try:
        import os
        import psycopg2
        from types import SimpleNamespace
        from services.summarizer import Summarizer
        from services.storage_ws6 import (
            list_documents_needing_summary,
            get_all_chunks_for_document,
            update_document_summary_columns,
        )

        # Dedicated connection so the advisory lock auto-releases on conn.close().
        lock_conn = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            cur = lock_conn.cursor()
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_WS6_SUMMARIES_LOCK_KEY,))
            if not cur.fetchone()[0]:
                logger.info("Summarization: skipped (another run holds the lock)")
                return

            docs = list_documents_needing_summary(limit=20)
            if not docs:
                return

            summarizer = Summarizer()
            ok = failed = 0
            for doc in docs:
                doc_id = doc["id"]
                try:
                    chunk_rows = get_all_chunks_for_document(doc_id)
                    if not chunk_rows:
                        continue
                    chunks = [
                        SimpleNamespace(
                            chunk_id=r["chunk_id"], document_id=r["document_id"],
                            title=r.get("title") or "", content=r.get("content") or "",
                        )
                        for r in chunk_rows
                    ]
                    result = summarizer.summarize(chunks, mode="short")
                    if result.text.strip():
                        update_document_summary_columns(
                            doc_id,
                            summary_short=result.text,
                            summary_verified=result.verified,
                        )
                        ok += 1
                    else:
                        failed += 1
                except Exception as e:
                    logger.warning(f"Summarization failed for doc {doc_id}: {e}")
                    failed += 1

            if ok or failed:
                logger.info(
                    f"Summarization: {ok} computed, {failed} failed "
                    f"(of {len(docs)} candidates)"
                )
        finally:
            lock_conn.close()
    except Exception as e:
        logger.error(f"Summarization job failed: {e}")


def scheduled_smoke_test():
    """WS5a Phase A — hourly full-ingest canary.

    Runs `scripts/nightly/00_smoke_test.py` against a rotating fixture from
    `data/smoke_tests/`. Exercises the full ingest path:
        chunker -> embedder -> Qdrant -> retrieve -> source-span verify -> audit

    Isolated namespaces (never touches user-visible data):
      - Qdrant collection : ``smoke_test_notulen_chunks`` (dedicated)
      - documents.id       : ``SMOKE_TEST_*`` prefix
      - documents.category : ``smoke_test``
      - Qdrant payload     : ``is_smoke_test=True``

    Always records result in ``pipeline_runs`` (job_name='00_smoke_test',
    triggered_by='smoke_test'). Per-failure rows go to ``pipeline_failures``.
    Full-pass events go to ``document_events``.

    Never raises — a flaky smoke must never take down the scheduler.
    """
    try:
        # Import as a module so we can call main() in-process (fast, shares pool).
        # The file starts with a digit so we use importlib. The module MUST be
        # registered in sys.modules BEFORE exec_module because the script's
        # @dataclass decorators look themselves up in sys.modules.
        import importlib.util
        import pathlib
        import sys as _sys
        _MOD_NAME = "_smoke_test_mod"
        if _MOD_NAME in _sys.modules:
            mod = _sys.modules[_MOD_NAME]
        else:
            script_path = pathlib.Path(__file__).parent / "scripts" / "nightly" / "00_smoke_test.py"
            spec = importlib.util.spec_from_file_location(_MOD_NAME, script_path)
            mod = importlib.util.module_from_spec(spec)
            _sys.modules[_MOD_NAME] = mod
            spec.loader.exec_module(mod)
        exit_code = mod.main([])
        if exit_code == 0:
            logger.info("Smoke test: all steps passed")
        elif exit_code == 1:
            logger.warning("Smoke test: one or more steps FAILED (see pipeline_runs)")
        else:
            logger.error("Smoke test: operational error (exit_code=2)")
    except Exception as e:
        logger.error(f"Smoke test job failed to launch: {e}")


def scheduled_qa_digest():
    """WS5a Phase A — run the unified QA digest once per day at 07:00 CET.

    Computes every audit check (chunk attribution, vector gaps, raadslid
    roles, financial coverage, queue depth, smoke test, active writers,
    lock contention), writes ``reports/qa_digest/YYYY-MM-DD.json``, appends
    a row to ``pipeline_runs``, and emails Dennis the go/no-go summary.

    Never raises — a broken digest must never take down the scheduler.
    """
    try:
        from scripts.nightly.qa_digest import run_full_digest
        from services.pipeline_health_email import send_daily_digest

        digest_result = run_full_digest(triggered_by="cron")
        recipient = os.getenv("PIPELINE_ALERT_EMAIL", "dennis@neodemos.nl")
        try:
            send_daily_digest(recipient)
        except Exception as email_err:
            logger.error(f"QA digest email failed: {email_err}")

        logger.info(
            "QA digest: %s (%s)",
            digest_result.get("overall_status"),
            digest_result.get("summary"),
        )
    except Exception as e:
        logger.error(f"QA digest job failed: {e}")


# Register scheduler jobs (session cleanup is registered in lifespan below).
_JOB_DEFAULTS = dict(max_instances=1, coalesce=True)
scheduler.add_job(scheduled_refresh, IntervalTrigger(minutes=15),
                  id='interval_refresh', name='Check for new documents every 15 minutes',
                  misfire_grace_time=300, **_JOB_DEFAULTS)
scheduler.add_job(scheduled_document_processor, IntervalTrigger(minutes=20),
                  id='document_processor', name='Chunk new documents and build BM25 tsvectors',
                  misfire_grace_time=300, **_JOB_DEFAULTS)
scheduler.add_job(scheduled_financial_sweep, IntervalTrigger(hours=1),
                  id='financial_sweep', name='Extract financial_lines from unprocessed table docs',
                  misfire_grace_time=600, **_JOB_DEFAULTS)
scheduler.add_job(scheduled_summarization, IntervalTrigger(hours=12),
                  id='summarization', name='WS6 — compute summary_short for new documents',
                  misfire_grace_time=1800, **_JOB_DEFAULTS)
# WS5a Phase A — hourly full-ingest canary. Isolated to a dedicated Qdrant
# collection + SMOKE_TEST_* document_id namespace, so it coexists with WS6
# Phase 3 (Gemini lock 7_640_601) and WS11 Phase 6 (autovacuum on
# document_chunks). Records result in pipeline_runs(job_name='00_smoke_test').
scheduler.add_job(scheduled_smoke_test, IntervalTrigger(hours=1),
                  id='smoke_test', name='Hourly full-ingest canary',
                  misfire_grace_time=600, **_JOB_DEFAULTS)
# WS5a Phase A — daily QA digest at 07:00 Europe/Amsterdam (CET/CEST). CronTrigger
# so it fires at a fixed wall-clock time; misfire_grace_time=3600 lets it run up
# to an hour late if the app was restarting at 07:00 exactly.
scheduler.add_job(scheduled_qa_digest,
                  CronTrigger(hour=7, minute=0, timezone='Europe/Amsterdam'),
                  id='qa_digest_daily',
                  name='Daily 07:00 CET QA digest + health email',
                  misfire_grace_time=3600, **_JOB_DEFAULTS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app lifecycle - start scheduler on startup, shutdown on exit"""
    # WS4: Fail fast in production if insecure defaults are still in place
    if os.getenv("ENVIRONMENT") == "production":
        _secret_key = os.getenv("SECRET_KEY", "")
        _db_password = os.getenv("DB_PASSWORD", "")
        if _secret_key in ("", "change-me-in-production"):
            raise RuntimeError(
                "STARTUP ABORTED: SECRET_KEY is unset or equals the insecure default. "
                "Set SECRET_KEY in production environment."
            )
        if _db_password in ("", "postgres"):
            raise RuntimeError(
                "STARTUP ABORTED: DB_PASSWORD is unset or equals the insecure default. "
                "Set DB_PASSWORD in production environment."
            )

    # Seed admin user from environment
    admin_email = os.getenv("ADMIN_EMAIL")
    admin_password = os.getenv("ADMIN_PASSWORD")
    if admin_email and admin_password:
        try:
            if not auth_service.get_user_by_email(admin_email):
                auth_service.create_user(
                    admin_email, admin_password, display_name="Admin", role="admin"
                )
                logger.info(f"Admin user seeded: {admin_email}")
        except Exception as e:
            logger.error(f"Failed to seed admin user: {e}")

    # Warn on weak admin password (minimum 12 chars)
    _admin_pw = os.getenv("ADMIN_PASSWORD", "")
    if _admin_pw and len(_admin_pw) < 12:
        logger.warning(
            "SECURITY: ADMIN_PASSWORD is shorter than 12 characters. "
            "Use a stronger password in production."
        )

    # Startup
    try:
        scheduler.start()
        logger.info("Refresh scheduler started (15-min interval)")
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")

    # Schedule daily session cleanup
    scheduler.add_job(
        cleanup_sessions,
        IntervalTrigger(hours=24),
        id='session_cleanup',
        name='Purge expired sessions daily',
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # Phase 7+ chat-workbench: sweep stale conversations
    try:
        from services.conversation_store import conversation_store
        await conversation_store.start_sweeper()
        logger.info("Chat conversation store sweeper started (5-min interval)")
    except Exception as e:
        logger.error(f"Failed to start conversation sweeper: {e}")

    yield

    # Shutdown
    try:
        scheduler.shutdown()
        logger.info("Scheduler shutdown complete")
    except Exception as e:
        logger.error(f"Failed to shutdown scheduler: {e}")

    # Close the shared database connection pool
    try:
        close_pool()
    except Exception as e:
        logger.error(f"Failed to close DB pool: {e}")


# ── App ──
app = FastAPI(title="NeoDemos", lifespan=lifespan)


# ── Middleware ──

class CanonicalHostRedirectMiddleware(BaseHTTPMiddleware):
    """Permanent 301 from neodemos.eu / www.neodemos.eu to neodemos.nl.

    Previously handled by Caddy; moved here when we switched the public reverse
    proxy to kamal-proxy, which has no native cross-TLD redirect feature.
    """

    async def dispatch(self, request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0].lower()
        if host.endswith("neodemos.eu"):
            target = f"https://neodemos.nl{request.url.path}"
            if request.url.query:
                target += f"?{request.url.query}"
            return RedirectResponse(url=target, status_code=301)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-NeoDemos-Version"] = VERSION_LABEL

        # GrapeJS visual editor needs CDN access (unpkg.com) plus 'unsafe-eval'
        # (well-known GrapeJS internal requirement). Scope the relaxation to the
        # admin editor route only — public pages keep the strict CSP.
        if request.url.path.startswith("/admin/editor"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' https://cdn.jsdelivr.net https://unpkg.com "
                "'unsafe-inline' 'unsafe-eval'; "
                "style-src 'self' https://unpkg.com 'unsafe-inline'; "
                "font-src 'self' data:; "
                "img-src 'self' data: blob: https:; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "font-src 'self'; "
                "img-src 'self' data:; "
                "frame-ancestors 'none'"
            )
        return response


# Note: Starlette runs middleware in reverse registration order for incoming
# requests, so the redirect middleware must be added LAST to execute FIRST.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CanonicalHostRedirectMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://neodemos.nl", "https://www.neodemos.nl"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Liveness probe (Kamal / kamal-proxy convention) ──

@app.get("/up", include_in_schema=False)
async def kamal_up():
    """Liveness probe consumed by kamal-proxy during deploys.
    Must return 200 quickly without touching DB/Qdrant."""
    return PlainTextResponse("ok", status_code=200)


# ── Routers ──
from routes.auth import router as auth_router
from routes.admin import router as admin_router
from routes.pages import router as pages_router
from routes.api import router as api_router

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(pages_router)
app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
