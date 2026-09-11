import csv
import sqlite3
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..database import get_db
from ..schemas import SiteCreate, SiteRead, SiteUpdate
from ..settings_resolver import get_device_name
from ..utils import build_update, get_csv_delimiter, resolve_seed_dir
from ..utils import utc_now_str as _now

router = APIRouter(prefix="/sites", tags=["sites"])

def seed_sites(db: sqlite3.Connection) -> None:
    """Insert missing sites from seed CSV. Existing rows (by site_code) are never overwritten."""
    seed_dir = resolve_seed_dir()
    if seed_dir is None:
        return
    seed_file = seed_dir / "sites.csv"
    if not seed_file.exists():
        return
    now = _now()
    with seed_file.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=get_csv_delimiter())
        for row in reader:
            site_code = (row.get("site_code") or "").strip()
            if not site_code:
                continue
            if db.execute(
                "SELECT 1 FROM sites WHERE site_code = ?", (site_code,)
            ).fetchone():
                continue
            lat_raw = (row.get("latitude") or "").strip()
            lon_raw = (row.get("longitude") or "").strip()
            lat = float(lat_raw) if lat_raw else None
            lon = float(lon_raw) if lon_raw else None
            db.execute(
                """INSERT INTO sites
                   (id, site_code, site, country, country_code, city_code, city,
                    location, latitude, longitude, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    site_code,
                    (row.get("site") or "").strip() or None,
                    (row.get("country") or "").strip(),
                    (row.get("country_code") or "").strip() or None,
                    (row.get("city_code") or "").strip() or None,
                    (row.get("city") or "").strip() or None,
                    (row.get("location") or "").strip() or None,
                    lat,
                    lon,
                    now,
                    now,
                ),
            )
    db.commit()


def _row_to_site(row: sqlite3.Row) -> SiteRead:
    return SiteRead(**dict(row))


def _derive_site_code(
    country_code: Optional[str], city_code: Optional[str], site: Optional[str]
) -> str:
    """Build `{country_code}{city_code}{site}`; empty string when no part is set.

    Derived fields are computed on read and never accepted from clients, so this is
    the single definition used by both create and update.
    """
    return (country_code or "") + (city_code or "") + (site or "")


def find_code_conflict(
    db: sqlite3.Connection,
    country_code: Optional[str],
    country: Optional[str],
    city_code: Optional[str],
    city: Optional[str],
    exclude_id: Optional[str] = None,
) -> Optional[str]:
    """Return why a short code cannot be bound to this name, or None when it can.

    The codes are a shared vocabulary across sites, so 'NO' must not mean Norway in
    one row and Norfolk in another. A plain message rather than an HTTPException so
    the Excel importer can apply the same rule and *skip* the offending row — its
    contract reports problems per row instead of aborting the upload.
    """
    checks = (
        ("country_code", "country", country_code, country),
        ("city_code", "city", city_code, city),
    )
    for code_column, name_column, code, name in checks:
        if not (code and name):
            continue
        # Column names come from the literal tuple above, never from user input.
        row = db.execute(
            f"SELECT {name_column} FROM sites WHERE {code_column} = ? AND {name_column} != ?"
            + (" AND id != ?" if exclude_id else ""),
            (code, name, exclude_id) if exclude_id else (code, name),
        ).fetchone()
        if row:
            return (
                f"{name_column.capitalize()} code '{code}' is already associated "
                f"with '{row[name_column]}'. "
                f"Use a different code or the same {name_column} name."
            )
    return None


def _check_code_conflicts(
    db: sqlite3.Connection,
    country_code: Optional[str],
    country: Optional[str],
    city_code: Optional[str],
    city: Optional[str],
    exclude_id: Optional[str] = None,
) -> None:
    reason = find_code_conflict(db, country_code, country, city_code, city, exclude_id)
    if reason:
        raise HTTPException(status_code=409, detail=reason)


@router.get("", response_model=list[SiteRead])
def list_sites(
    q: Optional[str] = Query(default=None),
    db: sqlite3.Connection = Depends(get_db),
):
    if q:
        rows = db.execute(
            "SELECT * FROM sites WHERE (site_code LIKE ? OR country LIKE ?)",
            (f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM sites").fetchall()
    return [_row_to_site(r) for r in rows]


@router.get("/{site_id}", response_model=SiteRead)
def get_site(site_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Site not found")
    return _row_to_site(row)


@router.post("", response_model=SiteRead, status_code=201)
def create_site(site_in: SiteCreate, db: sqlite3.Connection = Depends(get_db)):
    site_code_value = _derive_site_code(
        site_in.country_code, site_in.city_code, site_in.site
    ) or None
    if not site_code_value:
        raise HTTPException(
            status_code=422,
            detail="site_code could not be derived — provide country_code, city_code and site.",
        )
    if db.execute(
        "SELECT 1 FROM sites WHERE site_code = ?", (site_code_value,)
    ).fetchone():
        raise HTTPException(
            status_code=409, detail=f"Site with site_code '{site_code_value}' already exists"
        )
    _check_code_conflicts(
        db, site_in.country_code, site_in.country, site_in.city_code, site_in.city
    )

    new_id = str(uuid.uuid4())
    now = _now()
    device = get_device_name(db)
    db.execute(
        """INSERT INTO sites
           (id, site_code, site, country, country_code, city_code, city,
            location, longitude, latitude, comments,
            created_at, updated_at, created_by, updated_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            new_id,
            site_code_value,
            site_in.site,
            site_in.country,
            site_in.country_code,
            site_in.city_code,
            site_in.city,
            site_in.location,
            site_in.longitude,
            site_in.latitude,
            site_in.comments,
            now,
            now,
            device,
            device,
        ),
    )
    db.commit()
    row = db.execute("SELECT * FROM sites WHERE id = ?", (new_id,)).fetchone()
    return _row_to_site(row)


