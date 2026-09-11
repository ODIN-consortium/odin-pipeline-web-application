"""Characterization tests for the pipeline executor's job lifecycle.

`_run_pipeline` is what the single worker thread runs per job: prepare → resolve
command → subprocess → status update → post-process → cleanup. Every prior test
mocked `executor.launch` away, so none of this was covered.

These call `_run_pipeline` directly (not through the worker thread) against a real
temp-file SQLite DB, and use list commands so `wrap_cmd` is bypassed — a list is
executed as-is, which keeps the tests fast and platform-independent.
"""

import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

from backend.app.pipeline import executor
from backend.tests.conftest import _SCHEMA

_NOW = "2025-01-01T00:00:00.000Z"
_OK_CMD = [sys.executable, "-c", "pass"]
_FAIL_CMD = [sys.executable, "-c", "raise SystemExit(3)"]


@pytest.fixture()
def db_file(tmp_path: Path) -> str:
    """A real on-disk DB — the executor opens its own connections by path."""
    path = tmp_path / "odin.db"
    con = sqlite3.connect(path)
    schema = _SCHEMA.read_text(encoding="utf-8")
    con.executescript(
        "\n".join(ln for ln in schema.splitlines() if "journal_mode" not in ln.lower())
    )
    con.commit()
    con.close()
    return str(path)


def _insert_queued_run(db_file: str) -> str:
    run_id = str(uuid.uuid4())
    con = sqlite3.connect(db_file)
    con.execute(
        """INSERT INTO pipeline_runs
           (id, pipeline_type, status, params, created_at, updated_at, created_by)
           VALUES (?,?,?,?,?,?,?)""",
        (run_id, "taxprofiler", "queued", "{}", _NOW, _NOW, "test"),
    )
    con.commit()
    con.close()
    return run_id


def _read_run(db_file: str, run_id: str) -> sqlite3.Row:
    con = sqlite3.connect(db_file)
    con.row_factory = sqlite3.Row
    try:
        return con.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        con.close()


def _job(db_file: str, tmp_path: Path, **overrides) -> executor.Job:
    run_id = overrides.pop("run_id", None) or _insert_queued_run(db_file)
    fields = {
        "run_id": run_id,
        "cmd": _OK_CMD,
        "log_path": tmp_path / "run.log",
        "db_path": db_file,
    }
    fields.update(overrides)
    return executor.Job(**fields)


# ── happy path ────────────────────────────────────────────────────────────────


def test_successful_run_is_marked_done_with_exit_code(db_file: str, tmp_path: Path) -> None:
    job = _job(db_file, tmp_path)

    executor._run_pipeline(job)

    row = _read_run(db_file, job.run_id)
    assert row["status"] == "done"
    assert row["exit_code"] == 0
    assert row["started_at"] and row["finished_at"]
    # The resolved command is logged so it shows up in the SSE stream.
    assert "[ODIN] Command:" in job.log_path.read_text()


def test_failing_command_is_marked_failed_with_its_exit_code(
    db_file: str, tmp_path: Path
) -> None:
    job = _job(db_file, tmp_path, cmd=_FAIL_CMD)

    executor._run_pipeline(job)

    row = _read_run(db_file, job.run_id)
    assert row["status"] == "failed"
    assert row["exit_code"] == 3


def test_missing_log_directory_is_created(db_file: str, tmp_path: Path) -> None:
    job = _job(db_file, tmp_path, log_path=tmp_path / "logs" / "nested" / "run.log")

    executor._run_pipeline(job)

    assert job.log_path.exists()


# ── prepare step ──────────────────────────────────────────────────────────────


def test_prepare_failure_fails_the_run_without_running_the_command(
    db_file: str, tmp_path: Path
) -> None:
    def boom() -> None:
        raise RuntimeError("concat exploded")

    job = _job(db_file, tmp_path, cmd=_FAIL_CMD, prepare_fn=boom)

    executor._run_pipeline(job)

    row = _read_run(db_file, job.run_id)
    assert row["status"] == "failed"
    assert row["exit_code"] is None  # no subprocess ever started
    log = job.log_path.read_text()
    assert "Preparation failed: concat exploded" in log
    assert "[ODIN] Command:" not in log


def test_prepare_success_is_announced_in_the_log(db_file: str, tmp_path: Path) -> None:
    job = _job(db_file, tmp_path, prepare_fn=lambda: None)

    executor._run_pipeline(job)

    log = job.log_path.read_text()
    assert "Preparing inputs" in log
    assert "Inputs ready." in log
    assert _read_run(db_file, job.run_id)["status"] == "done"


