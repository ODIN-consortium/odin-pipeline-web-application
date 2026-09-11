import csv
import sqlite3
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..database import get_db
from ..schemas import BiomemeRunCreate, BiomemeRunRead, BiomemeRunUpdate
from ..settings_resolver import get_device_name
from ..utils import (
    build_update,
    get_csv_delimiter,
    is_yyyymmdd,
    resolve_sample_id,
    resolve_seed_dir,
)
from ..utils import utc_now_str as _now


def seed_biomeme_runs(db: sqlite3.Connection) -> None:
    """Insert missing biomeme_runs from seed/biomeme.csv.

    The CSV may have multiple rows per ``biomeme_run_name`` (one per sample).
    One DB row is created per unique ``biomeme_run_name``, linked to the
    *first* sample found by ``(sample_code, sampling_date)``.

    Existing rows (by biomeme_run_name) are never overwritten.
    """
    seed_dir = resolve_seed_dir()
    if seed_dir is None:
        return
    seed_file = seed_dir / "biomeme.csv"
    if not seed_file.exists():
        return

    now = _now()
    delimiter = get_csv_delimiter()
    seen: set[str] = set()

    with seed_file.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        for row in reader:
            run_name = (row.get("biomeme_run_name") or "").strip()
            if not run_name:
                continue
            if run_name in seen:
                continue  # only first row per run_name is used for sample linkage
            if db.execute(
                "SELECT 1 FROM biomeme_runs WHERE biomeme_run_name = ?", (run_name,)
            ).fetchone():
                seen.add(run_name)
                continue

            sample_code = (row.get("sample_code") or "").strip() or None
            sampling_date = (row.get("sampling_date") or "").strip() or None
            biomeme_sample_id = (row.get("biomeme_sample_id") or "").strip() or None
            dilution_raw = (row.get("dilution_factor") or "").strip()
            try:
                dilution_factor: Optional[float] = float(dilution_raw) if dilution_raw else None
            except ValueError:
                dilution_factor = None

            sample_id: Optional[str] = None
            if sample_code:
                sample_id = resolve_sample_id(
                    db,
                    sample_code,
                    sampling_date if sampling_date and is_yyyymmdd(sampling_date) else None,
                )
                if sample_id is None:
                    print(
                        f"[seed_biomeme_runs] no matching sample for "
                        f"run '{run_name}' / sample_code '{sample_code}' / date '{sampling_date}'"
                    )

            db.execute(
                """INSERT INTO biomeme_runs
                   (id, biomeme_run_name, sample_id, biomeme_sample_id,
                    dilution_factor, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), run_name, sample_id, biomeme_sample_id, dilution_factor, now, now),
            )
            seen.add(run_name)
    db.commit()

router = APIRouter(prefix="/biomeme-runs", tags=["biomeme-runs"])


def _row_to_run(row: sqlite3.Row) -> BiomemeRunRead:
    return BiomemeRunRead(**dict(row))


def _fetch_run(db: sqlite3.Connection, run_id: str) -> Optional[sqlite3.Row]:
    return db.execute(
        """SELECT br.*, s.sample_code, s.sampling_date
           FROM biomeme_runs br
           LEFT JOIN samples s ON s.id = br.sample_id
           WHERE br.id = ?""",
        (run_id,),
    ).fetchone()


@router.get("", response_model=list[BiomemeRunRead])
def list_biomeme_runs(
    sample_id: Optional[str] = Query(default=None),
    db: sqlite3.Connection = Depends(get_db),
):
    if sample_id:
        rows = db.execute(
            """SELECT br.*, s.sample_code, s.sampling_date FROM biomeme_runs br
               LEFT JOIN samples s ON s.id = br.sample_id
               WHERE br.sample_id = ?""",
            (sample_id,),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT br.*, s.sample_code, s.sampling_date FROM biomeme_runs br
               LEFT JOIN samples s ON s.id = br.sample_id"""
        ).fetchall()
    return [_row_to_run(r) for r in rows]


@router.get("/{run_id}", response_model=BiomemeRunRead)
def get_biomeme_run(run_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = _fetch_run(db, run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Biomeme run not found")
    return _row_to_run(row)


@router.post("", response_model=BiomemeRunRead, status_code=201)
def create_biomeme_run(run_in: BiomemeRunCreate, db: sqlite3.Connection = Depends(get_db)):
    if (
        run_in.sample_id
        and not db.execute(
            "SELECT 1 FROM samples WHERE id = ?", (run_in.sample_id,)
        ).fetchone()
    ):
        raise HTTPException(status_code=422, detail="sample_id not found")

    new_id = str(uuid.uuid4())
    now = _now()
    device = get_device_name(db)
    db.execute(
        """INSERT INTO biomeme_runs
           (id, biomeme_run_name, sample_id,
            biomeme_sample_id, dilution_factor, comments,
            created_at, updated_at, created_by, updated_by)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            new_id,
            run_in.biomeme_run_name,
            run_in.sample_id,
            run_in.biomeme_sample_id,
            run_in.dilution_factor,
            run_in.comments,
            now,
            now,
            device,
            device,
        ),
    )
    db.commit()
    row = _fetch_run(db, new_id)
    return _row_to_run(row)


@router.patch("/{run_id}", response_model=BiomemeRunRead)
def update_biomeme_run(
    run_id: str, run_in: BiomemeRunUpdate, db: sqlite3.Connection = Depends(get_db)
):
    base_row = db.execute(
        "SELECT * FROM biomeme_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if not base_row:
        raise HTTPException(status_code=404, detail="Biomeme run not found")

    current = dict(base_row)
    update_data = run_in.model_dump(exclude_unset=True)

    new_sample_id = update_data.get("sample_id", current["sample_id"])
    if (
        "sample_id" in update_data
        and new_sample_id
        and not db.execute(
            "SELECT 1 FROM samples WHERE id = ?", (new_sample_id,)
        ).fetchone()
    ):
        raise HTTPException(status_code=422, detail="sample_id not found")

    now = _now()
    # Only the columns that actually differ; see the note in samples.py::update_sample for
    # why a fixed full-row UPDATE was a sync-correctness problem and not just noise.
    # Note sample_id is nullable by design here — unlinking is a supported edit — so a
    # change to NULL is a real change and must still be written and stamped.
    candidates = {
        "biomeme_run_name": update_data.get("biomeme_run_name", current["biomeme_run_name"]),
        "sample_id": new_sample_id,
        "biomeme_sample_id": update_data.get(
            "biomeme_sample_id", current["biomeme_sample_id"]
        ),
        "dilution_factor": update_data.get("dilution_factor", current["dilution_factor"]),
        "comments": update_data.get("comments", current["comments"]),
    }
    changed = {key: value for key, value in candidates.items() if current[key] != value}
    if changed:
        sql, params = build_update("biomeme_runs", changed, run_id, now, get_device_name(db))
        db.execute(sql, params)
    db.commit()
    row = _fetch_run(db, run_id)
    return _row_to_run(row)


@router.delete("/{run_id}", status_code=204)
def delete_biomeme_run(run_id: str, db: sqlite3.Connection = Depends(get_db)):
    if not db.execute(
        "SELECT 1 FROM biomeme_runs WHERE id = ?", (run_id,)
    ).fetchone():
        raise HTTPException(status_code=404, detail="Biomeme run not found")
    db.execute("DELETE FROM biomeme_runs WHERE id = ?", (run_id,))
    db.commit()
