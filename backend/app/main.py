import logging
import os
import secrets
import sys
from base64 import b64decode
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # load .env from cwd (or any parent) before reading env vars

# ── Logging setup ─────────────────────────────────────────────────────────────
# Let uvicorn own the root logger to avoid duplicate output.
# Configure only the app-specific "odin" logger here.
# The LOG_LEVEL env var controls the level.  Example:
#   LOG_LEVEL=DEBUG docker compose up
_log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.getLogger("odin").setLevel(_log_level)

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from .api import (
    autocomplete_router,
    biomeme_discovery_router,
    biomeme_runs_router,
    databases_router,
    discovery_router,
    export_router,
    lookup_values_router,
    nanopore_run_accessions_router,
    nanopore_runs_router,
    pipeline_router,
    samples_router,
    settings_router,
    sites_router,
    sync_router,
)
from .api.biomeme_runs import seed_biomeme_runs
from .api.lookup_values import seed_lookup_values
from .api.nanopore_runs import seed_nanopore_runs
from .api.pipeline import requeue_pending_runs
from .api.samples import seed_samples
from .api.sites import seed_sites
from .database import DB_PATH, get_connection, init_db
from .pipeline.executor import shutdown as shutdown_executor
from .utils import coerce_path


def _check_pipeline_root() -> None:
    """Refuse to start without ODIN_PIPELINE_ROOT; warn if its path does not exist.

    ODIN_PIPELINE_ROOT is required and has no default: every path setting
    (minknow_dir, output_dir, biomeme_dir, ...) is derived from it, so guessing a
    value would silently point the whole installation at a directory the operator
    never chose.  Unset is a configuration error, not a missing directory, and it is
    fatal — matching `docker compose`, which already cannot start without it because
    it interpolates the variable into a bind mount.  `docker-entrypoint.sh` checks the
    same thing earlier and more readably; this covers a bare `uvicorn` run.

    A path that is set but does not yet exist is only a warning: the directory may be
    mounted or created later, individual pipeline launches and the /settings/health
    endpoint report it clearly, and blocking startup would stop an operator opening
    the UI to fix the configuration.
    """
    root = os.getenv("ODIN_PIPELINE_ROOT", "").strip()
    if not root:
        sys.stderr.write(
            "\nFATAL: ODIN_PIPELINE_ROOT is not set.\n"
            "  It is required and has no default — every path setting (minknow_dir,\n"
            "  output_dir, biomeme_dir, ...) is derived from it.\n"
            "  Set it in .env (see .env.example), for example:\n"
            "    ODIN_PIPELINE_ROOT=/mnt/<drive>/ODIN   # WSL2 / Docker on Windows\n"
            "    ODIN_PIPELINE_ROOT=/srv/odin           # Linux\n\n"
        )
        raise SystemExit(1)

    resolved = coerce_path(root)
    if not Path(resolved).exists():
        sys.stderr.write(
            f"\nWARNING: ODIN_PIPELINE_ROOT does not exist: {root}\n"
            f"  Resolved to: {resolved}\n"
            f"  Set ODIN_PIPELINE_ROOT in .env (or create the directory) before launching pipelines.\n"
            f"  The app will start but pipelines will fail until the path is available.\n\n"
        )


def _check_work_dir() -> None:
    """Warn at startup if ODIN_WORK_DIR is set but not accessible from this process.

    The backend only passes ODIN_WORK_DIR as a string to Nextflow; it never accesses
    the path directly.  When running on Windows the WSL2-native path (e.g.
    /home/user/odin_work) is not visible to the Windows process, so a hard check
    would abort the server even though everything would work fine at pipeline-launch
    time.  A warning is enough to catch genuine typos without blocking startup.
    """
    work_dir = os.getenv("ODIN_WORK_DIR", "").strip()
    if not work_dir:
        return

    resolved = coerce_path(work_dir)
    if not Path(resolved).is_dir():
        sys.stderr.write(
            f"\nWARNING: ODIN_WORK_DIR is set but not accessible from this process: {work_dir}\n"
            f"  Resolved to: {resolved}\n"
            f"  If this is a WSL2-native path, ensure it exists in WSL2 before launching pipelines:\n"
            f"    mkdir -p {work_dir}\n\n"
        )


# Validate eagerly — before the async lifespan starts — so uvicorn does not
# wrap the SystemExit in a traceback.
# NOTE: these are kept at module level intentionally for pre-startup stderr warnings.
# Database check is inside lifespan() to avoid duplicate output from multiple workers.

