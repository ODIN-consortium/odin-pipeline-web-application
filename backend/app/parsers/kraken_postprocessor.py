"""
Kraken2 / Taxprofiler post-processing service.

After a Taxprofiler pipeline run completes, this module:

  1. Searches the Nextflow output directory for Kraken2 report files (one per barcode).
  2. Joins each report with sample metadata read from the SQLite database.
  3. Annotates each row with pathogen classifications from a reference Excel file.
  4. Merges the new data with any existing Feather files (incremental update).
  5. Writes Feather files to the Enlighten data directory, split by country.

This is the in-process equivalent of pipeline_scripts/scripts/create_kraken_datasets.py
rewritten to query SQLite instead of an Excel file.  All pure data-transformation
functions are ported from the original script to ensure identical output.
"""

from __future__ import annotations

import logging
import os
import warnings
from difflib import get_close_matches
from pathlib import Path
from typing import Literal

import pandas as pd

from .kraken_parser import RANK_COLUMN_MAPPING, enrich_kraken2_lineage, read_kraken2_report
from .postprocess_common import (
    deduplicate_rows,
    load_postprocess_metadata,
    make_postprocess_logger,
    write_postprocess_sentinel,
)

warnings.filterwarnings("ignore", message="Data Validation extension is not supported")

logger = logging.getLogger(__name__)

# Log-line prefix for Kraken2/SSU post-processing output (highlighted by the frontend).
_KRAKEN_LOG_MARKER = "[ODIN-POST]"

KRAKEN2_COLUMN_NAMES = [
    "Pct. of Frags",
    "No of Frags root",
    "No of Frags",
    "Rank code",
    "NCBI tax ID",
    "Scientific name",
]


# ── Pure data-transformation functions ────────────────────────────────────────
# Ported from create_kraken_datasets.py and nanopore_metadata.py.
# These must stay pure (no CLI, no logging setup, no side-effects at import time).


def set_column_dtypes(df: pd.DataFrame, dtype_dict: dict) -> pd.DataFrame:
    """Set data types for specific columns in a DataFrame."""
    df_copy = df.copy()
    for col, dtype in dtype_dict.items():
        if col in df_copy.columns:
            try:
                df_copy[col] = df_copy[col].astype(dtype)
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to convert column '{col}' to {dtype}: {e}")
        else:
            logger.warning(f"Column '{col}' not found in DataFrame")
    return df_copy


def kraken2_to_dataframe(fpath: str) -> pd.DataFrame:
    """Convert a Kraken2 report file to a DataFrame with enriched lineage."""
    df0 = read_kraken2_report(fpath, KRAKEN2_COLUMN_NAMES)
    if df0.empty:
        logger.warning(f"Empty kraken2 report file: {fpath}")
        return pd.DataFrame()
    df = enrich_kraken2_lineage(df0, RANK_COLUMN_MAPPING)
    df.reset_index(drop=True, inplace=True)
    return df


def create_kraken_dataset(
    data_folder: str,
    prefix: str = "barcode",
    suffix: str = "kraken2.report.txt",
) -> pd.DataFrame:
    """
    Combine all Kraken2 report files under data_folder that match prefix*suffix.

    Uses Path.rglob() for cross-platform recursive search (avoids Windows
    backslash / forward-slash mismatch that breaks glob.glob with **).  
    """
    df_out = pd.DataFrame()
    base = Path(data_folder)
    if not base.is_dir():
        # A missing search root is a configuration/path bug, never a valid
        # "no results" state — rglob() on it returns an empty iterator without
        # raising, which once masked a path-coercion bug as a silent empty
        # post-processing run. Fail loudly instead.
        raise FileNotFoundError(
            f"Kraken2 search root does not exist: {data_folder} "
            f"(expected the pipeline output directory — check path settings)"
        )
    glob_pattern = f"{prefix}*{suffix}"
    found_files = list(base.rglob(glob_pattern))
    if not found_files:
        logger.warning(f"No kraken2 reports found in {data_folder} matching: {glob_pattern}")
        return df_out
    logger.debug(f"Found {len(found_files)} kraken2 report(s) in {data_folder}")
    for file_path in found_files:
        df = kraken2_to_dataframe(str(file_path))
        df["file_name"] = file_path.name
        df_out = pd.concat([df_out, df], axis=0)
    return df_out


