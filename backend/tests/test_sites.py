"""Tests for /api/sites — CRUD, site_code derivation, conflict detection."""

from fastapi.testclient import TestClient

# ── helpers ──────────────────────────────────────────────────────────────────

NORWAY_BERGEN = {
    "country": "Norway",
    "country_code": "NO",
    "city_code": "BGO",
    "site": "Park",
    "city": "Bergen",
    "created_by": "test",
}


def _create_site(client: TestClient, payload: dict | None = None) -> dict:
    payload = payload or NORWAY_BERGEN
    r = client.post("/api/sites", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# ── list ─────────────────────────────────────────────────────────────────────


def test_list_empty(client: TestClient) -> None:
    r = client.get("/api/sites")
    assert r.status_code == 200
    assert r.json() == []


def test_list_returns_created_site(client: TestClient) -> None:
    _create_site(client)
    r = client.get("/api/sites")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_list_filter_by_query(client: TestClient) -> None:
    _create_site(client)
    _create_site(
        client, {"country": "Sweden", "country_code": "SE", "city_code": "STO", "site": "Lake"}
    )
    r = client.get("/api/sites", params={"q": "NO"})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["site_code"] == "NOBGOPark"


# ── create ────────────────────────────────────────────────────────────────────


def test_create_derives_site_code(client: TestClient) -> None:
    data = _create_site(client)
    assert data["site_code"] == "NOBGOPark"


def test_create_returns_uuid_id(client: TestClient) -> None:
    data = _create_site(client)
    assert len(data["id"]) == 36  # UUID4 with dashes


def test_create_site_code_is_server_only(client: TestClient) -> None:
    """Clients cannot inject site_code — it must always be derived server-side."""
    payload = {**NORWAY_BERGEN, "site_code": "HACKED"}
    r = client.post("/api/sites", json=payload)
    # Pydantic ignores the extra field; site_code is still derived
    assert r.status_code == 201
    assert r.json()["site_code"] == "NOBGOPark"


def test_create_duplicate_site_code_rejected(client: TestClient) -> None:
    _create_site(client)
    r = client.post("/api/sites", json=NORWAY_BERGEN)
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


def test_create_without_derivable_parts_rejected(client: TestClient) -> None:
    """All three components missing → site_code cannot be derived."""
    r = client.post("/api/sites", json={"country": "Norway"})
    assert r.status_code == 422


def test_create_partial_components_uses_available(client: TestClient) -> None:
    """Derived site_code uses whatever components are provided."""
    r = client.post("/api/sites", json={"country": "Norway", "country_code": "NO", "site": "River"})
    assert r.status_code == 201
    assert r.json()["site_code"] == "NORiver"


def test_create_city_code_conflict_rejected(client: TestClient) -> None:
    """Same city_code mapped to a different city must be rejected."""
    _create_site(client)  # BGO → Bergen
    payload = {
        "country": "Norway",
        "country_code": "NO",
        "city_code": "BGO",  # same code, different city
        "site": "Pier",
        "city": "Oslo",
    }
    r = client.post("/api/sites", json=payload)
    assert r.status_code == 409
    assert "city" in r.json()["detail"].lower()


def test_create_country_code_conflict_rejected(client: TestClient) -> None:
    """Same country_code mapped to a different country must be rejected."""
    _create_site(client)  # NO → Norway
    payload = {
        "country": "Germany",
        "country_code": "NO",  # same code, different country
        "city_code": "BRL",
        "site": "Forest",
    }
    r = client.post("/api/sites", json=payload)
    assert r.status_code == 409
    assert "country" in r.json()["detail"].lower()


# ── get ───────────────────────────────────────────────────────────────────────


def test_get_by_id(client: TestClient) -> None:
    site = _create_site(client)
    r = client.get(f"/api/sites/{site['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == site["id"]


def test_get_nonexistent_returns_404(client: TestClient) -> None:
    r = client.get("/api/sites/does-not-exist")
    assert r.status_code == 404


# ── update ────────────────────────────────────────────────────────────────────


def test_update_field(client: TestClient) -> None:
    site = _create_site(client)
    r = client.patch(f"/api/sites/{site['id']}", json={"location": "Near the pond"})
    assert r.status_code == 200
    assert r.json()["location"] == "Near the pond"
    assert r.json()["site_code"] == "NOBGOPark"  # unchanged


def test_update_site_component_recalculates_site_code(client: TestClient) -> None:
    site = _create_site(client)
    r = client.patch(f"/api/sites/{site['id']}", json={"site": "Beach"})
    assert r.status_code == 200
    assert r.json()["site_code"] == "NOBGOBeach"


def test_update_nonexistent_returns_404(client: TestClient) -> None:
    r = client.patch("/api/sites/does-not-exist", json={"location": "X"})
    assert r.status_code == 404


def test_update_site_code_conflict_rejected(client: TestClient) -> None:
    """Renaming a site to a code already taken by another site must fail."""
    _create_site(client)  # site_a — exists to occupy the default site_code
    site_b = _create_site(
        client,
        {"country": "Norway", "country_code": "NO", "city_code": "BGO", "site": "Beach"},
    )
    # Try to rename site_b to match site_a
    r = client.patch(f"/api/sites/{site_b['id']}", json={"site": "Park"})
    assert r.status_code == 409


# ── delete ────────────────────────────────────────────────────────────────────


def test_delete_soft_deletes(client: TestClient) -> None:
    site = _create_site(client)
    r = client.delete(f"/api/sites/{site['id']}")
    assert r.status_code == 204
    # Subsequent GET returns 404
    assert client.get(f"/api/sites/{site['id']}").status_code == 404
    # List returns empty
    assert client.get("/api/sites").json() == []


def test_delete_nonexistent_returns_404(client: TestClient) -> None:
    r = client.delete("/api/sites/does-not-exist")
    assert r.status_code == 404
