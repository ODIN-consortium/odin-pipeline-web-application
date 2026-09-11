"""
Pipeline API — launch, monitor, cancel, and stream logs.

Endpoints
---------
  POST   /api/pipeline/runs               Launch a new pipeline run
  GET    /api/pipeline/runs               List all pipeline runs (newest first)
  GET    /api/pipeline/runs/active        Return the currently running run (or 404)
  GET    /api/pipeline/runs/{id}          Get a single run
  DELETE /api/pipeline/runs/{id}          Cancel a running pipeline
  DELETE /api/pipeline/runs/{id}/record   Delete a finished run record from the DB
  DELETE /api/pipeline/runs/{id}/workdir  Delete the Nextflow work directory from disk
  GET    /api/pipeline/runs/{id}/logs     SSE stream of log output
  GET    /api/pipeline/runs/{id}/manifest  Return run_manifest.txt content as plain text

  GET    /api/pipeline/nanopore/{run_accession}/merge-candidates
         Return related runs sharing sample_ids — used by the launch wizard.

  PUT    /api/pipeline/nanopore/{run_accession}/merge-decision
         Store the user's merge decision (auto_merge true/false).

  DELETE /api/pipeline/nanopore/{run_accession}/merge-decision
         Clear the merge decision (e.g. after re-registration).
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import platform
import re
import shutil
import sqlite3
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse

from ..database import DB_PATH, connect_sqlite_with_retry, get_db
from ..db.queries import (
    fetch_run_barcodes,
    find_continuation_partners,
    get_auto_merge,
    get_continuation_window,
    get_metadata_rows_from_db,
    get_pipeline_run_accessions,
    get_run_accession_meta,
)
from ..parsers.amr_postprocessor import run_amr_postprocessing
from ..parsers.discovery import find_run_dir
from ..parsers.kraken_postprocessor import run_ssu_postprocessing, run_taxprofiler_postprocessing
from ..pipeline import command_builder as cb
from ..pipeline import executor, samplesheet
from ..pipeline.command_builder import _get as _cb_get
from ..pipeline.command_builder import build_outdir
from ..pipeline.extract_reads import run_extraction
from ..pipeline.log_hints import detect_error_hint
from ..schemas import (
    PIPELINE_TYPES,
    MergeCandidate,
    MergeDecisionPayload,
    MergeDecisionStatus,
    PipelineLaunchPayload,
    PipelineRunRead,
)
from ..settings_resolver import lookup_setting
from ..utils import append_log as _append_run_log
from ..utils import (
    coerce_path,
    coerce_path_for_shell,
    normalize_for_storage,
    resolve_log_dir,
    run_accessions_slug,
    sql_placeholders,
    utc_now_str,
    validate_run_accession,
)

router = APIRouter(prefix="/pipeline", tags=["pipeline"])
logger = logging.getLogger("odin")


def _validate_nextflow_config(db: sqlite3.Connection) -> list[str]:
    """Nextflow profile/config consistency (all pipelines except squirrel)."""
    errors: list[str] = []
    _nf_profile = _cb_get(db, "nextflow_profile", "odin")
    _nf_config = _cb_get(db, "nextflow_config_file")
    if _nf_profile and not _nf_config:
        errors.append(
            f"Nextflow profile '{_nf_profile}' is configured but no config file is set. "
            "Add the path to odin.config in Settings → 'Nextflow config file', "
            "or set the NEXTFLOW_CONFIG_FILE environment variable."
        )
    elif _nf_config:
        _cfg_local = Path(coerce_path(_nf_config))
        if not _cfg_local.exists():
            errors.append(
                f"Nextflow config file not found: {_cfg_local}. "
                "Update 'Nextflow config file' in Settings to the correct path."
            )
    return errors


def _validate_squirrel_source(
    db: sqlite3.Connection, run_accessions: list[str], source_run_id: str | None
) -> list[str]:
    """Squirrel: the source mpox run must have a consensus FASTA on disk."""
    errors: list[str] = []
    if source_run_id:
        src = db.execute(
            "SELECT output_path FROM pipeline_runs WHERE id = ?", (source_run_id,)
        ).fetchone()
        if not src or not src["output_path"]:
            errors.append("Source mpox run has no recorded output path.")
        else:
            fasta = Path(coerce_path(src["output_path"])) / "all_consensus.fasta"
            if not fasta.exists():
                errors.append(
                    f"Consensus FASTA not found at: {fasta}. "
                    "Ensure the artic pipeline completed successfully."
                )
    elif run_accessions:
        # Launched directly from an existing artic output on disk
        output_dir = lookup_setting(db, "output_dir")
        if not output_dir:
            errors.append("output_dir must be configured in Settings.")
        else:
            artic_dir = Path(coerce_path(
                build_outdir(output_dir, "outputs_wf_artic-mpxv-nf", run_accessions[0])
            ))
            fasta = artic_dir / "all_consensus.fasta"
            if not fasta.exists():
                errors.append(
                    f"Consensus FASTA not found at: {fasta}. "
                    "Ensure the artic pipeline output is present at the expected path."
                )
    else:
        errors.append("squirrel requires source_run_id or run_accessions.")
    return errors


def _validate_taxprofiler_databases(db: sqlite3.Connection) -> list[str]:
    """Taxprofiler: needs database entries from any of the three sources.

    Mirrors the source priority of ``generate_databases_csv``: databases_file →
    DB table → auto-discovery. The gate must accept whatever that fallback chain
    would find, or a launch that would succeed is refused here first.
    """
    errors: list[str] = []
    db_count = db.execute("SELECT COUNT(*) FROM databases").fetchone()[0]
    databases_file = lookup_setting(db, "databases_file")
    databases_file_exists = bool(
        databases_file and Path(coerce_path(databases_file)).exists()
    )
    if db_count == 0 and not databases_file_exists and not cb.autodiscover_databases():
        errors.append(
            "No database entries are configured and no Kraken2 database was "
            "auto-discovered. Add an entry on the Databases page, place a "
            "databases.csv at "
            f"{databases_file or '$ODIN_PIPELINE_ROOT/input_sheets/databases.csv'}, "
            "or put a database directory containing hash.k2d under "
            "ODIN_DATABASE_PATH (or $ODIN_PIPELINE_ROOT/databases) "
            "before launching Taxprofiler."
        )

    pathogens_file = lookup_setting(db, "pathogens_file")
    if pathogens_file and not Path(pathogens_file).is_file():
        errors.append(
            f"Pathogens reference file not found on disk: '{pathogens_file}'. "
            "Check the path configured in Settings."
        )
    return errors


def _validate_mpox_barcode_types(db: sqlite3.Connection, run_accessions: list[str]) -> list[str]:
    """Mpox: all registered barcodes must have a 'type' set."""
    errors: list[str] = []
    for ra in run_accessions:
        missing_type = [r for r in fetch_run_barcodes(db, [ra]) if not r["type"]]
        if missing_type:
            barcodes = ", ".join(r["barcode"] for r in missing_type)
            errors.append(
                f"Run '{ra}': the following barcodes have no 'type' set (required for mpox pipeline): "
                f"{barcodes}. Edit the barcodes and set type to one of: "
                "test_sample, positive_control, negative_control, no_template_control."
            )
    return errors


def _validate_run_metadata(db: sqlite3.Connection, run_accessions: list[str]) -> list[str]:
    """All pipelines: protocol_id and sequencing_kit_id must be set on each run."""
    errors: list[str] = []
    for ra in run_accessions:
        nra_row = get_run_accession_meta(db, ra)
        if nra_row:
            missing_fields = [
                f for f in ("protocol_id", "sequencing_kit_id")
                if not nra_row[f]
            ]
            if missing_fields:
                errors.append(
                    f"Run '{ra}': {', '.join(missing_fields)} not set. "
                    "These are required for correct output directory naming and metadata enrichment. "
                    "Edit the run accession and fill in the missing fields."
                )
    return errors


def _validate_barcodes_linked(db: sqlite3.Connection, run_accessions: list[str]) -> list[str]:
    """All pipelines: every non-excluded barcode must be linked to a sample."""
    errors: list[str] = []
    for ra in run_accessions:
        unlinked = [r for r in fetch_run_barcodes(db, [ra]) if r["sample_id"] is None]
        if unlinked:
            barcodes = ", ".join(r["barcode"] for r in unlinked)
            errors.append(
                f"Run '{ra}': the following barcodes have no sample linked: {barcodes}. "
                "Link each barcode to a sample, or add them to the exclusion list."
            )
    return errors


def _validate_samples_have_site(db: sqlite3.Connection, run_accessions: list[str]) -> list[str]:
    """All pipelines: every linked sample must have a site."""
    errors: list[str] = []
    for ra in run_accessions:
        no_site = [
            r for r in fetch_run_barcodes(db, [ra])
            if r["sample_id"] is not None and r["site_id"] is None
        ]
        if no_site:
            listed = ", ".join(f"{r['barcode']} ({r['sample_code']})" for r in no_site)
            errors.append(
                f"Run '{ra}': the following samples have no site assigned: {listed}. "
                "Assign a site to each sample before launching — samples without a site "
                "will be missing from visualization output."
            )
    return errors


def _validate_merged_consistency(db: sqlite3.Connection, run_accessions: list[str]) -> list[str]:
    """Merged runs: the same barcode must not map to different samples."""
    errors: list[str] = []
    if len(run_accessions) > 1:
        placeholders = ",".join("?" * len(run_accessions))
        inconsistent = db.execute(
            f"""
            SELECT nr.barcode, COUNT(DISTINCT nr.sample_id) AS cnt
            FROM nanopore_runs nr
            JOIN nanopore_run_accessions nra ON nra.id = nr.accession_id
            WHERE nra.run_accession IN ({placeholders})
              AND nr.sample_id IS NOT NULL
            GROUP BY nr.barcode
            HAVING COUNT(DISTINCT nr.sample_id) > 1
            """,
            run_accessions,
        ).fetchall()
        if inconsistent:
            barcodes = ", ".join(r["barcode"] for r in inconsistent)
            errors.append(
                f"Merged run consistency error: the following barcodes are linked to different "
                f"samples across the merged run accessions: {barcodes}. "
                "Each barcode must map to the same sample in all merged runs."
            )
    return errors


def _validate_fastq_present(run_accessions: list[str], minknow_dir: str) -> list[str]:
    """Non-mpox/squirrel: the FASTQ directory must exist on disk and be non-empty."""
    errors: list[str] = []
    minknow_path = Path(coerce_path(minknow_dir))
    for ra in run_accessions:
        run_dir = find_run_dir(minknow_path, ra)
        if run_dir is None:
            errors.append(
                f"Run '{ra}' was not found on disk under '{minknow_dir}'. "
                "Check that the MinKNOW directory is correctly configured and the run data is present."
            )
        else:
            fastq_pass = run_dir / "fastq_pass"
            if not fastq_pass.is_dir():
                errors.append(
                    f"Run '{ra}': fastq_pass directory not found under {run_dir}."
                )
            elif not any(fastq_pass.iterdir()):
                errors.append(
                    f"Run '{ra}': fastq_pass directory is empty — no barcode subdirectories found."
                )
    return errors


def _validate_pre_launch(
    db: sqlite3.Connection,
    pipeline_type: str,
    run_accessions: list[str],
    minknow_dir: str,
    source_run_id: str | None = None,
) -> list[str]:
    """
    Synchronous pre-launch checks that should fail fast with a clear API error
    rather than letting the job queue and fail in the background.

    Returns a list of human-readable error strings (empty = all OK).
    """
    # squirrel skips the config check and validates only its own source (early return).
    if pipeline_type == "squirrel":
        return _validate_squirrel_source(db, run_accessions, source_run_id)

    errors: list[str] = []
    errors += _validate_nextflow_config(db)
    if pipeline_type == "taxprofiler":
        errors += _validate_taxprofiler_databases(db)
    if pipeline_type == "mpox":
        errors += _validate_mpox_barcode_types(db, run_accessions)
    errors += _validate_run_metadata(db, run_accessions)
    errors += _validate_barcodes_linked(db, run_accessions)
    errors += _validate_samples_have_site(db, run_accessions)
    errors += _validate_merged_consistency(db, run_accessions)
    # mpox reads fastq_pass directly; squirrel uses prior artic output — both skip this.
    if pipeline_type not in ("mpox", "squirrel"):
        errors += _validate_fastq_present(run_accessions, minknow_dir)
    return errors


def _collect_launch_warnings(
    db: sqlite3.Connection,
    run_accessions: list[str],
) -> list[str]:
    """Return non-blocking pre-launch warnings written to the run log.

    These do not prevent the pipeline from starting but alert operators to
    conditions that would produce incomplete or broken post-processing output:

    - sample_code has no '_' separator → site/type extraction will fail
    - site is missing country / longitude / latitude → null geography in Feather
    """
    warnings: list[str] = []

    for ra in run_accessions:
        rows = fetch_run_barcodes(db, [ra])
        linked = [r for r in rows if r["sample_id"] is not None]

        # ── sample_code must contain '_' so extract_site_and_type works ────────
        bad_code = [
            r["barcode"] for r in linked
            if r["sample_code"] and "_" not in r["sample_code"]
        ]
        if bad_code:
            warnings.append(
                f"[{ra}] Barcodes with a sample_code containing no '_' separator "
                f"(site extraction will fail in post-processing): {', '.join(bad_code)}."
            )

        # ── Sites must have country + coordinates for geography output ──────────
        site_ids = list({r["site_id"] for r in linked if r["site_id"]})
        if site_ids:
            geo_rows = db.execute(
                f"SELECT site_code, country, longitude, latitude"
                f" FROM sites WHERE id IN ({sql_placeholders(site_ids)})",
                site_ids,
            ).fetchall()
            no_geo = [
                r["site_code"] for r in geo_rows
                if not r["country"] or r["longitude"] is None or r["latitude"] is None
            ]
            if no_geo:
                warnings.append(
                    f"[{ra}] Site(s) missing geography data (country / longitude / latitude): "
                    f"{', '.join(no_geo)}. "
                    "Post-processing output will have null coordinates — "
                    "add the missing values on the Sites page."
                )

    return warnings


def _open_db() -> sqlite3.Connection:
    """Open a fresh DB connection for use inside background threads."""
    return connect_sqlite_with_retry(DB_PATH, context="pipeline api background")


def _create_pipeline_tmp_dir(run_id: str) -> Path:
    """Create a per-run temporary directory for concatenated FASTQ inputs.

    Priority order for the base directory:
      1. ODIN_TMP_DIR
      2. TMP_DIR
      3. TMPDIR
      4. /var/tmp
      5. platform default temp dir (fallback)
    """
    candidates = [
        os.getenv("ODIN_TMP_DIR"),
        os.getenv("TMP_DIR"),
        os.getenv("TMPDIR"),
        "/var/tmp",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            base = Path(coerce_path(candidate))
            base.mkdir(parents=True, exist_ok=True)
            return Path(tempfile.mkdtemp(prefix=f"odin_{run_id[:8]}_", dir=base))
        except Exception:
            continue
    return Path(tempfile.mkdtemp(prefix=f"odin_{run_id[:8]}_"))


def _describe_dir(path: Path) -> str:
    """Summarise a directory as "<n> file(s), <size> MB" for the run log.

    Best-effort: a directory being removed can race with a still-open writer, so any
    OSError degrades to a plain note rather than masking the real failure.
    """
    try:
        files = [p for p in path.rglob("*") if p.is_file()]
        total = sum(p.stat().st_size for p in files)
    except OSError as exc:
        return f"could not be measured ({exc})"
    return f"{len(files)} file(s), {total / (1024 * 1024):.1f} MB"


def _make_tmp_cleanup_fn(
    run_id: str, tmp_dir: Path, db_path: str, log_path: Optional[Path] = None
) -> Callable[[], None]:
    """Return a cleanup callback that removes the temporary concat workspace.

    The workspace is recorded in the run log before it is deleted. This runs on the
    failure paths too (a preparation error used to skip cleanup entirely and leak the
    workspace), and on those paths the directory is the main evidence of what went
    wrong — so its path and size go into the log the operator actually reads, rather
    than being kept on disk where they silently consume gigabytes.
    """

    def cleanup_fn() -> None:
        if tmp_dir.exists():
            if log_path is not None:
                _append_run_log(log_path, f"[ODIN] Removing temporary workspace: {tmp_dir}")
                _append_run_log(log_path, f"[ODIN]   contents: {_describe_dir(tmp_dir)}")
            shutil.rmtree(tmp_dir, ignore_errors=True)
        con = connect_sqlite_with_retry(db_path, context="pipeline cleanup")
        try:
            con.execute("UPDATE pipeline_runs SET work_dir = NULL, updated_at = ? WHERE id = ?", (utc_now_str(), run_id))
            con.commit()
        finally:
            con.close()

    return cleanup_fn


# utc_now_str(), lookup_setting(), _append_run_log() live in utils.py / settings.py — imported above.


def _row_to_read(row: sqlite3.Row, db: sqlite3.Connection) -> PipelineRunRead:
    run_accessions = get_pipeline_run_accessions(db, row["id"]) or None

    params = None
    try:
        p = row["params"]
        if p:
            params = json.loads(p)
    except (TypeError, json.JSONDecodeError):
        pass

    # Detect confidence reports on disk (fast directory glob — one check per run).
    confidence_report_targets: list[str] = []
    output_path = row["output_path"]
    if output_path and params:
        try:
            target = (params.get("pipeline_options") or {}).get("extract_target") or ""
            if target:
                normalised = re.sub(r"^/mnt(/[a-z]/)", r"\1", output_path)
                analysis_dir = Path(coerce_path(normalised)) / f"{target}_analysis"
                if analysis_dir.is_dir() and any(analysis_dir.glob("*_confidence_report.txt")):
                    confidence_report_targets = [target]
        except OSError:
            pass

    return PipelineRunRead(
        id=row["id"],
        pipeline_type=row["pipeline_type"],
        status=row["status"],
        run_accessions=run_accessions,
        params=params,
        pid=row["pid"],
        log_file=row["log_file"],
        exit_code=row["exit_code"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        output_path=row["output_path"],
        work_dir=row["work_dir"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        created_by=row["created_by"],
        error_hint=detect_error_hint(row["log_file"]) if row["status"] == "failed" else None,
        confidence_report_targets=confidence_report_targets,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline options (valid parameter values per pipeline type)
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_MPOX_CLADES = "cladei,cladeia,cladeib,cladeii,cladeiia,cladeiib"
_DEFAULT_MPOX_SCHEMES = (
    "artic-inrb-mpox/2500/v1.0.0,"
    "yale-mpox/2000/v1.0.0-cladei,"
    "yale-mpox/2000/v1.0.0-cladeii"
)


@router.get("/options/mpox")
def get_mpox_options():
    """Return the valid clade and scheme_version values for the Mpox pipeline.

    Overridable via environment variables:
      MPOX_CLADES   — comma-separated list of valid clade identifiers
      MPOX_SCHEMES  — comma-separated list of valid scheme_version strings
    """
    clades = [
        c.strip()
        for c in os.environ.get("MPOX_CLADES", _DEFAULT_MPOX_CLADES).split(",")
        if c.strip()
    ]
    schemes = [
        s.strip()
        for s in os.environ.get("MPOX_SCHEMES", _DEFAULT_MPOX_SCHEMES).split(",")
        if s.strip()
    ]
    return {"clades": clades, "schemes": schemes}


_DEFAULT_EXTRACT_TARGETS = [
    {"label": "mpox_cladeia", "display_name": "Mpox Clade Ia", "ref_accession": "NC_003310.1", "taxon_taxid": "10244", "taxon_sci_name": "Monkeypox virus"},
    {"label": "mpox_cladeib", "display_name": "Mpox Clade Ib", "ref_accession": "PP899475.1",  "taxon_taxid": "10244", "taxon_sci_name": "Monkeypox virus"},
    {"label": "mpox_cladeii", "display_name": "Mpox Clade II", "ref_accession": "NC_063383.1", "taxon_taxid": "10244", "taxon_sci_name": "Monkeypox virus"},
]


def _load_extract_targets_csv(path: str) -> list[dict]:
    targets = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            label = (row.get("label") or "").strip()
            display_name = (row.get("display_name") or "").strip()
            ref_accession = (row.get("ref_accession") or "").strip()
            if label and display_name and ref_accession:
                targets.append({
                    "label": label,
                    "display_name": display_name,
                    "ref_accession": ref_accession,
                    "taxon_taxid": (row.get("taxon_taxid") or "").strip(),
                    "taxon_sci_name": (row.get("taxon_sci_name") or "").strip(),
                })
    return targets


@router.get("/options/extract-targets")
def get_extract_targets(db: sqlite3.Connection = Depends(get_db)):
    """Return organisms available for post-taxprofiler read extraction.

    Path resolution (highest priority first):
      1. ODIN_EXTRACT_TARGETS_FILE env var
      2. extract_targets_file setting in the DB
      3. $ODIN_PIPELINE_ROOT/config/extract_targets.csv (default)
    Falls back to built-in Mpox Clade I/II defaults if the file is absent.
    """
    csv_path = lookup_setting(db, "extract_targets_file")
    if csv_path and os.path.isfile(csv_path):
        try:
            targets = _load_extract_targets_csv(csv_path)
            if targets:
                return {"targets": targets}
        except Exception:
            pass
    return {"targets": _DEFAULT_EXTRACT_TARGETS}


# ─────────────────────────────────────────────────────────────────────────────
# List / get
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/runs", response_model=list[PipelineRunRead])
def list_pipeline_runs(
    pipeline_type: Optional[str] = None,
    status: Optional[str] = None,
    db: sqlite3.Connection = Depends(get_db),
):
    query = "SELECT * FROM pipeline_runs"
    params: list = []
    conditions: list[str] = []
    if pipeline_type:
        conditions.append("pipeline_type = ?")
        params.append(pipeline_type)
    if status:
        conditions.append("status = ?")
        params.append(status)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC LIMIT 200"
    rows = db.execute(query, params).fetchall()
    return [_row_to_read(r, db) for r in rows]


@router.get("/runs/active", response_model=PipelineRunRead)
def get_active_run(db: sqlite3.Connection = Depends(get_db)):
    run_id = executor.current_run_id()
    if not run_id:
        raise HTTPException(status_code=404, detail="No pipeline is currently running")
    row = db.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    return _row_to_read(row, db)


@router.get("/runs/{run_id}", response_model=PipelineRunRead)
def get_pipeline_run(run_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    return _row_to_read(row, db)


# ─────────────────────────────────────────────────────────────────────────────
# Launch helpers
# ─────────────────────────────────────────────────────────────────────────────


def _ensure_outdir_native(
    output_dir_stored: str, subdir: str, identifier: str, run_accession: str = ""
) -> None:
    """
    Pre-create the Nextflow output directory using Python's native filesystem
    API before handing off to WSL.

    On Windows, both Java NIO (Nextflow) and bash ``mkdir -p`` fail on
    ``/mnt/d/...`` (v9fs/9p) WSL2 mount points when the directory doesn't
    exist yet.  Python running natively on Windows calls Win32 directly and
    works reliably.

    On Linux/Docker, Nextflow creates its own outdir normally — this is a
    no-op on those platforms.
    """
    if platform.system() != "Windows":
        return
    outdir = (
        Path(coerce_path(normalize_for_storage(output_dir_stored)))
        / "nanopore_processed"
        / subdir
        / identifier
    )
    if run_accession:
        outdir = outdir / run_accession
    outdir.mkdir(parents=True, exist_ok=True)


_PIPELINE_SUBDIRS: dict[str, str] = {
    "taxprofiler":        "outputs_taxprofiler",
    "wf_metagenomics_amr": "outputs_wf_metagenomics_amr",
    "wf_metagenomics_ssu": "outputs_wf_metagenomics_ssu",
    "mpox":               "outputs_wf_artic-mpxv-nf",
    "squirrel":           "output_squirrel",
}


def _resolve_output_path(output_path: str) -> Path:
    """Convert a stored output_path (``/mnt/c/...`` or ``/c/...``) to a native Path.

    Stored paths use the Git Bash ``/c/...`` convention (from
    ``normalize_for_storage``).  On Linux/Docker they may arrive as
    ``/mnt/c/...`` (written by ``coerce_path_for_shell``).  Strip any leading
    ``/mnt`` prefix so ``coerce_path`` can normalise to the platform convention.
    """
    normalised = re.sub(r"^/mnt(/[a-z]/)", r"\1", output_path)
    return Path(coerce_path(normalised))


def _write_run_manifest(
    db: sqlite3.Connection,
    output_dir_stored: str,
    subdir: str,
    identifier: str,
    slug: str,
    pipeline_type: str,
    run_accessions: list[str],
    auto_merge: bool,
    *,
    provisional: bool,
) -> None:
    """Write run_manifest.txt to the pipeline output directory.

    Contains the full run accession list, per-run disk metadata (start/stop
    times, flow cell) and a per-barcode sample table.  Useful when the
    filesystem slug is truncated (e.g. '...and_5_more') and independently
    useful as a self-contained record of what was processed.

    *provisional* marks the launch-time write: it carries only the requested
    accessions, because merge partners are resolved when the run starts (the
    prepare step rewrites the file with the accessions actually used). A
    provisional manifest must say so — an operator who opens it before the run
    starts otherwise reads a merged run listing one accession and "Merged: No",
    which looks like a stale file from a previous run of the same directory.
    """
    outdir = (
        Path(coerce_path(normalize_for_storage(output_dir_stored)))
        / "nanopore_processed"
        / subdir
        / identifier
    )
    if slug:
        outdir = outdir / slug
    outdir.mkdir(parents=True, exist_ok=True)

    # ── Run disk metadata ────────────────────────────────────────────────────
    disk_rows: dict[str, sqlite3.Row] = {}
    if run_accessions:
        for r in db.execute(
            "SELECT run_accession, run_started, run_stopped, flow_cell_id, run_name"
            f" FROM nanopore_disk_cache"
            f" WHERE run_accession IN ({sql_placeholders(run_accessions)})",
            run_accessions,
        ).fetchall():
            disk_rows[r["run_accession"]] = r

    # ── Per-barcode metadata ─────────────────────────────────────────────────
    meta_rows = get_metadata_rows_from_db(db, run_accessions)

    # ── Build text ───────────────────────────────────────────────────────────
    lines: list[str] = []
    lines.append("ODIN Pipeline Run Manifest")
    lines.append("=" * 60)
    lines.append(f"Generated:      {utc_now_str()}")
    if provisional:
        lines.append(
            "Status:         provisional \u2014 written at launch; rewritten with the"
            " accessions actually used when the run starts"
        )
    lines.append(f"Pipeline type:  {pipeline_type}")
    lines.append(f"File ID:        {identifier}")
    if provisional and auto_merge:
        lines.append(
            "Merged:         Yes \u2014 partner runs are resolved when the run starts"
        )
    elif auto_merge and len(run_accessions) > 1:
        lines.append(
            f"Merged:         Yes \u2014 {len(run_accessions)} run accession(s)"
            " concatenated before processing"
        )
    else:
        lines.append("Merged:         No")
    lines.append("")
    lines.append("Run accessions:")
    for i, ra in enumerate(run_accessions, 1):
        disk = disk_rows.get(ra)
        lines.append(f"  [{i}] {ra}")
        if disk:
            started = disk["run_started"] or "?"
            stopped = disk["run_stopped"] or "?"
            fc      = disk["flow_cell_id"] or "?"
            lines.append(f"      started: {started}  stopped: {stopped}  flow_cell: {fc}")
            if disk["run_name"]:
                lines.append(f"      run_name: {disk['run_name']}")
        else:
            lines.append("      (disk metadata not available)")

    if meta_rows:
        lines.append("")
        lines.append("Barcodes:")
        ra_index = {ra: i for i, ra in enumerate(run_accessions, 1)}
        headers = ["run", "barcode", "alias", "sample_code", "sample_type",
                   "sampling_date", "site", "country"]
        table: list[list[str]] = [
            [
                f"[{ra_index.get(r['run_accession'], '?')}]",
                r["barcode"],
                r["alias"],
                r["sample_code"],
                r["sample_type"],
                r["sampling_date"],
                r["sampling_site_id"],
                r["country"],
            ]
            for r in meta_rows
        ]
        widths = [
            max(len(headers[i]), max((len(row[i]) for row in table), default=0))
            for i in range(len(headers))
        ]

        def _fmt(cells: list[str]) -> str:
            return "  " + "  ".join(c.ljust(w) for c, w in zip(cells, widths))

        lines.append(_fmt(headers))
        lines.append("  " + "  ".join("-" * w for w in widths))
        for row in table:
            lines.append(_fmt(row))
    else:
        lines.append("")
        lines.append("Barcodes: (no registered barcodes found in database)")

    (outdir / "run_manifest.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8", errors="replace"
    )


def _make_postprocess_fn(
    db_factory: Callable[[], sqlite3.Connection],
    run_accessions: list[str],
    output_path_local: str,
    log_path: Path,
    required_setting_keys: list[str],
    log_prefix: str,
    run_postprocessing: Callable[..., None],
) -> Callable[[], None]:
    """Generic postprocess factory.  Reads *required_setting_keys* from the DB at
    execution time; skips (with a log message) if any are not configured."""

    def postprocess_fn() -> None:
        db = db_factory()
        try:
            settings = {k: lookup_setting(db, k) for k in required_setting_keys}
        finally:
            db.close()

        missing = [k for k, v in settings.items() if not v]
        if missing:
            _append_run_log(
                log_path,
                f"\n{log_prefix} Skipping post-processing: {', '.join(missing)}"
                " not configured in Settings.",
            )
            return

        run_postprocessing(
            run_accessions=run_accessions,
            output_path=output_path_local,
            db_path=str(DB_PATH),
            log_file=log_path,
            **settings,
        )

    return postprocess_fn


def _make_taxprofiler_postprocess_fn(
    db_factory: Callable[[], sqlite3.Connection],
    run_accessions: list[str],
    output_path_local: str,
    log_path: Path,
    pipeline_options: Optional[dict] = None,
) -> Callable[[], None]:
    enlighten_fn = _make_postprocess_fn(
        db_factory, run_accessions, output_path_local, log_path,
        required_setting_keys=["pathogens_file", "enlighten_data_path"],
        log_prefix="[ODIN-POST]",
        run_postprocessing=run_taxprofiler_postprocessing,
    )

    opts = pipeline_options or {}
    should_extract = bool(opts.get("save_reads")) and bool(opts.get("extract_target"))
    if not should_extract:
        return enlighten_fn

    def composed_fn() -> None:
        enlighten_fn()

        db = db_factory()
        try:
            store_dir = lookup_setting(db, "store_dir") or None
        finally:
            db.close()

        _append_run_log(log_path, "\n[ODIN-EXTRACT] Starting read extraction...")
        try:
            run_extraction(
                outdir=output_path_local,
                pipeline_options=opts,
                db_path=str(DB_PATH),
                log_path=log_path,
                store_dir=store_dir,
            )
        except Exception as exc:
            _append_run_log(log_path, f"[ODIN-EXTRACT] Extraction failed: {exc}")

    return composed_fn


def _make_amr_postprocess_fn(
    db_factory: Callable[[], sqlite3.Connection],
    run_accessions: list[str],
    output_path_local: str,
    log_path: Path,
) -> Callable[[], None]:
    return _make_postprocess_fn(
        db_factory, run_accessions, output_path_local, log_path,
        required_setting_keys=["enlighten_data_path"],
        log_prefix="[ODIN-POST-AMR]",
        run_postprocessing=run_amr_postprocessing,
    )


def _make_ssu_postprocess_fn(
    db_factory: Callable[[], sqlite3.Connection],
    run_accessions: list[str],
    output_path_local: str,
    log_path: Path,
) -> Callable[[], None]:
    return _make_postprocess_fn(
        db_factory, run_accessions, output_path_local, log_path,
        required_setting_keys=["pathogens_file", "enlighten_data_path"],
        log_prefix="[ODIN-POST-SSU]",
        run_postprocessing=run_ssu_postprocessing,
    )


def _build_file_identifier(
    db: sqlite3.Connection, primary_ra: str, run_accessions: list[str]
) -> str:
    """Return the output folder name for a run: ``{sampleName}_{slug}``.

    ``slug`` is the run_accession for a single run, or a joined/truncated string
    for merged runs (via run_accessions_slug).  sampleName comes from the primary
    run's nanopore_run_accessions row; if absent the slug is used alone.

    The (run_accession, barcode) pair is the unique key in the metadata, so
    including the run_accession in the folder name ensures each sequencing event
    has its own output directory even when sampleName is reused across runs.
    """
    row = db.execute(
        "SELECT sampleName FROM nanopore_run_accessions WHERE run_accession = ? LIMIT 1",
        (primary_ra,),
    ).fetchone()
    slug = run_accessions_slug(run_accessions)
    if row and row["sampleName"]:
        return f"{row['sampleName']}_{slug}"
    return slug


@dataclass(frozen=True)
class LaunchPlan:
    """Everything the background prepare step needs to build the Nextflow command.

    Assembled by the launch endpoint (and by `requeue_pending_runs` after a restart)
    and consumed inside the executor thread, so it must be self-contained: `db_factory`
    opens a fresh connection per use because the request's connection is long gone.
    """

    db_factory: Callable[[], sqlite3.Connection]
    pipeline_type: str
    run_accessions: list[str]
    auto_merge: bool
    minknow_dir: str
    output_dir_stored: str
    file_identifier: str
    tmp_dir: Path
    primary_ra: str
    clade: Optional[str]
    scheme_version: Optional[str]
    resume: bool
    log_path: Path
    artic_outdir: Optional[str] = None
    pipeline_options: Optional[dict] = None

    @property
    def subdir(self) -> str:
        """The outputs_* folder this pipeline writes into."""
        return _PIPELINE_SUBDIRS.get(self.pipeline_type, f"outputs_{self.pipeline_type}")

    @property
    def output_identifier(self) -> str:
        """The output-folder name: mpox/squirrel key on the run accession."""
        if self.pipeline_type in ("mpox", "squirrel"):
            return self.primary_ra
        return self.file_identifier


def _log_concat_started(plan: LaunchPlan) -> None:
    _append_run_log(
        plan.log_path,
        f"[ODIN] FASTQ concatenation started for {len(plan.run_accessions)} "
        f"run_accession(s) into {plan.tmp_dir}",
    )


def _log_concat_finished(log_path: Path, count: int, location: str) -> None:
    _append_run_log(
        log_path,
        f"[ODIN] FASTQ concatenation finished successfully: {count} file(s) created {location}",
    )


def _clear_stale_work_dir(plan: LaunchPlan) -> None:
    """Remove a leftover Nextflow work directory before a fresh launch.

    Stale task dirs hold dangling symlinks that fail the next run. Skipped when
    resuming — that cache is exactly what -resume reuses — and for mpox, which
    does not use a per-identifier work dir.
    """
    if plan.resume or plan.pipeline_type == "mpox":
        return
    work_dir = cb.resolve_work_dir(plan.db_factory(), plan.subdir, plan.file_identifier)
    if Path(work_dir).exists():
        shutil.rmtree(Path(work_dir), ignore_errors=True)
        _append_run_log(plan.log_path, f"[ODIN] Cleared stale work directory: {work_dir}")


def _prepare_taxprofiler(plan: LaunchPlan) -> tuple[str, list[str]]:
    """Concatenate FASTQ into one sheet + a databases CSV, then build the command."""
    _log_concat_started(plan)
    csv_path, _, merge_note, all_accessions_used = samplesheet.build_taxprofiler_samplesheet(
        plan.db_factory(),
        plan.run_accessions,
        plan.auto_merge,
        plan.minknow_dir,
        plan.output_dir_stored,
        plan.file_identifier,
        plan.tmp_dir,
    )
    if merge_note:
        _append_run_log(plan.log_path, merge_note)
    _log_concat_finished(
        plan.log_path, len(list(plan.tmp_dir.glob("*.fastq.gz"))), f"in {plan.tmp_dir}"
    )
    databases_csv = cb.generate_databases_csv(plan.db_factory(), plan.tmp_dir)
    cmd, _ = cb.build_taxprofiler_cmd(
        plan.db_factory(),
        str(csv_path),
        str(databases_csv),
        plan.file_identifier,
        pipeline_options=plan.pipeline_options,
    )
    _ensure_outdir_native(plan.output_dir_stored, plan.subdir, plan.file_identifier)
    return cmd, all_accessions_used


# AMR and SSU prepare their input identically and differ only in the command built.
_METAGENOMICS_PIPELINES = ("wf_metagenomics_amr", "wf_metagenomics_ssu")


def _metagenomics_cmd_builder(pipeline_type: str) -> Callable[..., tuple[str, str]]:
    """Return the wf-metagenomics command builder for the AMR or SSU variant."""
    if pipeline_type == "wf_metagenomics_amr":
        return cb.build_wf_metagenomics_amr_cmd
    return cb.build_wf_metagenomics_ssu_cmd


def _prepare_metagenomics(plan: LaunchPlan) -> tuple[str, list[str]]:
    """Build a wf-metagenomics command (AMR or SSU) over a concatenated FASTQ dir."""
    _log_concat_started(plan)
    fastq_dir, _, all_accessions_used = samplesheet.prepare_metagenomics_input(
        plan.db_factory(),
        plan.run_accessions,
        plan.auto_merge,
        plan.minknow_dir,
        plan.output_dir_stored,
        plan.file_identifier,
        plan.tmp_dir,
    )
    _log_concat_finished(
        plan.log_path, len(list(fastq_dir.rglob("*.fastq.gz"))), f"under {fastq_dir}"
    )
    build_cmd = _metagenomics_cmd_builder(plan.pipeline_type)
    cmd, _ = build_cmd(plan.db_factory(), str(fastq_dir), plan.file_identifier)
    _ensure_outdir_native(plan.output_dir_stored, plan.subdir, plan.file_identifier)
    return cmd, all_accessions_used


def _prepare_mpox(plan: LaunchPlan) -> tuple[str, list[str]]:
    """Build the artic command chained into squirrel, reading fastq_pass directly."""
    csv_path = samplesheet.build_mpox_samplesheet(
        plan.db_factory(), plan.primary_ra, plan.minknow_dir, plan.output_dir_stored
    )
    run_dir = find_run_dir(
        Path(coerce_path(normalize_for_storage(plan.minknow_dir))), plan.primary_ra
    )
    if run_dir is None:
        raise FileNotFoundError(
            f"Run directory '{plan.primary_ra}' not found under '{plan.minknow_dir}'"
        )
    nf_cmd, squirrel_cmd, _ = cb.build_mpox_cmd(
        plan.db_factory(),
        str(csv_path),
        coerce_path_for_shell(str(run_dir / "fastq_pass")),
        plan.primary_ra,
        plan.clade,
        plan.scheme_version,
    )
    _ensure_outdir_native(plan.output_dir_stored, _PIPELINE_SUBDIRS["mpox"], plan.primary_ra)
    _ensure_outdir_native(plan.output_dir_stored, _PIPELINE_SUBDIRS["squirrel"], plan.primary_ra)
    # -resume belongs to the nextflow step, not to the squirrel step chained after it.
    if plan.resume:
        nf_cmd = nf_cmd + " -resume"
    return f"{nf_cmd} && {squirrel_cmd}", plan.run_accessions


def _prepare_squirrel(plan: LaunchPlan) -> tuple[str, list[str]]:
    """Build the squirrel command over an existing artic output directory."""
    if not plan.artic_outdir:
        raise ValueError("artic_outdir is required for squirrel pipeline")
    _ensure_outdir_native(plan.output_dir_stored, plan.subdir, plan.primary_ra)
    cmd, _ = cb.build_squirrel_cmd(
        plan.db_factory(), plan.artic_outdir, plan.primary_ra, plan.clade or "cladeii"
    )
    return cmd, plan.run_accessions


def _build_pipeline_command(plan: LaunchPlan) -> tuple[str, list[str]]:
    """Return (shell command, accessions actually used) for the planned pipeline.

    The accession list can grow beyond what was requested: merging pulls in
    continuation partners during FASTQ concatenation, and the manifest must record
    what was really processed.
    """
    if plan.pipeline_type == "taxprofiler":
        return _prepare_taxprofiler(plan)
    if plan.pipeline_type in _METAGENOMICS_PIPELINES:
        return _prepare_metagenomics(plan)
    if plan.pipeline_type == "mpox":
        return _prepare_mpox(plan)
    return _prepare_squirrel(plan)


def _make_prepare_fn(plan: LaunchPlan, cmd_holder: list[str]) -> Callable[[], None]:
    """Return the prepare_fn closure that executor.launch() runs before nextflow.

    It appends the finished command to *cmd_holder* — the executor reads it from
    there once preparation succeeded. Exceptions propagate deliberately: the
    executor catches them and marks the run failed.
    """

    def prepare_fn() -> None:
        # Pre-create pipeline_logs dir so Nextflow's -log path is always on the
        # mounted volume rather than inside the container.  This is the same dir
        # as the ODIN streaming logs, giving operators one place to look.
        # Python mkdir works cross-platform (Windows Win32 + Linux/Docker alike).
        resolve_log_dir(plan.output_dir_stored).mkdir(parents=True, exist_ok=True)

        # Always log the full accession list so the run log is self-contained
        # even when the filesystem slug is truncated (e.g. "ERR1__and_5_more").
        _append_run_log(
            plan.log_path,
            f"[ODIN] Pipeline launch: type={plan.pipeline_type}, "
            f"run_accessions={plan.run_accessions}",
        )

        _clear_stale_work_dir(plan)

        cmd, all_accessions_used = _build_pipeline_command(plan)
        # mpox/squirrel handle their own resume flag inside the chained command.
        if plan.pipeline_type not in ("mpox", "squirrel") and plan.resume:
            cmd = cmd + " -resume"

        _write_manifest_safely(
            plan.db_factory(),
            plan.output_dir_stored,
            plan.pipeline_type,
            plan.output_identifier,
            all_accessions_used,
            plan.auto_merge,
            plan.log_path,
            provisional=False,
            failure_note="could not write run manifest",
        )
        cmd_holder.append(cmd)

    return prepare_fn


def requeue_pending_runs(db_path: str) -> int:
    """
    Re-enqueue any pipeline_runs that were queued or running when the server
    last stopped.  Called once from the app lifespan on startup.

    - Previously *running* runs are reset to 'queued' and launched with
      -resume so Nextflow can continue from its cached work directory.
    - Previously *queued* runs are re-enqueued as-is (no -resume).

    Returns the number of runs re-queued.
    """
    def _open(path: str) -> sqlite3.Connection:
        return connect_sqlite_with_retry(path, context="pipeline requeue")

    def _fail_unrunnable(run_id: str, reason: str) -> None:
        """Mark a pending row failed instead of skipping it.

        A skipped row stays 'queued' forever: no restart ever revisits it, and
        the duplicate-launch guard then blocks every future run of the same
        accession — observed in the field after an engine switchover left rows
        queued with no log file. A row this function cannot reconstruct will
        never become runnable, so failing it loudly is the only honest state.
        """
        logger.warning(f"Requeue: marking unrunnable pending run {run_id} failed: {reason}")
        upd = _open(db_path)
        try:
            now = utc_now_str()
            upd.execute(
                "UPDATE pipeline_runs SET status='failed', pid=NULL, exit_code=-1,"
                " finished_at=?, updated_at=? WHERE id=?",
                (now, now, run_id),
            )
            upd.commit()
        finally:
            upd.close()

    db = _open(db_path)
    try:
        rows = db.execute(
            "SELECT * FROM pipeline_runs"
            " WHERE status IN ('queued', 'running')"
            " ORDER BY created_at ASC"
        ).fetchall()
    finally:
        db.close()

    count = 0
    for row in rows:
        was_running = row["status"] == "running"
        run_id = row["id"]
        pipeline_type = row["pipeline_type"]
        params: dict = json.loads(row["params"] or "{}")
        log_path = Path(row["log_file"]) if row["log_file"] else None
        tmp_dir_stored = row["work_dir"]

        # Read config + run_accessions from DB (opened fresh per row)
        cfg_db = _open(db_path)
        try:
            run_accessions = get_pipeline_run_accessions(cfg_db, run_id)
        finally:
            cfg_db.close()

        if not run_accessions or not log_path:
            _fail_unrunnable(
                run_id,
                "missing run_accessions" if not run_accessions else "missing log file",
            )
            continue

        primary_ra = run_accessions[0]
        clade = params.get("clade")
        scheme_version = params.get("scheme_version")
        source_run_id = params.get("source_run_id")
        pipeline_options = params.get("pipeline_options") or {}
        # For previously-running jobs: force -resume so Nextflow skips completed tasks.
        # For previously-queued jobs: honour the user's original resume choice.
        resume = True if was_running else bool(params.get("resume", False))

        # For squirrel: look up the artic output path from the source run
        requeue_artic_outdir: Optional[str] = None
        if pipeline_type == "squirrel" and source_run_id:
            _src_db = _open(db_path)
            try:
                _src_row = _src_db.execute(
                    "SELECT output_path FROM pipeline_runs WHERE id = ?", (source_run_id,)
                ).fetchone()
                requeue_artic_outdir = _src_row["output_path"] if _src_row else None
            finally:
                _src_db.close()

        # Read config from DB (use lookup_setting so env-var overrides are honoured)
        cfg_db = _open(db_path)
        try:
            minknow_dir = lookup_setting(cfg_db, "minknow_dir")
            output_dir_stored = lookup_setting(cfg_db, "output_dir")
            if not minknow_dir or not output_dir_stored:
                _fail_unrunnable(run_id, "minknow_dir/output_dir not configured")
                continue

            auto_merge = get_auto_merge(cfg_db, primary_ra)
            file_identifier = _build_file_identifier(cfg_db, primary_ra, run_accessions)
        finally:
            cfg_db.close()

        # Reuse existing work dir when present (e.g. after restart).
        # Otherwise, create a fresh temporary workspace for concatenated FASTQ files.
        if tmp_dir_stored:
            tmp_dir = Path(coerce_path(tmp_dir_stored))
        else:
            tmp_dir = _create_pipeline_tmp_dir(run_id)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # Reset a previously-running job back to queued
        if was_running:
            upd_db = _open(db_path)
            try:
                upd_db.execute(
                    "UPDATE pipeline_runs SET status='queued', pid=NULL, updated_at=? WHERE id=?",
                    (utc_now_str(), run_id),
                )
                upd_db.commit()
            finally:
                upd_db.close()

        cmd_holder: list[str] = []
        prepare_fn = _make_prepare_fn(
            LaunchPlan(
                db_factory=lambda: _open(db_path),
                pipeline_type=pipeline_type,
                run_accessions=run_accessions,
                auto_merge=auto_merge,
                minknow_dir=minknow_dir or "",
                output_dir_stored=output_dir_stored,
                file_identifier=file_identifier,
                tmp_dir=tmp_dir,
                primary_ra=primary_ra,
                clade=clade,
                scheme_version=scheme_version,
                resume=resume,
                log_path=log_path,
                artic_outdir=requeue_artic_outdir,
                pipeline_options=pipeline_options,
            ),
            cmd_holder,
        )
        executor.launch(
            run_id,
            "",
            log_path,
            db_path,
            prepare_fn=prepare_fn,
            cmd_holder=cmd_holder,
            cleanup_fn=_make_tmp_cleanup_fn(run_id, tmp_dir, db_path, log_path),
        )
        count += 1

    return count


# ─────────────────────────────────────────────────────────────────────────────
# Launch
# ─────────────────────────────────────────────────────────────────────────────


def _validate_launch_request(payload: PipelineLaunchPayload) -> None:
    """Reject a launch payload with an unknown type or malformed run accessions."""
    if payload.pipeline_type not in PIPELINE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown pipeline_type '{payload.pipeline_type}'. "
            f"Valid types: {sorted(PIPELINE_TYPES)}",
        )
    if not payload.run_accessions:
        raise HTTPException(status_code=422, detail="run_accessions must not be empty")
    for ra in payload.run_accessions:
        if not validate_run_accession(ra):
            raise HTTPException(
                status_code=422,
                detail=f"Invalid run_accession format: {ra!r}",
            )


def _load_active_run_accessions(
    db: sqlite3.Connection, pipeline_type: str
) -> dict[str, set[str]]:
    """Return pipeline_run_id -> its run_accessions, for queued/running runs of this type."""
    rows = db.execute(
        """SELECT pra.pipeline_run_id, pra.run_accession
           FROM pipeline_run_accessions pra
           JOIN pipeline_runs pr ON pr.id = pra.pipeline_run_id
           WHERE pr.pipeline_type = ? AND pr.status IN ('queued', 'running')""",
        (pipeline_type,),
    ).fetchall()
    active_by_run: dict[str, set[str]] = {}
    for row in rows:
        active_by_run.setdefault(row["pipeline_run_id"], set()).add(row["run_accession"])
    return active_by_run


def _reject_conflicting_active_runs(
    pipeline_type: str,
    active_by_run: dict[str, set[str]],
    run_accessions: list[str],
) -> None:
    """Raise 409 if a queued/running run of this type already holds these accessions.

    Two rules: an identical run set is a duplicate launch, and any partial overlap
    would have two pipelines writing the same run's output.
    """
    target_set = set(run_accessions)
    duplicate_id = next((rid for rid, ras in active_by_run.items() if ras == target_set), None)
    if duplicate_id:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A '{pipeline_type}' run for {run_accessions} "
                f"is already queued or running (id: {duplicate_id})."
            ),
        )
    overlap = next(
        ((rid, sorted(ras & target_set)) for rid, ras in active_by_run.items() if ras & target_set),
        None,
    )
    if overlap:
        overlap_id, shared = overlap
        raise HTTPException(
            status_code=409,
            detail=(
                f"A '{pipeline_type}' run using overlapping run_accessions {shared} "
                f"is already queued or running (id: {overlap_id})."
            ),
        )


def _resolve_auto_merge_map(
    db: sqlite3.Connection, run_accessions: list[str]
) -> dict[str, bool]:
    """Return run_accession -> effective auto_merge decision.

    A stale stored decision must not force merge behaviour once the continuation
    partners it was made for are gone, so relatedness is re-checked here.
    """
    return {
        ra: (get_auto_merge(db, ra) if _find_related_runs(db, ra) else False)
        for ra in run_accessions
    }


def _continuation_lock_set(db: sqlite3.Connection, run_accessions: set[str]) -> set[str]:
    """Expand a run set with every continuation partner it could pull into a merge."""
    lock_set = set(run_accessions)
    for ra in run_accessions:
        for candidate in _find_related_runs(db, ra):
            if candidate.run_accession:
                lock_set.add(candidate.run_accession)
    return lock_set


def _load_active_merge_flags(
    db: sqlite3.Connection, active_ids: list[str]
) -> dict[str, bool]:
    """Return pipeline_run_id -> whether that active run merges.

    Merge intent is recorded in the run's own params at launch time, but a decision
    stored later against any of its accessions counts too (either can arm the lock).
    """
    merge_by_run: dict[str, bool] = {}
    if not active_ids:
        return merge_by_run
    placeholders = sql_placeholders(active_ids)
    for row in db.execute(
        f"SELECT id, params FROM pipeline_runs WHERE id IN ({placeholders})",
        active_ids,
    ).fetchall():
        try:
            params = json.loads(row["params"] or "{}")
        except (TypeError, json.JSONDecodeError):
            params = {}
        merge_by_run[row["id"]] = bool(params.get("auto_merge", False))

    for row in db.execute(
        f"""
        SELECT pra.pipeline_run_id,
               MAX(CASE WHEN nmd.auto_merge = 1 THEN 1 ELSE 0 END) AS merge_enabled
        FROM pipeline_run_accessions pra
        LEFT JOIN nanopore_merge_decisions nmd
          ON nmd.run_accession = pra.run_accession
        WHERE pra.pipeline_run_id IN ({placeholders})
        GROUP BY pra.pipeline_run_id
        """,
        active_ids,
    ).fetchall():
        if bool(row["merge_enabled"]):
            merge_by_run[row["pipeline_run_id"]] = True
    return merge_by_run


def _find_continuation_conflict(
    db: sqlite3.Connection,
    active_by_run: dict[str, set[str]],
    target_set: set[str],
    *,
    requested_merge_enabled: bool,
) -> Optional[tuple[str, list[str]]]:
    """Return (active run id, shared group members) blocking a merge-enabled launch.

    A merging run effectively claims its whole continuation group (same-flowcell
    split-run partners), so two runs from one group must not run concurrently when
    either side merges. Directly overlapping accessions are excluded — those are
    already reported by `_reject_conflicting_active_runs`.
    """
    active_merge_by_run = _load_active_merge_flags(db, list(active_by_run.keys()))
    target_lock_set = _continuation_lock_set(db, target_set)
    for rid, ras in active_by_run.items():
        if not (requested_merge_enabled or active_merge_by_run.get(rid, False)):
            continue
        if ras & target_set:
            continue
        shared = _continuation_lock_set(db, ras) & target_lock_set
        if shared:
            return rid, sorted(shared - (ras & target_set))
    return None


def _reject_continuation_group_conflict(
    db: sqlite3.Connection,
    pipeline_type: str,
    active_by_run: dict[str, set[str]],
    target_set: set[str],
    auto_merge_map: dict[str, bool],
) -> None:
    """Raise 409 when an active run locks the same continuation group."""
    conflict = _find_continuation_conflict(
        db,
        active_by_run,
        target_set,
        requested_merge_enabled=any(auto_merge_map.get(ra, False) for ra in target_set),
    )
    if conflict:
        conflict_id, shared_related = conflict
        raise HTTPException(
            status_code=409,
            detail=(
                f"A '{pipeline_type}' merge-enabled run from the same continuation group "
                f"({shared_related}) is already queued or running (id: {conflict_id})."
            ),
        )


def _require_configured_dirs(db: sqlite3.Connection, pipeline_type: str) -> tuple[str, str]:
    """Return (minknow_dir, output_dir); raise 422 when a required one is unset.

    squirrel reads prior artic output rather than raw FASTQ, so it needs no minknow_dir.
    """
    minknow_dir = lookup_setting(db, "minknow_dir")
    output_dir_stored = lookup_setting(db, "output_dir")
    if not output_dir_stored:
        raise HTTPException(status_code=422, detail="output_dir must be configured in Settings")
    if pipeline_type != "squirrel" and not minknow_dir:
        raise HTTPException(status_code=422, detail="minknow_dir must be configured in Settings")
    return minknow_dir or "", output_dir_stored


def _resolve_squirrel_source(
    db: sqlite3.Connection, payload: PipelineLaunchPayload, primary_ra: str
) -> tuple[Optional[str], Optional[str]]:
    """Return (clade, artic output dir) for a squirrel launch.

    The source is either a previous mpox/artic pipeline run (source_run_id) or an
    artic output directory already on disk for the requested run accession.
    """
    if payload.source_run_id:
        src_row = db.execute(
            "SELECT output_path, params FROM pipeline_runs WHERE id = ?",
            (payload.source_run_id,),
        ).fetchone()
        if not src_row:
            raise HTTPException(
                status_code=404, detail=f"Source run '{payload.source_run_id}' not found"
            )
        try:
            src_params = json.loads(src_row["params"] or "{}")
        except (TypeError, json.JSONDecodeError):
            src_params = {}
        clade = src_params.get("clade") or lookup_setting(db, "mpox_default_clade") or "cladeii"
        return clade, src_row["output_path"]

    if payload.run_accessions:
        output_dir = lookup_setting(db, "output_dir")
        if not output_dir:
            raise HTTPException(
                status_code=422, detail="output_dir must be configured in Settings"
            )
        artic_outdir = cb.build_outdir(
            coerce_path_for_shell(output_dir),
            _PIPELINE_SUBDIRS["mpox"],
            primary_ra,
        )
        clade = payload.clade or lookup_setting(db, "mpox_default_clade") or "cladeii"
        return clade, artic_outdir

    raise HTTPException(
        status_code=422, detail="squirrel requires source_run_id or run_accessions"
    )


def _reject_pre_launch_errors(
    db: sqlite3.Connection, payload: PipelineLaunchPayload, minknow_dir: str
) -> None:
    """Raise a single 422 listing every failed pre-launch check."""
    errors = _validate_pre_launch(
        db,
        payload.pipeline_type,
        payload.run_accessions,
        minknow_dir,
        source_run_id=payload.source_run_id,
    )
    if errors:
        raise HTTPException(
            status_code=422,
            detail="\n".join(["Pre-launch validation failed:"] + [f"  • {e}" for e in errors]),
        )


def _validate_mpox_options(payload: PipelineLaunchPayload) -> None:
    """mpox requires a clade and scheme_version, both from the known option lists."""
    if not payload.clade or not payload.scheme_version:
        raise HTTPException(
            status_code=422,
            detail="clade and scheme_version are required for the mpox pipeline",
        )
    mpox_options = get_mpox_options()
    valid_clades = set(mpox_options["clades"])
    valid_schemes = set(mpox_options["schemes"])
    if payload.clade not in valid_clades:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown clade: {payload.clade!r}. Valid clades: {sorted(valid_clades)}",
        )
    if payload.scheme_version not in valid_schemes:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown scheme_version: {payload.scheme_version!r}. "
                f"Valid schemes: {sorted(valid_schemes)}"
            ),
        )


def _build_launch_params(
    payload: PipelineLaunchPayload, auto_merge: bool, squirrel_clade: Optional[str]
) -> dict:
    """Build the params JSON stored on the pipeline_runs row."""
    if payload.pipeline_type == "mpox":
        return {
            "resume": payload.resume,
            "auto_merge": auto_merge,
            "clade": payload.clade,
            "scheme_version": payload.scheme_version,
        }
    if payload.pipeline_type == "squirrel":
        return {
            "resume": payload.resume,
            "auto_merge": False,
            "clade": squirrel_clade,
            "source_run_id": payload.source_run_id,
        }
    return {
        "resume": payload.resume,
        "auto_merge": auto_merge,
        "pipeline_options": payload.pipeline_options or {},
    }


def _compute_outdir(
    pipeline_type: str, output_dir_stored: str, primary_ra: str, file_identifier: str
) -> str:
    """Return the pipeline's output directory.

    It depends only on config, pipeline type and identifier — not on samplesheet
    contents — so it can be computed before the slow FASTQ concatenation, which the
    overwrite check and the DB record both need. mpox/squirrel key their output on
    the run accession; every other pipeline uses the file_identifier.
    """
    subdir = _PIPELINE_SUBDIRS.get(pipeline_type, f"outputs_{pipeline_type}")
    identifier = primary_ra if pipeline_type in ("mpox", "squirrel") else file_identifier
    return build_outdir(
        coerce_path_for_shell(normalize_for_storage(output_dir_stored)),
        subdir,
        identifier,
    )


def _check_overwrite(outdir: str, overwrite: bool) -> Path:
    """Return the outdir as a local path, raising 409 if it holds files already."""
    outdir_local = _resolve_output_path(outdir)
    if not overwrite and outdir_local.exists() and any(outdir_local.iterdir()):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "output_exists",
                "message": (
                    f"Output directory already exists and contains files: {outdir}. "
                    "Set overwrite=true to launch anyway."
                ),
                "output_path": outdir,
            },
        )
    return outdir_local


def _insert_pipeline_run(
    db: sqlite3.Connection,
    run_id: str,
    payload: PipelineLaunchPayload,
    params: dict,
    log_path: Path,
    outdir: str,
    tmp_dir: Path,
    now: str,
) -> None:
    """Insert the queued pipeline_runs row and its run_accession links."""
    db.execute(
        """INSERT INTO pipeline_runs
           (id, pipeline_type, status, params,
            log_file, output_path, work_dir, created_at, updated_at, created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            payload.pipeline_type,
            "queued",
            json.dumps(params),
            str(log_path),
            outdir,
            str(tmp_dir),
            now,
            now,
            payload.created_by or "",
        ),
    )
    for ra in payload.run_accessions:
        db.execute(
            "INSERT INTO pipeline_run_accessions (pipeline_run_id, run_accession) VALUES (?, ?)",
            (run_id, ra),
        )
    db.commit()


