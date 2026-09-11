import sqlite3

import pycountry
from fastapi import APIRouter, Depends

from ..database import get_db

router = APIRouter(prefix="/autocomplete", tags=["autocomplete"])


@router.get("/site-ids", response_model=list[str])
def site_ids(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT DISTINCT site_code FROM sites ORDER BY site_code"
    ).fetchall()
    return [r["site_code"] for r in rows if r["site_code"]]


def _codes_in_use(
    db: sqlite3.Connection, code_column: str, name_column: str
) -> dict[str, str]:
    """Return {code: name} for the codes already used by registered sites.

    A code with no name falls back to itself so the autocomplete entry is never blank.
    """
    # Column names are supplied by this module's callers, never by request data.
    rows = db.execute(
        f"SELECT DISTINCT {code_column}, {name_column} FROM sites WHERE {code_column} IS NOT NULL"
    ).fetchall()
    return {r[code_column]: r[name_column] or r[code_column] for r in rows}


def _as_options(merged: dict[str, str]) -> list[dict[str, str]]:
    """Render a {code: description} map as autocomplete options, sorted by description."""
    return sorted(
        ({"code": code, "description": desc} for code, desc in merged.items()),
        key=lambda option: option["description"],
    )


@router.get("/country-codes")
def country_codes(db: sqlite3.Connection = Depends(get_db)):
    merged = _codes_in_use(db, "country_code", "country")
    # Offer every ISO country too, without overriding a name this instance already uses.
    for country in pycountry.countries:
        merged.setdefault(country.alpha_2, country.name)
    return _as_options(merged)


@router.get("/city-codes")
def city_codes(db: sqlite3.Connection = Depends(get_db)):
    # Cities have no reference list — only the codes this instance already uses.
    return _as_options(_codes_in_use(db, "city_code", "city"))


@router.get("/protocol-ids", response_model=list[str])
def protocol_ids(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT DISTINCT protocol_id FROM nanopore_run_accessions WHERE protocol_id IS NOT NULL ORDER BY protocol_id"
    ).fetchall()
    return [r["protocol_id"] for r in rows]


@router.get("/sequencing-kit-ids", response_model=list[str])
def sequencing_kit_ids(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT DISTINCT sequencing_kit_id FROM nanopore_run_accessions WHERE sequencing_kit_id IS NOT NULL ORDER BY sequencing_kit_id"
    ).fetchall()
    return [r["sequencing_kit_id"] for r in rows]


@router.get("/last-run-defaults", response_model=dict)
def last_run_defaults(db: sqlite3.Connection = Depends(get_db)):
    row = db.execute(
        "SELECT nr.*, s.sample_code, nra.protocol_id, nra.sequencing_kit_id "
        "FROM nanopore_runs nr "
        "LEFT JOIN samples s ON s.id = nr.sample_id "
        "LEFT JOIN nanopore_run_accessions nra ON nra.id = nr.accession_id "
        # created_at has millisecond precision, so two runs registered in the same
        # millisecond tie and SQLite is free to return either — which made this endpoint
        # (and the test covering it) non-deterministic. rowid breaks the tie by insertion
        # order, which is what "the last run" means when the clock cannot separate them.
        "ORDER BY nr.created_at DESC, nr.rowid DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {}
    return {
        "sample_code": row["sample_code"],
        "protocol_id": row["protocol_id"],
        "sequencing_kit_id": row["sequencing_kit_id"],
    }
