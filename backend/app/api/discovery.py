import json
import logging
import os
import re
import sqlite3
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from ..database import get_db
from ..db.queries import (
    fetch_run_barcodes,
    find_continuation_partners,
    get_continuation_window,
    get_related_run_accessions,
    get_run_accession_meta,
)
from ..disk_scan_cache import MinknowScanResult
from ..disk_scan_cache import get_scan as get_disk_scan
from ..parsers.discovery import BiomemeDirOnDisk, find_run_dir, scan_biomeme_dir, scan_run_barcodes
from ..parsers.minknow_run_info import (
    extract_from_run_accession,
    extract_kit_from_protocol,
    get_run_info,
)
from ..parsers.postprocess_common import POSTPROCESS_SENTINEL
from ..schemas import (
    BarcodeInfoRead,
    BarcodeReadiness,
    BarcodeRegisterInfo,
    BarcodeStatus,
    BiomemeDiscoveryResult,
    BiomemeFileStatus,
    BiomemeFolderStatus,
    ContinuationEvidence,
    ExcludePayload,
    MinknowRunInfoRead,
    NanoporeDiscoveryResult,
    NanoporeReadiness,
    NanoporeRegisterPayload,
    NanoporeRunStatus,
    PipelineRunSummary,
)
from ..settings_resolver import lookup_setting
from ..utils import coerce_path, utc_now_str, validate_run_accession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/discovery", tags=["discovery"])

_BIOMEME_SCAN_CACHE: dict[str, tuple[float, dict[str, BiomemeDirOnDisk]]] = {}

# Pipeline output lives under {output_dir}/nanopore_processed/outputs_*/…
_PROCESSED_SUBDIR = "nanopore_processed"
# mpox/artic output is keyed by run_accession and marked complete by its consensus FASTA.
_ARTIC_OUTPUT_SUBDIR = "outputs_wf_artic-mpxv-nf"
_ARTIC_CONSENSUS_FILE = "all_consensus.fasta"


def _processed_root(output_dir_str: str) -> Path:
    """Return the processed-output root for a stored output_dir setting."""
    return Path(coerce_path(output_dir_str)) / _PROCESSED_SUBDIR


def _match_output_dir_in_type(out_type_dir: Path, run_accession: str) -> Optional[Path]:
    """Return the run's directory within a single outputs_* folder, or None.

    Tries three layouts:
      1. outputs_*/{sampleName}_{run_accession}/  — run_accession embedded in folder name
      2. outputs_*/{file_id}/{run_accession}/      — run_accession as a subdirectory
      3. outputs_*/{run_accession}/                — run_accession directly under outputs_*
    """
    try:
        for candidate in out_type_dir.iterdir():
            if not candidate.is_dir():
                continue
            if run_accession in candidate.name:
                return candidate
            nested = candidate / run_accession
            if nested.is_dir():
                return nested
        direct = out_type_dir / run_accession
        if direct.is_dir():
            return direct
    except OSError:
        pass
    return None


def find_run_output_dirs(processed_root: Path, run_accession: str) -> list[Path]:
    """Return *all* pipeline output directories for *run_accession*.

    A run processed by several pipelines has one directory under each
    ``outputs_*`` folder (e.g. ``outputs_taxprofiler``,
    ``outputs_wf_metagenomics_amr``).  Output folders are scanned in sorted
    name order so results are deterministic regardless of filesystem iteration
    order.  Returns an empty list when nothing matches or an OSError occurs.
    """
    results: list[Path] = []
    try:
        out_type_dirs = sorted(
            (
                d
                for d in processed_root.iterdir()
                if d.is_dir() and d.name.startswith("outputs_")
            ),
            key=lambda d: d.name,
        )
    except OSError:
        return results
    for out_type_dir in out_type_dirs:
        match = _match_output_dir_in_type(out_type_dir, run_accession)
        if match is not None:
            results.append(match)
    return results


def find_run_output_dir(processed_root: Path, run_accession: str) -> Optional[Path]:
    """Return the first pipeline output directory for *run_accession*, or None.

    Thin wrapper over :func:`find_run_output_dirs` for callers that only need a
    single representative directory (e.g. completion / post-processing checks).
    """
    dirs = find_run_output_dirs(processed_root, run_accession)
    return dirs[0] if dirs else None


def scan_confidence_report_targets(output_dirs: list[Path]) -> list[str]:
    """Return sorted, de-duplicated extract targets that have a confidence
    report on disk, scanning every provided output directory.

    A target is present when a ``{target}_analysis/`` subdirectory contains at
    least one ``*_confidence_report.txt`` file.  Read extraction can attach to
    any pipeline, so all of a run's output directories must be scanned.
    """
    targets: set[str] = set()
    suffix = "_analysis"
    for out_dir in output_dirs:
        try:
            for subdir in out_dir.iterdir():
                if subdir.is_dir() and subdir.name.endswith(suffix):
                    if any(subdir.glob("*_confidence_report.txt")):
                        targets.add(subdir.name[: -len(suffix)])
        except OSError:
            continue
    return sorted(targets)


def _biomeme_scan_ttl_seconds() -> float:
    raw = os.getenv("ODIN_BIOMEME_SCAN_TTL_SECONDS", "300")
    try:
        return float(raw)
    except ValueError:
        return 300.0


def _scan_biomeme_cached(biomeme_dir_str: Optional[str], *, force_refresh: bool) -> dict[str, BiomemeDirOnDisk]:
    if not biomeme_dir_str:
        return {}
    ttl = _biomeme_scan_ttl_seconds()
    now_mono = time.monotonic()
    cached = _BIOMEME_SCAN_CACHE.get(biomeme_dir_str)
    if not force_refresh and cached and ttl > 0 and (now_mono - cached[0]) < ttl:
        return cached[1]

    disk_index: dict[str, BiomemeDirOnDisk] = {}
    for entry in scan_biomeme_dir(Path(coerce_path(biomeme_dir_str)) / "biomeme_input_data"):
        disk_index[entry.relative_path] = entry
    _BIOMEME_SCAN_CACHE[biomeme_dir_str] = (now_mono, disk_index)
    return disk_index


# utc_now_str() and lookup_setting() live in utils.py / settings.py — imported above.


_NOISE_THRESHOLD_DISABLED = 0


def _noise_threshold(db: sqlite3.Connection) -> int:
    """Return the minimum read count below which a barcode is flagged as noise.

    0 disables the warnings entirely, and that is the default: a threshold is a
    site-specific judgement, so ODIN does not invent one. An unset, blank or
    unparseable setting therefore means "no warnings" rather than some hidden
    number the operator never chose.
    """
    raw = lookup_setting(db, "barcode_min_reads")
    if not raw:
        return _NOISE_THRESHOLD_DISABLED
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "Ignoring non-numeric barcode_min_reads setting %r; noise warnings disabled", raw
        )
        return _NOISE_THRESHOLD_DISABLED


def _barcode_status(
    is_excluded: bool,
    in_metadata: bool,
    on_disk: bool,
    fastq_count: int,
) -> str:
    """Canonical barcode status: used in both the bulk scan and the per-run readiness check."""
    if is_excluded:
        return "excluded"
    if in_metadata and fastq_count > 0:
        return "ready"
    if in_metadata and on_disk and fastq_count == 0:
        return "no_files"
    if in_metadata:
        return "not_on_disk"
    return "not_in_metadata"


# ─────────────────────────────────────────────────────────────────────────────
# Nanopore – private helpers
# ─────────────────────────────────────────────────────────────────────────────


