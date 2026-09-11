"""Tests for /api/nanopore-runs — CRUD, derived field computation, uniqueness."""

from fastapi.testclient import TestClient

# ── fixtures / helpers ────────────────────────────────────────────────────────


def _make_site(client: TestClient) -> dict:
    r = client.post(
        "/api/sites",
        json={"country": "Norway", "country_code": "NO", "city_code": "BGO", "site": "Park"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _make_sample(client: TestClient, site_id: str) -> dict:
    r = client.post(
        "/api/samples",
        json={"site_id": site_id, "sample_type": "water", "sampling_date": "20240601"},
    )
    assert r.status_code == 201, r.text
    return r.json()


RUN_PAYLOAD = {
    "run_accession": "ERR123456",
    "barcode": "BC01",
    "protocol_id": "SQK-LSK114",
    "sequencing_kit_id": "SQK-LSK114",
    "type": "GridION",
}


def _make_run(client: TestClient, sample_id: str, payload: dict | None = None) -> dict:
    p = {**(payload or RUN_PAYLOAD), "sample_id": sample_id}
    r = client.post("/api/nanopore-runs", json=p)
    assert r.status_code == 201, r.text
    return r.json()


# ── list ─────────────────────────────────────────────────────────────────────


def test_list_empty(client: TestClient) -> None:
    r = client.get("/api/nanopore-runs")
    assert r.status_code == 200
    assert r.json() == []


def test_list_returns_created(client: TestClient) -> None:
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    _make_run(client, sample["id"])
    assert len(client.get("/api/nanopore-runs").json()) == 1


def test_list_filter_by_run_accession(client: TestClient) -> None:
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    _make_run(client, sample["id"])
    _make_run(
        client,
        sample["id"],
        {**RUN_PAYLOAD, "run_accession": "ERR999999", "barcode": "BC02"},
    )
    r = client.get("/api/nanopore-runs", params={"run_accession": "ERR123456"})
    assert len(r.json()) == 1
    assert r.json()[0]["run_accession"] == "ERR123456"


def test_list_includes_sample_code_and_sampling_date(client: TestClient) -> None:
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    _make_run(client, sample["id"])
    run = client.get("/api/nanopore-runs").json()[0]
    assert run["sample_code"] == "NOBGOPark_water"
    assert run["sampling_date"] == "20240601"


# ── create ────────────────────────────────────────────────────────────────────


def test_create_derives_minknow_sample_id(client: TestClient) -> None:
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    run = _make_run(client, sample["id"])
    # {sample_code}_{protocol_id}_{sequencing_kit_id}
    assert run["minknow_sample_id"] == "NOBGOPark_water_SQK-LSK114_SQK-LSK114"


def test_create_derives_alias(client: TestClient) -> None:
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    run = _make_run(client, sample["id"])
    # {sample_code}_{barcode}
    assert run["alias"] == "NOBGOPark_water_BC01"


def test_create_client_cannot_inject_minknow_or_alias(client: TestClient) -> None:
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    p = {**RUN_PAYLOAD, "sample_id": sample["id"], "minknow_sample_id": "X", "alias": "Y"}
    r = client.post("/api/nanopore-runs", json=p)
    assert r.status_code == 201
    data = r.json()
    assert data["minknow_sample_id"] != "X"
    assert data["alias"] != "Y"


def test_create_duplicate_accession_barcode_rejected(client: TestClient) -> None:
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    _make_run(client, sample["id"])
    r = client.post("/api/nanopore-runs", json={**RUN_PAYLOAD, "sample_id": sample["id"]})
    assert r.status_code == 409


def test_create_invalid_sample_id_rejected(client: TestClient) -> None:
    r = client.post("/api/nanopore-runs", json={**RUN_PAYLOAD, "sample_id": "does-not-exist"})
    assert r.status_code == 422


def test_create_no_sample_id_still_creates(client: TestClient) -> None:
    """sample_id is optional; minknow_sample_id and alias will be None."""
    r = client.post("/api/nanopore-runs", json=RUN_PAYLOAD)
    assert r.status_code == 201
    data = r.json()
    assert data["minknow_sample_id"] is None
    assert data["alias"] is None


# ── get ───────────────────────────────────────────────────────────────────────


def test_get_by_id(client: TestClient) -> None:
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    run = _make_run(client, sample["id"])
    r = client.get(f"/api/nanopore-runs/{run['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == run["id"]


def test_get_nonexistent_returns_404(client: TestClient) -> None:
    assert client.get("/api/nanopore-runs/does-not-exist").status_code == 404


# ── update ────────────────────────────────────────────────────────────────────


def test_update_comment(client: TestClient) -> None:
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    run = _make_run(client, sample["id"])
    r = client.patch(f"/api/nanopore-runs/{run['id']}", json={"comments": "Looks good"})
    assert r.status_code == 200
    assert r.json()["comments"] == "Looks good"
    # Derived fields unchanged
    assert r.json()["alias"] == "NOBGOPark_water_BC01"


def test_update_barcode_recalculates_alias(client: TestClient) -> None:
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    run = _make_run(client, sample["id"])
    r = client.patch(f"/api/nanopore-runs/{run['id']}", json={"barcode": "BC99"})
    assert r.status_code == 200
    assert r.json()["alias"] == "NOBGOPark_water_BC99"


def test_update_duplicate_accession_barcode_rejected(client: TestClient) -> None:
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    run_a = _make_run(client, sample["id"])
    run_b = _make_run(
        client, sample["id"], {**RUN_PAYLOAD, "run_accession": "ERR999999", "barcode": "BC02"}
    )
    # Try to rename run_b to match run_a (use accession_id, not run_accession — new API)
    r = client.patch(
        f"/api/nanopore-runs/{run_b['id']}",
        json={"accession_id": run_a["accession_id"], "barcode": run_a["barcode"]},
    )
    assert r.status_code == 409


def test_update_nonexistent_returns_404(client: TestClient) -> None:
    r = client.patch("/api/nanopore-runs/does-not-exist", json={"comments": "x"})
    assert r.status_code == 404


# ── delete ────────────────────────────────────────────────────────────────────


def test_delete_soft_deletes(client: TestClient) -> None:
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    run = _make_run(client, sample["id"])
    r = client.delete(f"/api/nanopore-runs/{run['id']}")
    assert r.status_code == 204
    assert client.get(f"/api/nanopore-runs/{run['id']}").status_code == 404


def test_delete_nonexistent_returns_404(client: TestClient) -> None:
    assert client.delete("/api/nanopore-runs/does-not-exist").status_code == 404


# ── Update scope: a save must not stamp rows it did not change ────────────────
#
# A PATCH touches two tables: run-level fields live on nanopore_run_accessions and
# barcode-level fields on nanopore_runs. Both updates used to fire on *presence* of a
# submitted value rather than on it differing from what is stored — and the
# barcode-level one fired unconditionally, since it was always rebuilt from the row's
# current accession_id/sample_id/barcode.
#
# Why that is not merely untidy: updated_at is not in sync's _AUDIT_FIELDS, so it does
# drive merge decisions (`incoming_is_newer` in api/sync.py). A no-op write makes this
# device's copy look newer than it is, and the default decision for an updated row is
# "accept only if incoming is newer" — so another device's genuine edit to that row can
# be silently skipped. Editing a comment must therefore leave the barcode row alone.


def _run_audit(db, run_id: str) -> tuple:
    row = db.execute(
        "SELECT updated_at, updated_by FROM nanopore_runs WHERE id = ?", (run_id,)
    ).fetchone()
    return row["updated_at"], row["updated_by"]


def _accession_audit(db, accession_id: str) -> tuple:
    row = db.execute(
        "SELECT updated_at, updated_by FROM nanopore_run_accessions WHERE id = ?",
        (accession_id,),
    ).fetchone()
    return row["updated_at"], row["updated_by"]


def _accession_id_of(db, run_id: str) -> str:
    return db.execute(
        "SELECT accession_id FROM nanopore_runs WHERE id = ?", (run_id,)
    ).fetchone()["accession_id"]


def _setup(client: TestClient) -> dict:
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    return _make_run(client, sample["id"])


def test_editing_only_a_comment_leaves_the_barcode_row_untouched(
    client: TestClient, db, monkeypatch
) -> None:
    """The reported bug: a comment edit stamped nanopore_runs with no field changed.

    Observed in a real sync export — the barcode row came back with
    ``fields changed: ['updated_at', 'updated_by']`` and nothing else.
    """
    run = _setup(client)
    before = _run_audit(db, run["id"])

    monkeypatch.setenv("ODIN_DEVICE_NAME", "device-b")
    assert client.patch(
        f"/api/nanopore-runs/{run['id']}", json={"comments": "Looks good"}
    ).status_code == 200

    assert _run_audit(db, run["id"]) == before, (
        "nanopore_runs was stamped by an edit that changed only a run-level field"
    )


def test_editing_only_a_comment_still_stamps_the_accession_row(
    client: TestClient, db, monkeypatch
) -> None:
    """The other half: the row that did change must still be attributed."""
    run = _setup(client)
    accession_id = _accession_id_of(db, run["id"])

    monkeypatch.setenv("ODIN_DEVICE_NAME", "device-b")
    assert client.patch(
        f"/api/nanopore-runs/{run['id']}", json={"comments": "Looks good"}
    ).status_code == 200

    assert _accession_audit(db, accession_id)[1] == "device-b"


def test_resubmitting_an_unchanged_comment_stamps_nothing(
    client: TestClient, db, monkeypatch
) -> None:
    """Saving a form without editing anything must not touch either table."""
    run = _setup(client)
    monkeypatch.setenv("ODIN_DEVICE_NAME", "device-a")
    client.patch(f"/api/nanopore-runs/{run['id']}", json={"comments": "Looks good"})

    accession_id = _accession_id_of(db, run["id"])
    run_before = _run_audit(db, run["id"])
    acc_before = _accession_audit(db, accession_id)

    monkeypatch.setenv("ODIN_DEVICE_NAME", "device-b")
    assert client.patch(
        f"/api/nanopore-runs/{run['id']}", json={"comments": "Looks good"}
    ).status_code == 200

    assert _run_audit(db, run["id"]) == run_before
    assert _accession_audit(db, accession_id) == acc_before, (
        "an identical resubmission re-stamped the accession row"
    )


def test_changing_the_barcode_does_stamp_the_barcode_row(
    client: TestClient, db, monkeypatch
) -> None:
    """Guard against the fix over-reaching: real barcode-level edits must stamp."""
    run = _setup(client)
    monkeypatch.setenv("ODIN_DEVICE_NAME", "device-b")
    assert client.patch(
        f"/api/nanopore-runs/{run['id']}", json={"barcode": "BC99"}
    ).status_code == 200
    assert _run_audit(db, run["id"])[1] == "device-b"


def test_changing_the_sample_link_does_stamp_the_barcode_row(
    client: TestClient, db, monkeypatch
) -> None:
    site = _make_site(client)
    sample = _make_sample(client, site["id"])
    run = _make_run(client, sample["id"])
    # Second sample on the same site, with a different type so sample_code differs.
    r = client.post(
        "/api/samples",
        json={"site_id": site["id"], "sample_type": "sediment", "sampling_date": "20240602"},
    )
    assert r.status_code == 201, r.text

    monkeypatch.setenv("ODIN_DEVICE_NAME", "device-b")
    assert client.patch(
        f"/api/nanopore-runs/{run['id']}", json={"sample_id": r.json()["id"]}
    ).status_code == 200
    assert _run_audit(db, run["id"])[1] == "device-b"


def test_resubmitting_an_unchanged_type_stamps_nothing(
    client: TestClient, db, monkeypatch
) -> None:
    """A barcode-level field submitted with its existing value is not a change.

    RUN_PAYLOAD already sets type=GridION, so this PATCH sends what is already stored.
    (mpox_type has only GridION seeded in the test fixtures, so the "changed to a
    different code" case is covered by the barcode and sample_id tests above.)
    """
    run = _setup(client)
    monkeypatch.setenv("ODIN_DEVICE_NAME", "device-b")
    assert client.patch(
        f"/api/nanopore-runs/{run['id']}", json={"type": "GridION"}
    ).status_code == 200
    assert _run_audit(db, run["id"])[1] is None
