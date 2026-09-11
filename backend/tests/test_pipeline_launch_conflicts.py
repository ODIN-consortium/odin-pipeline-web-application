"""Characterization tests for the launch conflict rules of POST /api/pipeline/runs.

Three independent 409 rules guard a launch, and before these tests only the
first was covered:

1. same pipeline type + identical run set  → duplicate
2. same pipeline type + overlapping runs   → overlap
3. same pipeline type + same continuation group, when *either* side has merge
   enabled                                 → continuation-group lock

Rule 3 is the subtle one: it only applies when the requested launch or the
already-active run merges, and it deliberately excludes accessions that
overlap directly (those are rule 1/2's job). These tests pin that behaviour
before the endpoint is decomposed.

executor.launch is mocked throughout so no background thread starts.
"""

import json
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

_NOW = "2025-01-01T00:00:00.000Z"

# Two runs on the same flow cell, 2 h apart → continuation partners.
RA_A = "20260610_0800_MN00000_FAX00001_aaaa1111"
RA_B = "20260610_1200_MN00000_FAX00001_bbbb2222"
# A third run on a different flow cell → never a partner.
RA_C = "20260615_0800_MN00000_FAX00099_cccc3333"

_FLOW_CELL = "FAX00001"


# ── fixture helpers ───────────────────────────────────────────────────────────


def _set_config(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        """INSERT OR REPLACE INTO config_values
           (id, key, value, created_at, updated_at, created_by)
           VALUES (?,?,?,?,?,?)""",
        (str(uuid.uuid4()), key, value, _NOW, _NOW, "test"),
    )
    db.commit()


def _insert_active_run(
    db: sqlite3.Connection,
    run_accessions: list[str],
    *,
    pipeline_type: str = "taxprofiler",
    status: str = "queued",
    auto_merge: bool = False,
) -> str:
    """Insert a queued/running pipeline run covering *run_accessions*."""
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
            json.dumps({"resume": False, "auto_merge": auto_merge}),
            "/tmp/test.log",
            "/tmp/out",
            "/tmp/work",
            _NOW,
            _NOW,
            "test",
        ),
    )
    for ra in run_accessions:
        db.execute(
            "INSERT INTO pipeline_run_accessions (pipeline_run_id, run_accession) VALUES (?, ?)",
            (run_id, ra),
        )
    db.commit()
    return run_id


def _insert_disk_cache(
    db: sqlite3.Connection, run_accession: str, flow_cell: str, started: str, stopped: str
) -> None:
    db.execute(
        """INSERT INTO nanopore_disk_cache
           (run_accession, flow_cell_id, run_started, run_stopped, scanned_at)
           VALUES (?,?,?,?,?)""",
        (run_accession, flow_cell, started, stopped, _NOW),
    )
    db.commit()


def _make_continuation_pair(db: sqlite3.Connection) -> None:
    """Register RA_A and RA_B as same-flow-cell continuation partners (2 h gap)."""
    _insert_disk_cache(
        db, RA_A, _FLOW_CELL, "2026-06-10T08:00:00Z", "2026-06-10T10:00:00Z"
    )
    _insert_disk_cache(
        db, RA_B, _FLOW_CELL, "2026-06-10T12:00:00Z", "2026-06-10T14:00:00Z"
    )


def _set_merge_decision(db: sqlite3.Connection, run_accession: str, auto_merge: bool) -> None:
    db.execute(
        """INSERT OR REPLACE INTO nanopore_merge_decisions
           (run_accession, auto_merge, created_at, updated_at, created_by)
           VALUES (?,?,?,?,?)""",
        (run_accession, 1 if auto_merge else 0, _NOW, _NOW, "test"),
    )
    db.commit()


def _make_launchable(db: sqlite3.Connection, tmp_path: Path, *run_accessions: str) -> None:
    """Satisfy every pre-launch check so a taxprofiler launch can reach 201."""
    _set_config(db, "minknow_dir", str(tmp_path / "minknow"))
    _set_config(db, "output_dir", str(tmp_path / "output"))
    db.execute(
        "INSERT INTO databases (id, tool, db_name, db_params, db_path, created_at, updated_at)"
        " VALUES (?, 'kraken2', 'test_db', '', '/db', ?, ?)",
        (str(uuid.uuid4()), _NOW, _NOW),
    )
    pathogens_file = tmp_path / "pathogens.csv"
    pathogens_file.write_text("dummy")
    _set_config(db, "pathogens_file", str(pathogens_file))
    for ra in run_accessions:
        fastq_pass = tmp_path / "minknow" / ra / "fastq_pass"
        fastq_pass.mkdir(parents=True, exist_ok=True)
        (fastq_pass / "barcode01").mkdir(exist_ok=True)
    db.commit()