def _scan_disk_and_populate_cache(
    db: sqlite3.Connection,
    minknow_dir_str: Optional[str],
    scanned_at: str,
    *,
    force_scan: bool,
) -> tuple[dict[str, dict[str, int]], dict[str, str], dict[str, str], dict[str, str]]:
    """Perform a forced disk scan (always fresh), write results into nanopore_disk_cache,
    and return (disk_index, run_paths, disk_run_names, disk_sample_names)."""
    disk_index: dict[str, dict[str, int]] = {}
    run_paths: dict[str, str] = {}
    disk_run_names: dict[str, str] = {}
    disk_sample_names: dict[str, str] = {}
    if not minknow_dir_str:
        return disk_index, run_paths, disk_run_names, disk_sample_names

    result: MinknowScanResult = get_disk_scan(minknow_dir_str, force=force_scan)

    for ra, entry in result.entries.items():
        disk_index[ra] = {bc.barcode: bc.fastq_count for bc in entry.barcodes}
        if entry.run_path:
            run_paths[ra] = entry.run_path
            parts = entry.run_path.split("/")
            if len(parts) >= 3:
                disk_run_names[ra] = parts[-3]
                disk_sample_names[ra] = parts[-2]
            elif len(parts) == 2:
                disk_run_names[ra] = parts[-2]
        ri = entry.run_info
        logger.debug(
            "disk cache: %s  flow_cell=%s  started=%s  stopped=%s  kit=%s",
            ra, ri.flow_cell_id, ri.started, ri.acquisition_stopped, ri.sequencing_kit_extracted,
        )
        db.execute(
            """INSERT INTO nanopore_disk_cache
               (run_accession, flow_cell_id, run_started, run_stopped,
                sequencing_kit_id, run_name, scanned_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_accession) DO UPDATE SET
                   flow_cell_id      = COALESCE(excluded.flow_cell_id,      nanopore_disk_cache.flow_cell_id),
                   run_started       = COALESCE(excluded.run_started,        nanopore_disk_cache.run_started),
                   run_stopped       = COALESCE(excluded.run_stopped,        nanopore_disk_cache.run_stopped),
                   sequencing_kit_id = COALESCE(excluded.sequencing_kit_id, nanopore_disk_cache.sequencing_kit_id),
                   run_name          = COALESCE(excluded.run_name,           nanopore_disk_cache.run_name),
                   scanned_at        = excluded.scanned_at""",
            (
                ra,
                ri.flow_cell_id,
                ri.started,
                ri.acquisition_stopped,
                ri.sequencing_kit_extracted,
                ri.run_name,
                scanned_at,
            ),
        )

    _found = list(disk_index.keys())
    logger.info("disk scan complete: %d run folder(s) found under %s", len(_found), minknow_dir_str)
    if _found:
        _ph = ",".join("?" * len(_found))
        db.execute(
            f"DELETE FROM nanopore_disk_cache WHERE run_accession NOT IN ({_ph})",
            _found,
        )
    else:
        db.execute("DELETE FROM nanopore_disk_cache")

    # Persist cache updates so continuation detection works across requests.
    db.commit()

    return disk_index, run_paths, disk_run_names, disk_sample_names


def _related_run_accessions_for(db: sqlite3.Connection, run_accession: str) -> list[str]:
    """Return run_accessions sharing a barcode→sample_id with *run_accession*.

    Delegates to the shared query in db.queries — kept as a module-level shim
    so existing callers within this file don't need updating.
    """
    return get_related_run_accessions(db, run_accession)


def _detect_continuations(
    db: sqlite3.Connection,
    time_window_hours: float,
    related_map: dict[str, set[str]],
) -> tuple[dict[str, list[str]], dict[str, str], dict[str, ContinuationEvidence]]:
    """Query nanopore_disk_cache for continuation run pairs (same flow cell, close in time).
    Mutates related_map in place to include continuation partners.
    Returns (cont_partners_map, cont_confidence_map, cont_evidence_map)."""
    _fc_rows = db.execute(
        """
        SELECT DISTINCT ndc1.run_accession AS ra1, ndc2.run_accession AS ra2,
               'likely' AS confidence,
               ndc1.flow_cell_id AS flow_cell_id,
               (julianday(ndc2.run_started) - julianday(ndc1.run_stopped)) * 24 AS time_gap_hours,
               ndc1.sequencing_kit_id AS kit,
               CASE WHEN ndc1.run_name IS NOT NULL AND ndc1.run_name = ndc2.run_name
                    THEN ndc1.run_name ELSE NULL END AS run_name_match
        FROM nanopore_disk_cache ndc1
        JOIN nanopore_disk_cache ndc2
          ON  ndc1.flow_cell_id      = ndc2.flow_cell_id
         AND  ndc1.sequencing_kit_id = ndc2.sequencing_kit_id
         AND  ndc2.run_accession    != ndc1.run_accession
         AND  ndc1.flow_cell_id      IS NOT NULL
         AND  ndc1.sequencing_kit_id IS NOT NULL
         AND  ndc1.run_stopped       IS NOT NULL
         AND  ndc2.run_started       IS NOT NULL
         AND  (julianday(ndc2.run_started) - julianday(ndc1.run_stopped)) * 24
              BETWEEN 0 AND ?
        UNION ALL
        SELECT DISTINCT ndc1.run_accession AS ra1, ndc2.run_accession AS ra2,
               'possible' AS confidence,
               ndc1.flow_cell_id AS flow_cell_id,
               (julianday(ndc2.run_started) - julianday(ndc1.run_stopped)) * 24 AS time_gap_hours,
               COALESCE(ndc1.sequencing_kit_id, ndc2.sequencing_kit_id) AS kit,
               CASE WHEN ndc1.run_name IS NOT NULL AND ndc1.run_name = ndc2.run_name
                    THEN ndc1.run_name ELSE NULL END AS run_name_match
        FROM nanopore_disk_cache ndc1
        JOIN nanopore_disk_cache ndc2
          ON  ndc1.flow_cell_id   = ndc2.flow_cell_id
         AND  ndc2.run_accession != ndc1.run_accession
         AND  ndc1.flow_cell_id   IS NOT NULL
         AND  (ndc1.sequencing_kit_id IS NULL OR ndc2.sequencing_kit_id IS NULL)
         AND  ndc1.run_stopped   IS NOT NULL
         AND  ndc2.run_started   IS NOT NULL
         AND  (julianday(ndc2.run_started) - julianday(ndc1.run_stopped)) * 24
              BETWEEN 0 AND ?
        """,
        (time_window_hours, time_window_hours),
    ).fetchall()

    continuation_pairs: dict[frozenset, str] = {}
    continuation_evidence_raw: dict[frozenset, dict] = {}
    for fc_row in _fc_rows:
        key: frozenset = frozenset([fc_row["ra1"], fc_row["ra2"]])
        if continuation_pairs.get(key) != "likely":  # 'likely' wins over 'possible'
            continuation_pairs[key] = fc_row["confidence"]
            continuation_evidence_raw[key] = {
                "flow_cell_id": fc_row["flow_cell_id"],
                "time_gap_hours": fc_row["time_gap_hours"],
                "kit": fc_row["kit"],
                "run_name_match": fc_row["run_name_match"],
            }
        related_map.setdefault(fc_row["ra1"], set()).add(fc_row["ra2"])
        related_map.setdefault(fc_row["ra2"], set()).add(fc_row["ra1"])

    logger.info(
        "continuation detection: %d raw row(s) → %d unique pair(s): %s",
        len(_fc_rows),
        len(continuation_pairs),
        [(tuple(k), v) for k, v in continuation_pairs.items()] or "none",
    )

    cont_partners_map: dict[str, list[str]] = {}
    cont_confidence_map: dict[str, str] = {}
    cont_evidence_map: dict[str, ContinuationEvidence] = {}
    for key, conf in continuation_pairs.items():
        ra1, ra2 = tuple(key)
        ev = continuation_evidence_raw[key]
        cont_ev = ContinuationEvidence(**ev)
        for ra, partner in [(ra1, ra2), (ra2, ra1)]:
            cont_partners_map.setdefault(ra, []).append(partner)
            if cont_confidence_map.get(ra) != "likely":
                cont_confidence_map[ra] = conf
                cont_evidence_map[ra] = cont_ev

    return cont_partners_map, cont_confidence_map, cont_evidence_map


