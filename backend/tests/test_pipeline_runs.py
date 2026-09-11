"""
Tests for /api/pipeline — list, get, merge decisions, launch validation,
cancel, delete-record, delete-workdir.

Launch tests that need real directories and executor calls are run with
executor.launch mocked so no background threads or subprocesses start.
"""

import json
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ── helpers ───────────────────────────────────────────────────────────────────

_NOW = "2025-01-01T00:00:00.000Z"


def _insert_run(
    db: sqlite3.Connection,
    *,
    pipeline_type: str = "taxprofiler",
    status: str = "queued",
    run_accessions: list[str] | None = None,
    params: dict | None = None,
    log_file: str = "/tmp/test.log",
    output_path: str = "/tmp/out",
    work_dir: str = "/tmp/work",
) -> str:
    run_id = str(uuid.uuid4())
    db.execute(
        """INSERT INTO pipeline_runs
           (id, pipeline_type, status, params,
            log_file, output_path, work_dir, created_at, updated_at, created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            pipeline_type,
            status,
            json.dumps(params or {"resume": False}),
            log_file,
            output_path,
            work_dir,
            _NOW,
            _NOW,
            "test",
        ),
    )
    for ra in (run_accessions or ["ERR000001"]):
        db.execute(
            "INSERT OR IGNORE INTO nanopore_run_accessions (id, run_accession) VALUES (?, ?)",
            (str(uuid.uuid4()), ra),
        )
        db.execute(
            "INSERT INTO pipeline_run_accessions (pipeline_run_id, run_accession) VALUES (?, ?)",
            (run_id, ra),
        )
    db.commit()
    return run_id


def _set_config(db: sqlite3.Connection, key: str, value: str) -> None:
    entry_id = str(uuid.uuid4())
    db.execute(
        """INSERT OR REPLACE INTO config_values
           (id, key, value, created_at, updated_at, created_by)
           VALUES (?,?,?,?,?,?)""",
        (entry_id, key, value, _NOW, _NOW, "test"),
    )
    db.commit()


# ── list runs ─────────────────────────────────────────────────────────────────


def test_list_runs_empty(client: TestClient) -> None:
    r = client.get("/api/pipeline/runs")
    assert r.status_code == 200
    assert r.json() == []


def test_list_runs_returns_inserted(client: TestClient, db: sqlite3.Connection) -> None:
    _insert_run(db)
    _insert_run(db, pipeline_type="wf_metagenomics_ssu", run_accessions=["ERR000002"])
    r = client.get("/api/pipeline/runs")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_list_runs_newest_first(client: TestClient, db: sqlite3.Connection) -> None:
    _insert_run(db, run_accessions=["ERR000001"])
    # Insert a second run with a later timestamp
    run_id_2 = str(uuid.uuid4())
    db.execute(
        """INSERT INTO pipeline_runs
           (id, pipeline_type, status, params,
            log_file, output_path, work_dir, created_at, updated_at, created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id_2, "taxprofiler", "completed",
            json.dumps({"resume": False}),
            "/tmp/b.log", "/tmp/b", "/tmp/bw",
            "2025-06-01T00:00:00.000Z", "2025-06-01T00:00:00.000Z", "test",
        ),
    )
    db.execute(
        "INSERT OR IGNORE INTO nanopore_run_accessions (id, run_accession) VALUES (?, ?)",
        (str(uuid.uuid4()), "ERR000002"),
    )
    db.execute(
        "INSERT INTO pipeline_run_accessions (pipeline_run_id, run_accession) VALUES (?, ?)",
        (run_id_2, "ERR000002"),
    )
    db.commit()
    runs = client.get("/api/pipeline/runs").json()
    assert runs[0]["run_accessions"] == ["ERR000002"]


# ── get single run ────────────────────────────────────────────────────────────


def test_get_run_not_found(client: TestClient) -> None:
    r = client.get("/api/pipeline/runs/does-not-exist")
    assert r.status_code == 404


