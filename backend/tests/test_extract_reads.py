"""Tests for backend/app/pipeline/extract_reads.py.

The first group is regression coverage for two bugs found in the 2026-07-24
maintainability review:
  * the read counter over-counted FASTQ records (quality lines can start with
    ``@``), and
  * the confidence-report writer crashed on a malformed f-string format spec
    whenever a taxid was present.

The later groups cover the pure helpers pulled out of ``_extract_one`` during the
D7 decomposition — filename parsing, samtools output parsing and target
resolution — none of which was reachable for testing while inlined.
"""

import io

from backend.app.pipeline.extract_reads import (
    CoverageStats,
    SampleIds,
    TaxonTarget,
    _build_extraction_paths,
    _count_fastq_reads,
    _derive_sample_ids,
    _parse_coverage,
    _parse_ref_length,
    _resolve_ref,
    _write_full_report,
)


def _write_fastq(path, records):
    """Write *records* (list of (header, seq, qual)) as a 4-line-per-read FASTQ."""
    with open(path, "w") as f:
        for header, seq, qual in records:
            f.write(f"@{header}\n{seq}\n+\n{qual}\n")


def test_count_fastq_reads_counts_records_not_at_lines(tmp_path):
    # Second read's quality line starts with '@' (a valid Phred+33 char). The
    # old startswith('@') counter returned 3 here; the correct answer is 2.
    fq = tmp_path / "reads.fastq"
    _write_fastq(
        fq,
        [
            ("read1", "ACGT", "IIII"),
            ("read2", "TTGCA", "@III!"),  # quality begins with '@'
        ],
    )
    assert _count_fastq_reads(str(fq)) == 2


def test_count_fastq_reads_empty_file(tmp_path):
    fq = tmp_path / "empty.fastq"
    fq.write_text("")
    assert _count_fastq_reads(str(fq)) == 0


def test_count_fastq_reads_truncated_warns_but_does_not_abort(tmp_path):
    # A line count that isn't a multiple of 4 means a truncated/corrupt file.
    # The count is informational, so it must NOT raise: it returns the floor and
    # logs a warning when a log handle is provided.
    fq = tmp_path / "truncated.fastq"
    fq.write_text("@r1\nACGT\n+\n")  # 3 lines: missing the quality line
    log = io.BytesIO()
    count = _count_fastq_reads(str(fq), log_fh=log)
    assert count == 0  # floor, pipeline continues
    assert b"WARNING" in log.getvalue()  # anomaly surfaced in the run log


def test_count_fastq_reads_truncated_without_log_is_silent(tmp_path):
    fq = tmp_path / "truncated.fastq"
    fq.write_text("@r1\nACGT\n+\n@r2\nTT\n+\nII\n")  # 7 lines
    assert _count_fastq_reads(str(fq)) == 1  # floor of 7//4, no crash


def _full_report(tmp_path, *, taxid):
    out = tmp_path / "report.txt"
    _write_full_report(
        str(out),
        TaxonTarget(
            label="Monkeypox virus",
            ref_accession="NC_063383.1",
            taxid=taxid,
            sci_name="Monkeypox virus",
        ),
        SampleIds(sample_name="barcode01_RUN123", barcode="barcode01", run_accession="RUN123"),
        "197209",            # ref_length
        "classified.fastq",  # classified_fastq
        "kraken.report",     # report_file
        "ref.fasta",         # ref_fasta
        "1234",              # kraken_taxon_count
        42,                  # read_count
        CoverageStats(
            covered_bases="195000", pct_covered="98.9", mean_depth="55.1", mean_mapq="60"
        ),
        "out.fastq", "out.bam",
        {},                  # meta (empty -> falls back to barcode/run block)
    )
    return out.read_text()


def test_write_full_report_with_taxid_does_not_crash(tmp_path):
    # Regression: the old f"{taxon_taxid:-{taxon_label}}" raised ValueError
    # (a sign is not allowed in a string format spec) whenever taxid was set.
    text = _full_report(tmp_path, taxid="10244")
    assert "taxid 10244" in text


def test_write_full_report_falls_back_to_label_without_taxid(tmp_path):
    text = _full_report(tmp_path, taxid="")
    assert "taxid Monkeypox virus" in text


def test_write_full_report_renders_coverage_stats(tmp_path):
    """Coverage numbers reach the report from the CoverageStats record."""
    text = _full_report(tmp_path, taxid="10244")
    assert "195000 / 197209 bp  (98.9%)" in text
    assert "Mean sequencing depth:     55.1x" in text
    assert "Mean mapping quality:      60" in text


# ── _derive_sample_ids — barcode/run split from the FASTQ filename ────────────


