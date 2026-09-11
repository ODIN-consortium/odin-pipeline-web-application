"""
Sync endpoints — Phase 2 (transport) and Phase 3 (merge algorithm).

Routes
------
GET  /sync/export    – download a JSON snapshot of all syncable tables
POST /sync/preview   – classify incoming JSON rows into a merge diff
POST /sync/apply     – apply accepted decisions from a preview

Merge categories (Phase 3)
--------------------------
new                  – UUID not in local DB, UNIQUE keys also absent
identical            – UUID exists locally, all values match
updated              – UUID exists locally, values differ, no UNIQUE clash
independent_duplicate– UUID unknown, but UNIQUE key matches a different local row
conflict             – UUID exists locally AND UNIQUE key clashes with yet another row
"""

from __future__ import annotations

import io
import json
import re
import sqlite3
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..database import get_db
from ..settings_resolver import get_device_name
from ..utils import sql_placeholders
from ..utils import utc_now_str as _now

router = APIRouter(prefix="/sync", tags=["sync"])

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

EXPORT_VERSION = "1"

#: Tables that participate in sync, with their UNIQUE constraint column groups.
#: Each inner list is one UNIQUE constraint (multi-column = compound key).
#: nanopore_run_accessions has TWO separate single-column UNIQUE constraints.
SYNCABLE_TABLES: list[dict[str, Any]] = [
    {"name": "lookup_values",           "unique_keys": [["list", "code"]]},
    {"name": "databases",               "unique_keys": [["tool", "db_name"]]},
    {"name": "sites",                   "unique_keys": [["site_code"]]},
    {"name": "samples",                 "unique_keys": [["sample_code", "sampling_date"]]},
    {"name": "nanopore_run_accessions", "unique_keys": [["run_accession"], ["label"]]},
    {"name": "nanopore_runs",           "unique_keys": [["accession_id", "barcode"]]},
    {"name": "biomeme_runs",            "unique_keys": [["biomeme_run_name"]]},
]

_TABLE_MAP: dict[str, dict[str, Any]] = {t["name"]: t for t in SYNCABLE_TABLES}

# Only allow safe identifier characters in column / table names to prevent
# SQL injection via crafted import JSON.
_SAFE_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────────────────────────────────────


class SyncImport(BaseModel):
    """The JSON envelope produced by GET /sync/export."""

    version: str
    exported_at: str
    #: Optional because device_name has no default: an export made before a device name
    #: was configured carries null here. Requiring a string would mean ODIN could produce
    #: a file it then refused to import.
    exported_by: Optional[str] = None
    tables: dict[str, list[dict[str, Any]]]


class SyncApplyRequest(BaseModel):
    import_data: SyncImport
    #: table_name → { incoming_row_id → action }
    #: Valid actions: "accept" | "skip" | "keep_mine" | "use_theirs"
    decisions: dict[str, dict[str, str]]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _safe_ident(name: str) -> str:
    """Raise 422 if *name* contains characters that are not safe for SQL identifiers."""
    if not _SAFE_IDENT_RE.match(name):
        raise HTTPException(status_code=422, detail=f"Invalid identifier in import data: {name!r}")
    return name


def _validate_row_columns(row: dict[str, Any]) -> None:
    for col in row:
        _safe_ident(col)


def _sync_insert(table: str, row: dict[str, Any]) -> tuple[str, list]:
    """Build ``INSERT INTO <table> (...) VALUES (...)`` for a full row.

    Every column identifier is passed through ``_safe_ident``, so injection
    safety is structural — callers cannot forget the guard.
    """
    cols = ", ".join(_safe_ident(c) for c in row)
    placeholders = ", ".join("?" for _ in row)
    sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"  # noqa: S608
    return sql, list(row.values())


def _sync_update(table: str, set_fields: dict[str, Any], record_id: Any) -> tuple[str, list]:
    """Build ``UPDATE <table> SET ... WHERE id = ?`` for *set_fields*.

    Every column identifier is passed through ``_safe_ident`` (see _sync_insert).
    """
    set_clause = ", ".join(f"{_safe_ident(k)} = ?" for k in set_fields)
    sql = f"UPDATE {table} SET {set_clause} WHERE id = ?"  # noqa: S608
    return sql, list(set_fields.values()) + [record_id]


