"""One Biomeme run file holds several samples — biomeme_runs is one row per (run, sample).

Before 2026-09-17 the table had UNIQUE (biomeme_run_name): the Excel import treated a
second sample of the same run as an update of the first, seeding kept the first row only,
and the processing script — which groups wells into samples from this table — silently
lost every other sample in the run (35 rows for 35 runs on the demo instance; 231 output
rows where the same files give 654). These tests pin the (run, sample) identity at every
entry point: API, Excel import (merge and replace), registration from disk, discovery,
seeding and sync's merge key.
"""

import io
import sqlite3
import uuid
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

from backend.app.api.biomeme_runs import seed_biomeme_runs
from backend.app.api.sync import _TABLE_MAP

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_NOW = "2025-01-01T00:00:00.000Z"
RUN = "2025-09-04-05260312"


# ── helpers ───────────────────────────────────────────────────────────────────


def _site(client: TestClient) -> dict:
    r = client.post(
        "/api/sites",
        json={"country": "Norway", "country_code": "NO", "city_code": "BGO", "site": "River"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _sample(client: TestClient, site_id: str, sampling_date: str) -> dict:
    r = client.post(
        "/api/samples",
        json={"site_id": site_id, "sample_type": "water", "sampling_date": sampling_date},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _run(client: TestClient, sample_id: str | None, **fields) -> object:
    return client.post(
        "/api/biomeme-runs",
        json={"biomeme_run_name": RUN, "sample_id": sample_id, **fields},
    )


def _xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("biomeme")
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _upload(client: TestClient, xlsx: bytes, mode: str = "merge", import_valid_rows: bool = False):
    return client.post(
        "/api/export/excel/import",
        files={"file": ("meta.xlsx", xlsx, _XLSX)},
        params={"mode": mode, "import_valid_rows": import_valid_rows},
    )


def _rows_for_run(client: TestClient) -> list[dict]:
    return [r for r in client.get("/api/biomeme-runs").json() if r["biomeme_run_name"] == RUN]


def _set_config(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        """INSERT OR REPLACE INTO config_values (id, key, value, created_at, updated_at, created_by)
           VALUES (?,?,?,?,?,?)""",
        (str(uuid.uuid4()), key, value, _NOW, _NOW, "test"),
    )
    db.commit()


@pytest.fixture()
def two_samples(client: TestClient) -> tuple[dict, dict]:
    """Two sampling occasions of one site — two samples that share a Biomeme run."""
    site = _site(client)
    return _sample(client, site["id"], "20250628"), _sample(client, site["id"], "20250731")


# ── API ───────────────────────────────────────────────────────────────────────


def test_two_samples_of_one_run_are_two_rows(client: TestClient, two_samples) -> None:
    s1, s2 = two_samples
    assert _run(client, s1["id"], biomeme_sample_id="1,NTC").status_code == 201
    assert _run(client, s2["id"], biomeme_sample_id="2,NTC").status_code == 201

    rows = _rows_for_run(client)
    assert {r["sample_code"] for r in rows} == {s1["sample_code"], s2["sample_code"]}
    assert {r["biomeme_sample_id"] for r in rows} == {"1,NTC", "2,NTC"}


def test_same_run_and_sample_twice_is_a_conflict(client: TestClient, two_samples) -> None:
    s1, _ = two_samples
    assert _run(client, s1["id"]).status_code == 201
    r = _run(client, s1["id"])
    assert r.status_code == 409
    assert "already has a row for this sample" in r.json()["detail"]


def test_a_run_keeps_at_most_one_unlinked_row(client: TestClient) -> None:
    assert _run(client, None).status_code == 201
    r = _run(client, None)
    assert r.status_code == 409
    assert "no sample" in r.json()["detail"]


def test_update_cannot_collide_with_another_row_of_the_run(client: TestClient, two_samples) -> None:
    s1, s2 = two_samples
    _run(client, s1["id"])
    second = _run(client, s2["id"]).json()

    r = client.patch(f"/api/biomeme-runs/{second['id']}", json={"sample_id": s1["id"]})

    assert r.status_code == 409
    assert client.get(f"/api/biomeme-runs/{second['id']}").json()["sample_id"] == s2["id"]


def test_update_that_changes_nothing_about_identity_is_not_a_conflict(
    client: TestClient, two_samples
) -> None:
    s1, _ = two_samples
    created = _run(client, s1["id"]).json()
    r = client.patch(f"/api/biomeme-runs/{created['id']}", json={"comments": "note"})
    assert r.status_code == 200
    assert r.json()["comments"] == "note"


# ── Excel import ──────────────────────────────────────────────────────────────

_HEADER = ["biomeme_run_name", "sample_code", "sampling_date", "biomeme_sample_id"]


def test_excel_import_creates_one_row_per_sample_of_a_run(client: TestClient, two_samples) -> None:
    s1, s2 = two_samples
    xlsx = _xlsx([
        _HEADER,
        [RUN, s1["sample_code"], "20250628", "1,NTC"],
        [RUN, s2["sample_code"], "20250731", "2,NTC"],
    ])

    r = _upload(client, xlsx)

    assert r.status_code == 200, r.text
    assert r.json()["summary"]["biomeme"]["created"] == 2
    rows = _rows_for_run(client)
    assert {(x["sample_code"], x["biomeme_sample_id"]) for x in rows} == {
        (s1["sample_code"], "1,NTC"), (s2["sample_code"], "2,NTC"),
    }


def test_reimporting_the_same_workbook_changes_nothing(client: TestClient, two_samples) -> None:
    s1, s2 = two_samples
    xlsx = _xlsx([
        _HEADER,
        [RUN, s1["sample_code"], "20250628", "1,NTC"],
        [RUN, s2["sample_code"], "20250731", "2,NTC"],
    ])
    _upload(client, xlsx)

    summary = _upload(client, xlsx).json()["summary"]["biomeme"]

    assert summary["created"] == 0 and summary["updated"] == 0 and summary["unchanged"] == 2
    assert len(_rows_for_run(client)) == 2


def test_excel_import_rejects_a_well_given_to_two_samples(client: TestClient, two_samples) -> None:
    """A well holds one sample; the shared NTC wells are not a collision."""
    s1, s2 = two_samples
    xlsx = _xlsx([
        _HEADER,
        [RUN, s1["sample_code"], "20250628", "1,2,NTC"],
        [RUN, s2["sample_code"], "20250731", "2,NTC"],
    ])

    rejected = _upload(client, xlsx)
    assert rejected.status_code == 422
    assert _rows_for_run(client) == []

    partial = _upload(client, xlsx, import_valid_rows=True)
    assert partial.status_code == 200, partial.text
    body = partial.json()
    assert body["summary"]["biomeme"] == {
        "created": 1, "updated": 0, "unchanged": 0, "deleted": 0, "skipped": 1,
    }
    assert "well 2" in body["problems"][0]["reason"]
    assert [x["sample_code"] for x in _rows_for_run(client)] == [s1["sample_code"]]


def test_shared_control_wells_are_not_a_collision(client: TestClient, two_samples) -> None:
    """Every sample row of a run lists the run's NTC and POS controls; only sample wells collide."""
    s1, s2 = two_samples
    xlsx = _xlsx([
        _HEADER,
        [RUN, s1["sample_code"], "20250628", "1,2,POS,NTC1,NTC2"],
        [RUN, s2["sample_code"], "20250731", "3,4,POS,NTC1,NTC2"],
    ])

    r = _upload(client, xlsx)

    assert r.status_code == 200, r.text
    assert r.json()["summary"]["biomeme"]["created"] == 2


def test_replace_mode_prunes_run_sample_pairs_absent_from_the_workbook(
    client: TestClient, two_samples
) -> None:
    s1, s2 = two_samples
    _run(client, s1["id"], biomeme_sample_id="1,NTC")
    _run(client, s2["id"], biomeme_sample_id="2,NTC")

    r = _upload(client, _xlsx([_HEADER, [RUN, s1["sample_code"], "20250628", "1,NTC"]]), mode="replace")

    assert r.status_code == 200, r.text
    assert r.json()["summary"]["biomeme"]["deleted"] == 1
    assert [x["sample_code"] for x in _rows_for_run(client)] == [s1["sample_code"]]


# ── Registration from disk and discovery ──────────────────────────────────────


def test_register_adds_a_second_sample_to_a_registered_run(client: TestClient, two_samples) -> None:
    s1, s2 = two_samples
    first = client.post("/api/biomeme/register", json={
        "biomeme_run_names": [RUN], "per_run_sample_ids": {RUN: s1["id"]},
    })
    second = client.post("/api/biomeme/register", json={
        "biomeme_run_names": [RUN], "per_run_sample_ids": {RUN: s2["id"]},
    })
    again = client.post("/api/biomeme/register", json={
        "biomeme_run_names": [RUN], "per_run_sample_ids": {RUN: s2["id"]},
    })

    assert first.json()["created"] == [RUN]
    assert second.json()["created"] == [RUN]
    assert again.json() == {"created": [], "skipped": [RUN]}
    assert {x["sample_code"] for x in _rows_for_run(client)} == {s1["sample_code"], s2["sample_code"]}


def test_register_without_a_sample_is_skipped_once_the_run_has_rows(
    client: TestClient, two_samples
) -> None:
    s1, _ = two_samples
    client.post("/api/biomeme/register", json={
        "biomeme_run_names": [RUN], "per_run_sample_ids": {RUN: s1["id"]},
    })

    r = client.post("/api/biomeme/register", json={"biomeme_run_names": [RUN]})

    assert r.json() == {"created": [], "skipped": [RUN]}
    assert len(_rows_for_run(client)) == 1


def test_discovery_lists_every_sample_of_a_run_file(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path, two_samples
) -> None:
    s1, s2 = two_samples
    day_dir = tmp_path / "biomeme_input_data" / "NO" / "20250904"
    day_dir.mkdir(parents=True)
    (day_dir / f"{RUN}.xlsx").write_text("stub")
    _set_config(db, "biomeme_dir", str(tmp_path))
    _run(client, s1["id"], biomeme_sample_id="1,NTC")
    _run(client, s2["id"], biomeme_sample_id="2,NTC")

    folders = client.get("/api/discovery/biomeme", params={"force_refresh": True}).json()["folders"]
    (file_status,) = folders[0]["files"]
    assert file_status["registered"] is True
    assert folders[0]["registered_count"] == 1          # one file, registered
    assert {s["sample_code"] for s in file_status["samples"]} == {s1["sample_code"], s2["sample_code"]}
    assert {s["biomeme_sample_id"] for s in file_status["samples"]} == {"1,NTC", "2,NTC"}

    (discovered,) = client.get("/api/biomeme/discover").json()
    assert discovered["registered"] is True
    assert {s["sample_code"] for s in discovered["samples"]} == {s1["sample_code"], s2["sample_code"]}


# ── Seeding ───────────────────────────────────────────────────────────────────


def test_seed_creates_one_row_per_csv_row_and_is_idempotent(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    two_samples,
) -> None:
    s1, s2 = two_samples
    (tmp_path / "biomeme.csv").write_text(
        "biomeme_run_name;sample_code;sampling_date;biomeme_sample_id;dilution_factor\n"
        f"{RUN};{s1['sample_code']};20250628;1,NTC;\n"
        f"{RUN};{s2['sample_code']};20250731;2,NTC;0.5\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ODIN_SEED_DIR", str(tmp_path))

    seed_biomeme_runs(db)
    seed_biomeme_runs(db)

    rows = _rows_for_run(client)
    assert {(x["sample_code"], x["biomeme_sample_id"], x["dilution_factor"]) for x in rows} == {
        (s1["sample_code"], "1,NTC", None), (s2["sample_code"], "2,NTC", 0.5),
    }


# ── Sync ──────────────────────────────────────────────────────────────────────


def test_sync_merge_key_is_run_and_sample() -> None:
    assert _TABLE_MAP["biomeme_runs"]["unique_keys"] == [["biomeme_run_name", "sample_id"]]
