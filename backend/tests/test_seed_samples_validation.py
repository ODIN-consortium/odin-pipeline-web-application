"""seed_samples must skip an unusable CSV row, never raise.

Seeding runs inside the FastAPI startup lifespan, which wraps it in try/finally with no
except (main.py). An exception there does not degrade a feature — it stops the application
from booting. So every row-level problem has to be a skip with a message, not a raise,
which matters more now that samples carries NOT NULL and CHECK constraints that a
hand-edited CSV can violate.
"""

import sqlite3
from pathlib import Path

import pytest

from backend.app.api.samples import seed_samples

_HEADER = "site_ID;sample_type;sample_code;sampling_date;date_extraction"


@pytest.fixture()
def seeded_site(db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO sites (id, site_code, country) VALUES ('s1','DVDVRV','Demoville')")
    db.commit()


def _run_seed(db, tmp_path: Path, monkeypatch, rows: str) -> int:
    (tmp_path / "samples.csv").write_text(f"{_HEADER}\n{rows}", encoding="utf-8")
    monkeypatch.setenv("ODIN_SEED_DIR", str(tmp_path))
    seed_samples(db)  # must not raise
    return db.execute("SELECT COUNT(*) FROM samples").fetchone()[0]


def test_good_row_is_seeded(db, tmp_path, monkeypatch, seeded_site) -> None:
    assert _run_seed(db, tmp_path, monkeypatch, "DVDVRV;WW;DVDVRV_WW;20260610;20260610\n") == 1


def test_malformed_date_extraction_skips_the_row_without_raising(
    db, tmp_path, monkeypatch, seeded_site
) -> None:
    """This raised IntegrityError once date_extraction gained its CHECK — i.e. it broke boot."""
    assert _run_seed(db, tmp_path, monkeypatch, "DVDVRV;WW;DVDVRV_WW;20260610;2026-06-10\n") == 0


def test_malformed_sampling_date_skips_the_row(db, tmp_path, monkeypatch, seeded_site) -> None:
    assert _run_seed(db, tmp_path, monkeypatch, "DVDVRV;WW;DVDVRV_WW;10 June 2026;\n") == 0


def test_blank_sample_type_skips_the_row(db, tmp_path, monkeypatch, seeded_site) -> None:
    assert _run_seed(db, tmp_path, monkeypatch, "DVDVRV;;DVDVRV_WW;20260610;\n") == 0


def test_unresolvable_site_skips_the_row(db, tmp_path, monkeypatch, seeded_site) -> None:
    assert _run_seed(db, tmp_path, monkeypatch, "NOSUCHSITE;WW;X_WW;20260610;\n") == 0


def test_one_bad_row_does_not_cost_the_good_ones(
    db, tmp_path, monkeypatch, seeded_site
) -> None:
    """The property that matters at startup: a bad row is isolated, not fatal."""
    rows = (
        "DVDVRV;WW;DVDVRV_WW;20260610;20260610\n"
        "DVDVRV;WW;DVDVRV_WW;20260611;2026-06-11\n"   # bad date_extraction
        "DVDVRV;DW;DVDVRV_DW;20260612;20260612\n"
    )
    assert _run_seed(db, tmp_path, monkeypatch, rows) == 2
