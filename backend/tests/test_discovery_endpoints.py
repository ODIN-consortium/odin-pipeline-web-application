"""Tests for ODIN backend discovery API — _barcode_status, biomeme folder exclusions,
and the nanopore confidence-report endpoint."""

import sqlite3
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.api.discovery import _barcode_status

# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────


def _set_config(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        "INSERT OR REPLACE INTO config_values (id, key, value, created_at, updated_at, created_by)"
        " VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), key, value, "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z", "test"),
    )
    db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# _barcode_status — pure function
# ─────────────────────────────────────────────────────────────────────────────


def test_barcode_status_excluded() -> None:
    assert _barcode_status(True, False, False, 0) == "excluded"


def test_barcode_status_ready() -> None:
    assert _barcode_status(False, True, True, 5) == "ready"


def test_barcode_status_no_files() -> None:
    assert _barcode_status(False, True, True, 0) == "no_files"


def test_barcode_status_not_on_disk() -> None:
    assert _barcode_status(False, True, False, 0) == "not_on_disk"


def test_barcode_status_not_in_metadata() -> None:
    assert _barcode_status(False, False, True, 3) == "not_in_metadata"


def test_barcode_status_excluded_takes_priority_over_ready() -> None:
    assert _barcode_status(True, True, True, 10) == "excluded"


# ─────────────────────────────────────────────────────────────────────────────
# Biomeme PUT /exclude
# ─────────────────────────────────────────────────────────────────────────────

_EXCLUDE_PAYLOAD = {"reason": "test reason", "created_by": "test_user"}


def test_biomeme_exclude_folder_simple_path(client: TestClient, db: sqlite3.Connection) -> None:
    r = client.put("/api/discovery/biomeme/folders/DC/20250915/exclude", json=_EXCLUDE_PAYLOAD)
    assert r.status_code == 204


def test_biomeme_exclude_folder_row_inserted(client: TestClient, db: sqlite3.Connection) -> None:
    client.put("/api/discovery/biomeme/folders/DC/20250915/exclude", json=_EXCLUDE_PAYLOAD)
    row = db.execute(
        "SELECT folder_path FROM biomeme_folder_exclusions WHERE folder_path = ?",
        ("DC/20250915",),
    ).fetchone()
    assert row is not None
    assert row["folder_path"] == "DC/20250915"


def test_biomeme_exclude_folder_idempotent(client: TestClient) -> None:
    r1 = client.put("/api/discovery/biomeme/folders/DC/20250915/exclude", json=_EXCLUDE_PAYLOAD)
    r2 = client.put("/api/discovery/biomeme/folders/DC/20250915/exclude", json=_EXCLUDE_PAYLOAD)
    assert r1.status_code == 204
    assert r2.status_code == 204


def test_biomeme_exclude_folder_multi_level_path(client: TestClient) -> None:
    r = client.put("/api/discovery/biomeme/folders/DC/2025/09/15/exclude", json=_EXCLUDE_PAYLOAD)
    assert r.status_code == 204


def test_biomeme_exclude_folder_dotdot_rejected(client: TestClient) -> None:
    # Starlette normalises a raw "../" in the URL before routing, so the path traversal
    # must be percent-encoded (%2F..%2F) to reach the validator with ".." intact.
    r = client.put("/api/discovery/biomeme/folders/DC%2F..%2Fetc/exclude", json=_EXCLUDE_PAYLOAD)
    assert r.status_code == 422


def test_biomeme_exclude_folder_dollar_sign_rejected(client: TestClient) -> None:
    r = client.put("/api/discovery/biomeme/folders/DC/$secret/exclude", json=_EXCLUDE_PAYLOAD)
    assert r.status_code == 422


def test_biomeme_exclude_folder_leading_slash_rejected(client: TestClient) -> None:
    r = client.put("/api/discovery/biomeme/folders//etc/passwd/exclude", json=_EXCLUDE_PAYLOAD)
    assert r.status_code in (404, 422)


