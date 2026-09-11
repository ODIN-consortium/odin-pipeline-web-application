"""device_name resolution — one chain, shared by the API and by row stamping.

Before these tests, ``device_name`` had two independent resolution ladders:

  * ``utils.get_device_name(db)``  — DB → ODIN_DEVICE_NAME → socket.gethostname()
  * the settings API / export      — env → DB → default, but ``device_name`` was not
                                     registered as an env-var key, so the env var was
                                     ignored entirely

Two consequences, both covered below: setting ODIN_DEVICE_NAME stamped rows with the
env value while the Settings page kept showing the container ID, and a DB value beat
the env var — the inverse of every other setting and of the documented 12-factor
priority in ``settings_resolver``.
"""

import socket
import sqlite3

from fastapi.testclient import TestClient

from backend.app.settings_resolver import get_device_name

# ``conftest`` blanks ODIN_DEVICE_NAME before the backend is imported, so the
# "nothing configured" cases here do not depend on whether whoever runs the suite
# has a device name in their own .env. Tests that want one set it via monkeypatch.


def _site_payload(site: str) -> dict:
    """A valid site, minus ``created_by`` — the point is what the server stamps."""
    return {
        "country": "Norway",
        "country_code": "NO",
        "city_code": "BGO",
        "city": "Bergen",
        "site": site,
    }


def _set_db_value(db: sqlite3.Connection, value: str) -> None:
    db.execute(
        "INSERT INTO config_values (id, key, value, updated_at) VALUES (?, 'device_name', ?, ?)",
        ("dn-test", value, "2026-07-29T00:00:00.000Z"),
    )
    db.commit()


# ── The bug: the two consumers must never disagree ────────────────────────────


def test_api_and_row_stamping_agree_when_env_var_is_set(
    client: TestClient, db: sqlite3.Connection, monkeypatch
) -> None:
    """The regression that motivated this file.

    With ODIN_DEVICE_NAME set, rows were stamped with it while GET /api/settings
    reported the container ID, so the UI contradicted what was written to the data.
    """
    monkeypatch.setenv("ODIN_DEVICE_NAME", "field-laptop-01")

    api_value = client.get("/api/settings/device_name").json()["value"]
    stamped_value = get_device_name(db)

    assert stamped_value == "field-laptop-01"
    assert api_value == stamped_value


def test_api_and_row_stamping_agree_when_nothing_is_set(
    client: TestClient, db: sqlite3.Connection
) -> None:
    api_value = client.get("/api/settings/device_name").json()["value"]
    assert get_device_name(db) is None
    assert api_value is None


def test_api_and_row_stamping_agree_on_a_db_value(
    client: TestClient, db: sqlite3.Connection
) -> None:
    _set_db_value(db, "bench-pc")
    api_value = client.get("/api/settings/device_name").json()["value"]
    assert get_device_name(db) == "bench-pc"
    assert api_value == "bench-pc"


# ── Priority: env wins, as it does for every other setting ────────────────────


def test_env_var_wins_over_db_value(db: sqlite3.Connection, monkeypatch) -> None:
    """Previously the DB won, making device_name the one setting .env could not override."""
    _set_db_value(db, "stale-name-typed-in-the-ui")
    monkeypatch.setenv("ODIN_DEVICE_NAME", "authoritative-from-env")
    assert get_device_name(db) == "authoritative-from-env"


def test_blank_env_var_is_ignored(db: sqlite3.Connection, monkeypatch) -> None:
    _set_db_value(db, "bench-pc")
    monkeypatch.setenv("ODIN_DEVICE_NAME", "   ")
    assert get_device_name(db) == "bench-pc"


# ── No hostname fallback ──────────────────────────────────────────────────────


def test_unset_does_not_fall_back_to_hostname(db: sqlite3.Connection, monkeypatch) -> None:
    """Under Docker the hostname is the container ID, which changes on every recreation.

    Auto-filling it would attribute one machine's rows to a series of phantom devices,
    which is the opposite of what sync needs this field for.
    """
    monkeypatch.delenv("ODIN_DEVICE_NAME", raising=False)
    assert get_device_name(db) is None
    assert get_device_name(db) != socket.gethostname()


