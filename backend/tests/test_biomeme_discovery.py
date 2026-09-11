"""Tests for the Biomeme discovery scan.

`_iter_biomeme_files` walks biomeme_input_data/{country}/{YYYYMMDD}/{run}.xlsx.
It was an inline triple-nested loop inside the endpoint and untested; these pin
the layout rules (what counts as a device output file, and what is skipped).
"""

import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.api.biomeme_discovery import _iter_biomeme_files

_NOW = "2025-01-01T00:00:00.000Z"


def _make_input_tree(root: Path) -> Path:
    input_data = root / "biomeme_input_data"
    input_data.mkdir(parents=True)
    return input_data


def _add_run(input_data: Path, country: str, date: str, name: str) -> Path:
    day_dir = input_data / country / date
    day_dir.mkdir(parents=True, exist_ok=True)
    file_path = day_dir / name
    file_path.write_text("stub")
    return file_path


# ── _iter_biomeme_files ───────────────────────────────────────────────────────


def test_scan_finds_runs_in_country_and_date_folders(tmp_path: Path) -> None:
    input_data = _make_input_tree(tmp_path)
    _add_run(input_data, "NO", "20260601", "run_a.xlsx")
    _add_run(input_data, "UG", "20260602", "run_b.xlsx")

    found = list(_iter_biomeme_files(input_data))

    assert [(c, d, f.stem) for c, d, f in found] == [
        ("NO", "20260601", "run_a"),
        ("UG", "20260602", "run_b"),
    ]


def test_scan_is_sorted_for_a_stable_discovery_list(tmp_path: Path) -> None:
    input_data = _make_input_tree(tmp_path)
    _add_run(input_data, "NO", "20260603", "z_run.xlsx")
    _add_run(input_data, "NO", "20260601", "a_run.xlsx")
    _add_run(input_data, "NO", "20260601", "b_run.xlsx")

    found = [(d, f.stem) for _c, d, f in _iter_biomeme_files(input_data)]

    assert found == [
        ("20260601", "a_run"),
        ("20260601", "b_run"),
        ("20260603", "z_run"),
    ]


def test_scan_ignores_non_xlsx_files(tmp_path: Path) -> None:
    input_data = _make_input_tree(tmp_path)
    _add_run(input_data, "NO", "20260601", "notes.txt")
    _add_run(input_data, "NO", "20260601", "real_run.xlsx")

    assert [f.stem for _c, _d, f in _iter_biomeme_files(input_data)] == ["real_run"]


def test_scan_ignores_files_at_the_country_and_root_levels(tmp_path: Path) -> None:
    """Only files two levels deep are device outputs — strays must not be reported."""
    input_data = _make_input_tree(tmp_path)
    (input_data / "loose.xlsx").write_text("stub")
    (input_data / "NO").mkdir()
    (input_data / "NO" / "loose.xlsx").write_text("stub")

    assert list(_iter_biomeme_files(input_data)) == []


def test_scan_of_an_empty_tree_returns_nothing(tmp_path: Path) -> None:
    assert list(_iter_biomeme_files(_make_input_tree(tmp_path))) == []


# ── GET /api/biomeme/discover ─────────────────────────────────────────────────


def _set_config(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        """INSERT OR REPLACE INTO config_values
           (id, key, value, created_at, updated_at, created_by)
           VALUES (?,?,?,?,?,?)""",
        (str(uuid.uuid4()), key, value, _NOW, _NOW, "test"),
    )
    db.commit()


def test_discover_requires_biomeme_dir(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no stored value and no ODIN_PIPELINE_ROOT there is no path to scan.

    Note a *blank* stored value is not enough: blank means "use the computed default",
    so the default under ODIN_PIPELINE_ROOT would apply.
    """
    monkeypatch.delenv("ODIN_PIPELINE_ROOT", raising=False)

    r = client.get("/api/biomeme/discover")

    assert r.status_code == 422
    assert "not configured" in r.json()["detail"]


def test_discover_annotates_registered_runs(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    input_data = _make_input_tree(tmp_path)
    _add_run(input_data, "NO", "20260601", "registered_run.xlsx")
    _add_run(input_data, "NO", "20260601", "new_run.xlsx")
    _set_config(db, "biomeme_dir", str(tmp_path))
    db.execute(
        """INSERT INTO biomeme_runs (id, biomeme_run_name, created_at, updated_at)
           VALUES (?,?,?,?)""",
        (str(uuid.uuid4()), "registered_run", _NOW, _NOW),
    )
    db.commit()

    r = client.get("/api/biomeme/discover")

    assert r.status_code == 200
    by_name = {row["biomeme_run_name"]: row for row in r.json()}
    assert by_name["registered_run"]["registered"] is True
    assert by_name["registered_run"]["run_id"]
    assert by_name["new_run"]["registered"] is False
    assert by_name["new_run"]["run_id"] is None
    assert by_name["new_run"]["country_code"] == "NO"
    assert by_name["new_run"]["sampling_date"] == "20260601"


def test_discover_missing_input_dir_returns_empty_list(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    """A configured root without a biomeme_input_data folder is not an error."""
    _set_config(db, "biomeme_dir", str(tmp_path))

    r = client.get("/api/biomeme/discover")

    assert r.status_code == 200
    assert r.json() == []