def _write_manifest_safely(
    db: sqlite3.Connection,
    output_dir_stored: str,
    pipeline_type: str,
    output_identifier: str,
    run_accessions: list[str],
    auto_merge: bool,
    log_path: Path,
    *,
    provisional: bool,
    failure_note: str,
) -> None:
    """Write run_manifest.txt, logging *failure_note* instead of raising on failure.

    The manifest is a record, not a precondition — a write failure must never abort
    a launch. Called twice per run: once at launch with the requested accessions
    (*provisional*, and the manifest says so), then again from prepare_fn with the
    accessions actually used (they can differ once merging pulls in continuation
    partners).
    """
    try:
        _write_run_manifest(
            db,
            output_dir_stored,
            _PIPELINE_SUBDIRS.get(pipeline_type, f"outputs_{pipeline_type}"),
            output_identifier,
            "",
            pipeline_type,
            run_accessions,
            auto_merge,
            provisional=provisional,
        )
    except Exception as manifest_err:
        _append_run_log(log_path, f"[ODIN] Warning: {failure_note}: {manifest_err}")


def _select_postprocess_fn(
    pipeline_type: str,
    run_accessions: list[str],
    outdir_local: str,
    log_path: Path,
    pipeline_options: Optional[dict],
) -> Optional[Callable[[], None]]:
    """Return the post-processing callback for a pipeline type, or None if it has none."""
    if pipeline_type == "taxprofiler":
        return _make_taxprofiler_postprocess_fn(
            _open_db, run_accessions, outdir_local, log_path, pipeline_options=pipeline_options
        )
    if pipeline_type == "wf_metagenomics_ssu":
        return _make_ssu_postprocess_fn(_open_db, run_accessions, outdir_local, log_path)
    if pipeline_type == "wf_metagenomics_amr":
        return _make_amr_postprocess_fn(_open_db, run_accessions, outdir_local, log_path)
    return None


