"""Contract tests: an explicit null in an update payload clears an optional field.

Every update endpoint uses ``model_dump(exclude_unset=True)``, which distinguishes
three cases that must stay distinct:

  * key absent          → leave the stored value alone (partial update)
  * key present, null   → clear the field
  * key present, value  → set the field

The middle case is the one the UI needs in order to let a user empty an optional
field, and it had no coverage. (The frontend forms were dropping cleared fields
from the payload instead of sending null, so clearing silently did nothing —
these tests pin the API side of that contract.)
"""

import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

_NOW = "2025-01-01T00:00:00.000Z"


# ── fixtures: one of each entity, with its optional fields populated ───────────


@pytest.fixture()
def site_id(client: TestClient) -> str:
    r = client.post(
        "/api/sites",
        json={
            "country_code": "NO",
            "city_code": "BGN",
            "site": "01",
            "country": "Norway",
            "city": "Bergen",
            "location": "Harbour",
            "comments": "initial note",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture()
def sample_id(client: TestClient, site_id: str) -> str:
    r = client.post(
        "/api/samples",
        json={
            "site_id": site_id,
            "sample_type": "water",
            "sampling_date": "20260601",
            "comments": "initial note",
            "comments_sampling": "sampling note",
            "partner_sample_code": "P-1",
            "depth": "5",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture()
def biomeme_run_id(client: TestClient, db: sqlite3.Connection, sample_id: str) -> str:
    run_id = str(uuid.uuid4())
    db.execute(
        """INSERT INTO biomeme_runs
           (id, biomeme_run_name, sample_id, biomeme_sample_id, dilution_factor,
            comments, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (run_id, "BM-001", sample_id, "BS-1", 2.5, "initial note", _NOW, _NOW),
    )
    db.commit()
    return run_id


@pytest.fixture()
def nanopore_run_id(client: TestClient, sample_id: str) -> str:
    r = client.post(
        "/api/nanopore-runs",
        json={
            "label": "planned-run",
            "sample_id": sample_id,
            "barcode": "barcode01",
            "protocol_id": "SQK-LSK114",
            "sequencing_kit_id": "SQK-LSK114",
            "comments": "initial note",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── clearing with an explicit null ────────────────────────────────────────────


def test_site_comment_can_be_cleared(client: TestClient, site_id: str) -> None:
    assert client.get(f"/api/sites/{site_id}").json()["comments"] == "initial note"

    r = client.patch(f"/api/sites/{site_id}", json={"comments": None})

    assert r.status_code == 200, r.text
    assert r.json()["comments"] is None
    assert client.get(f"/api/sites/{site_id}").json()["comments"] is None


def test_sample_comment_can_be_cleared(client: TestClient, sample_id: str) -> None:
    r = client.patch(f"/api/samples/{sample_id}", json={"comments": None})

    assert r.status_code == 200, r.text
    assert r.json()["comments"] is None


@pytest.mark.parametrize(
    "field", ["comments", "comments_sampling", "partner_sample_code", "depth"]
)
def test_every_optional_sample_field_can_be_cleared(
    client: TestClient, sample_id: str, field: str
) -> None:
    r = client.patch(f"/api/samples/{sample_id}", json={field: None})

    assert r.status_code == 200, r.text
    assert r.json()[field] is None


def test_biomeme_run_comment_can_be_cleared(
    client: TestClient, biomeme_run_id: str
) -> None:
    r = client.patch(f"/api/biomeme-runs/{biomeme_run_id}", json={"comments": None})

    assert r.status_code == 200, r.text
    assert r.json()["comments"] is None


@pytest.mark.parametrize("field", ["comments", "biomeme_sample_id", "dilution_factor"])
def test_every_optional_biomeme_field_can_be_cleared(
    client: TestClient, biomeme_run_id: str, field: str
) -> None:
    r = client.patch(f"/api/biomeme-runs/{biomeme_run_id}", json={field: None})

    assert r.status_code == 200, r.text
    assert r.json()[field] is None


def test_biomeme_run_sample_can_be_unlinked(
    client: TestClient, biomeme_run_id: str
) -> None:
    """Unlinking a sample is a legitimate edit, not just a metadata tweak."""
    r = client.patch(f"/api/biomeme-runs/{biomeme_run_id}", json={"sample_id": None})

    assert r.status_code == 200, r.text
    assert r.json()["sample_id"] is None


def test_nanopore_run_comment_can_be_cleared(
    client: TestClient, nanopore_run_id: str
) -> None:
    r = client.patch(f"/api/nanopore-runs/{nanopore_run_id}", json={"comments": None})

    assert r.status_code == 200, r.text
    assert r.json()["comments"] is None


# ── partial updates must still leave untouched fields alone ────────────────────


def test_omitted_fields_are_not_cleared(client: TestClient, sample_id: str) -> None:
    """The counterpart guarantee: a partial update must not blank everything else."""
    r = client.patch(f"/api/samples/{sample_id}", json={"comments": "changed"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["comments"] == "changed"
    assert body["comments_sampling"] == "sampling note"
    assert body["partner_sample_code"] == "P-1"


def test_omitted_fields_are_not_cleared_for_biomeme(
    client: TestClient, biomeme_run_id: str
) -> None:
    r = client.patch(
        f"/api/biomeme-runs/{biomeme_run_id}", json={"biomeme_sample_id": "BS-2"}
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["biomeme_sample_id"] == "BS-2"
    assert body["comments"] == "initial note"


# ── other update endpoints with optional fields ───────────────────────────────


def test_database_entry_params_can_be_cleared(
    client: TestClient, db: sqlite3.Connection
) -> None:
    entry_id = str(uuid.uuid4())
    db.execute(
        """INSERT INTO databases (id, tool, db_name, db_params, db_path, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?)""",
        (entry_id, "kraken2", "test_db", "--quick", "/db", _NOW, _NOW),
    )
    db.commit()

    r = client.put(
        f"/api/databases/{entry_id}",
        json={"tool": "kraken2", "db_name": "test_db", "db_path": "/db", "db_params": None},
    )

    assert r.status_code == 200, r.text
    assert r.json()["db_params"] in (None, "")


def test_lookup_value_description_can_be_cleared(
    client: TestClient, db: sqlite3.Connection
) -> None:
    entry_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO lookup_values (id, list, code, description, external_code)"
        " VALUES (?,?,?,?,?)",
        (entry_id, "sample_type", "clearme", "a description", "EXT-1"),
    )
    db.commit()

    r = client.put(
        f"/api/lookup-values/sample_type/{entry_id}",
        json={"description": None, "external_code": None},
    )

    assert r.status_code == 200, r.text
    assert r.json()["description"] is None
    assert r.json()["external_code"] is None