def add_metadata(df: pd.DataFrame, metadata_row: dict) -> pd.DataFrame:
    """Add sample metadata columns to a DataFrame.

    Applies every key/value pair from *metadata_row* to *df*, overwriting any
    existing column with the same name.  The row is expected to come from
    ``get_metadata_rows_from_db`` in ``db.queries`` — see that function for
    the full column contract.
    """
    for col, val in metadata_row.items():
        df[col] = val
    return df


def split_dataset(
    df: pd.DataFrame,
    split_by: str = "country",
    dataset_label: str = "taxprofiler",
) -> list[tuple[str, pd.DataFrame]]:
    """Split DataFrame into named subsets by unique values in split_by column.

    The *dataset_label* is appended to each subset name, e.g.
    ``"Norway_taxprofiler"`` or ``"Norway_ssu"``.
    """
    if split_by not in df.columns:
        logger.error(f"Split-by column '{split_by}' not found; falling back to 'country'.")
        split_by = "country"
    subsets = []
    for value in df[split_by].dropna().unique():
        subset_df = df[df[split_by] == value]
        if not subset_df.empty:
            subsets.append((f"{value}_{dataset_label}", subset_df))
    # Samples whose site has no country (or no site assigned at all)
    null_df = df[df[split_by].isna()]
    if not null_df.empty:
        logger.warning(
            "%d row(s) have no country set (sample missing site or site missing country). "
            "Saving to 'Unknown_%s'.",
            len(null_df),
            dataset_label,
        )
        subsets.append((f"Unknown_{dataset_label}", null_df))
    return subsets


def merge_with_existing_file(
    new_df: pd.DataFrame,
    run_accessions: set[str],
    existing_file: str,
    exclude_cols: set[str] | None = None,
) -> pd.DataFrame:
    """
    Merge new_df with an existing Feather file, replacing rows for run_accessions.

    Rows whose run_accession is in run_accessions are removed from the existing file
    before concatenation so re-processed runs replace (not duplicate) old results.
    """
    if not os.path.exists(existing_file):
        return new_df
    try:
        existing_df = pd.read_feather(existing_file)
    except Exception as e:
        logger.warning(
            f"Could not read existing feather '{existing_file}': {e}. Using only new data."
        )
        return new_df

    if "run_accession" in existing_df.columns:
        existing_df = existing_df[~existing_df["run_accession"].isin(run_accessions)]

    logger.info(
        f"Merging: existing={len(existing_df)} rows, new={len(new_df)} rows"
        f" from {existing_file}"
    )
    merged_df = pd.concat([existing_df, new_df], ignore_index=True)

    if exclude_cols is None:
        exclude_cols = {"run_accession", "runName"}
        if "incomplete_data" in merged_df.columns:
            exclude_cols.add("incomplete_data")

    dedup_cols = [c for c in merged_df.columns if c not in exclude_cols]
    try:
        merged_df.drop_duplicates(subset=dedup_cols, inplace=True, keep="first")
    except Exception as e:
        logger.warning(f"Deduplication failed: {e}. Proceeding without dedup.")

    merged_df.reset_index(drop=True, inplace=True)
    logger.info(f"Merged dataset: {len(merged_df)} total rows")
    return merged_df


def merge_datasets_with_existing(
    subsets: list[tuple[str, pd.DataFrame]],
    run_accessions: set[str],
    output_path: str,
) -> list[tuple[str, pd.DataFrame]]:
    """Merge each subset with its counterpart Feather file in output_path."""
    merged = []
    for name, subset_df in subsets:
        existing_file = os.path.join(output_path, f"{name}.feather")
        merged_df = merge_with_existing_file(subset_df, run_accessions, existing_file)
        merged.append((name, merged_df))
    return merged


