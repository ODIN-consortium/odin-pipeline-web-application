"""Tests for the scaffolding shared by the pipeline post-processors.

These blocks were copy-pasted in kraken_postprocessor and amr_postprocessor and
untested in both. Since post-processing runs after the pipeline already
succeeded, the contract under test is largely "never raise, report and continue".
"""

import sqlite3
import uuid
from pathlib import Path

import pandas as pd
import pytest

from backend.app.parsers.kraken_parser import RANK_COLUMN_MAPPING, enrich_kraken2_lineage
from backend.app.parsers.postprocess_common import (
    POSTPROCESS_SENTINEL,
    deduplicate_rows,
    load_postprocess_metadata,
    make_postprocess_logger,
    write_postprocess_sentinel,
)
from backend.tests.conftest import _SCHEMA

_NOW = "2025-01-01T00:00:00.000Z"
RA = "20260610_0800_MN00000_FAX00001_aaaa1111"


# ── make_postprocess_logger ───────────────────────────────────────────────────


def test_logger_writes_marker_prefixed_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "run.log"
    log = make_postprocess_logger(log_file, "[ODIN-POST-AMR]")

    log("first")
    log("second")

    assert log_file.read_text().splitlines() == [
        "[ODIN-POST-AMR] first",
        "[ODIN-POST-AMR] second",
    ]


def test_logger_appends_to_an_existing_log(tmp_path: Path) -> None:
    log_file = tmp_path / "run.log"
    log_file.write_text("[ODIN] Command: nextflow run\n")

    make_postprocess_logger(log_file, "[ODIN-POST]")("done")

    assert log_file.read_text().startswith("[ODIN] Command: nextflow run\n")
    assert "[ODIN-POST] done" in log_file.read_text()


def test_logger_swallows_write_failures(tmp_path: Path) -> None:
    """An unwritable log must not fail post-processing of a successful run."""
    log = make_postprocess_logger(tmp_path / "missing_dir" / "run.log", "[ODIN-POST]")

    log("this cannot be written")  # must not raise


# ── load_postprocess_metadata ─────────────────────────────────────────────────


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


