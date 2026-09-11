"""POST /sync/restore — make the database match a snapshot.

The counterpart to /sync/apply, not a variant of it. A merge answers "add their information to
mine" and deliberately never deletes: rows the other device lacks are information to keep. A
restore answers "put mine back to this", so it must delete rows the snapshot does not contain.
They take the same file and have opposite consequences.

That gap is why the automatic pre-import backup could not previously undo an import that *added*
rows — replaying it through the merge path re-asserted old values but left the additions behind.
"""

import sqlite3

from fastapi.testclient import TestClient

SITE = {"country": "Norway", "country_code": "NO", "city_code": "BGO", "site": "Park"}


def _site(client: TestClient, **over) -> dict:
    r = client.post("/api/sites", json={**SITE, **over})
    assert r.status_code == 201, r.text
    return r.json()


def _sample(client: TestClient, site_id: str, sample_type: str = "water") -> dict:
    r = client.post(
        "/api/samples",
        json={"site_id": site_id, "sample_type": sample_type, "sampling_date": "20240601"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _snapshot(client: TestClient) -> dict:
    r = client.get("/api/sync/export")
    assert r.status_code == 200, r.text
    return r.json()


def _restore(client: TestClient, snapshot: dict) -> dict:
    r = client.post("/api/sync/restore", json=snapshot)
    assert r.status_code == 200, r.text
    return r.json()


# ── the property the whole backup story rests on ──────────────────────────────


def test_restore_removes_rows_added_after_the_snapshot(client: TestClient) -> None:
    """The gap that motivated this endpoint: a merge could not undo an addition."""
    site = _site(client)
    before = _snapshot(client)

    _site(client, site="Beach")  # an addition the snapshot does not know about
    assert len(client.get("/api/sites").json()) == 2

    result = _restore(client, before)

    remaining = client.get("/api/sites").json()
    assert [s["id"] for s in remaining] == [site["id"]]
    assert result["total_deleted"] >= 1


def test_restore_brings_back_a_deleted_row(client: TestClient) -> None:
    site = _site(client)
    before = _snapshot(client)

    assert client.delete(f"/api/sites/{site['id']}").status_code == 204
    assert client.get("/api/sites").json() == []

    _restore(client, before)

    restored = client.get("/api/sites").json()
    assert [s["id"] for s in restored] == [site["id"]]


def test_restore_reverts_a_changed_value(client: TestClient) -> None:
    site = _site(client)
    before = _snapshot(client)

    client.patch(f"/api/sites/{site['id']}", json={"location": "moved"})
    assert client.get(f"/api/sites/{site['id']}").json()["location"] == "moved"

    _restore(client, before)

    assert client.get(f"/api/sites/{site['id']}").json()["location"] is None


def test_a_snapshot_round_trips_exactly(client: TestClient) -> None:
    """Mutate in every direction, restore, and the snapshot should match the original."""
    site = _site(client)
    _sample(client, site["id"])
    before = _snapshot(client)

    other = _site(client, site="Beach")          # addition
    _sample(client, other["id"], "sediment")     # addition
    client.patch(f"/api/sites/{site['id']}", json={"comments": "changed"})  # modification

    _restore(client, before)

    after = _snapshot(client)
    for table in before["tables"]:
        assert after["tables"][table] == before["tables"][table], table


# ── ordering, which foreign keys make load-bearing ────────────────────────────


def test_restore_deletes_children_before_parents(client: TestClient) -> None:
    """A sample cannot be deleted while a nanopore run references it.

    The delete order is explicit rather than the reverse of the upsert order, because
    nanopore_run_accessions has no parent among these tables and sits differently in each.
    """
    site = _site(client)
    empty = _snapshot(client)  # snapshot with the site but nothing else

    sample = _sample(client, site["id"])
    r = client.post(
        "/api/nanopore-runs",
        json={
            "run_accession": "ERR1",
            "barcode": "barcode01",
            "protocol_id": "SQK-LSK114",
            "sequencing_kit_id": "SQK-LSK114",
            "sample_id": sample["id"],
        },
    )
    assert r.status_code == 201, r.text

    _restore(client, empty)  # must not trip the FK from nanopore_runs to samples

    assert client.get("/api/samples").json() == []
    assert client.get("/api/nanopore-runs").json() == []


def test_restore_upserts_parents_before_children(client: TestClient) -> None:
    """A sample cannot be inserted before the site it references."""
    site = _site(client)
    sample = _sample(client, site["id"])
    full = _snapshot(client)

    assert client.delete(f"/api/samples/{sample['id']}").status_code == 204
    assert client.delete(f"/api/sites/{site['id']}").status_code == 204

    _restore(client, full)

    assert len(client.get("/api/sites").json()) == 1
    assert len(client.get("/api/samples").json()) == 1


# ── scope and safety ──────────────────────────────────────────────────────────


def test_restore_leaves_local_only_state_alone(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """Pipeline history is not metadata and is not in a snapshot, so a restore must not touch it."""
    _site(client)
    snapshot = _snapshot(client)
    db.execute(
        "INSERT INTO pipeline_runs (id, pipeline_type, status, params, created_at, updated_at) "
        "VALUES ('run-1','taxprofiler','done','{}','2026-01-01T00:00:00.000Z','2026-01-01T00:00:00.000Z')"
    )
    db.commit()

    _restore(client, snapshot)

    assert db.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0] == 1


def test_restore_rejects_an_unknown_table(client: TestClient) -> None:
    snapshot = _snapshot(client)
    snapshot["tables"]["not_a_table"] = []
    r = client.post("/api/sync/restore", json=snapshot)
    assert r.status_code == 422
    assert "not_a_table" in r.json()["detail"]


def test_restore_rejects_a_row_without_an_id(client: TestClient) -> None:
    """Restore matches by primary key, so a row without one cannot be placed."""
    _site(client)
    snapshot = _snapshot(client)
    snapshot["tables"]["sites"] = [{"site_code": "XX", "country": "Nowhere"}]
    r = client.post("/api/sync/restore", json=snapshot)
    assert r.status_code == 422
    assert "no id" in r.json()["detail"]


def test_a_failed_restore_changes_nothing(client: TestClient) -> None:
    """All or nothing: a snapshot that cannot be applied must leave the database untouched."""
    site = _site(client)
    _sample(client, site["id"])
    snapshot = _snapshot(client)
    before = client.get("/api/samples").json()

    # A sample referencing a site the snapshot does not contain violates the FK.
    snapshot["tables"]["samples"][0]["site_id"] = "no-such-site"
    r = client.post("/api/sync/restore", json=snapshot)
    assert r.status_code == 409, r.text
    assert "nothing was changed" in r.json()["detail"]

    assert client.get("/api/samples").json() == before
    assert len(client.get("/api/sites").json()) == 1


def test_restore_reports_what_it_did(client: TestClient) -> None:
    _site(client)
    before = _snapshot(client)
    _site(client, site="Beach")

    result = _restore(client, before)

    assert result["tables"]["sites"]["deleted"] == 1
    assert result["tables"]["sites"]["restored"] == 1
    assert result["total_restored"] >= 1
    assert result["exported_at"] == before["exported_at"]


def test_restore_of_an_empty_snapshot_empties_the_syncable_tables(
    client: TestClient,
) -> None:
    """Restoring a snapshot taken from an empty database is a legitimate, total deletion."""
    empty = _snapshot(client)
    site = _site(client)
    _sample(client, site["id"])

    _restore(client, empty)

    assert client.get("/api/sites").json() == []
    assert client.get("/api/samples").json() == []