def save_subsets(
    subsets: list[tuple[str, pd.DataFrame]],
    output_path: str,
    ds_type: Literal["feather", "xlsx"] = "feather",
) -> None:
    """Save each subset DataFrame to feather or xlsx in output_path."""
    os.makedirs(output_path, exist_ok=True)
    for name, subset_df in subsets:
        out_file = os.path.join(output_path, f"{name}.{ds_type}")
        if ds_type == "xlsx":
            subset_df.to_excel(out_file, index=False)
        else:
            subset_df.to_feather(out_file)
        logger.info(f"Saved {len(subset_df)} rows → {out_file}")


def create_target_groups(
    subsets: list[tuple[str, pd.DataFrame]],
) -> list[tuple[str, pd.DataFrame]]:
    """Create target-group subsets (rows where 'Target group' is not null)."""
    target_groups = []
    for name, subset_df in subsets:
        if "Target group" in subset_df.columns:
            tg_df = subset_df.loc[subset_df["Target group"].notna()].copy()
            if not tg_df.empty:
                tg_df.reset_index(drop=True, inplace=True)
                target_groups.append((f"{name}_target_group", tg_df))
    return target_groups


# ── Pathogen annotation (reads same Excel format as original) ─────────────────


def filter_and_cast_to_int(filename: str, df: pd.DataFrame, col_name: str) -> pd.DataFrame:
    """Filter rows with invalid values in col_name and cast to int."""
    numeric_col = pd.to_numeric(df[col_name], errors="coerce")
    mask_valid = numeric_col.notna() & (numeric_col % 1 == 0)
    invalid_values = df.loc[~mask_valid, col_name].unique()
    if len(invalid_values) > 0:
        logger.warning(
            f"Invalid (non-integer) values in {filename} column '{col_name}' will be skipped:"
            f" {list(invalid_values)}"
        )
    df_valid = df.loc[mask_valid].copy()
    df_valid[col_name] = numeric_col[mask_valid].astype(int)
    return df_valid


def correct_column_names(
    df: pd.DataFrame, required_columns: list, threshold: float = 0.8
) -> pd.DataFrame:
    """Fuzzy-rename columns to match required_columns (corrects minor typos)."""
    corrected_columns = df.columns.to_list()
    existing_columns = set(df.columns)
    for correct_col in required_columns:
        if correct_col not in existing_columns:
            close_matches = get_close_matches(correct_col, df.columns, n=1, cutoff=threshold)
            if close_matches:
                matched_col = close_matches[0]
                col_index = corrected_columns.index(matched_col)
                corrected_columns[col_index] = correct_col
            else:
                raise ValueError(
                    f"Column '{correct_col}' missing and no close match found"
                    f" in: {list(df.columns)}"
                )
    df.columns = corrected_columns
    return df


def find_sheet_with_column(file_path: str, column: str) -> pd.DataFrame:
    """Find the first Excel sheet containing column (case-insensitive)."""
    low_column = column.lower()
    xls = pd.ExcelFile(file_path)
    for sheet_name in xls.sheet_names:
        df_raw = pd.read_excel(xls, sheet_name=sheet_name, header=None)
        for idx, row in df_raw.iterrows():
            values = [str(val).strip().lower() for val in row.values]
            if low_column in values:
                return pd.read_excel(xls, sheet_name=sheet_name, header=idx)
    raise ValueError(f"'{column}' column not found in any sheet of {file_path}.")


def read_pathogens(pathogens_file: str) -> pd.DataFrame:
    """Read and validate the pathogens reference Excel file."""
    required_columns = [
        "domain [bacterial/viral/eukaryotic]",
        "Priority",
        "Target group",
        "Priority pathogen group",
        "scientific name",
        "TaxonomyID",
    ]
    sheet_name = "comprehensive_pathogen_list"
    pathogens = None
    try:
        pathogens = pd.read_excel(
            pathogens_file, sheet_name=sheet_name, header=2, dtype=str
        )
        pathogens = correct_column_names(pathogens, required_columns)
    except ValueError:
        pass
    if pathogens is None or not all(col in pathogens.columns for col in required_columns):
        pathogens = find_sheet_with_column(pathogens_file, column="Target group")
        pathogens = correct_column_names(pathogens, required_columns)
    if not all(col in pathogens.columns for col in required_columns):
        raise ValueError(f"Pathogens file must contain: {required_columns}")
    pathogens = pathogens.dropna(subset=["TaxonomyID"])
    pathogens = filter_and_cast_to_int(pathogens_file, pathogens, "TaxonomyID")
    pathogens.rename(columns={"domain [bacterial/viral/eukaryotic]": "Type"}, inplace=True)
    return pathogens


