"""
Post-taxprofiler read extraction.

Python equivalent of pipeline_scripts/scripts/extract_reads.sh. Runs minimap2
and samtools via ``bash -c`` so that tools installed in WSL are reachable both
when the backend runs natively on Windows (local dev) and when it runs in the
Linux Docker container (production).

All paths passed to shell commands are converted with ``_sp()``
(Windows ``D:\\...`` → WSL ``/mnt/d/...``; Linux paths unchanged).

For each Kraken2 classified FASTQ found under the taxprofiler output directory:
  1. Resolve the reference genome accession from the extract_target option
  2. Download the reference from NCBI (cached in store_dir/pathogen_refs/)
  3. Align all classified reads to the reference with minimap2
  4. Extract reads that mapped (samtools view -F 4)
  5. Compute coverage statistics (samtools coverage)
  6. Look up sample metadata from the ODIN DB
  7. Write a plain-text confidence report
"""

from __future__ import annotations

import glob
import os
import re
import shlex
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..utils import coerce_path_for_shell


def _sp(path: str) -> str:
    """Convert a filesystem path to a shell-safe WSL path.

    On Windows: ``D:\\foo\\bar`` → ``/mnt/d/foo/bar``
    On Linux/Docker: identity (already a POSIX path).
    """
    return coerce_path_for_shell(path)


def _bash(cmd: str, **kwargs) -> subprocess.CompletedProcess:
    """Run *cmd* inside ``bash -c``.  Extra kwargs are passed to subprocess.run."""
    return subprocess.run(["bash", "-c", cmd], **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# Known taxon shortcuts  (matches extract_reads.sh case statement and
# config/extract_targets.csv defaults)
# ─────────────────────────────────────────────────────────────────────────────

_KNOWN_TARGETS: dict[str, dict[str, str]] = {
    "mpox_cladeia": {
        "ref_accession": "NC_003310.1",
        "taxon_taxid": "10244",
        "taxon_sci_name": "Monkeypox virus",
    },
    "mpox_cladeib": {
        "ref_accession": "PP899475.1",
        "taxon_taxid": "10244",
        "taxon_sci_name": "Monkeypox virus",
    },
    "mpox_cladeii": {
        "ref_accession": "NC_063383.1",
        "taxon_taxid": "10244",
        "taxon_sci_name": "Monkeypox virus",
    },
}

_LABEL_ALIASES: dict[str, str] = {
    "cladei":        "mpox_cladeia",
    "cladeia":       "mpox_cladeia",
    "mpox-clade-i":  "mpox_cladeia",
    "mpox-clade-ia": "mpox_cladeia",
    "mpox-cladei":   "mpox_cladeia",
    "mpox-cladeia":  "mpox_cladeia",
    "cladeib":       "mpox_cladeib",
    "mpox-clade-ib": "mpox_cladeib",
    "mpox-cladeib":  "mpox_cladeib",
    "cladeii":       "mpox_cladeii",
    "mpox-clade-ii": "mpox_cladeii",
    "mpox-cladeii":  "mpox_cladeii",
}


# ─────────────────────────────────────────────────────────────────────────────
# Reference resolution
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TaxonTarget:
    """The taxon being extracted: how to align it and how to recognise it.

    `taxid` and `sci_name` may be empty — a custom target has neither, and runs
    launched before the extract_targets CSV grew those columns stored neither.
    """

    label: str
    ref_accession: str
    taxid: str = ""
    sci_name: str = ""


def _resolve_ref(pipeline_options: dict) -> TaxonTarget:
    """Resolve the extract_target option into the taxon to extract."""
    raw = (pipeline_options.get("extract_target") or "").strip().lower()
    canonical = _LABEL_ALIASES.get(raw, raw)

    if canonical == "custom":
        return TaxonTarget(
            label=(pipeline_options.get("extract_taxon_label") or "custom").strip(),
            ref_accession=(pipeline_options.get("extract_ref_accession") or "").strip(),
        )

    # Prefer values stored in pipeline_options at launch time (populated from the
    # extract_targets CSV since the taxon_taxid/taxon_sci_name columns were added).
    ref_accession = (pipeline_options.get("extract_ref_accession") or "").strip()
    if ref_accession:
        return TaxonTarget(
            label=canonical,
            ref_accession=ref_accession,
            taxid=(pipeline_options.get("taxon_taxid") or "").strip(),
            sci_name=(pipeline_options.get("taxon_sci_name") or "").strip(),
        )

    # Backward compat: look up from built-in table for runs launched before the
    # CSV columns were added (ref_accession was not stored in pipeline_options then).
    info = _KNOWN_TARGETS.get(canonical)
    if info:
        return TaxonTarget(
            label=canonical,
            ref_accession=info["ref_accession"],
            taxid=info["taxon_taxid"],
            sci_name=info["taxon_sci_name"],
        )

    return TaxonTarget(label=canonical, ref_accession=canonical)


# ─────────────────────────────────────────────────────────────────────────────
# Reference FASTA download / cache
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_ref(ref_accession: str, store_dir: str, log_fh) -> str:
    """Download reference FASTA from NCBI if not already cached. Returns path."""
    ref_dir = Path(store_dir) / "pathogen_refs"
    ref_dir.mkdir(parents=True, exist_ok=True)
    ref_fasta = ref_dir / f"{ref_accession}.fasta"

    if ref_fasta.exists() and ref_fasta.stat().st_size > 0:
        _log(log_fh, f"Using cached reference: {ref_fasta}")
        return str(ref_fasta)

    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        f"?db=nuccore&id={ref_accession}&rettype=fasta&retmode=text"
    )
    _log(log_fh, f"Downloading reference {ref_accession} from NCBI...")
    log_fh.flush()

    tmp = str(ref_fasta) + ".tmp"
    # curl runs inside bash so it inherits WSL's CA cert setup; path converted to WSL format
    cmd = f"curl -fsSL {shlex.quote(url)} -o {shlex.quote(_sp(tmp))}"
    ret = _bash(cmd, stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL).returncode

    if ret != 0 or not Path(tmp).exists() or Path(tmp).stat().st_size == 0:
        Path(tmp).unlink(missing_ok=True)
        raise RuntimeError(
            f"Failed to download reference {ref_accession} from NCBI. "
            "Check network / proxy / accession."
        )
    Path(tmp).rename(ref_fasta)
    _log(log_fh, f"Reference saved to: {ref_fasta}")
    return str(ref_fasta)