@router.post("/runs", response_model=PipelineRunRead, status_code=201)
def launch_pipeline(
    payload: PipelineLaunchPayload,
    db: sqlite3.Connection = Depends(get_db),
):
    _validate_launch_request(payload)

    active_by_run = _load_active_run_accessions(db, payload.pipeline_type)
    _reject_conflicting_active_runs(payload.pipeline_type, active_by_run, payload.run_accessions)

    auto_merge_map = _resolve_auto_merge_map(db, payload.run_accessions)
    _reject_continuation_group_conflict(
        db, payload.pipeline_type, active_by_run, set(payload.run_accessions), auto_merge_map
    )

    # The first run_accession is the file_identifier base and the mpox/squirrel outdir key.
    primary_ra = payload.run_accessions[0]
    auto_merge = auto_merge_map.get(primary_ra, False)

    minknow_dir, output_dir_stored = _require_configured_dirs(db, payload.pipeline_type)

    _squirrel_source_clade: str | None = None
    _squirrel_artic_outdir: str | None = None
    if payload.pipeline_type == "squirrel":
        _squirrel_source_clade, _squirrel_artic_outdir = _resolve_squirrel_source(
            db, payload, primary_ra
        )

    _reject_pre_launch_errors(db, payload, minknow_dir)

    # File identifier: {sampleName}_{run_accession(s)} of the run(s) being processed
    file_identifier = _build_file_identifier(db, primary_ra, payload.run_accessions)

    run_id = str(uuid.uuid4())
    now = utc_now_str()

    # Log file lives alongside the output directory (or in ODIN_LOG_DIR if set)
    logs_dir = resolve_log_dir(output_dir_stored)
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{run_id[:8]}_{payload.pipeline_type}.log"

    if payload.pipeline_type == "mpox":
        _validate_mpox_options(payload)
    params_extra = _build_launch_params(payload, auto_merge, _squirrel_source_clade)

    outdir = _compute_outdir(payload.pipeline_type, output_dir_stored, primary_ra, file_identifier)
    outdir_local = _check_overwrite(outdir, payload.overwrite)

    # ── Samplesheet build + cmd — deferred to background thread ──────────────
    # FASTQ concatenation can take minutes for large runs; running it here would
    # block the HTTP response.  We pass a prepare_fn to the executor so it runs
    # inside the background thread before nextflow starts.
    # Build concatenated FASTQ files in a temporary workspace, preferably on ext4.
    # This keeps large intermediate .fastq.gz files off Windows/DrvFs output paths.
    tmp_dir = _create_pipeline_tmp_dir(run_id)

    plan = LaunchPlan(
        db_factory=_open_db,
        pipeline_type=payload.pipeline_type,
        run_accessions=payload.run_accessions,
        auto_merge=auto_merge,
        minknow_dir=minknow_dir,
        output_dir_stored=output_dir_stored,
        file_identifier=file_identifier,
        tmp_dir=tmp_dir,
        primary_ra=primary_ra,
        clade=payload.clade if payload.pipeline_type == "mpox" else _squirrel_source_clade,
        scheme_version=payload.scheme_version,
        resume=payload.resume,
        log_path=log_path,
        artic_outdir=_squirrel_artic_outdir,
        pipeline_options=payload.pipeline_options,
    )
    cmd_holder: list[str] = []
    prepare_fn = _make_prepare_fn(plan, cmd_holder)

    _insert_pipeline_run(
        db, run_id, payload, params_extra, log_path, outdir, tmp_dir, now
    )

    # Write non-blocking warnings (geography gaps, sample_code pattern) to the
    # run log before the pipeline starts so operators see them immediately.
    for _w in _collect_launch_warnings(db, payload.run_accessions):
        _append_run_log(log_path, f"[ODIN] Warning: {_w}")

    # Write the manifest up-front so it exists from the moment the response returns;
    # prepare_fn rewrites it in the background with the accessions actually used.
    _write_manifest_safely(
        db,
        output_dir_stored,
        payload.pipeline_type,
        plan.output_identifier,
        payload.run_accessions,
        auto_merge,
        log_path,
        provisional=True,
        failure_note="could not write run manifest at launch",
    )

    executor.launch(
        run_id, "", log_path, str(DB_PATH), prepare_fn=prepare_fn, cmd_holder=cmd_holder,
        postprocess_fn=_select_postprocess_fn(
            payload.pipeline_type,
            payload.run_accessions,
            str(outdir_local),
            log_path,
            payload.pipeline_options,
        ),
        cleanup_fn=_make_tmp_cleanup_fn(run_id, tmp_dir, str(DB_PATH), log_path),
    )

    row = db.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_read(row, db)


