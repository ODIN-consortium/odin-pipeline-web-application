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

from ..parsers.minknow_run_info import extract_from_run_accession
from ..settings_resolver import lookup_setting
from ..utils import sql_placeholders

# ─────────────────────────────────────────────────────────────────────────────
# SQL-D0 — Derived identifiers
#
# These are computed on read and never stored, so they cannot go stale (see
# test_derived_code_immutability.py). Every query that surfaces one takes it from
# here, so the four that do cannot disagree about what it means.
#
# Each fragment assumes the canonical join aliases used throughout this module:
#   s = samples, nr = nanopore_runs, nra = nanopore_run_accessions
# ─────────────────────────────────────────────────────────────────────────────

#: MinKNOW's sample identifier: sample_code + protocol + sequencing kit. NULL when
#: any part is missing, because a partial identifier would collide with a real one.
SQL_MINKNOW_SAMPLE_ID = (
    "CASE WHEN s.sample_code IS NOT NULL "
    "AND nra.protocol_id IS NOT NULL "
    "AND nra.sequencing_kit_id IS NOT NULL "
    "THEN s.sample_code || '_' || nra.protocol_id || '_' || nra.sequencing_kit_id "
    "ELSE NULL END"
)

#: One physical sample, one library prep: the MinKNOW identifier plus the sampling
#: date. `sample_code` alone identifies a site and a sample type, and recurs on every
#: visit to that site, so counting distinct `sample_code` counts sites, not samples.
#:
#: SQLite cannot enforce this as a UNIQUE index: the expression spans three tables,
#: and an expression index may only reference columns of the table it is built on.
#: The invariant is pinned by tests instead — see test_queries.py.
SQL_SAMPLE_KEY = (
    f"CASE WHEN ({SQL_MINKNOW_SAMPLE_ID}) IS NOT NULL "
    "AND s.sampling_date IS NOT NULL "
    f"THEN ({SQL_MINKNOW_SAMPLE_ID}) || '_' || s.sampling_date "
    "ELSE NULL END"
)


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
        run_accession, barcode, sample_id, type, replicate, replicate_type,
        sample_code, sampling_date, sample_type, protocol_id, sequencing_kit_id,
        site_id, site_code, alias, is_excluded.

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
                   nr.replicate,
                   nr.replicate_type,
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
               nr.replicate,
               nr.replicate_type,
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

def load_flow_cells(db: sqlite3.Connection) -> dict[str, str]:
    """run_accession -> flow cell ID, for every run the disk scan has seen."""
    return {
        r["run_accession"]: r["flow_cell_id"]
        for r in db.execute(
            "SELECT run_accession, flow_cell_id FROM nanopore_disk_cache WHERE flow_cell_id IS NOT NULL"
        )
    }


def flow_cell_of(run_accession: str, known: dict[str, str]) -> Optional[str]:
    """The run's flow cell: from the disk scan when it has the run, else from the
    accession itself, which MinKNOW builds around it. None when neither says."""
    if run_accession in known:
        return known[run_accession]
    return extract_from_run_accession(run_accession)[0]


def same_flow_cell(ra1: str, ra2: str, known: dict[str, str]) -> bool:
    """True when both runs are known to be on one flow cell."""
    fc1 = flow_cell_of(ra1, known)
    return fc1 is not None and fc1 == flow_cell_of(ra2, known)


