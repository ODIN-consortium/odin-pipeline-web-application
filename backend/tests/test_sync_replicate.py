"""New nanopore_runs columns travel through sync without being listed anywhere.

Export is SELECT * and restore discovers columns with PRAGMA table_info, so a column added by
a migration needs no sync change — this pins that, because the replicate plan once assumed
otherwise and a future reader might too.
"""

from fastapi.testclient import TestClient

from backend.tests.test_nanopore_runs import RUN_PAYLOAD, _make_run, _make_sample, _make_site
from backend.tests.test_sync import _export


def test_sync_export_carries_the_replicate_columns(client: TestClient) -> None:
    sample = _make_sample(client, _make_site(client)["id"])
    created = _make_run(client, sample["id"], {**RUN_PAYLOAD, "replicate": 3, "replicate_type": "technical"})

    rows = _export(client)["tables"]["nanopore_runs"]
    row = next(r for r in rows if r["id"] == created["id"])

    assert row["replicate"] == 3
    assert row["replicate_type"] == "technical"
