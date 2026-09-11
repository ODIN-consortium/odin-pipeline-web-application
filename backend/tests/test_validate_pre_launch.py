"""Characterization tests for pipeline._validate_pre_launch.

Written test-first (Batch 3, D3), before decomposing the ~190-line function into
per-concern validators. They pin current behaviour via membership checks on the
returned error list so the decomposition can be verified behaviour-preserving.
The Nextflow-config and disk-dependent branches (fastq_pass, FASTA on disk) are
covered end-to-end by test_pipeline_runs; here we target the branch structure.
"""

import sqlite3
import uuid

from backend.app.api.pipeline import _validate_pre_launch


def _insert_run_accession(
    db: sqlite3.Connection, run_accession: str, protocol_id="SQK-LSK114", kit="SQK-LSK114"
) -> str:
    nra_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO nanopore_run_accessions"
        " (id, run_accession, protocol_id, sequencing_kit_id, created_at, updated_at, created_by, updated_by)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (nra_id, run_accession, protocol_id, kit,
         "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z", "test", "test"),
    )
    db.commit()
    return nra_id


def _insert_nanopore_run(db: sqlite3.Connection, nra_id: str, sample_id, barcode="barcode01") -> None:
    db.execute(
        "INSERT INTO nanopore_runs"
        " (id, accession_id, sample_id, barcode, created_at, updated_at, created_by)"
        " VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), nra_id, sample_id, barcode,
         "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z", "test"),
    )
    db.commit()


def test_squirrel_returns_only_its_own_error_and_stops(db: sqlite3.Connection):
    # squirrel skips the config check and returns early — no other checks run.
    errors = _validate_pre_launch(db, "squirrel", [], "", source_run_id=None)
    assert errors == ["squirrel requires source_run_id or run_accessions."]


def test_taxprofiler_flags_missing_databases(db: sqlite3.Connection):
    errors = _validate_pre_launch(db, "taxprofiler", [], "/nonexistent", source_run_id=None)
    assert any("No database entries are configured" in e for e in errors)


def test_flags_run_accession_missing_protocol_and_kit(db: sqlite3.Connection):
    _insert_run_accession(db, "RUN_META", protocol_id=None, kit=None)
    errors = _validate_pre_launch(db, "wf_metagenomics_amr", ["RUN_META"], "/nonexistent", source_run_id=None)
    assert any("protocol_id, sequencing_kit_id not set" in e for e in errors)


def test_flags_barcode_not_linked_to_sample(db: sqlite3.Connection):
    nra_id = _insert_run_accession(db, "RUN_UNLINKED")
    _insert_nanopore_run(db, nra_id, sample_id=None, barcode="barcode01")
    errors = _validate_pre_launch(db, "wf_metagenomics_amr", ["RUN_UNLINKED"], "/nonexistent", source_run_id=None)
    assert any("no sample linked" in e for e in errors)
