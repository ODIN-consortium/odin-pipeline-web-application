"""The run set a merge decision covers is ONE set, used everywhere.

Two same-flow-cell restarts related through the sample link (same barcode -> same
sample) must be in that set everywhere: the dashboard's "will merge FASTQ with", the
decision snapshot, the launch-time merge resolution and the FASTQ concatenation. Taking
continuation partners alone, or requiring a ``run_stopped`` that an aborted run never
has, yields merge toggled on, an empty snapshot, a permanent "set has changed" warning,
and a fraction of the reads processed.
"""

import sqlite3
import uuid

from fastapi.testclient import TestClient

from backend.app.api.pipeline import _resolve_auto_merge_map, _validate_pre_launch
from backend.app.db.queries import find_continuation_partners, get_merge_group
from backend.app.pipeline.samplesheet import _get_related_run_accessions

RA1 = "20250917_0225_MN00000_FBD00001_13346033"   # aborted: no run_stopped, no kit
RA2 = "20250917_0257_MN00000_FBD00001_f7b56bc7"   # aborted restart
RA3 = "20250917_0512_MN00000_FBD00001_9f562735"   # the run that completed
_NOW = "2025-01-01T00:00:00Z"


def _disk_cache(db, ra, flow_cell, started, stopped=None, kit=None):
    db.execute(
        "INSERT INTO nanopore_disk_cache"
        " (run_accession, flow_cell_id, run_started, run_stopped, sequencing_kit_id, run_name, scanned_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (ra, flow_cell, started, stopped, kit, "SAMPLE15_BF", _NOW),
    )
    db.commit()


def _registered_sample(db) -> str:
    site_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO sites (id, site_code, site, country, country_code, city_code, city, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (site_id, "BFOUSA", "SA", "Burkina Faso", "BF", "OU", "Ouagadougou", _NOW, _NOW),
    )
    sample_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO samples (id, site_id, sample_code, sample_type, sampling_date, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (sample_id, site_id, "BFOUSA_WW", "WW", "20250225", _NOW, _NOW),
    )
    db.commit()
    return sample_id


def _barcode01_linked(db, ra, sample_id) -> None:
    """Register barcode01 of *ra* as the given sample."""
    nra_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO nanopore_run_accessions"
        " (id, run_accession, protocol_id, sequencing_kit_id, created_at, updated_at, created_by, updated_by)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (nra_id, ra, "P2", "RB_GEN", _NOW, _NOW, "test", "test"),
    )
    db.execute(
        "INSERT INTO nanopore_runs (id, accession_id, sample_id, barcode, created_at, updated_at, created_by)"
        " VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), nra_id, sample_id, "barcode01", _NOW, _NOW, "test"),
    )
    db.commit()


# ── continuation detection ────────────────────────────────────────────────────


def test_aborted_run_without_stop_time_is_a_continuation_partner(db: sqlite3.Connection) -> None:
    """A MinKNOW abort leaves no final_summary: run_stopped and kit are NULL. The
    restart that follows must still be found; the gap is then measured from the start."""
    _disk_cache(db, RA1, "FBD00001", "2025-09-17T02:25:00")                       # aborted
    _disk_cache(db, RA2, "FBD00001", "2025-09-17T02:57:00")                       # aborted
    _disk_cache(db, RA3, "FBD00001", "2025-09-17T05:18:00", "2025-09-19T00:28:00", "SQK-RBK114-24")

    partners = {r["partner"] for r in find_continuation_partners(db, RA1, 48.0)}

    assert partners == {RA2, RA3}
    gaps = {r["partner"]: r["gap_hours"] for r in find_continuation_partners(db, RA1, 48.0)}
    assert 0 < gaps[RA2] < 1 and 2 < gaps[RA3] < 4


def test_restart_outside_the_window_is_not_a_partner(db: sqlite3.Connection) -> None:
    _disk_cache(db, RA1, "FBD00001", "2025-09-17T02:25:00")
    _disk_cache(db, RA3, "FBD00001", "2025-09-20T05:18:00", "2025-09-21T00:00:00", "SQK-RBK114-24")
    assert find_continuation_partners(db, RA1, 48.0) == []


# ── the merge group ───────────────────────────────────────────────────────────


