"""Tests for /api/lookup-values/* endpoints — CRUD, ISO countries, seed."""

import uuid

from fastapi.testclient import TestClient


def _create(client: TestClient, list_name: str, code: str, description: str | None = None) -> dict:
    r = client.post(f"/api/lookup-values/{list_name}", json={"code": code, "description": description})
    assert r.status_code == 201, r.text
    return r.json()


# ── ISO countries ─────────────────────────────────────────────────────────────


def test_iso_countries_returns_list(client: TestClient) -> None:
    r = client.get("/api/lookup-values/countries/iso")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) > 100


def test_iso_countries_sorted_by_description(client: TestClient) -> None:
    r = client.get("/api/lookup-values/countries/iso")
    descriptions = [e["description"] for e in r.json()]
    assert descriptions == sorted(descriptions)


def test_iso_countries_norway_present(client: TestClient) -> None:
    r = client.get("/api/lookup-values/countries/iso")
    entry = next((e for e in r.json() if e["code"] == "NO"), None)
    assert entry is not None
    assert entry["description"] == "Norway"


# ── get list ──────────────────────────────────────────────────────────────────


def test_get_list_returns_seeded_entries(client: TestClient) -> None:
    r = client.get("/api/lookup-values/sample_type")
    assert r.status_code == 200
    codes = {e["code"] for e in r.json()}
    assert "water" in codes
    assert "sediment" in codes


def test_get_list_empty_for_unknown_list(client: TestClient) -> None:
    r = client.get("/api/lookup-values/nonexistent_list")
    assert r.status_code == 200
    assert r.json() == []


def test_get_list_sorted_by_code(client: TestClient) -> None:
    _create(client, "sample_type", "zzz")
    _create(client, "sample_type", "aaa")
    r = client.get("/api/lookup-values/sample_type")
    codes = [e["code"] for e in r.json()]
    assert codes == sorted(codes)


def test_get_list_entry_has_required_fields(client: TestClient) -> None:
    r = client.get("/api/lookup-values/sample_type")
    entry = r.json()[0]
    assert "id" in entry
    assert "list" in entry
    assert "code" in entry


# ── create entry ──────────────────────────────────────────────────────────────


def test_create_entry_returns_201_with_body(client: TestClient) -> None:
    data = _create(client, "sample_type", "soil", "Soil (test)")
    assert data["code"] == "soil"
    assert data["description"] == "Soil (test)"
    assert data["list"] == "sample_type"
    assert "id" in data


def test_create_entry_persists_in_list(client: TestClient) -> None:
    _create(client, "sample_type", "soil")
    codes = {e["code"] for e in client.get("/api/lookup-values/sample_type").json()}
    assert "soil" in codes


def test_create_entry_optional_description_is_null(client: TestClient) -> None:
    data = _create(client, "sample_type", "soil")
    assert data["description"] is None


def test_create_entry_duplicate_code_returns_409(client: TestClient) -> None:
    _create(client, "sample_type", "soil")
    r = client.post("/api/lookup-values/sample_type", json={"code": "soil"})
    assert r.status_code == 409


def test_create_entry_unmanaged_list_returns_403(client: TestClient) -> None:
    r = client.post("/api/lookup-values/some_internal_list", json={"code": "x"})
    assert r.status_code == 403


def test_create_entry_all_managed_lists_accepted(client: TestClient) -> None:
    for list_name in ("sample_type", "protocol_id", "sequencing_kit_id", "mpox_type"):
        r = client.post(f"/api/lookup-values/{list_name}", json={"code": f"test_{list_name}"})
        assert r.status_code == 201, f"Expected 201 for managed list '{list_name}', got {r.status_code}"


# ── update entry ──────────────────────────────────────────────────────────────


def test_update_entry_changes_description(client: TestClient) -> None:
    entry = _create(client, "sample_type", "soil", "Old")
    r = client.put(f"/api/lookup-values/sample_type/{entry['id']}", json={"description": "New"})
    assert r.status_code == 200
    assert r.json()["description"] == "New"
    assert r.json()["code"] == "soil"


def test_update_entry_sets_external_code(client: TestClient) -> None:
    entry = _create(client, "sample_type", "soil")
    r = client.put(f"/api/lookup-values/sample_type/{entry['id']}", json={"external_code": "EXT-01"})
    assert r.status_code == 200
    assert r.json()["external_code"] == "EXT-01"


