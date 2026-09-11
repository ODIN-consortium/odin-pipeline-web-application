"""In-memory TTL cache for MinKNOW disk scan results.

A full scan of the MinKNOW directory tree is expensive when the tree is large.
This module maintains a process-wide singleton that is refreshed lazily — either
when the TTL expires or when the caller explicitly forces a refresh.

Environment variable
--------------------
ODIN_DISK_SCAN_TTL_SECONDS   float, default 300 (5 minutes)
    Age (in seconds) after which a cached result is considered stale.
    Set to 0 to always perform a fresh scan (original behaviour).

Usage
-----
    from .disk_scan_cache import get_scan

    # Auto-refresh when stale (readiness endpoint, link guard, …)
    result = get_scan(minknow_dir)

    # Always force a fresh scan (user-triggered discovery endpoint)
    result = get_scan(minknow_dir, force=True)

    # Access per-run data
    entry = result.entries.get(run_accession)
    if entry and entry.run_info.acquisition_stopped:
        disk_barcodes = {bc.barcode for bc in entry.barcodes}
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .parsers.discovery import BarcodeOnDisk, scan_minknow_dir
from .parsers.minknow_run_info import MinknowRunInfo, get_run_info
from .utils import coerce_path

logger = logging.getLogger(__name__)

_DEFAULT_TTL: float = 300.0


def _get_ttl() -> float:
    raw = os.getenv("ODIN_DISK_SCAN_TTL_SECONDS")
    if raw is None:
        return _DEFAULT_TTL
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid ODIN_DISK_SCAN_TTL_SECONDS=%r — using default %s", raw, _DEFAULT_TTL)
        return _DEFAULT_TTL


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RunScanEntry:
    """Disk data for a single run accession."""
    barcodes: list[BarcodeOnDisk]
    run_info: MinknowRunInfo
    run_path: str  # relative path from minknow_dir root


@dataclass
class MinknowScanResult:
    """Full result of a MinKNOW directory scan."""
    entries: dict[str, RunScanEntry]  # keyed by run_accession
    minknow_dir: str                  # the path this result was scanned from
    scanned_at: float                 # time.monotonic() timestamp


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

_lock: threading.Lock = threading.Lock()
_cache: Optional[MinknowScanResult] = None


def _is_fresh(result: Optional[MinknowScanResult], minknow_dir_str: str) -> bool:
    if result is None:
        return False
    if result.minknow_dir != minknow_dir_str:
        return False
    ttl = _get_ttl()
    if ttl == 0:
        return False
    return (time.monotonic() - result.scanned_at) < ttl


def _do_scan(minknow_dir_str: str) -> MinknowScanResult:
    minknow_root = Path(coerce_path(minknow_dir_str))
    entries: dict[str, RunScanEntry] = {}
    for run in scan_minknow_dir(minknow_root):
        run_dir_path = minknow_root / (run.run_path or run.run_accession)
        ri = get_run_info(run_dir_path)
        entries[run.run_accession] = RunScanEntry(
            barcodes=run.barcodes,
            run_info=ri,
            run_path=run.run_path,
        )
    logger.info(
        "disk scan complete: %d run(s) found under %s", len(entries), minknow_dir_str
    )
    return MinknowScanResult(
        entries=entries,
        minknow_dir=minknow_dir_str,
        scanned_at=time.monotonic(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_scan(minknow_dir: "Path | str", *, force: bool = False) -> MinknowScanResult:
    """Return the cached scan result, refreshing if stale or *force* is True.

    Parameters
    ----------
    minknow_dir:
        Root path of the MinKNOW data directory.
    force:
        If True, always perform a fresh scan regardless of TTL.
        Pass True when the user explicitly triggers a discovery scan so that
        stale cached data is never returned to them.
    """
    global _cache
    minknow_dir_str = str(minknow_dir) if isinstance(minknow_dir, Path) else minknow_dir

    # Fast path — no lock needed for a read when cache is already fresh.
    if not force and _is_fresh(_cache, minknow_dir_str):
        return _cache  # type: ignore[return-value]

    with _lock:
        # Re-check after acquiring the lock; another thread may have just scanned.
        if not force and _is_fresh(_cache, minknow_dir_str):
            return _cache  # type: ignore[return-value]
        result = _do_scan(minknow_dir_str)
        _cache = result

    return result
