import csv
import sqlite3
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..database import get_db
from ..schemas import SampleCreate, SampleRead, SampleUpdate
from ..settings_resolver import get_device_name
from ..utils import (
    build_update,
    get_csv_decimal,
    get_csv_delimiter,
    is_yyyymmdd,
    resolve_seed_dir,
)
from ..utils import utc_now_str as _now
from .lookup_values import require_lookup_code


def _validate_sampling_date(value: str | None) -> None:
    """Raise HTTP 422 if *value* is not a valid YYYYMMDD date string."""
    if not value:
        return
    if not is_yyyymmdd(value):
        raise HTTPException(
            status_code=422,
            detail=f"sampling_date must be in YYYYMMDD format (got: '{value}')",
        )
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"sampling_date '{value}' is not a valid date",
        )


def _parse_decimal(value: str) -> Optional[float]:
    """Parse a decimal that may use the configured decimal separator (e.g. '0,164' → 0.164)."""
    v = value.strip().replace(get_csv_decimal(), ".")
    try:
        return float(v)
    except ValueError:
        return None


def seed_samples(db: sqlite3.Connection) -> None:
    """Insert missing samples from seed CSV. Existing rows (by sample_code + sampling_date)
    are never overwritten. Rows whose sampling_date is not YYYYMMDD are silently skipped
    (this drops the human-readable description row that appears as row 2 in the template).
    site_ID is matched to sites.site_code; rows with no matching site are skipped, as are
    rows with a blank sample_type or a malformed date_extraction — both reported on stdout.

    Every row-level problem here must be a skip rather than a raise: seeding runs inside the
    startup lifespan, which wraps it in try/finally with no except, so an exception stops the
    application from booting instead of degrading one feature."""
    seed_dir = resolve_seed_dir()
    if seed_dir is None:
        return
    seed_file = seed_dir / "samples.csv"
    if not seed_file.exists():
        return
    now = _now()
    # Build a site_code → site_id lookup once
    site_map: dict[str, str] = {
        r["site_code"]: r["id"]
        for r in db.execute(
            "SELECT id, site_code FROM sites"
        ).fetchall()
    }
    with seed_file.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=get_csv_delimiter())
        for row in reader:
            sampling_date = (row.get("sampling_date") or "").strip()
            if not is_yyyymmdd(sampling_date):
                continue  # skip description row and blanks
            sample_code = (row.get("sample_code") or "").strip()
            if not sample_code:
                continue
            if db.execute(
                "SELECT 1 FROM samples WHERE sample_code = ? AND sampling_date = ?",
                (sample_code, sampling_date),
            ).fetchone():
                continue  # already exists — never overwrite
            site_code = (row.get("site_ID") or "").strip()
            site_id = site_map.get(site_code)
            if not site_id:
                print(
                    f"[seed_samples] skipping {sample_code}/{sampling_date}: "
                    f"site_code '{site_code}' not found in sites table"
                )
                continue
            date_extraction = (row.get("date_extraction") or "").strip()
            if date_extraction and not is_yyyymmdd(date_extraction):
                # sampling_date is already format-checked above, but date_extraction was not
                # and now carries a CHECK constraint. Seeding runs inside the startup
                # lifespan, which wraps it in try/finally with no except — so letting the
                # constraint raise here would stop the application from booting over one
                # malformed cell in a CSV. Skip the row and say so; seeding never overwrites,
                # so fixing the file and restarting picks it up cleanly.
                print(
                    f"[seed_samples] skipping {sample_code}/{sampling_date}: "
                    f"date_extraction '{date_extraction}' is not YYYYMMDD"
                )
                continue
            sample_type = (row.get("sample_type") or "").strip()
            if not sample_type:
                # sample_code is derived from site + sample_type, so a blank type would give a
                # malformed code — and sample_code is half of the sync merge key. Skip for the
                # same reason the missing-site case above does.
                print(
                    f"[seed_samples] skipping {sample_code}/{sampling_date}: "
                    "sample_type is blank"
                )
                continue
            db.execute(
                """INSERT INTO samples
                   (id, sample_code, site_id, sample_type, depth, elevation,
                    sampling_date, comments_sampling, partner_sample_code,
                    date_extraction, nucleic_acid_concentration, extract_volume,
                    comments_extraction, elution_volume,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    sample_code,
                    site_id,
                    sample_type,
                    (row.get("depth") or "").strip() or None,
                    (row.get("elevation") or "").strip() or None,
                    sampling_date,
                    (row.get("comments_sampling") or "").strip() or None,
                    (row.get("partner_sample_code") or "").strip() or None,
                    date_extraction or None,
                    _parse_decimal(row.get("nucleic_acid_concentration") or ""),
                    (row.get("extract_volume") or "").strip() or None,
                    (row.get("comments_extraction") or "").strip() or None,
                    (row.get("elution_volume") or "").strip() or None,
                    now,
                    now,
                ),
            )
    db.commit()

router = APIRouter(prefix="/samples", tags=["samples"])


def _row_to_sample(row: sqlite3.Row) -> SampleRead:
    return SampleRead(**dict(row))


@router.get("", response_model=list[SampleRead])
def list_samples(
    site_id: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None, description="Filter by sample_code"),
    db: sqlite3.Connection = Depends(get_db),
):
    if site_id and q:
        rows = db.execute(
            "SELECT * FROM samples WHERE site_id = ? AND sample_code LIKE ?",
            (site_id, f"%{q}%"),
        ).fetchall()
    elif site_id:
        rows = db.execute(
            "SELECT * FROM samples WHERE site_id = ?", (site_id,)
        ).fetchall()
    elif q:
        rows = db.execute(
            "SELECT * FROM samples WHERE sample_code LIKE ?", (f"%{q}%",)
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM samples").fetchall()
    return [_row_to_sample(r) for r in rows]


@router.get("/{sample_id}", response_model=SampleRead)
def get_sample(sample_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute(
        "SELECT * FROM samples WHERE id = ?", (sample_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Sample not found")
    return _row_to_sample(row)


def _derive_sample_code(
    db: sqlite3.Connection, site_id: Optional[str], sample_type: Optional[str]
) -> Optional[str]:
    """Derive `{site_code}_{sample_type}`, or None when it cannot be derived.

    Derived fields are computed on read and never accepted from clients, so this is
    the single definition used by both create and update.
    """
    if not site_id or not sample_type:
        return None
    site_row = db.execute("SELECT site_code FROM sites WHERE id = ?", (site_id,)).fetchone()
    if not site_row:
        return None
    return f"{site_row['site_code']}_{sample_type}"


@router.post("", response_model=SampleRead, status_code=201)
def create_sample(sample_in: SampleCreate, db: sqlite3.Connection = Depends(get_db)):
    if sample_in.site_id and sample_in.sample_type:
        require_lookup_code(db, "sample_type", sample_in.sample_type)
    sample_code = _derive_sample_code(db, sample_in.site_id, sample_in.sample_type)
    if not sample_code:
        raise HTTPException(
            status_code=422,
            detail="sample_code could not be derived — provide site_id + sample_type.",
        )

    _validate_sampling_date(sample_in.sampling_date)

    if db.execute(
        "SELECT 1 FROM samples WHERE sample_code = ? AND sampling_date = ?",
        (sample_code, sample_in.sampling_date),
    ).fetchone():
        raise HTTPException(
            status_code=409,
            detail=f"Sample with sample_code '{sample_code}' and date '{sample_in.sampling_date}' already exists",
        )
    if (
        sample_in.site_id
        and not db.execute(
            "SELECT 1 FROM sites WHERE id = ?", (sample_in.site_id,)
        ).fetchone()
    ):
        raise HTTPException(status_code=422, detail="site_id not found")

    new_id = str(uuid.uuid4())
    now = _now()
    device = get_device_name(db)
    db.execute(
        """INSERT INTO samples
           (id, sample_code, site_id, sample_type, depth, elevation, sampling_date,
            comments_sampling, partner_sample_code, date_extraction,
            nucleic_acid_concentration, extract_volume, comments_extraction,
            elution_volume, comments, created_at, updated_at, created_by, updated_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            new_id,
            sample_code,
            sample_in.site_id,
            sample_in.sample_type,
            sample_in.depth,
            sample_in.elevation,
            sample_in.sampling_date,
            sample_in.comments_sampling,
            sample_in.partner_sample_code,
            sample_in.date_extraction,
            sample_in.nucleic_acid_concentration,
            sample_in.extract_volume,
            sample_in.comments_extraction,
            sample_in.elution_volume,
            sample_in.comments,
            now,
            now,
            device,
            device,
        ),
    )
    db.commit()
    row = db.execute("SELECT * FROM samples WHERE id = ?", (new_id,)).fetchone()
    return _row_to_sample(row)