def _register_barcode(db_file: str, run_accession: str, barcode: str) -> None:
    """Insert the minimum rows for a barcode to appear in the metadata query."""
    con = sqlite3.connect(db_file)
    site_id, sample_id, nra_id = (str(uuid.uuid4()) for _ in range(3))
    con.execute(
        """INSERT INTO sites (id, site_code, site, country, country_code, city_code, city,
                              created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (site_id, "NOBGN01", "01", "Norway", "NO", "BGN", "", _NOW, _NOW),
    )
    con.execute(
        """INSERT INTO samples (id, site_id, sample_code, sample_type, sampling_date,
                                created_at, updated_at)
           VALUES (?,?,?,?,?,?,?)""",
        (sample_id, site_id, "NOBGN01_water", "water", "20260601", _NOW, _NOW),
    )
    con.execute(
        """INSERT INTO nanopore_run_accessions (id, run_accession, created_at, updated_at)
           VALUES (?,?,?,?)""",
        (nra_id, run_accession, _NOW, _NOW),
    )
    con.execute(
        """INSERT INTO nanopore_runs (id, accession_id, sample_id, barcode, created_at, updated_at)
           VALUES (?,?,?,?,?,?)""",
        (str(uuid.uuid4()), nra_id, sample_id, barcode, _NOW, _NOW),
    )
    con.commit()
    con.close()


def test_metadata_rows_are_returned_for_a_registered_run(
    db_file: str, tmp_path: Path
) -> None:
    _register_barcode(db_file, RA, "barcode01")
    log = make_postprocess_logger(tmp_path / "run.log", "[ODIN-POST]")

    rows = load_postprocess_metadata(db_file, [RA], log, skip_note="Skipping.")

    assert [r["barcode"] for r in rows] == ["barcode01"]
    assert "Found 1 barcode row(s)" in (tmp_path / "run.log").read_text()


def test_no_metadata_returns_empty_and_logs_the_skip_note(
    db_file: str, tmp_path: Path
) -> None:
    log = make_postprocess_logger(tmp_path / "run.log", "[ODIN-POST]")

    rows = load_postprocess_metadata(
        db_file, [RA], log, skip_note="Skipping AMR post-processing."
    )

    assert rows == []
    log_text = (tmp_path / "run.log").read_text()
    assert "No metadata found in database" in log_text
    assert "Skipping AMR post-processing." in log_text


# ── deduplicate_rows ──────────────────────────────────────────────────────────


def _log_sink() -> tuple[list[str], object]:
    messages: list[str] = []
    return messages, messages.append


def test_duplicates_are_dropped_ignoring_bookkeeping_columns() -> None:
    """The same observation from two run_accessions collapses into one row."""
    df = pd.DataFrame(
        [
            {"taxon": "Mpox", "count": 5, "run_accession": "RA1", "incomplete_data": False},
            {"taxon": "Mpox", "count": 5, "run_accession": "RA2", "incomplete_data": True},
            {"taxon": "Flu", "count": 2, "run_accession": "RA1", "incomplete_data": False},
        ]
    )
    messages, log = _log_sink()

    result = deduplicate_rows(
        df, log, ignore_columns={"run_accession", "incomplete_data"}
    )

    assert list(result["taxon"]) == ["Mpox", "Flu"]
    # The first occurrence wins, so RA1's row survives.
    assert list(result["run_accession"]) == ["RA1", "RA1"]
    assert messages == ["Removed 1 duplicate rows."]


def test_distinct_rows_are_kept_and_nothing_is_logged() -> None:
    df = pd.DataFrame([{"taxon": "Mpox", "count": 5}, {"taxon": "Mpox", "count": 6}])
    messages, log = _log_sink()

    result = deduplicate_rows(df, log, ignore_columns={"run_accession"})

    assert len(result) == 2
    assert messages == []


def test_index_is_reset_after_dropping() -> None:
    df = pd.DataFrame([{"a": 1}, {"a": 1}, {"a": 2}])
    _messages, log = _log_sink()

    result = deduplicate_rows(df, log, ignore_columns=set())

    assert list(result.index) == [0, 1]


def test_deduplicate_does_not_mutate_the_input_frame() -> None:
    df = pd.DataFrame([{"a": 1}, {"a": 1}])
    _messages, log = _log_sink()

    deduplicate_rows(df, log, ignore_columns=set())

    assert len(df) == 2


# ── write_postprocess_sentinel ────────────────────────────────────────────────


def test_sentinel_is_written_into_the_output_dir(tmp_path: Path) -> None:
    write_postprocess_sentinel(str(tmp_path))

    assert (tmp_path / POSTPROCESS_SENTINEL).is_file()


def test_sentinel_write_failure_is_swallowed(tmp_path: Path) -> None:
    """A missing output dir must not fail an otherwise successful run."""
    write_postprocess_sentinel(str(tmp_path / "does_not_exist"))  # must not raise


# ── kraken_parser purity ──────────────────────────────────────────────────────


def test_enrich_lineage_leaves_the_input_frame_untouched() -> None:
    """The documented contract is "returns an enriched frame", not in-place edit."""
    df = pd.DataFrame(
        [
            {"Rank code": "D", "Scientific name": "Viruses"},
            {"Rank code": "S", "Scientific name": "Monkeypox virus"},
        ]
    )
    original_columns = list(df.columns)

    enriched = enrich_kraken2_lineage(df, RANK_COLUMN_MAPPING)

    assert list(df.columns) == original_columns
    assert "Species" in enriched.columns
    assert enriched.loc[1, "Species"] == "Monkeypox virus"


# ── create_kraken_dataset search-root guard ───────────────────────────────────


def test_create_kraken_dataset_missing_root_raises(tmp_path: Path) -> None:
    """A non-existent search root is a path bug, not an empty result.

    Regression: rglob() on a missing directory yields nothing without raising,
    which masked the Podman path-coercion bug as a silent no-output run.
    """
    from backend.app.parsers.kraken_postprocessor import create_kraken_dataset

    with pytest.raises(FileNotFoundError, match="search root"):
        create_kraken_dataset(str(tmp_path / "does-not-exist"))


def test_create_kraken_dataset_empty_existing_root_returns_empty(tmp_path: Path) -> None:
    from backend.app.parsers.kraken_postprocessor import create_kraken_dataset

    df = create_kraken_dataset(str(tmp_path))
    assert df.empty
