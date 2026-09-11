"""
Samplesheet builders — read from ODIN DB, produce CSV files for each pipeline.

All functions receive an open sqlite3.Connection and write a CSV to disk,
returning the absolute path as a string.  No Excel files are read.

Taxprofiler samplesheet format (nf-core/taxprofiler):
  sample, run_accession, instrument_platform, fastq_1

Mpox samplesheet format (artic-network/artic-mpxv-nf):
  barcode, alias, type

The concatenate step (merging FASTQ files across run_accessions) is handled
here inline. When merge is enabled we expand only to continuation partners
(same flow cell, compatible kit, within the configured split-run window),
then concatenate on disk.
"""

from __future__ import annotations

import csv
import gzip
import shutil
import sqlite3
from pathlib import Path
from typing import Optional

from ..db.queries import fetch_run_barcodes, find_continuation_partners, get_continuation_window
from ..parsers.discovery import find_run_dir
from ..utils import coerce_path, coerce_path_for_shell, run_accessions_slug

# ── Helpers ───────────────────────────────────────────────────────────────────


def _coerce(p: Optional[str]) -> Optional[Path]:
    if not p:
        return None
    return Path(coerce_path(p))


def _fastq_files(barcode_dir: Path) -> list[Path]:
    """Return sorted list of .fastq.gz (and .fastq) files in a barcode directory."""
    return sorted(f for f in barcode_dir.iterdir() if f.is_file() and f.suffix in {".gz", ".fastq"})


def _concatenate_barcodes(
    run_accessions: list[str],
    barcode_sample_map: dict[str, dict],  # barcode -> {sample_id, minknow_sample_id, alias, type}
    minknow_dir: Path,
    tmp_dir: Path,
    concat_tag: str,
) -> dict[str, Path]:
    """
    Concatenate FASTQ files from one or more run_accessions.
    One output file per barcode: {tmp_dir}/{barcode}_{concat_tag}.fastq.gz

    ``concat_tag`` is a short, deterministic identifier for the full run set.
    The complete accession list is intentionally not embedded in the filename
    to avoid Windows / Linux path-length failures for larger merged groups.

    Returns dict: barcode -> output_file_path.
    Only barcodes present in barcode_sample_map are written.
    """
    output_files: dict[str, Path] = {}
    tmp_dir.mkdir(parents=True, exist_ok=True)

    for barcode, meta in barcode_sample_map.items():
        sources: list[Path] = []
        for ra in run_accessions:
            run_dir = find_run_dir(minknow_dir, ra)
            if run_dir is None:
                continue
            bc_dir = run_dir / "fastq_pass" / barcode
            if bc_dir.is_dir():
                sources.extend(_fastq_files(bc_dir))

        if not sources:
            continue

        out_path = tmp_dir / f"{barcode}_{concat_tag}.fastq.gz"

        # Fast path: when all inputs are .fastq.gz, concatenate gzip members as raw bytes.
        # This avoids decompression/recompression and is much faster on large runs.
        if all(src.suffix == ".gz" for src in sources):
            with out_path.open("wb") as out_fh:
                for src in sources:
                    with src.open("rb") as in_fh:
                        shutil.copyfileobj(in_fh, out_fh, length=8 * 1024 * 1024)
        else:
            # Mixed plain/gz input fallback: normalise by writing a single gz stream.
            with gzip.open(out_path, "wb") as out_fh:
                for src in sources:
                    if src.suffix == ".gz":
                        with gzip.open(src, "rb") as in_fh:
                            shutil.copyfileobj(in_fh, out_fh)
                    else:  # plain .fastq
                        with src.open("rb") as in_fh:
                            shutil.copyfileobj(in_fh, out_fh)

        output_files[barcode] = out_path

    return output_files