def test_merge_group_is_continuations_plus_same_flow_cell_sample_links(db: sqlite3.Connection) -> None:
    same_flowcell_late = "20251001_1200_MN00000_FBD00001_bbbb2222"   # outside the window
    _disk_cache(db, RA1, "FBD00001", "2025-09-17T02:25:00")
    _disk_cache(db, RA2, "FBD00001", "2025-09-17T02:57:00")
    sample_id = _registered_sample(db)
    _barcode01_linked(db, RA1, sample_id)
    _barcode01_linked(db, same_flowcell_late, sample_id)

    assert get_merge_group(db, RA1, 48.0) == sorted([RA2, same_flowcell_late])
    assert get_merge_group(db, RA2, 48.0) == [RA1]


def test_sample_linked_run_on_another_flow_cell_is_never_merged(db: sqlite3.Connection) -> None:
    """A sample sequenced again on another flow cell is a separate sequencing event.
    Merging it concatenated the whole other run, barcode by barcode — samples that
    differ between the two runs included (TazaniaDAR2 + samplingDay1, 9 Oct 2026)."""
    other_flowcell = "20251001_1200_MN00000_FBD00002_aaaa1111"
    _disk_cache(db, RA1, "FBD00001", "2025-09-17T02:25:00")
    sample_id = _registered_sample(db)
    _barcode01_linked(db, RA1, sample_id)
    _barcode01_linked(db, other_flowcell, sample_id)    # same sample, weeks later, other flow cell

    assert get_merge_group(db, RA1, 48.0) == []
    assert get_merge_group(db, other_flowcell, 48.0) == []


def test_disk_cache_flow_cell_wins_over_the_accession(db: sqlite3.Connection) -> None:
    """The scan reads the flow cell from the run's own files; the accession is only a
    fallback for runs the scan has not seen."""
    other = "20251001_1200_MN00000_FBD00002_aaaa1111"
    _disk_cache(db, RA1, "FBD00001", "2025-09-17T02:25:00")
    _disk_cache(db, other, "FBD00001", "2025-10-01T12:00:00")   # files say FBD00001
    sample_id = _registered_sample(db)
    _barcode01_linked(db, RA1, sample_id)
    _barcode01_linked(db, other, sample_id)

    assert get_merge_group(db, RA1, 48.0) == [other]


# ── the same set at every consumer ────────────────────────────────────────────


def test_decision_snapshot_covers_sample_linked_runs(client: TestClient, db: sqlite3.Connection) -> None:
    """What the user is shown as related is what the decision is recorded against —
    otherwise readiness reports the decision stale on every visit."""
    sample_id = _registered_sample(db)
    _barcode01_linked(db, RA1, sample_id)
    _barcode01_linked(db, RA3, sample_id)          # no disk cache rows at all

    candidates = client.get(f"/api/pipeline/nanopore/{RA1}/merge-candidates").json()
    assert [c["run_accession"] for c in candidates["related_runs"]] == [RA3]

    assert client.put(f"/api/pipeline/nanopore/{RA1}/merge-decision", json={"auto_merge": True}).status_code == 204
    rows = {r["run_accession"]: r for r in db.execute("SELECT * FROM nanopore_merge_decisions")}
    assert rows[RA1]["auto_merge"] == 1 and rows[RA1]["related_snapshot"] == f'["{RA3}"]'
    assert rows[RA3]["auto_merge"] == 1 and rows[RA3]["related_snapshot"] == f'["{RA1}"]'


def test_launch_resolves_merge_for_a_sample_linked_group(client: TestClient, db: sqlite3.Connection) -> None:
    sample_id = _registered_sample(db)
    _barcode01_linked(db, RA1, sample_id)
    _barcode01_linked(db, RA3, sample_id)
    client.put(f"/api/pipeline/nanopore/{RA1}/merge-decision", json={"auto_merge": True})

    assert _resolve_auto_merge_map(db, [RA1]) == {RA1: True}


def test_concatenation_expands_to_the_whole_merge_group(db: sqlite3.Connection) -> None:
    _disk_cache(db, RA1, "FBD00001", "2025-09-17T02:25:00")
    _disk_cache(db, RA2, "FBD00001", "2025-09-17T02:57:00")
    sample_id = _registered_sample(db)
    _barcode01_linked(db, RA1, sample_id)
    _barcode01_linked(db, RA3, sample_id)          # RA3 not in the disk cache: sample link only

    assert _get_related_run_accessions(db, [RA1], auto_merge=True) == [RA1, RA2, RA3]
    assert _get_related_run_accessions(db, [RA1], auto_merge=False) == [RA1]


# ── pre-launch checks look at the whole merge group ──────────────────────────