def get_base_rank(rank: str) -> str:
    return "".join(filter(str.isalpha, rank))


def get_relevant_columns(rank: str, column_mapping: dict) -> list:
    rank_order = list(column_mapping.keys())
    base_rank = get_base_rank(rank)
    if base_rank in rank_order:
        index = rank_order.index(base_rank)
        return [column_mapping[r] for r in rank_order[: index + 1]]
    return []


def create_df_mask_from_column_values(
    df: pd.DataFrame, row: pd.Series, columns_to_compare: list[str]
) -> pd.Series:
    mask = pd.Series(True, index=df.index)
    for col in columns_to_compare:
        if col in row:
            val = row[col]
            if pd.notnull(val) and str(val).strip() != "":
                mask &= df[col] == val
    return mask


def add_pathogens(df: pd.DataFrame, pathogens_file: str) -> pd.DataFrame:
    """Annotate Kraken2 DataFrame rows with pathogen Priority/Target group/etc."""
    merged = df.copy()
    pathogens = read_pathogens(pathogens_file)
    pathogens = set_column_dtypes(pathogens, {"TaxonomyID": "int64"})
    merged = set_column_dtypes(merged, {"NCBI tax ID": "int64"})
    merged["Priority"] = None
    merged["Target group"] = None
    merged["Priority pathogen group"] = None

    for _, pathogen_row in pathogens.iterrows():
        subset = merged.loc[merged["NCBI tax ID"] == pathogen_row["TaxonomyID"]]
        if len(subset) == 0:
            continue
        match_row = subset.iloc[0]
        columns_to_compare = get_relevant_columns(
            match_row.get("Rank code"), RANK_COLUMN_MAPPING
        )
        mask = create_df_mask_from_column_values(merged, match_row, columns_to_compare)
        merged.loc[mask, "Type"] = pathogen_row["Type"]
        merged.loc[mask, "Priority"] = pathogen_row["Priority"]
        merged.loc[mask, "Target group"] = pathogen_row["Target group"]
        merged.loc[mask, "Priority pathogen group"] = pathogen_row["Priority pathogen group"]
    return merged


# ── Main entry point ──────────────────────────────────────────────────────────


