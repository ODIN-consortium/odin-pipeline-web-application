"""Tests for settings API endpoints not covered by test_settings.py.

Covers: /definitions, /health, /file/export, /file/import.
"""

import io
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ── definitions ───────────────────────────────────────────────────────────────


def test_definitions_returns_200(client: TestClient) -> None:
    r = client.get("/api/settings/definitions")
    assert r.status_code == 200


def test_definitions_returns_list(client: TestClient) -> None:
    r = client.get("/api/settings/definitions")
    assert isinstance(r.json(), list)
    assert len(r.json()) > 0


def test_definitions_contains_expected_keys(client: TestClient) -> None:
    keys = {d["key"] for d in client.get("/api/settings/definitions").json()}
    assert "minknow_dir" in keys
    assert "output_dir" in keys
    assert "nextflow_profile" in keys
    assert "device_name" in keys
    assert "enlighten_data_path" in keys


def test_definitions_each_entry_has_key_and_label(client: TestClient) -> None:
    for entry in client.get("/api/settings/definitions").json():
        assert "key" in entry
        assert "label" in entry


# ── health ────────────────────────────────────────────────────────────────────


def test_health_always_returns_200(client: TestClient) -> None:
    r = client.get("/api/settings/health")
    assert r.status_code == 200


def test_health_returns_issues_list(client: TestClient) -> None:
    r = client.get("/api/settings/health")
    assert "issues" in r.json()
    assert isinstance(r.json()["issues"], list)


def test_health_silent_for_missing_databases_file_default(client: TestClient) -> None:
    # databases_file is optional with fallbacks (Databases page, auto-discovery):
    # when only the computed default location is missing, that is a normal state.
    issues = {i["key"]: i for i in client.get("/api/settings/health").json()["issues"]}
    assert "databases_file" not in issues


def test_health_reports_not_found_for_explicit_databases_file(client: TestClient) -> None:
    # An explicitly configured path that does not exist IS an issue.
    client.put("/api/settings/databases_file", json={"value": "/nonexistent/databases.csv"})
    issues = {i["key"]: i for i in client.get("/api/settings/health").json()["issues"]}
    assert "databases_file" in issues
    assert issues["databases_file"]["issue"] == "not_found"
    client.put("/api/settings/databases_file", json={"value": None})


def test_health_silent_for_missing_extract_targets_file_default(client: TestClient) -> None:
    issues = {i["key"]: i for i in client.get("/api/settings/health").json()["issues"]}
    assert "extract_targets_file" not in issues


def test_health_reports_warning_for_missing_pathogens_file(client: TestClient) -> None:
    issues = {i["key"]: i for i in client.get("/api/settings/health").json()["issues"]}
    assert "pathogens_file" in issues
    assert issues["pathogens_file"]["issue"] == "warning"


def test_health_does_not_report_nextflow_config_file_when_it_exists(client: TestClient) -> None:
    # conftest creates {ODIN_PIPELINE_ROOT}/config/odin.config, so no issue expected
    issues = {i["key"]: i for i in client.get("/api/settings/health").json()["issues"]}
    assert "nextflow_config_file" not in issues