# ─────────────────────────────────────────────────────────────────────────────
# Biomeme DELETE /exclude
# ─────────────────────────────────────────────────────────────────────────────


def test_biomeme_unexclude_folder_removes_row(client: TestClient, db: sqlite3.Connection) -> None:
    client.put("/api/discovery/biomeme/folders/DC/20250915/exclude", json=_EXCLUDE_PAYLOAD)
    r = client.delete("/api/discovery/biomeme/folders/DC/20250915/exclude")
    assert r.status_code == 204
    row = db.execute(
        "SELECT folder_path FROM biomeme_folder_exclusions WHERE folder_path = ?",
        ("DC/20250915",),
    ).fetchone()
    assert row is None


def test_biomeme_unexclude_folder_nonexistent_is_idempotent(client: TestClient) -> None:
    r = client.delete("/api/discovery/biomeme/folders/DC/20250915/exclude")
    assert r.status_code == 204


def test_biomeme_unexclude_folder_invalid_path_rejected(client: TestClient) -> None:
    r = client.delete("/api/discovery/biomeme/folders/DC%2F..%2Fetc/exclude")
    assert r.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# Confidence report — input validation (SEC-4, SEC-8)
# ─────────────────────────────────────────────────────────────────────────────


def test_confidence_report_run_accession_with_dot_rejected(client: TestClient) -> None:
    r = client.get("/api/discovery/nanopore/run.accession/confidence-report?target=kraken2")
    assert r.status_code == 422


def test_confidence_report_run_accession_with_slash_rejected(client: TestClient) -> None:
    # A percent-encoded slash (%2F) in the run_accession segment routes to a
    # different handler or results in a 404 — either way it must not be 200.
    r = client.get("/api/discovery/nanopore/run%2Faccession/confidence-report?target=kraken2")
    assert r.status_code in (404, 422)


def test_confidence_report_run_accession_too_long_rejected(client: TestClient) -> None:
    long_accession = "A" * 151
    r = client.get(f"/api/discovery/nanopore/{long_accession}/confidence-report?target=kraken2")
    assert r.status_code == 422


def test_confidence_report_valid_run_accession_not_422(client: TestClient) -> None:
    r = client.get(
        "/api/discovery/nanopore/20250912_0843_MN00000_FBD00001_44c4f359/confidence-report?target=kraken2"
    )
    assert r.status_code != 422


def test_confidence_report_target_with_dotdot_rejected(client: TestClient) -> None:
    r = client.get("/api/discovery/nanopore/ERR000001/confidence-report?target=..%2Fetc")
    assert r.status_code == 422


def test_confidence_report_target_with_slash_rejected(client: TestClient) -> None:
    r = client.get("/api/discovery/nanopore/ERR000001/confidence-report?target=kraken2%2Fother")
    assert r.status_code == 422


def test_confidence_report_valid_target_not_422(client: TestClient) -> None:
    r = client.get("/api/discovery/nanopore/ERR000001/confidence-report?target=kraken2")
    assert r.status_code != 422


# ─────────────────────────────────────────────────────────────────────────────
# Confidence report — functional
# ─────────────────────────────────────────────────────────────────────────────


def _make_report_dir(tmp_path: Path, run_accession: str, target: str) -> Path:
    analysis_dir = (
        tmp_path / "output" / "nanopore_processed" / "outputs_taxprofiler" / run_accession / f"{target}_analysis"
    )
    analysis_dir.mkdir(parents=True)
    return analysis_dir


def test_confidence_report_404_when_nanopore_processed_not_present_no_explicit_config(
    client: TestClient,
) -> None:
    # ODIN_PIPELINE_ROOT is always set in the test environment, so output_dir
    # always resolves to a computed default (PIPELINE_ROOT/output).  That
    # directory does not contain nanopore_processed, so the endpoint returns 404.
    r = client.get("/api/discovery/nanopore/ERR000001/confidence-report?target=kraken2")
    assert r.status_code == 404


