"""
biomeme_discovery.py — Biomeme file discovery, registration, and pipeline launch.

Endpoints
---------
GET  /biomeme/discover          Scan biomeme_input_data and annotate with DB state.
POST /biomeme/register          Bulk-create biomeme_runs rows from discovered files.
POST /biomeme/launch            Launch the biomeme processing script.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..database import DB_PATH, get_db
from ..pipeline import command_builder as cb
from ..pipeline import executor
from ..utils import resolve_log_dir, utc_now_str
from .settings import lookup_setting

router = APIRouter(prefix="/biomeme", tags=["biomeme"])


# ── Schemas ───────────────────────────────────────────────────────────────────


class DiscoveredBiomemeRun(BaseModel):
    biomeme_run_name: str
    country_code: str
    sampling_date: str          # YYYYMMDD folder name
    file_path: str
    registered: bool
    run_id: Optional[str] = None
    sample_id: Optional[str] = None
    sample_code: Optional[str] = None


class BiomemeRegisterPayload(BaseModel):
    biomeme_run_names: list[str]
    sample_id: Optional[str] = None     # applied to all if no per-entry mapping
    per_run_sample_ids: Optional[dict] = None  # {biomeme_run_name: sample_id}


class BiomemeRegisterResult(BaseModel):
    created: list[str]
    skipped: list[str]


class BiomemeLaunchPayload(BaseModel):
    biomeme_run_ids: Optional[list[str]] = None   # filter by specific biomeme_run DB ids
    created_by: Optional[str] = None


class BiomemeLaunchResult(BaseModel):
    id: str
    status: str
    log_file: Optional[str] = None
    created_at: str


# ── Discover ──────────────────────────────────────────────────────────────────


def _iter_biomeme_files(input_data_path: Path) -> Iterator[tuple[str, str, Path]]:
    """Yield (country_code, sampling_date, file) for every device output on disk.

    The expected layout is ``biomeme_input_data/{country_code}/{YYYYMMDD}/{run}.xlsx``;
    anything that does not fit (stray files, unexpected extensions) is skipped, and
    entries are walked in sorted order so the discovery list is stable.
    """
    for country_entry in sorted(input_data_path.iterdir()):
        if not country_entry.is_dir():
            continue
        for date_entry in sorted(country_entry.iterdir()):
            if not date_entry.is_dir():
                continue
            for file_entry in sorted(date_entry.iterdir()):
                if file_entry.is_file() and file_entry.name.endswith(".xlsx"):
                    yield country_entry.name, date_entry.name, file_entry


def _registered_biomeme_runs(db: sqlite3.Connection) -> dict[str, dict]:
    """Return biomeme_run_name -> the DB state of that run, for annotating the scan."""
    rows = db.execute(
        """SELECT br.id, br.biomeme_run_name, br.sample_id, sa.sample_code
           FROM biomeme_runs br
           LEFT JOIN samples sa ON sa.id = br.sample_id"""
    ).fetchall()
    return {
        r["biomeme_run_name"]: {
            "run_id": r["id"],
            "sample_id": r["sample_id"],
            "sample_code": r["sample_code"],
        }
        for r in rows
    }


@router.get("/discover", response_model=list[DiscoveredBiomemeRun])
def discover_biomeme_runs(db: sqlite3.Connection = Depends(get_db)):
    """Scan biomeme_input_data and return annotated list of device output files."""
    biomeme_dir = lookup_setting(db, "biomeme_dir")
    if not biomeme_dir:
        raise HTTPException(
            status_code=422,
            detail="'Biomeme data root directory' is not configured in Settings.",
        )

    input_data_path = Path(biomeme_dir) / "biomeme_input_data"
    if not input_data_path.is_dir():
        return []

    registered = _registered_biomeme_runs(db)

    discovered: list[DiscoveredBiomemeRun] = []
    try:
        for country_code, sampling_date, file_entry in _iter_biomeme_files(input_data_path):
            reg = registered.get(file_entry.stem)
            discovered.append(
                DiscoveredBiomemeRun(
                    biomeme_run_name=file_entry.stem,
                    country_code=country_code,
                    sampling_date=sampling_date,
                    file_path=str(file_entry),
                    registered=reg is not None,
                    run_id=reg["run_id"] if reg else None,
                    sample_id=reg["sample_id"] if reg else None,
                    sample_code=reg["sample_code"] if reg else None,
                )
            )
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail=f"Cannot read biomeme_input_data: {exc}") from exc

    return discovered


# ── Register ──────────────────────────────────────────────────────────────────


@router.post("/register", response_model=BiomemeRegisterResult, status_code=201)
def register_biomeme_runs(
    payload: BiomemeRegisterPayload,
    db: sqlite3.Connection = Depends(get_db),
):
    """Bulk-create biomeme_runs rows for discovered files. Idempotent (skips existing)."""
    existing = {
        r["biomeme_run_name"]
        for r in db.execute("SELECT biomeme_run_name FROM biomeme_runs").fetchall()
    }

    created: list[str] = []
    skipped: list[str] = []
    now = utc_now_str()

    for run_name in payload.biomeme_run_names:
        if run_name in existing:
            skipped.append(run_name)
            continue

        sample_id = (
            (payload.per_run_sample_ids or {}).get(run_name)
            or payload.sample_id
        )
        if sample_id and not db.execute("SELECT 1 FROM samples WHERE id = ?", (sample_id,)).fetchone():
            raise HTTPException(status_code=422, detail=f"sample_id '{sample_id}' not found")

        db.execute(
            """INSERT INTO biomeme_runs
               (id, biomeme_run_name, sample_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), run_name, sample_id, now, now),
        )
        created.append(run_name)

    db.commit()
    return BiomemeRegisterResult(created=created, skipped=skipped)


# ── Launch ────────────────────────────────────────────────────────────────────


@router.post("/launch", response_model=BiomemeLaunchResult, status_code=201)
def launch_biomeme_pipeline(
    payload: BiomemeLaunchPayload,
    db: sqlite3.Connection = Depends(get_db),
):
    """Launch the biomeme processing script as a background subprocess."""
    # Block if a biomeme run is already queued or running
    active = db.execute(
        "SELECT id FROM pipeline_runs WHERE pipeline_type = 'biomeme' AND status IN ('queued', 'running')"
    ).fetchone()
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"A biomeme run is already queued or running (id: {active['id']}).",
        )

    output_dir_stored = lookup_setting(db, "output_dir")
    if not output_dir_stored:
        raise HTTPException(status_code=422, detail="'Pipeline output directory' is not configured in Settings.")

    # Build command — validates biomeme_dir and enlighten_data_path
    try:
        cmd, extra_env = cb.build_biomeme_cmd(db, str(DB_PATH))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    run_id = str(uuid.uuid4())
    now = utc_now_str()

    logs_dir = resolve_log_dir(output_dir_stored)
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{run_id[:8]}_biomeme.log"

    params: dict = {}
    if payload.biomeme_run_ids:
        params["biomeme_run_ids"] = payload.biomeme_run_ids

    db.execute(
        """INSERT INTO pipeline_runs
           (id, pipeline_type, status, params, log_file, created_at, updated_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            "biomeme",
            "queued",
            json.dumps(params),
            str(log_path),
            now,
            now,
            payload.created_by or "",
        ),
    )
    db.commit()

    executor.launch(
        run_id=run_id,
        cmd=cmd,
        log_path=log_path,
        db_path=str(DB_PATH),
        extra_env=extra_env,
    )

    return BiomemeLaunchResult(
        id=run_id,
        status="queued",
        log_file=str(log_path),
        created_at=now,
    )
