"""Scaffolding shared by the pipeline post-processors.

Every post-processor (Kraken2/taxprofiler, SSU, AMR) follows the same outline:
log with a marker prefix → load barcode metadata from the ODIN DB → build one
combined DataFrame → deduplicate → merge with the existing Feather files →
write a sentinel. The middle step is pipeline-specific; everything around it is
identical, and lived as copy-pasted blocks in each post-processor before this
module existed.

Post-processing runs after the pipeline already succeeded, so nothing here raises:
problems are reported through the run log and the run stays 'done'.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Callable

import pandas as pd

from ..db.queries import get_metadata_rows_from_db

logger = logging.getLogger(__name__)

# Marker file written into a pipeline's output dir once post-processing finished.
# The dashboard compares its mtime against multiqc_report.html to tell whether
# post-processing is up to date (see api/discovery.py::_check_output_dir).
POSTPROCESS_SENTINEL = ".odin_postprocessed"

LogFn = Callable[[str], None]


def make_postprocess_logger(log_file: Path, marker: str) -> LogFn:
    """Return a log function writing *marker*-prefixed lines to the run log.

    The prefix is what the frontend uses to highlight post-processing output in
    the streamed log. Write failures are ignored — losing a log line must never
    fail post-processing.
    """

    def _log(msg: str) -> None:
        logger.info(msg)
        try:
            with log_file.open("ab") as fh:
                fh.write(f"{marker} {msg}\n".encode())
        except Exception:
            pass

    return _log


def load_postprocess_metadata(
    db_path: str, run_accessions: list[str], log: LogFn, *, skip_note: str
) -> list[sqlite3.Row]:
    """Load the per-barcode metadata rows for *run_accessions*.

    Returns an empty list when there is nothing to process, having logged
    *skip_note* — the caller should then return without writing output.
    """
    con = sqlite3.connect(db_path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        metadata_rows = get_metadata_rows_from_db(con, run_accessions)
    finally:
        con.close()

    if not metadata_rows:
        log(f"No metadata found in database for run_accessions: {run_accessions}. {skip_note}")
        return []

    log(
        f"Found {len(metadata_rows)} barcode row(s) in metadata"
        f" for {len(run_accessions)} run_accession(s)."
    )
    return metadata_rows


def deduplicate_rows(
    df: pd.DataFrame, log: LogFn, *, ignore_columns: set[str]
) -> pd.DataFrame:
    """Drop duplicate rows, ignoring *ignore_columns* when comparing.

    The ignored columns are per-run bookkeeping (which run_accession contributed a
    row, and whether its metadata was complete): two runs reporting the same
    observation are one row, not two.
    """
    dedup_cols = [c for c in df.columns if c not in ignore_columns]
    rows_before = len(df)
    df = df.drop_duplicates(subset=dedup_cols, keep="first")
    rows_removed = rows_before - len(df)
    if rows_removed > 0:
        log(f"Removed {rows_removed} duplicate rows.")
    return df.reset_index(drop=True)


def write_postprocess_sentinel(output_path: str) -> None:
    """Mark this output directory as post-processed (best effort)."""
    try:
        (Path(output_path) / POSTPROCESS_SENTINEL).touch()
    except OSError:
        pass