def test_cleanup_runs_when_preparation_fails(db_file: str, tmp_path: Path) -> None:
    """A preparation failure must still release the temporary FASTQ workspace.

    This test previously asserted the opposite, documenting the leak: `_run_pipeline`
    opened its try/finally *after* the prepare and command-resolution early returns, so
    neither reached `_run_cleanup`. The workspace lives under ODIN_TMP_DIR, which
    docker-compose bind-mounts host:container identically for DooD, so leaked
    directories accumulated on the host and survived container restarts — and a bad
    input file fails on every retry, with concatenated nanopore FASTQ measured in
    gigabytes.
    """
    cleaned: list[bool] = []

    def boom() -> None:
        raise RuntimeError("nope")

    job = _job(
        db_file,
        tmp_path,
        prepare_fn=boom,
        cleanup_fn=lambda: cleaned.append(True),
    )

    executor._run_pipeline(job)

    assert cleaned == [True]
    assert _read_run(db_file, job.run_id)["status"] == "failed"


def test_cleanup_runs_when_preparation_produces_no_command(
    db_file: str, tmp_path: Path
) -> None:
    """The second leak path: prepare succeeded but left the cmd_holder empty.

    By this point prepare_fn has already built the workspace, so skipping cleanup here
    leaked exactly as a prepare failure did.
    """
    cleaned: list[bool] = []
    holder: list[str] = []

    job = _job(
        db_file,
        tmp_path,
        prepare_fn=lambda: None,
        cleanup_fn=lambda: cleaned.append(True),
        cmd_holder=holder,
    )

    executor._run_pipeline(job)

    assert cleaned == [True]
    assert _read_run(db_file, job.run_id)["status"] == "failed"


# ── cmd_holder — the command produced by prepare_fn ───────────────────────────


def test_command_from_cmd_holder_is_executed(db_file: str, tmp_path: Path) -> None:
    marker = tmp_path / "ran.txt"
    holder: list = []

    def prepare() -> None:
        holder.append([sys.executable, "-c", f"open(r'{marker}', 'w').write('ok')"])

    job = _job(db_file, tmp_path, cmd="ignored", prepare_fn=prepare, cmd_holder=holder)

    executor._run_pipeline(job)

    assert marker.read_text() == "ok"
    assert _read_run(db_file, job.run_id)["status"] == "done"


def test_empty_cmd_holder_fails_the_run(db_file: str, tmp_path: Path) -> None:
    """prepare_fn returned without producing a command — there is nothing to run."""
    job = _job(db_file, tmp_path, prepare_fn=lambda: None, cmd_holder=[])

    executor._run_pipeline(job)

    assert _read_run(db_file, job.run_id)["status"] == "failed"
    assert "No command produced" in job.log_path.read_text()


# ── post-processing ───────────────────────────────────────────────────────────


def test_postprocess_runs_after_a_successful_command(db_file: str, tmp_path: Path) -> None:
    calls: list[str] = []
    job = _job(db_file, tmp_path, postprocess_fn=lambda: calls.append("post"))

    executor._run_pipeline(job)

    assert calls == ["post"]


def test_postprocess_is_skipped_when_the_command_failed(
    db_file: str, tmp_path: Path
) -> None:
    calls: list[str] = []
    job = _job(db_file, tmp_path, cmd=_FAIL_CMD, postprocess_fn=lambda: calls.append("post"))

    executor._run_pipeline(job)

    assert calls == []


def test_postprocess_failure_is_logged_but_keeps_the_run_done(
    db_file: str, tmp_path: Path
) -> None:
    """The pipeline itself succeeded, so post-processing must not fail the run."""

    def boom() -> None:
        raise RuntimeError("postproc exploded")

    job = _job(db_file, tmp_path, postprocess_fn=boom)

    executor._run_pipeline(job)

    assert _read_run(db_file, job.run_id)["status"] == "done"
    assert "[ODIN-POST] Post-processing failed: postproc exploded" in job.log_path.read_text()


# ── cleanup ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("cmd", [_OK_CMD, _FAIL_CMD])
def test_cleanup_runs_whether_the_command_succeeds_or_fails(
    db_file: str, tmp_path: Path, cmd: list[str]
) -> None:
    cleaned: list[bool] = []
    job = _job(db_file, tmp_path, cmd=cmd, cleanup_fn=lambda: cleaned.append(True))

    executor._run_pipeline(job)

    assert cleaned == [True]


def test_cleanup_failure_is_logged_and_swallowed(db_file: str, tmp_path: Path) -> None:
    def boom() -> None:
        raise RuntimeError("rmtree exploded")

    job = _job(db_file, tmp_path, cleanup_fn=boom)

    executor._run_pipeline(job)

    assert _read_run(db_file, job.run_id)["status"] == "done"
    assert "Cleanup failed: rmtree exploded" in job.log_path.read_text()