# ─────────────────────────────────────────────────────────────────────────────
# Sample metadata lookup from the ODIN DB
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_sample_metadata(db_path: str, barcode: str, run_accession: str) -> dict:
    """Query the ODIN DB for barcode+run metadata. Returns {} on any failure."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                s.sample_code,
                s.sampling_date,
                s.sample_type,
                lv.description      AS sample_type_desc,
                s.partner_sample_code,
                s.comments_sampling AS comments,
                si.id               AS site_id,
                si.site,
                si.city,
                si.country
            FROM nanopore_runs nr
            JOIN nanopore_run_accessions nra ON nr.accession_id = nra.id
            JOIN samples s  ON nr.sample_id = s.id
            LEFT JOIN sites si ON s.site_id = si.id
            LEFT JOIN lookup_values lv
                   ON lv.code = s.sample_type AND lv.list = 'sample_type'
            WHERE nra.run_accession = ? AND nr.barcode = ?
            LIMIT 1
            """,
            (run_accession, barcode),
        ).fetchone()
        conn.close()
        return dict(row) if row else {}
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Logging helper
# ─────────────────────────────────────────────────────────────────────────────

def _log(log_fh, msg: str) -> None:
    log_fh.write(f"[ODIN-EXTRACT] {msg}\n".encode())


# ─────────────────────────────────────────────────────────────────────────────
# Report writers  (match extract_reads.sh format exactly)
# ─────────────────────────────────────────────────────────────────────────────

