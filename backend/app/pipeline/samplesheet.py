"""
Samplesheet builders — read from ODIN DB, produce CSV files for each pipeline.

All functions receive an open sqlite3.Connection and write a CSV to disk,
returning the absolute path as a string.  No Excel files are read.

Taxprofiler samplesheet format (nf-core/taxprofiler):
  sample, run_accession, instrument_platform, fastq_1

Mpox samplesheet format (artic-network/artic-mpxv-nf):
  barcode, alias, type

The concatenate step (merging FASTQ files across run_accessions) is handled
here inline. When merge is enabled we expand to the run's merge group
(``db.queries.get_merge_group``: continuation partners on the same flow cell
plus same-flow-cell runs sharing a barcode → sample link — the same set the
dashboard shows as "will merge FASTQ with"), then concatenate on disk, taking from
each run only the barcodes that run registers.

Replicates are pooled in the same step. Barcodes numbered as replicates of one
sample (``pipeline.replicates``) are concatenated into one input named after
them — always for taxprofiler and SSU, and for AMR unless the installation's
``amr_replicates`` setting says per barcode — so that the workflow sees the
sample once. A group that cannot be pooled stops the build; pre-launch
validation refuses the same groups earlier.
"""

from __future__ import annotations

import csv
import gzip
import shutil
import sqlite3
from pathlib import Path
from typing import Optional

from ..db.queries import fetch_run_barcodes, get_continuation_window, get_merge_group
from ..parsers.discovery import find_run_dir
from ..utils import coerce_path, coerce_path_for_shell, run_accessions_slug
from .replicates import plan_inputs, replicate_groups

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
    inputs: dict[str, list[str]],  # input name -> barcode directories concatenated into it
    minknow_dir: Path,
    tmp_dir: Path,
    concat_tag: str,
    registered: set[tuple[str, str]],
) -> dict[str, Path]:
    """
    Concatenate FASTQ files from one or more run_accessions.
    One output file per input: {tmp_dir}/{name}_{concat_tag}.fastq.gz

    An input is normally one barcode, read from that barcode's directory in every
    accession that registers it. A pooled replicate group is one input fed by several
    barcode directories (``replicates.plan_inputs``); its name lists the barcodes.

    ``registered`` holds the (run_accession, barcode) pairs with metadata, excluded
    barcodes left out. A barcode directory is read only for a run that registers that
    barcode: reads MinKNOW sorted into a barcode nobody used on that run (stray
    assignments, a few reads each) never join another run's sample.

    ``concat_tag`` is a short, deterministic identifier for the full run set.
    The complete accession list is intentionally not embedded in the filename
    to avoid Windows / Linux path-length failures for larger merged groups.

    Returns dict: input name -> output_file_path.
    Only inputs with at least one FASTQ file on disk are written.
    """
    output_files: dict[str, Path] = {}
    tmp_dir.mkdir(parents=True, exist_ok=True)

    for name, barcodes in inputs.items():
        sources: list[Path] = []
        for ra in run_accessions:
            run_dir = find_run_dir(minknow_dir, ra)
            if run_dir is None:
                continue
            for barcode in barcodes:
                if (ra, barcode) not in registered:
                    continue
                bc_dir = run_dir / "fastq_pass" / barcode
                if bc_dir.is_dir():
                    sources.extend(_fastq_files(bc_dir))

        if not sources:
            continue

        out_path = tmp_dir / f"{name}_{concat_tag}.fastq.gz"

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

        output_files[name] = out_path

    return output_files


def _registered(rows: list[sqlite3.Row]) -> set[tuple[str, str]]:
    """The (run_accession, barcode) pairs of ``fetch_run_barcodes`` rows."""
    return {(r["run_accession"], r["barcode"]) for r in rows}


def _plan_inputs(rows: list[sqlite3.Row], *, pool: bool) -> tuple[dict[str, list[str]], list[str]]:
    """Decide the inputs for the barcode rows of a run set, and the notes to log.

    Groups across the whole run set (continuations of one flow cell share barcodes, so a
    group spans them). Raises ``ValueError`` for a group that cannot be pooled.
    """
    groups = replicate_groups(rows, by_run=False)
    inputs = plan_inputs(groups, pool=pool)
    notes = [
        f"[ODIN] Pooling replicates: {g.pooled_name} = {' + '.join(g.barcodes)} ({g.label})"
        for g in groups
        if pool and g.numbered
    ]
    return inputs, notes


