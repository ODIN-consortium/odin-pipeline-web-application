"""Shared SQL query functions — single source of truth for recurring query patterns.

All functions accept a ``sqlite3.Connection`` (with ``row_factory = sqlite3.Row``
already set by the app's DB setup) and return ``sqlite3.Row`` objects or plain
Python values, so callers can project and filter without coupling to raw SQL.

Adding a query here instead of inline means:
- Changing join conditions (e.g. a new exclusion flag, a schema rename) happens
  in exactly one place and all callers benefit automatically.
- Unit tests can mock or assert on named functions rather than raw SQL strings.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from ..settings_resolver import lookup_setting
from ..utils import sql_placeholders

# ─────────────────────────────────────────────────────────────────────────────
# SQL-A — Non-excluded barcode fetch
# ─────────────────────────────────────────────────────────────────────────────

def fetch_run_barcodes(
    db: sqlite3.Connection,
    run_accessions: list[str],
    *,
    include_excluded: bool = False,
) -> list[sqlite3.Row]:
    """Return one row per barcode for the given run_accession(s), enriched with
    sample and site metadata.

    Returned columns:
        run_accession, barcode, sample_id, type, sample_code, sampling_date,
        sample_type, protocol_id, sequencing_kit_id, site_id, site_code,
        alias, is_excluded.

    By default excluded barcodes (those present in nanopore_barcode_exclusions)
    are omitted.  Pass ``include_excluded=True`` to include them; in that case
    ``is_excluded`` will be 1 for excluded rows and 0 otherwise.  When
    ``include_excluded=False`` every returned row has ``is_excluded = 0``.

    Callers filter/project the columns they need — do not duplicate the join.
    """
    if not run_accessions:
        return []
    placeholders = sql_placeholders(run_accessions)

    if include_excluded:
        return db.execute(
            f"""
            SELECT nra.run_accession,
                   nr.barcode,
                   nr.sample_id,
                   nr.type,
                   s.sample_code,
                   s.sampling_date,
                   s.sample_type,
                   nra.protocol_id,
                   nra.sequencing_kit_id,
                   st.id       AS site_id,
                   st.site_code,
                   CASE WHEN s.sample_code IS NOT NULL
                        THEN s.sample_code || '_' || nr.barcode
                        ELSE NULL END AS alias,
                   CASE WHEN e.barcode IS NOT NULL THEN 1 ELSE 0 END AS is_excluded
            FROM nanopore_runs nr
            JOIN nanopore_run_accessions nra ON nra.id = nr.accession_id
            LEFT JOIN samples s  ON s.id  = nr.sample_id
            LEFT JOIN sites   st ON st.id = s.site_id
            LEFT JOIN nanopore_barcode_exclusions e
              ON e.run_accession = nra.run_accession AND e.barcode = nr.barcode
            WHERE nra.run_accession IN ({placeholders})
            ORDER BY nra.run_accession, nr.barcode
            """,
            run_accessions,
        ).fetchall()

    return db.execute(
        f"""
        SELECT nra.run_accession,
               nr.barcode,
               nr.sample_id,
               nr.type,
               s.sample_code,
               s.sampling_date,
               s.sample_type,
               nra.protocol_id,
               nra.sequencing_kit_id,
               st.id       AS site_id,
               st.site_code,
               CASE WHEN s.sample_code IS NOT NULL
                    THEN s.sample_code || '_' || nr.barcode
                    ELSE NULL END AS alias,
               0 AS is_excluded
        FROM nanopore_runs nr
        JOIN nanopore_run_accessions nra ON nra.id = nr.accession_id
        LEFT JOIN samples s  ON s.id  = nr.sample_id
        LEFT JOIN sites   st ON st.id = s.site_id
        WHERE nra.run_accession IN ({placeholders})
          AND NOT EXISTS (
              SELECT 1 FROM nanopore_barcode_exclusions e
              WHERE e.run_accession = nra.run_accession
                AND e.barcode       = nr.barcode
          )
        ORDER BY nra.run_accession, nr.barcode
        """,
        run_accessions,
    ).fetchall()


# ─────────────────────────────────────────────────────────────────────────────
# SQL-B — Pipeline run accessions
# ─────────────────────────────────────────────────────────────────────────────

def get_pipeline_run_accessions(db: sqlite3.Connection, run_id: str) -> list[str]:
    """Return the ordered list of run_accessions for a pipeline run."""
    rows = db.execute(
        "SELECT run_accession FROM pipeline_run_accessions"
        " WHERE pipeline_run_id = ? ORDER BY rowid",
        (run_id,),
    ).fetchall()
    return [r["run_accession"] for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# SQL-C — Auto-merge decision
# ─────────────────────────────────────────────────────────────────────────────

def get_auto_merge(db: sqlite3.Connection, run_accession: str) -> bool:
    """Return the auto_merge flag for a run, defaulting to False if no decision exists."""
    row = db.execute(
        "SELECT auto_merge FROM nanopore_merge_decisions WHERE run_accession = ?",
        (run_accession,),
    ).fetchone()
    return bool(row["auto_merge"]) if row is not None else False


# ─────────────────────────────────────────────────────────────────────────────
# SQL-D — Run accession metadata
# ─────────────────────────────────────────────────────────────────────────────

def get_run_accession_meta(
    db: sqlite3.Connection, run_accession: str
) -> Optional[sqlite3.Row]:
    """Return the nanopore_run_accessions row for a run_accession, or None.

    Columns: protocol_id, sequencing_kit_id.
    """
    return db.execute(
        "SELECT protocol_id, sequencing_kit_id"
        " FROM nanopore_run_accessions WHERE run_accession = ?",
        (run_accession,),
    ).fetchone()


# ─────────────────────────────────────────────────────────────────────────────
# SQL-E — Related runs (sample_id-based)
# ─────────────────────────────────────────────────────────────────────────────

def get_related_run_accessions(db: sqlite3.Connection, run_accession: str) -> list[str]:
    """Return run_accessions that share at least one barcode→sample_id with
    *run_accession*.

    Two runs are related when the same barcode maps to the same sample_id in
    both.  This is the canonical definition used by the discovery scan and the
    samplesheet builders — a single source of truth so both behave identically
    if the relatedness condition changes.
    """
    rows = db.execute(
        """
        SELECT DISTINCT nra2.run_accession
        FROM nanopore_runs nr1
        JOIN nanopore_run_accessions nra1
          ON nra1.id = nr1.accession_id AND nra1.run_accession = ?
        JOIN nanopore_runs nr2
          ON nr1.sample_id = nr2.sample_id
         AND nr1.barcode   = nr2.barcode
         AND nr2.accession_id != nr1.accession_id
         AND nr1.sample_id IS NOT NULL
        JOIN nanopore_run_accessions nra2 ON nra2.id = nr2.accession_id
        WHERE nra2.run_accession IS NOT NULL
        """,
        (run_accession,),
    ).fetchall()
    return [r["run_accession"] for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# SQL-F — Postprocessor metadata fetch
# ─────────────────────────────────────────────────────────────────────────────

def get_metadata_rows_from_db(
    db: sqlite3.Connection,
    run_accessions: list[str],
) -> list[dict]:
    """Return per-barcode metadata dicts for the given run_accessions.

    Column shape matches what the old Excel metadata sheet provided so that
    ``add_metadata()`` in the postprocessors produces identical output to the
    original pipeline scripts.

    Key renames vs the DB schema (intentional — part of the Enlighten data
    contract):
    - ``longitude``  →  ``lon``   (float or None)
    - ``latitude``   →  ``lat``   (float or None)
    - ``sample_code`` is duplicated as ``sample_id`` for compatibility with
      the old Excel column name used by ``add_metadata()``.
    - ``site_code``  →  ``sampling_site_id``
    """
    if not run_accessions:
        return []

    rows = db.execute(
        f"""
        SELECT
            nra.run_accession,
            COALESCE(trim(nr.barcode), '')       AS barcode,
            COALESCE(nra.protocol_id, '')        AS protocol_id,
            COALESCE(nra.sequencing_kit_id, '')  AS sequencing_kit_id,
            COALESCE(nr.type, '')                AS type,
            COALESCE(nra.runName, '')            AS runName,
            COALESCE(nra.sampleName, '')         AS sampleName,
            COALESCE(
                s.sample_code || '_' || trim(nr.barcode), ''
            )                                    AS alias,
            COALESCE(s.sample_code, '')          AS sample_code,
            COALESCE(s.sample_code, '')          AS sample_id,
            COALESCE(s.sampling_date, '')        AS sampling_date,
            COALESCE(site.site_code, '')         AS sampling_site_id,
            COALESCE(s.sample_type, '')          AS sample_type,
            COALESCE(site.country, '')           AS country,
            site.longitude                       AS lon,
            site.latitude                        AS lat
        FROM nanopore_runs nr
        JOIN nanopore_run_accessions nra ON nra.id = nr.accession_id
        JOIN samples s  ON nr.sample_id = s.id
        LEFT JOIN sites site ON s.site_id = site.id
        WHERE nra.run_accession IN ({sql_placeholders(run_accessions)})
          AND nr.barcode IS NOT NULL
          AND trim(nr.barcode) != ''
        ORDER BY nr.barcode, nra.run_accession
        """,
        run_accessions,
    ).fetchall()

    return [{k: r[k] for k in r.keys()} for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# SQL-G — Continuation detection
# ─────────────────────────────────────────────────────────────────────────────

def get_continuation_window(db: sqlite3.Connection) -> float:
    """Return the configured continuation time window in hours, defaulting to 48.0."""
    raw = lookup_setting(db, "continuation_time_window_hours")
    try:
        return float(raw) if raw else 48.0
    except (ValueError, TypeError):
        return 48.0


def find_continuation_partners(
    db: sqlite3.Connection,
    run_accession: str,
    time_window_hours: float,
) -> list[sqlite3.Row]:
    """Return continuation partner rows for *run_accession*.

    A partner is a run on the same flow cell with a compatible kit started
    within *time_window_hours* of this run stopping (or vice-versa).

    Returned columns: partner, partner_kit, partner_run_name, gap_hours.
    """
    return db.execute(
        """
        SELECT ndc2.run_accession AS partner,
               ndc2.sequencing_kit_id AS partner_kit,
               ndc2.run_name AS partner_run_name,
               (julianday(ndc2.run_started) - julianday(ndc1.run_stopped)) * 24 AS gap_hours
        FROM nanopore_disk_cache ndc1
        JOIN nanopore_disk_cache ndc2
          ON ndc2.flow_cell_id = ndc1.flow_cell_id
         AND ndc2.run_accession != ndc1.run_accession
         AND ndc1.run_stopped IS NOT NULL AND ndc2.run_started IS NOT NULL
         AND (ndc1.sequencing_kit_id IS NULL OR ndc2.sequencing_kit_id IS NULL
              OR ndc1.sequencing_kit_id = ndc2.sequencing_kit_id)
         AND (julianday(ndc2.run_started) - julianday(ndc1.run_stopped)) * 24 BETWEEN 0 AND ?
        WHERE ndc1.run_accession = ?
        UNION
        SELECT ndc2.run_accession AS partner,
               ndc2.sequencing_kit_id AS partner_kit,
               ndc2.run_name AS partner_run_name,
               (julianday(ndc1.run_started) - julianday(ndc2.run_stopped)) * 24 AS gap_hours
        FROM nanopore_disk_cache ndc1
        JOIN nanopore_disk_cache ndc2
          ON ndc2.flow_cell_id = ndc1.flow_cell_id
         AND ndc2.run_accession != ndc1.run_accession
         AND ndc2.run_stopped IS NOT NULL AND ndc1.run_started IS NOT NULL
         AND (ndc1.sequencing_kit_id IS NULL OR ndc2.sequencing_kit_id IS NULL
              OR ndc1.sequencing_kit_id = ndc2.sequencing_kit_id)
         AND (julianday(ndc1.run_started) - julianday(ndc2.run_stopped)) * 24 BETWEEN 0 AND ?
        WHERE ndc1.run_accession = ?
        """,
        (time_window_hours, run_accession, time_window_hours, run_accession),
    ).fetchall()