def _meta_block(barcode: str, run_accession: str, meta: dict) -> str:
    lines = ["Sample metadata", "---------------"]
    if meta:
        lines += [
            f"Sample code:         {meta.get('sample_code', '')}",
            f"Sample type:         {meta.get('sample_type', '')}  —  {meta.get('sample_type_desc', '')}",
            f"Sampling date:       {meta.get('sampling_date', '')}",
            f"Site:                {meta.get('site_id', '')}  {('/ ' + meta['site']) if meta.get('site') else ''}",
            f"Location:            {(meta.get('city', '') + ', ') if meta.get('city') else ''}{meta.get('country', '')}",
        ]
        if meta.get("partner_sample_code"):
            lines.append(f"Partner sample code: {meta['partner_sample_code']}")
        if meta.get("comments"):
            lines.append(f"Sampling notes:      {meta['comments']}")
    else:
        lines += [
            f"Barcode:             {barcode}",
            f"Run accession:       {run_accession}",
            "(No metadata record found for this barcode / run combination)",
        ]
    return "\n".join(lines)


@dataclass(frozen=True)
class SampleIds:
    """Identifiers derived from one classified-FASTQ filename."""

    sample_name: str
    barcode: str
    run_accession: str


@dataclass(frozen=True)
class ExtractionPaths:
    """The output files written for one (sample, taxon) extraction.

    ``all_bam`` is the intermediate alignment of *all* classified reads; it is
    deleted once the mapped subset has been split out into ``bam``/``fastq``.
    """

    fastq: str
    bam: str
    report: str
    all_bam: str


# Column offsets in a `samtools coverage` row:
# rname startpos endpos numreads covbases coverage meandepth meanbaseq meanmapq
_COV_COVBASES = 4
_COV_COVERAGE = 5
_COV_MEANDEPTH = 6
_COV_MEANMAPQ = 8
_COV_MIN_FIELDS = 9


@dataclass(frozen=True)
class CoverageStats:
    """Coverage of the reference by the extracted reads, as reported by samtools.

    Values are kept as strings because they are only rendered into the confidence
    report, and empty means "samtools gave us no usable row".
    """

    covered_bases: str = ""
    pct_covered: str = ""
    mean_depth: str = ""
    mean_mapq: str = ""


def _count_fastq_reads(fastq_path: str, log_fh=None) -> int:
    """Count reads in a FASTQ file.

    FASTQ stores four lines per read (header, sequence, ``+`` separator,
    quality), so the read count is the line count divided by four. Counting
    lines that start with ``@`` is wrong: a quality line may also begin with
    ``@`` (ASCII 64 == Phred+33 quality 31) — and no content pattern can tell a
    header from a quality line, so parsers read fixed 4-line blocks. See Cock
    et al., Nucleic Acids Research 38(6):1767-1771, 2010 (doi:10.1093/nar/gkp1137).

    A line count that is not a multiple of four means a truncated/corrupt file
    (the upstream ``samtools fastq`` always writes complete 4-line records). This
    count is informational (it feeds a confidence report), so we do not abort the
    run over it: we log a warning when a log handle is given and return the floor.
    """
    with open(fastq_path) as f:
        line_count = sum(1 for _ in f)
    if line_count % 4 != 0 and log_fh is not None:
        _log(
            log_fh,
            f"WARNING: {fastq_path} has {line_count} lines, not a multiple of 4 "
            "(possibly truncated); reported read count is approximate.",
        )
    return line_count // 4


