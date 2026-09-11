"""Tests for the discovery output-scan helpers.

`find_run_output_dir` coverage is regression protection for the bug where the
confidence-report endpoint used a different (incomplete) layout-detection
algorithm than the dashboard scan, causing "No output directory found" for the
standard flat layout.

The `_scan_output_state` / `_scan_artic_only_runs` sections characterise the
disk-scan half of `discover_nanopore`, which is otherwise only exercised
indirectly (and, for the artic tree, not at all) by the endpoint tests.
"""

import os
from pathlib import Path

from backend.app.api.discovery import (
    _params_extract_target,
    _scan_artic_only_runs,
    _scan_output_state,
    find_run_output_dir,
    find_run_output_dirs,
    scan_confidence_report_targets,
)

RA = "20260611_0800_MN00000_FAX00002_ccdd5678"


def _make_root(tmp_path: Path) -> Path:
    root = tmp_path / "nanopore_processed"
    root.mkdir()
    return root


# ── Layout 1: flat — {sampleName}_{run_accession} ─────────────────────────


def test_flat_layout_found(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    out_dir = root / "outputs_taxprofiler" / f"DemoSample_{RA}"
    out_dir.mkdir(parents=True)

    result = find_run_output_dir(root, RA)
    assert result == out_dir


def test_flat_layout_run_accession_only(tmp_path: Path) -> None:
    """file_identifier may equal the run_accession when sampleName is absent."""
    root = _make_root(tmp_path)
    out_dir = root / "outputs_taxprofiler" / RA
    out_dir.mkdir(parents=True)

    result = find_run_output_dir(root, RA)
    assert result == out_dir


def test_flat_layout_merged_runs(tmp_path: Path) -> None:
    """Merged runs use {sampleName}_{ra1}__{ra2} — both accessions should match."""
    other_ra = "20260612_0900_MN00000_FAX00003_aabb1234"
    root = _make_root(tmp_path)
    out_dir = root / "outputs_taxprofiler" / f"DemoSample_{RA}__{other_ra}"
    out_dir.mkdir(parents=True)

    assert find_run_output_dir(root, RA) == out_dir
    assert find_run_output_dir(root, other_ra) == out_dir


# ── Layout 2: nested — {file_id}/{run_accession} ──────────────────────────


def test_nested_layout_found(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    nested = root / "outputs_taxprofiler" / "SomeSample" / RA
    nested.mkdir(parents=True)

    result = find_run_output_dir(root, RA)
    assert result == nested


# ── Layout 3: direct — outputs_*/{run_accession} ──────────────────────────


def test_direct_layout_found(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    direct = root / "outputs_wf_artic-mpxv-nf" / RA
    direct.mkdir(parents=True)

    result = find_run_output_dir(root, RA)
    assert result == direct


# ── Non-outputs_* directories are ignored ─────────────────────────────────


def test_ignores_non_outputs_dirs(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    (root / "work" / f"DemoSample_{RA}").mkdir(parents=True)

    assert find_run_output_dir(root, RA) is None


# ── Not found ─────────────────────────────────────────────────────────────


def test_not_found_returns_none(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    (root / "outputs_taxprofiler" / "DemoSample_other_accession").mkdir(parents=True)

    assert find_run_output_dir(root, RA) is None


def test_empty_outputs_dir_returns_none(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    (root / "outputs_taxprofiler").mkdir()

    assert find_run_output_dir(root, RA) is None


def test_processed_root_missing_returns_none(tmp_path: Path) -> None:
    result = find_run_output_dir(tmp_path / "does_not_exist", RA)
    assert result is None


# ── find_run_output_dirs — a run processed by several pipelines ────────────


def test_find_all_output_dirs_across_pipelines(tmp_path: Path) -> None:
    """A run run through multiple pipelines has one dir under each outputs_*."""
    root = _make_root(tmp_path)
    tax = root / "outputs_taxprofiler" / f"DemoSample_{RA}"
    amr = root / "outputs_wf_metagenomics_amr" / f"DemoSample_{RA}"
    ssu = root / "outputs_wf_metagenomics_ssu" / f"DemoSample_{RA}"
    for d in (tax, amr, ssu):
        d.mkdir(parents=True)

    result = find_run_output_dirs(root, RA)
    # All three found, in sorted outputs_* name order (deterministic).
    assert result == [tax, amr, ssu]


def test_find_output_dir_returns_first_of_all(tmp_path: Path) -> None:
    """The singular helper returns the first of find_run_output_dirs."""
    root = _make_root(tmp_path)
    tax = root / "outputs_taxprofiler" / f"DemoSample_{RA}"
    amr = root / "outputs_wf_metagenomics_amr" / f"DemoSample_{RA}"
    for d in (tax, amr):
        d.mkdir(parents=True)

    assert find_run_output_dir(root, RA) == tax


# ── scan_confidence_report_targets — union across every output dir ─────────


def _write_report(analysis_dir: Path) -> None:
    analysis_dir.mkdir(parents=True)
    (analysis_dir / "barcode01.some_confidence_report.txt").write_text("ok")


def test_confidence_targets_scanned_across_all_output_dirs(tmp_path: Path) -> None:
    """Read-extraction reports under any pipeline output dir are all discovered."""
    root = _make_root(tmp_path)
    tax_run = root / "outputs_taxprofiler" / f"DemoSample_{RA}"
    amr_run = root / "outputs_wf_metagenomics_amr" / f"DemoSample_{RA}"
    tax_run.mkdir(parents=True)
    amr_run.mkdir(parents=True)
    _write_report(tax_run / "mpox_cladeia_analysis")
    _write_report(amr_run / "sars_analysis")

    targets = scan_confidence_report_targets(find_run_output_dirs(root, RA))
    assert targets == ["mpox_cladeia", "sars"]


def test_confidence_targets_ignores_analysis_dir_without_report(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    run_dir = root / "outputs_taxprofiler" / f"DemoSample_{RA}"
    (run_dir / "empty_analysis").mkdir(parents=True)  # no *_confidence_report.txt

    targets = scan_confidence_report_targets(find_run_output_dirs(root, RA))
    assert targets == []


def test_confidence_targets_deduplicates_same_target_in_two_dirs(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    tax_run = root / "outputs_taxprofiler" / f"DemoSample_{RA}"
    amr_run = root / "outputs_wf_metagenomics_amr" / f"DemoSample_{RA}"
    tax_run.mkdir(parents=True)
    amr_run.mkdir(parents=True)
    _write_report(tax_run / "mpox_cladeia_analysis")
    _write_report(amr_run / "mpox_cladeia_analysis")

    targets = scan_confidence_report_targets(find_run_output_dirs(root, RA))
    assert targets == ["mpox_cladeia"]


# ── _params_extract_target — read extract_target out of a params blob ──────


def test_extract_target_read_from_pipeline_options() -> None:
    blob = '{"pipeline_options": {"extract_target": "mpox_cladeia"}}'
    assert _params_extract_target(blob) == "mpox_cladeia"


def test_extract_target_absent_returns_none() -> None:
    assert _params_extract_target("{}") is None
    assert _params_extract_target(None) is None
    assert _params_extract_target('{"pipeline_options": {}}') is None


def test_extract_target_empty_string_is_normalised_to_none() -> None:
    assert _params_extract_target('{"pipeline_options": {"extract_target": ""}}') is None


def test_extract_target_malformed_json_returns_none() -> None:
    """A corrupt params column must not break the whole discovery scan."""
    assert _params_extract_target("{not json") is None


# ── _scan_artic_only_runs — the mpox/artic pre-scan ────────────────────────


def _make_artic_run(tmp_path: Path, accession: str, *, with_consensus: bool = True) -> Path:
    run_dir = _make_artic_root(tmp_path) / accession
    run_dir.mkdir(parents=True, exist_ok=True)
    if with_consensus:
        (run_dir / "all_consensus.fasta").write_text(">seq\nACGT\n")
    return run_dir


def _make_artic_root(tmp_path: Path) -> Path:
    return tmp_path / "nanopore_processed" / "outputs_wf_artic-mpxv-nf"


def test_artic_scan_without_output_dir_finds_nothing() -> None:
    assert _scan_artic_only_runs(None, {RA}) == ({}, set())
    assert _scan_artic_only_runs("", {RA}) == ({}, set())


def test_artic_scan_missing_root_finds_nothing(tmp_path: Path) -> None:
    assert _scan_artic_only_runs(str(tmp_path), {RA}) == ({}, set())


def test_artic_scan_reports_known_run_as_on_disk_only(tmp_path: Path) -> None:
    """A run already known from the DB/minknow scan is not a disk-only run."""
    _make_artic_run(tmp_path, RA)

    on_disk, disk_only = _scan_artic_only_runs(str(tmp_path), {RA})
    assert on_disk == {RA: True}
    assert disk_only == set()


def test_artic_scan_reports_unknown_run_as_disk_only(tmp_path: Path) -> None:
    """Runs existing only as artic output must still surface on the dashboard."""
    _make_artic_run(tmp_path, RA)

    on_disk, disk_only = _scan_artic_only_runs(str(tmp_path), set())
    assert on_disk == {RA: True}
    assert disk_only == {RA}


def test_artic_scan_ignores_dir_without_consensus_fasta(tmp_path: Path) -> None:
    """all_consensus.fasta is the completion marker — an empty dir does not count."""
    _make_artic_run(tmp_path, RA, with_consensus=False)

    assert _scan_artic_only_runs(str(tmp_path), set()) == ({}, set())


# ── _scan_output_state — per-accession output/postprocessing/artic state ───


def _write_multiqc(run_dir: Path) -> Path:
    report = run_dir / "multiqc" / "multiqc_report.html"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("<html/>")
    return report


def test_output_state_without_output_dir_keeps_artic_prescan() -> None:
    """No output_dir configured — the pre-scan result must survive untouched."""
    state = _scan_output_state(None, [RA], {RA: True})
    assert state.artic_on_disk == {RA: True}
    assert state.completed == {}
    assert state.postprocessing_fresh == {}
    assert state.confidence_targets == {}


def test_output_state_missing_processed_root_keeps_artic_prescan(tmp_path: Path) -> None:
    state = _scan_output_state(str(tmp_path), [RA], {RA: True})
    assert state.artic_on_disk == {RA: True}
    assert state.completed == {}


def test_output_state_run_without_output_is_incomplete(tmp_path: Path) -> None:
    _make_root(tmp_path)
    state = _scan_output_state(str(tmp_path), [RA], {})
    assert state.completed == {RA: False}
    assert state.postprocessing_fresh == {RA: None}


def test_output_state_multiqc_without_sentinel_is_complete_but_stale(tmp_path: Path) -> None:
    """Pipeline finished, post-processing never ran."""
    root = _make_root(tmp_path)
    _write_multiqc(root / "outputs_taxprofiler" / f"DemoSample_{RA}")

    state = _scan_output_state(str(tmp_path), [RA], {})
    assert state.completed == {RA: True}
    assert state.postprocessing_fresh == {RA: False}


def test_output_state_sentinel_newer_than_multiqc_is_fresh(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    run_dir = root / "outputs_taxprofiler" / f"DemoSample_{RA}"
    report = _write_multiqc(run_dir)
    sentinel = run_dir / ".odin_postprocessed"
    sentinel.write_text("done")
    os.utime(report, (1_700_000_000, 1_700_000_000))
    os.utime(sentinel, (1_700_000_100, 1_700_000_100))

    state = _scan_output_state(str(tmp_path), [RA], {})
    assert state.postprocessing_fresh == {RA: True}


def test_output_state_sentinel_older_than_multiqc_is_stale(tmp_path: Path) -> None:
    """A re-run of the pipeline invalidates an earlier post-processing pass."""
    root = _make_root(tmp_path)
    run_dir = root / "outputs_taxprofiler" / f"DemoSample_{RA}"
    report = _write_multiqc(run_dir)
    sentinel = run_dir / ".odin_postprocessed"
    sentinel.write_text("done")
    os.utime(sentinel, (1_700_000_000, 1_700_000_000))
    os.utime(report, (1_700_000_100, 1_700_000_100))

    state = _scan_output_state(str(tmp_path), [RA], {})
    assert state.postprocessing_fresh == {RA: False}


def test_output_state_squirrel_only_output_has_no_freshness(tmp_path: Path) -> None:
    """Post-processing does not apply to squirrel/mpox output."""
    root = _make_root(tmp_path)
    run_dir = root / "outputs_wf_artic-mpxv-nf" / RA
    (run_dir / "squirrel_output").mkdir(parents=True)

    state = _scan_output_state(str(tmp_path), [RA], {})
    assert state.completed == {RA: True}
    assert state.postprocessing_fresh == {RA: None}


def test_output_state_collects_confidence_targets(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    run_dir = root / "outputs_taxprofiler" / f"DemoSample_{RA}"
    _write_multiqc(run_dir)
    _write_report(run_dir / "sars_analysis")

    state = _scan_output_state(str(tmp_path), [RA], {})
    assert state.confidence_targets == {RA: ["sars"]}


def test_output_state_refreshes_artic_flag_per_accession(tmp_path: Path) -> None:
    """The per-accession scan overrides the pre-scan for accessions it covers."""
    _make_root(tmp_path)
    other_ra = "20260612_0900_MN00000_FAX00003_aabb1234"
    _make_artic_run(tmp_path, RA)

    state = _scan_output_state(str(tmp_path), [RA, other_ra], {RA: True})
    assert state.artic_on_disk == {RA: True, other_ra: False}