logger = logging.getLogger("odin")


def _check_database_volume() -> None:
    """Log a warning at startup if the Kraken2 database path is not available."""
    db_path_str = os.getenv("ODIN_DATABASE_PATH", "").strip()
    if not db_path_str:
        # Derive default the same way command_builder does
        pipeline_root = os.getenv("ODIN_PIPELINE_ROOT", "").strip()
        if not pipeline_root:
            return  # nothing to check — _check_pipeline_root already reported this
        db_path_str = pipeline_root.rstrip("/") + "/databases"
    db_path = Path(coerce_path(db_path_str))
    if not db_path.exists():
        logger.warning(
            f"Database directory does not exist: {db_path_str}. "
            "Taxprofiler runs will fail. Create the directory and place your "
            "Kraken2 database there, or set ODIN_DATABASE_PATH in .env."
        )
    elif not list(db_path.rglob("*.k2d")):
        logger.warning(
            f"Database directory ({db_path_str}) contains no .k2d files. "
            "Populate the directory with Kraken2 database files — see README."
        )
    else:
        logger.info(f"Kraken2 database OK at {db_path_str}")


def _cors_origins() -> list[str]:
    """Read allowed origins from CORS_ORIGINS env var (comma-separated) or use defaults."""
    env = os.getenv("CORS_ORIGINS", "").strip()
    if env:
        return [o.strip() for o in env.split(",") if o.strip()]
    return ["http://localhost:4200", "http://localhost:8080"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_pipeline_root()
    _check_work_dir()
    _check_database_volume()
    init_db()
    db = get_connection()
    try:
        seed_lookup_values(db)
        seed_sites(db)
        seed_samples(db)
        seed_biomeme_runs(db)
        seed_nanopore_runs(db)
    finally:
        db.close()

    # Re-enqueue any pipeline runs that were queued or running when the server
    # last stopped.  Interrupted runs are restarted with -resume so Nextflow
    # can skip already-completed tasks.
    requeue_count = requeue_pending_runs(str(DB_PATH))
    if requeue_count:
        logger.info("Re-queued %d pending pipeline run(s) from previous session.", requeue_count)
    yield
    # ── Shutdown ──────────────────────────────────────────────────────────────
    # Terminate any running pipeline process so the container exits cleanly.
    # The run will be re-queued on next startup (requeue_pending_runs above).
    shutdown_executor()


app = FastAPI(
    title="ODIN Pipeline API",
    version="0.1.0",
    description="Backend API for the ODIN pipeline web application.",
    lifespan=lifespan,
)

# ── Optional HTTP Basic Auth ──────────────────────────────────────────────────
# Mirrors the frontend nginx Basic Auth: active only when BOTH ODIN_AUTH_USER and
# ODIN_AUTH_PASSWORD are set. Use the SAME credentials as the frontend so the
# Authorization header nginx forwards validates here too. Defense in depth for
# requests that reach the API without passing the frontend (e.g. another container
# on the Compose network). A no-op when the vars are unset.
# Added (below) before CORSMiddleware so CORS stays outermost — preflight OPTIONS
# and 401 responses still receive CORS headers.
_AUTH_USER = os.getenv("ODIN_AUTH_USER", "")
_AUTH_PASSWORD = os.getenv("ODIN_AUTH_PASSWORD", "")


async def _basic_auth(request, call_next):
    if _AUTH_USER and _AUTH_PASSWORD and request.method != "OPTIONS":
        header = request.headers.get("Authorization", "")
        authorized = False
        if header.startswith("Basic "):
            try:
                user, _, password = b64decode(header[6:]).decode("utf-8").partition(":")
                authorized = secrets.compare_digest(user, _AUTH_USER) and secrets.compare_digest(
                    password, _AUTH_PASSWORD
                )
            except (ValueError, UnicodeDecodeError):
                authorized = False
        if not authorized:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="ODIN"'})
    return await call_next(request)


app.add_middleware(BaseHTTPMiddleware, dispatch=_basic_auth)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (
    sites_router,
    nanopore_run_accessions_router,
    nanopore_runs_router,
    biomeme_runs_router,
    biomeme_discovery_router,
    samples_router,
    lookup_values_router,
    settings_router,
    export_router,
    autocomplete_router,
    discovery_router,
    pipeline_router,
    databases_router,
    sync_router,
):
    app.include_router(r, prefix="/api")

