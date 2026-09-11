"""The schema runner: schema.sql for new databases, numbered migrations for old ones.

Every case opens a file database rather than :memory:, because that is what production
does and what the WAL pragma in schema.sql is written for.
"""

import re
import shutil
import sqlite3
from pathlib import Path

import pytest

from backend.app import database
from backend.app.database import (
    MANUAL_BASELINE,
    MIGRATIONS_DIR,
    MigrationError,
    apply_schema_and_migrations,
    get_schema_version,
    latest_schema_version,
    migration_files,
)

SCHEMA = database._SCHEMA.read_text(encoding="utf-8")

# The nanopore_runs columns that migration 003 adds, as schema.sql declares them. Cutting
# this block out of schema.sql gives the table as it was before 003 — the state an older
# database is in when the runner first meets it.
_REPLICATE_COLUMNS = re.compile(
    r"    -- Several barcodes of one run can carry.*?'technical'\)\),\n", re.DOTALL
)
assert _REPLICATE_COLUMNS.search(SCHEMA), "schema.sql no longer has the replicate block this test cuts out"


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def _connect(tmp_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(tmp_path / "odin.db")
    con.row_factory = sqlite3.Row
    return con


def _old_database(tmp_path: Path, *, version: int, without_002: bool = False) -> sqlite3.Connection:
    """A database as an older install left it: current schema minus the 003 columns."""
    text = _REPLICATE_COLUMNS.sub("", SCHEMA)
    if without_002:
        text = text.replace("UNIQUE (biomeme_run_name, sample_id)", "UNIQUE (biomeme_run_name)")
    con = _connect(tmp_path)
    con.executescript(text)
    con.execute(f"PRAGMA user_version = {version}")
    return con


def _migrations_dir(tmp_path: Path, *extra: tuple[str, str]) -> Path:
    """A migrations directory holding the shipped 003 plus any (name, sql) given."""
    d = tmp_path / "migrations"
    d.mkdir()
    for shipped in MIGRATIONS_DIR.glob("003_*.sql"):
        shutil.copy(shipped, d / shipped.name)
    for name, sql in extra:
        (d / name).write_text(sql, encoding="utf-8")
    return d


# ── new and current databases ─────────────────────────────────────────────────


def test_a_new_database_is_stamped_latest_and_applies_nothing(tmp_path):
    con = _connect(tmp_path)

    applied = apply_schema_and_migrations(con)

    assert applied == []
    assert get_schema_version(con) == latest_schema_version()
    assert {"replicate", "replicate_type"} <= _columns(con, "nanopore_runs")


def test_running_again_on_a_current_database_changes_nothing(tmp_path):
    con = _connect(tmp_path)
    apply_schema_and_migrations(con)
    before = get_schema_version(con)

    assert apply_schema_and_migrations(con) == []
    assert get_schema_version(con) == before


# ── older databases ───────────────────────────────────────────────────────────


def test_an_older_database_at_the_baseline_gets_003(tmp_path):
    con = _old_database(tmp_path, version=MANUAL_BASELINE)
    assert "replicate" not in _columns(con, "nanopore_runs")

    applied = apply_schema_and_migrations(con)

    assert applied == [3]
    assert {"replicate", "replicate_type"} <= _columns(con, "nanopore_runs")
    assert get_schema_version(con) == 3


def test_an_untracked_database_with_the_manual_migrations_done_is_stamped_then_migrated(tmp_path):
    # user_version 0 is every database from before the runner existed. The hand-applied
    # 001 and 002 are judged by their effect on the schema, not by a number nobody stored.
    con = _old_database(tmp_path, version=0)

    applied = apply_schema_and_migrations(con)

    assert applied == [3]
    assert get_schema_version(con) == 3


def test_an_untracked_database_missing_002_is_refused_with_the_file_named(tmp_path):
    con = _old_database(tmp_path, version=0, without_002=True)

    with pytest.raises(MigrationError, match="002_biomeme_runs_one_row_per_sample.sql"):
        apply_schema_and_migrations(con)

    # Nothing was applied and nothing was stamped: the operator runs 002 by hand, then
    # starts again and the runner takes it from there.
    assert get_schema_version(con) == 0
    assert "replicate" not in _columns(con, "nanopore_runs")


def test_a_column_already_added_by_hand_does_not_fail_the_migration(tmp_path):
    # Someone applied 003 by hand before upgrading — or an earlier start was interrupted
    # between its two statements. SQLite has no ADD COLUMN IF NOT EXISTS; the runner is it.
    con = _old_database(tmp_path, version=MANUAL_BASELINE)
    con.execute("ALTER TABLE nanopore_runs ADD COLUMN replicate INTEGER")

    applied = apply_schema_and_migrations(con)

    assert applied == [3]
    assert {"replicate", "replicate_type"} <= _columns(con, "nanopore_runs")
    assert get_schema_version(con) == 3


# ── the sequence itself ───────────────────────────────────────────────────────


def test_a_failing_migration_names_the_file_and_leaves_the_version_where_it_got_to(tmp_path):
    broken = ("004_broken.sql", "ALTER TABLE no_such_table ADD COLUMN x INTEGER;\n")
    con = _old_database(tmp_path, version=MANUAL_BASELINE)

    with pytest.raises(MigrationError, match="004_broken.sql"):
        apply_schema_and_migrations(con, _migrations_dir(tmp_path, broken))

    # 003 was applied and stamped before 004 failed; 004 was rolled back.
    assert get_schema_version(con) == 3
    assert "x" not in _columns(con, "nanopore_runs")
    assert not con.in_transaction


def test_a_gap_in_the_numbering_is_refused(tmp_path):
    gap = ("005_too_far.sql", "ALTER TABLE nanopore_runs ADD COLUMN later INTEGER;\n")
    con = _old_database(tmp_path, version=MANUAL_BASELINE)

    with pytest.raises(MigrationError, match=r"005.*no migration 004"):
        apply_schema_and_migrations(con, _migrations_dir(tmp_path, gap))

    assert get_schema_version(con) == 3   # 003 went through; 005 did not


def test_two_files_sharing_a_number_are_refused(tmp_path):
    twin = ("003_second_opinion.sql", "SELECT 1;\n")

    with pytest.raises(MigrationError, match="003"):
        migration_files(_migrations_dir(tmp_path, twin))


def test_the_shipped_migrations_run_contiguously_from_the_first(tmp_path):
    numbers = [n for n, _ in migration_files()]
    assert numbers == list(range(1, numbers[-1] + 1)), numbers
    assert numbers[-1] > MANUAL_BASELINE, "nothing for the runner to apply"


def test_the_migrations_live_inside_the_package_the_image_copies():
    # Dockerfile.backend copies backend/ and nothing above it. A migrations directory at
    # the repository root would be absent from every container, and the runner would find
    # nothing to apply while the tests, run from a checkout, all passed.
    assert MIGRATIONS_DIR.parent == Path(database.__file__).parent
    assert MIGRATIONS_DIR.is_dir()
