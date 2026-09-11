"""The temporary-workspace cleanup callback: what it removes, and what it records.

Cleanup now runs on the failure paths too (a preparation error used to skip it and leak
the workspace — see test_executor.py). On those paths the workspace is the main evidence
of what went wrong, so its path and size are written to the run log before it is deleted:
the operator gets the diagnostic detail from the log they already read, without the
directory quietly consuming gigabytes under ODIN_TMP_DIR — a path docker-compose
bind-mounts host:container identically for DooD, so leaks land on the host and survive
container restarts.
"""

import sqlite3
import uuid
from pathlib import Path

import pytest

from backend.app.api.pipeline import _describe_dir, _make_tmp_cleanup_fn
from backend.tests.conftest import _SCHEMA

_NOW = "2026-07-30T00:00:00.000Z"


@pytest.fixture()
def db_file(tmp_path: Path) -> str:
    path = tmp_path / "odin.db"
    con = sqlite3.connect(path)
    schema = _SCHEMA.read_text(encoding="utf-8")
    con.executescript(
        "\n".join(ln for ln in schema.splitlines() if "journal_mode" not in ln.lower())
    )
    con.commit()
    con.close()
    return str(path)


def _insert_run(db_file: str, work_dir: str) -> str:
    run_id = str(uuid.uuid4())
    con = sqlite3.connect(db_file)
    con.execute(
        """INSERT INTO pipeline_runs
           (id, pipeline_type, status, params, work_dir, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?)""",
        (run_id, "taxprofiler", "failed", "{}", work_dir, _NOW, _NOW),
    )
    con.commit()
    con.close()
    return run_id


def _work_dir_of(db_file: str, run_id: str):
    con = sqlite3.connect(db_file)
    try:
        return con.execute(
            "SELECT work_dir FROM pipeline_runs WHERE id = ?", (run_id,)
        ).fetchone()[0]
    finally:
        con.close()


# ── _describe_dir ─────────────────────────────────────────────────────────────


def test_describe_dir_counts_files_and_reports_size(tmp_path: Path) -> None:
    (tmp_path / "a.fastq.gz").write_bytes(b"x" * 1024)
    (tmp_path / "b.fastq.gz").write_bytes(b"y" * 2048)
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "c.fastq.gz").write_bytes(b"z" * 512)

    described = _describe_dir(tmp_path)

    assert "3 file(s)" in described, described
    assert "MB" in described


def test_describe_dir_ignores_directories_in_the_count(tmp_path: Path) -> None:
    (tmp_path / "only-a-dir").mkdir()
    assert "0 file(s)" in _describe_dir(tmp_path)


def test_describe_dir_reports_zero_for_a_missing_directory(tmp_path: Path) -> None:
    """rglob on a nonexistent path yields nothing rather than raising, so this is "0"."""
    assert "0 file(s)" in _describe_dir(tmp_path / "does-not-exist")


def test_describe_dir_degrades_when_the_scan_itself_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measuring the workspace must never mask the failure that led us here.

    Walking it can fail for reasons outside our control — an unreadable subdirectory, or
    the workspace being torn down by a still-running writer. (Note `is_file()` swallows a
    vanished file itself, so the reachable trigger is the traversal raising, not a single
    stat.)
    """
    (tmp_path / "reads.fastq.gz").write_bytes(b"x" * 16)

    def unreadable(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "rglob", unreadable)

    assert "could not be measured" in _describe_dir(tmp_path)


# ── cleanup_fn ────────────────────────────────────────────────────────────────


def test_cleanup_removes_the_workspace_and_clears_work_dir(
    db_file: str, tmp_path: Path
) -> None:
    workspace = tmp_path / "concat"
    workspace.mkdir()
    (workspace / "reads.fastq.gz").write_bytes(b"x" * 4096)
    run_id = _insert_run(db_file, str(workspace))

    _make_tmp_cleanup_fn(run_id, workspace, db_file)()

    assert not workspace.exists()
    assert _work_dir_of(db_file, run_id) is None


def test_cleanup_records_the_workspace_in_the_run_log_before_deleting(
    db_file: str, tmp_path: Path
) -> None:
    """The diagnostic half of the fix: the log must name what was thrown away."""
    workspace = tmp_path / "concat"
    workspace.mkdir()
    (workspace / "reads.fastq.gz").write_bytes(b"x" * 4096)
    log_path = tmp_path / "run.log"
    run_id = _insert_run(db_file, str(workspace))

    _make_tmp_cleanup_fn(run_id, workspace, db_file, log_path)()

    logged = log_path.read_text(encoding="utf-8")
    assert str(workspace) in logged
    assert "1 file(s)" in logged
    assert not workspace.exists()


def test_cleanup_without_a_log_path_still_removes_the_workspace(
    db_file: str, tmp_path: Path
) -> None:
    """log_path is optional, so the absence of a log must not skip the removal."""
    workspace = tmp_path / "concat"
    workspace.mkdir()
    run_id = _insert_run(db_file, str(workspace))

    _make_tmp_cleanup_fn(run_id, workspace, db_file, None)()

    assert not workspace.exists()


def test_cleanup_is_a_noop_when_the_workspace_is_already_gone(
    db_file: str, tmp_path: Path
) -> None:
    """Cleanup now runs on more paths, so it must tolerate having nothing to do."""
    workspace = tmp_path / "never-created"
    log_path = tmp_path / "run.log"
    run_id = _insert_run(db_file, str(workspace))

    _make_tmp_cleanup_fn(run_id, workspace, db_file, log_path)()

    # Nothing to describe, and work_dir is still cleared.
    assert not log_path.exists()
    assert _work_dir_of(db_file, run_id) is None
