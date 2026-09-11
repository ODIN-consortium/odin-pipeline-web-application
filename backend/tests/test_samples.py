"""Tests for /api/samples — CRUD, sample_code derivation, FK validation."""

from fastapi.testclient import TestClient

# ── test data ─────────────────────────────────────────────────────────────────

SITE_PAYLOAD = {
    "country": "Norway",
    "country_code": "NO",
    "city_code": "BGO",
    "site": "Park",
    "city": "Bergen",
}

SAMPLE_PAYLOAD = {
    "sample_type": "water",
    "sampling_date": "20240601",
    "depth": "10m",
    "created_by": "test",
}


# ── helpers ───────────────────────────────────────────────────────────────────


def _create_site(client: TestClient, payload: dict | None = None) -> dict:
    r = client.post("/api/sites", json=payload or SITE_PAYLOAD)
    assert r.status_code == 201, r.text
    return r.json()


def _create_sample(client: TestClient, site_id: str, payload: dict | None = None) -> dict:
    p = {**(payload or SAMPLE_PAYLOAD), "site_id": site_id}
    r = client.post("/api/samples", json=p)
    assert r.status_code == 201, r.text
    return r.json()


# ── list ─────────────────────────────────────────────────────────────────────


def test_list_empty(client: TestClient) -> None:
    r = client.get("/api/samples")
    assert r.status_code == 200
    assert r.json() == []


def test_list_returns_created(client: TestClient) -> None:
    site = _create_site(client)
    _create_sample(client, site["id"])
    assert len(client.get("/api/samples").json()) == 1


def test_list_filter_by_site_id(client: TestClient) -> None:
    site_a = _create_site(client)
    site_b = _create_site(
        client,
        {"country": "Sweden", "country_code": "SE", "city_code": "STO", "site": "Lake"},
    )
    _create_sample(client, site_a["id"])
    _create_sample(client, site_b["id"])

    r = client.get("/api/samples", params={"site_id": site_a["id"]})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["site_id"] == site_a["id"]


# ── create ────────────────────────────────────────────────────────────────────


def test_create_derives_sample_code(client: TestClient) -> None:
    site = _create_site(client)
    data = _create_sample(client, site["id"])
    # Expected: {site_ID}_{sample_type} = NOBGOPark_water
    assert data["sample_code"] == "NOBGOPark_water"


def test_create_sample_code_not_sent_by_client(client: TestClient) -> None:
    """sample_code injected by client must be ignored."""
    site = _create_site(client)
    p = {**SAMPLE_PAYLOAD, "site_id": site["id"], "sample_code": "HACKED"}
    r = client.post("/api/samples", json=p)
    assert r.status_code == 201
    assert r.json()["sample_code"] == "NOBGOPark_water"


def test_create_missing_site_id_rejected(client: TestClient) -> None:
    r = client.post("/api/samples", json=SAMPLE_PAYLOAD)
    assert r.status_code == 422


def test_create_invalid_site_id_rejected(client: TestClient) -> None:
    p = {**SAMPLE_PAYLOAD, "site_id": "does-not-exist"}
    r = client.post("/api/samples", json=p)
    assert r.status_code == 422


def test_create_missing_sample_type_rejected(client: TestClient) -> None:
    site = _create_site(client)
    p = {"sampling_date": "20240601", "site_id": site["id"]}
    r = client.post("/api/samples", json=p)
    assert r.status_code == 422


def test_create_duplicate_sample_code_and_date_rejected(client: TestClient) -> None:
    site = _create_site(client)
    _create_sample(client, site["id"])  # NOBGOPark_water / 20240601
    r = client.post("/api/samples", json={**SAMPLE_PAYLOAD, "site_id": site["id"]})
    assert r.status_code == 409


def test_create_same_code_different_date_allowed(client: TestClient) -> None:
    """Same site + sample_type on a different day must be a new valid sample."""
    site = _create_site(client)
    _create_sample(client, site["id"])  # 20240601
    r = client.post(
        "/api/samples",
        json={**SAMPLE_PAYLOAD, "site_id": site["id"], "sampling_date": "20240715"},
    )
    assert r.status_code == 201
    assert r.json()["sample_code"] == "NOBGOPark_water"  # same code, different row


def test_create_invalid_sampling_date_rejected(client: TestClient) -> None:
    site = _create_site(client)
    p = {**SAMPLE_PAYLOAD, "site_id": site["id"], "sampling_date": "2024-06-01"}
    r = client.post("/api/samples", json=p)
    assert r.status_code == 422


def test_create_with_optional_fields(client: TestClient) -> None:
    site = _create_site(client)
    p = {
        **SAMPLE_PAYLOAD,
        "site_id": site["id"],
        "depth": "5m",
        "elevation": "200m",
        "partner_sample_code": "EXT-001",
        "comments": "Nice day",
    }
    data = _create_sample(client, site["id"], payload={**SAMPLE_PAYLOAD, **p})
    assert data["depth"] == "5m"
    assert data["comments"] == "Nice day"


# ── get ───────────────────────────────────────────────────────────────────────


def test_get_by_id(client: TestClient) -> None:
    site = _create_site(client)
    sample = _create_sample(client, site["id"])
    r = client.get(f"/api/samples/{sample['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == sample["id"]


def test_get_nonexistent_returns_404(client: TestClient) -> None:
    assert client.get("/api/samples/does-not-exist").status_code == 404


# ── update ────────────────────────────────────────────────────────────────────


def test_update_field(client: TestClient) -> None:
    site = _create_site(client)
    sample = _create_sample(client, site["id"])
    r = client.patch(f"/api/samples/{sample['id']}", json={"depth": "20m"})
    assert r.status_code == 200
    assert r.json()["depth"] == "20m"
    assert r.json()["sample_code"] == "NOBGOPark_water"  # unchanged


def test_update_sample_type_recalculates_code(client: TestClient) -> None:
    site = _create_site(client)
    sample = _create_sample(client, site["id"])
    r = client.patch(f"/api/samples/{sample['id']}", json={"sample_type": "sediment"})
    assert r.status_code == 200
    assert r.json()["sample_code"] == "NOBGOPark_sediment"


def test_update_nonexistent_returns_404(client: TestClient) -> None:
    r = client.patch("/api/samples/does-not-exist", json={"depth": "1m"})
    assert r.status_code == 404


def test_update_to_conflicting_code_and_date_rejected(client: TestClient) -> None:
    site = _create_site(client)
    s1 = _create_sample(client, site["id"])  # NOBGOPark_water / 20240601
    _create_sample(
        client,
        site["id"],
        {"sample_type": "sediment", "sampling_date": "20240601"},
    )  # NOBGOPark_sediment / 20240601
    # Try to rename s1 to clash with s2 (same code + same date)
    r = client.patch(f"/api/samples/{s1['id']}", json={"sample_type": "sediment"})
    assert r.status_code == 409


# ── delete ────────────────────────────────────────────────────────────────────


def test_delete_soft_deletes(client: TestClient) -> None:
    site = _create_site(client)
    sample = _create_sample(client, site["id"])
    r = client.delete(f"/api/samples/{sample['id']}")
    assert r.status_code == 204
    assert client.get(f"/api/samples/{sample['id']}").status_code == 404


def test_delete_nonexistent_returns_404(client: TestClient) -> None:
    assert client.delete("/api/samples/does-not-exist").status_code == 404
