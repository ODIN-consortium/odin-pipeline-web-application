"""
Tests for backend.app.pipeline.samplesheet.

Covers _get_related_run_accessions, build_taxprofiler_samplesheet,
prepare_metagenomics_input, and build_mpox_samplesheet.
"""

import csv
import gzip
import uuid
from pathlib import Path

import pytest

from backend.app.pipeline.samplesheet import (
    _get_related_run_accessions,
    build_mpox_samplesheet,
    build_taxprofiler_samplesheet,
    prepare_metagenomics_input,
)

# ── constants ─────────────────────────────────────────────────────────────────

SITE = {
    "country": "Norway",
    "country_code": "NO",
    "city_code": "BGO",
    "site": "Harbour",
    "longitude": 5.3,
    "latitude": 60.4,
}
SAMPLE = {"sample_type": "water", "sampling_date": "20240601"}
RUN = {
    "run_accession": "ERR000001",
    "barcode": "barcode01",
    "protocol_id": "SQK-LSK114",
    "sequencing_kit_id": "SQK-LSK114",
    "type": "GridION",
}

# ── helpers ───────────────────────────────────────────────────────────────────


def _write_fastq_gz(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as f:
        f.write(b"@read1\nACGT\n+\nIIII\n")


def _setup_run(client, site=None, sample=None, run=None):
    """Create site → sample → nanopore-run via API. Returns (site, sample) dicts."""
    site_data = client.post("/api/sites", json=site or SITE).json()
    sample_data = client.post(
        "/api/samples", json={**(sample or SAMPLE), "site_id": site_data["id"]}
    ).json()
    run_payload = {**(run or RUN), "sample_id": sample_data["id"]}
    resp = client.post("/api/nanopore-runs", json=run_payload)
    assert resp.status_code in (200, 201), resp.text
    return site_data, sample_data


def _minknow_stored(tmp_path: Path) -> str:
    """Return the minknow dir as a stored (forward-slash Windows) path string."""
    minknow = tmp_path / "minknow"
    minknow.mkdir(exist_ok=True)
    # normalize_for_storage converts D:\... → /d/... for stored form;
    # passing a plain str of the Windows path works because coerce_path() handles it.
    return str(minknow)


def _output_stored(tmp_path: Path) -> str:
    output = tmp_path / "output"
    output.mkdir(exist_ok=True)
    return str(output)


def _fastq_path(tmp_path: Path, run_accession: str, barcode: str) -> Path:
    return tmp_path / "minknow" / run_accession / "fastq_pass" / barcode / "reads.fastq.gz"


# ── _get_related_run_accessions ───────────────────────────────────────────────


def test_get_related_returns_input_unchanged_when_auto_merge_false(db):
    accessions = ["ERR000001", "ERR000002"]
    result = _get_related_run_accessions(db, accessions, auto_merge=False)
    assert result == accessions


def test_get_related_no_cache_entries_returns_original_list(db):
    result = _get_related_run_accessions(db, ["ERR000001"], auto_merge=True)
    assert result == ["ERR000001"]


def test_get_related_expands_continuation_partner(db):
    db.execute(
        "INSERT INTO nanopore_disk_cache (run_accession, flow_cell_id, run_started, run_stopped, scanned_at)"
        " VALUES (?,?,?,?,?)",
        ("ERR000001", "FC001", "2025-01-01T10:00:00", "2025-01-01T12:00:00", "2025-01-01T12:01:00"),
    )
    db.execute(
        "INSERT INTO nanopore_disk_cache (run_accession, flow_cell_id, run_started, run_stopped, scanned_at)"
        " VALUES (?,?,?,?,?)",
        ("ERR000002", "FC001", "2025-01-01T13:00:00", None, "2025-01-01T13:01:00"),
    )
    db.commit()

    result = _get_related_run_accessions(db, ["ERR000001"], auto_merge=True)
    assert "ERR000001" in result
    assert "ERR000002" in result


def test_get_related_originals_come_before_partners(db):
    db.execute(
        "INSERT INTO nanopore_disk_cache (run_accession, flow_cell_id, run_started, run_stopped, scanned_at)"
        " VALUES (?,?,?,?,?)",
        ("ERR000001", "FC001", "2025-01-01T10:00:00", "2025-01-01T12:00:00", "2025-01-01T12:01:00"),
    )
    db.execute(
        "INSERT INTO nanopore_disk_cache (run_accession, flow_cell_id, run_started, run_stopped, scanned_at)"
        " VALUES (?,?,?,?,?)",
        ("ERR000002", "FC001", "2025-01-01T13:00:00", None, "2025-01-01T13:01:00"),
    )
    db.commit()

    result = _get_related_run_accessions(db, ["ERR000001"], auto_merge=True)
    assert result.index("ERR000001") < result.index("ERR000002")


# ── build_taxprofiler_samplesheet ─────────────────────────────────────────────


def test_taxprofiler_returns_four_tuple(db, client, tmp_path):
    _setup_run(client)
    _write_fastq_gz(_fastq_path(tmp_path, "ERR000001", "barcode01"))

    result = build_taxprofiler_samplesheet(
        db,
        ["ERR000001"],
        auto_merge=False,
        minknow_dir_stored=_minknow_stored(tmp_path),
        output_dir_stored=_output_stored(tmp_path),
        file_identifier="test",
        tmp_dir=tmp_path / "tmp",
    )
    assert len(result) == 4


def test_taxprofiler_csv_path_exists(db, client, tmp_path):
    _setup_run(client)
    _write_fastq_gz(_fastq_path(tmp_path, "ERR000001", "barcode01"))

    csv_path, _, _, _ = build_taxprofiler_samplesheet(
        db,
        ["ERR000001"],
        auto_merge=False,
        minknow_dir_stored=_minknow_stored(tmp_path),
        output_dir_stored=_output_stored(tmp_path),
        file_identifier="test",
        tmp_dir=tmp_path / "tmp",
    )
    assert csv_path.exists()


def test_taxprofiler_csv_header(db, client, tmp_path):
    _setup_run(client)
    _write_fastq_gz(_fastq_path(tmp_path, "ERR000001", "barcode01"))

    csv_path, _, _, _ = build_taxprofiler_samplesheet(
        db,
        ["ERR000001"],
        auto_merge=False,
        minknow_dir_stored=_minknow_stored(tmp_path),
        output_dir_stored=_output_stored(tmp_path),
        file_identifier="test",
        tmp_dir=tmp_path / "tmp",
    )
    with csv_path.open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
    assert header == ["sample", "run_accession", "instrument_platform", "fastq_1"]


def test_taxprofiler_csv_has_one_data_row(db, client, tmp_path):
    _setup_run(client)
    _write_fastq_gz(_fastq_path(tmp_path, "ERR000001", "barcode01"))

    csv_path, _, _, _ = build_taxprofiler_samplesheet(
        db,
        ["ERR000001"],
        auto_merge=False,
        minknow_dir_stored=_minknow_stored(tmp_path),
        output_dir_stored=_output_stored(tmp_path),
        file_identifier="test",
        tmp_dir=tmp_path / "tmp",
    )
    with csv_path.open(newline="") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 2  # header + one data row


def test_taxprofiler_merge_note_is_none_when_no_merge(db, client, tmp_path):
    _setup_run(client)
    _write_fastq_gz(_fastq_path(tmp_path, "ERR000001", "barcode01"))

    _, _, merge_note, _ = build_taxprofiler_samplesheet(
        db,
        ["ERR000001"],
        auto_merge=False,
        minknow_dir_stored=_minknow_stored(tmp_path),
        output_dir_stored=_output_stored(tmp_path),
        file_identifier="test",
        tmp_dir=tmp_path / "tmp",
    )
    assert merge_note is None


def test_taxprofiler_all_accessions_contains_input(db, client, tmp_path):
    _setup_run(client)
    _write_fastq_gz(_fastq_path(tmp_path, "ERR000001", "barcode01"))

    _, _, _, all_accessions = build_taxprofiler_samplesheet(
        db,
        ["ERR000001"],
        auto_merge=False,
        minknow_dir_stored=_minknow_stored(tmp_path),
        output_dir_stored=_output_stored(tmp_path),
        file_identifier="test",
        tmp_dir=tmp_path / "tmp",
    )
    assert "ERR000001" in all_accessions


def test_taxprofiler_raises_when_no_registered_barcodes(db, client, tmp_path):
    with pytest.raises(ValueError, match="No run_accession rows"):
        build_taxprofiler_samplesheet(
            db,
            ["ERR_NONEXISTENT"],
            auto_merge=False,
            minknow_dir_stored=_minknow_stored(tmp_path),
            output_dir_stored=_output_stored(tmp_path),
            file_identifier="test",
            tmp_dir=tmp_path / "tmp",
        )


def test_taxprofiler_raises_when_no_fastq_files_on_disk(db, client, tmp_path):
    _setup_run(client)
    # barcode dir is absent — no FASTQ files

    with pytest.raises(ValueError, match="No FASTQ files found"):
        build_taxprofiler_samplesheet(
            db,
            ["ERR000001"],
            auto_merge=False,
            minknow_dir_stored=_minknow_stored(tmp_path),
            output_dir_stored=_output_stored(tmp_path),
            file_identifier="test",
            tmp_dir=tmp_path / "tmp",
        )


# ── prepare_metagenomics_input ────────────────────────────────────────────────


def test_metagenomics_returns_three_tuple(db, client, tmp_path):
    _setup_run(client)
    _write_fastq_gz(_fastq_path(tmp_path, "ERR000001", "barcode01"))

    result = prepare_metagenomics_input(
        db,
        ["ERR000001"],
        auto_merge=False,
        minknow_dir_stored=_minknow_stored(tmp_path),
        output_dir_stored=_output_stored(tmp_path),
        file_identifier="testid",
        tmp_dir=tmp_path / "tmp",
    )
    assert len(result) == 3


def test_metagenomics_structured_dir_exists(db, client, tmp_path):
    _setup_run(client)
    _write_fastq_gz(_fastq_path(tmp_path, "ERR000001", "barcode01"))

    structured_dir, _, _ = prepare_metagenomics_input(
        db,
        ["ERR000001"],
        auto_merge=False,
        minknow_dir_stored=_minknow_stored(tmp_path),
        output_dir_stored=_output_stored(tmp_path),
        file_identifier="testid",
        tmp_dir=tmp_path / "tmp",
    )
    assert structured_dir.is_dir()


def test_metagenomics_structured_layout_contains_barcode_fastq(db, client, tmp_path):
    _setup_run(client)
    _write_fastq_gz(_fastq_path(tmp_path, "ERR000001", "barcode01"))

    structured_dir, _, _ = prepare_metagenomics_input(
        db,
        ["ERR000001"],
        auto_merge=False,
        minknow_dir_stored=_minknow_stored(tmp_path),
        output_dir_stored=_output_stored(tmp_path),
        file_identifier="testid",
        tmp_dir=tmp_path / "tmp",
    )
    expected = structured_dir / "barcode01" / "barcode01_testid.fastq.gz"
    assert expected.exists()


def test_metagenomics_all_accessions_returned(db, client, tmp_path):
    _setup_run(client)
    _write_fastq_gz(_fastq_path(tmp_path, "ERR000001", "barcode01"))

    _, _, all_accessions = prepare_metagenomics_input(
        db,
        ["ERR000001"],
        auto_merge=False,
        minknow_dir_stored=_minknow_stored(tmp_path),
        output_dir_stored=_output_stored(tmp_path),
        file_identifier="testid",
        tmp_dir=tmp_path / "tmp",
    )
    assert "ERR000001" in all_accessions


def test_metagenomics_raises_when_no_fastq_files_on_disk(db, client, tmp_path):
    _setup_run(client)
    # no FASTQ files created

    with pytest.raises(ValueError, match="No FASTQ files found"):
        prepare_metagenomics_input(
            db,
            ["ERR000001"],
            auto_merge=False,
            minknow_dir_stored=_minknow_stored(tmp_path),
            output_dir_stored=_output_stored(tmp_path),
            file_identifier="testid",
            tmp_dir=tmp_path / "tmp",
        )


# ── build_mpox_samplesheet ────────────────────────────────────────────────────


def _seed_mpox_type(db, code: str, description: str = "Test type") -> None:
    db.execute(
        "INSERT OR IGNORE INTO lookup_values (id, list, code, description) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), "mpox_type", code, description),
    )
    db.commit()


def _setup_mpox_run(client, db, run_accession: str = "ERR000001", barcode: str = "barcode01", mpox_type: str = "test_sample"):
    _seed_mpox_type(db, mpox_type)
    site = client.post("/api/sites", json=SITE).json()
    sample = client.post("/api/samples", json={**SAMPLE, "site_id": site["id"]}).json()
    run_payload = {
        "run_accession": run_accession,
        "barcode": barcode,
        "protocol_id": "SQK-LSK114",
        "sequencing_kit_id": "SQK-LSK114",
        "type": mpox_type,
        "sample_id": sample["id"],
    }
    resp = client.post("/api/nanopore-runs", json=run_payload)
    assert resp.status_code in (200, 201), resp.text


def test_mpox_returns_path_to_csv(db, client, tmp_path):
    _setup_mpox_run(client, db)
    _write_fastq_gz(_fastq_path(tmp_path, "ERR000001", "barcode01"))

    result = build_mpox_samplesheet(
        db,
        "ERR000001",
        minknow_dir_stored=_minknow_stored(tmp_path),
        output_dir_stored=_output_stored(tmp_path),
    )
    assert isinstance(result, Path)
    assert result.exists()


def test_mpox_csv_has_correct_columns(db, client, tmp_path):
    _setup_mpox_run(client, db)
    _write_fastq_gz(_fastq_path(tmp_path, "ERR000001", "barcode01"))

    csv_path = build_mpox_samplesheet(
        db,
        "ERR000001",
        minknow_dir_stored=_minknow_stored(tmp_path),
        output_dir_stored=_output_stored(tmp_path),
    )
    with csv_path.open(newline="") as f:
        header = next(csv.reader(f))
    assert header == ["barcode", "alias", "type"]


def test_mpox_barcode_with_dir_and_valid_type_appears_in_csv(db, client, tmp_path):
    _setup_mpox_run(client, db, mpox_type="test_sample")
    _write_fastq_gz(_fastq_path(tmp_path, "ERR000001", "barcode01"))

    csv_path = build_mpox_samplesheet(
        db,
        "ERR000001",
        minknow_dir_stored=_minknow_stored(tmp_path),
        output_dir_stored=_output_stored(tmp_path),
    )
    with csv_path.open(newline="") as f:
        rows = list(csv.reader(f))
    barcodes = [r[0] for r in rows[1:]]
    assert "barcode01" in barcodes
    types = [r[2] for r in rows[1:]]
    assert "test_sample" in types


def test_mpox_barcode_with_dir_and_invalid_type_raises(db, client, tmp_path):
    # Seed an invalid type into lookup_values so the API accepts it,
    # but the samplesheet builder should reject it.
    _seed_mpox_type(db, "invalid_type", "Not a real mpox type")
    site = client.post("/api/sites", json=SITE).json()
    sample = client.post("/api/samples", json={**SAMPLE, "site_id": site["id"]}).json()
    resp = client.post(
        "/api/nanopore-runs",
        json={
            "run_accession": "ERR000001",
            "barcode": "barcode01",
            "protocol_id": "SQK-LSK114",
            "sequencing_kit_id": "SQK-LSK114",
            "type": "invalid_type",
            "sample_id": sample["id"],
        },
    )
    assert resp.status_code in (200, 201), resp.text

    _write_fastq_gz(_fastq_path(tmp_path, "ERR000001", "barcode01"))

    with pytest.raises(ValueError, match="Invalid type"):
        build_mpox_samplesheet(
            db,
            "ERR000001",
            minknow_dir_stored=_minknow_stored(tmp_path),
            output_dir_stored=_output_stored(tmp_path),
        )


def test_mpox_barcode_missing_from_disk_is_silently_skipped(db, client, tmp_path):
    _setup_mpox_run(client, db, mpox_type="test_sample")
    # The run dir with fastq_pass must exist so find_run_dir succeeds,
    # but the barcode subdir is absent — the barcode is silently skipped.
    fastq_pass = tmp_path / "minknow" / "ERR000001" / "fastq_pass"
    fastq_pass.mkdir(parents=True, exist_ok=True)

    csv_path = build_mpox_samplesheet(
        db,
        "ERR000001",
        minknow_dir_stored=_minknow_stored(tmp_path),
        output_dir_stored=_output_stored(tmp_path),
    )
    with csv_path.open(newline="") as f:
        rows = list(csv.reader(f))
    barcodes = [r[0] for r in rows[1:]]
    assert "barcode01" not in barcodes


def test_mpox_raises_file_not_found_when_run_dir_absent(db, client, tmp_path):
    _setup_mpox_run(client, db, mpox_type="test_sample")
    # No directory at all under minknow

    with pytest.raises(FileNotFoundError):
        build_mpox_samplesheet(
            db,
            "ERR000001",
            minknow_dir_stored=_minknow_stored(tmp_path),
            output_dir_stored=_output_stored(tmp_path),
        )