def _link(db, ra, barcode, sample_id) -> None:
    """Register *barcode* of *ra* as *sample_id* (adds the accession when new)."""
    row = db.execute("SELECT id FROM nanopore_run_accessions WHERE run_accession = ?", (ra,)).fetchone()
    if row is None:
        nra_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO nanopore_run_accessions"
            " (id, run_accession, protocol_id, sequencing_kit_id, created_at, updated_at, created_by, updated_by)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (nra_id, ra, "P2", "RB_GEN", _NOW, _NOW, "test", "test"),
        )
    else:
        nra_id = row["id"]
    db.execute(
        "INSERT INTO nanopore_runs (id, accession_id, sample_id, barcode, created_at, updated_at, created_by)"
        " VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), nra_id, sample_id, barcode, _NOW, _NOW, "test"),
    )
    db.commit()


def _second_sample(db, code: str) -> str:
    site_id = db.execute("SELECT id FROM sites LIMIT 1").fetchone()["id"]
    sample_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO samples (id, site_id, sample_code, sample_type, sampling_date, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (sample_id, site_id, code, "RW", "20250903", _NOW, _NOW),
    )
    db.commit()
    return sample_id


def test_launch_refuses_a_merge_partner_whose_barcode_carries_another_sample(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """A one-run launch with merge on processes the whole group. Before 9 Oct 2026 this
    check saw the one run only, and a partner whose barcode02 held a different sample
    was concatenated into it unchecked (TazaniaDAR2 + samplingDay1)."""
    _disk_cache(db, RA1, "FBD00001", "2025-09-17T02:25:00", "2025-09-18T00:00:00", "SQK-RBK114-24")
    _disk_cache(db, RA2, "FBD00001", "2025-09-18T03:00:00", None, "SQK-RBK114-24")
    shared = _registered_sample(db)
    _link(db, RA1, "barcode01", shared)
    _link(db, RA2, "barcode01", shared)
    _link(db, RA1, "barcode02", _second_sample(db, "BFOUSA_DW"))
    _link(db, RA2, "barcode02", _second_sample(db, "BFOUSA_RW"))
    assert client.put(f"/api/pipeline/nanopore/{RA1}/merge-decision", json={"auto_merge": True}).status_code == 204

    errors = _validate_pre_launch(db, "wf_metagenomics_amr", [RA1], "/nonexistent")

    assert any("barcode02" in e and "different samples" in e for e in errors), errors


def test_launch_without_merge_checks_the_requested_run_only(
    client: TestClient, db: sqlite3.Connection
) -> None:
    _disk_cache(db, RA1, "FBD00001", "2025-09-17T02:25:00", "2025-09-18T00:00:00", "SQK-RBK114-24")
    _disk_cache(db, RA2, "FBD00001", "2025-09-18T03:00:00", None, "SQK-RBK114-24")
    shared = _registered_sample(db)
    _link(db, RA1, "barcode01", shared)
    _link(db, RA2, "barcode01", shared)
    _link(db, RA1, "barcode02", _second_sample(db, "BFOUSA_DW"))
    _link(db, RA2, "barcode02", _second_sample(db, "BFOUSA_RW"))
    assert client.put(f"/api/pipeline/nanopore/{RA1}/merge-decision", json={"auto_merge": False}).status_code == 204

    errors = _validate_pre_launch(db, "wf_metagenomics_amr", [RA1], "/nonexistent")

    assert not any("different samples" in e for e in errors), errors


def test_launch_refuses_replicate_numbers_that_differ_between_merged_loadings(
    client: TestClient, db: sqlite3.Connection
) -> None:
    _disk_cache(db, RA1, "FBD00001", "2025-09-17T02:25:00", "2025-09-18T00:00:00", "SQK-RBK114-24")
    _disk_cache(db, RA2, "FBD00001", "2025-09-18T03:00:00", None, "SQK-RBK114-24")
    shared = _registered_sample(db)
    for ra, numbers in ((RA1, {"barcode01": 1, "barcode02": 2}), (RA2, {"barcode01": 2, "barcode02": 1})):
        for barcode, n in numbers.items():
            _link(db, ra, barcode, shared)
            db.execute(
                "UPDATE nanopore_runs SET replicate = ? WHERE barcode = ? AND accession_id ="
                " (SELECT id FROM nanopore_run_accessions WHERE run_accession = ?)",
                (n, barcode, ra),
            )
    db.commit()
    assert client.put(f"/api/pipeline/nanopore/{RA1}/merge-decision", json={"auto_merge": True}).status_code == 204

    errors = _validate_pre_launch(db, "taxprofiler", [RA1], "/nonexistent")

    assert any("different replicate numbers" in e for e in errors), errors