def test_health_reports_missing_for_required_key_without_root(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without ODIN_PIPELINE_ROOT, enlighten_data_path has no default and is required
    monkeypatch.delenv("ODIN_PIPELINE_ROOT", raising=False)
    r = client.get("/api/settings/health")
    issues = {i["key"]: i for i in r.json()["issues"]}
    assert "enlighten_data_path" in issues
    assert issues["enlighten_data_path"]["issue"] == "missing"


def test_health_reports_create_failed_when_makedirs_raises(
    client: TestClient,
) -> None:
    # Patch both exists (so the health check thinks dirs are absent) and makedirs
    # (so creation fails), forcing the create_failed branch.
    with (
        patch("backend.app.api.settings.os.path.exists", return_value=False),
        patch("backend.app.api.settings.os.makedirs", side_effect=OSError("permission denied")),
    ):
        r = client.get("/api/settings/health")
    issues = {i["key"]: i for i in r.json()["issues"]}
    dir_keys = {"minknow_dir", "output_dir", "biomeme_dir", "enlighten_data_path"}
    reported = dir_keys & issues.keys()
    assert reported, "Expected at least one directory key to report create_failed"
    for key in reported:
        assert issues[key]["issue"] == "create_failed"


# ── export ────────────────────────────────────────────────────────────────────


def test_export_returns_200(client: TestClient) -> None:
    r = client.get("/api/settings/file/export")
    assert r.status_code == 200


def test_export_response_is_attachment(client: TestClient) -> None:
    r = client.get("/api/settings/file/export")
    assert "attachment" in r.headers.get("content-disposition", "")


def test_export_body_has_correct_format_field(client: TestClient) -> None:
    body = client.get("/api/settings/file/export").json()
    assert body["format"] == "odin-settings-v1"


def test_export_body_has_settings_list(client: TestClient) -> None:
    body = client.get("/api/settings/file/export").json()
    assert "settings" in body
    assert isinstance(body["settings"], list)
    assert len(body["settings"]) > 0


def test_export_db_stored_key_has_source_db(client: TestClient) -> None:
    client.put("/api/settings/output_dir", json={"value": "/opt/odin/output"})
    settings = {s["key"]: s for s in client.get("/api/settings/file/export").json()["settings"]}
    assert settings["output_dir"]["source"] == "db"


def test_export_env_var_key_has_source_env(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTFLOW_CONFIG_FILE", "/some/path/odin.config")
    settings = {s["key"]: s for s in client.get("/api/settings/file/export").json()["settings"]}
    assert settings["nextflow_config_file"]["source"] == "env"


def test_export_unset_key_has_source_default(client: TestClient) -> None:
    # nextflow_profile has no suffix and is never stored — source must be "default"
    settings = {s["key"]: s for s in client.get("/api/settings/file/export").json()["settings"]}
    assert settings["nextflow_profile"]["source"] == "default"


# ── import ────────────────────────────────────────────────────────────────────


def _upload(client: TestClient, payload: object, filename: str = "settings.json") -> object:
    content = json.dumps(payload).encode()
    return client.post(
        "/api/settings/file/import",
        files={"file": (filename, io.BytesIO(content), "application/json")},
    )


def test_import_v1_format_imports_known_key(client: TestClient) -> None:
    payload = {
        "format": "odin-settings-v1",
        "settings": [{"key": "minknow_dir", "value": "/mnt/data/minknow"}],
    }
    r = _upload(client, payload)
    assert r.status_code == 200
    assert r.json()["summary"]["imported"] == 1


def test_import_plain_dict_format_imports_known_key(client: TestClient) -> None:
    r = _upload(client, {"minknow_dir": "/mnt/data/minknow"})
    assert r.status_code == 200
    assert r.json()["summary"]["imported"] == 1


def test_import_then_get_returns_stored_value(client: TestClient) -> None:
    _upload(client, {"biomeme_dir": "/mnt/data/biomeme"})
    r = client.get("/api/settings/biomeme_dir")
    assert r.status_code == 200
    assert r.json()["value"] is not None
    assert "biomeme" in r.json()["value"]


def test_import_unknown_key_is_skipped(client: TestClient) -> None:
    r = _upload(client, {"totally_unknown_key_xyz": "/some/path"})
    assert r.status_code == 200
    summary = r.json()["summary"]
    assert summary["skipped_unknown"] == 1
    assert summary["imported"] == 0


def test_import_env_var_key_is_skipped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTFLOW_CONFIG_FILE", "/env/path/odin.config")
    r = _upload(client, {"nextflow_config_file": "/other/path/odin.config"})
    assert r.status_code == 200
    summary = r.json()["summary"]
    assert summary["skipped_env"] == 1


def test_import_non_json_filename_returns_400(client: TestClient) -> None:
    content = json.dumps({"minknow_dir": "/mnt/data"}).encode()
    r = client.post(
        "/api/settings/file/import",
        files={"file": ("settings.txt", io.BytesIO(content), "text/plain")},
    )
    assert r.status_code == 400


def test_import_non_json_body_returns_400(client: TestClient) -> None:
    r = client.post(
        "/api/settings/file/import",
        files={"file": ("settings.json", io.BytesIO(b"not json at all!!!"), "application/json")},
    )
    assert r.status_code == 400


def test_import_json_array_returns_400(client: TestClient) -> None:
    r = _upload(client, [{"key": "minknow_dir", "value": "/mnt/data"}])
    assert r.status_code == 400


def test_import_file_over_10mb_returns_413(client: TestClient) -> None:
    # Build a payload that exceeds 10 MB when JSON-serialised
    large_value = "x" * (10 * 1024 * 1024 + 1)
    oversized = json.dumps({"minknow_dir": large_value}).encode()
    r = client.post(
        "/api/settings/file/import",
        files={"file": ("settings.json", io.BytesIO(oversized), "application/json")},
    )
    assert r.status_code == 413


# ── source exposure (defaults as placeholders) ─────────────────────────────────


def test_get_all_marks_computed_defaults_with_source_default(client: TestClient) -> None:
    values = {v["key"]: v for v in client.get("/api/settings").json()}
    v = values["databases_file"]
    assert v["source"] == "default"
    assert v["value"].endswith("/input_sheets/databases.csv")


def test_get_all_stored_value_has_no_source(client: TestClient) -> None:
    client.put("/api/settings/minknow_dir", json={"value": "/data/minknow"})
    values = {v["key"]: v for v in client.get("/api/settings").json()}
    assert values["minknow_dir"]["source"] is None
    client.put("/api/settings/minknow_dir", json={"value": None})


def test_cleared_value_resolves_as_default_again(client: TestClient) -> None:
    """Clearing a setting must render exactly like never-set (placeholder)."""
    client.put("/api/settings/minknow_dir", json={"value": "/data/minknow"})
    client.put("/api/settings/minknow_dir", json={"value": None})
    values = {v["key"]: v for v in client.get("/api/settings").json()}
    v = values["minknow_dir"]
    assert v["source"] == "default"
    assert v["value"].endswith("/minknow")
