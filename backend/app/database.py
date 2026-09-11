import logging
import os
import re
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

#: Numbered SQL files applied at startup to bring an older database up to schema.sql.
#: Inside the package rather than at the repository root because the Docker image copies
#: backend/ and nothing above it; a directory the image does not contain would leave every
#: deployment unmigrated while the tests passed.
MIGRATIONS_DIR = Path(__file__).parent / "migrations"

#: 001 and 002 are table rebuilds applied by hand, from before the schema version was
#: tracked. The runner never applies them: it cannot tell a database that needs them from
#: one that already has their effect by number alone, so it checks for the effect instead
#: (_missing_manual_migrations) and refuses to start without it. Automatic application
#: begins at the next number.
MANUAL_BASELINE = 2

_MIGRATION_NAME = re.compile(r"^(\d{3})_.+\.sql$")
_ADD_COLUMN = re.compile(
    r"^\s*ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)", re.IGNORECASE
)

logger = logging.getLogger(__name__)


class MigrationError(RuntimeError):
    """The database cannot be brought up to date; the message says why and what to do."""


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
    """Create the schema, and bring an existing database up to date."""
    con = get_connection()
    try:
        apply_schema_and_migrations(con)
    finally:
        con.close()


# ── Schema version and migrations ─────────────────────────────────────────────


def get_schema_version(con: sqlite3.Connection) -> int:
    return int(con.execute("PRAGMA user_version").fetchone()[0])


def _set_schema_version(con: sqlite3.Connection, version: int) -> None:
    # PRAGMA takes no parameters; the int() keeps the interpolation to a number.
    con.execute(f"PRAGMA user_version = {int(version)}")


def migration_files(directory: Path = None) -> list[tuple[int, Path]]:
    """The migrations on disk as (number, path), in order. Two files sharing a number is
    an error here rather than a surprise later."""
    directory = MIGRATIONS_DIR if directory is None else directory
    found: dict[int, Path] = {}
    for path in sorted(directory.glob("*.sql")) if directory.is_dir() else []:
        m = _MIGRATION_NAME.match(path.name)
        if not m:
            continue
        number = int(m.group(1))
        if number in found:
            raise MigrationError(
                f"Two migration files carry the number {number:03d}: "
                f"{found[number].name} and {path.name} in {directory}"
            )
        found[number] = path
    return sorted(found.items())


def latest_schema_version(directory: Path = None) -> int:
    numbers = [n for n, _ in migration_files(directory)]
    return max(numbers, default=MANUAL_BASELINE)


def _table_sql(con: sqlite3.Connection, table: str) -> str:
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row[0] if row and row[0] else ""


def _missing_manual_migrations(con: sqlite3.Connection) -> list[str]:
    """Which of the hand-applied migrations an untracked database still lacks, judged by
    their effect on the schema — the same checks their headers tell an operator to run."""
    missing = []
    if not re.search(r"\bsite_id\s+TEXT\s+NOT\s+NULL\b", _table_sql(con, "samples")):
        missing.append("001_samples_require_site_and_type.sql")
    if not re.search(
        r"UNIQUE\s*\(\s*biomeme_run_name\s*,\s*sample_id\s*\)", _table_sql(con, "biomeme_runs")
    ):
        missing.append("002_biomeme_runs_one_row_per_sample.sql")
    return missing


def _column_exists(con: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in con.execute(f"PRAGMA table_info({table})"))


def _statements(sql_text: str) -> list[str]:
    """Split a migration file into statements, comments removed. Good enough for files
    this repository writes: no semicolons inside string literals."""
    body = "\n".join(
        line for line in sql_text.splitlines() if not line.lstrip().startswith("--")
    )
    return [s.strip() for s in body.split(";") if s.strip()]


def _apply_migration(con: sqlite3.Connection, number: int, path: Path) -> None:
    """Run one migration file in a transaction and stamp its number.

    An ``ALTER TABLE … ADD COLUMN`` whose column is already there is skipped, so a file
    that was applied by hand before the runner existed — or a run interrupted after some
    of its statements — does not fail on the second attempt. SQLite has no
    ``ADD COLUMN IF NOT EXISTS``; this is that.
    """
    to_run = []
    for statement in _statements(path.read_text(encoding="utf-8")):
        m = _ADD_COLUMN.match(statement)
        if m and _column_exists(con, m.group(1), m.group(2)):
            logger.info(
                "Migration %03d: %s.%s already exists, statement skipped",
                number, m.group(1), m.group(2),
            )
            continue
        to_run.append(statement)
    script = "BEGIN;\n" + ";\n".join(to_run) + (";\n" if to_run else "") \
        + f"PRAGMA user_version = {int(number)};\nCOMMIT;"
    try:
        con.executescript(script)
    except sqlite3.Error as exc:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise MigrationError(
            f"Migration {path.name} failed and was rolled back: {exc}. "
            f"The database is still at schema version {get_schema_version(con)}; "
            f"fix the cause and start again."
        ) from exc


def apply_schema_and_migrations(con: sqlite3.Connection, directory: Path = None) -> list[int]:
    """Create any missing tables from schema.sql, then apply the migrations an existing
    database has not had. Returns the numbers applied.

    A new database gets the current schema and is stamped with the latest version, so it
    never runs a migration whose effect it already has. A database from before the version
    was tracked (``user_version`` 0) is accepted only if the hand-applied migrations have
    been done — their effect is checked, not assumed — and is then stamped at the baseline
    before anything newer is applied. Raises MigrationError, with the file named, when a
    migration fails or one is missing from the sequence; startup stops rather than running
    against a schema the code does not expect.
    """
    directory = MIGRATIONS_DIR if directory is None else directory
    fresh = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
    ).fetchone()[0] == 0
    con.executescript(_SCHEMA.read_text(encoding="utf-8"))

    files = migration_files(directory)
    latest = max([n for n, _ in files], default=MANUAL_BASELINE)

    if fresh:
        _set_schema_version(con, latest)
        logger.info("Database created at schema version %d", latest)
        return []

    version = get_schema_version(con)
    if version == 0:
        missing = _missing_manual_migrations(con)
        if missing:
            listed = "\n".join(f"    {name}" for name in missing)
            raise MigrationError(
                "This database predates schema version tracking and still needs the "
                "hand-applied migration(s) below. They rebuild tables and are not run "
                "automatically. Stop the application, back up the database, then run each "
                "file's PRE-FLIGHT and MIGRATION sections as its header describes, in order:\n"
                f"{listed}\n"
                f"Files are in {directory}."
            )
        version = MANUAL_BASELINE
        _set_schema_version(con, version)

    applied: list[int] = []
    for number, path in files:
        if number <= version:
            continue
        if number != version + 1:
            raise MigrationError(
                f"Migration {number:03d} ({path.name}) cannot be applied: the database is at "
                f"version {version} and no migration {version + 1:03d} exists in {directory}."
            )
        _apply_migration(con, number, path)
        logger.info("Applied database migration %03d (%s)", number, path.name)
        version = number
        applied.append(number)
    return applied


def get_db():
    """FastAPI dependency: yields an open sqlite3 connection, closes on exit."""
    con = get_connection()
    try:
        yield con
    finally:
        con.close()
