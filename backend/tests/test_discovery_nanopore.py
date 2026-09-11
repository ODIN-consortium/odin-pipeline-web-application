"""Tests for the three main nanopore discovery endpoints.

Covers enough of the business logic paths to make the planned refactors of
discover_nanopore(), nanopore_readiness(), and nanopore_register() safe.

Disk-scan tests create a real MinKNOW folder structure under pytest's tmp_path
(unique per test → no cache collisions).  DB-only tests use the no_disk_scan
fixture, because a scan that finds nothing evicts the nanopore_disk_cache rows
they pre-insert for continuation testing.
"""

import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def no_disk_scan():
    """Suppress the MinKNOW disk scan for tests that pre-insert disk-cache rows.

    The scan deletes cache rows for runs it does not find on disk, so a scan over an
    empty or missing directory wipes exactly the data these tests set up. They are
    about continuation detection and registration, not disk scanning.
    """
    with patch(
        "backend.app.api.discovery._scan_disk_and_populate_cache",
        return_value=({}, {}, {}, {}),
    ):
        yield

# ─────────────────────────────────────────────────────────────────────────────
# Constants — valid MinKNOW-format run accessions
# ─────────────────────────────────────────────────────────────────────────────

RA  = "20260610_0800_MN00000_FAX00001_aabb1234"
RA2 = "20260611_0800_MN00000_FAX00002_ccdd5678"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _set_config(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        "INSERT OR REPLACE INTO config_values"
        " (id, key, value, created_at, updated_at, created_by)"
        " VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), key, value,
         "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z", "test"),
    )
    db.commit()


def _make_minknow_dir(tmp_path: Path, run_accession: str, barcodes: list[str]) -> Path:
    """Create a minimal MinKNOW folder structure with a stub FASTQ file per barcode."""
    minknow_dir = tmp_path / "minknow"
    run_dir = minknow_dir / "Experiment" / "Sample" / run_accession / "fastq_pass"
    for barcode in barcodes:
        bc_dir = run_dir / barcode
        bc_dir.mkdir(parents=True, exist_ok=True)
        # One stub file — scanner counts .fastq.gz files; content not read
        (bc_dir / "test.fastq.gz").write_bytes(b"\x1f\x8b")
    return minknow_dir


def _insert_site(
    db: sqlite3.Connection,
    country: str = "Norway",
    country_code: str = "NO",
    city_code: str = "BGN",
    site: str = "01",
) -> str:
    site_id = str(uuid.uuid4())
    site_code = f"{country_code}{city_code}{site}".upper()
    db.execute(
        "INSERT INTO sites"
        " (id, site_code, site, country, country_code, city_code, city, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (site_id, site_code, site, country, country_code, city_code, "",
         "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
    )
    db.commit()
    return site_id


def _insert_sample(
    db: sqlite3.Connection,
    site_id: str,
    sample_code: str = "NOBGN01_water",
    sample_type: str = "water",
    sampling_date: str = "20260101",
) -> str:
    sample_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO samples"
        " (id, site_id, sample_code, sample_type, sampling_date, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (sample_id, site_id, sample_code, sample_type, sampling_date,
         "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
    )
    db.commit()
    return sample_id


def _insert_run_accession(
    db: sqlite3.Connection,
    run_accession: str,
    protocol_id: str = "SQK-LSK114",
    kit: str = "SQK-LSK114",
) -> str:
    nra_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO nanopore_run_accessions"
        " (id, run_accession, protocol_id, sequencing_kit_id, created_at, updated_at, created_by, updated_by)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (nra_id, run_accession, protocol_id, kit,
         "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z", "test", "test"),
    )
    db.commit()
    return nra_id


def _insert_nanopore_run(
    db: sqlite3.Connection,
    nra_id: str,
    sample_id: str | None,
    barcode: str = "barcode01",
) -> None:
    db.execute(
        "INSERT INTO nanopore_runs"
        " (id, accession_id, sample_id, barcode, created_at, updated_at, created_by)"
        " VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), nra_id, sample_id, barcode,
         "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z", "test"),
    )
    db.commit()


