"""Tests for /api/autocomplete/* endpoints."""

from fastapi.testclient import TestClient

_SITE_NO = {
    "country": "Norway",
    "country_code": "NO",
    "city_code": "BGO",
    "city": "Bergen",
    "site": "River",
}

_SITE_SE = {
    "country": "Sweden",
    "country_code": "SE",
    "city_code": "STO",
    "city": "Stockholm",
    "site": "Lake",
}


def _site(client: TestClient, payload: dict | None = None) -> dict:
    r = client.post("/api/sites", json=payload or _SITE_NO)
    assert r.status_code == 201, r.text
    return r.json()


def _sample(client: TestClient, site_id: str, **kw) -> dict:
    r = client.post("/api/samples", json={
        "site_id": site_id, "sampling_date": "20240601", "sample_type": "water", **kw,
    })
    assert r.status_code == 201, r.text
    return r.json()


def _nanopore_run(client: TestClient, *, run_accession: str, barcode: str = "barcode01", **kw) -> dict:
    r = client.post("/api/nanopore-runs", json={
        "run_accession": run_accession, "barcode": barcode, **kw,
    })
    assert r.status_code == 201, r.text
    return r.json()


# ── site-ids ──────────────────────────────────────────────────────────────────


def test_site_ids_empty_when_no_sites(client: TestClient) -> None:
    r = client.get("/api/autocomplete/site-ids")
    assert r.status_code == 200
    assert r.json() == []


def test_site_ids_returns_site_codes(client: TestClient) -> None:
    _site(client, _SITE_NO)
    _site(client, _SITE_SE)
    r = client.get("/api/autocomplete/site-ids")
    assert r.status_code == 200
    codes = set(r.json())
    assert "NOBGORiver" in codes
    assert "SESTOLake" in codes


def test_site_ids_response_is_list(client: TestClient) -> None:
    _site(client)
    r = client.get("/api/autocomplete/site-ids")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ── country-codes ─────────────────────────────────────────────────────────────


def test_country_codes_includes_pycountry_entries(client: TestClient) -> None:
    r = client.get("/api/autocomplete/country-codes")
    assert r.status_code == 200
    codes = {e["code"] for e in r.json()}
    assert "NO" in codes
    assert "SE" in codes
    assert "DE" in codes


def test_country_codes_db_country_name_overrides_pycountry(client: TestClient) -> None:
    _site(client, {**_SITE_NO, "country": "Norwegen"})
    r = client.get("/api/autocomplete/country-codes")
    no_entry = next((e for e in r.json() if e["code"] == "NO"), None)
    assert no_entry is not None
    assert no_entry["description"] == "Norwegen"


def test_country_codes_sorted_by_description(client: TestClient) -> None:
    r = client.get("/api/autocomplete/country-codes")
    descriptions = [e["description"] for e in r.json()]
    assert descriptions == sorted(descriptions)


def test_country_codes_each_entry_has_code_and_description(client: TestClient) -> None:
    r = client.get("/api/autocomplete/country-codes")
    assert r.status_code == 200
    first = r.json()[0]
    assert "code" in first
    assert "description" in first


# ── city-codes ────────────────────────────────────────────────────────────────


def test_city_codes_empty_when_no_city_code_set(client: TestClient) -> None:
    _site(client, {"country": "Norway", "country_code": "NO", "site": "X"})
    r = client.get("/api/autocomplete/city-codes")
    assert r.status_code == 200
    assert r.json() == []


def test_city_codes_returns_code_and_city_name(client: TestClient) -> None:
    _site(client, _SITE_NO)
    r = client.get("/api/autocomplete/city-codes")
    assert r.status_code == 200
    data = r.json()
    codes = {e["code"] for e in data}
    assert "BGO" in codes
    entry = next(e for e in data if e["code"] == "BGO")
    assert entry["description"] == "Bergen"


def test_city_codes_sorted_by_description(client: TestClient) -> None:
    _site(client, _SITE_NO)
    _site(client, _SITE_SE)
    r = client.get("/api/autocomplete/city-codes")
    descriptions = [e["description"] for e in r.json()]
    assert descriptions == sorted(descriptions)


# ── protocol-ids ──────────────────────────────────────────────────────────────


