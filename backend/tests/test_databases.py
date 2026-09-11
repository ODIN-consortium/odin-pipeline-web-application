"""Tests for /api/databases — CRUD, soft-delete, path normalisation, uniqueness."""

from fastapi.testclient import TestClient

# ── helpers ───────────────────────────────────────────────────────────────────

_BASE = {
    "tool": "kraken2",
    "db_name": "PlusPF-8",
    "db_path": "/opt/databases/kraken2/PlusPF-8",
}


def _create(client: TestClient, overrides: dict | None = None) -> dict:
    payload = {**_BASE, **(overrides or {})}
    r = client.post("/api/databases", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# ── list ──────────────────────────────────────────────────────────────────────


def test_list_empty(client: TestClient) -> None:
    r = client.get("/api/databases")
    assert r.status_code == 200
    assert r.json() == []


def test_list_returns_created_entries(client: TestClient) -> None:
    _create(client)
    _create(client, {"tool": "diamond", "db_name": "NCBI-nr"})
    entries = client.get("/api/databases").json()
    assert len(entries) == 2


def test_list_excludes_soft_deleted(client: TestClient) -> None:
    entry = _create(client)
    client.delete(f"/api/databases/{entry['id']}")
    assert client.get("/api/databases").json() == []


# ── get ───────────────────────────────────────────────────────────────────────


def test_get_existing(client: TestClient) -> None:
    entry = _create(client)
    r = client.get(f"/api/databases/{entry['id']}")
    assert r.status_code == 200
    assert r.json()["tool"] == "kraken2"


def test_get_not_found(client: TestClient) -> None:
    r = client.get("/api/databases/does-not-exist")
    assert r.status_code == 404


def test_get_not_found_after_soft_delete(client: TestClient) -> None:
    entry = _create(client)
    client.delete(f"/api/databases/{entry['id']}")
    assert client.get(f"/api/databases/{entry['id']}").status_code == 404


# ── create ────────────────────────────────────────────────────────────────────


def test_create_returns_correct_fields(client: TestClient) -> None:
    r = client.post("/api/databases", json=_BASE)
    assert r.status_code == 201
    body = r.json()
    assert body["tool"] == "kraken2"
    assert body["db_name"] == "PlusPF-8"
    assert "id" in body
    assert "created_at" in body


def test_create_stores_db_params(client: TestClient) -> None:
    payload = {**_BASE, "db_params": "--quick"}
    entry = client.post("/api/databases", json=payload).json()
    assert entry["db_params"] == "--quick"


def test_create_duplicate_tool_db_name_returns_409(client: TestClient) -> None:
    _create(client)
    r = client.post("/api/databases", json=_BASE)
    assert r.status_code == 409


def test_create_same_tool_different_db_name_ok(client: TestClient) -> None:
    _create(client)
    r = client.post("/api/databases", json={**_BASE, "db_name": "PlusPFP-8"})
    assert r.status_code == 201


# ── update ────────────────────────────────────────────────────────────────────


def test_update_tool_name(client: TestClient) -> None:
    entry = _create(client)
    r = client.put(f"/api/databases/{entry['id']}", json={"db_name": "PlusPFP-16"})
    assert r.status_code == 200
    assert r.json()["db_name"] == "PlusPFP-16"


def test_update_preserves_unmentioned_fields(client: TestClient) -> None:
    entry = _create(client, {"db_params": "--quick"})
    client.put(f"/api/databases/{entry['id']}", json={"db_name": "updated"})
    updated = client.get(f"/api/databases/{entry['id']}").json()
    assert updated["db_params"] == "--quick"
    assert updated["tool"] == "kraken2"


def test_update_not_found(client: TestClient) -> None:
    r = client.put("/api/databases/no-such-id", json={"db_name": "x"})
    assert r.status_code == 404


# ── delete ────────────────────────────────────────────────────────────────────


def test_delete_returns_204(client: TestClient) -> None:
    entry = _create(client)
    r = client.delete(f"/api/databases/{entry['id']}")
    assert r.status_code == 204


def test_delete_removes_row(client: TestClient, db) -> None:
    """Row is hard-deleted from the DB."""
    entry = _create(client)
    client.delete(f"/api/databases/{entry['id']}")
    row = db.execute(
        "SELECT id FROM databases WHERE id = ?", (entry["id"],)
    ).fetchone()
    assert row is None


def test_delete_not_found(client: TestClient) -> None:
    r = client.delete("/api/databases/no-such-id")
    assert r.status_code == 404


def test_delete_already_deleted_returns_404(client: TestClient) -> None:
    entry = _create(client)
    client.delete(f"/api/databases/{entry['id']}")
    r = client.delete(f"/api/databases/{entry['id']}")
    assert r.status_code == 404


# ── effective (source fallback) ───────────────────────────────────────────────


def test_effective_empty_table_no_autodiscovery_returns_200(client: TestClient) -> None:
    """Regression: with no file, no entries and nothing to auto-discover, the
    endpoint must return an empty list — v1.0.6/7 crashed with a NameError here."""
    r = client.get("/api/databases/effective")
    assert r.status_code == 200
    body = r.json()
    assert body["entries"] == []


def test_effective_reports_autodiscovered_databases(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    """With an empty table, databases under ODIN_DATABASE_PATH are surfaced."""
    dbdir = tmp_path / "MY_DB"
    dbdir.mkdir()
    (dbdir / "hash.k2d").write_bytes(b"\x00")
    monkeypatch.setenv("ODIN_DATABASE_PATH", str(tmp_path))
    r = client.get("/api/databases/effective")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "autodiscover"
    assert [e["db_name"] for e in body["entries"]] == ["MY_DB"]


def test_effective_table_entries_win_over_autodiscovery(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    dbdir = tmp_path / "MY_DB"
    dbdir.mkdir()
    (dbdir / "hash.k2d").write_bytes(b"\x00")
    monkeypatch.setenv("ODIN_DATABASE_PATH", str(tmp_path))
    _create(client)
    r = client.get("/api/databases/effective")
    assert r.status_code == 200
    assert r.json()["source"] == "db"