def _seed_registered_run(
    db: sqlite3.Connection,
    run_accession: str,
    barcodes: list[str],
) -> tuple[str, str, str]:
    """Insert a fully-registered run (site + sample + accession + barcode rows).

    Returns (site_id, sample_id, nra_id).
    """
    site_id   = _insert_site(db)
    sample_id = _insert_sample(db, site_id)
    nra_id    = _insert_run_accession(db, run_accession)
    for bc in barcodes:
        _insert_nanopore_run(db, nra_id, sample_id, bc)
    return site_id, sample_id, nra_id


def _insert_disk_cache(
    db: sqlite3.Connection,
    run_accession: str,
    flow_cell_id: str,
    run_started: str,
    run_stopped: str,
    kit: str = "SQK-LSK114",
    run_name: str = "TestRun",
) -> None:
    db.execute(
        "INSERT INTO nanopore_disk_cache"
        " (run_accession, flow_cell_id, run_started, run_stopped, sequencing_kit_id, run_name, scanned_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (run_accession, flow_cell_id, run_started, run_stopped, kit, run_name,
         "2026-06-10T12:00:00Z"),
    )
    db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/discovery/nanopore
# ─────────────────────────────────────────────────────────────────────────────


def test_discover_nanopore_empty_db_returns_200(client: TestClient) -> None:
    r = client.get("/api/discovery/nanopore")
    assert r.status_code == 200
    assert r.json()["runs"] == []


def test_discover_nanopore_registered_run_not_on_disk(
    client: TestClient, db: sqlite3.Connection
) -> None:
    _seed_registered_run(db, RA, ["barcode01"])
    r = client.get("/api/discovery/nanopore")
    assert r.status_code == 200
    runs = r.json()["runs"]
    assert len(runs) == 1
    run = runs[0]
    assert run["run_accession"] == RA
    assert run["in_metadata"] is True
    assert run["on_disk"] is False
    assert run["status"] == "not_on_disk"