def test_confidence_report_404_when_nanopore_processed_missing(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _set_config(db, "output_dir", str(output_dir))
    r = client.get("/api/discovery/nanopore/ERR000001/confidence-report?target=kraken2")
    assert r.status_code == 404


def test_confidence_report_404_when_run_output_dir_not_found(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    processed = tmp_path / "output" / "nanopore_processed"
    processed.mkdir(parents=True)
    _set_config(db, "output_dir", str(tmp_path / "output"))
    r = client.get("/api/discovery/nanopore/ERR000001/confidence-report?target=kraken2")
    assert r.status_code == 404


def test_confidence_report_404_when_analysis_dir_missing(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    run_accession = "ERR000001"
    target = "kraken2"
    run_dir = (
        tmp_path / "output" / "nanopore_processed" / "outputs_taxprofiler" / run_accession
    )
    run_dir.mkdir(parents=True)
    _set_config(db, "output_dir", str(tmp_path / "output"))
    r = client.get(f"/api/discovery/nanopore/{run_accession}/confidence-report?target={target}")
    assert r.status_code == 404


def test_confidence_report_404_when_no_report_files(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    run_accession = "ERR000001"
    target = "kraken2"
    analysis_dir = _make_report_dir(tmp_path, run_accession, target)
    (analysis_dir / "other_file.txt").write_text("not a report")
    _set_config(db, "output_dir", str(tmp_path / "output"))
    r = client.get(f"/api/discovery/nanopore/{run_accession}/confidence-report?target={target}")
    assert r.status_code == 404


def test_confidence_report_200_with_one_report_file(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    run_accession = "ERR000001"
    target = "kraken2"
    analysis_dir = _make_report_dir(tmp_path, run_accession, target)
    report_content = "Sample: ERR000001\nConfidence: 0.95\n"
    (analysis_dir / "sample_confidence_report.txt").write_text(report_content)
    _set_config(db, "output_dir", str(tmp_path / "output"))
    r = client.get(f"/api/discovery/nanopore/{run_accession}/confidence-report?target={target}")
    assert r.status_code == 200
    assert "Confidence: 0.95" in r.text


def test_confidence_report_200_with_two_report_files_joined(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    run_accession = "ERR000002"
    target = "kraken2"
    analysis_dir = _make_report_dir(tmp_path, run_accession, target)
    (analysis_dir / "alpha_confidence_report.txt").write_text("Report: alpha")
    (analysis_dir / "beta_confidence_report.txt").write_text("Report: beta")
    _set_config(db, "output_dir", str(tmp_path / "output"))
    r = client.get(f"/api/discovery/nanopore/{run_accession}/confidence-report?target={target}")
    assert r.status_code == 200
    assert "Report: alpha" in r.text
    assert "Report: beta" in r.text
    assert "─" in r.text


def test_confidence_report_found_under_non_first_output_dir(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Regression: the report lives under a later outputs_* dir while an earlier
    one (taxprofiler) has no analysis dir.  The endpoint must scan all dirs."""
    run_accession = "ERR000003"
    target = "mpox_cladeia"
    processed = tmp_path / "output" / "nanopore_processed"
    # Earlier-sorting output dir for the same run, WITHOUT the analysis dir.
    (processed / "outputs_taxprofiler" / run_accession).mkdir(parents=True)
    # Later-sorting output dir that actually holds the report.
    analysis_dir = processed / "outputs_wf_metagenomics_ssu" / run_accession / f"{target}_analysis"
    analysis_dir.mkdir(parents=True)
    (analysis_dir / "sample_confidence_report.txt").write_text("Confidence: 0.88")
    _set_config(db, "output_dir", str(tmp_path / "output"))
    r = client.get(f"/api/discovery/nanopore/{run_accession}/confidence-report?target={target}")
    assert r.status_code == 200
    assert "Confidence: 0.88" in r.text
