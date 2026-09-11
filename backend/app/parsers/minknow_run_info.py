"""
Pure filesystem parser for MinKNOW run-level metadata files.

Reads two small files from a run directory:
  - final_summary_*.txt       key=value pairs (instrument, kit, run times, …)
  - barcode_alignment_*.tsv   per-barcode read counts

All functions tolerate missing or malformed files gracefully — they return
empty/None values so callers can degrade silently.

Never reads sequencing_summary_*.txt (>50 MB).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

# Matches the standard MinKNOW run directory name:
#   {YYYYMMDD}_{HHMM}_{instrument}_{flow_cell_id}_{protocol_run_id_prefix}
# e.g. 20250912_0843_MN00000_FBD00001_44c4f359
_MINKNOW_DIR_RE = re.compile(
    r"^\d{8}_\d{4}_[A-Z0-9]+_([A-Z]{2,3}\d{4,6})_[0-9a-f]{8,}$"
)


@dataclass
class MinknowRunInfo:
    instrument: str | None = None
    flow_cell_id: str | None = None
    run_name: str | None = None            # protocol_group_id — MinKNOW "experiment" name
    sample_name: str | None = None         # sample_id field in final_summary
    sequencing_kit_raw: str | None = None  # full protocol string from final_summary
    sequencing_kit_extracted: str | None = None  # e.g. "SQK-RBK114-24"
    started: str | None = None             # ISO datetime string
    acquisition_stopped: str | None = None
    barcode_read_counts: dict[str, int] = field(default_factory=dict)


def parse_final_summary(run_dir: Path) -> dict[str, str]:
    """
    Read the first matching final_summary_*.txt in *run_dir*.
    Returns a plain dict of key→value strings.
    Returns an empty dict if no file is found or parsing fails.
    """
    try:
        matches = sorted(run_dir.glob("final_summary_*.txt"))
        if not matches:
            return {}
        result: dict[str, str] = {}
        for line in matches[0].read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if "=" in line:
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip()
        return result
    except OSError:
        return {}


def parse_barcode_alignment(run_dir: Path) -> dict[str, int]:
    """
    Read the first matching barcode_alignment_*.tsv in *run_dir*.
    Returns {barcode_name: read_count} where read_count comes from the
    'target_unclassified' column.
    Returns an empty dict if no file is found, the expected column is absent,
    or parsing fails.
    """
    try:
        matches = sorted(run_dir.glob("barcode_alignment_*.tsv"))
        if not matches:
            return {}
        result: dict[str, int] = {}
        with matches[0].open(encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            if reader.fieldnames is None or "target_unclassified" not in reader.fieldnames:
                return {}
            for row in reader:
                barcode = (row.get("barcode") or "").strip()
                raw_count = (row.get("target_unclassified") or "0").strip()
                if barcode:
                    try:
                        result[barcode] = int(raw_count)
                    except ValueError:
                        result[barcode] = 0
        return result
    except OSError:
        return {}


def extract_kit_from_protocol(protocol: str) -> str | None:
    """
    Extract the kit identifier from a MinKNOW protocol string.

    Expected format:
      sequencing/<name>:<flow_cell>:<kit_code>:<speed>
    The kit is the third colon-separated segment (index 2).

    Returns None if the string is missing or does not match the expected format.
    """
    if not protocol:
        return None
    parts = protocol.split(":")
    if len(parts) >= 3:
        kit = parts[2].strip()
        return kit if kit else None
    return None


def extract_from_run_accession(run_accession: str) -> tuple[str | None, str | None]:
    """
    Extract (flow_cell_id, approx_started) from a MinKNOW run directory name.

    Used as a fallback when final_summary_*.txt is absent.
    Returns (None, None) if the name does not match the expected format.

    Format: {YYYYMMDD}_{HHMM}_{instrument}_{flow_cell_id}_{protocol_run_id}
    Example: 20250912_0843_MN00000_FBD00001_44c4f359
    """
    m = _MINKNOW_DIR_RE.match(run_accession)
    if not m:
        return None, None
    parts = run_accession.split("_")
    flow_cell_id = parts[3]
    date, time_ = parts[0], parts[1]
    approx_started = (
        f"{date[:4]}-{date[4:6]}-{date[6:8]}"
        f"T{time_[:2]}:{time_[2:]}:00"
    )
    return flow_cell_id, approx_started


def get_run_info(run_dir: Path) -> MinknowRunInfo:
    """
    Collect all available MinKNOW metadata from *run_dir*.
    Silently ignores missing or malformed files.

    Directory-structure fallbacks:
      run_name   → run_dir.parent.parent.name  (grandparent = experiment batch folder)
      sample_name → run_dir.parent.name        (parent = sample folder)
    These are used when protocol_group_id / sample_id are absent from final_summary.
    """
    summary = parse_final_summary(run_dir)
    barcode_counts = parse_barcode_alignment(run_dir)

    protocol_raw = summary.get("protocol")
    kit_extracted = extract_kit_from_protocol(protocol_raw) if protocol_raw else None

    # Prefer final_summary fields; fall back to folder names
    run_name = summary.get("protocol_group_id") or None
    sample_name = summary.get("sample_id") or None
    if not run_name:
        run_name = run_dir.parent.parent.name or None
    if not sample_name:
        sample_name = run_dir.parent.name or None

    flow_cell_id = summary.get("flow_cell_id") or None
    started = summary.get("started") or None

    # Folder-name fallback: extract flow_cell_id / approx start when final_summary is absent
    if not flow_cell_id or not started:
        fb_flow_cell, fb_started = extract_from_run_accession(run_dir.name)
        if not flow_cell_id:
            flow_cell_id = fb_flow_cell
        if not started:
            started = fb_started

    return MinknowRunInfo(
        instrument=summary.get("instrument") or None,
        flow_cell_id=flow_cell_id,
        run_name=run_name,
        sample_name=sample_name,
        sequencing_kit_raw=protocol_raw or None,
        sequencing_kit_extracted=kit_extracted,
        started=started,
        acquisition_stopped=summary.get("acquisition_stopped") or None,
        barcode_read_counts=barcode_counts,
    )