# ─────────────────────────────────────────────────────────────────────────────
# Cancel
# ─────────────────────────────────────────────────────────────────────────────


@router.delete("/runs/{run_id}", status_code=204)
def cancel_pipeline_run(run_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    if row["status"] not in ("queued", "running"):
        raise HTTPException(
            status_code=409, detail=f"Cannot cancel a run with status '{row['status']}'"
        )
    executor.cancel(run_id, str(DB_PATH))
    # If the executor had no live process (e.g. after a server restart) it
    # returns silently without updating the DB — force the status update here.
    db.execute(
        "UPDATE pipeline_runs SET status='cancelled', finished_at=?, updated_at=? WHERE id=? AND status IN ('queued','running')",
        (utc_now_str(), utc_now_str(), run_id),
    )
    db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Delete record
# ─────────────────────────────────────────────────────────────────────────────


@router.delete("/runs/{run_id}/record", status_code=204)
def delete_pipeline_run_record(run_id: str, db: sqlite3.Connection = Depends(get_db)):
    """Permanently remove a finished run record from the database."""
    row = db.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    if row["status"] in ("queued", "running"):
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a run that is queued or running. Cancel it first.",
        )
    db.execute("DELETE FROM pipeline_runs WHERE id = ?", (run_id,))
    db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Delete work directory