def run_taxprofiler_postprocessing(
    run_accessions: list[str],
    output_path: str,
    enlighten_data_path: str,
    pathogens_file: str,
    db_path: str,
    log_file: Path,
    dataset_label: str = "taxprofiler",
) -> None:
    """
    Post-process a completed Taxprofiler (or SSU) run.

    Searches output_path recursively for Kraken2 report files, enriches them
    with metadata from the SQLite database, annotates pathogen classifications,
    merges with existing Feather files, and writes updated Feather files to
    enlighten_data_path for Enlighten to pick up.

    *dataset_label* is embedded in the Feather file names, e.g.
    ``"Norway_taxprofiler.feather"`` or ``"Norway_ssu.feather"``.

    Appends progress to log_file.  Never raises — errors are logged to log_file
    so the pipeline_run stays in 'done' status even if post-processing fails.
    """

    _log = make_postprocess_logger(log_file, _KRAKEN_LOG_MARKER)

    _log("Starting Kraken2 post-processing...")

    # ── 1. Get metadata from SQLite ───────────────────────────────────────────
    metadata_rows = load_postprocess_metadata(
        db_path, run_accessions, _log, skip_note="Skipping post-processing."
    )
    if not metadata_rows:
        return

    # ── 2. Search for Kraken2 reports and build combined DataFrame ────────────
    combined_df = pd.DataFrame()
    processed_run_accessions: set[str] = set()
    empty_reports: set[str] = set()

    for row in metadata_rows:
        run_accession = row["run_accession"]
        barcode = row["barcode"]
        if not barcode:
            continue

        _log(f"Processing run_accession={run_accession}, barcode={barcode}")
        tmp = create_kraken_dataset(output_path, prefix=barcode)
        if tmp.empty:
            empty_reports.add(f"{run_accession}/{barcode}")
            continue

        tmp = add_metadata(tmp, row)

        try:
            tmp["sampling_date"] = pd.to_datetime(
                tmp["sampling_date"], errors="raise", format="%Y%m%d"
            )
        except Exception as e:
            _log(f"Warning: sampling_date conversion failed for {run_accession}/{barcode}: {e}")

        tmp["incomplete_data"] = False
        processed_run_accessions.add(run_accession)

        frames = [f for f in [combined_df, tmp] if not f.empty and not f.isna().all().all()]
        combined_df = pd.concat(frames, axis=0)

    if empty_reports:
        _log(f"Warning: No valid Kraken2 reports found for: {sorted(empty_reports)}")

    if combined_df.empty:
        _log("No Kraken2 data found. Post-processing complete with no output written.")
        return

    # ── 3. Deduplicate ────────────────────────────────────────────────────────
    combined_df = deduplicate_rows(
        combined_df,
        _log,
        ignore_columns={"run_accession", "incomplete_data", "runName"},
    )

    # ── 4. Add pathogen annotations ───────────────────────────────────────────
    _log(f"Adding pathogen annotations from {pathogens_file} ...")
    combined_df = add_pathogens(combined_df, pathogens_file)

    # ── 5. Type coercions ─────────────────────────────────────────────────────
    combined_df = set_column_dtypes(
        combined_df,
        {
            "Pct. of Frags": "float64",
            "No of Frags root": "int64",
            "No of Frags": "int64",
            "NCBI tax ID": "str",
            "protocol_id": "str",
            "sampling_date": "datetime64[ns]",
            "lon": "float64",
            "lat": "float64",
        },
    )

    # ── 6. Sort ───────────────────────────────────────────────────────────────
    sort_cols = [
        "sampling_site_id",
        "sample_id",
        "sampling_date",
        "Priority pathogen group",
        "Target group",
    ]
    sort_cols_present = [c for c in sort_cols if c in combined_df.columns]
    if sort_cols_present:
        combined_df = combined_df.sort_values(by=sort_cols_present, ascending=True)
    combined_df = combined_df.reset_index(drop=True)

    # ── 7. Split → merge → save ───────────────────────────────────────────────
    subsets = split_dataset(combined_df, split_by="country", dataset_label=dataset_label)
    if not subsets:
        _log("No data to save after splitting by country.")
        return

    _log(f"Merging with existing Feather files in {enlighten_data_path} ...")
    merged_subsets = merge_datasets_with_existing(
        subsets, processed_run_accessions, enlighten_data_path
    )
    save_subsets(merged_subsets, enlighten_data_path, "feather")

    target_groups = create_target_groups(merged_subsets)
    if target_groups:
        save_subsets(target_groups, enlighten_data_path, "feather")

    _log(
        f"Post-processing complete."
        f" Wrote {len(merged_subsets) + len(target_groups)} Feather file(s)"
        f" to {enlighten_data_path}."
    )

    write_postprocess_sentinel(output_path)


def run_ssu_postprocessing(
    run_accessions: list[str],
    output_path: str,
    enlighten_data_path: str,
    pathogens_file: str,
    db_path: str,
    log_file: Path,
) -> None:
    """Post-process a completed wf_metagenomics_ssu run.

    The SSU pipeline (epi2me-labs/wf-metagenomics with SILVA database) produces
    Kraken2-format report files with the same ``barcode*kraken2.report.txt``
    naming convention as Taxprofiler.  This function reuses
    ``run_taxprofiler_postprocessing`` with ``dataset_label="ssu"`` so that
    output Feather files are named ``{country}_ssu.feather`` instead of
    ``{country}_taxprofiler.feather``, keeping SSU and metagenomics data
    separate in the Enlighten data directory.
    """
    run_taxprofiler_postprocessing(
        run_accessions=run_accessions,
        output_path=output_path,
        enlighten_data_path=enlighten_data_path,
        pathogens_file=pathogens_file,
        db_path=db_path,
        log_file=log_file,
        dataset_label="ssu",
    )