def _get_related_run_accessions(
    db: sqlite3.Connection,
    run_accessions: list[str],
    auto_merge: bool,
) -> list[str]:
    """
    If auto_merge is True, expand to continuation partners only.
    Otherwise return as-is.
    """
    if not auto_merge:
        return run_accessions

    time_window = get_continuation_window(db)
    expanded = list(run_accessions)
    for ra in run_accessions:
        for r in find_continuation_partners(db, ra, time_window):
            if r["partner"]:
                expanded.append(r["partner"])
    return list(dict.fromkeys(expanded))


def _concat_tag(run_accessions: list[str]) -> str:
    """Short filesystem-safe tag for concatenated FASTQ filenames."""
    return run_accessions_slug(sorted(run_accessions), max_len=64)


def _db_path_label(db: sqlite3.Connection) -> str:
    row = db.execute("PRAGMA database_list").fetchone()
    if row and row[2]:
        return str(row[2])
    return "<unknown>"


# ── Taxprofiler ───────────────────────────────────────────────────────────────


def build_taxprofiler_samplesheet(
    db: sqlite3.Connection,
    run_accessions: list[str],
    auto_merge: bool,
    minknow_dir_stored: str,
    output_dir_stored: str,
    file_identifier: str,
    tmp_dir: Path,
) -> tuple[Path, Path, str | None, list[str]]:
    """
    Build the nf-core/taxprofiler samplesheet CSV.

    Returns (csv_path, tmp_dir, merge_note, all_accessions) — all_accessions is the
    full expanded list after merge resolution (may be longer than run_accessions).
    Caller is responsible for cleaning up tmp_dir after the pipeline finishes.
    """
    minknow_dir = _coerce(minknow_dir_stored)
    output_dir = _coerce(output_dir_stored)
    if not minknow_dir or not output_dir:
        raise ValueError("minknow_dir and output_dir must be configured")

    all_accessions = _get_related_run_accessions(db, run_accessions, auto_merge)
    merged_extra = [ra for ra in all_accessions if ra not in run_accessions]
    if auto_merge and merged_extra:
        _merge_note = f"[ODIN] Merge: adding {len(merged_extra)} related run(s): {merged_extra}"
    else:
        _merge_note = None

    # Build barcode → metadata map from DB (only non-excluded, non-deleted rows)
    rows = fetch_run_barcodes(db, all_accessions)

    if not rows:
        placeholders = ",".join("?" * len(all_accessions))
        db_label = _db_path_label(db)
        registered = db.execute(
            f"SELECT COUNT(*) AS cnt FROM nanopore_run_accessions WHERE run_accession IN ({placeholders})",
            all_accessions,
        ).fetchone()
        registered_count = int(registered["cnt"] or 0) if registered else 0
        if registered_count == 0:
            raise ValueError(
                f"No run_accession rows were found in the active database ({db_label}) for: {run_accessions}. "
                "This usually means the running backend is pointed at a different odin.db "
                "than the one you checked manually."
            )
        raise ValueError(
            f"Run accession(s) were found in the active database ({db_label}), but no non-excluded barcodes were available for: {run_accessions}. "
            "Check whether all barcodes are excluded or unlinked from samples."
        )

    barcode_meta: dict[str, dict] = {}
    for r in rows:
        barcode_meta[r["barcode"]] = {
            "alias": r["alias"] or r["barcode"],
        }

    concat_tag = _concat_tag(all_accessions)
    csv_run_accession = run_accessions_slug(run_accessions + merged_extra)

    concat_files = _concatenate_barcodes(
        all_accessions, barcode_meta, minknow_dir, tmp_dir, concat_tag
    )

    if not concat_files:
        raise ValueError("No FASTQ files found on disk for the selected barcodes")

    csv_dir = output_dir / "nanopore_processed"
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_path = csv_dir / f"all_samples_{concat_tag}.csv"

    with csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["sample", "run_accession", "instrument_platform", "fastq_1"])
        for barcode, out_file in sorted(concat_files.items()):
            writer.writerow(
                [
                    barcode,
                    csv_run_accession,
                    "OXFORD_NANOPORE",
                    coerce_path_for_shell(str(out_file.resolve())),
                ]
            )

    return csv_path, tmp_dir, _merge_note, all_accessions