# ─────────────────────────────────────────────────────────────────────────────


@router.delete("/runs/{run_id}/workdir", status_code=204)
def delete_pipeline_run_workdir(run_id: str, db: sqlite3.Connection = Depends(get_db)):
    """Delete the Nextflow work directory for a finished run to free disk space.

    The work directory is derived from output_path: parent dir + '/work'.
    Deleting it removes the resume cache for this sample — subsequent runs
    cannot resume from this point.
    """
    row = db.execute(
        "SELECT status, output_path FROM pipeline_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    if row["status"] in ("queued", "running"):
        raise HTTPException(
            status_code=409,
            detail="Cannot delete the work directory of an active run.",
        )
    output_path = row["output_path"]
    if not output_path:
        raise HTTPException(status_code=404, detail="No output_path recorded for this run.")
    # Nextflow work dir sits one level above the pipeline output subdirectory
    # e.g. .../nanopore_processed/SAMPLE_ID/outputs_taxprofiler  -> .../SAMPLE_ID/work
    work_dir = _resolve_output_path(output_path).parent / "work"
    if not work_dir.exists():
        # Already gone — not an error
        return
    shutil.rmtree(work_dir)


# ─────────────────────────────────────────────────────────────────────────────
# Run manifest
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/runs/{run_id}/manifest", response_class=PlainTextResponse)
def get_run_manifest(run_id: str, db: sqlite3.Connection = Depends(get_db)):
    """Return the contents of run_manifest.txt for a pipeline run as plain text."""
    row = db.execute(
        "SELECT output_path FROM pipeline_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    output_path = row["output_path"]
    if not output_path:
        raise HTTPException(status_code=404, detail="No output path recorded for this run")
    manifest_path = _resolve_output_path(output_path) / "run_manifest.txt"
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="run_manifest.txt not found for this run")
    return manifest_path.read_text(encoding="utf-8", errors="replace")


@router.get("/runs/{run_id}/confidence-report", response_class=PlainTextResponse)
def get_confidence_report(run_id: str, db: sqlite3.Connection = Depends(get_db)):
    """Return the confidence report(s) produced by read extraction for a pipeline run."""
    row = db.execute(
        "SELECT output_path, params FROM pipeline_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    output_path = row["output_path"]
    if not output_path:
        raise HTTPException(status_code=404, detail="No output path recorded for this run")
    try:
        params = json.loads(row["params"] or "{}")
        extract_target = params.get("pipeline_options", {}).get("extract_target") or ""
    except (TypeError, json.JSONDecodeError):
        extract_target = ""
    if not extract_target:
        raise HTTPException(status_code=404, detail="No extract_target set for this run")
    analysis_dir = _resolve_output_path(output_path) / f"{extract_target}_analysis"
    if not analysis_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"No analysis directory found for target '{extract_target}'")
    reports = sorted(analysis_dir.glob("*_confidence_report.txt"))
    if not reports:
        raise HTTPException(status_code=404, detail="No confidence report files found")
    sep = "\n" + "─" * 60 + "\n"
    parts = [rp.read_text(encoding="utf-8", errors="replace").rstrip() for rp in reports]
    return sep.join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Standalone post-processing
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/runs/{run_id}/postprocess", status_code=202)
def trigger_postprocess(run_id: str, db: sqlite3.Connection = Depends(get_db)):
    """Trigger Kraken2 post-processing for a completed pipeline run.

    Supported pipeline types: ``taxprofiler``, ``wf_metagenomics_ssu``.
    Post-processing runs in a background thread; this endpoint returns 202
    immediately.  Progress is appended to the existing run log.
    """
    row = db.execute(
        "SELECT pipeline_type, status, output_path, log_file, params FROM pipeline_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    _supported = {"taxprofiler", "wf_metagenomics_ssu", "wf_metagenomics_amr"}
    if row["pipeline_type"] not in _supported:
        raise HTTPException(
            status_code=409,
            detail=f"Post-processing is only supported for: {', '.join(sorted(_supported))}",
        )
    if not row["output_path"]:
        raise HTTPException(status_code=409, detail="No output_path recorded for this run")
    if not row["log_file"]:
        raise HTTPException(status_code=409, detail="No log_file recorded for this run")

    try:
        stored_params = json.loads(row["params"] or "{}")
        pipeline_options = stored_params.get("pipeline_options") or {}
    except (TypeError, json.JSONDecodeError):
        pipeline_options = {}

    run_accessions = get_pipeline_run_accessions(db, run_id)

    output_path_local = coerce_path(row["output_path"])
    log_path = Path(coerce_path(row["log_file"]))
    if row["pipeline_type"] == "wf_metagenomics_ssu":
        postprocess_fn = _make_ssu_postprocess_fn(
            _open_db, run_accessions, output_path_local, log_path
        )
    elif row["pipeline_type"] == "wf_metagenomics_amr":
        postprocess_fn = _make_amr_postprocess_fn(
            _open_db, run_accessions, output_path_local, log_path
        )
    else:
        postprocess_fn = _make_taxprofiler_postprocess_fn(
            _open_db, run_accessions, output_path_local, log_path,
            pipeline_options=pipeline_options,
        )

    t = threading.Thread(target=postprocess_fn, daemon=True)
    t.start()
    return {"detail": "Post-processing started", "run_id": run_id}


# ─────────────────────────────────────────────────────────────────────────────
# SSE log stream
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/runs/{run_id}/logs")
async def stream_logs(
    run_id: str,
    offset: int = 0,
    db: sqlite3.Connection = Depends(get_db),
):
    row = db.execute(
        "SELECT log_file, status FROM pipeline_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Pipeline run not found")

    log_file = row["log_file"]
    current_status = row["status"]

    # If there is no log file yet, return a proper SSE stream with a terminal
    # status event rather than a 404.  A 404 causes EventSource to retry in a
    # tight loop; a clean SSE close stops it immediately.
    if not log_file:
        async def _no_log_stream():
            yield "data: (No log file recorded for this run)\n\n"
            yield f"event: status\ndata: {current_status}\n\n"

        return StreamingResponse(
            _no_log_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    log_path = Path(log_file)

    async def event_generator():
        last_byte = max(0, offset)  # resume from client-supplied offset on reconnect

        while True:
            text, last_byte = executor.tail_log(log_path, last_byte)
            if text:
                # Emit each line as a separate SSE data event; include the byte
                # offset as the event id so the client can resume after a reconnect.
                for line in text.splitlines():
                    yield f"id: {last_byte}\ndata: {line}\n\n"

            # Check if the run is still active
            con = connect_sqlite_with_retry(DB_PATH, context="pipeline sse status")
            try:
                status_row = con.execute(
                    "SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)
                ).fetchone()
                current_status = status_row["status"] if status_row else "unknown"
            finally:
                con.close()

            if current_status in ("done", "failed", "cancelled"):
                # Drain any remaining output
                text, last_byte = executor.tail_log(log_path, last_byte)
                if text:
                    for line in text.splitlines():
                        yield f"id: {last_byte}\ndata: {line}\n\n"
                yield f"event: status\ndata: {current_status}\n\n"
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Merge decisions
# ─────────────────────────────────────────────────────────────────────────────


def _find_related_runs(db: sqlite3.Connection, run_accession: str) -> list[MergeCandidate]:
    """Find continuation partners for a run (same flow cell, split run window)."""
    tw = get_continuation_window(db)
    partner_rows = find_continuation_partners(db, run_accession, tw)

    candidates: list[MergeCandidate] = []
    for prow in partner_rows:
        partner_ra = prow["partner"]
        if not partner_ra:
            continue
        detail = db.execute(
            """
            SELECT GROUP_CONCAT(DISTINCT nr.barcode) AS barcodes,
                   GROUP_CONCAT(DISTINCT s.sample_code) AS sample_codes
            FROM nanopore_runs nr
            JOIN nanopore_run_accessions nra ON nra.id = nr.accession_id
            LEFT JOIN samples s ON s.id = nr.sample_id
            WHERE nra.run_accession = ?
            """,
            (partner_ra,),
        ).fetchone()
        barcodes = [b for b in (detail["barcodes"] or "").split(",") if b]
        sample_codes = [s for s in (detail["sample_codes"] or "").split(",") if s]
        candidates.append(
            MergeCandidate(
                run_accession=partner_ra,
                barcodes=sorted(set(barcodes)),
                sample_codes=sorted(set(sample_codes)),
            )
        )
    return sorted(candidates, key=lambda c: c.run_accession)


@router.get(
    "/nanopore/{run_accession}/merge-candidates",
    response_model=MergeDecisionStatus,
)
def get_merge_candidates(run_accession: str, db: sqlite3.Connection = Depends(get_db)):
    if not validate_run_accession(run_accession):
        raise HTTPException(status_code=422, detail="Invalid run_accession format")
    related = _find_related_runs(db, run_accession)
    decision_row = db.execute(
        "SELECT auto_merge FROM nanopore_merge_decisions WHERE run_accession = ?",
        (run_accession,),
    ).fetchone()
    return MergeDecisionStatus(
        run_accession=run_accession,
        decision_made=decision_row is not None,
        auto_merge=bool(decision_row["auto_merge"]) if decision_row else None,
        related_runs=related,
    )


def _upsert_merge_decision(
    db: sqlite3.Connection,
    run_accession: str,
    auto_merge: int,
    snapshot: str,
    now: str,
    created_by: str,
) -> None:
    db.execute(
        """INSERT INTO nanopore_merge_decisions
               (run_accession, auto_merge, related_snapshot, created_at, updated_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(run_accession) DO UPDATE SET
               auto_merge=excluded.auto_merge,
               related_snapshot=excluded.related_snapshot,
               updated_at=excluded.updated_at""",
        (run_accession, auto_merge, snapshot, now, now, created_by),
    )


@router.put("/nanopore/{run_accession}/merge-decision", status_code=204)
def set_merge_decision(
    run_accession: str,
    payload: MergeDecisionPayload,
    db: sqlite3.Connection = Depends(get_db),
):
    if not validate_run_accession(run_accession):
        raise HTTPException(status_code=422, detail="Invalid run_accession format")

    related = _find_related_runs(db, run_accession)
    snapshot = json.dumps(sorted(r.run_accession for r in related))
    now = utc_now_str()
    auto_merge_int = 1 if payload.auto_merge else 0
    _upsert_merge_decision(db, run_accession, auto_merge_int, snapshot, now, payload.created_by or "")

    # Always propagate the same decision to every related run so the group stays consistent.
    for candidate in related:
        peer_related = _find_related_runs(db, candidate.run_accession)
        peer_snapshot = json.dumps(sorted(r.run_accession for r in peer_related))
        _upsert_merge_decision(db, candidate.run_accession, auto_merge_int, peer_snapshot, now, payload.created_by or "")

    db.commit()


@router.delete("/nanopore/{run_accession}/merge-decision", status_code=204)
def clear_merge_decision(run_accession: str, db: sqlite3.Connection = Depends(get_db)):
    if not validate_run_accession(run_accession):
        raise HTTPException(status_code=422, detail="Invalid run_accession format")
    db.execute("DELETE FROM nanopore_merge_decisions WHERE run_accession = ?", (run_accession,))
    db.commit()
