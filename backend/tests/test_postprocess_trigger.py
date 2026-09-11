"""POST /api/pipeline/runs/{id}/postprocess — the standalone re-export of one run.

The endpoint had no test, and the replicate work changed the AMR post-processor's
signature (``pool_replicates`` became required) without reaching it: every AMR
re-export answered 500 (9 Oct 2026, rebuilding the Enlighten datasets). The endpoint
now selects the post-processor the way the launch does, from the run's recorded params.
"""

import json
import sqlite3
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

_NOW = "2026-01-01T00:00:00Z"
RA = "20260610_0800_MN00000_FAX00001_aabb1234"


def _done_run(db: sqlite3.Connection, pipeline_type: str, params: dict) -> str:
    run_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO pipeline_runs"
        " (id, pipeline_type, status, params, output_path, log_file, created_at, updated_at, created_by)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (run_id, pipeline_type, "done", json.dumps(params),
         f"/mnt/i/pipeline/output/nanopore_processed/outputs_{pipeline_type}/X_{RA}",
         f"/ODIN/logs/{run_id}_{pipeline_type}.log", _NOW, _NOW, "test"),
    )
    db.execute(
        "INSERT INTO pipeline_run_accessions (pipeline_run_id, run_accession) VALUES (?,?)",
        (run_id, RA),
    )
    db.commit()
    return run_id


@pytest.mark.parametrize(
    ("recorded", "expect_pooled"),
    [("pool", True), ("per_barcode", False), (None, True)],
)
def test_amr_reexport_passes_the_recorded_replicate_mode(
    client: TestClient, db: sqlite3.Connection, recorded: str | None, expect_pooled: bool
) -> None:
    params = {"auto_merge": False}
    if recorded:
        params["amr_replicates"] = recorded
    run_id = _done_run(db, "wf_metagenomics_amr", params)
    seen: dict = {}

    def fake(db_factory, run_accessions, outdir, log_path, *, pool_replicates):
        seen.update(run_accessions=run_accessions, pool_replicates=pool_replicates)
        return lambda: None

    with patch("backend.app.api.pipeline._make_amr_postprocess_fn", side_effect=fake):
        r = client.post(f"/api/pipeline/runs/{run_id}/postprocess")

    assert r.status_code == 202, r.text
    assert seen == {"run_accessions": [RA], "pool_replicates": expect_pooled}


def test_taxprofiler_reexport_still_passes_its_options(client: TestClient, db: sqlite3.Connection) -> None:
    run_id = _done_run(db, "taxprofiler", {"pipeline_options": {"save_reads": True}})
    seen: dict = {}

    def fake(db_factory, run_accessions, outdir, log_path, *, pipeline_options):
        seen.update(pipeline_options=pipeline_options)
        return lambda: None

    with patch("backend.app.api.pipeline._make_taxprofiler_postprocess_fn", side_effect=fake):
        r = client.post(f"/api/pipeline/runs/{run_id}/postprocess")

    assert r.status_code == 202, r.text
    assert seen == {"pipeline_options": {"save_reads": True}}


def test_reexport_of_an_unsupported_type_is_refused(client: TestClient, db: sqlite3.Connection) -> None:
    run_id = _done_run(db, "mpox", {})
    assert client.post(f"/api/pipeline/runs/{run_id}/postprocess").status_code == 409