def _write_empty_report(
    output_report: str,
    target: TaxonTarget,
    ids: SampleIds,
    classified_fastq: str, report_file: str,
    meta: dict,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with open(output_report, "w") as f:
        f.write(f"""\
Read Extraction and Alignment Confidence Report
===============================================
Taxon:           {target.label}
Reference:       {target.ref_accession}
Barcode:         {ids.barcode}
Run:             {ids.run_accession}
Report date:     {now}

{_meta_block(ids.barcode, ids.run_accession, meta)}

Input files
-----------
Kraken2 report:   {report_file}
Classified FASTQ: {classified_fastq}

RESULT: No reads from the Kraken2 classified set mapped to {target.ref_accession}.
""")


def _write_full_report(
    output_report: str,
    target: TaxonTarget,
    ids: SampleIds,
    ref_length: str,
    classified_fastq: str, report_file: str, ref_fasta: str,
    kraken_taxon_count: str,
    read_count: int,
    coverage: CoverageStats,
    output_fastq: str, output_bam: str,
    meta: dict,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    taxon_id_str = target.taxid or target.label
    sci_str = f" ({target.sci_name})" if target.sci_name else ""
    with open(output_report, "w") as f:
        f.write(f"""\
Read Extraction and Alignment Confidence Report
===============================================
Taxon:               {target.label}
Reference:           {target.ref_accession}
Reference length:    {ref_length or 'unknown'} bp
Barcode:             {ids.barcode}
Run:                 {ids.run_accession}
Report date:         {now}

{_meta_block(ids.barcode, ids.run_accession, meta)}

Input files
-----------
Kraken2 report:      {report_file}
Classified FASTQ:    {classified_fastq}
Reference FASTA:     {ref_fasta}

Kraken2 classification
----------------------
Reads classified as taxid {taxon_id_str}{sci_str}: {kraken_taxon_count}

Alignment to reference (minimap2 map-ont)
-----------------------------------------
All Kraken2-classified reads aligned to {target.ref_accession}.
Reads that mapped (extracted as {target.label} candidates): {read_count}
Reference bases covered:   {coverage.covered_bases} / {ref_length or '?'} bp  ({coverage.pct_covered}%)
Mean sequencing depth:     {coverage.mean_depth}x
Mean mapping quality:      {coverage.mean_mapq}

Output files
------------
Extracted reads (FASTQ): {output_fastq}
Alignment (BAM):         {output_bam}
This report:             {output_report}

Interpretation guidance
-----------------------
High confidence (genuine signal):
  - Genome coverage > 10% AND mean depth > 1x AND mean mapq > 20
  - Multiple reads aligned across distinct genome regions

Low confidence (possible noise or mis-classification):
  - Genome coverage < 5% with reads clustering in one region
  - Mean mapping quality < 20 (reads map equally well elsewhere)
  - Fewer than ~10 aligned reads

Note: This analysis uses reads assigned by Kraken2 during metagenomic screening.
It is intended as a screening indicator, not a diagnostic confirmation.
""")


# ─────────────────────────────────────────────────────────────────────────────
# Per-sample extraction
# ─────────────────────────────────────────────────────────────────────────────

def _derive_sample_ids(classified_fastq: str, db_name: str) -> SampleIds:
    """Derive the sample name, barcode and run accession from a classified FASTQ path.

    taxprofiler names these files ``{barcode}_{run_accession}_{db_name}.kraken2.
    classified.fastq.gz``, so the barcode is the first underscore-separated part of
    the remaining stem and the run accession is everything after it.
    """
    stem = re.sub(
        r"\.kraken2\.classified\.fastq\.gz$", "", Path(classified_fastq).name
    )
    db_suffix = f"_{db_name}"
    sample_name = stem[: -len(db_suffix)] if stem.endswith(db_suffix) else stem
    barcode = sample_name.split("_")[0]
    return SampleIds(
        sample_name=sample_name,
        barcode=barcode,
        run_accession=sample_name[len(barcode) + 1:],
    )


def _build_extraction_paths(
    output_dir: Path, sample_name: str, taxon_label: str
) -> ExtractionPaths:
    """Name the four output files for one (sample, taxon) extraction."""
    return ExtractionPaths(
        fastq=str(output_dir / f"{sample_name}.{taxon_label}.fastq"),
        bam=str(output_dir / f"{sample_name}.{taxon_label}.bam"),
        report=str(output_dir / f"{sample_name}.{taxon_label}_confidence_report.txt"),
        all_bam=str(output_dir / f"{sample_name}.all_classified.bam"),
    )


def _align_classified_reads(
    ref_fasta: str, classified_fastq: str, all_bam: str, sample_name: str, log_fh
) -> None:
    """Align every Kraken2-classified read to the reference, sorted into *all_bam*.

    Paths are converted to WSL form so minimap2/samtools running under ``bash -c``
    can open them when the backend runs natively on Windows.
    """
    _log(log_fh, "Step 1: Aligning classified reads with minimap2...")
    align_cmd = (
        f"minimap2 -ax map-ont -t 4 {shlex.quote(_sp(ref_fasta))} {shlex.quote(_sp(classified_fastq))}"
        f" | samtools sort -o {shlex.quote(_sp(all_bam))}"
    )
    _log(log_fh, f"+ bash -c {align_cmd!r}")
    log_fh.flush()
    ret = _bash(align_cmd, stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL).returncode
    if ret != 0:
        raise RuntimeError(f"minimap2 | samtools sort failed (exit {ret}) for {sample_name}")

    _bash(
        f"samtools index {shlex.quote(_sp(all_bam))}",
        stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, check=True,
    )


def _extract_mapped_reads(paths: ExtractionPaths, log_fh) -> None:
    """Keep only the reads that mapped (``samtools view -F 4``), as BAM and FASTQ."""
    _log(log_fh, "Step 2: Extracting mapped reads...")
    _bash(
        f"samtools view -b -F 4 -o {shlex.quote(_sp(paths.bam))} {shlex.quote(_sp(paths.all_bam))}",
        stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, check=True,
    )
    _bash(
        f"samtools index {shlex.quote(_sp(paths.bam))}",
        stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, check=True,
    )
    # Shell redirect so samtools writes the FASTQ to the file in WSL's view of the path
    _bash(
        f"samtools fastq {shlex.quote(_sp(paths.bam))} > {shlex.quote(_sp(paths.fastq))}",
        stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, check=True,
    )


def _remove_intermediate_bam(all_bam: str) -> None:
    """Delete the all-classified-reads BAM; only the mapped subset is kept."""
    for path in (all_bam, all_bam + ".bai"):
        try:
            os.remove(path)
        except OSError:
            pass


def _parse_coverage(coverage_stdout: str) -> CoverageStats:
    """Parse the single data row of ``samtools coverage`` output.

    Fields stay empty when there is no data row or the row is short: these numbers
    only decorate the confidence report, so a partial report beats failing the run.
    """
    lines = coverage_stdout.strip().splitlines()
    if len(lines) <= 1:
        return CoverageStats()
    fields = lines[1].split("\t")
    if len(fields) < _COV_MIN_FIELDS:
        return CoverageStats()
    return CoverageStats(
        covered_bases=fields[_COV_COVBASES],
        pct_covered=fields[_COV_COVERAGE],
        mean_depth=fields[_COV_MEANDEPTH],
        mean_mapq=fields[_COV_MEANMAPQ],
    )


def _parse_ref_length(header_stdout: str) -> str:
    """Return the reference length from a BAM header's ``@SQ ... LN:`` field."""
    return next(
        (re.search(r"LN:(\d+)", ln).group(1) for ln in header_stdout.splitlines() if "LN:" in ln),
        "",
    )


def _kraken_taxon_count(report_file: str, target: TaxonTarget) -> str:
    """Sum the Kraken2 report's read counts for this taxon.

    Mirrors the awk logic of extract_reads.sh: column 2 is the clade read count,
    column 5 the taxid and column 6 the taxon name. Matching by taxid is exact;
    without one (custom targets) it falls back to a case-insensitive name match.
    """
    if not os.path.isfile(report_file):
        return "0"
    if target.taxid:
        awk_prog = f"$5 == {target.taxid} {{sum+=$2}} END {{print sum+0}}"
    else:
        label_esc = target.label.replace("'", "'\\''")
        awk_prog = f'tolower($6) ~ tolower("{label_esc}") {{sum+=$2}} END {{print sum+0}}'
    result = _bash(
        f"awk {shlex.quote(awk_prog)} {shlex.quote(_sp(report_file))}",
        capture_output=True, text=True,
    )
    return result.stdout.strip() or "0"


def _collect_alignment_stats(output_bam: str, log_fh) -> tuple[CoverageStats, str]:
    """Return (coverage stats, reference length) for the extracted-reads BAM."""
    _log(log_fh, "Step 3: Coverage statistics...")
    cov = _bash(
        f"samtools coverage {shlex.quote(_sp(output_bam))}",
        capture_output=True, text=True,
    )
    hdr = _bash(
        f"samtools view -H {shlex.quote(_sp(output_bam))}",
        capture_output=True, text=True,
    )
    return _parse_coverage(cov.stdout), _parse_ref_length(hdr.stdout)


def _extract_one(
    classified_fastq: str,
    db_name: str,
    target: TaxonTarget,
    ref_fasta: str,
    outdir: str,
    db_path: str,
    log_fh,
) -> None:
    """Run extraction for a single classified FASTQ (one barcode, one DB)."""
    ids = _derive_sample_ids(classified_fastq, db_name)
    report_file = re.sub(r"\.classified\.fastq\.gz$", ".kraken2.report.txt", classified_fastq)

    output_dir = Path(outdir) / f"{target.label}_analysis"
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = _build_extraction_paths(output_dir, ids.sample_name, target.label)

    _log(log_fh, f"--- {ids.sample_name} ({db_name}) ---")
    log_fh.flush()

    meta = _lookup_sample_metadata(db_path, ids.barcode, ids.run_accession)

    _align_classified_reads(ref_fasta, classified_fastq, paths.all_bam, ids.sample_name, log_fh)
    _extract_mapped_reads(paths, log_fh)

    # Count reads using Python (reads the file via the native filesystem path)
    read_count = _count_fastq_reads(paths.fastq, log_fh)
    _log(log_fh, f"Reads mapped to {target.ref_accession}: {read_count}")
    _remove_intermediate_bam(paths.all_bam)

    if read_count == 0:
        _write_empty_report(paths.report, target, ids, classified_fastq, report_file, meta)
        _log(log_fh, f"No reads mapped. Report: {paths.report}")
        return

    coverage, ref_length = _collect_alignment_stats(paths.bam, log_fh)
    kraken_taxon_count = _kraken_taxon_count(report_file, target)

    _log(log_fh, "Step 4: Writing confidence report...")
    _write_full_report(
        paths.report,
        target,
        ids,
        ref_length,
        classified_fastq, report_file, ref_fasta,
        kraken_taxon_count,
        read_count,
        coverage,
        paths.fastq, paths.bam,
        meta,
    )
    _log(log_fh, f"Report: {paths.report}")
    _log(log_fh, f"Done: {ids.sample_name}")


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_extraction(
    outdir: str,
    pipeline_options: dict,
    db_path: str,
    log_path: Path,
    store_dir: Optional[str] = None,
) -> None:
    """
    Run post-taxprofiler read extraction for all barcodes.

    outdir          — taxprofiler --outdir value (kraken2/ lives inside here)
    pipeline_options — from the pipeline run params (extract_target, etc.)
    db_path         — path to the ODIN SQLite database
    log_path        — existing pipeline run log file (appended to)
    store_dir       — directory for caching reference FASTAs; defaults to
                      {db_path_dir}/store_dir/
    """
    target = _resolve_ref(pipeline_options)

    if not target.ref_accession:
        raise RuntimeError(
            "No reference accession for extraction — set extract_target to a known "
            "label (mpox_cladeia / mpox_cladeib / mpox_cladeii) or set "
            "extract_target=custom and provide extract_ref_accession."
        )

    if not store_dir:
        store_dir = str(Path(db_path).parent / "store_dir")

    with log_path.open("ab") as log_fh:
        _log(log_fh, "=== Starting read extraction ===")
        _log(log_fh, f"Target:    {target.label}")
        _log(log_fh, f"Reference: {target.ref_accession}")
        log_fh.flush()

        ref_fasta = _fetch_ref(target.ref_accession, store_dir, log_fh)

        pattern = os.path.join(outdir, "kraken2", "*", "*.kraken2.classified.fastq.gz")
        classified_files = sorted(glob.glob(pattern))

        if not classified_files:
            _log(log_fh, f"No classified FASTQs found at: {pattern}")
            _log(log_fh, "Did you run taxprofiler with save_reads enabled?")
            return

        _log(log_fh, f"Found {len(classified_files)} classified FASTQ file(s)")

        errors: list[str] = []
        for fastq_path in classified_files:
            db_name = Path(fastq_path).parent.name
            try:
                _extract_one(
                    fastq_path, db_name, target, ref_fasta, outdir, db_path, log_fh
                )
            except Exception as exc:
                msg = f"ERROR processing {Path(fastq_path).name}: {exc}"
                _log(log_fh, msg)
                errors.append(msg)

        if errors:
            _log(log_fh, f"=== Extraction complete with {len(errors)} error(s) ===")
        else:
            _log(log_fh, "=== Extraction complete ===")
