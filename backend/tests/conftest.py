"""
Shared pytest fixtures for the ODIN backend test suite.

Strategy
--------
* Environment variables are set at module level (before any backend imports)
  so that `database.DB_PATH` resolves to a temp directory.
* Each test gets a fresh **in-memory** SQLite connection injected via
  FastAPI's dependency-override mechanism, keeping tests fully isolated.
* The FastAPI app lifespan (init_db + seed) runs against the temp-dir file DB;
  test routes use the in-memory override — the two never interact.
"""

import os
import sqlite3
import tempfile
import uuid
from pathlib import Path

# ── Must be set before any backend module is imported ──────────────────────
_tmp = tempfile.mkdtemp(prefix="odin_test_")
os.environ.setdefault("ODIN_PIPELINE_ROOT", _tmp)
os.environ.setdefault("ODIN_DATA_DIR", _tmp)
# ODIN_WORK_DIR may be a WSL2-native path in .env that does not exist on the
# Windows host where tests run — set to empty so load_dotenv() in main.py
# does not override it and the startup directory check is skipped.
os.environ["ODIN_WORK_DIR"] = ""
# Same mechanism, generalised: every settings key that can be overridden by an
# environment variable is blanked here.
#
# Why this is necessary: main.py calls load_dotenv(), so importing the backend pulls
# the developer's real .env into the test process, and an env override beats a DB
# value (the documented 12-factor priority). A test that writes a DB value and then
# asserts on it therefore *silently depends on the developer not having that variable
# set* — it passes by luck of local configuration rather than because its premise
# holds. Measured on this repo: a .env containing ODIN_PATHOGENS_FILE fails 8 tests,
# NEXTFLOW_CONFIG_FILE fails 10, and NEXTFLOW_PROFILE / ODIN_ENLIGHTEN_URL /
# ODIN_ENLIGHTEN_DATA_PATH / ODIN_DEVICE_NAME one or two each. In every case the
# production code was behaving correctly — it honoured the override, or correctly
# reported a file it could not find — so blanking here fixes the tests' premise and
# changes no behaviour.
#
# Empty reads as unset (get_env_overrides strips blanks) and load_dotenv() will not
# replace an already-set variable. Tests that want an override set it explicitly with
# monkeypatch, which is where env-precedence is actually covered
# (test_settings_resolver.py, test_device_name.py).
#
# Keep in step with settings_resolver._ENV_VAR_KEYS —
# test_conftest_env_isolation.py fails if a new override key is added without being
# neutralised here.
NEUTRALISED_ENV_VARS = (
    "ODIN_MINKNOW_DIR",
    "ODIN_DATABASES_FILE",
    "ODIN_PATHOGENS_FILE",
    "ODIN_EXTRACT_TARGETS_FILE",
    "NEXTFLOW_CONFIG_FILE",
    "NEXTFLOW_PROFILE",
    "ODIN_ENLIGHTEN_URL",
    "ODIN_ENLIGHTEN_DATA_PATH",
    "ODIN_DEVICE_NAME",
)
for _var in NEUTRALISED_ENV_VARS:
    os.environ[_var] = ""
# Create the default Nextflow config file so _validate_pre_launch passes
# when the DB/env has no explicit nextflow_config_file override.
_nf_config_dir = Path(_tmp) / "config"
_nf_config_dir.mkdir(parents=True, exist_ok=True)
(_nf_config_dir / "odin.config").write_text("// test stub")
# ───────────────────────────────────────────────────────────────────────────

import pytest
from fastapi.testclient import TestClient

_SCHEMA = Path(__file__).parents[1] / "app" / "schema.sql"


def _seed_test_lookup_values(con: sqlite3.Connection) -> None:
    """Seed the lookup codes used across the test suite.

    Tests were written before Item 6 validation was added and use short
    human-readable codes that are not in the production seed file.  Rather
    than changing every test assertion, we insert these codes into the
    in-memory test DB here.
    """
    entries = [
        # sample_type
        ("sample_type", "water",    "Water (test)"),
        ("sample_type", "sediment", "Sediment (test)"),
        # protocol_id — tests use a kit name rather than ODIN shortcode
        ("protocol_id", "SQK-LSK114", "Ligation sequencing (test)"),
        # sequencing_kit_id
        ("sequencing_kit_id", "SQK-LSK114", "Ligation sequencing (test)"),
        # mpox_type — tests use "GridION" as a stand-in device/type value
        ("mpox_type", "GridION", "GridION (test)"),
    ]
    for list_name, code, desc in entries:
        con.execute(
            "INSERT OR IGNORE INTO lookup_values (id, list, code, description)"
            " VALUES (?,?,?,?)",
            (str(uuid.uuid4()), list_name, code, desc),
        )
    con.commit()


def make_test_db() -> sqlite3.Connection:
    """Return a fresh in-memory SQLite connection initialised with the full schema."""
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    # WAL is not supported for :memory: — strip the pragma so executescript
    # doesn't leave the connection in an unexpected state.
    schema = _SCHEMA.read_text(encoding="utf-8")
    schema = "\n".join(line for line in schema.splitlines() if "journal_mode" not in line.lower())
    con.executescript(schema)
    _seed_test_lookup_values(con)
    return con


@pytest.fixture()
def db() -> sqlite3.Connection:
    """Yield a clean in-memory DB per test, then close it."""
    con = make_test_db()
    yield con
    con.close()


@pytest.fixture()
def client(db: sqlite3.Connection) -> TestClient:
    """
    Yield a FastAPI TestClient whose get_db dependency is overridden to use
    the test-scoped in-memory DB.
    """
    from backend.app.database import get_db
    from backend.app.main import app

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()
