import csv
import logging
import re
import sqlite3
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..database import get_db
from ..schemas import (
    NanoporeRunCreate,
    NanoporeRunRead,
    NanoporeRunUpdate,
)
from ..settings_resolver import get_device_name
from ..utils import build_update, get_csv_delimiter, resolve_seed_dir
from ..utils import utc_now_str as _now
from .lookup_values import require_lookup_code

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nanopore-runs", tags=["nanopore-runs"])

_BARCODE_RE = re.compile(r"^barcode\d{2,}$")


# ─────────────────────────────────────────────────────────────────────────────
# Startup seed
# ─────────────────────────────────────────────────────────────────────────────


def seed_nanopore_runs(db: sqlite3.Connection) -> None:
    """Insert missing nanopore runs from config/nanopore.csv.

    Required columns (semicolon-delimited by default):
        run_accession, barcode, sample_code, sampling_date,
        protocol_id, sequencing_kit_id, type, runName, sampleName

    Rows with a run_accession are inserted under a matching (or new)
    nanopore_run_accessions row.

    Rows WITHOUT a run_accession are grouped by (protocol_id,
    sequencing_kit_id, type).  Each unique combination gets one pending
    nanopore_run_accessions row (label "Pending 1", "Pending 2", …) that is
    reused across restarts.  These appear in the dashboard "Link to group"
    picker so the user can attach them to a real run once it arrives.

    Rows whose sample cannot be resolved via sample_code+sampling_date are
    skipped with a warning.
    """
    seed_dir = resolve_seed_dir()
    if seed_dir is None:
        return
    seed_file = seed_dir / "nanopore.csv"
    if not seed_file.exists():
        return

    now = _now()
    delimiter = get_csv_delimiter()

    # Pre-build lookup: (sample_code, sampling_date) -> sample id
    sample_map: dict[tuple[str, str], str] = {
        (r["sample_code"], r["sampling_date"]): r["id"]
        for r in db.execute("SELECT id, sample_code, sampling_date FROM samples").fetchall()
    }

    pending_rows: list[dict] = []  # rows with no run_accession

    with seed_file.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        for row in reader:
            run_accession = (row.get("run_accession") or "").strip()
            barcode = (row.get("barcode") or "").strip()
            if not barcode:
                continue

            if not run_accession:
                pending_rows.append(dict(row))
                continue

            # ── Upsert nanopore_run_accessions (insert-if-missing) ────────────
            nra_row = db.execute(
                "SELECT id FROM nanopore_run_accessions WHERE run_accession = ?",
                (run_accession,),
            ).fetchone()
            if nra_row:
                accession_id = nra_row["id"]
            else:
                accession_id = str(uuid.uuid4())
                db.execute(
                    """INSERT INTO nanopore_run_accessions
                       (id, run_accession, protocol_id, sequencing_kit_id,
                        runName, sampleName, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        accession_id,
                        run_accession,
                        (row.get("protocol_id") or "").strip() or None,
                        (row.get("sequencing_kit_id") or "").strip() or None,
                        (row.get("runName") or "").strip() or None,
                        (row.get("sampleName") or "").strip() or None,
                        now,
                        now,
                    ),
                )

            # ── Skip if (accession_id, barcode) already registered ────────────
            if db.execute(
                "SELECT 1 FROM nanopore_runs WHERE accession_id = ? AND barcode = ?",
                (accession_id, barcode),
            ).fetchone():
                continue

            # ── Resolve sample_id from sample_code + sampling_date ────────────
            sample_code = (row.get("sample_code") or "").strip()
            sampling_date = (row.get("sampling_date") or "").strip()
            sample_id = sample_map.get((sample_code, sampling_date))
            if not sample_id:
                print(
                    f"[seed_nanopore_runs] skipping {run_accession}/{barcode}: "
                    f"sample '{sample_code}' / '{sampling_date}' not found in samples table"
                )
                continue

            db.execute(
                """INSERT INTO nanopore_runs
                   (id, accession_id, sample_id, barcode, type, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), accession_id, sample_id, barcode,
                 (row.get("type") or "").strip() or None, now, now),
            )

    # ── Handle pending rows (no run_accession) ────────────────────────────────
    # Group by (protocol_id, sequencing_kit_id, type) — same combination → same
    # pending group.
    pending_by_key: dict[tuple[str, str], list[dict]] = {}
    for row in pending_rows:
        key = (
            (row.get("protocol_id") or "").strip(),
            (row.get("sequencing_kit_id") or "").strip(),
        )
        pending_by_key.setdefault(key, []).append(row)

    for (protocol_id, sequencing_kit_id), rows in pending_by_key.items():
        # Find existing pending NRA with matching kit/protocol
        existing = db.execute(
            """SELECT id FROM nanopore_run_accessions
               WHERE run_accession IS NULL
                 AND (protocol_id IS ? OR protocol_id = ?)
                 AND (sequencing_kit_id IS ? OR sequencing_kit_id = ?)""",
            (
                protocol_id or None, protocol_id or None,
                sequencing_kit_id or None, sequencing_kit_id or None,
            ),
        ).fetchone()

        if existing:
            accession_id = existing["id"]
        else:
            # Determine next "Pending N" label
            row_max = db.execute(
                "SELECT MAX(CAST(SUBSTR(label, 9) AS INTEGER)) "
                "FROM nanopore_run_accessions WHERE label LIKE 'Pending %'"
            ).fetchone()[0]
            next_n = (row_max or 0) + 1
            label = f"Pending {next_n}"
            accession_id = str(uuid.uuid4())
            db.execute(
                """INSERT INTO nanopore_run_accessions
                   (id, run_accession, label, protocol_id, sequencing_kit_id,
                    created_at, updated_at)
                   VALUES (?,NULL,?,?,?,?,?)""",
                (
                    accession_id,
                    label,
                    protocol_id or None,
                    sequencing_kit_id or None,
                    now,
                    now,
                ),
            )
            print(f"[seed_nanopore_runs] created pending group '{label}' "
                  f"(protocol={protocol_id}, kit={sequencing_kit_id})")

        for row in rows:
            barcode = (row.get("barcode") or "").strip()
            if db.execute(
                "SELECT 1 FROM nanopore_runs WHERE accession_id = ? AND barcode = ?",
                (accession_id, barcode),
            ).fetchone():
                continue

            sample_code = (row.get("sample_code") or "").strip()
            sampling_date = (row.get("sampling_date") or "").strip()
            sample_id = sample_map.get((sample_code, sampling_date))
            if not sample_id:
                print(
                    f"[seed_nanopore_runs] skipping pending/{barcode}: "
                    f"sample '{sample_code}' / '{sampling_date}' not found in samples table"
                )
                continue

            db.execute(
                """INSERT INTO nanopore_runs
                   (id, accession_id, sample_id, barcode, type, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), accession_id, sample_id, barcode,
                 (row.get("type") or "").strip() or None, now, now),
            )

    db.commit()



_SELECT_COLS = (
    "nr.id, nr.accession_id, nr.sample_id, nr.barcode, "
    "nr.created_at, nr.updated_at, nr.created_by, nr.updated_by, "
    "nra.run_accession, nra.label, "
    "nra.protocol_id, nra.sequencing_kit_id, nr.type, nra.runName, nra.sampleName, nra.comments, "
    "s.sample_code, s.sampling_date, "
    "CASE WHEN s.sample_code IS NOT NULL"
    "          AND nra.protocol_id IS NOT NULL"
    "          AND nra.sequencing_kit_id IS NOT NULL"
    "     THEN s.sample_code || '_' || nra.protocol_id || '_' || nra.sequencing_kit_id"
    "     ELSE NULL END AS minknow_sample_id, "
    "CASE WHEN s.sample_code IS NOT NULL"
    "     THEN s.sample_code || '_' || nr.barcode"
    "     ELSE NULL END AS alias"
)

_FROM_JOINS = (
    "FROM nanopore_runs nr "
    "JOIN nanopore_run_accessions nra ON nra.id = nr.accession_id "
    "LEFT JOIN samples s ON s.id = nr.sample_id"
)


def _row_to_run(row: sqlite3.Row) -> NanoporeRunRead:
    return NanoporeRunRead(**dict(row))


def _fetch_run(db: sqlite3.Connection, run_id: str) -> Optional[sqlite3.Row]:
    return db.execute(
        f"SELECT {_SELECT_COLS} {_FROM_JOINS} WHERE nr.id = ?",  # noqa: S608
        (run_id,),
    ).fetchone()


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get("", response_model=list[NanoporeRunRead])
def list_nanopore_runs(
    run_accession: Optional[str] = Query(default=None),
    sample_id: Optional[str] = Query(default=None),
    db: sqlite3.Connection = Depends(get_db),
):
    where = ["1=1"]
    params: list = []
    if run_accession:
        where.append("nra.run_accession = ?")
        params.append(run_accession)
    if sample_id:
        where.append("nr.sample_id = ?")
        params.append(sample_id)
    sql = f"SELECT {_SELECT_COLS} {_FROM_JOINS} WHERE {' AND '.join(where)}"  # noqa: S608
    rows = db.execute(sql, params).fetchall()
    return [_row_to_run(r) for r in rows]


@router.get("/{run_id}", response_model=NanoporeRunRead)
def get_nanopore_run(run_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = _fetch_run(db, run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Nanopore run not found")
    return _row_to_run(row)


@router.post("", response_model=NanoporeRunRead, status_code=201)
def create_nanopore_run(run_in: NanoporeRunCreate, db: sqlite3.Connection = Depends(get_db)):
    # Validate sample exists when supplied.
    if run_in.sample_id and not db.execute(
        "SELECT 1 FROM samples WHERE id = ?", (run_in.sample_id,)
    ).fetchone():
        raise HTTPException(status_code=422, detail="sample_id not found")

    require_lookup_code(db, "protocol_id", run_in.protocol_id)
    require_lookup_code(db, "sequencing_kit_id", run_in.sequencing_kit_id)
    require_lookup_code(db, "mpox_type", run_in.type)

    if run_in.barcode and not _BARCODE_RE.match(run_in.barcode):
        logger.warning(
            "Barcode '%s' does not match expected Nanopore format 'barcodeNN' "
            "(e.g. barcode01). This may cause the pipeline to skip this barcode.",
            run_in.barcode,
        )

    now = _now()
    device = get_device_name(db)

    # ── Resolve accession_id ────────────────────────────────────────────────
    if run_in.accession_id:
        # Caller passes an existing UUID directly.
        nra_row = db.execute(
            "SELECT id FROM nanopore_run_accessions WHERE id = ?",
            (run_in.accession_id,),
        ).fetchone()
        if not nra_row:
            raise HTTPException(status_code=422, detail="accession_id not found")
        accession_id = run_in.accession_id

    elif run_in.run_accession:
        # Upsert by run_accession string (backward-compatible path).
        tmp_id = str(uuid.uuid4())
        db.execute(
            """INSERT INTO nanopore_run_accessions
               (id, run_accession, protocol_id, sequencing_kit_id,
                runName, sampleName, comments, created_at, updated_at, created_by, updated_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(run_accession) DO UPDATE SET
                   protocol_id = COALESCE(excluded.protocol_id, nanopore_run_accessions.protocol_id),
                   sequencing_kit_id = COALESCE(excluded.sequencing_kit_id, nanopore_run_accessions.sequencing_kit_id),
                   runName = COALESCE(excluded.runName, nanopore_run_accessions.runName),
                   sampleName = COALESCE(excluded.sampleName, nanopore_run_accessions.sampleName),
                   comments = COALESCE(excluded.comments, nanopore_run_accessions.comments),
                   updated_at = excluded.updated_at,
                   updated_by = excluded.updated_by""",
            (
                tmp_id,
                run_in.run_accession,
                run_in.protocol_id,
                run_in.sequencing_kit_id,
                run_in.runName,
                run_in.sampleName,
                run_in.comments,
                now, now,
                device,
                device,
            ),
        )
        nra_row = db.execute(
            "SELECT id FROM nanopore_run_accessions WHERE run_accession = ?",
            (run_in.run_accession,),
        ).fetchone()
        accession_id = nra_row["id"]

    elif run_in.label:
        # Create a pending accession with a label, or 409 if duplicate.
        accession_id = str(uuid.uuid4())
        try:
            db.execute(
                """INSERT INTO nanopore_run_accessions
                   (id, label, protocol_id, sequencing_kit_id,
                    runName, sampleName, comments, created_at, updated_at, created_by, updated_by)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    accession_id,
                    run_in.label,
                    run_in.protocol_id,
                    run_in.sequencing_kit_id,
                    run_in.runName,
                    run_in.sampleName,
                    run_in.comments,
                    now, now,
                    device,
                    device,
                ),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409, detail=f"label '{run_in.label}' already exists"
            )

    else:
        raise HTTPException(
            status_code=422,
            detail="One of accession_id, run_accession, or label must be provided.",
        )

    # ── Uniqueness check on (accession_id, barcode) ────────────────────────
    if db.execute(
        "SELECT 1 FROM nanopore_runs WHERE accession_id = ? AND barcode = ?",
        (accession_id, run_in.barcode),
    ).fetchone():
        raise HTTPException(
            status_code=409, detail="(accession_id, barcode) pair already exists"
        )

    # ── Insert barcode row ─────────────────────────────────────────────────
    new_id = str(uuid.uuid4())
    db.execute(
        """INSERT INTO nanopore_runs
           (id, accession_id, sample_id, barcode, type,
            created_at, updated_at, created_by, updated_by)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            new_id,
            accession_id,
            run_in.sample_id,
            run_in.barcode,
            run_in.type,
            now, now,
            device,
            device,
        ),
    )
    db.commit()
    return _row_to_run(_fetch_run(db, new_id))


@router.patch("/{run_id}", response_model=NanoporeRunRead)
def update_nanopore_run(
    run_id: str, run_in: NanoporeRunUpdate, db: sqlite3.Connection = Depends(get_db)
):
    base_row = db.execute(
        "SELECT * FROM nanopore_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if not base_row:
        raise HTTPException(status_code=404, detail="Nanopore run not found")

    current = dict(base_row)
    update_data = run_in.model_dump(exclude_unset=True)

    now = _now()

    # ── Resolve new accession_id ───────────────────────────────────────────
    old_accession_id = current["accession_id"]
    new_accession_id = update_data.get("accession_id", old_accession_id)
    if "accession_id" in update_data and new_accession_id != old_accession_id:
        if not db.execute(
            "SELECT 1 FROM nanopore_run_accessions WHERE id = ?",
            (new_accession_id,),
        ).fetchone():
            raise HTTPException(status_code=422, detail="accession_id not found")

    # ── Uniqueness check on (accession_id, barcode) ────────────────────────
    new_barcode = update_data.get("barcode", current["barcode"])
    if new_accession_id != old_accession_id or new_barcode != current["barcode"]:
        if db.execute(
            "SELECT 1 FROM nanopore_runs WHERE accession_id = ? AND barcode = ? AND id != ?",
            (new_accession_id, new_barcode, run_id),
        ).fetchone():
            raise HTTPException(
                status_code=409, detail="(accession_id, barcode) pair already exists"
            )

    # ── Validate new sample_id ─────────────────────────────────────────────
    new_sample_id = update_data.get("sample_id", current["sample_id"])
    if (
        "sample_id" in update_data
        and new_sample_id
        and not db.execute(
            "SELECT 1 FROM samples WHERE id = ?", (new_sample_id,)
        ).fetchone()
    ):
        raise HTTPException(status_code=422, detail="sample_id not found")

    # ── Update run-level fields on nanopore_run_accessions ─────────────────
    run_level_fields = ("protocol_id", "sequencing_kit_id", "runName", "sampleName", "comments")
    run_level_updates = {k: update_data[k] for k in run_level_fields if k in update_data}
    if run_level_updates:
        require_lookup_code(db, "protocol_id", run_level_updates.get("protocol_id"))
        require_lookup_code(db, "sequencing_kit_id", run_level_updates.get("sequencing_kit_id"))
        # Write only the fields whose value actually differs. A field submitted with the
        # value already stored is not an edit, and stamping updated_at for it makes this
        # device's copy look newer than it is — `incoming_is_newer` in api/sync.py
        # compares updated_at (which, unlike created_by/updated_by, is deliberately not
        # in _AUDIT_FIELDS), and the default decision for an updated row is "accept only
        # if incoming is newer". A no-op write can therefore make another device's real
        # edit lose the merge.
        accession_row = db.execute(
            "SELECT * FROM nanopore_run_accessions WHERE id = ?", (new_accession_id,)
        ).fetchone()
        changed_run_level = {
            key: value
            for key, value in run_level_updates.items()
            if accession_row is None or accession_row[key] != value
        }
        if changed_run_level:
            sql, params = build_update(
                "nanopore_run_accessions",
                changed_run_level,
                new_accession_id,
                now,
                get_device_name(db),
            )
            db.execute(sql, params)

    # ── Update barcode-level row ───────────────────────────────────────────
    # These three are always resolved above — from update_data when supplied, otherwise
    # from the current row — so before filtering, this UPDATE ran on *every* PATCH,
    # including one that only touched a run-level comment. It rewrote three identical
    # values and bumped updated_at/updated_by on a row nothing had changed. Same
    # consequence for sync as described for the run-level update above.
    barcode_candidates: dict = {
        "accession_id": new_accession_id,
        "sample_id": new_sample_id,
        "barcode": new_barcode,
    }
    if "type" in update_data:
        require_lookup_code(db, "mpox_type", update_data["type"])
        barcode_candidates["type"] = update_data["type"]
    barcode_level = {
        key: value for key, value in barcode_candidates.items() if current[key] != value
    }
    if barcode_level:
        sql, params = build_update(
            "nanopore_runs", barcode_level, run_id, now, get_device_name(db)
        )
        db.execute(sql, params)

    db.commit()
    return _row_to_run(_fetch_run(db, run_id))


@router.delete("/{run_id}", status_code=204)
def delete_nanopore_run(run_id: str, db: sqlite3.Connection = Depends(get_db)):
    if not db.execute(
        "SELECT 1 FROM nanopore_runs WHERE id = ?", (run_id,)
    ).fetchone():
        raise HTTPException(status_code=404, detail="Nanopore run not found")
    db.execute("DELETE FROM nanopore_runs WHERE id = ?", (run_id,))
    db.commit()
