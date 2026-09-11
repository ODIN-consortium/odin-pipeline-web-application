"""Tests for pipeline/log_hints.py — error hint detection from Nextflow logs."""

from pathlib import Path

from backend.app.pipeline.log_hints import detect_error_hint

# ── helpers ──────────────────────────────────────────────────────────────────


def _write_log(tmp_path: Path, content: str) -> Path:
    p = tmp_path / ".nextflow.log"
    p.write_text(content, encoding="utf-8")
    return p


# ── squirrel high-N detector ──────────────────────────────────────────────────


def test_squirrel_high_n_all_excluded_returns_hint(tmp_path):
    log = _write_log(
        tmp_path,
        "2 sequences flagged as high N content (>0.2): squirrel/suggested_to_exclude.csv\n"
        "Aligned sequences written to: squirrel/all_consensus.aln.fasta\n"
        'ValueError: No records found in handle\n',
    )
    hint = detect_error_hint(str(log))
    assert hint is not None
    assert "2" in hint
    assert "high n content" in hint.lower()
    assert "mpox" in hint.lower()


def test_squirrel_high_n_count_in_hint(tmp_path):
    log = _write_log(
        tmp_path,
        "5 sequences flagged as high N content (>0.2): squirrel/suggested_to_exclude.csv\n"
        "ValueError: No records found in handle\n",
    )
    hint = detect_error_hint(str(log))
    assert hint is not None
    assert "5" in hint


def test_squirrel_high_n_without_count_line(tmp_path):
    """If the count line is absent but the crash is present, still return a hint."""
    log = _write_log(tmp_path, "ValueError: No records found in handle\n")
    hint = detect_error_hint(str(log))
    assert hint is not None
    assert "high-n" in hint.lower() or "n content" in hint.lower()


def test_no_records_without_squirrel_context_still_matches(tmp_path):
    """The crash string alone is enough — it's specific to this squirrel scenario."""
    log = _write_log(tmp_path, "Bio.AlignIO ValueError: No records found in handle\n")
    hint = detect_error_hint(str(log))
    assert hint is not None


def test_successful_run_no_hint(tmp_path):
    log = _write_log(tmp_path, "Workflow completed successfully\n")
    assert detect_error_hint(str(log)) is None


# ── existing pattern detectors ────────────────────────────────────────────────


def test_ssl_certificate_error(tmp_path):
    log = _write_log(tmp_path, "CERTIFICATE_VERIFY_FAILED peer unverified\n")
    hint = detect_error_hint(str(log))
    assert hint is not None
    assert "certificate" in hint.lower()


def test_git_lfs_pointer(tmp_path):
    log = _write_log(tmp_path, "gzip: file.gz: not in gzip format\n")
    hint = detect_error_hint(str(log))
    assert hint is not None
    assert "git lfs" in hint.lower()


def test_no_space_left(tmp_path):
    log = _write_log(tmp_path, "write /mnt/d/work/file: no space left on device\n")
    hint = detect_error_hint(str(log))
    assert hint is not None
    assert "disk" in hint.lower() or "space" in hint.lower()


def test_insufficient_cpus(tmp_path):
    log = _write_log(
        tmp_path,
        "ERROR ~ Error executing process > 'fastcat (1)'\n"
        "Caused by:\n"
        "  Process requirement exceeds available CPUs -- req: 4; avail: 2\n",
    )
    hint = detect_error_hint(str(log))
    assert hint is not None
    assert "cpu" in hint.lower()
    assert "docker" in hint.lower()


def test_missing_log_file_returns_none():
    assert detect_error_hint("/nonexistent/path/.nextflow.log") is None


def test_none_log_file_returns_none():
    assert detect_error_hint(None) is None
