"""Tests for the startup configuration checks in main.py.

ODIN_PIPELINE_ROOT is required and has no default — every path setting derives from
it — so an unset value is fatal rather than a warning. A path that is set but does
not exist stays a warning: it may be mounted later, and blocking startup would stop
an operator opening the UI to fix the configuration.
"""

from pathlib import Path

import pytest

from backend.app.main import _check_database_volume, _check_pipeline_root


def test_unset_pipeline_root_aborts_startup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.delenv("ODIN_PIPELINE_ROOT", raising=False)

    with pytest.raises(SystemExit) as exc:
        _check_pipeline_root()

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "FATAL" in err
    assert "ODIN_PIPELINE_ROOT is not set" in err
    # The message must tell the operator what to do, not just what is wrong.
    assert ".env" in err


def test_blank_pipeline_root_aborts_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace is not configuration."""
    monkeypatch.setenv("ODIN_PIPELINE_ROOT", "   ")

    with pytest.raises(SystemExit):
        _check_pipeline_root()


def test_missing_directory_only_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """The path may be mounted later — this must not stop the server."""
    monkeypatch.setenv("ODIN_PIPELINE_ROOT", str(tmp_path / "not_yet_mounted"))

    _check_pipeline_root()  # must not raise

    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "does not exist" in err


def test_existing_directory_is_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("ODIN_PIPELINE_ROOT", str(tmp_path))

    _check_pipeline_root()

    assert capsys.readouterr().err == ""


def test_database_check_is_skipped_without_a_pipeline_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No root means no derived databases path to probe — and no guessed one either."""
    monkeypatch.delenv("ODIN_PIPELINE_ROOT", raising=False)
    monkeypatch.delenv("ODIN_DATABASE_PATH", raising=False)

    _check_database_volume()  # must not raise or probe a made-up path
