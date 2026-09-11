"""The startup seed reads the replicate columns of nanopore.csv.

The demo's seed CSV is how a replicate group reaches a fresh database without anyone
typing it in, so the seeder has to carry the two columns through — and, because a seed
must never stop the app starting, to drop a bad value with a message rather than fail.
"""

import logging
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.api.nanopore_runs import seed_nanopore_runs

RA = "20260610_0800_MN00000_FAX00001_aabb1234"
HEADER = "run_accession;barcode;sample_code;sampling_date;protocol_id;sequencing_kit_id;type;runName;sampleName;replicate;replicate_type\n"


def _sample(client: TestClient) -> None:
    site = client.post("/api/sites", json={
        "country": "Demoville", "country_code": "DV", "city_code": "DV", "site": "River",
    }).json()
    r = client.post("/api/samples", json={
        "site_id": site["id"], "sample_type": "water", "sampling_date": "20260610",
    })
    assert r.status_code == 201, r.text


def _seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db: sqlite3.Connection, rows: str) -> list:
    (tmp_path / "nanopore.csv").write_text(HEADER + rows, encoding="utf-8")
    monkeypatch.setenv("ODIN_SEED_DIR", str(tmp_path))
    seed_nanopore_runs(db)
    db.commit()
    return db.execute(
        "SELECT barcode, replicate, replicate_type FROM nanopore_runs ORDER BY barcode"
    ).fetchall()


def test_numbered_rows_are_seeded_with_their_number_and_type(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sample(client)
    code = db.execute("SELECT sample_code FROM samples").fetchone()[0]

    rows = _seed(tmp_path, monkeypatch, db, (
        f"{RA};barcode01;{code};20260610;P2;RB_GEN;;Demo;S1;1;collection\n"
        f"{RA};barcode03;{code};20260610;P2;RB_GEN;;Demo;S1;2;collection\n"
        f"{RA};barcode02;{code};20260610;P2;RB_GEN;;Demo;S1;;\n"
    ))

    assert [tuple(r) for r in rows] == [
        ("barcode01", 1, "collection"),
        ("barcode02", None, None),
        ("barcode03", 2, "collection"),
    ]


def test_a_bad_value_is_dropped_and_the_barcode_still_seeded(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _sample(client)
    code = db.execute("SELECT sample_code FROM samples").fetchone()[0]

    caplog.set_level(logging.WARNING, logger="backend.app.api.nanopore_runs")
    rows = _seed(tmp_path, monkeypatch, db, (
        f"{RA};barcode01;{code};20260610;P2;RB_GEN;;Demo;S1;two;collection\n"
        f"{RA};barcode02;{code};20260610;P2;RB_GEN;;Demo;S1;;technical\n"
        f"{RA};barcode03;{code};20260610;P2;RB_GEN;;Demo;S1;1;biological\n"
    ))

    assert [tuple(r) for r in rows] == [
        ("barcode01", None, None),
        ("barcode02", None, None),
        ("barcode03", 1, None),
    ]
    out = caplog.text
    assert "'two' is not a positive integer" in out
    assert "replicate_type without a replicate number" in out
    assert "'biological' unknown" in out


def test_a_seed_file_without_the_columns_still_works(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sample(client)
    code = db.execute("SELECT sample_code FROM samples").fetchone()[0]
    (tmp_path / "nanopore.csv").write_text(
        "run_accession;barcode;sample_code;sampling_date;protocol_id;sequencing_kit_id;type;runName;sampleName\n"
        f"{RA};barcode01;{code};20260610;P2;RB_GEN;;Demo;S1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ODIN_SEED_DIR", str(tmp_path))

    seed_nanopore_runs(db)

    assert db.execute("SELECT replicate FROM nanopore_runs").fetchone()[0] is None
