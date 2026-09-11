"""Tests for backend.app.db.queries — the shared SQL query layer.

Focus: contract tests that pin the exact dict keys and column renames produced
by each function.  These tests exist specifically to give Issue 11
(postprocessor row-to-dict standardisation) a safety net before any rename
of SQL aliases or output field names.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.db.queries import get_metadata_rows_from_db

# ── helpers ───────────────────────────────────────────────────────────────────

RUN_ACCESSION = "ERR000001"

SITE_PAYLOAD = {
    "country": "Norway",
    "country_code": "NO",
    "city_code": "BGO",
    "site": "Harbour",
    "longitude": 5.3221,
    "latitude": 60.3913,
}

SAMPLE_PAYLOAD = {
    "sample_type": "water",
    "sampling_date": "20240601",
}

RUN_PAYLOAD = {
    "run_accession": RUN_ACCESSION,
    "barcode": "barcode01",
    "protocol_id": "SQK-LSK114",
    "sequencing_kit_id": "SQK-LSK114",
    "type": "GridION",
}


def _make_site(client: TestClient, payload: dict | None = None) -> dict:
    r = client.post("/api/sites", json=payload or SITE_PAYLOAD)
    assert r.status_code == 201, r.text
    return r.json()


def _make_sample(client: TestClient, site_id: str, payload: dict | None = None) -> dict:
    r = client.post("/api/samples", json={**(payload or SAMPLE_PAYLOAD), "site_id": site_id})
    assert r.status_code == 201, r.text
    return r.json()


def _make_run(client: TestClient, sample_id: str, payload: dict | None = None) -> dict:
    r = client.post("/api/nanopore-runs", json={**(payload or RUN_PAYLOAD), "sample_id": sample_id})
    assert r.status_code == 201, r.text
    return r.json()


# ── get_metadata_rows_from_db ─────────────────────────────────────────────────


class TestGetMetadataRowsFromDb:
    """Contract tests for get_metadata_rows_from_db.

    Every test that touches output keys is a potential guard for Issue 11:
    if a future rename changes a key name here, the test failure is the
    intended signal that downstream consumers (Enlighten) must be updated too.
    """

    def test_empty_accessions_returns_empty_list(self, db: sqlite3.Connection) -> None:
        assert get_metadata_rows_from_db(db, []) == []

    def test_unknown_accession_returns_empty_list(self, db: sqlite3.Connection) -> None:
        assert get_metadata_rows_from_db(db, ["DOES_NOT_EXIST"]) == []

    def test_returns_one_row_per_barcode(
        self, client: TestClient, db: sqlite3.Connection
    ) -> None:
        site = _make_site(client)
        sample = _make_sample(client, site["id"])
        _make_run(client, sample["id"])

        rows = get_metadata_rows_from_db(db, [RUN_ACCESSION])
        assert len(rows) == 1

    def test_all_expected_keys_present(
        self, client: TestClient, db: sqlite3.Connection
    ) -> None:
        """Pin the complete set of output keys — any addition or removal is a breaking change."""
        site = _make_site(client)
        sample = _make_sample(client, site["id"])
        _make_run(client, sample["id"])

        row = get_metadata_rows_from_db(db, [RUN_ACCESSION])[0]
        expected_keys = {
            "run_accession",
            "barcode",
            "protocol_id",
            "sequencing_kit_id",
            "alias",
            "type",
            "runName",
            "sampleName",
            "sample_code",
            "sample_id",       # renamed: same value as sample_code (Enlighten compat)
            "sampling_date",
            "sampling_site_id",  # renamed: DB column is site_code
            "sample_type",
            "country",
            "lon",             # renamed: DB column is longitude
            "lat",             # renamed: DB column is latitude
        }
        assert set(row.keys()) == expected_keys

    def test_column_rename_lon_lat(
        self, client: TestClient, db: sqlite3.Connection
    ) -> None:
        """lon/lat are floats derived from longitude/latitude — the DB column names
        must NOT appear in the output dict."""
        site = _make_site(client)
        sample = _make_sample(client, site["id"])
        _make_run(client, sample["id"])

        row = get_metadata_rows_from_db(db, [RUN_ACCESSION])[0]

        assert "longitude" not in row, "raw DB column 'longitude' must not be exposed"
        assert "latitude" not in row, "raw DB column 'latitude' must not be exposed"
        assert row["lon"] == pytest.approx(5.3221)
        assert row["lat"] == pytest.approx(60.3913)

    def test_column_rename_sampling_site_id(
        self, client: TestClient, db: sqlite3.Connection
    ) -> None:
        """sampling_site_id carries the site_code value — site_code must not appear."""
        site = _make_site(client)
        sample = _make_sample(client, site["id"])
        _make_run(client, sample["id"])

        row = get_metadata_rows_from_db(db, [RUN_ACCESSION])[0]

        assert "site_code" not in row, "raw DB column 'site_code' must not be exposed"
        assert row["sampling_site_id"] == "NOBGOHarbour"

    def test_sample_id_alias_equals_sample_code(
        self, client: TestClient, db: sqlite3.Connection
    ) -> None:
        """sample_id is a deliberate alias for sample_code (Enlighten data contract)."""
        site = _make_site(client)
        sample = _make_sample(client, site["id"])
        _make_run(client, sample["id"])

        row = get_metadata_rows_from_db(db, [RUN_ACCESSION])[0]

        assert row["sample_id"] == row["sample_code"]
        assert row["sample_id"] != "", "sample_id should not be empty"

    def test_alias_field_combines_sample_code_and_barcode(
        self, client: TestClient, db: sqlite3.Connection
    ) -> None:
        site = _make_site(client)
        sample = _make_sample(client, site["id"])
        _make_run(client, sample["id"])

        row = get_metadata_rows_from_db(db, [RUN_ACCESSION])[0]
        sample_code = row["sample_code"]
        assert row["alias"] == f"{sample_code}_barcode01"

    def test_lon_lat_none_when_site_has_no_coordinates(
        self, client: TestClient, db: sqlite3.Connection
    ) -> None:
        site = _make_site(client, {
            "country": "Norway",
            "country_code": "NO",
            "city_code": "OSL",
            "site": "River",
            # no longitude / latitude
        })
        sample = _make_sample(client, site["id"])
        _make_run(client, sample["id"], {**RUN_PAYLOAD, "run_accession": "ERR000099"})

        rows = get_metadata_rows_from_db(db, ["ERR000099"])
        assert len(rows) == 1
        assert rows[0]["lon"] is None
        assert rows[0]["lat"] is None

    def test_filters_to_requested_accessions_only(
        self, client: TestClient, db: sqlite3.Connection
    ) -> None:
        site = _make_site(client)
        sample = _make_sample(client, site["id"])
        _make_run(client, sample["id"])
        _make_run(client, sample["id"], {**RUN_PAYLOAD, "run_accession": "ERR000002", "barcode": "barcode02"})

        rows = get_metadata_rows_from_db(db, [RUN_ACCESSION])
        assert len(rows) == 1
        assert rows[0]["run_accession"] == RUN_ACCESSION

    def test_multiple_barcodes_returned_in_order(
        self, client: TestClient, db: sqlite3.Connection
    ) -> None:
        site = _make_site(client)
        sample = _make_sample(client, site["id"])
        _make_run(client, sample["id"], {**RUN_PAYLOAD, "barcode": "barcode02"})
        _make_run(client, sample["id"], {**RUN_PAYLOAD, "barcode": "barcode01"})

        rows = get_metadata_rows_from_db(db, [RUN_ACCESSION])
        assert len(rows) == 2
        assert [r["barcode"] for r in rows] == ["barcode01", "barcode02"]

    def test_multiple_run_accessions(
        self, client: TestClient, db: sqlite3.Connection
    ) -> None:
        site = _make_site(client)
        sample = _make_sample(client, site["id"])
        _make_run(client, sample["id"])
        _make_run(client, sample["id"], {**RUN_PAYLOAD, "run_accession": "ERR000002", "barcode": "barcode02"})

        rows = get_metadata_rows_from_db(db, [RUN_ACCESSION, "ERR000002"])
        accessions = {r["run_accession"] for r in rows}
        assert accessions == {RUN_ACCESSION, "ERR000002"}
