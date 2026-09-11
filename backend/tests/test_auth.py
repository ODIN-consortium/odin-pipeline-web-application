"""Tests for the optional HTTP Basic Auth middleware in backend/app/main.py.

The middleware reads ODIN_AUTH_USER / ODIN_AUTH_PASSWORD as module-level globals
at request time, so tests toggle it by monkeypatching those globals.
"""

import base64


def _basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_no_auth_required_when_unset(client):
    # Default test env leaves ODIN_AUTH_* unset — the middleware is a no-op.
    assert client.get("/api/sites").status_code == 200


def test_unauthenticated_request_rejected_when_enabled(client, monkeypatch):
    monkeypatch.setattr("backend.app.main._AUTH_USER", "odin")
    monkeypatch.setattr("backend.app.main._AUTH_PASSWORD", "secret")
    resp = client.get("/api/sites")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate", "").startswith("Basic")


def test_correct_credentials_accepted(client, monkeypatch):
    monkeypatch.setattr("backend.app.main._AUTH_USER", "odin")
    monkeypatch.setattr("backend.app.main._AUTH_PASSWORD", "secret")
    assert client.get("/api/sites", headers=_basic("odin", "secret")).status_code == 200


def test_wrong_credentials_rejected(client, monkeypatch):
    monkeypatch.setattr("backend.app.main._AUTH_USER", "odin")
    monkeypatch.setattr("backend.app.main._AUTH_PASSWORD", "secret")
    assert client.get("/api/sites", headers=_basic("odin", "wrong")).status_code == 401


def test_options_preflight_not_blocked(client, monkeypatch):
    # CORS preflight carries no credentials; the middleware must let it through.
    monkeypatch.setattr("backend.app.main._AUTH_USER", "odin")
    monkeypatch.setattr("backend.app.main._AUTH_PASSWORD", "secret")
    resp = client.options(
        "/api/sites",
        headers={"Origin": "http://localhost:4200", "Access-Control-Request-Method": "GET"},
    )
    assert resp.status_code != 401
