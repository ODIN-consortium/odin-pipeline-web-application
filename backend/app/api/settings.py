"""Settings HTTP endpoints.

The resolution logic itself (env var → DB → computed default) lives in
``app.settings_resolver``, which every layer can import; this module owns the
endpoints and their presentation concerns only.
"""

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from ..database import get_db
from ..schemas import ConfigValueRead, ConfigValueUpdate
from ..settings_resolver import (
    DEFINITIONS,
    KNOWN_KEYS,
    get_defaults,
    get_env_overrides,
    lookup_setting,
)
from ..utils import coerce_path, normalize_for_storage
from ..utils import utc_now_str as _now

router = APIRouter(prefix="/settings", tags=["settings"])

# lookup_setting is re-exported for the routers in this package that already import
# it from here; code outside api/ imports it from app.settings_resolver directly.
__all__ = ["KNOWN_KEYS", "lookup_setting", "router"]


def _load_db_rows(
    db: sqlite3.Connection, *, only_non_empty: bool = False
) -> dict[str, sqlite3.Row]:
    """Return config_values rows keyed by setting key.

    The health check passes only_non_empty=True: for reporting purposes a row with
    an empty value is the same as no row, so the computed default applies.
    """
    return {
        r["key"]: r
        for r in db.execute("SELECT * FROM config_values").fetchall()
        if r["value"] or not only_non_empty
    }


@dataclass(frozen=True)
class ResolvedSetting:
    """A setting's effective value and where it came from."""

    value: Optional[str]
    source: str  # "env" | "db" | "default"
    updated_at: Optional[str] = None  # the DB row's timestamp; None for env/default

    @property
    def api_source(self) -> Optional[str]:
        """The source label the API exposes.

        "env" renders read-only in the UI; "default" tells the UI the value is a
        computed default rather than anything stored, so it can render it as a
        placeholder instead of as field content (a default displayed as content
        is indistinguishable from a configured value). A stored row gets None.
        """
        return self.source if self.source in ("env", "default") else None


def _resolve_setting(
    key: str,
    env_overrides: dict[str, str],
    db_rows: dict[str, sqlite3.Row],
    defaults: dict[str, Optional[str]],
) -> ResolvedSetting:
    """Resolve one setting through the canonical env-var → DB → default priority.

    The endpoint-level counterpart to `settings_resolver.lookup_setting`, which applies
    the same priority to one key at a time (with path coercion) for every other layer.
    """
    if key in env_overrides:
        return ResolvedSetting(env_overrides[key], "env")
    row = db_rows.get(key)
    if row is not None:
        return ResolvedSetting(row["value"], "db", row["updated_at"])
    return ResolvedSetting(defaults.get(key), "default")