def _get_related_run_accessions(
    db: sqlite3.Connection,
    run_accessions: list[str],
    auto_merge: bool,
) -> list[str]:
    """
    If auto_merge is True, expand each run to its merge group (see get_merge_group).
    Otherwise return as-is. Originals keep their position; partners follow.
    """
    if not auto_merge:
        return run_accessions

    time_window = get_continuation_window(db)
    expanded = list(run_accessions)
    for ra in run_accessions:
        expanded.extend(get_merge_group(db, ra, time_window))
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
) -> tuple[Path, Path, list[str], list[str]]:
    """
    Build the nf-core/taxprofiler samplesheet CSV.

    Returns (csv_path, tmp_dir, notes, all_accessions) — notes are lines for the run
    log (merge partners added, replicate groups pooled), all_accessions is the full
    expanded list after merge resolution (may be longer than run_accessions).
    Replicate groups are always pooled: one samplesheet row per group, named after its
    barcodes, so the sample is classified once.
    Caller is responsible for cleaning up tmp_dir after the pipeline finishes.
    """
    minknow_dir = _coerce(minknow_dir_stored)
    output_dir = _coerce(output_dir_stored)
    if not minknow_dir or not output_dir:
        raise ValueError("minknow_dir and output_dir must be configured")

    all_accessions = _get_related_run_accessions(db, run_accessions, auto_merge)
    merged_extra = [ra for ra in all_accessions if ra not in run_accessions]
    notes: list[str] = []
    if auto_merge and merged_extra:
        notes.append(f"[ODIN] Merge: adding {len(merged_extra)} related run(s): {merged_extra}")

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

    inputs, pooling_notes = _plan_inputs(rows, pool=True)
    notes.extend(pooling_notes)

    concat_tag = _concat_tag(all_accessions)
    csv_run_accession = run_accessions_slug(run_accessions + merged_extra)

    concat_files = _concatenate_barcodes(
        all_accessions, inputs, minknow_dir, tmp_dir, concat_tag, _registered(rows)
    )

    if not concat_files:
        raise ValueError("No FASTQ files found on disk for the selected barcodes")

    csv_dir = output_dir / "nanopore_processed"
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_path = csv_dir / f"all_samples_{concat_tag}.csv"

    with csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["sample", "run_accession", "instrument_platform", "fastq_1"])
        for name, out_file in sorted(concat_files.items()):
            writer.writerow(
                [
                    name,
                    csv_run_accession,
                    "OXFORD_NANOPORE",
                    coerce_path_for_shell(str(out_file.resolve())),
                ]
            )

    return csv_path, tmp_dir, notes, all_accessions


# ── wf-metagenomics (AMR and SSU share the same samplesheet — just fastq dir) ─


def prepare_metagenomics_input(
    db: sqlite3.Connection,
    run_accessions: list[str],
    auto_merge: bool,
    minknow_dir_stored: str,
    output_dir_stored: str,
    file_identifier: str,
    tmp_dir: Path,
    *,
    pool_replicates: bool = True,
) -> tuple[Path, Path, list[str], list[str]]:
    """
    wf-metagenomics takes a directory of per-sample FASTQ sub-directories (not a CSV).
    Returns (fastq_input_dir, tmp_dir, all_accessions, notes).

    With ``pool_replicates`` a replicate group becomes one sub-directory named after its
    barcodes; the workflow then reports the pooled input under that name. SSU always
    pools; AMR passes the ``amr_replicates`` setting.
    """
    minknow_dir = _coerce(minknow_dir_stored)
    output_dir = _coerce(output_dir_stored)
    if not minknow_dir or not output_dir:
        raise ValueError("minknow_dir and output_dir must be configured")

    all_accessions = _get_related_run_accessions(db, run_accessions, auto_merge)
    merged_extra = [ra for ra in all_accessions if ra not in run_accessions]
    notes: list[str] = []
    if auto_merge and merged_extra:
        notes.append(f"[ODIN] Merge: adding {len(merged_extra)} related run(s): {merged_extra}")

    rows = fetch_run_barcodes(db, all_accessions)

    if not rows:
        raise ValueError(f"No registered barcodes found for runs: {run_accessions}")

    inputs, pooling_notes = _plan_inputs(rows, pool=pool_replicates)
    notes.extend(pooling_notes)

    concat_files = _concatenate_barcodes(
        all_accessions, inputs, minknow_dir, tmp_dir, file_identifier, _registered(rows)
    )

    if not concat_files:
        raise ValueError("No FASTQ files found on disk")

    # wf-metagenomics expects one sub-directory per sample inside --fastq
    # Restructure: tmp_dir/{name}/{name}_{identifier}.fastq.gz
    structured_dir = tmp_dir / "barcode_structure"
    for name, src_file in concat_files.items():
        bc_dir = structured_dir / name
        bc_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_file), str(bc_dir / src_file.name))

    return structured_dir, tmp_dir, all_accessions, notes


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
