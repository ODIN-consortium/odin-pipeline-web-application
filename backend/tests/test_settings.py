"""Tests for /api/settings — list, get, and upsert config values."""

import socket

from fastapi.testclient import TestClient

# ── list ──────────────────────────────────────────────────────────────────────


def test_list_returns_known_keys(client: TestClient) -> None:
    """GET / returns at least the known settings keys (from settings_definitions.json)."""
    r = client.get("/api/settings")
    assert r.status_code == 200
    # The fixture sets ODIN_PIPELINE_ROOT so defaults are populated
    keys = {item["key"] for item in r.json()}
    # These are always present in the definitions
    assert "minknow_dir" in keys
    assert "output_dir" in keys


def test_list_returns_stored_value(client: TestClient) -> None:
    client.put("/api/settings/output_dir", json={"value": "/opt/odin/output"})
    items = client.get("/api/settings").json()
    match = next((i for i in items if i["key"] == "output_dir"), None)
    assert match is not None
    # normalize_for_storage passes Linux paths through unchanged
    assert match["value"] == "/opt/odin/output"


# ── get single key ────────────────────────────────────────────────────────────


def test_get_key_with_no_default_returns_null_before_upsert(client: TestClient) -> None:
    """A known key with no computed default returns 200 with null value when not set."""
    # enlighten_url has no default_suffix / default_hostname in settings_definitions.json
    r = client.get("/api/settings/enlighten_url")
    assert r.status_code == 200
    assert r.json()["key"] == "enlighten_url"
    assert r.json()["value"] is None


def test_get_key_with_default_suffix_returns_computed_default(client: TestClient) -> None:
    """A known key with default_suffix returns the derived path even without a DB row."""
    # minknow_dir has default_suffix "/minknow" — fixture sets ODIN_PIPELINE_ROOT
    r = client.get("/api/settings/minknow_dir")
    assert r.status_code == 200
    assert r.json()["key"] == "minknow_dir"
    # Should be non-null (the computed path), matching the list endpoint behaviour
    assert r.json()["value"] is not None
    assert r.json()["value"].endswith("/minknow")


def test_get_device_name_returns_null_when_not_set(client: TestClient) -> None:
    """device_name has no default, so an unset value reads as null — not the hostname.

    This replaces a test that asserted the opposite. That test came from a real fix for a
    real inconsistency (the list endpoint showed the hostname fallback while the single-key
    endpoint returned null), but it fixed it in the direction that made the *list* endpoint
    authoritative, which silently disabled a safety feature: the sync page's
    "Device name not set" warning is driven by this endpoint's value being empty, so a
    hostname fallback meant the warning could never fire. Under Docker that hostname is the
    container ID, which changes on every recreation — so the field the sync feature uses to
    tell devices apart was being auto-filled with a value that does not identify a device.

    The inconsistency is still fixed (see the sibling test below); both endpoints now
    return null. Unset means unset.
    """
    r = client.get("/api/settings/device_name")
    assert r.status_code == 200
    assert r.json()["key"] == "device_name"
    assert r.json()["value"] is None
    assert r.json()["value"] != socket.gethostname()


def test_get_single_key_matches_list_endpoint(client: TestClient) -> None:
    """GET /api/settings/{key} and GET /api/settings return the same resolved value."""
    r_list = client.get("/api/settings")
    r_single = client.get("/api/settings/device_name")
    list_val = next((i["value"] for i in r_list.json() if i["key"] == "device_name"), None)
    assert r_single.json()["value"] == list_val


def test_get_unknown_key_returns_404(client: TestClient) -> None:
    """Requesting a key that is not in the settings definitions returns 404."""
    r = client.get("/api/settings/does_not_exist")
    assert r.status_code == 404


def test_get_key_returns_stored_value(client: TestClient) -> None:
    client.put("/api/settings/minknow_dir", json={"value": "/mnt/data/minknow"})
    r = client.get("/api/settings/minknow_dir")
    assert r.status_code == 200
    assert r.json()["key"] == "minknow_dir"
    assert r.json()["value"] == "/mnt/data/minknow"


# ── upsert ────────────────────────────────────────────────────────────────────


def test_upsert_creates_new_key(client: TestClient) -> None:
    r = client.put("/api/settings/output_dir", json={"value": "/opt/odin/output"})
    assert r.status_code == 200
    assert r.json()["key"] == "output_dir"
    assert r.json()["value"] == "/opt/odin/output"


def test_upsert_updates_existing_key(client: TestClient) -> None:
    client.put("/api/settings/output_dir", json={"value": "/opt/odin/v1"})
    r = client.put("/api/settings/output_dir", json={"value": "/opt/odin/v2"})
    assert r.status_code == 200
    assert r.json()["value"] == "/opt/odin/v2"
    # Confirm only one row via GET
    r2 = client.get("/api/settings/output_dir")
    assert r2.json()["value"] == "/opt/odin/v2"


def test_upsert_accepts_none_value(client: TestClient) -> None:
    """Setting a key to null is allowed — clears the value."""
    client.put("/api/settings/output_dir", json={"value": "/opt/odin"})
    r = client.put("/api/settings/output_dir", json={"value": None})
    assert r.status_code == 200
    assert r.json()["value"] is None


def test_upsert_normalizes_windows_path(client: TestClient) -> None:
    """Windows-style paths are stored in Git Bash / Unix format."""
    r = client.put("/api/settings/minknow_dir", json={"value": "D:\\MinKNOW\\data"})
    assert r.status_code == 200
    stored = r.json()["value"]
    # normalize_for_storage converts D:\... to /d/...
    assert stored.startswith("/d/") or stored.startswith("/D/")
