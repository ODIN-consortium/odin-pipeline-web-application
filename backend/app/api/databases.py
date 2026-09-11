"""
CRUD for taxprofiler database entries.

Paths are stored in Git Bash format via normalize_for_storage().
At launch time, command_builder generates the CSV with coerce_path() applied.

Pure WSL paths (/home/...) are stored and returned unchanged — they are
already correct for WSL execution and have no cross-platform equivalent.
"""

from __future__ import annotations

import csv as _csv
import sqlite3
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ..database import get_db
from ..pipeline.command_builder import autodiscover_databases
from ..schemas import DatabaseEntryCreate, DatabaseEntryRead, DatabaseEntryUpdate
from ..settings_resolver import get_device_name
from ..utils import coerce_path, normalize_for_storage
from ..utils import utc_now_str as _now
from .settings import lookup_setting

router = APIRouter(prefix="/databases", tags=["databases"])


def _row_to_read(row: sqlite3.Row) -> DatabaseEntryRead:
    return DatabaseEntryRead(
        id=row["id"],
        tool=row["tool"],
        db_name=row["db_name"],
        db_params=row["db_params"],
        db_path=row["db_path"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        created_by=row["created_by"],
    )


@router.get("", response_model=list[DatabaseEntryRead])
def list_databases(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT * FROM databases ORDER BY tool, db_name"
    ).fetchall()
    return [_row_to_read(r) for r in rows]


@router.get("/effective")
def list_effective_databases(db: sqlite3.Connection = Depends(get_db)):
    """Return the database entries that will actually be used at pipeline launch time.

    When a databases_file is configured and exists, its rows are returned with
    source='file' and the file path. Otherwise the DB table rows are returned
    with source='db'.
    """
    databases_file_path = lookup_setting(db, "databases_file")
    if databases_file_path:
        native = coerce_path(databases_file_path)
        if native and Path(native).exists():
            rows = []
            with open(native, newline="", encoding="utf-8-sig") as fh:
                reader = _csv.DictReader(fh)
                for row in reader:
                    rows.append({
                        "tool":      row.get("tool", "").strip(),
                        "db_name":   row.get("db_name", "").strip(),
                        "db_params": row.get("db_params", "").strip(),
                        "db_path":   row.get("db_path", "").strip(),
                    })
            return {"source": "file", "file": databases_file_path, "entries": rows}

    db_rows = db.execute(
        "SELECT tool, db_name, db_params, db_path FROM databases ORDER BY tool, db_name"
    ).fetchall()
    if db_rows:
        return {
            "source": "db",
            "file": None,
            "entries": [{"tool": r["tool"], "db_name": r["db_name"],
                         "db_params": r["db_params"] or "", "db_path": r["db_path"] or ""}
                        for r in db_rows],
        }

    # Nothing configured — show what launch-time auto-discovery would find, so
    # an empty Databases page does not read as "Taxprofiler cannot run" when a
    # database directory is present under ODIN_DATABASE_PATH.
    auto = autodiscover_databases()
    return {
        "source": "autodiscover" if auto else "db",
        "file": None,
        "entries": auto,
    }


@router.get("/{entry_id}", response_model=DatabaseEntryRead)
def get_database(entry_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute(
        "SELECT * FROM databases WHERE id = ?", (entry_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Database entry not found")
    return _row_to_read(row)


@router.post("", response_model=DatabaseEntryRead, status_code=201)
def create_database(payload: DatabaseEntryCreate, db: sqlite3.Connection = Depends(get_db)):
    # Normalize path on write
    db_path = normalize_for_storage(payload.db_path)
    now = _now()
    entry_id = str(uuid.uuid4())
    device = get_device_name(db)
    try:
        db.execute(
            """INSERT INTO databases (id, tool, db_name, db_params, db_path, created_at,
               updated_at, created_by)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                entry_id,
                payload.tool,
                payload.db_name,
                payload.db_params,
                db_path,
                now,
                now,
                device,
            ),
        )
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"A database entry with tool='{payload.tool}' and db_name='{payload.db_name}' already exists.",
        )
    row = db.execute("SELECT * FROM databases WHERE id = ?", (entry_id,)).fetchone()
    return _row_to_read(row)


@router.put("/{entry_id}", response_model=DatabaseEntryRead)
def update_database(
    entry_id: str,
    payload: DatabaseEntryUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    row = db.execute(
        "SELECT * FROM databases WHERE id = ?", (entry_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Database entry not found")

    update_data = payload.model_dump(exclude_unset=True)

    # tool / db_name / db_path are required in practice — an entry without them is
    # unusable — so a null there means "not provided" and keeps the stored value.
    tool = update_data.get("tool") or row["tool"]
    db_name = update_data.get("db_name") or row["db_name"]
    db_path_in = update_data.get("db_path")
    db_path = normalize_for_storage(db_path_in) if db_path_in else row["db_path"]
    # db_params is optional, so key presence decides: an explicit null clears it.
    db_params = update_data.get("db_params", row["db_params"])

    db.execute(
        """UPDATE databases SET tool=?, db_name=?, db_params=?, db_path=?, updated_at=?
           WHERE id=?""",
        (tool, db_name, db_params, db_path, _now(), entry_id),
    )
    db.commit()
    return _row_to_read(db.execute("SELECT * FROM databases WHERE id = ?", (entry_id,)).fetchone())


@router.delete("/{entry_id}", status_code=204)
def delete_database(entry_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute(
        "SELECT id FROM databases WHERE id = ?", (entry_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Database entry not found")
    db.execute("DELETE FROM databases WHERE id = ?", (entry_id,))
    db.commit()