# ── wf-metagenomics (AMR and SSU share the same samplesheet — just fastq dir) ─


def prepare_metagenomics_input(
    db: sqlite3.Connection,
    run_accessions: list[str],
    auto_merge: bool,
    minknow_dir_stored: str,
    output_dir_stored: str,
    file_identifier: str,
    tmp_dir: Path,
) -> tuple[Path, Path, list[str]]:
    """
    wf-metagenomics takes a directory of per-barcode FASTQ files (not a CSV).
    Returns (fastq_input_dir, tmp_dir, all_accessions).
    """
    minknow_dir = _coerce(minknow_dir_stored)
    output_dir = _coerce(output_dir_stored)
    if not minknow_dir or not output_dir:
        raise ValueError("minknow_dir and output_dir must be configured")

    all_accessions = _get_related_run_accessions(db, run_accessions, auto_merge)

    rows = fetch_run_barcodes(db, all_accessions)

    if not rows:
        raise ValueError(f"No registered barcodes found for runs: {run_accessions}")

    barcode_meta: dict[str, dict] = {
        r["barcode"]: {"alias": r["alias"] or r["barcode"]}
        for r in rows
    }

    concat_files = _concatenate_barcodes(
        all_accessions, barcode_meta, minknow_dir, tmp_dir, file_identifier
    )

    if not concat_files:
        raise ValueError("No FASTQ files found on disk")

    # wf-metagenomics expects barcode sub-directories inside --fastq
    # Restructure: tmp_dir/{barcode}/{barcode}_{identifier}.fastq.gz
    structured_dir = tmp_dir / "barcode_structure"
    for barcode, src_file in concat_files.items():
        bc_dir = structured_dir / barcode
        bc_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_file), str(bc_dir / src_file.name))

    return structured_dir, tmp_dir, all_accessions


# ── Mpox ─────────────────────────────────────────────────────────────────────


def build_mpox_samplesheet(
    db: sqlite3.Connection,
    run_accession: str,
    minknow_dir_stored: str,
    output_dir_stored: str,
) -> Path:
    """
    Build the artic-mpxv-nf samplesheet CSV.
    Reads directly from fastq_pass (no concatenation needed for mpox).
    Requires nanopore_runs.type to be set per barcode.

    Returns csv_path.
    """
    minknow_dir = _coerce(minknow_dir_stored)
    output_dir = _coerce(output_dir_stored)
    if not minknow_dir or not output_dir:
        raise ValueError("minknow_dir and output_dir must be configured")

    allowed_types = {"test_sample", "positive_control", "negative_control", "no_template_control"}

    rows = fetch_run_barcodes(db, [run_accession])

    if not rows:
        raise ValueError(f"No registered barcodes found for run_accession: {run_accession}")

    csv_dir = output_dir / "nanopore_processed"
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_path = csv_dir / f"mpox_samples_{run_accession}.csv"

    run_dir = find_run_dir(minknow_dir, run_accession)
    if run_dir is None:
        raise FileNotFoundError(
            f"Run directory '{run_accession}' not found under '{minknow_dir}'"
        )
    fastq_dir = run_dir / "fastq_pass"
    if not fastq_dir.is_dir():
        raise FileNotFoundError(f"fastq_pass directory not found: {fastq_dir}")

    with csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["barcode", "alias", "type"])
        for r in rows:
            barcode = r["barcode"]
            bc_dir = fastq_dir / barcode
            if not bc_dir.is_dir():
                continue  # skip barcodes not on disk

            sample_type = str(r["type"] or "").strip().lower()
            if sample_type not in allowed_types:
                raise ValueError(
                    f"Invalid type '{sample_type}' for barcode '{barcode}'. "
                    f"Allowed: {', '.join(sorted(allowed_types))}"
                )

            alias = str(r["alias"] or "").strip()
            if not alias or alias.lower() == "nan":
                alias = f"{barcode}_{r['sample_code']}"

            writer.writerow([barcode, alias, sample_type])

    return csv_path