@router.patch("/{sample_id}", response_model=SampleRead)
def update_sample(
    sample_id: str, sample_in: SampleUpdate, db: sqlite3.Connection = Depends(get_db)
):
    row = db.execute(
        "SELECT * FROM samples WHERE id = ?", (sample_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Sample not found")

    current = dict(row)
    update_data = sample_in.model_dump(exclude_unset=True)

    _validate_sampling_date(update_data.get("sampling_date"))

    # ── sample_code inputs are frozen once the sample has been sequenced ────────
    # Changing site_id or sample_type re-derives this sample's own sample_code, so the row
    # stays self-consistent — unlike a site rename, nothing else goes stale. What changes is
    # the sample's *identity*, and that matters once it has been published: `alias`
    # (sample_code + barcode) is written into the Nextflow samplesheet
    # (pipeline/samplesheet.py), so output directories and reports on disk are named from it.
    # sample_code is also half the sync merge key (UNIQUE (sample_code, sampling_date)).
    #
    # The predicate is therefore narrower than the sites one: a sample with no runs has never
    # reached a samplesheet, so renaming it is still safe and stays allowed.
    changed_identity = [
        field
        for field in ("site_id", "sample_type")
        if field in update_data and update_data[field] != current[field]
    ]
    if changed_identity:
        run_counts = db.execute(
            "SELECT (SELECT COUNT(*) FROM nanopore_runs WHERE sample_id = ?) "
            "     + (SELECT COUNT(*) FROM biomeme_runs  WHERE sample_id = ?)",
            (sample_id, sample_id),
        ).fetchone()[0]
        if run_counts:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot change {', '.join(changed_identity)} on sample "
                    f"'{current['sample_code']}': {run_counts} sequencing run(s) reference "
                    "it, and its sample_code names pipeline output directories on disk. "
                    "Create a new sample instead. Other fields can still be edited, though "
                    "sampling_date has to stay unique for this sample_code."
                ),
            )

    # Always re-derive sample_code when site_id or sample_type changes
    new_sample_code = current["sample_code"]
    if any(k in update_data for k in ("site_id", "sample_type")):
        new_sample_type = update_data.get("sample_type", current["sample_type"])
        if "sample_type" in update_data:
            require_lookup_code(db, "sample_type", new_sample_type)
        derived = _derive_sample_code(
            db, update_data.get("site_id", current["site_id"]), new_sample_type
        )
        # An undeliverable derivation (e.g. site row gone) keeps the stored code.
        new_sample_code = derived or new_sample_code

    if new_sample_code != current["sample_code"] or "sampling_date" in update_data:
        new_sampling_date = update_data.get("sampling_date", current["sampling_date"])
        if db.execute(
            "SELECT 1 FROM samples WHERE sample_code = ? AND sampling_date = ? AND id != ?",
            (new_sample_code, new_sampling_date, sample_id),
        ).fetchone():
            raise HTTPException(
                status_code=409,
                detail=f"Sample with sample_code '{new_sample_code}' and date '{new_sampling_date}' already exists",
            )

    new_site_id = update_data.get("site_id", current["site_id"])
    if (
        "site_id" in update_data
        and new_site_id
        and not db.execute(
            "SELECT 1 FROM sites WHERE id = ?", (new_site_id,)
        ).fetchone()
    ):
        raise HTTPException(status_code=422, detail="site_id not found")

    now = _now()
    # Write only the columns whose value actually differs, and skip the UPDATE entirely
    # when none do. This was a fixed full-row UPDATE built from
    # `update_data.get(key, current[key])`, so every PATCH rewrote every column and always
    # bumped updated_at/updated_by. `updated_at` is deliberately not in sync's
    # _AUDIT_FIELDS, so it drives `incoming_is_newer` — a no-op write makes this device's
    # copy look newer than it is and can make another device's real edit lose the merge.
    # The UI submits the whole form on every save (see core/utils/form-payload.ts), so this
    # was the normal path, not an edge case.
    candidates = {
        "sample_code": new_sample_code,
        "site_id": new_site_id,
        "sample_type": update_data.get("sample_type", current["sample_type"]),
        "depth": update_data.get("depth", current["depth"]),
        "elevation": update_data.get("elevation", current["elevation"]),
        "sampling_date": update_data.get("sampling_date", current["sampling_date"]),
        "comments_sampling": update_data.get("comments_sampling", current["comments_sampling"]),
        "partner_sample_code": update_data.get(
            "partner_sample_code", current["partner_sample_code"]
        ),
        "date_extraction": update_data.get("date_extraction", current["date_extraction"]),
        "nucleic_acid_concentration": update_data.get(
            "nucleic_acid_concentration", current["nucleic_acid_concentration"]
        ),
        "extract_volume": update_data.get("extract_volume", current["extract_volume"]),
        "comments_extraction": update_data.get(
            "comments_extraction", current["comments_extraction"]
        ),
        "elution_volume": update_data.get("elution_volume", current["elution_volume"]),
        "comments": update_data.get("comments", current["comments"]),
    }
    changed = {key: value for key, value in candidates.items() if current[key] != value}
    if changed:
        sql, params = build_update("samples", changed, sample_id, now, get_device_name(db))
        db.execute(sql, params)
    db.commit()
    row = db.execute("SELECT * FROM samples WHERE id = ?", (sample_id,)).fetchone()
    return _row_to_sample(row)


@router.delete("/{sample_id}", status_code=204)
def delete_sample(sample_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute(
        "SELECT 1 FROM samples WHERE id = ?", (sample_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Sample not found")
    in_use = db.execute(
        "SELECT 1 FROM nanopore_runs WHERE sample_id = ? LIMIT 1", (sample_id,)
    ).fetchone() or db.execute(
        "SELECT 1 FROM biomeme_runs WHERE sample_id = ? LIMIT 1", (sample_id,)
    ).fetchone()
    if in_use:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete sample: it is referenced by one or more runs",
        )
    db.execute("DELETE FROM samples WHERE id = ?", (sample_id,))
    db.commit()
