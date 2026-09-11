"""CRUD + link endpoints for nanopore_run_accessions.

Routes
------
GET    /nanopore-run-accessions            – list (optional ?pending=true filter)
GET    /nanopore-run-accessions/{id}       – single accession
POST   /nanopore-run-accessions            – create (pending or with run_accession)
PATCH  /nanopore-run-accessions/{id}       – update label / kit / protocol …
POST   /nanopore-run-accessions/{id}/link  – set run_accession on a pending accession
DELETE /nanopore-run-accessions/{id}       – soft-delete
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..database import get_db
from ..disk_scan_cache import get_scan as get_disk_scan
from ..parsers.discovery import find_run_dir
from ..schemas import (
    NanoporeRunAccessionCreate,
    NanoporeRunAccessionRead,
    NanoporeRunAccessionUpdate,
)
from ..settings_resolver import get_device_name
from ..utils import build_update, coerce_path
from ..utils import utc_now_str as _now
from .lookup_values import require_lookup_code
from .settings import lookup_setting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nanopore-run-accessions", tags=["nanopore-run-accessions"])


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _fetch_nra(db, nra_id: str) -> dict:
    row = db.execute(
        "SELECT * FROM nanopore_run_accessions WHERE id = ?",
        (nra_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Accession not found")
    return dict(row)


# ─────────────────────────────────────────────────────────────────────────────
# GET /nanopore-run-accessions
# ─────────────────────────────────────────────────────────────────────────────


@router.get("", response_model=list[NanoporeRunAccessionRead])
def list_nanopore_run_accessions(
    pending: Optional[bool] = Query(None),
    db=Depends(get_db),
):
    """Return all accessions.  Pass ``?pending=true`` to get only
    rows where *run_accession IS NULL* (pre-run plans not yet linked to a run).
    """
    sql = "SELECT * FROM nanopore_run_accessions"
    clauses: list[str] = []
    if pending is True:
        clauses.append("run_accession IS NULL")
    elif pending is False:
        clauses.append("run_accession IS NOT NULL")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC"
    rows = db.execute(sql).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# GET /nanopore-run-accessions/{id}
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/{nra_id}", response_model=NanoporeRunAccessionRead)
def get_nanopore_run_accession(nra_id: str, db=Depends(get_db)):
    return _fetch_nra(db, nra_id)


# ─────────────────────────────────────────────────────────────────────────────
# POST /nanopore-run-accessions
# ─────────────────────────────────────────────────────────────────────────────


@router.post("", response_model=NanoporeRunAccessionRead, status_code=201)
def create_nanopore_run_accession(
    body: NanoporeRunAccessionCreate, db=Depends(get_db)
):
    if body.run_accession is None and body.label is None:
        raise HTTPException(
            status_code=422,
            detail="Either run_accession or label must be provided.",
        )

    require_lookup_code(db, "protocol_id", body.protocol_id)
    require_lookup_code(db, "sequencing_kit_id", body.sequencing_kit_id)

    now = _now()
    nra_id = str(uuid.uuid4())
    device = get_device_name(db)
    try:
        db.execute(
            """
            INSERT INTO nanopore_run_accessions
                (id, run_accession, label, protocol_id, sequencing_kit_id,
                 runName, sampleName, comments, created_at, updated_at, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                nra_id,
                body.run_accession,
                body.label,
                body.protocol_id,
                body.sequencing_kit_id,
                body.runName,
                body.sampleName,
                body.comments,
                now,
                now,
                device,
            ),
        )
    except sqlite3.IntegrityError:
        key = (
            f"run_accession '{body.run_accession}'" if body.run_accession
            else f"label '{body.label}'"
        )
        raise HTTPException(status_code=409, detail=f"{key} already exists.")
    db.commit()
    return _fetch_nra(db, nra_id)


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /nanopore-run-accessions/{id}
# ─────────────────────────────────────────────────────────────────────────────


