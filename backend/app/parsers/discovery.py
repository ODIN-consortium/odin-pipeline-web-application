"""
Pure filesystem discovery — no database access.

Scans MinKNOW and Biomeme data directories and returns structured results
that callers can cross-reference against the database.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

_FASTQ_SUFFIXES = (".fastq.gz", ".fastq")


# ─────────────────────────────────────────────────────────────────────────────
# MinKNOW / Nanopore
# ─────────────────────────────────────────────────────────────────────────────

# Expected on-disk layout:
#   {minknow_dir}/
#     {run_accession}/
#       fastq_pass/
#         {barcode}/
#           *.fastq.gz


@dataclass
class BarcodeOnDisk:
    barcode: str
    fastq_count: int


@dataclass
class RunOnDisk:
    run_accession: str
    has_fastq_pass: bool
    barcodes: list[BarcodeOnDisk] = field(default_factory=list)
    run_path: str = ""  # relative path from minknow_dir, e.g. "Lib2BF_ODIN/Lib2BFrun/20250912_..."


def find_run_dir(minknow_dir: Path, run_accession: str) -> Path | None:
    """
    Search *minknow_dir* (up to 3 levels deep) for a directory whose name
    matches *run_accession* and that contains a ``fastq_pass`` subfolder.
    Returns the absolute Path if found, else None.
    """

    def _search(directory: Path, depth: int) -> Path | None:
        if depth <= 0:
            return None
        for entry in directory.iterdir():
            if not entry.is_dir():
                continue
            if entry.name == run_accession and (entry / "fastq_pass").is_dir():
                return entry
            found = _search(entry, depth - 1)
            if found:
                return found
        return None

    if not minknow_dir.is_dir():
        return None
    return _search(minknow_dir, depth=3)


def scan_minknow_dir(minknow_dir: Path) -> list[RunOnDisk]:
    """
    Scan a MinKNOW data root and return one RunOnDisk per run directory.

    MinKNOW may nest runs under experiment/sample grouping folders, so the
    depth of ``fastq_pass`` varies:

      depth 1  {minknow_dir}/{run_id}/fastq_pass/
      depth 2  {minknow_dir}/{experiment}/{run_id}/fastq_pass/
      depth 3  {minknow_dir}/{experiment}/{sample}/{run_id}/fastq_pass/

    A directory that contains ``fastq_pass`` is treated as a run directory;
    its name becomes ``run_accession``.  The search stops at that node and
    does not descend further.  Non-directories and symlinks are ignored.
    """
    if not minknow_dir.is_dir():
        return []

    results: list[RunOnDisk] = []

    def _search(directory: Path, rel_prefix: str, depth: int) -> None:
        if depth <= 0:
            return
        for entry in sorted(directory.iterdir()):
            if not entry.is_dir():
                continue
            rel = f"{rel_prefix}{entry.name}" if rel_prefix else entry.name
            if (entry / "fastq_pass").is_dir():
                results.append(
                    RunOnDisk(
                        run_accession=entry.name,
                        has_fastq_pass=True,
                        barcodes=scan_run_barcodes(entry),
                        run_path=rel,
                    )
                )
            else:
                _search(entry, rel + "/", depth - 1)

    _search(minknow_dir, "", depth=3)
    return results


def scan_run_barcodes(run_dir: Path) -> list[BarcodeOnDisk]:
    """
    Scan the fastq_pass subdirectories of a single run directory.
    Returns one BarcodeOnDisk per barcode sub-directory found.
    Shared by both the full-scan and the per-run readiness endpoints.
    """
    fastq_pass = run_dir / "fastq_pass"
    if not fastq_pass.is_dir():
        return []
    result: list[BarcodeOnDisk] = []
    for bc_dir in sorted(fastq_pass.iterdir()):
        if not bc_dir.is_dir():
            continue
        count = sum(
            1
            for f in os.scandir(bc_dir)
            if not f.is_dir(follow_symlinks=False) and f.name.endswith(_FASTQ_SUFFIXES)
        )
        result.append(BarcodeOnDisk(barcode=bc_dir.name, fastq_count=count))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Biomeme
# ─────────────────────────────────────────────────────────────────────────────

# Expected on-disk layout:
#   {biomeme_dir}/
#     {country_code}/
#       {sampling_date}/    ← YYYYMMDD
#         *.csv  (or other data files)


@dataclass
class BiomemeDirOnDisk:
    """A leaf directory (country_code/sampling_date) that contains files."""

    country_code: str
    sampling_date: str
    relative_path: str  # "{country_code}/{sampling_date}"
    file_count: int
    run_names: list[str] = field(default_factory=list)  # xlsx file stems


def scan_biomeme_dir(biomeme_dir: Path) -> list[BiomemeDirOnDisk]:
    """
    Scan a Biomeme data root and return one entry per leaf directory that
    contains at least one file.  Two-level structure expected:
    {country_code}/{sampling_date}/.
    """
    if not biomeme_dir.is_dir():
        return []

    results: list[BiomemeDirOnDisk] = []
    for cc_dir in sorted(biomeme_dir.iterdir()):
        if not cc_dir.is_dir():
            continue
        for date_dir in sorted(cc_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            xlsx_files = sorted(
                f for f in date_dir.iterdir() if f.is_file() and f.suffix == ".xlsx"
            )
            if xlsx_files:
                results.append(
                    BiomemeDirOnDisk(
                        country_code=cc_dir.name,
                        sampling_date=date_dir.name,
                        relative_path=f"{cc_dir.name}/{date_dir.name}",
                        file_count=len(xlsx_files),
                        run_names=[f.stem for f in xlsx_files],
                    )
                )
    return results