@router.patch("/{site_id}", response_model=SiteRead)
def update_site(site_id: str, site_in: SiteUpdate, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM sites WHERE id = ? ", (site_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Site not found")

    current = dict(row)
    update_data = site_in.model_dump(exclude_unset=True)

    # Merge for conflict checks
    country_code = update_data.get("country_code", current["country_code"])
    country = update_data.get("country", current["country"])
    city_code = update_data.get("city_code", current["city_code"])
    city = update_data.get("city", current["city"])
    _check_code_conflicts(db, country_code, country, city_code, city, exclude_id=site_id)

    # ── site_code components are frozen once samples exist ──────────────────
    # site_code is copied into every sample's stored sample_code, so renaming a site that
    # has samples leaves those codes stale — verified behaviour before this guard: a site
    # went NOBGOPark -> NOBGOHarbour while its sample stayed NOBGOPark_water. Two harms
    # follow immediately, before anything touches a pipeline: the stored copies are wrong,
    # and sample_code is half the sync merge key (UNIQUE (sample_code, sampling_date)), so
    # two devices renaming independently diverge.
    #
    # Cascading the rename was considered and rejected: `alias` (sample_code + barcode) is
    # written into the Nextflow samplesheet (pipeline/samplesheet.py), so output directories
    # and reports already on disk are named from the old code. Rewriting the database would
    # silently break the correspondence with those files, and nothing could repair it.
    #
    # A site with no samples has no stored copy anywhere, so correcting a typo stays allowed.
    # Everything except these three fields (city, country, location, coordinates, comments)
    # remains editable at any time.
    changed_components = [
        field
        for field in ("country_code", "city_code", "site")
        if field in update_data and update_data[field] != current[field]
    ]
    if changed_components:
        sample_count = db.execute(
            "SELECT COUNT(*) FROM samples WHERE site_id = ?", (site_id,)
        ).fetchone()[0]
        if sample_count:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot change {', '.join(changed_components)} on site "
                    f"'{current['site_code']}': {sample_count} sample(s) reference it, and "
                    "their sample_code — which also names pipeline output directories on "
                    "disk — is derived from it. Create a new site instead. Location, "
                    "coordinates and comments remain freely editable; country and city can "
                    "be corrected too, but must stay consistent with their codes, which are "
                    "shared across all sites."
                ),
            )

    # Recalculate site_code if components changed
    new_site_code = current["site_code"]
    if any(k in update_data for k in ("country_code", "city_code", "site")):
        derived = _derive_site_code(
            update_data.get("country_code") or current["country_code"],
            update_data.get("city_code") or current["city_code"],
            update_data.get("site") or current["site"],
        )
        if derived:
            conflict = db.execute(
                "SELECT 1 FROM sites WHERE site_code = ? AND id != ?",
                (derived, site_id),
            ).fetchone()
            if conflict:
                raise HTTPException(
                    status_code=409,
                    detail=f"Derived site_code '{derived}' already exists for another site.",
                )
            new_site_code = derived

    now = _now()
    # Only the columns that actually differ; see the note in samples.py::update_sample for
    # why a fixed full-row UPDATE was a sync-correctness problem and not just noise.
    candidates = {
        "site_code": new_site_code,
        "site": update_data.get("site", current["site"]),
        "country": update_data.get("country", current["country"]),
        "country_code": update_data.get("country_code", current["country_code"]),
        "city_code": update_data.get("city_code", current["city_code"]),
        "city": update_data.get("city", current["city"]),
        "location": update_data.get("location", current["location"]),
        "longitude": update_data.get("longitude", current["longitude"]),
        "latitude": update_data.get("latitude", current["latitude"]),
        "comments": update_data.get("comments", current["comments"]),
    }
    changed = {key: value for key, value in candidates.items() if current[key] != value}
    if changed:
        sql, params = build_update("sites", changed, site_id, now, get_device_name(db))
        db.execute(sql, params)
    db.commit()
    row = db.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    return _row_to_site(row)


@router.delete("/{site_id}", status_code=204)
def delete_site(site_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT 1 FROM sites WHERE id = ?", (site_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Site not found")
    in_use = db.execute(
        "SELECT 1 FROM samples WHERE site_id = ? LIMIT 1", (site_id,)
    ).fetchone()
    if in_use:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete site: it is referenced by one or more samples",
        )
    db.execute("DELETE FROM sites WHERE id = ?", (site_id,))
    db.commit()