def test_created_by_is_null_when_no_device_name_is_configured(
    client: TestClient, db: sqlite3.Connection, monkeypatch
) -> None:
    """The stamp must be NULL rather than a guessed name — sync already tolerates NULL."""
    monkeypatch.delenv("ODIN_DEVICE_NAME", raising=False)
    r = client.post("/api/sites", json=_site_payload("NullStamp"))
    assert r.status_code == 201, r.text
    row = db.execute(
        "SELECT created_by FROM sites WHERE id = ?", (r.json()["id"],)
    ).fetchone()
    assert row["created_by"] is None


def test_created_by_uses_the_env_var_when_set(
    client: TestClient, db: sqlite3.Connection, monkeypatch
) -> None:
    monkeypatch.setenv("ODIN_DEVICE_NAME", "field-laptop-01")
    r = client.post("/api/sites", json=_site_payload("EnvStamp"))
    assert r.status_code == 201, r.text
    row = db.execute(
        "SELECT created_by FROM sites WHERE id = ?", (r.json()["id"],)
    ).fetchone()
    assert row["created_by"] == "field-laptop-01"


# ── Config health must not treat a device name as a file path ─────────────────


def _issues_for(client: TestClient, key: str) -> list[dict]:
    r = client.get("/api/settings/health")
    assert r.status_code == 200
    return [i for i in r.json()["issues"] if i["key"] == key]


def test_health_does_not_report_a_set_device_name_as_a_missing_file(
    client: TestClient, monkeypatch
) -> None:
    """Regression: marking device_name `required` sent it down the file-existence branch.

    get_settings_health only ever saw path settings before, because every definition
    that passed its guard had a default_suffix or default_file_suffix. device_name is
    the first required non-path setting, so a perfectly good value like "BGO-2009" was
    reported as "not_found" — surfacing in the UI as "File not found on disk: BGO-2009",
    both against the field and in the global "Configuration issues detected" banner.
    """
    monkeypatch.setenv("ODIN_DEVICE_NAME", "BGO-2009")
    assert _issues_for(client, "device_name") == []


def test_health_reports_a_missing_device_name(client: TestClient, monkeypatch) -> None:
    """Unset is still worth reporting — just as "missing", not as an absent file."""
    monkeypatch.delenv("ODIN_DEVICE_NAME", raising=False)
    issues = _issues_for(client, "device_name")
    assert len(issues) == 1
    assert issues[0]["issue"] == "missing"
    assert issues[0]["value"] is None


def test_health_still_checks_real_path_settings(client: TestClient, monkeypatch) -> None:
    """Guard against the fix over-reaching: path settings must still be verified."""
    monkeypatch.setenv("ODIN_PATHOGENS_FILE", "/definitely/not/a/real/path.xlsx")
    issues = _issues_for(client, "pathogens_file")
    assert len(issues) == 1
    assert issues[0]["issue"] in ("warning", "not_found")


# ── Sync export filename, which does string work on the value ─────────────────


def test_sync_export_filename_when_device_name_is_unset(
    client: TestClient, monkeypatch
) -> None:
    """The slug does .lower() on the value, so None must not reach it."""
    monkeypatch.delenv("ODIN_DEVICE_NAME", raising=False)
    r = client.get("/api/sync/export")
    assert r.status_code == 200
    assert "unnamed-device" in r.headers["content-disposition"]


def test_sync_export_filename_slugifies_the_device_name(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setenv("ODIN_DEVICE_NAME", "Alice / Kenya-Laptop")
    r = client.get("/api/sync/export")
    assert r.status_code == 200
    assert "odin-alice-kenya-laptop-" in r.headers["content-disposition"]


def test_sync_export_records_exported_by(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("ODIN_DEVICE_NAME", "field-laptop-01")
    r = client.get("/api/sync/export")
    assert r.status_code == 200
    assert r.json()["exported_by"] == "field-laptop-01"


def test_export_without_a_device_name_can_be_imported_again(
    client: TestClient, monkeypatch
) -> None:
    """ODIN must never produce a sync file it then refuses to read.

    Removing the hostname fallback made ``exported_by`` null, and ``SyncImport`` declared
    it as a required ``str`` — so export succeeded and importing the result failed 422.
    Sixteen existing sync tests caught it; this pins the round trip directly.
    """
    monkeypatch.delenv("ODIN_DEVICE_NAME", raising=False)
    exported = client.get("/api/sync/export").json()
    assert exported["exported_by"] is None

    # /preview takes the envelope directly; /apply wraps it in import_data.
    assert client.post("/api/sync/preview", json=exported).status_code == 200
    r = client.post("/api/sync/apply", json={"import_data": exported, "decisions": {}})
    assert r.status_code == 200, r.text