@router.patch("/{nra_id}", response_model=NanoporeRunAccessionRead)
def update_nanopore_run_accession(
    nra_id: str, body: NanoporeRunAccessionUpdate, db=Depends(get_db)
):
    current = _fetch_nra(db, nra_id)  # 404 guard, and the baseline for change detection
    now = _now()

    fields: dict = body.model_dump(exclude_unset=True)
    if not fields:
        return _fetch_nra(db, nra_id)

    # Uniqueness check when updating run_accession or label.
    if "run_accession" in fields and fields["run_accession"] is not None:
        clash = db.execute(
            "SELECT id FROM nanopore_run_accessions WHERE run_accession = ? AND id != ?",
            (fields["run_accession"], nra_id),
        ).fetchone()
        if clash:
            raise HTTPException(
                status_code=409,
                detail=f"run_accession '{fields['run_accession']}' already exists.",
            )
    if "label" in fields and fields["label"] is not None:
        clash = db.execute(
            "SELECT id FROM nanopore_run_accessions WHERE label = ? AND id != ?",
            (fields["label"], nra_id),
        ).fetchone()
        if clash:
            raise HTTPException(
                status_code=409,
                detail=f"label '{fields['label']}' already exists.",
            )

    if "protocol_id" in fields:
        require_lookup_code(db, "protocol_id", fields.get("protocol_id"))
    if "sequencing_kit_id" in fields:
        require_lookup_code(db, "sequencing_kit_id", fields.get("sequencing_kit_id"))

    # Only the fields whose value actually differs. This wrote every submitted field, so
    # resubmitting one unchanged still bumped updated_at — and updated_at drives sync's
    # `incoming_is_newer`, so a no-op write can make another device's real edit lose the
    # merge. See samples.py::update_sample for the full reasoning.
    changed = {key: value for key, value in fields.items() if current[key] != value}
    if changed:
        sql, params = build_update(
            "nanopore_run_accessions", changed, nra_id, now, get_device_name(db)
        )
        db.execute(sql, params)
    db.commit()
    return _fetch_nra(db, nra_id)


# ─────────────────────────────────────────────────────────────────────────────
# Disk auto-populate helper
# ─────────────────────────────────────────────────────────────────────────────


def _auto_from_disk(db, nra_id: str, run_accession: str, nra: dict, now: str) -> None:
    """Best-effort: fill runName / sampleName from the on-disk path when they
    are not already set.  Silently logs and returns on any failure."""
    try:
        minknow_dir_str = lookup_setting(db, "minknow_dir")
        if not minknow_dir_str:
            return

        _minknow_root = Path(coerce_path(minknow_dir_str))
        run_dir = find_run_dir(_minknow_root, run_accession)
        if not run_dir:
            logger.info("link auto-populate: run dir not found for %s", run_accession)
            return

        parts = run_dir.relative_to(_minknow_root).parts
        # MinKNOW layout: …/{experiment}/{sample}/{run_accession}
        _run_name: Optional[str] = parts[-3] if len(parts) >= 3 else (parts[-2] if len(parts) >= 2 else None)
        _sample_name: Optional[str] = parts[-2] if len(parts) >= 2 else None

        auto_fields: dict = {}
        if not nra.get("runName") and _run_name:
            auto_fields["runName"] = _run_name
        if not nra.get("sampleName") and _sample_name:
            auto_fields["sampleName"] = _sample_name

        if auto_fields:
            _set = ", ".join(f"{k} = ?" for k in auto_fields)
            db.execute(
                f"UPDATE nanopore_run_accessions SET {_set}, updated_at = ? WHERE id = ?",  # noqa: S608
                [*auto_fields.values(), now, nra_id],
            )
            db.commit()
            logger.info(
                "link auto-populate: set %s for %s (run_accession=%s)",
                list(auto_fields.keys()), nra_id, run_accession,
            )
    except Exception:
        logger.exception("link auto-populate failed for run_accession=%s", run_accession)


# ─────────────────────────────────────────────────────────────────────────────
# POST /nanopore-run-accessions/{id}/link
# ─────────────────────────────────────────────────────────────────────────────


class LinkRunRequest(BaseModel):
    run_accession: str
    barcode_ids: Optional[list[str]] = None  # nanopore_runs.id list; None = link all


def _reject_duplicate_run_accession(db, run_accession: str) -> None:
    """A run_accession identifies one sequencer run — it cannot be linked twice."""
    if db.execute(
        "SELECT id FROM nanopore_run_accessions WHERE run_accession = ?",
        (run_accession,),
    ).fetchone():
        raise HTTPException(
            status_code=409,
            detail=f"run_accession '{run_accession}' already exists.",
        )