def _build_sample_usage_warning_map(db: sqlite3.Connection) -> dict[str, list[str]]:
    """Return per-run warnings for samples linked to multiple nanopore rows.

    This is a review-only warning: the pattern can be intentional, but it can
    also mean the same sample was assigned multiple barcodes by mistake.
    """
    rows = db.execute(
        """
        SELECT nra.run_accession, nr.barcode, s.sample_code, s.sampling_date,
               nra.protocol_id, nra.sequencing_kit_id
        FROM nanopore_runs nr
        JOIN nanopore_run_accessions nra ON nra.id = nr.accession_id
        LEFT JOIN samples s ON s.id = nr.sample_id
        WHERE nra.run_accession IS NOT NULL
          AND s.sample_code IS NOT NULL
          AND s.sampling_date IS NOT NULL
        """
    ).fetchall()

    rows_by_run_and_sample: dict[tuple[str, str, str, Optional[str], Optional[str]], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        run_accession = row["run_accession"]
        if not run_accession:
            continue
        key = (
            run_accession,
            row["sample_code"],
            row["sampling_date"],
            row["protocol_id"],
            row["sequencing_kit_id"],
        )
        rows_by_run_and_sample[key].append(row)

    warning_map: dict[str, list[str]] = defaultdict(list)
    for (run_accession, sample_code, sampling_date, protocol_id, sequencing_kit_id), sample_rows in rows_by_run_and_sample.items():
        sample_label = f"{sample_code} ({sampling_date})"

        barcodes = sorted({row["barcode"] for row in sample_rows if row["barcode"]})
        if len(barcodes) < 2:
            continue

        warning = (
            f"Review sample {sample_label}: multiple barcodes "
            f"({', '.join(barcodes)}) are linked in this run's metadata"
        )
        if protocol_id or sequencing_kit_id:
            warning += " for"
            if protocol_id:
                warning += f" protocol {protocol_id}"
            if sequencing_kit_id:
                warning += f" kit {sequencing_kit_id}"
        warning += ". This may be intentional, but it should be confirmed."

        warning_map[run_accession].append(warning)

    return {run_accession: sorted(set(warnings)) for run_accession, warnings in warning_map.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Nanopore
# ─────────────────────────────────────────────────────────────────────────────


def _check_output_dir(d: Path) -> tuple[bool, Optional[bool]]:
    """Return (has_output, postprocessing_fresh) for a pipeline run directory.

    has_output is True when the pipeline completed (multiqc or squirrel present).
    postprocessing_fresh is True when the .odin_postprocessed sentinel is newer than
    multiqc_report.html, False when output exists but the sentinel is absent/stale,
    and None when not applicable (no multiqc output, e.g. squirrel only)."""
    multiqc = d / "multiqc" / "multiqc_report.html"
    squirrel = d / "squirrel_output"
    if not (multiqc.is_file() or squirrel.is_dir()):
        return False, None
    sentinel = d / POSTPROCESS_SENTINEL
    if not sentinel.exists():
        # Output exists but post-processing has never been run
        fresh: Optional[bool] = False if multiqc.is_file() else None
    elif multiqc.is_file():
        fresh = sentinel.stat().st_mtime >= multiqc.stat().st_mtime
    else:
        fresh = None  # squirrel / mpox — post-processing not applicable
    return True, fresh


def _derive_run_status(
    run_excluded: bool, on_disk: bool, artic_on_disk: bool,
    in_metadata: bool, bc_statuses: list[BarcodeStatus],
) -> str:
    """Roll the per-barcode statuses up into a single run-level status.

    Excluded barcodes do not count against readiness.
    """
    if run_excluded:
        return "excluded"
    active_bcs = [b for b in bc_statuses if not b.is_excluded]
    meta_bcs = [b for b in active_bcs if b.in_metadata]
    ready_bcs = [b for b in meta_bcs if b.status == "ready"]
    not_in_meta_on_disk = [b for b in active_bcs if b.status == "not_in_metadata"]
    if not on_disk and not artic_on_disk:
        return "not_on_disk"
    if not in_metadata and not active_bcs:
        return "excluded"  # all barcodes excluded — treat as no-op
    if not in_metadata:
        return "not_in_metadata"
    if not_in_meta_on_disk:
        return "partial"
    if meta_bcs and len(ready_bcs) == len(meta_bcs):
        return "ready"
    if ready_bcs:
        return "partial"
    return "no_files"


def _load_exclusions(db: sqlite3.Connection) -> tuple[set[str], set[tuple[str, str]]]:
    """Return (excluded run_accessions, excluded (run_accession, barcode) pairs)."""
    excluded_runs = {
        r["run_accession"]
        for r in db.execute("SELECT run_accession FROM nanopore_run_exclusions").fetchall()
    }
    excluded_barcodes = {
        (r["run_accession"], r["barcode"])
        for r in db.execute(
            "SELECT run_accession, barcode FROM nanopore_barcode_exclusions"
        ).fetchall()
    }
    return excluded_runs, excluded_barcodes


def _load_registered_index(
    db: sqlite3.Connection,
) -> tuple[dict[str, dict[str, dict]], dict[str, str], dict[str, str]]:
    """Return (db_index, run_name_map, sample_name_map) for all registered runs.

    db_index maps run_accession -> {barcode -> barcode_meta}. Run-level metadata
    (protocol_id, kit, runName, sampleName) comes from nanopore_run_accessions;
    rows whose linked sample has been soft-deleted are excluded.
    """
    db_runs = db.execute(
        """SELECT nra.run_accession, nr.barcode,
                  nra.protocol_id, nra.sequencing_kit_id,
                  nra.runName, nra.sampleName,
                  CASE WHEN s.sample_code IS NOT NULL
                            AND nra.protocol_id IS NOT NULL
                            AND nra.sequencing_kit_id IS NOT NULL
                       THEN s.sample_code || '_' || nra.protocol_id || '_' || nra.sequencing_kit_id
                       ELSE NULL END AS minknow_sample_id,
                  s.id AS sample_id, s.sample_code, s.sampling_date
           FROM nanopore_runs nr
           JOIN nanopore_run_accessions nra ON nra.id = nr.accession_id
           LEFT JOIN samples s ON s.id = nr.sample_id
           WHERE nra.run_accession IS NOT NULL"""
    ).fetchall()
    db_index: dict[str, dict[str, dict]] = {}
    run_name_map: dict[str, str] = {}
    sample_name_map: dict[str, str] = {}
    for row in db_runs:
        db_index.setdefault(row["run_accession"], {})[row["barcode"]] = {
            "sample_id": row["sample_id"],
            "sample_code": row["sample_code"],
            "sampling_date": row["sampling_date"],
            "protocol_id": row["protocol_id"],
            "sequencing_kit_id": row["sequencing_kit_id"],
            "minknow_sample_id": row["minknow_sample_id"],
        }
        ra = row["run_accession"]
        if row["runName"] and ra not in run_name_map:
            run_name_map[ra] = row["runName"]
        if row["sampleName"] and ra not in sample_name_map:
            sample_name_map[ra] = row["sampleName"]
    return db_index, run_name_map, sample_name_map


def _load_merge_decisions(db: sqlite3.Connection) -> dict[str, bool]:
    """Return run_accession -> auto_merge flag."""
    return {
        r["run_accession"]: bool(r["auto_merge"])
        for r in db.execute(
            "SELECT run_accession, auto_merge FROM nanopore_merge_decisions"
        ).fetchall()
    }


def _load_related_map(db: sqlite3.Connection) -> dict[str, set[str]]:
    """Return run_accession -> set of other run_accessions sharing a (sample, barcode)."""
    related_rows = db.execute(
        """
        SELECT DISTINCT nra1.run_accession AS ra1, nra2.run_accession AS ra2
        FROM nanopore_runs nr1
        JOIN nanopore_run_accessions nra1 ON nra1.id = nr1.accession_id
        JOIN nanopore_runs nr2
          ON nr1.sample_id = nr2.sample_id
         AND nr1.barcode   = nr2.barcode
         AND nr2.accession_id != nr1.accession_id
         AND nr1.sample_id IS NOT NULL
        JOIN nanopore_run_accessions nra2 ON nra2.id = nr2.accession_id
        WHERE nra1.run_accession IS NOT NULL AND nra2.run_accession IS NOT NULL
        """
    ).fetchall()
    related_map: dict[str, set[str]] = {}
    for r in related_rows:
        related_map.setdefault(r["ra1"], set()).add(r["ra2"])
    return related_map


@dataclass
class PipelineRunIndex:
    """Pipeline-run history per run_accession, plus scalars for the most recent run.

    The last_* maps describe only the newest run of each accession (what the
    dashboard chip shows); runs_by_accession holds the full history, newest-first,
    for the run-manifest list dialog.
    """

    last_status: dict[str, str] = field(default_factory=dict)
    last_id: dict[str, str] = field(default_factory=dict)
    last_type: dict[str, str] = field(default_factory=dict)
    last_extract_target: dict[str, Optional[str]] = field(default_factory=dict)
    runs_by_accession: dict[str, list[PipelineRunSummary]] = field(default_factory=dict)


def _params_extract_target(params_json: Optional[str]) -> Optional[str]:
    """Read pipeline_options.extract_target out of a stored params JSON blob."""
    try:
        params = json.loads(params_json or "{}")
        return params.get("pipeline_options", {}).get("extract_target") or None
    except (TypeError, json.JSONDecodeError):
        return None


def _load_pipeline_runs(db: sqlite3.Connection) -> PipelineRunIndex:
    """Bulk-load every pipeline run, grouped by run_accession.

    Rows are read oldest-first so the last_* scalars — which later rows overwrite —
    end up holding the most recent run; each history list is built newest-first.
    """
    index = PipelineRunIndex()
    for row in db.execute(
        """SELECT pr.id, pr.status, pr.pipeline_type, pr.params, pr.created_at,
                  pra.run_accession
           FROM pipeline_runs pr
           JOIN pipeline_run_accessions pra ON pra.pipeline_run_id = pr.id
           ORDER BY pr.created_at ASC"""
    ).fetchall():
        ra = row["run_accession"]
        extract_target = _params_extract_target(row["params"])
        index.last_status[ra] = row["status"]
        index.last_id[ra] = row["id"]
        index.last_type[ra] = row["pipeline_type"]
        index.last_extract_target[ra] = extract_target
        index.runs_by_accession.setdefault(ra, []).insert(
            0,
            PipelineRunSummary(
                id=row["id"],
                pipeline_type=row["pipeline_type"],
                status=row["status"],
                created_at=row["created_at"],
                extract_target=extract_target,
            ),
        )
    return index


def _scan_artic_only_runs(
    output_dir_str: Optional[str], known_accessions: set[str]
) -> tuple[dict[str, bool], set[str]]:
    """Pre-scan the artic (mpox) output tree.

    Returns (artic_on_disk, disk_only_accessions). Runs that exist *only* as artic
    output — absent from both the DB and minknow_dir — are reported separately so
    they still appear on the dashboard.
    """
    artic_on_disk: dict[str, bool] = {}
    disk_only: set[str] = set()
    if not output_dir_str:
        return artic_on_disk, disk_only
    try:
        artic_root = _processed_root(output_dir_str) / _ARTIC_OUTPUT_SUBDIR
        if not artic_root.is_dir():
            return artic_on_disk, disk_only
        for entry in artic_root.iterdir():
            if entry.is_dir() and (entry / _ARTIC_CONSENSUS_FILE).is_file():
                artic_on_disk[entry.name] = True
                if entry.name not in known_accessions:
                    disk_only.add(entry.name)
    except OSError:
        pass
    return artic_on_disk, disk_only


@dataclass
class OutputDiskState:
    """What the processed-output tree on disk says about each run_accession."""

    completed: dict[str, bool] = field(default_factory=dict)
    postprocessing_fresh: dict[str, Optional[bool]] = field(default_factory=dict)
    confidence_targets: dict[str, list[str]] = field(default_factory=dict)
    artic_on_disk: dict[str, bool] = field(default_factory=dict)


def _scan_output_state(
    output_dir_str: Optional[str],
    accessions: list[str],
    artic_on_disk: dict[str, bool],
) -> OutputDiskState:
    """Scan each run's pipeline-output folders on disk.

    A run counts as complete when its output dir holds multiqc/multiqc_report.html
    (the final step of every nf-core pipeline) — see `_check_output_dir`. This is
    deliberately independent of the pipeline_runs table, so it still works when the
    app DB was reset or the pipeline was launched from the command line.

    `artic_on_disk` seeds the returned map with the pre-scan from
    `_scan_artic_only_runs`; entries for `accessions` are refreshed here.
    """
    state = OutputDiskState(artic_on_disk=dict(artic_on_disk))
    if not output_dir_str:
        return state
    try:
        processed_root = _processed_root(output_dir_str)
        if not processed_root.exists():
            return state
        for ra in accessions:
            output_dirs = find_run_output_dirs(processed_root, ra)
            first_dir = output_dirs[0] if output_dirs else None
            completed, fresh = _check_output_dir(first_dir) if first_dir else (False, None)
            state.completed[ra] = completed
            state.postprocessing_fresh[ra] = fresh

            # Read-extraction confidence reports can live under any of the run's
            # output dirs (e.g. taxprofiler, wf_metagenomics), so scan them all
            # rather than only the first match.
            targets = scan_confidence_report_targets(output_dirs)
            if targets:
                state.confidence_targets[ra] = targets

            # mpox/artic uses the run_accession directly as its folder name.
            artic_dir = processed_root / _ARTIC_OUTPUT_SUBDIR / ra
            state.artic_on_disk[ra] = (artic_dir / _ARTIC_CONSENSUS_FILE).is_file()
    except OSError:
        pass
    return state


def _build_barcode_statuses(
    run_accession: str,
    db_barcodes: dict[str, dict],
    disk_barcodes: dict[str, int],
    on_disk: bool,
    excluded_barcodes: set[tuple[str, str]],
) -> list[BarcodeStatus]:
    """Merge a run's registered barcodes with those found on disk, one status each."""
    statuses: list[BarcodeStatus] = []
    for barcode in sorted(set(db_barcodes) | set(disk_barcodes)):
        bc_in_meta = barcode in db_barcodes
        fastq_count = disk_barcodes.get(barcode, 0)
        bc_meta = db_barcodes.get(barcode, {})
        bc_excluded = (run_accession, barcode) in excluded_barcodes
        statuses.append(
            BarcodeStatus(
                barcode=barcode,
                in_metadata=bc_in_meta,
                fastq_count=fastq_count,
                sample_id=bc_meta.get("sample_id"),
                sample_code=bc_meta.get("sample_code"),
                sampling_date=bc_meta.get("sampling_date"),
                protocol_id=bc_meta.get("protocol_id"),
                sequencing_kit_id=bc_meta.get("sequencing_kit_id"),
                minknow_sample_id=bc_meta.get("minknow_sample_id"),
                is_excluded=bc_excluded,
                status=_barcode_status(bc_excluded, bc_in_meta, on_disk, fastq_count),
            )
        )
    return statuses


@router.get("/nanopore", response_model=NanoporeDiscoveryResult)
def discover_nanopore(
    force_refresh: bool = Query(default=False, description="Force a fresh disk scan (bypass cache)"),
    db: sqlite3.Connection = Depends(get_db),
):
    minknow_dir_str = lookup_setting(db, "minknow_dir")
    scanned_at = utc_now_str()

    excluded_runs, excluded_barcodes = _load_exclusions(db)

    db_index, run_name_map, sample_name_map = _load_registered_index(db)

    merge_decisions = _load_merge_decisions(db)

    pipeline_runs = _load_pipeline_runs(db)

    related_map = _load_related_map(db)

    disk_index, run_paths, disk_run_names, disk_sample_names = _scan_disk_and_populate_cache(
        db, minknow_dir_str, scanned_at, force_scan=force_refresh
    )

    _time_window = get_continuation_window(db)
    cont_partners_map, cont_confidence_map, cont_evidence_map = _detect_continuations(
        db, _time_window, related_map
    )
    sample_warning_map = _build_sample_usage_warning_map(db)

    output_dir_str = lookup_setting(db, "output_dir")
    artic_prescan, artic_disk_only_accessions = _scan_artic_only_runs(
        output_dir_str, set(db_index) | set(disk_index)
    )

    all_accessions = sorted(set(db_index) | set(disk_index) | artic_disk_only_accessions)

    output_state = _scan_output_state(output_dir_str, all_accessions, artic_prescan)

    runs: list[NanoporeRunStatus] = []

    for run_accession in all_accessions:
        in_metadata = run_accession in db_index
        on_disk = run_accession in disk_index
        run_excluded = run_accession in excluded_runs

        bc_statuses = _build_barcode_statuses(
            run_accession,
            db_index.get(run_accession, {}),
            disk_index.get(run_accession, {}),
            on_disk,
            excluded_barcodes,
        )

        artic_on_disk = output_state.artic_on_disk.get(run_accession, False)
        run_status = _derive_run_status(
            run_excluded, on_disk, artic_on_disk, in_metadata, bc_statuses
        )

        runs.append(
            NanoporeRunStatus(
                run_accession=run_accession,
                run_path=run_paths.get(run_accession),
                # DB values take precedence; fall back to folder-derived names for disk-only runs
                run_name=run_name_map.get(run_accession) or disk_run_names.get(run_accession),
                sample_name=sample_name_map.get(run_accession) or disk_sample_names.get(run_accession),
                in_metadata=in_metadata,
                on_disk=on_disk,
                barcodes=bc_statuses,
                is_excluded=run_excluded,
                status=run_status,
                related_run_accessions=sorted(related_map.get(run_accession, set())),
                auto_merge=merge_decisions.get(run_accession),
                last_pipeline_run_status=pipeline_runs.last_status.get(run_accession),
                last_pipeline_run_id=pipeline_runs.last_id.get(run_accession),
                last_pipeline_run_type=pipeline_runs.last_type.get(run_accession),
                last_pipeline_run_extract_target=pipeline_runs.last_extract_target.get(run_accession),
                pipeline_runs=pipeline_runs.runs_by_accession.get(run_accession, []),
                confidence_report_targets=output_state.confidence_targets.get(run_accession, []),
                output_on_disk=output_state.completed.get(run_accession, False),
                artic_on_disk=artic_on_disk,
                postprocessing_fresh=output_state.postprocessing_fresh.get(run_accession),
                metadata_warnings=sample_warning_map.get(run_accession, []),
                continuation_run_accessions=sorted(cont_partners_map.get(run_accession, [])),
                continuation_confidence=cont_confidence_map.get(run_accession),
                continuation_evidence=cont_evidence_map.get(run_accession),
            )
        )

    return NanoporeDiscoveryResult(minknow_dir=minknow_dir_str, scanned_at=scanned_at, runs=runs)


# ─────────────────────────────────────────────────────────────────────────────
# Exclusion toggles — nanopore
# ─────────────────────────────────────────────────────────────────────────────


@router.put("/nanopore/{run_accession}/exclude", status_code=204)
def exclude_nanopore_run(
    run_accession: str,
    payload: ExcludePayload,
    db: sqlite3.Connection = Depends(get_db),
):
    """Mark a run as excluded (idempotent)."""
    db.execute(
        """INSERT INTO nanopore_run_exclusions (run_accession, reason, created_by)
           VALUES (?, ?, ?)
           ON CONFLICT(run_accession) DO UPDATE SET reason=excluded.reason""",
        (run_accession, payload.reason, payload.created_by),
    )
    db.commit()


@router.delete("/nanopore/{run_accession}/exclude", status_code=204)
def unexclude_nanopore_run(
    run_accession: str,
    db: sqlite3.Connection = Depends(get_db),
):
    """Remove run exclusion."""
    db.execute("DELETE FROM nanopore_run_exclusions WHERE run_accession = ?", (run_accession,))
    db.commit()


@router.put("/nanopore/{run_accession}/barcodes/{barcode}/exclude", status_code=204)
def exclude_nanopore_barcode(
    run_accession: str,
    barcode: str,
    payload: ExcludePayload,
    db: sqlite3.Connection = Depends(get_db),
):
    """Mark a barcode within a run as excluded (idempotent)."""
    db.execute(
        """INSERT INTO nanopore_barcode_exclusions (run_accession, barcode, reason, created_by)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(run_accession, barcode) DO UPDATE SET reason=excluded.reason""",
        (run_accession, barcode, payload.reason, payload.created_by),
    )
    db.commit()


@router.delete("/nanopore/{run_accession}/barcodes/{barcode}/exclude", status_code=204)
def unexclude_nanopore_barcode(
    run_accession: str,
    barcode: str,
    db: sqlite3.Connection = Depends(get_db),
):
    """Remove barcode exclusion."""
    db.execute(
        "DELETE FROM nanopore_barcode_exclusions WHERE run_accession = ? AND barcode = ?",
        (run_accession, barcode),
    )
    db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Biomeme
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/biomeme", response_model=BiomemeDiscoveryResult)
def discover_biomeme(
    force_refresh: bool = Query(default=False, description="Force a fresh disk scan (bypass cache)"),
    db: sqlite3.Connection = Depends(get_db),
):
    biomeme_dir_str = lookup_setting(db, "biomeme_dir")
    scanned_at = utc_now_str()

    # Build lookup keyed by xlsx file stem (biomeme_run_name) → {sample_code, sampling_date}
    db_rows = db.execute(
        """SELECT br.biomeme_run_name, s.sample_code, s.sampling_date
           FROM biomeme_runs br
           LEFT JOIN samples s ON s.id = br.sample_id"""
    ).fetchall()
    db_index: dict[str, dict] = {
        r["biomeme_run_name"]: {
            "sample_code": r["sample_code"],
            "sampling_date": r["sampling_date"],
        }
        for r in db_rows
    }

    # Load excluded folders
    excluded_folders: set[str] = {
        r["folder_path"]
        for r in db.execute("SELECT folder_path FROM biomeme_folder_exclusions").fetchall()
    }

    # Scan disk: {folder_path: BiomemeDirOnDisk}
    disk_index = _scan_biomeme_cached(biomeme_dir_str, force_refresh=force_refresh)

    folders: list[BiomemeFolderStatus] = []
    for folder in sorted(disk_index.values(), key=lambda f: f.relative_path):
        is_excluded = folder.relative_path in excluded_folders
        files: list[BiomemeFileStatus] = []
        for run_name in folder.run_names:
            db_hit = db_index.get(run_name)
            files.append(
                BiomemeFileStatus(
                    run_name=run_name,
                    registered=db_hit is not None,
                    sample_code=db_hit["sample_code"] if db_hit else None,
                    sampling_date=db_hit["sampling_date"] if db_hit else None,
                )
            )

        registered_count = sum(1 for f in files if f.registered)
        if is_excluded:
            status = "excluded"
        elif registered_count == len(files) and files:
            status = "ready"
        elif registered_count > 0:
            status = "partial"
        else:
            status = "not_in_metadata"

        folders.append(
            BiomemeFolderStatus(
                folder_path=folder.relative_path,
                country_code=folder.country_code,
                folder_date=folder.sampling_date,
                file_count=folder.file_count,
                registered_count=registered_count,
                is_excluded=is_excluded,
                status=status,
                files=files,
            )
        )

    return BiomemeDiscoveryResult(biomeme_dir=biomeme_dir_str, scanned_at=scanned_at, folders=folders)


_FOLDER_PATH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9/_\-\.]{0,198}")


def _validate_folder_path(folder_path: str) -> None:
    if not _FOLDER_PATH_RE.fullmatch(folder_path) or ".." in folder_path:
        raise HTTPException(status_code=422, detail="Invalid folder_path")


@router.put("/biomeme/folders/{folder_path:path}/exclude", status_code=204)
def exclude_biomeme_folder(
    folder_path: str,
    payload: ExcludePayload,
    db: sqlite3.Connection = Depends(get_db),
):
    """Mark a biomeme disk folder as excluded (idempotent). folder_path e.g. 'DC/20250915'."""
    _validate_folder_path(folder_path)
    db.execute(
        """INSERT INTO biomeme_folder_exclusions (folder_path, reason, created_by)
           VALUES (?, ?, ?)
           ON CONFLICT(folder_path) DO UPDATE SET reason=excluded.reason""",
        (folder_path, payload.reason, payload.created_by),
    )
    db.commit()


@router.delete("/biomeme/folders/{folder_path:path}/exclude", status_code=204)
def unexclude_biomeme_folder(
    folder_path: str,
    db: sqlite3.Connection = Depends(get_db),
):
    """Remove folder exclusion."""
    _validate_folder_path(folder_path)
    db.execute("DELETE FROM biomeme_folder_exclusions WHERE folder_path = ?", (folder_path,))
    db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Readiness check — nanopore
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/nanopore/{run_accession}/confidence-report", response_class=PlainTextResponse)
def get_nanopore_confidence_report(
    run_accession: str, target: str, db: sqlite3.Connection = Depends(get_db)
):
    """Return confidence report(s) for a run by scanning disk, without requiring a pipeline_runs DB record."""
    if not validate_run_accession(run_accession):
        raise HTTPException(status_code=422, detail="Invalid run_accession format")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", target):
        raise HTTPException(status_code=422, detail="Invalid target identifier")
    
    output_dir_str = lookup_setting(db, "output_dir")
    if not output_dir_str:
        raise HTTPException(status_code=503, detail="output_dir not configured")
    processed_root = Path(coerce_path(output_dir_str)) / "nanopore_processed"
    if not processed_root.is_dir():
        raise HTTPException(status_code=404, detail="nanopore_processed directory not found")
    found_dirs = find_run_output_dirs(processed_root, run_accession)
    if not found_dirs:
        raise HTTPException(status_code=404, detail=f"No output directory found for run '{run_accession}'")
    # The target's analysis dir may live under any of the run's output dirs.
    reports: list[Path] = []
    for found_dir in found_dirs:
        analysis_dir = found_dir / f"{target}_analysis"
        if analysis_dir.is_dir():
            reports.extend(sorted(analysis_dir.glob("*_confidence_report.txt")))
    if not reports:
        raise HTTPException(
            status_code=404,
            detail=f"No confidence report found for target '{target}'",
        )
    sep = "\n" + "─" * 60 + "\n"
    return sep.join(rp.read_text(encoding="utf-8", errors="replace").rstrip() for rp in reports)


@router.get("/nanopore/{run_accession}/readiness", response_model=NanoporeReadiness)
def nanopore_readiness(run_accession: str, db: sqlite3.Connection = Depends(get_db)):
    """Route wrapper around :func:`_compute_readiness`."""
    return _compute_readiness(db, run_accession)


def _compute_readiness(db: sqlite3.Connection, run_accession: str) -> NanoporeReadiness:
    """
    Return a structured readiness report for a single nanopore run, telling the
    frontend exactly what is present, what is missing, and what action to offer.

    Plain helper (not a route) so nanopore_register can reuse it without one
    route function calling another.
    """
    if not validate_run_accession(run_accession):
        raise HTTPException(status_code=422, detail="Invalid run_accession format")
    minknow_dir_str = lookup_setting(db, "minknow_dir")
    sample_warning_map = _build_sample_usage_warning_map(db)

    # Keep nanopore_disk_cache in sync with the latest scan snapshot (TTL-based)
    # so continuation partners are available even when opening the wizard
    # directly (without a prior dashboard refresh).
    _scan_disk_and_populate_cache(
        db,
        minknow_dir_str,
        utc_now_str(),
        force_scan=False,
    )

    # ── Disk ──────────────────────────────────────────────────────────────────
    barcodes_on_disk: list[str] = []
    barcode_fastq: dict[str, int] = {}  # barcode → fastq count
    barcode_read_counts: dict[str, int] = {}  # barcode → read count from barcode_alignment
    on_disk = False
    sequencing_kit_raw: Optional[str] = None
    run_started: Optional[str] = None

    if minknow_dir_str:
        scan = get_disk_scan(minknow_dir_str)  # TTL-based; no forced rescan
        entry = scan.entries.get(run_accession)
        on_disk = entry is not None
        if on_disk:
            for bc in entry.barcodes:
                barcodes_on_disk.append(bc.barcode)
                barcode_fastq[bc.barcode] = bc.fastq_count
            run_info = entry.run_info
            barcode_read_counts = run_info.barcode_read_counts
            sequencing_kit_raw = run_info.sequencing_kit_raw
            run_started = run_info.started

    # ── Metadata ──────────────────────────────────────────────────────────────
    # Load per-barcode exclusions for this run from the exclusions table.
    # Kept separate so that barcodes on-disk but not yet in nanopore_runs
    # can still be checked against the exclusion list via the fallback below.
    excluded_barcodes: set[str] = {
        r["barcode"]
        for r in db.execute(
            "SELECT barcode FROM nanopore_barcode_exclusions WHERE run_accession = ?",
            (run_accession,),
        ).fetchall()
    }

    db_rows = fetch_run_barcodes(db, [run_accession], include_excluded=True)

    in_metadata = len(db_rows) > 0
    barcodes_in_metadata = [r["barcode"] for r in db_rows]

    # Per-barcode metadata map: barcode → {sample_id, sampling_date, site_id, site_code, ...}
    barcode_meta: dict[str, dict] = {}
    for r in db_rows:
        barcode_meta[r["barcode"]] = {
            "sample_id": r["sample_id"],
            "sample_code": r["sample_code"],
            "sampling_date": r["sampling_date"],
            "sample_type": r["sample_type"],
            "protocol_id": r["protocol_id"],
            "sequencing_kit_id": r["sequencing_kit_id"],
            "site_id": r["site_id"],
            "site_code": r["site_code"],
            "type": r["type"],
            "is_excluded": bool(r["is_excluded"]),
        }

    # Run-level sequencing metadata — read directly from nanopore_run_accessions.
    # This replaces the fragile "pick first row" pattern.
    nra_row = get_run_accession_meta(db, run_accession)
    existing_protocol_id = nra_row["protocol_id"] if nra_row else None
    existing_sequencing_kit_id = nra_row["sequencing_kit_id"] if nra_row else None

    # If the kit is not yet registered, try to match the disk-derived kit string
    # against lookup_values.external_code (exact) or lookup_values.code (exact).
    if existing_sequencing_kit_id is None and sequencing_kit_raw:
        kit_extracted = extract_kit_from_protocol(sequencing_kit_raw)
        if kit_extracted:
            # Try external_code first, then fall back to code
            kit_row = db.execute(
                """SELECT code FROM lookup_values
                   WHERE list = 'sequencing_kit_id'
                     AND (external_code = ? OR code = ?)
                   LIMIT 1""",
                (kit_extracted, kit_extracted),
            ).fetchone()
            if kit_row:
                existing_sequencing_kit_id = kit_row["code"]

    # Sample type is shared across the run — take from any registered barcode's sample
    first_with_sample = next((r for r in db_rows if r["sample_type"]), None)
    existing_sample_type = first_with_sample["sample_type"] if first_with_sample else None

    # Site is shared across the run — take from any registered barcode
    first_with_site = next((m for m in barcode_meta.values() if m["site_id"]), None)
    existing_site_id = first_with_site["site_id"] if first_with_site else None
    existing_site_code = first_with_site["site_code"] if first_with_site else None

    # Gap flags: only consider barcodes that are already registered (in nanopore_runs).
    # Unregistered on-disk barcodes are treated as implicitly excluded — they don't
    # block readiness and don't force the register step.
    missing_site = existing_site_id is None
    registered_barcodes = list(barcode_meta.keys())
    missing_sample = (
        any(barcode_meta[bc].get("sample_id") is None for bc in registered_barcodes)
        if registered_barcodes
        else not in_metadata
    )
    missing_sampling_date = (
        any(barcode_meta[bc].get("sampling_date") is None for bc in registered_barcodes)
        if registered_barcodes
        else not in_metadata
    )

    # ── Per-barcode readiness ─────────────────────────────────────────────────
    all_barcodes = sorted(set(barcodes_on_disk) | set(barcodes_in_metadata))
    bc_readiness: list[BarcodeReadiness] = []
    for barcode in all_barcodes:
        bc_in_meta = barcode in barcodes_in_metadata
        fastq_count = barcode_fastq.get(barcode, 0)
        bc_meta = barcode_meta.get(barcode, {})
        is_excluded = bc_meta.get("is_excluded", barcode in excluded_barcodes)
        bc_status = _barcode_status(is_excluded, bc_in_meta, on_disk, fastq_count)
        bc_readiness.append(
            BarcodeReadiness(
                barcode=barcode,
                fastq_count=fastq_count,
                in_metadata=bc_in_meta,
                is_excluded=is_excluded,
                sample_id=bc_meta.get("sample_id"),
                sample_code=bc_meta.get("sample_code"),
                sampling_date=bc_meta.get("sampling_date"),
                sample_type=bc_meta.get("sample_type"),
                protocol_id=bc_meta.get("protocol_id"),
                sequencing_kit_id=bc_meta.get("sequencing_kit_id"),
                site_id=bc_meta.get("site_id"),
                site_code=bc_meta.get("site_code"),
                read_count=barcode_read_counts.get(barcode, 0),
                type=bc_meta.get("type"),
                status=bc_status,
            )
        )

    # ── Run-level status & action ─────────────────────────────────────────────
    # Excluded barcodes don't count towards readiness in either direction.
    meta_bcs = [b for b in bc_readiness if b.in_metadata and not b.is_excluded]
    ready_bcs = [b for b in meta_bcs if b.status == "ready"]

    needs_registration = not in_metadata or missing_site or missing_sample or missing_sampling_date

    # ── Merge decision check ──────────────────────────────────────────────────
    # Only relevant when the run is fully registered (no registration needed)
    merge_decision_needed = False
    merge_decision_made = False
    merge_decision_stale = False
    auto_merge: Optional[bool] = None
    related_run_accessions: list[str] = []

    if in_metadata and not needs_registration:
        # Related runs: same barcode linked to the same sample_id across runs.
        related_run_accessions = _related_run_accessions_for(db, run_accession)

    # ── Continuation detection ────────────────────────────────────────────────
    continuation_run_accessions: list[str] = []
    continuation_confidence: Optional[str] = None
    continuation_evidence: Optional[ContinuationEvidence] = None

    _ndc_row = db.execute(
        "SELECT flow_cell_id, run_started, run_stopped, sequencing_kit_id, run_name"
        " FROM nanopore_disk_cache WHERE run_accession = ?",
        (run_accession,),
    ).fetchone()

    if _ndc_row and _ndc_row["flow_cell_id"]:
        _this_flow_cell = _ndc_row["flow_cell_id"]
        _this_kit = _ndc_row["sequencing_kit_id"]
        _tw = get_continuation_window(db)
        _cont_rows = find_continuation_partners(db, run_accession, _tw)

        for _cr in _cont_rows:
            _partner_kit = _cr["partner_kit"]
            if _this_kit and _partner_kit:
                _conf = "likely"
            else:
                _conf = "possible"
            continuation_run_accessions.append(_cr["partner"])
            # Keep highest-confidence evidence
            if continuation_confidence != "likely":
                continuation_confidence = _conf
                _this_run_name = _ndc_row["run_name"]
                _run_name_match = (
                    _this_run_name
                    if _this_run_name and _this_run_name == _cr["partner_run_name"]
                    else None
                )
                continuation_evidence = ContinuationEvidence(
                    flow_cell_id=_this_flow_cell,
                    time_gap_hours=_cr["gap_hours"],
                    kit=_this_kit or _partner_kit,
                    run_name_match=_run_name_match,
                )
        continuation_run_accessions = sorted(continuation_run_accessions)
        # Merge continuation partners into related_run_accessions (sample_id links already there)
        related_run_accessions = sorted(set(related_run_accessions) | set(continuation_run_accessions))

    if in_metadata and not needs_registration and related_run_accessions:
        decision_row = db.execute(
            "SELECT auto_merge, related_snapshot FROM nanopore_merge_decisions WHERE run_accession = ?",
            (run_accession,),
        ).fetchone()
        if decision_row is not None:
            snapshot = decision_row["related_snapshot"]
            snapshot_set = set(json.loads(snapshot)) if snapshot else None
            live_set = set(related_run_accessions)
            if snapshot_set is None or snapshot_set != live_set:
                # snapshot is NULL (predates this feature) or set has changed — re-prompt
                merge_decision_needed = True
                merge_decision_stale = True
            else:
                merge_decision_made = True
                auto_merge = bool(decision_row["auto_merge"])
        else:
            merge_decision_needed = True

    if not on_disk:
        run_status = "not_on_disk"
        action = "unavailable"
    elif needs_registration:
        run_status = "not_in_metadata" if not in_metadata else "partial"
        action = "register_launch"
    elif merge_decision_needed:
        run_status = "partial"
        action = "merge_decision_needed"
    elif meta_bcs and len(ready_bcs) == len(meta_bcs):
        run_status = "ready"
        action = "launch"
    elif ready_bcs:
        run_status = "partial"
        action = "launch_with_warn"
    else:
        run_status = "no_files"
        action = "launch_with_warn"

    return NanoporeReadiness(
        run_accession=run_accession,
        on_disk=on_disk,
        in_metadata=in_metadata,
        barcodes_on_disk=barcodes_on_disk,
        barcodes_in_metadata=barcodes_in_metadata,
        barcodes=bc_readiness,
        missing_site=missing_site,
        missing_sample=missing_sample,
        missing_sampling_date=missing_sampling_date,
        merge_decision_needed=merge_decision_needed,
        merge_decision_made=merge_decision_made,
        merge_decision_stale=merge_decision_stale,
        auto_merge=auto_merge,
        related_run_accessions=sorted(set(related_run_accessions)),
        existing_site_id=existing_site_id,
        existing_site_code=existing_site_code,
        existing_protocol_id=existing_protocol_id,
        existing_sequencing_kit_id=existing_sequencing_kit_id,
        existing_sample_type=existing_sample_type,
        sequencing_kit_raw=sequencing_kit_raw,
        run_started=run_started,
        metadata_warnings=sample_warning_map.get(run_accession, []),
        continuation_run_accessions=continuation_run_accessions,
        continuation_confidence=continuation_confidence,
        continuation_evidence=continuation_evidence,
        action=action,
        status=run_status,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Run-info endpoint — used by the Runs tab "Add run" form
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/nanopore/run-info", response_model=MinknowRunInfoRead)
def nanopore_run_info(
    run_accession: str,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Return lightweight MinKNOW metadata for a single run_accession.
    Used by the manual "Add run" form to opportunistically auto-fill fields
    and show a barcode helper table when the data happens to be on disk.

    Returns 404 when the run is not found — the caller silently degrades.
    All other fields are nullable; missing MinKNOW files produce None/[].
    """
    minknow_dir_str = lookup_setting(db, "minknow_dir")
    if not minknow_dir_str:
        raise HTTPException(status_code=404, detail="MinKNOW directory not configured")

    minknow_dir = Path(coerce_path(minknow_dir_str))
    run_dir = find_run_dir(minknow_dir, run_accession)
    if run_dir is None:
        raise HTTPException(status_code=404, detail="run_accession not found on disk")

    threshold = _noise_threshold(db)

    run_info = get_run_info(run_dir)

    # Derive run_name / sample_name from directory structure so they match
    # what the register endpoint stores.  The folder hierarchy is:
    #   {minknow_dir}/{runName}/{sampleName}/{run_accession}   (depth 3)
    #   {minknow_dir}/{runName}/{run_accession}                (depth 2)
    # protocol_group_id / sample_id from final_summary are used only as fallbacks.
    run_name_final: Optional[str] = None
    sample_name_final: Optional[str] = None
    try:
        rel_parts = run_dir.relative_to(minknow_dir).parts
        if len(rel_parts) >= 3:
            run_name_final = rel_parts[-3]
            sample_name_final = rel_parts[-2]
        elif len(rel_parts) == 2:
            run_name_final = rel_parts[-2]
    except ValueError:
        pass
    if not run_name_final:
        run_name_final = run_info.run_name       # protocol_group_id fallback
    if not sample_name_final:
        sample_name_final = run_info.sample_name  # sample_id fallback

    # Attempt kit lookup: external_code first, then code
    kit_id: Optional[str] = None
    if run_info.sequencing_kit_extracted:
        kit_row = db.execute(
            """SELECT code FROM lookup_values
               WHERE list = 'sequencing_kit_id'
                 AND (external_code = ? OR code = ?)
               LIMIT 1""",
            (run_info.sequencing_kit_extracted, run_info.sequencing_kit_extracted),
        ).fetchone()
        if kit_row:
            kit_id = kit_row["code"]

    # Merge barcode_alignment read counts with fastq_pass file counts
    disk_barcodes = scan_run_barcodes(run_dir)
    barcodes: list[BarcodeInfoRead] = []
    for bc in disk_barcodes:
        read_count = run_info.barcode_read_counts.get(bc.barcode, 0)
        is_used = threshold == 0 or read_count >= threshold
        barcodes.append(
            BarcodeInfoRead(
                barcode=bc.barcode,
                read_count=read_count,
                fastq_file_count=bc.fastq_count,
                is_used=is_used,
            )
        )

    return MinknowRunInfoRead(
        run_accession=run_accession,
        instrument=run_info.instrument,
        flow_cell_id=run_info.flow_cell_id,
        run_name=run_name_final,
        sample_name=sample_name_final,
        sequencing_kit_raw=run_info.sequencing_kit_raw,
        sequencing_kit_id=kit_id,
        run_started=run_info.started,
        barcodes=barcodes,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Register & launch — nanopore
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/nanopore/{run_accession}/register", response_model=NanoporeReadiness)
def nanopore_register(
    run_accession: str,
    payload: NanoporeRegisterPayload,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Atomically create/use existing site → sample → nanopore_run records for a
    run_accession, then return the updated readiness so the frontend can
    immediately proceed to launch.
    """
    now = utc_now_str()

    # ── Derive runName / sampleName from disk path ────────────────────────────
    # run_path relative to minknow_dir may be:
    #   depth 1: {run_accession}                       → no parents
    #   depth 2: {experiment}/{run_accession}           → runName only
    #   depth 3: {experiment}/{sample}/{run_accession}  → runName + sampleName
    _run_name_auto: Optional[str] = None
    _sample_name_auto: Optional[str] = None
    _flow_cell_id_auto: Optional[str] = None
    _run_started_auto: Optional[str] = None
    _run_stopped_auto: Optional[str] = None
    _disk_kit_auto: Optional[str] = None
    _disk_run_name_auto: Optional[str] = None
    _minknow_dir_str = lookup_setting(db, "minknow_dir")
    if _minknow_dir_str:
        _mkdir = Path(coerce_path(_minknow_dir_str))
        _run_dir = find_run_dir(_mkdir, run_accession)
        if _run_dir:
            try:
                _rel_parts = _run_dir.relative_to(_mkdir).parts
                if len(_rel_parts) == 3:
                    _run_name_auto, _sample_name_auto = _rel_parts[0], _rel_parts[1]
                elif len(_rel_parts) == 2:
                    _sample_name_auto = _rel_parts[0]
            except ValueError:
                pass
            _ri = get_run_info(_run_dir)
            _flow_cell_id_auto = _ri.flow_cell_id
            _run_started_auto = _ri.started
            _run_stopped_auto = _ri.acquisition_stopped
            _disk_kit_auto = _ri.sequencing_kit_extracted
            _disk_run_name_auto = _ri.run_name
        else:
            # Run not yet on disk — extract what we can from the folder name alone
            _flow_cell_id_auto, _run_started_auto = extract_from_run_accession(run_accession)

    # ── Resolve or create site ────────────────────────────────────────────────
    site_id = payload.site_id
    site_code_val: str = ""

    if not site_id:
        # Check whether every barcode-to-register supplies its own site_id.
        # If so, we don't need a run-level site — skip creation entirely.
        all_barcodes_have_site = bool(
            payload.barcodes and all(bc.site_id for bc in payload.barcodes)
        )

        if not all_barcodes_have_site:
            if not payload.country:
                raise HTTPException(status_code=422, detail="country is required to create a site")
            country_code = payload.country_code or ""
            city_code = payload.city_code or ""
            site_code = f"{country_code}{city_code}{payload.site or ''}".upper()

            # Restore a soft-deleted site with the same site_code rather than colliding
            existing_site = db.execute("SELECT id FROM sites WHERE site_code = ?", (site_code,)).fetchone()
            if existing_site:
                site_id = existing_site["id"]
                db.execute(
                    """UPDATE sites SET country=?, country_code=?, city_code=?,
                       city=?, location=?, latitude=?, longitude=?, updated_at=? WHERE id=?""",
                    (
                        payload.country,
                        payload.country_code or "",
                        payload.city_code or "",
                        payload.city or "",
                        payload.location or None,
                        payload.latitude,
                        payload.longitude,
                        now,
                        site_id,
                    ),
                )
            else:
                site_id = str(uuid.uuid4())
                db.execute(
                    """INSERT INTO sites (id, site_code, site, country, country_code, city_code, city,
                       location, latitude, longitude, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        site_id,
                        site_code,
                        payload.site or "",
                        payload.country,
                        payload.country_code or "",
                        payload.city_code or "",
                        payload.city or "",
                        payload.location or None,
                        payload.latitude,
                        payload.longitude,
                        now,
                        now,
                    ),
                )

    # Fetch site_code for deriving sample_code (only needed when site_id is set)
    if site_id:
        site_row = db.execute("SELECT site_code FROM sites WHERE id = ?", (site_id,)).fetchone()
        if not site_row:
            raise HTTPException(status_code=404, detail="Site not found")
        site_code_val = site_row["site_code"]

    # ── Upsert run-level accession record ────────────────────────────────────
    # protocol_id and sequencing_kit_id are run-level (same for all barcodes).
    run_protocol = payload.protocol_id
    run_kit = payload.sequencing_kit_id
    _nra_tmp_id = str(uuid.uuid4())
    db.execute(
        """INSERT INTO nanopore_run_accessions
           (id, run_accession, protocol_id, sequencing_kit_id,
            runName, sampleName,
            created_at, updated_at, created_by, updated_by)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(run_accession) DO UPDATE SET
               protocol_id = COALESCE(excluded.protocol_id, nanopore_run_accessions.protocol_id),
               sequencing_kit_id = COALESCE(excluded.sequencing_kit_id, nanopore_run_accessions.sequencing_kit_id),
               runName = COALESCE(excluded.runName, nanopore_run_accessions.runName),
               sampleName = COALESCE(excluded.sampleName, nanopore_run_accessions.sampleName),
               updated_at = excluded.updated_at,
               updated_by = excluded.updated_by""",
        (
            _nra_tmp_id,
            run_accession,
            run_protocol,
            run_kit,
            _run_name_auto,
            _sample_name_auto,
            now, now,
            payload.created_by,
            payload.created_by,
        ),
    )
    # Get the actual UUID (may differ from _nra_tmp_id when row already existed)
    _nra_id = db.execute(
        "SELECT id FROM nanopore_run_accessions WHERE run_accession = ?",
        (run_accession,),
    ).fetchone()["id"]
    # Keep disk cache in sync when a run is registered outside of a full scan
    db.execute(
        """INSERT INTO nanopore_disk_cache
           (run_accession, flow_cell_id, run_started, run_stopped,
            sequencing_kit_id, run_name, scanned_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(run_accession) DO UPDATE SET
               flow_cell_id      = COALESCE(excluded.flow_cell_id,      nanopore_disk_cache.flow_cell_id),
               run_started       = COALESCE(excluded.run_started,        nanopore_disk_cache.run_started),
               run_stopped       = COALESCE(excluded.run_stopped,        nanopore_disk_cache.run_stopped),
               sequencing_kit_id = COALESCE(excluded.sequencing_kit_id, nanopore_disk_cache.sequencing_kit_id),
               run_name          = COALESCE(excluded.run_name,           nanopore_disk_cache.run_name),
               scanned_at        = excluded.scanned_at""",
        (
            run_accession,
            _flow_cell_id_auto,
            _run_started_auto,
            _run_stopped_auto,
            _disk_kit_auto,
            _disk_run_name_auto or _run_name_auto,
            now,
        ),
    )

    # ── Build list of barcodes to register ───────────────────────────────────
    # If the frontend sent an explicit BarcodeRegisterInfo list (even empty), use it.
    # None means "not provided" → fall back to disk scan.
    # An empty list [] means "register nothing" (e.g. save-site-only).
    barcodes_to_register: list[BarcodeRegisterInfo]
    if payload.barcodes is not None:
        barcodes_to_register = payload.barcodes
    else:
        minknow_dir_str = lookup_setting(db, "minknow_dir")
        disk_barcodes: list[str] = []
        if minknow_dir_str:
            minknow_dir = Path(coerce_path(minknow_dir_str))
            fastq_pass = minknow_dir / run_accession / "fastq_pass"
            if fastq_pass.is_dir():
                disk_barcodes = sorted(d.name for d in fastq_pass.iterdir() if d.is_dir())
        barcodes_to_register = [BarcodeRegisterInfo(barcode=b) for b in disk_barcodes]

    # ── Cache: (site_id, sample_code_base, date) → (sample_id, sample_code) ─────
    sample_cache: dict[tuple[str, str, str], tuple[str, str]] = {}

    def _resolve_sample(
        effective_site_id: str, bc_sample_code_base: str, bc_sample_type: str, sampling_date: str
    ) -> tuple[str, str]:
        """Find or create sample for (effective_site_id, bc_sample_code_base, sampling_date). Returns (sample_id, sample_code)."""
        key = (effective_site_id, bc_sample_code_base, sampling_date)
        if key in sample_cache:
            return sample_cache[key]
        # Intentionally matches soft-deleted rows so they can be restored (upsert-restore pattern).
        existing = db.execute(
            "SELECT id FROM samples WHERE sample_code = ? AND sampling_date = ?",
            (bc_sample_code_base, sampling_date),
        ).fetchone()
        if existing:
            sid = existing["id"]
            db.execute(
                """UPDATE samples SET site_id=?, sample_type=?, updated_at=?
                   WHERE id=?""",
                (effective_site_id, bc_sample_type, now, sid),
            )
        else:
            sid = str(uuid.uuid4())
            db.execute(
                """INSERT INTO samples (id, site_id, sample_code, sample_type, sampling_date,
                   created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (sid, effective_site_id, bc_sample_code_base, bc_sample_type, sampling_date, now, now),
            )
        result = (sid, bc_sample_code_base)
        sample_cache[key] = result
        return result

    # ── Register each barcode ─────────────────────────────────────────────────
    for bc_info in barcodes_to_register:
        barcode = bc_info.barcode

        # Resolve per-barcode sample type (run-level protocol/kit are already in nanopore_run_accessions)
        bc_sample_type = bc_info.sample_type or payload.sample_type or ""

        # Per-barcode site override: look up the site_code for sample_code derivation
        bc_site_id = bc_info.site_id or site_id
        if not bc_site_id:
            # No site at all — skip this barcode (can't build a sample_code)
            continue
        if bc_site_id != site_id:
            bc_site_row = db.execute("SELECT site_code FROM sites WHERE id = ?", (bc_site_id,)).fetchone()
            if not bc_site_row:
                raise HTTPException(status_code=404, detail=f"Site '{bc_site_id}' not found for barcode '{barcode}'")
            bc_site_code_val = bc_site_row["site_code"]
        else:
            bc_site_code_val = site_code_val

        # Resolve sample_id and sample_code for this barcode
        if bc_info.sample_id:
            # Caller supplied an existing sample — validate it exists and is not deleted
            sample_row_bc = db.execute(
                "SELECT id, sample_code FROM samples WHERE id = ?",
                (bc_info.sample_id,),
            ).fetchone()
            if not sample_row_bc:
                raise HTTPException(
                    status_code=404,
                    detail=f"Sample '{bc_info.sample_id}' not found for barcode '{barcode}'",
                )
            bc_sample_id = sample_row_bc["id"]
            sc = sample_row_bc["sample_code"]
        elif bc_info.sampling_date and bc_sample_type:
            bc_sample_code_base = f"{bc_site_code_val}_{bc_sample_type}"
            bc_sample_id, sc = _resolve_sample(
                bc_site_id, bc_sample_code_base, bc_sample_type, bc_info.sampling_date
            )
        else:
            # Incomplete — skip this barcode; it can be completed later.
            continue
        existing = db.execute(
            "SELECT nr.id FROM nanopore_runs nr"
            " JOIN nanopore_run_accessions nra ON nra.id = nr.accession_id"
            " WHERE nra.run_accession = ? AND nr.barcode = ?",
            (run_accession, barcode),
        ).fetchone()
        if existing:
            # Always re-link to the current sample (handles date correction too)
            db.execute(
                """UPDATE nanopore_runs SET sample_id=?,
                   type=COALESCE(?, type),
                   updated_at=?, created_by=?
                   WHERE id=?""",
                (
                    bc_sample_id,
                    bc_info.type or None,
                    now,
                    payload.created_by or "",
                    existing["id"],
                ),
            )
        else:
            db.execute(
                """INSERT INTO nanopore_runs
                   (id, accession_id, sample_id, barcode, type,
                    created_at, updated_at, created_by)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    _nra_id,
                    bc_sample_id,
                    barcode,
                    bc_info.type or None,
                    now,
                    now,
                    payload.created_by or "",
                ),
            )

    db.commit()

    # Return fresh readiness after registration
    return _compute_readiness(db, run_accession)
