"""The replicate fields on the nanopore-runs API: stored, read back, and kept as a pair."""

from fastapi.testclient import TestClient

from backend.tests.test_nanopore_runs import RUN_PAYLOAD, _make_run, _make_sample, _make_site


def _sample(client: TestClient) -> str:
    return _make_sample(client, _make_site(client)["id"])["id"]


def test_create_stores_and_returns_the_pair(client: TestClient) -> None:
    created = _make_run(client, _sample(client), {
        **RUN_PAYLOAD, "replicate": 2, "replicate_type": "collection",
    })

    assert created["replicate"] == 2
    assert created["replicate_type"] == "collection"
    listed = client.get("/api/nanopore-runs").json()
    assert listed[0]["replicate"] == 2 and listed[0]["replicate_type"] == "collection"


def test_both_default_to_null(client: TestClient) -> None:
    created = _make_run(client, _sample(client))
    assert created["replicate"] is None and created["replicate_type"] is None


def test_zero_is_not_a_replicate_number(client: TestClient) -> None:
    r = client.post("/api/nanopore-runs", json={
        **RUN_PAYLOAD, "sample_id": _sample(client), "replicate": 0,
    })
    assert r.status_code == 422


def test_an_unknown_type_is_refused(client: TestClient) -> None:
    r = client.post("/api/nanopore-runs", json={
        **RUN_PAYLOAD, "sample_id": _sample(client), "replicate": 1, "replicate_type": "biological",
    })
    assert r.status_code == 422


def test_a_type_without_a_number_is_refused_on_create(client: TestClient) -> None:
    r = client.post("/api/nanopore-runs", json={
        **RUN_PAYLOAD, "sample_id": _sample(client), "replicate_type": "technical",
    })
    assert r.status_code == 422
    assert "replicate_type needs a replicate number" in r.text


def test_update_sets_the_pair_and_clears_it(client: TestClient) -> None:
    run = _make_run(client, _sample(client))

    r = client.patch(f"/api/nanopore-runs/{run['id']}", json={"replicate": 1, "replicate_type": "extraction"})
    assert r.status_code == 200, r.text
    assert (r.json()["replicate"], r.json()["replicate_type"]) == (1, "extraction")

    r = client.patch(f"/api/nanopore-runs/{run['id']}", json={"replicate": None, "replicate_type": None})
    assert r.status_code == 200, r.text
    assert (r.json()["replicate"], r.json()["replicate_type"]) == (None, None)


def test_update_cannot_leave_a_type_without_a_number(client: TestClient) -> None:
    # Clearing the number alone would leave replicate_type describing nothing; the pair is
    # validated as it will be stored, whichever half the request mentions.
    run = _make_run(client, _sample(client), {
        **RUN_PAYLOAD, "replicate": 1, "replicate_type": "extraction",
    })

    r = client.patch(f"/api/nanopore-runs/{run['id']}", json={"replicate": None})

    assert r.status_code == 422
    assert "replicate_type needs a replicate number" in r.text


def test_update_may_set_the_type_once_a_number_is_stored(client: TestClient) -> None:
    run = _make_run(client, _sample(client), {**RUN_PAYLOAD, "replicate": 1})

    r = client.patch(f"/api/nanopore-runs/{run['id']}", json={"replicate_type": "technical"})

    assert r.status_code == 200, r.text
    assert r.json()["replicate_type"] == "technical"