def test_update_entry_empty_body_returns_unchanged(client: TestClient) -> None:
    entry = _create(client, "sample_type", "soil", "Original")
    r = client.put(f"/api/lookup-values/sample_type/{entry['id']}", json={})
    assert r.status_code == 200
    assert r.json()["description"] == "Original"


def test_update_entry_not_found_returns_404(client: TestClient) -> None:
    r = client.put(f"/api/lookup-values/sample_type/{uuid.uuid4()}", json={"description": "x"})
    assert r.status_code == 404


def test_update_entry_unmanaged_list_returns_403(client: TestClient) -> None:
    r = client.put(f"/api/lookup-values/internal_list/{uuid.uuid4()}", json={"description": "x"})
    assert r.status_code == 403


def test_update_entry_wrong_list_for_id_returns_404(client: TestClient) -> None:
    entry = _create(client, "sample_type", "soil")
    # Use the real id but wrong list_name
    r = client.put(f"/api/lookup-values/mpox_type/{entry['id']}", json={"description": "x"})
    assert r.status_code == 404


# ── delete entry ──────────────────────────────────────────────────────────────


def test_delete_entry_returns_204(client: TestClient) -> None:
    entry = _create(client, "sample_type", "soil")
    r = client.delete(f"/api/lookup-values/sample_type/{entry['id']}")
    assert r.status_code == 204


def test_delete_entry_removes_from_list(client: TestClient) -> None:
    entry = _create(client, "sample_type", "soil")
    client.delete(f"/api/lookup-values/sample_type/{entry['id']}")
    codes = {e["code"] for e in client.get("/api/lookup-values/sample_type").json()}
    assert "soil" not in codes


def test_delete_entry_not_found_returns_404(client: TestClient) -> None:
    r = client.delete(f"/api/lookup-values/sample_type/{uuid.uuid4()}")
    assert r.status_code == 404


def test_delete_entry_unmanaged_list_returns_403(client: TestClient) -> None:
    r = client.delete(f"/api/lookup-values/internal_list/{uuid.uuid4()}")
    assert r.status_code == 403


def test_delete_entry_in_use_by_run_returns_409(client: TestClient) -> None:
    # SQK-LSK114 is seeded in conftest as a protocol_id lookup code.
    # Create a nanopore run that references it — delete must be blocked.
    run_r = client.post("/api/nanopore-runs", json={
        "run_accession": "run001", "barcode": "barcode01", "protocol_id": "SQK-LSK114",
    })
    assert run_r.status_code == 201

    entries = client.get("/api/lookup-values/protocol_id").json()
    entry = next(e for e in entries if e["code"] == "SQK-LSK114")
    r = client.delete(f"/api/lookup-values/protocol_id/{entry['id']}")
    assert r.status_code == 409
    assert "referenced" in r.json()["detail"].lower()


def test_delete_entry_in_use_by_sample_returns_409(client: TestClient) -> None:
    # Create a sample with sample_type="water" (seeded in conftest), then try to delete it.
    site_r = client.post("/api/sites", json={
        "country": "Norway", "country_code": "NO", "city_code": "BGO", "site": "X",
    })
    site_id = site_r.json()["id"]
    client.post("/api/samples", json={
        "site_id": site_id, "sampling_date": "20240601", "sample_type": "water",
    })

    entries = client.get("/api/lookup-values/sample_type").json()
    water_entry = next(e for e in entries if e["code"] == "water")
    r = client.delete(f"/api/lookup-values/sample_type/{water_entry['id']}")
    assert r.status_code == 409


# ── seed endpoint ─────────────────────────────────────────────────────────────


def test_seed_returns_201_with_detail(client: TestClient) -> None:
    r = client.post("/api/lookup-values/seed")
    assert r.status_code == 201
    assert "detail" in r.json()


def test_seed_does_not_overwrite_existing_entries(client: TestClient) -> None:
    # Manually update a seeded entry, then re-seed. The change must survive.
    entries = client.get("/api/lookup-values/sample_type").json()
    water = next(e for e in entries if e["code"] == "water")
    client.put(f"/api/lookup-values/sample_type/{water['id']}", json={"description": "Custom water"})

    client.post("/api/lookup-values/seed")

    updated_entries = client.get("/api/lookup-values/sample_type").json()
    updated_water = next(e for e in updated_entries if e["code"] == "water")
    assert updated_water["description"] == "Custom water"