def test_discover_nanopore_disk_only_run_not_in_metadata(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    minknow_dir = _make_minknow_dir(tmp_path, RA, ["barcode01"])
    _set_config(db, "minknow_dir", str(minknow_dir))
    r = client.get("/api/discovery/nanopore?force_refresh=true")
    assert r.status_code == 200
    runs = r.json()["runs"]
    assert len(runs) == 1
    run = runs[0]
    assert run["run_accession"] == RA
    assert run["in_metadata"] is False
    assert run["on_disk"] is True
    assert run["status"] == "not_in_metadata"


def test_discover_nanopore_registered_run_on_disk_ready(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    minknow_dir = _make_minknow_dir(tmp_path, RA, ["barcode01"])
    _set_config(db, "minknow_dir", str(minknow_dir))
    _seed_registered_run(db, RA, ["barcode01"])
    r = client.get("/api/discovery/nanopore?force_refresh=true")
    assert r.status_code == 200
    runs_by_ra = {run["run_accession"]: run for run in r.json()["runs"]}
    run = runs_by_ra[RA]
    assert run["in_metadata"] is True
    assert run["on_disk"] is True
    assert run["status"] == "ready"


def test_discover_nanopore_partial_when_db_barcode_not_on_disk(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    # barcode01 on disk; barcode02 registered in DB but no files → partial
    minknow_dir = _make_minknow_dir(tmp_path, RA, ["barcode01"])
    _set_config(db, "minknow_dir", str(minknow_dir))
    _seed_registered_run(db, RA, ["barcode01", "barcode02"])
    r = client.get("/api/discovery/nanopore?force_refresh=true")
    assert r.status_code == 200
    runs_by_ra = {run["run_accession"]: run for run in r.json()["runs"]}
    assert runs_by_ra[RA]["status"] == "partial"


def test_discover_nanopore_excluded_run_status(
    client: TestClient, db: sqlite3.Connection
) -> None:
    _seed_registered_run(db, RA, ["barcode01"])
    db.execute(
        "INSERT INTO nanopore_run_exclusions (run_accession, reason, created_at, created_by)"
        " VALUES (?,?,?,?)",
        (RA, "test exclusion", "2025-01-01T00:00:00Z", "test"),
    )
    db.commit()
    r = client.get("/api/discovery/nanopore")
    assert r.status_code == 200
    runs_by_ra = {run["run_accession"]: run for run in r.json()["runs"]}
    assert runs_by_ra[RA]["status"] == "excluded"
    assert runs_by_ra[RA]["is_excluded"] is True


def test_discover_nanopore_pipeline_run_status_propagated(
    client: TestClient, db: sqlite3.Connection
) -> None:
    _seed_registered_run(db, RA, ["barcode01"])
    pr_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO pipeline_runs"
        " (id, pipeline_type, status, params, created_at, updated_at, created_by)"
        " VALUES (?,?,?,?,?,?,?)",
        (pr_id, "taxprofiler", "completed", "{}",
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "test"),
    )
    db.execute(
        "INSERT INTO pipeline_run_accessions (pipeline_run_id, run_accession) VALUES (?,?)",
        (pr_id, RA),
    )
    db.commit()
    r = client.get("/api/discovery/nanopore")
    assert r.status_code == 200
    runs_by_ra = {run["run_accession"]: run for run in r.json()["runs"]}
    run = runs_by_ra[RA]
    assert run["last_pipeline_run_status"] == "completed"
    assert run["last_pipeline_run_id"] == pr_id
    assert run["last_pipeline_run_type"] == "taxprofiler"
    # A single run still populates the full pipeline_runs list.
    assert [pr["id"] for pr in run["pipeline_runs"]] == [pr_id]


def test_discover_nanopore_lists_all_pipeline_runs_newest_first(
    client: TestClient, db: sqlite3.Connection
) -> None:
    _seed_registered_run(db, RA, ["barcode01"])
    tax_id = str(uuid.uuid4())
    amr_id = str(uuid.uuid4())
    # taxprofiler launched first, then AMR — AMR is the most recent.
    for pr_id, pipeline_type, created in (
        (tax_id, "taxprofiler", "2026-01-01T00:00:00Z"),
        (amr_id, "wf-metagenomics-amr", "2026-01-02T00:00:00Z"),
    ):
        db.execute(
            "INSERT INTO pipeline_runs"
            " (id, pipeline_type, status, params, created_at, updated_at, created_by)"
            " VALUES (?,?,?,?,?,?,?)",
            (pr_id, pipeline_type, "completed", "{}", created, created, "test"),
        )
        db.execute(
            "INSERT INTO pipeline_run_accessions (pipeline_run_id, run_accession) VALUES (?,?)",
            (pr_id, RA),
        )
    db.commit()
    r = client.get("/api/discovery/nanopore")
    assert r.status_code == 200
    run = {run["run_accession"]: run for run in r.json()["runs"]}[RA]
    # Both runs are exposed, newest first.
    assert [pr["id"] for pr in run["pipeline_runs"]] == [amr_id, tax_id]
    assert [pr["pipeline_type"] for pr in run["pipeline_runs"]] == [
        "wf-metagenomics-amr",
        "taxprofiler",
    ]
    # The last_pipeline_run_* scalars still track the most recent run.
    assert run["last_pipeline_run_id"] == amr_id


def test_discover_nanopore_continuation_detected(
    client: TestClient, db: sqlite3.Connection, no_disk_scan
) -> None:
    # This test is about continuation detection, not disk scanning: suppress the scan
    # so it cannot evict the nanopore_disk_cache rows pre-inserted below.
    # Share site/sample between RA and RA2 to avoid UNIQUE constraint on site_code.
    site_id, sample_id, _ = _seed_registered_run(db, RA, ["barcode01"])
    nra_id2 = _insert_run_accession(db, RA2)
    _insert_nanopore_run(db, nra_id2, sample_id, "barcode01")
    _insert_disk_cache(db, RA,  "FAX00001", "2026-06-10T08:00:00Z", "2026-06-10T10:00:00Z")
    _insert_disk_cache(db, RA2, "FAX00001", "2026-06-10T10:30:00Z", "2026-06-10T12:00:00Z")
    r = client.get("/api/discovery/nanopore")
    assert r.status_code == 200
    runs_by_ra = {run["run_accession"]: run for run in r.json()["runs"]}
    assert RA2 in runs_by_ra[RA]["continuation_run_accessions"]
    assert RA  in runs_by_ra[RA2]["continuation_run_accessions"]


def test_discover_nanopore_response_includes_scanned_at(client: TestClient) -> None:
    r = client.get("/api/discovery/nanopore")
    assert r.status_code == 200
    assert "scanned_at" in r.json()


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/discovery/nanopore/{run_accession}/readiness
# ─────────────────────────────────────────────────────────────────────────────


def test_nanopore_readiness_dot_in_accession_rejected(client: TestClient) -> None:
    r = client.get("/api/discovery/nanopore/invalid.accession/readiness")
    assert r.status_code == 422


def test_nanopore_readiness_accession_too_long_rejected(client: TestClient) -> None:
    r = client.get(f"/api/discovery/nanopore/{'A' * 151}/readiness")
    assert r.status_code == 422


def test_nanopore_readiness_unknown_run_returns_not_on_disk(client: TestClient) -> None:
    r = client.get(f"/api/discovery/nanopore/{RA}/readiness")
    assert r.status_code == 200
    body = r.json()
    assert body["on_disk"] is False
    assert body["in_metadata"] is False
    assert body["status"] == "not_on_disk"
    assert body["action"] == "unavailable"


def test_nanopore_readiness_on_disk_not_registered_action_is_register_launch(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    minknow_dir = _make_minknow_dir(tmp_path, RA, ["barcode01"])
    _set_config(db, "minknow_dir", str(minknow_dir))
    r = client.get(f"/api/discovery/nanopore/{RA}/readiness")
    assert r.status_code == 200
    body = r.json()
    assert body["on_disk"] is True
    assert body["in_metadata"] is False
    assert body["action"] == "register_launch"


def test_nanopore_readiness_on_disk_missing_sample_action_is_register_launch(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    minknow_dir = _make_minknow_dir(tmp_path, RA, ["barcode01"])
    _set_config(db, "minknow_dir", str(minknow_dir))
    nra_id = _insert_run_accession(db, RA)
    _insert_nanopore_run(db, nra_id, sample_id=None, barcode="barcode01")
    r = client.get(f"/api/discovery/nanopore/{RA}/readiness")
    assert r.status_code == 200
    body = r.json()
    assert body["missing_sample"] is True
    assert body["action"] == "register_launch"


def test_nanopore_readiness_fully_registered_on_disk_ready(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    minknow_dir = _make_minknow_dir(tmp_path, RA, ["barcode01"])
    _set_config(db, "minknow_dir", str(minknow_dir))
    _seed_registered_run(db, RA, ["barcode01"])
    r = client.get(f"/api/discovery/nanopore/{RA}/readiness")
    assert r.status_code == 200
    body = r.json()
    assert body["on_disk"] is True
    assert body["in_metadata"] is True
    assert body["missing_site"] is False
    assert body["missing_sample"] is False
    assert body["missing_sampling_date"] is False
    assert body["status"] == "ready"
    assert body["action"] == "launch"
    assert "barcode01" in body["barcodes_on_disk"]


def test_nanopore_readiness_continuation_detected(
    client: TestClient, db: sqlite3.Connection, no_disk_scan
) -> None:
    # Suppress the disk scan so it cannot evict the cache rows pre-inserted below.
    # RA starts 08:00, stops 10:00; RA2 starts 10:30 on same flow cell → continuation
    _insert_disk_cache(db, RA,  "FAX00001", "2026-06-10T08:00:00Z", "2026-06-10T10:00:00Z")
    _insert_disk_cache(db, RA2, "FAX00001", "2026-06-10T10:30:00Z", "2026-06-10T12:00:00Z")
    r = client.get(f"/api/discovery/nanopore/{RA}/readiness")
    assert r.status_code == 200
    body = r.json()
    assert RA2 in body["continuation_run_accessions"]
    assert body["continuation_confidence"] in ("likely", "possible")
    assert body["continuation_evidence"] is not None


def test_nanopore_readiness_merge_decision_needed_when_related_run_present(
    client: TestClient, db: sqlite3.Connection
) -> None:
    # Two accessions sharing the same barcode→sample → related → merge decision needed
    site_id   = _insert_site(db)
    sample_id = _insert_sample(db, site_id)
    nra_id1 = _insert_run_accession(db, RA)
    nra_id2 = _insert_run_accession(db, RA2)
    _insert_nanopore_run(db, nra_id1, sample_id, "barcode01")
    _insert_nanopore_run(db, nra_id2, sample_id, "barcode01")
    r = client.get(f"/api/discovery/nanopore/{RA}/readiness")
    assert r.status_code == 200
    body = r.json()
    assert body["merge_decision_needed"] is True
    assert RA2 in body["related_run_accessions"]


def test_nanopore_readiness_barcodes_list_populated(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    minknow_dir = _make_minknow_dir(tmp_path, RA, ["barcode01", "barcode02"])
    _set_config(db, "minknow_dir", str(minknow_dir))
    _seed_registered_run(db, RA, ["barcode01", "barcode02"])
    r = client.get(f"/api/discovery/nanopore/{RA}/readiness")
    assert r.status_code == 200
    body = r.json()
    barcodes = {b["barcode"]: b for b in body["barcodes"]}
    assert "barcode01" in barcodes
    assert "barcode02" in barcodes
    assert barcodes["barcode01"]["status"] == "ready"
    assert barcodes["barcode01"]["in_metadata"] is True


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/discovery/nanopore/{run_accession}/register
# ─────────────────────────────────────────────────────────────────────────────


_REGISTER_PAYLOAD: dict = {
    "country": "Norway",
    "country_code": "NO",
    "city_code": "BGN",
    "city": "Bergen",
    "site": "01",
    "sample_type": "water",
    "protocol_id": "SQK-LSK114",
    "sequencing_kit_id": "SQK-LSK114",
    "created_by": "test_user",
    "barcodes": [
        {"barcode": "barcode01", "sampling_date": "20260101", "sample_type": "water"},
    ],
}


def test_nanopore_register_creates_site_accession_and_run(
    client: TestClient, db: sqlite3.Connection
) -> None:
    r = client.post(f"/api/discovery/nanopore/{RA}/register", json=_REGISTER_PAYLOAD)
    assert r.status_code == 200
    assert db.execute("SELECT id FROM sites WHERE country_code = 'NO'").fetchone() is not None
    assert db.execute(
        "SELECT id FROM nanopore_run_accessions WHERE run_accession = ?", (RA,)
    ).fetchone() is not None
    assert db.execute(
        "SELECT nr.id FROM nanopore_runs nr"
        " JOIN nanopore_run_accessions nra ON nra.id = nr.accession_id"
        " WHERE nra.run_accession = ? AND nr.barcode = 'barcode01'",
        (RA,),
    ).fetchone() is not None


def test_nanopore_register_returns_readiness_shape(
    client: TestClient, db: sqlite3.Connection
) -> None:
    r = client.post(f"/api/discovery/nanopore/{RA}/register", json=_REGISTER_PAYLOAD)
    assert r.status_code == 200
    body = r.json()
    for field in ("barcodes", "action", "status", "in_metadata",
                  "missing_site", "missing_sample", "missing_sampling_date"):
        assert field in body, f"missing field: {field}"


def test_nanopore_register_metadata_complete_after_registration(
    client: TestClient, db: sqlite3.Connection
) -> None:
    r = client.post(f"/api/discovery/nanopore/{RA}/register", json=_REGISTER_PAYLOAD)
    assert r.status_code == 200
    body = r.json()
    assert body["in_metadata"] is True
    assert body["missing_site"] is False
    assert body["missing_sample"] is False
    assert body["missing_sampling_date"] is False


def test_nanopore_register_is_idempotent(
    client: TestClient, db: sqlite3.Connection
) -> None:
    client.post(f"/api/discovery/nanopore/{RA}/register", json=_REGISTER_PAYLOAD)
    client.post(f"/api/discovery/nanopore/{RA}/register", json=_REGISTER_PAYLOAD)
    assert db.execute("SELECT COUNT(*) FROM sites WHERE country_code = 'NO'").fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM nanopore_run_accessions WHERE run_accession = ?", (RA,)
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM nanopore_runs nr"
        " JOIN nanopore_run_accessions nra ON nra.id = nr.accession_id"
        " WHERE nra.run_accession = ? AND nr.barcode = 'barcode01'",
        (RA,),
    ).fetchone()[0] == 1


def test_nanopore_register_without_country_raises_422(client: TestClient) -> None:
    payload = {**_REGISTER_PAYLOAD, "country": None}
    r = client.post(f"/api/discovery/nanopore/{RA}/register", json=payload)
    assert r.status_code == 422


def test_nanopore_register_reuses_existing_site_by_site_code(
    client: TestClient, db: sqlite3.Connection
) -> None:
    existing_site_id = _insert_site(db, country_code="NO", city_code="BGN", site="01")
    r = client.post(f"/api/discovery/nanopore/{RA}/register", json=_REGISTER_PAYLOAD)
    assert r.status_code == 200
    # No duplicate site created
    assert db.execute("SELECT COUNT(*) FROM sites WHERE country_code = 'NO'").fetchone()[0] == 1
    # The sample's site_id matches the existing site
    nr_row = db.execute(
        "SELECT nr.sample_id FROM nanopore_runs nr"
        " JOIN nanopore_run_accessions nra ON nra.id = nr.accession_id"
        " WHERE nra.run_accession = ?",
        (RA,),
    ).fetchone()
    sample_row = db.execute(
        "SELECT site_id FROM samples WHERE id = ?", (nr_row["sample_id"],)
    ).fetchone()
    assert sample_row["site_id"] == existing_site_id


def test_nanopore_register_re_registration_updates_sample_link(
    client: TestClient, db: sqlite3.Connection
) -> None:
    # Register once, creating a sample
    client.post(f"/api/discovery/nanopore/{RA}/register", json=_REGISTER_PAYLOAD)
    original_sample = db.execute(
        "SELECT nr.sample_id FROM nanopore_runs nr"
        " JOIN nanopore_run_accessions nra ON nra.id = nr.accession_id"
        " WHERE nra.run_accession = ? AND nr.barcode = 'barcode01'",
        (RA,),
    ).fetchone()["sample_id"]

    # Register again with a different sampling_date → a new sample is created and linked
    payload2 = {
        **_REGISTER_PAYLOAD,
        "barcodes": [{"barcode": "barcode01", "sampling_date": "20260201", "sample_type": "water"}],
    }
    client.post(f"/api/discovery/nanopore/{RA}/register", json=payload2)
    updated_sample = db.execute(
        "SELECT nr.sample_id FROM nanopore_runs nr"
        " JOIN nanopore_run_accessions nra ON nra.id = nr.accession_id"
        " WHERE nra.run_accession = ? AND nr.barcode = 'barcode01'",
        (RA,),
    ).fetchone()["sample_id"]
    assert updated_sample != original_sample


def test_nanopore_register_disk_cache_updated(
    client: TestClient, db: sqlite3.Connection, no_disk_scan
) -> None:
    # Suppress the disk scan that nanopore_readiness() (called at the end of register)
    # would otherwise run, wiping the row register just inserted.
    client.post(f"/api/discovery/nanopore/{RA}/register", json=_REGISTER_PAYLOAD)
    row = db.execute(
        "SELECT run_accession FROM nanopore_disk_cache WHERE run_accession = ?", (RA,)
    ).fetchone()
    assert row is not None


def test_nanopore_register_invalid_run_accession_rejected(client: TestClient) -> None:
    # Invalid format reaches nanopore_readiness() at the end of register → 422
    r = client.post(
        "/api/discovery/nanopore/invalid.accession/register", json=_REGISTER_PAYLOAD
    )
    assert r.status_code == 422