def test_derive_sample_ids_strips_db_suffix():
    ids = _derive_sample_ids(
        "/out/kraken2/pluspf/barcode01_RUN123_pluspf.kraken2.classified.fastq.gz",
        "pluspf",
    )
    assert ids.sample_name == "barcode01_RUN123"
    assert ids.barcode == "barcode01"
    assert ids.run_accession == "RUN123"


def test_derive_sample_ids_keeps_run_accession_underscores():
    """MinKNOW run accessions contain underscores — only the first part is the barcode."""
    ra = "20260610_0800_MN00000_FAX00001_aaaa1111"
    ids = _derive_sample_ids(
        f"/out/kraken2/db1/barcode07_{ra}_db1.kraken2.classified.fastq.gz", "db1"
    )
    assert ids.barcode == "barcode07"
    assert ids.run_accession == ra


def test_derive_sample_ids_without_matching_db_suffix():
    """When the filename does not end in _{db_name}, the whole stem is the sample."""
    ids = _derive_sample_ids(
        "/out/kraken2/db1/barcode01_RUN123.kraken2.classified.fastq.gz", "other_db"
    )
    assert ids.sample_name == "barcode01_RUN123"


# ── _build_extraction_paths ───────────────────────────────────────────────────


def test_extraction_paths_are_named_after_sample_and_taxon(tmp_path):
    paths = _build_extraction_paths(tmp_path, "barcode01_RUN123", "mpox_cladeii")
    assert paths.fastq.endswith("barcode01_RUN123.mpox_cladeii.fastq")
    assert paths.bam.endswith("barcode01_RUN123.mpox_cladeii.bam")
    assert paths.report.endswith("barcode01_RUN123.mpox_cladeii_confidence_report.txt")
    # The intermediate BAM is taxon-independent — it holds all classified reads.
    assert paths.all_bam.endswith("barcode01_RUN123.all_classified.bam")


# ── _parse_coverage — replaces magic tab-column indices ──────────────────────

_COV_HEADER = "#rname\tstartpos\tendpos\tnumreads\tcovbases\tcoverage\tmeandepth\tmeanbaseq\tmeanmapq"


def test_parse_coverage_reads_the_documented_columns():
    row = "NC_063383.1\t1\t197209\t40\t195000\t98.9\t55.1\t31.2\t60"
    stats = _parse_coverage(f"{_COV_HEADER}\n{row}\n")
    assert stats == CoverageStats(
        covered_bases="195000", pct_covered="98.9", mean_depth="55.1", mean_mapq="60"
    )


def test_parse_coverage_header_only_returns_empty_stats():
    assert _parse_coverage(f"{_COV_HEADER}\n") == CoverageStats()


def test_parse_coverage_empty_output_returns_empty_stats():
    assert _parse_coverage("") == CoverageStats()


def test_parse_coverage_short_row_returns_empty_stats():
    """A truncated row must not half-populate the report with shifted values."""
    assert _parse_coverage(f"{_COV_HEADER}\nNC_1\t1\t100\t5\n") == CoverageStats()


# ── _parse_ref_length ─────────────────────────────────────────────────────────


def test_parse_ref_length_from_sq_header():
    header = "@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:NC_063383.1\tLN:197209\n@PG\tID:minimap2\n"
    assert _parse_ref_length(header) == "197209"


def test_parse_ref_length_missing_returns_empty():
    assert _parse_ref_length("@HD\tVN:1.6\n") == ""


# ── _resolve_ref — target resolution ─────────────────────────────────────────


def test_resolve_ref_known_alias_maps_to_builtin_target():
    target = _resolve_ref({"extract_target": "cladeii"})
    assert target.label == "mpox_cladeii"
    assert target.ref_accession == "NC_063383.1"
    assert target.taxid == "10244"


def test_resolve_ref_prefers_values_stored_at_launch():
    target = _resolve_ref(
        {
            "extract_target": "mpox_cladeii",
            "extract_ref_accession": "PP899475.1",
            "taxon_taxid": "999",
            "taxon_sci_name": "Test virus",
        }
    )
    assert (target.ref_accession, target.taxid, target.sci_name) == (
        "PP899475.1",
        "999",
        "Test virus",
    )


def test_resolve_ref_custom_target_has_no_taxid():
    target = _resolve_ref(
        {
            "extract_target": "custom",
            "extract_ref_accession": "MN908947.3",
            "extract_taxon_label": "sars",
        }
    )
    assert (target.label, target.ref_accession) == ("sars", "MN908947.3")
    assert target.taxid == ""


def test_resolve_ref_unknown_target_falls_back_to_the_label():
    target = _resolve_ref({"extract_target": "something_else"})
    assert target.label == "something_else"
    assert target.ref_accession == "something_else"