def _reject_barcode_mismatch(db, nra_id: str, run_accession: str) -> None:
    """Guard against linking a pending plan to the wrong finished run.

    When the run on disk is finished (acquisition_stopped is set) and the pending
    accession has registered barcodes, at least one of them must exist in the run's
    fastq_pass/ directory. Zero overlap means the wrong pending group was selected.
    Runs still in progress are not checked — their barcodes are still appearing.
    """
    pending_barcodes = {
        r["barcode"]
        for r in db.execute(
            "SELECT barcode FROM nanopore_runs WHERE accession_id = ?", (nra_id,)
        ).fetchall()
    }
    if not pending_barcodes:
        return
    minknow_dir_str = lookup_setting(db, "minknow_dir")
    if not minknow_dir_str:
        return

    scan = get_disk_scan(minknow_dir_str)  # TTL-based; never forced on link
    entry = scan.entries.get(run_accession)
    if not entry or not entry.run_info.acquisition_stopped:
        return

    disk_barcodes = {bc.barcode for bc in entry.barcodes}
    if disk_barcodes and pending_barcodes.isdisjoint(disk_barcodes):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Cannot link: the run is completed and there is no overlap between "
                f"registered barcodes ({sorted(pending_barcodes)}) and barcodes found "
                f"on disk ({sorted(disk_barcodes)}). You may have selected the wrong "
                f"pending group."
            ),
        )


def _split_off_unselected_barcodes(
    db, nra_id: str, nra: dict, body: "LinkRunRequest", now: str
) -> None:
    """Move barcodes the user did not select into a fresh pending accession.

    They were planned together but did not end up on this run, so they must stay
    registered and unlinked rather than being dragged onto the linked accession.
    """
    all_run_ids = [
        r["id"]
        for r in db.execute(
            "SELECT id FROM nanopore_runs WHERE accession_id = ?", (nra_id,)
        ).fetchall()
    ]
    excluded_ids = [rid for rid in all_run_ids if rid not in (body.barcode_ids or [])]
    if not excluded_ids:
        return

    new_nra_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO nanopore_run_accessions
            (id, label, protocol_id, sequencing_kit_id,
             runName, sampleName, comments, created_at, updated_at, created_by)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            new_nra_id,
            f"{nra.get('label') or body.run_accession}__split",
            nra.get("protocol_id"),
            nra.get("sequencing_kit_id"),
            nra.get("runName"),
            nra.get("sampleName"),
            nra.get("comments"),
            now,
            now,
            get_device_name(db),
        ),
    )
    for rid in excluded_ids:
        db.execute(
            "UPDATE nanopore_runs SET accession_id = ?, updated_at = ? WHERE id = ?",
            (new_nra_id, now, rid),
        )


@router.post("/{nra_id}/link", response_model=NanoporeRunAccessionRead)
def link_run_accession(
    nra_id: str, body: LinkRunRequest, db=Depends(get_db)
):
    """Attach a real sequencer run_accession string to a pending plan and
    optionally restrict which barcodes belong to this run (others become
    unlinked / re-assigned to a new pending accession).
    """
    nra = _fetch_nra(db, nra_id)

    if nra.get("run_accession") is not None:
        raise HTTPException(
            status_code=409,
            detail="This accession already has a run_accession set.",
        )

    now = _now()

    _reject_duplicate_run_accession(db, body.run_accession)
    _reject_barcode_mismatch(db, nra_id, body.run_accession)

    if body.barcode_ids is not None:
        _split_off_unselected_barcodes(db, nra_id, nra, body, now)

    # Set run_accession on this accession.
    db.execute(
        "UPDATE nanopore_run_accessions SET run_accession = ?, updated_at = ? WHERE id = ?",
        (body.run_accession, now, nra_id),
    )
    db.commit()

    # Auto-populate runName / sampleName from disk path if not already set.
    if not nra.get("runName") or not nra.get("sampleName"):
        _auto_from_disk(db, nra_id, body.run_accession, nra, now)

    return _fetch_nra(db, nra_id)


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /nanopore-run-accessions/{id}
# ─────────────────────────────────────────────────────────────────────────────


@router.delete("/{nra_id}", status_code=204)
def delete_nanopore_run_accession(nra_id: str, db=Depends(get_db)):
    _fetch_nra(db, nra_id)  # 404 guard
    db.execute("DELETE FROM nanopore_run_accessions WHERE id = ?", (nra_id,))
    db.commit()
