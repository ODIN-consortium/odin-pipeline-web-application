"""Update endpoints must stamp `updated_at`/`updated_by` only when something changed.

`samples`, `sites` and `biomeme_runs` each issue a fixed, unconditional full-row UPDATE
built from `update_data.get(key, current[key])`, so every PATCH rewrites every column —
including the ones nobody touched — and always bumps the audit columns.

That is not cosmetic. `updated_at` is deliberately *not* in sync's `_AUDIT_FIELDS` (unlike
`created_by`/`updated_by`), so it does drive merge decisions: `api/sync.py` computes
`incoming_is_newer` from it, and the default decision for an updated row is "accept only if
incoming is newer". A no-op write therefore makes this device's copy look newer than it is
and can silently make another device's genuine edit lose the merge.

It is also the common case rather than an edge case: `toUpdatePayload` in
`core/utils/form-payload.ts` deliberately sends the *whole* form (filtering it out broke
field-clearing), so every save from every edit dialog submits every field.

Each endpoint below is checked three ways: an identical resubmission stamps nothing, a real
edit still stamps, and the stored derived column (`sample_code` / `site_code`) is still
recomputed when its inputs change — the last being the guard against the fix over-reaching.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

DEVICE = "device-b"


# ── helpers ───────────────────────────────────────────────────────────────────


def _audit(db: sqlite3.Connection, table: str, row_id: str) -> tuple:
    # Table names come from this module's own literals, never from user input.
    row = db.execute(
        f"SELECT updated_at, updated_by FROM {table} WHERE id = ?",  # noqa: S608
        (row_id,),
    ).fetchone()
    return row["updated_at"], row["updated_by"]


def _make_site(client: TestClient, site: str = "Park") -> dict:
    r = client.post(
        "/api/sites",
        json={
            "country": "Norway",
            "country_code": "NO",
            "city_code": "BGO",
            "city": "Bergen",
            "site": site,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _make_sample(client: TestClient, site_id: str, sample_type: str = "water") -> dict:
    r = client.post(
        "/api/samples",
        json={"site_id": site_id, "sample_type": sample_type, "sampling_date": "20240601"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _make_biomeme_run(client: TestClient, sample_id: str) -> dict:
    r = client.post(
        "/api/biomeme-runs",
        json={"biomeme_run_name": "BM-001", "sample_id": sample_id, "comments": "first"},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture()
def device(monkeypatch) -> str:
    """A device name distinct from the one rows were created with (conftest blanks it)."""
    monkeypatch.setenv("ODIN_DEVICE_NAME", DEVICE)
    return DEVICE


# ── samples ───────────────────────────────────────────────────────────────────


def test_sample_resubmitting_identical_values_stamps_nothing(
    client: TestClient, db: sqlite3.Connection, device: str
) -> None:
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    before = _audit(db, "samples", sample["id"])

    # Exactly what the UI sends: the whole form, unchanged.
    r = client.patch(
        f"/api/samples/{sample['id']}",
        json={
            "site_id": site["id"],
            "sample_type": "water",
            "sampling_date": "20240601",
            "comments": None,
        },
    )
    assert r.status_code == 200, r.text
    assert _audit(db, "samples", sample["id"]) == before


def test_sample_real_edit_still_stamps(
    client: TestClient, db: sqlite3.Connection, device: str
) -> None:
    site = _make_site(client)
    sample = _make_sample(client, site["id"])

    r = client.patch(f"/api/samples/{sample['id']}", json={"comments": "changed"})
    assert r.status_code == 200, r.text
    assert _audit(db, "samples", sample["id"])[1] == DEVICE


def test_sample_code_is_still_recomputed_when_its_inputs_change(
    client: TestClient, db: sqlite3.Connection, device: str
) -> None:
    """Guard against over-reaching: the derived column must still be written."""
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    assert sample["sample_code"] == "NOBGOPark_water"

    r = client.patch(f"/api/samples/{sample['id']}", json={"sample_type": "sediment"})
    assert r.status_code == 200, r.text
    assert r.json()["sample_code"] == "NOBGOPark_sediment"
    assert _audit(db, "samples", sample["id"])[1] == DEVICE


# ── sites ─────────────────────────────────────────────────────────────────────


def test_site_resubmitting_identical_values_stamps_nothing(
    client: TestClient, db: sqlite3.Connection, device: str
) -> None:
    site = _make_site(client)
    before = _audit(db, "sites", site["id"])

    r = client.patch(
        f"/api/sites/{site['id']}",
        json={
            "country": "Norway",
            "country_code": "NO",
            "city_code": "BGO",
            "city": "Bergen",
            "site": "Park",
            "comments": None,
        },
    )
    assert r.status_code == 200, r.text
    assert _audit(db, "sites", site["id"]) == before


def test_site_real_edit_still_stamps(
    client: TestClient, db: sqlite3.Connection, device: str
) -> None:
    site = _make_site(client)
    r = client.patch(f"/api/sites/{site['id']}", json={"comments": "changed"})
    assert r.status_code == 200, r.text
    assert _audit(db, "sites", site["id"])[1] == DEVICE


def test_site_code_is_still_recomputed_when_its_inputs_change(
    client: TestClient, db: sqlite3.Connection, device: str
) -> None:
    site = _make_site(client)
    assert site["site_code"] == "NOBGOPark"

    r = client.patch(f"/api/sites/{site['id']}", json={"site": "Harbour"})
    assert r.status_code == 200, r.text
    assert r.json()["site_code"] == "NOBGOHarbour"
    assert _audit(db, "sites", site["id"])[1] == DEVICE


# ── biomeme_runs ──────────────────────────────────────────────────────────────


def test_biomeme_resubmitting_identical_values_stamps_nothing(
    client: TestClient, db: sqlite3.Connection, device: str
) -> None:
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    run = _make_biomeme_run(client, sample["id"])
    before = _audit(db, "biomeme_runs", run["id"])

    r = client.patch(
        f"/api/biomeme-runs/{run['id']}",
        json={
            "biomeme_run_name": "BM-001",
            "sample_id": sample["id"],
            "comments": "first",
        },
    )
    assert r.status_code == 200, r.text
    assert _audit(db, "biomeme_runs", run["id"]) == before


def test_biomeme_real_edit_still_stamps(
    client: TestClient, db: sqlite3.Connection, device: str
) -> None:
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    run = _make_biomeme_run(client, sample["id"])

    r = client.patch(f"/api/biomeme-runs/{run['id']}", json={"comments": "changed"})
    assert r.status_code == 200, r.text
    assert _audit(db, "biomeme_runs", run["id"])[1] == DEVICE


def test_biomeme_unlinking_the_sample_still_stamps(
    client: TestClient, db: sqlite3.Connection, device: str
) -> None:
    """Unlinking is a supported edit — sample_id is nullable by design — so it must stamp."""
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    run = _make_biomeme_run(client, sample["id"])

    r = client.patch(f"/api/biomeme-runs/{run['id']}", json={"sample_id": None})
    assert r.status_code == 200, r.text
    assert db.execute(
        "SELECT sample_id FROM biomeme_runs WHERE id = ?", (run["id"],)
    ).fetchone()["sample_id"] is None
    assert _audit(db, "biomeme_runs", run["id"])[1] == DEVICE


# ── nanopore_run_accessions ───────────────────────────────────────────────────


def _make_accession(client: TestClient) -> dict:
    r = client.post("/api/nanopore-run-accessions", json={"run_accession": "ERR555000"})
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_accession_resubmitting_an_unchanged_field_stamps_nothing(
    client: TestClient, db: sqlite3.Connection, device: str
) -> None:
    """The milder form of the same bug: this one wrote every *submitted* field."""
    nra = _make_accession(client)
    before = _audit(db, "nanopore_run_accessions", nra["id"])

    r = client.patch(
        f"/api/nanopore-run-accessions/{nra['id']}", json={"run_accession": "ERR555000"}
    )
    assert r.status_code == 200, r.text
    assert _audit(db, "nanopore_run_accessions", nra["id"]) == before


def test_accession_real_edit_still_stamps(
    client: TestClient, db: sqlite3.Connection, device: str
) -> None:
    nra = _make_accession(client)
    r = client.patch(
        f"/api/nanopore-run-accessions/{nra['id']}", json={"comments": "changed"}
    )
    assert r.status_code == 200, r.text
    assert _audit(db, "nanopore_run_accessions", nra["id"])[1] == DEVICE
