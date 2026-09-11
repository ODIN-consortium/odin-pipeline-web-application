import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional, Union

from .utils import coerce_path

_pipeline_root = os.getenv("ODIN_PIPELINE_ROOT")
_default_data_dir = (
    str(Path(coerce_path(_pipeline_root)) / ".odin-app")
    if _pipeline_root
    else str(Path.home() / ".odin-app")
)
_data_dir = Path(coerce_path(os.getenv("ODIN_DATA_DIR", _default_data_dir)))
_data_dir.mkdir(parents=True, exist_ok=True)

DB_PATH = _data_dir / "odin.db"

_SCHEMA = Path(__file__).parent / "schema.sql"

logger = logging.getLogger(__name__)


def connect_sqlite_with_retry(
    db_path: Union[str, Path],
    *,
    retries: int = 6,
    initial_delay_seconds: float = 0.2,
    backoff_factor: float = 1.8,
    context: str = "sqlite",
) -> sqlite3.Connection:
    """Open a SQLite connection with gentle retry for transient open failures."""
    path_str = str(db_path)
    delay = max(0.01, initial_delay_seconds)
    last_error: Optional[sqlite3.OperationalError] = None

    for attempt in range(1, retries + 1):
        try:
            con = sqlite3.connect(path_str, check_same_thread=False)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode = WAL")
            con.execute("PRAGMA foreign_keys = ON")
            return con
        except sqlite3.OperationalError as exc:
            last_error = exc
            msg = str(exc).lower()
            is_retryable = "unable to open database file" in msg
            if not is_retryable or attempt >= retries:
                raise
            logger.warning(
                "SQLite open failed (%s) for %s (attempt %d/%d): %s; retrying in %.2fs",
                context,
                path_str,
                attempt,
                retries,
                exc,
                delay,
            )
            time.sleep(delay)
            delay *= backoff_factor

    if last_error is not None:
        raise last_error
    raise sqlite3.OperationalError("Unable to open database file")


def get_connection() -> sqlite3.Connection:
    """Open a connection with row_factory and foreign keys enabled."""
    return connect_sqlite_with_retry(DB_PATH, context="api request")


def init_db() -> None:
    """Create all tables from schema.sql."""
    con = get_connection()
    con.executescript(_SCHEMA.read_text(encoding="utf-8"))
    con.close()


def get_db():
    """FastAPI dependency: yields an open sqlite3 connection, closes on exit."""
    con = get_connection()
    try:
        yield con
    finally:
        con.close()