def test_protocol_ids_empty_when_no_runs(client: TestClient) -> None:
    r = client.get("/api/autocomplete/protocol-ids")
    assert r.status_code == 200
    assert r.json() == []


def test_protocol_ids_returns_value(client: TestClient) -> None:
    _nanopore_run(client, run_accession="run001", protocol_id="SQK-LSK114")
    r = client.get("/api/autocomplete/protocol-ids")
    assert r.status_code == 200
    assert "SQK-LSK114" in r.json()


def test_protocol_ids_are_distinct(client: TestClient) -> None:
    _nanopore_run(client, run_accession="run001", barcode="barcode01", protocol_id="SQK-LSK114")
    _nanopore_run(client, run_accession="run002", barcode="barcode01", protocol_id="SQK-LSK114")
    r = client.get("/api/autocomplete/protocol-ids")
    assert r.json().count("SQK-LSK114") == 1


def test_protocol_ids_null_values_excluded(client: TestClient) -> None:
    _nanopore_run(client, run_accession="run001")  # no protocol_id
    r = client.get("/api/autocomplete/protocol-ids")
    assert r.json() == []


# ── sequencing-kit-ids ────────────────────────────────────────────────────────


def test_sequencing_kit_ids_empty_when_no_runs(client: TestClient) -> None:
    r = client.get("/api/autocomplete/sequencing-kit-ids")
    assert r.status_code == 200
    assert r.json() == []


def test_sequencing_kit_ids_returns_value(client: TestClient) -> None:
    _nanopore_run(client, run_accession="run001", sequencing_kit_id="SQK-LSK114")
    r = client.get("/api/autocomplete/sequencing-kit-ids")
    assert r.status_code == 200
    assert "SQK-LSK114" in r.json()


def test_sequencing_kit_ids_are_distinct(client: TestClient) -> None:
    _nanopore_run(client, run_accession="run001", barcode="barcode01", sequencing_kit_id="SQK-LSK114")
    _nanopore_run(client, run_accession="run002", barcode="barcode01", sequencing_kit_id="SQK-LSK114")
    r = client.get("/api/autocomplete/sequencing-kit-ids")
    assert r.json().count("SQK-LSK114") == 1


def test_sequencing_kit_ids_null_values_excluded(client: TestClient) -> None:
    _nanopore_run(client, run_accession="run001")  # no sequencing_kit_id
    r = client.get("/api/autocomplete/sequencing-kit-ids")
    assert r.json() == []


# ── last-run-defaults ─────────────────────────────────────────────────────────


def test_last_run_defaults_empty_dict_when_no_runs(client: TestClient) -> None:
    r = client.get("/api/autocomplete/last-run-defaults")
    assert r.status_code == 200
    assert r.json() == {}


def test_last_run_defaults_returns_protocol_and_kit(client: TestClient) -> None:
    _nanopore_run(
        client,
        run_accession="run001",
        protocol_id="SQK-LSK114",
        sequencing_kit_id="SQK-LSK114",
    )
    r = client.get("/api/autocomplete/last-run-defaults")
    assert r.status_code == 200
    data = r.json()
    assert data["protocol_id"] == "SQK-LSK114"
    assert data["sequencing_kit_id"] == "SQK-LSK114"


def test_last_run_defaults_includes_sample_code(client: TestClient) -> None:
    site = _site(client)
    sample = _sample(client, site["id"])
    _nanopore_run(
        client,
        run_accession="run001",
        sample_id=sample["id"],
        protocol_id="SQK-LSK114",
        sequencing_kit_id="SQK-LSK114",
    )
    r = client.get("/api/autocomplete/last-run-defaults")
    assert r.status_code == 200
    assert r.json()["sample_code"] == sample["sample_code"]


def test_last_run_defaults_returns_most_recent_run(client: TestClient) -> None:
    _nanopore_run(client, run_accession="run001", barcode="barcode01", protocol_id="SQK-LSK114")
    _nanopore_run(client, run_accession="run002", barcode="barcode01", protocol_id="SQK-LSK114", sequencing_kit_id="SQK-LSK114")
    r = client.get("/api/autocomplete/last-run-defaults")
    data = r.json()
    # The most recent run has both protocol and kit set
    assert data["sequencing_kit_id"] == "SQK-LSK114"