@router.get("/health")
def get_settings_health(db: sqlite3.Connection = Depends(get_db)):
    """
    Check configuration health.  Returns a list of issues for settings that are
    either required, warn_if_missing, or have a ``default_suffix`` / ``default_file_suffix``:

    - Entries with ``default_suffix`` (directory paths):
        Tries to create the directory if it does not exist.
        Reports ``"create_failed"`` only if creation fails.

    - Entries with ``default_file_suffix`` (file paths with auto-computed defaults):
        Reports ``"not_found"`` if the resolved path does not exist.

    - Entries with ``required: true`` but no suffix (file paths):
        Reports ``"missing"`` if no value is set.
        Reports ``"not_found"`` if the value is set but the path does not exist.

    - Entries with ``warn_if_missing: true`` (optional but recommended):
        Reports ``"warning"`` if not set or the path does not exist.
    """
    db_rows = _load_db_rows(db, only_non_empty=True)
    env_overrides = get_env_overrides()
    defaults = get_defaults()

    issues = []
    for d in DEFINITIONS:
        has_default_suffix = bool(d.get("default_suffix"))
        has_default_file_suffix = bool(d.get("default_file_suffix"))
        is_required = bool(d.get("required"))
        warn_if_missing = bool(d.get("warn_if_missing"))
        if not has_default_suffix and not has_default_file_suffix and not is_required and not warn_if_missing:
            continue

        key = d["key"]
        resolved = _resolve_setting(key, env_overrides, db_rows, defaults)
        value: Optional[str] = resolved.value

        if not value:
            issue_level = "missing" if is_required else "warning"
            issues.append({"key": key, "label": d["label"], "issue": issue_level, "value": None})
            continue

        # Only path settings have anything to verify on disk. A required setting that is
        # not a path — `device_name` is the first — is fully satisfied by having a value,
        # and running the file checks on it reports nonsense ("File not found on disk:
        # BGO-2009"). Every entry that reached the checks below used to have a
        # default_suffix or default_file_suffix, so being a path was an accidental
        # invariant of the guard above rather than something this loop actually tested.
        if not has_default_suffix and not has_default_file_suffix:
            continue

        native = coerce_path(value)
        if has_default_suffix:
            # Directory — try to create it automatically
            if not os.path.exists(native):
                try:
                    os.makedirs(native, exist_ok=True)
                except OSError:
                    issues.append(
                        {"key": key, "label": d["label"], "issue": "create_failed", "value": value}
                    )
        else:
            # File path — just check existence, cannot auto-create
            if not os.path.exists(native):
                if resolved.source == "default" and not warn_if_missing and not is_required:
                    # An optional file whose only "value" is the computed
                    # conventional location (databases.csv, extract_targets.csv):
                    # other sources cover its role, so absence is a normal state
                    # rather than an issue. A path someone explicitly configured
                    # (env or Settings) that is missing is still reported.
                    continue
                issue_level = "warning" if warn_if_missing and not is_required else "not_found"
                issues.append(
                    {"key": key, "label": d["label"], "issue": issue_level, "value": value}
                )

    return {"issues": issues}


@router.get("/definitions", response_model=list[dict[str, Any]])
def get_definitions():
    return DEFINITIONS


@router.get("", response_model=list[ConfigValueRead])
def get_all_settings(db: sqlite3.Connection = Depends(get_db)):
    # only_non_empty: a cleared row means "use the default" (writes normalise
    # empty to NULL), so it must resolve — and render — exactly like never-set.
    rows = _load_db_rows(db, only_non_empty=True)
    env_overrides = get_env_overrides()
    defaults = get_defaults()
    result = []
    now = _now()
    for key in KNOWN_KEYS:
        # An env-var override wins and is flagged so the UI can show it read-only.
        resolved = _resolve_setting(key, env_overrides, rows, defaults)
        result.append(
            ConfigValueRead(
                key=key,
                value=resolved.value,
                updated_at=resolved.updated_at or now,
                source=resolved.api_source,
            )
        )
    for key, row in rows.items():
        if key not in KNOWN_KEYS:
            result.append(
                ConfigValueRead(key=row["key"], value=row["value"], updated_at=row["updated_at"])
            )
    return result


