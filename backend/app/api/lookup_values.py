import json
import os
import sqlite3
import uuid
from pathlib import Path

import pycountry
from fastapi import APIRouter, Depends, HTTPException

from ..database import get_db
from ..schemas import LookupValueCreate, LookupValueRead, LookupValueUpdate

router = APIRouter(prefix="/lookup-values", tags=["lookup-values"])

_BUNDLED_SEED_FILE = Path(__file__).parent.parent / "seed" / "lookup_values.json"

# Lists that users are allowed to manage via the UI / API
MANAGED_LISTS = {"sample_type", "protocol_id", "sequencing_kit_id", "mpox_type"}

# Tables+columns that reference lookup values by code (for in-use checks)
_REFERENCES: dict[str, list[tuple[str, str]]] = {
    "sample_type":       [("samples", "sample_type")],
    "protocol_id":       [("nanopore_run_accessions", "protocol_id")],
    "sequencing_kit_id": [("nanopore_run_accessions", "sequencing_kit_id")],
    "mpox_type":         [("nanopore_runs", "type")],
}


def _seed_file() -> Path:
    """Return seed file path from ODIN_LOOKUP_VALUES_FILE env var, or the bundled default."""
    env = os.getenv("ODIN_LOOKUP_VALUES_FILE")
    return Path(env) if env else _BUNDLED_SEED_FILE


def seed_lookup_values(db: sqlite3.Connection) -> None:
    """Insert missing rows from seed JSON. Existing rows (by list+code) are never overwritten."""
    seed_file = _seed_file()
    if not seed_file.exists():
        return
    data: dict = json.loads(seed_file.read_text(encoding="utf-8"))
    for list_name, entries in data.items():
        for entry in entries:
            existing = db.execute(
                "SELECT 1 FROM lookup_values WHERE list = ? AND code = ?",
                (list_name, entry["code"]),
            ).fetchone()
            if not existing:
                db.execute(
                    "INSERT INTO lookup_values (id, list, code, description, external_code)"
                    " VALUES (?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        list_name,
                        entry["code"],
                        entry.get("description"),
                        entry.get("external_code"),
                    ),
                )
    db.commit()


def require_lookup_code(db: sqlite3.Connection, list_name: str, code: str | None) -> None:
    """Raise HTTP 422 if `code` is not None and not present in the given lookup list."""
    if code is None:
        return
    row = db.execute(
        "SELECT 1 FROM lookup_values WHERE list = ? AND code = ?",
        (list_name, code),
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=422,
            detail=f"'{code}' is not a valid code for lookup list '{list_name}'",
        )


@router.get("/countries/iso", summary="All ISO 3166-1 countries from pycountry")
def get_iso_countries():
    return sorted(
        [{"code": c.alpha_2, "description": c.name} for c in pycountry.countries],
        key=lambda x: x["description"],
    )


@router.get("/{list_name}", response_model=list[LookupValueRead])
def get_list(list_name: str, db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT * FROM lookup_values WHERE list = ? ORDER BY code",
        (list_name,),
    ).fetchall()
    return [LookupValueRead(**dict(r)) for r in rows]


@router.post("/seed", status_code=201, summary="Seed lookup values from JSON file")
def trigger_seed(db: sqlite3.Connection = Depends(get_db)):
    seed_lookup_values(db)
    return {"detail": "Lookup values seeded"}


@router.post("/{list_name}", response_model=LookupValueRead, status_code=201)
def create_entry(
    list_name: str,
    body: LookupValueCreate,
    db: sqlite3.Connection = Depends(get_db),
):
    if list_name not in MANAGED_LISTS:
        raise HTTPException(status_code=403, detail=f"List '{list_name}' is not user-manageable")
    if db.execute(
        "SELECT 1 FROM lookup_values WHERE list = ? AND code = ?",
        (list_name, body.code),
    ).fetchone():
        raise HTTPException(status_code=409, detail=f"Code '{body.code}' already exists in '{list_name}'")
    new_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO lookup_values (id, list, code, description, external_code) VALUES (?,?,?,?,?)",
        (new_id, list_name, body.code, body.description, body.external_code),
    )
    db.commit()
    row = db.execute("SELECT * FROM lookup_values WHERE id = ?", (new_id,)).fetchone()
    return LookupValueRead(**dict(row))


@router.put("/{list_name}/{entry_id}", response_model=LookupValueRead)
def update_entry(
    list_name: str,
    entry_id: str,
    body: LookupValueUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    if list_name not in MANAGED_LISTS:
        raise HTTPException(status_code=403, detail=f"List '{list_name}' is not user-manageable")
    row = db.execute(
        "SELECT * FROM lookup_values WHERE id = ? AND list = ?",
        (entry_id, list_name),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        return LookupValueRead(**dict(row))
    fields = ", ".join(f"{k} = ?" for k in update_data)
    db.execute(
        f"UPDATE lookup_values SET {fields} WHERE id = ?",
        (*update_data.values(), entry_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM lookup_values WHERE id = ?", (entry_id,)).fetchone()
    return LookupValueRead(**dict(row))


@router.delete("/{list_name}/{entry_id}", status_code=204)
def delete_entry(
    list_name: str,
    entry_id: str,
    db: sqlite3.Connection = Depends(get_db),
):
    if list_name not in MANAGED_LISTS:
        raise HTTPException(status_code=403, detail=f"List '{list_name}' is not user-manageable")
    row = db.execute(
        "SELECT code FROM lookup_values WHERE id = ? AND list = ?",
        (entry_id, list_name),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    code = row["code"]
    # Refuse to delete a code that is still referenced in any data table
    for table, col in _REFERENCES.get(list_name, []):
        in_use = db.execute(
            f"SELECT 1 FROM {table} WHERE {col} = ? LIMIT 1", (code,)  # noqa: S608
        ).fetchone()
        if in_use:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete '{code}' — it is still referenced in {table}.{col}",
            )
    db.execute("DELETE FROM lookup_values WHERE id = ?", (entry_id,))
    db.commit()