def _launch(client: TestClient, run_accessions: list[str], **extra):
    with patch("backend.app.api.pipeline.executor.launch"):
        return client.post(
            "/api/pipeline/runs",
            json={"pipeline_type": "taxprofiler", "run_accessions": run_accessions, **extra},
        )


# ── rule 2: overlapping run accessions ────────────────────────────────────────


def test_launch_overlapping_run_accessions_returns_409(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """A partial overlap with an active run is rejected, naming the shared runs."""
    active_id = _insert_active_run(db, [RA_A, RA_B])

    r = _launch(client, [RA_B, RA_C])

    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "overlapping run_accessions" in detail
    assert RA_B in detail
    assert active_id in detail


def test_launch_overlap_ignored_for_finished_runs(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Only queued/running runs lock accessions — a completed run does not."""
    _insert_active_run(db, [RA_A], status="completed")
    _make_launchable(db, tmp_path, RA_A)

    assert _launch(client, [RA_A]).status_code == 201


def test_launch_overlap_is_scoped_to_the_same_pipeline_type(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    """A different pipeline type may use the same run concurrently."""
    _insert_active_run(db, [RA_A], pipeline_type="wf_metagenomics_amr")
    _make_launchable(db, tmp_path, RA_A)

    assert _launch(client, [RA_A]).status_code == 201


# ── rule 3: continuation-group lock ───────────────────────────────────────────


def test_continuation_group_conflict_when_requested_launch_merges(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Requested run merges → its partner's active run locks the whole group.

    RA_A and RA_B share a flow cell. An active run holds RA_B only, so there is
    no direct accession overlap — the launch is blocked purely by the merge lock.
    """
    _make_continuation_pair(db)
    _set_merge_decision(db, RA_A, True)
    active_id = _insert_active_run(db, [RA_B])
    _make_launchable(db, tmp_path, RA_A)

    r = _launch(client, [RA_A])

    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "continuation group" in detail
    assert active_id in detail
    # The shared members are reported, excluding the directly-overlapping ones.
    assert RA_B in detail


def test_continuation_group_conflict_when_active_run_merges(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    """The lock is symmetric: merge enabled on the *active* run also blocks."""
    _make_continuation_pair(db)
    _set_merge_decision(db, RA_B, True)
    active_id = _insert_active_run(db, [RA_B], auto_merge=True)
    _make_launchable(db, tmp_path, RA_A)

    r = _launch(client, [RA_A])

    assert r.status_code == 409
    assert "continuation group" in r.json()["detail"]
    assert active_id in r.json()["detail"]


def test_continuation_group_conflict_from_active_params_auto_merge(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    """auto_merge recorded in the active run's params counts, with no stored decision."""
    _make_continuation_pair(db)
    _insert_active_run(db, [RA_B], auto_merge=True)
    _make_launchable(db, tmp_path, RA_A)

    r = _launch(client, [RA_A])

    assert r.status_code == 409
    assert "continuation group" in r.json()["detail"]


def test_no_continuation_conflict_when_neither_side_merges(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Without merge on either side, partners may be processed independently."""
    _make_continuation_pair(db)
    _insert_active_run(db, [RA_B])
    _make_launchable(db, tmp_path, RA_A)

    assert _launch(client, [RA_A]).status_code == 201


def test_no_continuation_conflict_when_merge_decision_is_declined(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    """An explicit "do not merge" decision does not arm the lock."""
    _make_continuation_pair(db)
    _set_merge_decision(db, RA_A, False)
    _insert_active_run(db, [RA_B])
    _make_launchable(db, tmp_path, RA_A)

    assert _launch(client, [RA_A]).status_code == 201


def test_no_continuation_conflict_for_unrelated_flow_cells(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Merge enabled, but the active run is on another flow cell → no shared group."""
    _make_continuation_pair(db)
    _insert_disk_cache(
        db, RA_C, "FAX00099", "2026-06-15T08:00:00Z", "2026-06-15T10:00:00Z"
    )
    _set_merge_decision(db, RA_A, True)
    _insert_active_run(db, [RA_C])
    _make_launchable(db, tmp_path, RA_A)

    assert _launch(client, [RA_A]).status_code == 201


def test_stale_merge_decision_without_partners_does_not_arm_the_lock(
    client: TestClient, db: sqlite3.Connection, tmp_path: Path
) -> None:
    """A merge decision left over from when partners existed is ignored.

    RA_A has a stored auto_merge=1 but no continuation partners on disk, so the
    launch must not be treated as merge-enabled (and auto_merge is stored False).
    """
    _set_merge_decision(db, RA_A, True)
    _insert_active_run(db, [RA_C])
    _make_launchable(db, tmp_path, RA_A)

    r = _launch(client, [RA_A])

    assert r.status_code == 201
    assert r.json()["params"]["auto_merge"] is False