def _find_unique_match(
    db: sqlite3.Connection,
    table_name: str,
    unique_key_groups: list[list[str]],
    row: dict[str, Any],
    exclude_id: Optional[str] = None,
) -> Optional[sqlite3.Row]:
    """Return the first local row that matches any non-null UNIQUE key group of *row*.

    NULL values are never considered a UNIQUE match (mirrors SQLite behaviour).
    If *exclude_id* is given, that local row is excluded from the search (used
    when we already know the UUID matches and want to detect clashes with *other* rows).
    """
    for key_cols in unique_key_groups:
        values = [row.get(col) for col in key_cols]
        if any(v is None for v in values):
            continue  # NULLs never clash in UNIQUE constraints
        where_parts = [f"{_safe_ident(col)} = ?" for col in key_cols]
        params: list[Any] = values[:]
        if exclude_id is not None:
            where_parts.append("id != ?")
            params.append(exclude_id)
        where_clause = " AND ".join(where_parts)
        local = db.execute(
            f"SELECT * FROM {table_name} WHERE {where_clause}",  # noqa: S608
            params,
        ).fetchone()
        if local is not None:
            return local
    return None


# Fields that record audit info but should not drive merge decisions.
# Two rows are effectively identical when all *other* fields match;
# one device having NULL here while another has a name is not a real data change.
_AUDIT_FIELDS = frozenset({"created_by", "updated_by"})


def _data_equal(local: dict[str, Any], incoming: dict[str, Any]) -> bool:
    """Return True if *local* and *incoming* are identical ignoring audit fields."""
    keys = set(local) | set(incoming)
    return all(local.get(k) == incoming.get(k) for k in keys if k not in _AUDIT_FIELDS)