@router.get("/file/export")
def export_settings_file(db: sqlite3.Connection = Depends(get_db)):
    rows = _load_db_rows(db)
    env_overrides = get_env_overrides()
    defaults = get_defaults()

    settings_payload: list[dict[str, Any]] = []
    for key in KNOWN_KEYS:
        resolved = _resolve_setting(key, env_overrides, rows, defaults)
        settings_payload.append(
            {"key": key, "value": resolved.value, "source": resolved.source}
        )

    for key, row in rows.items():
        if key not in KNOWN_KEYS:
            settings_payload.append({"key": key, "value": row["value"], "source": "db"})

    payload = {
        "format": "odin-settings-v1",
        "exported_at": _now(),
        "settings": settings_payload,
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return StreamingResponse(
        BytesIO(body),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=odin_settings.json"},
    )


@router.post("/file/import")
async def import_settings_file(
    file: UploadFile = File(...),
    db: sqlite3.Connection = Depends(get_db),
):
    filename = (file.filename or "").lower()
    if filename and not filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Please upload a .json settings file")

    _MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
    try:
        raw = await file.read(_MAX_UPLOAD_BYTES + 1)
        if len(raw) > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File too large (max 10 MB)")
        data = json.loads(raw.decode("utf-8"))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Uploaded file is not valid JSON")

    if isinstance(data, dict) and "settings" in data and isinstance(data["settings"], list):
        items = data["settings"]
    elif isinstance(data, dict):
        # Backward-compatible plain object format: {"key": "value"}
        items = [{"key": k, "value": v} for k, v in data.items()]
    else:
        raise HTTPException(status_code=400, detail="Unsupported settings file structure")

    env_overrides = get_env_overrides()
    now = _now()
    imported = 0
    skipped_env = 0
    skipped_invalid = 0
    skipped_unknown = 0

    for item in items:
        if not isinstance(item, dict):
            skipped_invalid += 1
            continue
        key = item.get("key")
        if not isinstance(key, str) or not key.strip():
            skipped_invalid += 1
            continue
        key = key.strip()

        if key not in KNOWN_KEYS:
            skipped_unknown += 1
            continue

        if key in env_overrides:
            skipped_env += 1
            continue

        value_in = item.get("value")
        if value_in is None:
            value = None
        elif isinstance(value_in, (str, int, float, bool)):
            value = normalize_for_storage(str(value_in))
        else:
            skipped_invalid += 1
            continue

        _upsert_config_value(db, key, value, now)
        imported += 1

    db.commit()
    return {
        "detail": "Settings import completed",
        "summary": {
            "imported": imported,
            "skipped_env": skipped_env,
            "skipped_invalid": skipped_invalid,
            "skipped_unknown": skipped_unknown,
        },
    }


def _upsert_config_value(db: sqlite3.Connection, key: str, value: Optional[str], now: str) -> None:
    """Insert or update a single ``config_values`` row, keyed by ``key``.

    An empty value is stored as NULL: clearing a setting in the UI and never having
    set it are the same request — "use the default" — and keeping them as one state
    in the data means no consumer has to invent a policy for the empty string.
    """
    if not value:
        value = None
    row = db.execute("SELECT id FROM config_values WHERE key = ?", (key,)).fetchone()
    if row:
        db.execute(
            "UPDATE config_values SET value=?, updated_at=? WHERE key=?",
            (value, now, key),
        )
    else:
        db.execute(
            "INSERT INTO config_values (id, key, value, updated_at) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), key, value, now),
        )


@router.get("/{key}", response_model=ConfigValueRead)
def get_setting(key: str, db: sqlite3.Connection = Depends(get_db)):
    # Unknown keys 404 immediately — unknown key is a caller bug, not an unset setting.
    if key not in KNOWN_KEYS:
        row = db.execute("SELECT * FROM config_values WHERE key = ?", (key,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")
        return ConfigValueRead(key=row["key"], value=row["value"], updated_at=row["updated_at"])

    # Apply the same env-var → DB → computed-default priority as get_all_settings().
    resolved = _resolve_setting(
        key, get_env_overrides(), _load_db_rows(db, only_non_empty=True), get_defaults()
    )
    return ConfigValueRead(
        key=key,
        value=resolved.value,
        updated_at=resolved.updated_at or _now(),
        source=resolved.api_source,
    )


@router.put("/{key}", response_model=ConfigValueRead)
def upsert_setting(key: str, body: ConfigValueUpdate, db: sqlite3.Connection = Depends(get_db)):
    now = _now()
    value = normalize_for_storage(body.value) if body.value is not None else None
    _upsert_config_value(db, key, value, now)
    db.commit()
    row = db.execute(
        "SELECT * FROM config_values WHERE key = ?", (key,)
    ).fetchone()
    return ConfigValueRead(key=row["key"], value=row["value"], updated_at=row["updated_at"])