def test_get_run_returns_correct_data(client: TestClient, db: sqlite3.Connection) -> None:
    run_id = _insert_run(db, pipeline_type="wf_metagenomics_amr", run_accessions=["ERR111"])
    r = client.get(f"/api/pipeline/runs/{run_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["pipeline_type"] == "wf_metagenomics_amr"
    assert body["run_accessions"] == ["ERR111"]
    assert body["status"] == "queued"


# ── active run ────────────────────────────────────────────────────────────────


def test_active_run_404_when_idle(client: TestClient) -> None:
    """Returns 404 when no pipeline is running."""
    with patch("backend.app.pipeline.executor.current_run_id", return_value=None):
        r = client.get("/api/pipeline/runs/active")
    assert r.status_code == 404


# ── merge decisions ───────────────────────────────────────────────────────────


def test_set_merge_decision(client: TestClient) -> None:
    r = client.put(
        "/api/pipeline/nanopore/ERR123456/merge-decision",
        json={"auto_merge": True},
    )
    assert r.status_code == 204


def test_set_merge_decision_then_read_back(client: TestClient, db: sqlite3.Connection) -> None:
    client.put(
        "/api/pipeline/nanopore/ERR123456/merge-decision",
        json={"auto_merge": True},
    )
    row = db.execute(
        "SELECT auto_merge FROM nanopore_merge_decisions WHERE run_accession = ?",
        ("ERR123456",),
    ).fetchone()
    assert row is not None
    assert row["auto_merge"] == 1


def test_update_merge_decision(client: TestClient, db: sqlite3.Connection) -> None:
    client.put("/api/pipeline/nanopore/ERR123456/merge-decision", json={"auto_merge": True})
    client.put("/api/pipeline/nanopore/ERR123456/merge-decision", json={"auto_merge": False})
    row = db.execute(
        "SELECT auto_merge FROM nanopore_merge_decisions WHERE run_accession = ?",
        ("ERR123456",),
    ).fetchone()
    assert row["auto_merge"] == 0


def test_clear_merge_decision(client: TestClient, db: sqlite3.Connection) -> None:
    client.put("/api/pipeline/nanopore/ERR123456/merge-decision", json={"auto_merge": True})
    r = client.delete("/api/pipeline/nanopore/ERR123456/merge-decision")
    assert r.status_code == 204
    row = db.execute(
        "SELECT auto_merge FROM nanopore_merge_decisions WHERE run_accession = ?",
        ("ERR123456",),
    ).fetchone()
    assert row is None


def test_clear_nonexistent_merge_decision_is_204(client: TestClient) -> None:
    """Clearing a decision that was never set is idempotent."""
    r = client.delete("/api/pipeline/nanopore/ERR999/merge-decision")
    assert r.status_code == 204


# ── launch — validation failures (before executor is touched) ─────────────────


def test_launch_invalid_pipeline_type(client: TestClient) -> None:
    r = client.post(
        "/api/pipeline/runs",
        json={"pipeline_type": "imaginary_pipeline", "run_accessions": ["ERR1"]},
    )
    assert r.status_code == 422


def test_launch_empty_run_accessions(client: TestClient) -> None:
    r = client.post(
        "/api/pipeline/runs",
        json={"pipeline_type": "taxprofiler", "run_accessions": []},
    )
    assert r.status_code == 422


def test_launch_missing_settings_returns_422(
    client: TestClient, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without minknow_dir / output_dir in settings the endpoint must return 422."""
    # Suppress env-var defaults so settings are genuinely absent
    monkeypatch.delenv("ODIN_PIPELINE_ROOT", raising=False)
    r = client.post(
        "/api/pipeline/runs",
        json={"pipeline_type": "taxprofiler", "run_accessions": ["ERR000001"]},
    )
    assert r.status_code == 422
    # output_dir is checked before minknow_dir in the endpoint
    assert "output_dir" in r.json()["detail"]


def test_launch_mpox_missing_clade(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    """mpox requires clade + scheme_version."""
    _set_config(db, "minknow_dir", str(tmp_path / "minknow"))
    _set_config(db, "output_dir", str(tmp_path / "output"))
    r = client.post(
        "/api/pipeline/runs",
        json={
            "pipeline_type": "mpox",
            "run_accessions": ["ERR000001"],
            "clade": None,
            "scheme_version": None,
        },
    )
    assert r.status_code == 422
    assert "clade" in r.json()["detail"]


def test_launch_duplicate_returns_409(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """A second identical queued/running job must be rejected with 409."""
    _insert_run(db, pipeline_type="taxprofiler", status="queued", run_accessions=["ERR000001"])
    r = client.post(
        "/api/pipeline/runs",
        json={"pipeline_type": "taxprofiler", "run_accessions": ["ERR000001"]},
    )
    assert r.status_code == 409


# ── launch — happy path (mocked executor) ────────────────────────────────────


def test_launch_creates_db_record(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Successful launch creates a pipeline_run row with status=queued."""
    _set_config(db, "minknow_dir", str(tmp_path / "minknow"))
    _set_config(db, "output_dir", str(tmp_path / "output"))
    # Satisfy pre-launch checks: databases table must have an entry for taxprofiler
    db.execute(
        "INSERT INTO databases (id, tool, db_name, db_params, db_path, created_at, updated_at)"
        " VALUES (?, 'kraken2', 'test_db', '', '/db', ?, ?)",
        (str(uuid.uuid4()), _NOW, _NOW),
    )
    # Satisfy pre-launch check: pathogens_file must be configured and exist
    pathogens_file = tmp_path / "pathogens.csv"
    pathogens_file.write_text("dummy")
    _set_config(db, "pathogens_file", str(pathogens_file))
    # Create a non-empty fastq_pass directory so the FASTQ check passes
    fastq_pass = tmp_path / "minknow" / "ERR000001" / "fastq_pass"
    fastq_pass.mkdir(parents=True)
    (fastq_pass / "barcode01").mkdir()
    db.commit()

    with patch("backend.app.api.pipeline.executor.launch"):
        r = client.post(
            "/api/pipeline/runs",
            json={"pipeline_type": "taxprofiler", "run_accessions": ["ERR000001"]},
        )

    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "queued"
    assert body["pipeline_type"] == "taxprofiler"
    assert body["run_accessions"] == ["ERR000001"]
    assert body["params"]["resume"] is False


def test_launch_stores_resume_flag(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    """The user's resume choice is persisted in params."""
    _set_config(db, "minknow_dir", str(tmp_path / "minknow"))
    _set_config(db, "output_dir", str(tmp_path / "output"))
    # Satisfy pre-launch checks
    db.execute(
        "INSERT INTO databases (id, tool, db_name, db_params, db_path, created_at, updated_at)"
        " VALUES (?, 'kraken2', 'test_db', '', '/db', ?, ?)",
        (str(uuid.uuid4()), _NOW, _NOW),
    )
    pathogens_file = tmp_path / "pathogens.csv"
    pathogens_file.write_text("dummy")
    _set_config(db, "pathogens_file", str(pathogens_file))
    fastq_pass = tmp_path / "minknow" / "ERR000001" / "fastq_pass"
    fastq_pass.mkdir(parents=True)
    (fastq_pass / "barcode01").mkdir()
    db.commit()

    with patch("backend.app.api.pipeline.executor.launch"):
        r = client.post(
            "/api/pipeline/runs",
            json={
                "pipeline_type": "taxprofiler",
                "run_accessions": ["ERR000001"],
                "resume": True,
            },
        )

    assert r.status_code == 201
    assert r.json()["params"]["resume"] is True


# ── cancel ────────────────────────────────────────────────────────────────────


def test_cancel_not_found(client: TestClient) -> None:
    r = client.delete("/api/pipeline/runs/does-not-exist")
    assert r.status_code == 404


def test_cancel_finished_run_returns_409(
    client: TestClient, db: sqlite3.Connection
) -> None:
    run_id = _insert_run(db, status="completed")
    r = client.delete(f"/api/pipeline/runs/{run_id}")
    assert r.status_code == 409


def test_cancel_queued_run(client: TestClient, db: sqlite3.Connection) -> None:
    run_id = _insert_run(db, status="queued")
    with patch("backend.app.api.pipeline.executor.cancel"):
        r = client.delete(f"/api/pipeline/runs/{run_id}")
    assert r.status_code == 204
    row = db.execute(
        "SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row["status"] == "cancelled"


# ── delete record ─────────────────────────────────────────────────────────────


def test_delete_record_not_found(client: TestClient) -> None:
    r = client.delete("/api/pipeline/runs/does-not-exist/record")
    assert r.status_code == 404


def test_delete_record_blocked_when_queued(
    client: TestClient, db: sqlite3.Connection
) -> None:
    run_id = _insert_run(db, status="queued")
    r = client.delete(f"/api/pipeline/runs/{run_id}/record")
    assert r.status_code == 409


def test_delete_record_blocked_when_running(
    client: TestClient, db: sqlite3.Connection
) -> None:
    run_id = _insert_run(db, status="running")
    r = client.delete(f"/api/pipeline/runs/{run_id}/record")
    assert r.status_code == 409


def test_delete_record_removes_finished_run(
    client: TestClient, db: sqlite3.Connection
) -> None:
    run_id = _insert_run(db, status="completed")
    r = client.delete(f"/api/pipeline/runs/{run_id}/record")
    assert r.status_code == 204
    row = db.execute(
        "SELECT id FROM pipeline_runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row is None


def test_delete_record_cancelled_run(
    client: TestClient, db: sqlite3.Connection
) -> None:
    run_id = _insert_run(db, status="cancelled")
    r = client.delete(f"/api/pipeline/runs/{run_id}/record")
    assert r.status_code == 204


# ── delete workdir ────────────────────────────────────────────────────────────


def test_delete_workdir_not_found(client: TestClient) -> None:
    r = client.delete("/api/pipeline/runs/does-not-exist/workdir")
    assert r.status_code == 404


def test_delete_workdir_blocked_when_running(
    client: TestClient, db: sqlite3.Connection
) -> None:
    run_id = _insert_run(db, status="running")
    r = client.delete(f"/api/pipeline/runs/{run_id}/workdir")
    assert r.status_code == 409


def test_delete_workdir_no_output_path_returns_404(
    client: TestClient, db: sqlite3.Connection
) -> None:
    run_id = str(uuid.uuid4())
    db.execute(
        """INSERT INTO pipeline_runs
           (id, pipeline_type, status, params,
            log_file, output_path, work_dir, created_at, updated_at, created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id, "taxprofiler", "completed",
            json.dumps({"resume": False}),
            "/tmp/t.log", None, None,
            _NOW, _NOW, "test",
        ),
    )
    db.execute(
        "INSERT OR IGNORE INTO nanopore_run_accessions (id, run_accession) VALUES (?, ?)",
        (str(uuid.uuid4()), "ERR1"),
    )
    db.execute(
        "INSERT INTO pipeline_run_accessions (pipeline_run_id, run_accession) VALUES (?, ?)",
        (run_id, "ERR1"),
    )
    db.commit()
    r = client.delete(f"/api/pipeline/runs/{run_id}/workdir")
    assert r.status_code == 404


def test_delete_workdir_removes_directory(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    # Create a realistic output structure
    outdir = tmp_path / "nanopore_processed" / "SAMPLE1" / "outputs_taxprofiler"
    work_dir = tmp_path / "nanopore_processed" / "SAMPLE1" / "work"
    outdir.mkdir(parents=True)
    work_dir.mkdir(parents=True)
    (work_dir / "some_task").mkdir()

    run_id = _insert_run(db, status="completed", output_path=str(outdir))
    r = client.delete(f"/api/pipeline/runs/{run_id}/workdir")
    assert r.status_code == 204
    assert not work_dir.exists()


# ── requeue reconciliation of unrunnable pending rows ─────────────────────────


def test_requeue_fails_ghost_queued_row(tmp_path: Path) -> None:
    """A queued row that can never run (no accessions, no log file) must be
    marked failed at startup, not silently skipped — a skipped row blocks the
    duplicate-launch guard for its accession forever (field incident 2026-09-02
    after a container-engine switchover)."""
    from backend.app.api.pipeline import requeue_pending_runs
    from backend.tests.conftest import _SCHEMA

    db_file = tmp_path / "odin.db"
    con = sqlite3.connect(db_file)
    con.executescript(_SCHEMA.read_text(encoding="utf-8"))
    con.execute(
        "INSERT INTO pipeline_runs (id, pipeline_type, status, created_at, updated_at)"
        " VALUES ('ghost-1', 'taxprofiler', 'queued', '2026-09-01T12:00:00Z', '2026-09-01T12:00:00Z')"
    )
    con.commit()
    con.close()

    requeued = requeue_pending_runs(str(db_file))

    con = sqlite3.connect(db_file)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT status, exit_code, finished_at FROM pipeline_runs WHERE id='ghost-1'").fetchone()
    con.close()
    assert requeued == 0
    assert row["status"] == "failed"
    assert row["exit_code"] == -1
    assert row["finished_at"] is not None
