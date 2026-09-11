"""
AMR (Antimicrobial Resistance) post-processing service.

After a wf-metagenomics AMR pipeline run completes, this module:

  1. Searches the Nextflow output directory for AMR JSON files (one per barcode).
  2. Parses each JSON and enriches results with sample metadata from the SQLite database.
  3. Merges the new data with any existing amr_data.feather (incremental update).
  4. Writes amr_data.feather and amr_data_summary_by_resistance.feather to
     enlighten_data_path.

This is the in-process equivalent of pipeline_scripts/scripts/parse_amr_json.py
rewritten to query SQLite instead of an Excel file.
"""

from __future__ import annotations

import glob
import json
import logging
import os
from pathlib import Path

import pandas as pd

from .kraken_postprocessor import (
    add_metadata,
    merge_with_existing_file,
    save_subsets,
)
from .postprocess_common import (
    deduplicate_rows,
    load_postprocess_metadata,
    make_postprocess_logger,
    write_postprocess_sentinel,
)

logger = logging.getLogger(__name__)

# Log-line prefix for AMR post-processing output (highlighted by the frontend).
_AMR_LOG_MARKER = "[ODIN-POST-AMR]"


# ── JSON parsing ──────────────────────────────────────────────────────────────


def parse_amr_json_file(file_path: str, run_accession: str | None = None) -> list[dict]:
    """
    Parse a single AMR JSON file produced by epi2me-labs/wf-metagenomics --amr.

    Returns one record per resistance token (semicolon-delimited resistance values
    are expanded into individual rows for easier analysis).
    """
    try:
        with open(file_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        logger.warning(f"Could not read AMR JSON '{file_path}': {exc}")
        return []

    records: list[dict] = []
    for barcode_key, barcode_data in data.items():
        if not isinstance(barcode_data, dict):
            continue
        pass_status = barcode_data.get("pass")
        results = barcode_data.get("results", {})
        for gene_description, gene_data in results.items():
            if not isinstance(gene_data, dict):
                continue
            for meta in gene_data.get("meta", []):
                resistance_all = meta.get("RESISTANCE")
                tokens = resistance_all.split(";") if resistance_all else [None]
                short_name = (
                    ";".join(t[:4] for t in tokens if t) if tokens else None
                )
                records.append(
                    {
                        "run_accession": run_accession,
                        "barcode": barcode_key,
                        "pass_status": pass_status,
                        "gene_description": gene_description,
                        "multi_resistance": len(tokens) > 1 if tokens else False,
                        "resistance": resistance_all,
                        "short_name": short_name,
                        "count": gene_data.get("count", 0),
                        "sequence": meta.get("SEQUENCE"),
                        "start": meta.get("START"),
                        "end": meta.get("END"),
                        "coverage_percent": meta.get("%COVERAGE"),
                        "identity_percent": meta.get("%IDENTITY"),
                        "file": file_path,
                    }
                )
    return records


def _find_amr_json(output_path: str, barcode: str) -> list[str]:
    """Find {barcode}.amr.json files inside output_path (direct and recursive)."""
    patterns = [
        os.path.join(output_path, "amr", f"{barcode}.amr.json"),
        os.path.join(output_path, "**", "amr", f"{barcode}.amr.json"),
    ]
    found: list[str] = []
    for p in patterns:
        found.extend(glob.glob(p, recursive=True))
    return sorted(set(found))


# ── Main entry-point ──────────────────────────────────────────────────────────


def run_amr_postprocessing(
    run_accessions: list[str],
    output_path: str,
    enlighten_data_path: str,
    db_path: str,
    log_file: Path,
) -> None:
    """
    Post-process a completed wf-metagenomics AMR run.

    Searches output_path recursively for AMR JSON files, enriches them with
    sample/site metadata from the SQLite database, merges with any existing
    amr_data.feather, and writes updated Feather files to enlighten_data_path.

    Appends progress to log_file.  Never raises — errors are logged so the
    pipeline_run stays in 'done' status even if post-processing fails.
    """

    _log = make_postprocess_logger(log_file, _AMR_LOG_MARKER)

    _log("Starting AMR post-processing...")

    # ── 1. Get metadata from SQLite ───────────────────────────────────────────
    metadata_rows = load_postprocess_metadata(
        db_path, run_accessions, _log, skip_note="Skipping AMR post-processing."
    )
    if not metadata_rows:
        return

    # ── 2. Parse AMR JSON files and build combined DataFrame ──────────────────
    combined_df = pd.DataFrame()
    processed_run_accessions: set[str] = set()
    missing_files: set[str] = set()

    for row in metadata_rows:
        run_accession = row["run_accession"]
        barcode = row["barcode"]
        if not barcode:
            continue

        _log(f"Processing run_accession={run_accession}, barcode={barcode}")
        files = _find_amr_json(output_path, barcode)
        if not files:
            missing_files.add(f"{run_accession}/{barcode}")
            continue

        for file_path in files:
            records = parse_amr_json_file(file_path, run_accession=run_accession)
            if not records:
                continue
            tmp = pd.DataFrame(records)
            tmp["incomplete_data"] = False
            # Attach metadata columns from the DB row
            tmp = add_metadata(tmp, row)
            frames = [f for f in [combined_df, tmp] if not f.empty]
            combined_df = pd.concat(frames, axis=0)

        processed_run_accessions.add(run_accession)

    if missing_files:
        _log(f"Warning: no AMR JSON files found for: {sorted(missing_files)}")

    if combined_df.empty:
        _log("No AMR data parsed. Post-processing complete with no output written.")
        return

    # ── 3. Deduplicate ────────────────────────────────────────────────────────
    combined_df = deduplicate_rows(
        combined_df, _log, ignore_columns={"incomplete_data", "run_accession"}
    )

    # ── 4. Merge with existing amr_data.feather ───────────────────────────────
    os.makedirs(enlighten_data_path, exist_ok=True)
    amr_feather = os.path.join(enlighten_data_path, "amr_data.feather")
    _log(f"Merging with existing data in {enlighten_data_path} ...")
    merged_df = merge_with_existing_file(combined_df, processed_run_accessions, amr_feather)

    # ── 5. Save main dataset ──────────────────────────────────────────────────
    save_subsets([("amr_data", merged_df)], enlighten_data_path, "feather")
    _log(f"Saved amr_data.feather ({len(merged_df)} rows)")

    # ── 6. Build and save resistance summary ─────────────────────────────────
    _create_amr_summary(merged_df, enlighten_data_path, _log)

    _log("AMR post-processing complete.")

    write_postprocess_sentinel(output_path)


def _create_amr_summary(df: pd.DataFrame, output_path: str, log_fn) -> None:
    """Summarise by resistance gene and save amr_data_summary_by_resistance.feather."""
    if "resistance" not in df.columns:
        log_fn("No 'resistance' column — skipping summary.")
        return

    agg_spec: dict = {
        "resistance": "count",
        "run_accession": "nunique",
        "barcode": "nunique",
    }
    for col, agg in (
        ("sequence", "nunique"),
        ("coverage_percent", "mean"),
        ("identity_percent", "mean"),
    ):
        if col in df.columns:
            agg_spec[col] = agg
    if "sample_code" in df.columns:
        agg_spec["sample_code"] = lambda x: "; ".join(sorted(set(x.dropna().astype(str))))

    try:
        summary = df.groupby(["resistance"]).agg(agg_spec).round(2)
        summary.sort_values(by="resistance", ascending=True, inplace=True)
        out_file = os.path.join(output_path, "amr_data_summary_by_resistance.feather")
        summary.reset_index().to_feather(out_file)
        log_fn(f"Saved resistance summary ({len(summary)} rows) → {out_file}")
    except Exception as exc:
        log_fn(f"Warning: could not create resistance summary: {exc}")
