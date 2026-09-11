"""Content tests for the run manifest, around its two-phase write.

The manifest is written twice per run: once synchronously at launch (so the
endpoint never serves a previous run's file from a reused output directory),
and again from prepare_fn with the accessions actually used — which, for a
merged run, are only resolved when the run starts, potentially long after
launch (queue wait plus FASTQ concatenation).

Found by an operator: the launch-time manifest of a merged run listed one
accession and said "Merged: No", which read as a stale manifest from the
previous run. The launch-time write must say what it is — provisional — and
must not claim an unmerged run while the merge is still pending.
"""

import sqlite3
from pathlib import Path

from backend.app.api import pipeline as pipeline_api

RA1 = "20260610_0800_MN00000_FAX00001_aaaa1111"
RA2 = "20260611_0800_MN00000_FAX00001_bbbb2222"


def _write(
    db: sqlite3.Connection,
    tmp_path: Path,
    *,
    run_accessions: list[str],
    auto_merge: bool,
    provisional: bool,
) -> str:
    pipeline_api._write_run_manifest(
        db,
        str(tmp_path),
        "outputs_taxprofiler",
        "DemoSample_" + RA1,
        "",
        "taxprofiler",
        run_accessions,
        auto_merge,
        provisional=provisional,
    )
    manifest = (
        tmp_path / "nanopore_processed" / "outputs_taxprofiler" / ("DemoSample_" + RA1)
        / "run_manifest.txt"
    )
    return manifest.read_text(encoding="utf-8")


def test_final_manifest_lists_all_merged_accessions(db, tmp_path):
    text = _write(db, tmp_path, run_accessions=[RA1, RA2], auto_merge=True, provisional=False)

    assert RA1 in text
    assert RA2 in text
    assert "Merged:         Yes" in text
    assert "provisional" not in text


def test_final_unmerged_manifest_says_merged_no(db, tmp_path):
    text = _write(db, tmp_path, run_accessions=[RA1], auto_merge=False, provisional=False)

    assert "Merged:         No" in text
    assert "provisional" not in text


def test_provisional_manifest_says_it_will_be_rewritten(db, tmp_path):
    text = _write(db, tmp_path, run_accessions=[RA1], auto_merge=False, provisional=True)

    assert "provisional" in text
    assert "when the run starts" in text


def test_provisional_merge_is_pending_not_denied(db, tmp_path):
    # The launch request carries one accession; the merge partners are pulled in by
    # prepare_fn. Until then the manifest must not claim "Merged: No" for a run the
    # operator told to merge — that is exactly what read as a stale file.
    text = _write(db, tmp_path, run_accessions=[RA1], auto_merge=True, provisional=True)

    assert "Merged:         No" not in text
    assert "Merged:         Yes" in text
    assert "resolved when the run starts" in text


def test_prepare_rewrite_replaces_the_provisional_note(db, tmp_path):
    _write(db, tmp_path, run_accessions=[RA1], auto_merge=True, provisional=True)
    text = _write(db, tmp_path, run_accessions=[RA1, RA2], auto_merge=True, provisional=False)

    assert "provisional" not in text
    assert RA2 in text
    assert "2 run accession(s)" in text