def _classify_table(
    db: sqlite3.Connection,
    table_def: dict[str, Any],
    incoming_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Classify each incoming row into one of five merge categories."""
    table_name = table_def["name"]
    unique_keys: list[list[str]] = table_def["unique_keys"]

    cats: dict[str, list[dict[str, Any]]] = {
        "new": [],
        "identical": [],
        "updated": [],
        "independent_duplicate": [],
        "conflict": [],
    }

    for row in incoming_rows:
        _validate_row_columns(row)
        existing: Optional[sqlite3.Row] = db.execute(
            f"SELECT * FROM {table_name} WHERE id = ?",  # noqa: S608
            (row["id"],),
        ).fetchone()

        if existing is not None:
            local_dict = dict(existing)
            if _data_equal(local_dict, row):
                cats["identical"].append({"incoming": row})
            else:
                # Check whether updating this row would create a UNIQUE clash
                # with a *different* local row (not the row we're about to update).
                clash = _find_unique_match(
                    db, table_name, unique_keys, row, exclude_id=str(row["id"])
                )
                entry: dict[str, Any] = {"incoming": row, "local": local_dict}
                if clash is not None:
                    entry["conflict_row"] = dict(clash)
                    cats["conflict"].append(entry)
                else:
                    cats["updated"].append(entry)
        else:
            # UUID unknown locally — check for independent duplicate on UNIQUE keys
            dup = _find_unique_match(db, table_name, unique_keys, row)
            if dup is not None:
                cats["independent_duplicate"].append({"incoming": row, "local": dict(dup)})
            else:
                cats["new"].append({"incoming": row})

    return cats


# ─────────────────────────────────────────────────────────────────────────────
# GET /sync/export
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/export")
def export_sync(db: sqlite3.Connection = Depends(get_db)):
    """Download a JSON snapshot of all syncable tables."""
    now = _now()
    device = get_device_name(db)

    tables: dict[str, list[dict[str, Any]]] = {}
    for t in SYNCABLE_TABLES:
        rows = db.execute(f"SELECT * FROM {t['name']}").fetchall()  # noqa: S608
        tables[t["name"]] = [dict(r) for r in rows]

    payload = {
        "version": EXPORT_VERSION,
        "exported_at": now,
        "exported_by": device,
        "tables": tables,
    }

    # device_name has no default and may be unset — the sync page warns about exactly
    # that. Name the file "unnamed-device" rather than crashing or producing "odin--<date>",
    # so an export that slipped past the warning is still obviously unattributed.
    slug = re.sub(r"[^a-z0-9]+", "-", (device or "unnamed-device").lower()).strip("-")
    filename = f"odin-{slug}-{now[:10]}.json"

    return StreamingResponse(
        io.BytesIO(json.dumps(payload, ensure_ascii=False, indent=2).encode()),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /sync/preview
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/preview")
def preview_sync(import_data: SyncImport, db: sqlite3.Connection = Depends(get_db)):
    """Classify incoming rows into a merge diff without writing anything to the DB."""
    _check_version(import_data.version)

    diff: dict[str, dict[str, Any]] = {}
    for t in SYNCABLE_TABLES:
        incoming = import_data.tables.get(t["name"], [])
        diff[t["name"]] = _classify_table(db, t, incoming)

    # Compute per-table summary counts for convenience
    summary: dict[str, dict[str, int]] = {}
    for table_name, cats in diff.items():
        summary[table_name] = {cat: len(rows) for cat, rows in cats.items()}

    return {
        "exported_by": import_data.exported_by,
        "exported_at": import_data.exported_at,
        "summary": summary,
        "diff": diff,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Apply helpers — one per decision category. Each loops its category's entries
# and mutates `counts` in place; SQL identifiers always go through _safe_ident.
# ─────────────────────────────────────────────────────────────────────────────


def _apply_new(
    db: sqlite3.Connection, table_name: str, entries: list[dict],
    decisions: dict, device: str, counts: dict,
) -> None:
    for entry in entries:
        row = entry["incoming"]
        _validate_row_columns(row)
        action = decisions.get(str(row["id"]), "accept")
        if action == "accept":
            # Stamp with local device info but preserve original timestamps
            insert_row = dict(row)
            insert_row.setdefault("created_by", device)
            insert_row.setdefault("updated_by", device)
            sql, params = _sync_insert(table_name, insert_row)
            db.execute(sql, params)
            counts["inserted"] += 1
        else:
            counts["skipped"] += 1


def _apply_updated(
    db: sqlite3.Connection, table_name: str, entries: list[dict],
    decisions: dict, device: str, now: str, counts: dict,
) -> None:
    for entry in entries:
        row = entry["incoming"]
        local = entry["local"]
        action = decisions.get(str(row["id"]), "accept")
        # Only overwrite if the incoming row is genuinely newer
        incoming_is_newer = (row.get("updated_at") or "") > (local.get("updated_at") or "")
        if action == "accept" and incoming_is_newer:
            set_fields = {k: v for k, v in row.items() if k not in ("id", "created_at")}
            set_fields["updated_by"] = device
            set_fields["updated_at"] = now
            sql, params = _sync_update(table_name, set_fields, row["id"])
            db.execute(sql, params)
            counts["updated"] += 1
        else:
            counts["skipped"] += 1


def _apply_independent_duplicate(
    db: sqlite3.Connection, table_name: str, entries: list[dict],
    decisions: dict, all_key_cols: set, device: str, now: str, counts: dict,
) -> None:
    for entry in entries:
        row = entry["incoming"]
        local = entry["local"]
        action = decisions.get(str(row["id"]), "keep_mine")
        if action == "use_theirs":
            # Update non-key, non-id fields on the *local* row (keep local UUID)
            non_key = {
                k: v for k, v in row.items()
                if k not in all_key_cols and k not in ("id", "created_at", "created_by")
            }
            if non_key:
                non_key["updated_by"] = device
                non_key["updated_at"] = now
                sql, params = _sync_update(table_name, non_key, local["id"])
                db.execute(sql, params)
                counts["updated"] += 1
            else:
                counts["skipped"] += 1
        else:
            counts["skipped"] += 1


def _apply_conflict(
    db: sqlite3.Connection, table_name: str, entries: list[dict],
    decisions: dict, device: str, now: str, counts: dict,
) -> None:
    for entry in entries:
        row = entry["incoming"]
        action = decisions.get(str(row["id"]), "keep_mine")
        if action == "use_theirs":
            set_fields = {k: v for k, v in row.items() if k not in ("id", "created_at")}
            set_fields["updated_by"] = device
            set_fields["updated_at"] = now
            sql, params = _sync_update(table_name, set_fields, row["id"])
            db.execute(sql, params)
            counts["updated"] += 1
        else:
            counts["skipped"] += 1


# ─────────────────────────────────────────────────────────────────────────────
# POST /sync/apply
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/apply")
def apply_sync(payload: SyncApplyRequest, db: sqlite3.Connection = Depends(get_db)):
    """Apply accepted decisions from a preview in a single atomic transaction.

    The server re-classifies rows from the import data to prevent the client
    from injecting arbitrary operations — decisions only control *which* of
    the server-determined changes are committed.

    Decision actions per category
    ------------------------------
    new:                  "accept" (default) | "skip"
    updated:              "accept" (default, applies if incoming.updated_at > local) | "skip"
    independent_duplicate:"use_theirs" | "keep_mine" (default)
    conflict:             "use_theirs" | "keep_mine" (default)
    identical:            always skipped (no-op)
    """
    _check_version(payload.import_data.version)

    applied: dict[str, dict[str, int]] = {}
    now = _now()
    device = get_device_name(db)

    try:
        for t in SYNCABLE_TABLES:
            table_name = t["name"]
            incoming = payload.import_data.tables.get(table_name, [])
            cats = _classify_table(db, t, incoming)
            table_decisions = payload.decisions.get(table_name, {})
            counts = {"inserted": 0, "updated": 0, "skipped": 0}

            all_key_cols: set[str] = set()
            for key_group in t["unique_keys"]:
                all_key_cols.update(key_group)

            _apply_new(db, table_name, cats["new"], table_decisions, device, counts)
            _apply_updated(db, table_name, cats["updated"], table_decisions, device, now, counts)
            _apply_independent_duplicate(
                db, table_name, cats["independent_duplicate"],
                table_decisions, all_key_cols, device, now, counts,
            )
            _apply_conflict(db, table_name, cats["conflict"], table_decisions, device, now, counts)

            applied[table_name] = counts

        db.commit()

    except sqlite3.IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                f"Merge aborted due to a constraint violation: {exc}. "
                "No changes were applied. Review conflicts and try again."
            ),
        ) from exc

    total_inserted = sum(c["inserted"] for c in applied.values())
    total_updated = sum(c["updated"] for c in applied.values())
    return {
        "applied": applied,
        "total_inserted": total_inserted,
        "total_updated": total_updated,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _check_version(version: str) -> None:
    if version != EXPORT_VERSION:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unsupported import version '{version}'. "
                f"This device expects version '{EXPORT_VERSION}'."
            ),
        )

# ─────────────────────────────────────────────────────────────────────────────
# POST /sync/restore
# ─────────────────────────────────────────────────────────────────────────────

# Upsert parents before children, and delete children before parents, because foreign keys
# are enforced. samples -> sites; nanopore_runs -> nanopore_run_accessions and samples;
# biomeme_runs -> samples. Getting either order wrong fails loudly rather than silently, which
# is the better kind of wrong, but neither list should be derived by reversing the other:
# nanopore_run_accessions has no parent among these and its position differs.
_RESTORE_UPSERT_ORDER = (
    "lookup_values",
    "databases",
    "sites",
    "samples",
    "nanopore_run_accessions",
    "nanopore_runs",
    "biomeme_runs",
)
_RESTORE_DELETE_ORDER = (
    "biomeme_runs",
    "nanopore_runs",
    "samples",
    "nanopore_run_accessions",
    "sites",
    "databases",
    "lookup_values",
)


def _table_columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in db.execute(f"PRAGMA table_info({_safe_ident(table)})")}


def _restore_table(db: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> int:
    """Insert or update every row of *table* from the snapshot, matched by id.

    Matching is by primary key, not by natural key. That is the difference from a merge: a
    merge reconciles two devices that generated different ids for the same logical row, while a
    restore puts *this* device back to a state it was in, where the ids are its own.
    """
    columns = _table_columns(db, table)
    written = 0
    for row in rows:
        _validate_row_columns(row)
        row_id = row.get("id")
        if not row_id:
            raise HTTPException(
                status_code=422,
                detail=f"Snapshot row in {table!r} has no id, so it cannot be restored.",
            )
        # Ignore columns the snapshot has but this schema does not; a snapshot taken before a
        # column was removed should still restore.
        fields = {k: v for k, v in row.items() if k in columns and k != "id"}
        exists = db.execute(
            f"SELECT 1 FROM {_safe_ident(table)} WHERE id = ?", (row_id,)  # noqa: S608
        ).fetchone()
        if exists:
            assignments = ", ".join(f"{_safe_ident(k)}=?" for k in fields)
            db.execute(
                f"UPDATE {_safe_ident(table)} SET {assignments} WHERE id = ?",  # noqa: S608
                [*fields.values(), row_id],
            )
        else:
            names = ["id", *fields.keys()]
            placeholders = ",".join("?" * len(names))
            column_list = ", ".join(_safe_ident(n) for n in names)
            db.execute(
                f"INSERT INTO {_safe_ident(table)} ({column_list}) VALUES ({placeholders})",  # noqa: S608
                [row_id, *fields.values()],
            )
        written += 1
    return written


def _delete_rows_absent_from(
    db: sqlite3.Connection, table: str, rows: list[dict[str, Any]]
) -> int:
    """Delete local rows of *table* that the snapshot does not contain.

    This is what makes a restore a restore rather than a merge, and it is the only place in the
    application that removes data the operator did not name. sync/apply deliberately never
    deletes: it exists to combine two devices, so rows the other side lacks are information to
    keep, not garbage.
    """
    keep = [r["id"] for r in rows if r.get("id")]
    if keep:
        placeholders = sql_placeholders(keep)
        cursor = db.execute(
            f"DELETE FROM {_safe_ident(table)} WHERE id NOT IN ({placeholders})",  # noqa: S608
            keep,
        )
    else:
        cursor = db.execute(f"DELETE FROM {_safe_ident(table)}")  # noqa: S608
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


@router.post("/restore")
def restore_sync(payload: SyncImport, db: sqlite3.Connection = Depends(get_db)):
    """Make this database match a snapshot: upsert everything in it, delete everything else.

    The counterpart to /sync/apply rather than a variant of it. A merge answers "add their
    information to mine"; a restore answers "put mine back to this". They take the same file and
    have opposite consequences, which is why they are separate endpoints with separate names
    instead of one endpoint with a flag.

    Only the syncable tables are touched. Local-only state — pipeline history, exclusions, merge
    decisions — is not in a snapshot and is left alone, which is correct: it describes this
    installation rather than the metadata.
    """
    tables = payload.tables
    unknown = sorted(set(tables) - set(_TABLE_MAP))
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"Snapshot contains unknown table(s): {', '.join(unknown)}"
        )

    restored: dict[str, dict[str, int]] = {}
    try:
        db.execute("BEGIN")
        # Deletions first, children before parents, so that a row about to be removed cannot
        # block the upsert of its replacement.
        for table in _RESTORE_DELETE_ORDER:
            if table in tables:
                restored.setdefault(table, {"restored": 0, "deleted": 0})
                restored[table]["deleted"] = _delete_rows_absent_from(db, table, tables[table])
        for table in _RESTORE_UPSERT_ORDER:
            if table in tables:
                restored.setdefault(table, {"restored": 0, "deleted": 0})
                restored[table]["restored"] = _restore_table(db, table, tables[table])
        db.commit()
    except sqlite3.IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Restore failed and nothing was changed: {exc}"
        ) from exc
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Restore failed: {exc}") from exc

    return {
        "detail": "Restore complete.",
        "exported_at": payload.exported_at,
        "exported_by": payload.exported_by,
        "tables": restored,
        "total_restored": sum(t["restored"] for t in restored.values()),
        "total_deleted": sum(t["deleted"] for t in restored.values()),
    }
