"""The replicate columns reach the per-barcode metadata rows the feather writers consume."""

import sqlite3

from fastapi.testclient import TestClient

from backend.app.db.queries import get_metadata_rows_from_db
from backend.tests.test_queries import (
    RUN_ACCESSION,
    RUN_PAYLOAD,
    _make_run,
    _make_sample,
    _make_site,
)


def test_metadata_rows_carry_replicate_and_type(client: TestClient, db: sqlite3.Connection) -> None:
    sample = _make_sample(client, _make_site(client)["id"])
    _make_run(client, sample["id"], {**RUN_PAYLOAD, "replicate": 2, "replicate_type": "collection"})

    row = get_metadata_rows_from_db(db, [RUN_ACCESSION])[0]

    assert row["replicate"] == 2
    assert row["replicate_type"] == "collection"


def test_a_barcode_outside_any_group_has_null_number_and_empty_type(
    client: TestClient, db: sqlite3.Connection
) -> None:
    # replicate stays NULL rather than '' in the metadata row, which is the database's
    # integer; the dataset writers turn it into text ('' / '2' / 'pooled') themselves.
    # replicate_type follows the text columns' convention of '' for absent.
    sample = _make_sample(client, _make_site(client)["id"])
    _make_run(client, sample["id"])

    row = get_metadata_rows_from_db(db, [RUN_ACCESSION])[0]

    assert row["replicate"] is None
    assert row["replicate_type"] == ""