def get_related_run_accessions(db: sqlite3.Connection, run_accession: str) -> list[str]:
    """Return run_accessions on the same flow cell that share at least one
    barcode→sample_id with *run_accession*.

    Two runs are related when the same barcode maps to the same sample_id in
    both, and they are loadings of one flow cell. This is the canonical definition used by the discovery scan and the
    samplesheet builders — a single source of truth so both behave identically
    if the relatedness condition changes.

    The flow cell condition is what makes "related" mean "the same library": a sample
    sequenced again on another flow cell is a separate sequencing event and is never
    merged. Without it the merge concatenated a whole run from another flow cell into
    this one, barcode by barcode — including barcodes that hold different samples in
    the two runs (TazaniaDAR2 + samplingDay1, 9 Oct 2026: river water read into
    drinking-water barcodes).
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
    known = load_flow_cells(db)
    return [
        r["run_accession"] for r in rows if same_flow_cell(run_accession, r["run_accession"], known)
    ]


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
    - ``site_code``  →  ``sampling_site_id``
    - ``sample_id`` is MinKNOW's identifier (``SQL_MINKNOW_SAMPLE_ID``), matching
      the column of that name in the mobile-lab metadata workbook.
    - ``sample_key`` (``SQL_SAMPLE_KEY``) identifies one sample: the MinKNOW
      identifier plus the sampling date. Consumers counting samples want this;
      ``sample_code`` alone counts sites.

    ``trim(nr.barcode)`` is deliberate. Every write path strips the barcode, but this
    query also runs against databases maintained elsewhere, which may hold a padded
    value. Without the trim such a row would emit a ``barcode`` and an ``alias`` that
    disagree with each other in the same feather.
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
            nr.replicate                         AS replicate,
            COALESCE(nr.replicate_type, '')      AS replicate_type,
            COALESCE(nra.runName, '')            AS runName,
            COALESCE(nra.sampleName, '')         AS sampleName,
            COALESCE(
                s.sample_code || '_' || trim(nr.barcode), ''
            )                                    AS alias,
            COALESCE(s.sample_code, '')          AS sample_code,
            COALESCE({SQL_MINKNOW_SAMPLE_ID}, '') AS sample_id,
            COALESCE({SQL_SAMPLE_KEY}, '')       AS sample_key,
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


AMR_REPLICATES_POOL = "pool"
AMR_REPLICATES_PER_BARCODE = "per_barcode"
AMR_REPLICATES_VALUES = (AMR_REPLICATES_POOL, AMR_REPLICATES_PER_BARCODE)


def get_amr_replicates(db: sqlite3.Connection) -> str:
    """How a wf-metagenomics AMR run treats a replicate group: ``pool`` or ``per_barcode``.

    The ``amr_replicates`` setting; unset means ``pool``, so that a sample's AMR profile
    comes from the same pooled reads as its taxonomy unless an installation chooses
    otherwise. Taxprofiler and SSU always pool and have no setting.

    Raises ``ValueError`` for any other value — it can only arrive through the
    ``ODIN_AMR_REPLICATES`` environment variable, and a launch must not guess what a
    misspelt choice meant.
    """
    raw = (lookup_setting(db, "amr_replicates") or "").strip()
    if not raw:
        return AMR_REPLICATES_POOL
    if raw not in AMR_REPLICATES_VALUES:
        raise ValueError(
            f"amr_replicates is '{raw}'; it must be one of {', '.join(AMR_REPLICATES_VALUES)} "
            f"(Settings, or the ODIN_AMR_REPLICATES environment variable)."
        )
    return raw


def find_continuation_partners(
    db: sqlite3.Connection,
    run_accession: str,
    time_window_hours: float,
) -> list[sqlite3.Row]:
    """Return continuation partner rows for *run_accession*.

    A partner is a run on the same flow cell with a compatible kit started
    within *time_window_hours* of this run stopping (or vice-versa).

    A run that MinKNOW aborted has no final_summary, so no ``run_stopped`` and
    usually no kit in the disk cache — and an aborted run is exactly what a
    restart on the same flow cell follows. The gap is therefore measured from
    ``run_stopped`` when known and from ``run_started`` otherwise. Requiring
    ``run_stopped`` would make the restart case undetectable.

    Returned columns: partner, partner_kit, partner_run_name, gap_hours.
    """
    return db.execute(
        """
        SELECT ndc2.run_accession AS partner,
               ndc2.sequencing_kit_id AS partner_kit,
               ndc2.run_name AS partner_run_name,
               (julianday(ndc2.run_started)
                - julianday(COALESCE(ndc1.run_stopped, ndc1.run_started))) * 24 AS gap_hours
        FROM nanopore_disk_cache ndc1
        JOIN nanopore_disk_cache ndc2
          ON ndc2.flow_cell_id = ndc1.flow_cell_id
         AND ndc2.run_accession != ndc1.run_accession
         AND ndc1.run_started IS NOT NULL AND ndc2.run_started IS NOT NULL
         AND (ndc1.sequencing_kit_id IS NULL OR ndc2.sequencing_kit_id IS NULL
              OR ndc1.sequencing_kit_id = ndc2.sequencing_kit_id)
         AND (julianday(ndc2.run_started)
              - julianday(COALESCE(ndc1.run_stopped, ndc1.run_started))) * 24 BETWEEN 0 AND ?
        WHERE ndc1.run_accession = ?
        UNION
        SELECT ndc2.run_accession AS partner,
               ndc2.sequencing_kit_id AS partner_kit,
               ndc2.run_name AS partner_run_name,
               (julianday(ndc1.run_started)
                - julianday(COALESCE(ndc2.run_stopped, ndc2.run_started))) * 24 AS gap_hours
        FROM nanopore_disk_cache ndc1
        JOIN nanopore_disk_cache ndc2
          ON ndc2.flow_cell_id = ndc1.flow_cell_id
         AND ndc2.run_accession != ndc1.run_accession
         AND ndc2.run_started IS NOT NULL AND ndc1.run_started IS NOT NULL
         AND (ndc1.sequencing_kit_id IS NULL OR ndc2.sequencing_kit_id IS NULL
              OR ndc1.sequencing_kit_id = ndc2.sequencing_kit_id)
         AND (julianday(ndc1.run_started)
              - julianday(COALESCE(ndc2.run_stopped, ndc2.run_started))) * 24 BETWEEN 0 AND ?
        WHERE ndc1.run_accession = ?
        """,
        (time_window_hours, run_accession, time_window_hours, run_accession),
    ).fetchall()


def get_merge_group(
    db: sqlite3.Connection, run_accession: str, time_window_hours: Optional[float] = None
) -> list[str]:
    """Every run a merge decision for *run_accession* covers, excluding itself.

    The group is the union of continuation partners (same flow cell, close in
    time — ``find_continuation_partners``) and runs on the same flow cell that
    share a barcode → sample link (``get_related_run_accessions``). Every member is
    a loading of this run's flow cell; nothing from another flow cell is merged. The dashboard shows this set
    as "will merge FASTQ with"; the decision snapshot, the launch-time merge
    resolution and the FASTQ concatenation must use the very same set. Taking
    continuation partners alone would show a run related through its sample as
    merged and then process it alone.
    """
    if time_window_hours is None:
        time_window_hours = get_continuation_window(db)
    group = {
        r["partner"] for r in find_continuation_partners(db, run_accession, time_window_hours)
        if r["partner"]
    }
    group.update(get_related_run_accessions(db, run_accession))
    group.discard(run_accession)
    return sorted(group)
