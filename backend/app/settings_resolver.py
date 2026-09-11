"""Settings resolution — the canonical env-var → DB → computed-default lookup.

This lives outside ``api/`` on purpose. Every layer needs to resolve settings: the
routers, ``db/queries.py`` and ``pipeline/command_builder.py``. When this logic lived
in ``api/settings.py`` those lower layers had to import the HTTP layer, which is
circular (importing ``api.settings`` runs ``api/__init__``, which imports the routers,
which import ``db.queries`` / ``command_builder``) — so each of them worked around it
with a function-local import, one of them reaching for private names.

This module depends only on ``utils`` and the settings-definition seed file, so any
layer can import it at the top of the file.

The definitions themselves live in ``seed/settings_definitions.json``: each entry
names a key, its label, and how its default is computed (a path suffix under
ODIN_PIPELINE_ROOT, or the machine hostname).
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

from .utils import coerce_path, normalize_for_storage

_DEFS_FILE = Path(__file__).parent / "seed" / "settings_definitions.json"


def _load_definitions() -> list[dict[str, Any]]:
    if _DEFS_FILE.exists():
        return json.loads(_DEFS_FILE.read_text(encoding="utf-8"))
    return []


DEFINITIONS: list[dict[str, Any]] = _load_definitions()

# Every setting the application knows about: key -> human-readable label.
KNOWN_KEYS: dict[str, str] = {d["key"]: d["label"] for d in DEFINITIONS}

_ENV_VAR_KEYS: dict[str, str] = {
    # Must be mounted host:container identically in docker-compose.yml when it points
    # outside ODIN_PIPELINE_ROOT — samplesheets embed FASTQ paths under it, and the
    # sibling task containers bind those paths from the host (DooD).
    "ODIN_MINKNOW_DIR":           "minknow_dir",
    "ODIN_DATABASES_FILE":        "databases_file",
    "ODIN_PATHOGENS_FILE":        "pathogens_file",
    "ODIN_EXTRACT_TARGETS_FILE":  "extract_targets_file",
    "NEXTFLOW_CONFIG_FILE":       "nextflow_config_file",
    "NEXTFLOW_PROFILE":           "nextflow_profile",
    "ODIN_ENLIGHTEN_URL":         "enlighten_url",
    "ODIN_ENLIGHTEN_DATA_PATH":   "enlighten_data_path",
    # device_name is the only non-path key here, so note what that means:
    # get_env_overrides runs values through normalize_for_storage, which rewrites
    # Windows absolute paths and leaves everything else alone (bar trimming). A
    # device name is untouched unless someone literally names their machine "C:/lab".
    "ODIN_DEVICE_NAME":           "device_name",
}


def get_env_overrides() -> dict[str, str]:
    """Returns settings whose values are forced by environment variables.

    Env vars always win over DB values — the standard 12-factor priority.
    """
    result: dict[str, str] = {}
    for env_var, key in _ENV_VAR_KEYS.items():
        val = os.environ.get(env_var, "").strip()
        if val:
            result[key] = normalize_for_storage(val)
    return result


def get_defaults() -> dict[str, Optional[str]]:
    """Compute every setting's default value.

    Every default is derived from ODIN_PIPELINE_ROOT, so none of them exist until it
    is configured. Settings with neither ``default_suffix`` nor ``default_file_suffix``
    have no default at all — see ``get_device_name`` for a case where inventing one
    actively corrupts data.
    """
    defaults: dict[str, Optional[str]] = {}

    root = os.getenv("ODIN_PIPELINE_ROOT", "").strip()
    if root:
        # Use normalize_for_storage so the value is always in forward-slash form.
        # Avoid Path() here — on Windows it converts /mnt/d/... to \mnt\d\... (backslashes).
        root_norm = normalize_for_storage(root).rstrip("/")
        defaults.update({
            d["key"]: root_norm + d["default_suffix"]
            for d in DEFINITIONS
            if d.get("default_suffix")
        })
        defaults.update({
            d["key"]: root_norm + d["default_file_suffix"]
            for d in DEFINITIONS
            if d.get("default_file_suffix")
        })

    return defaults


def resolve_setting_value(db: sqlite3.Connection, key: str) -> Optional[str]:
    """Resolve a setting to its raw stored form: env var → DB row → computed default.

    No path coercion — callers apply the coercion their consumer needs (see
    `lookup_setting` for native paths and `command_builder._get` for shell paths).

    An empty stored value means "not configured" and falls back to the computed
    default, exactly like a NULL or a missing row. Writes normalise empty to NULL
    (`api.settings._upsert_config_value`), so a blank row only appears in databases
    written by older versions; treating it as unset keeps those consistent.

    Turning a feature off is therefore not expressed by clearing a path — it needs a
    value the consumer understands (e.g. `barcode_min_reads = 0`), so that the intent
    is explicit rather than inferred from an empty string.
    """
    env_overrides = get_env_overrides()
    if key in env_overrides:
        return env_overrides[key]

    row = db.execute("SELECT value FROM config_values WHERE key = ?", (key,)).fetchone()
    stored = row["value"] if row else None
    return stored or get_defaults().get(key)


def lookup_setting(db: sqlite3.Connection, key: str) -> Optional[str]:
    """Canonical setting lookup: env-var override → DB → computed default → coerce_path.

    Returns a path valid on the current platform, for use with `pathlib`. This is the
    single authoritative implementation — call it directly rather than wrapping it in
    a per-module helper or reading ``config_values`` by hand.
    """
    value = resolve_setting_value(db, key)
    return coerce_path(value) if value else None


def get_device_name(db: sqlite3.Connection) -> Optional[str]:
    """The name stamped into ``created_by``/``updated_by`` and sync export filenames.

    Lives here rather than in ``utils`` because it must resolve through the shared
    chain, and ``settings_resolver`` imports ``utils`` — the other direction would be
    circular. It is a settings lookup, so this is its layer.

    **No computed default, deliberately.** This value is written into permanent
    records and into the filenames other devices import, so a guessed name does
    lasting damage rather than merely being wrong once. The guess used to be
    ``socket.gethostname()``, which under Docker is the container ID: it changes every
    time the container is recreated, so a single machine's rows accumulate under a
    series of phantom device identities — precisely the thing sync uses this field to
    tell apart. Unset now means unset: rows carry NULL and the sync page's
    "set a device name" warning fires as it was designed to.

    Set ``ODIN_DEVICE_NAME`` in ``.env`` for a value that survives container
    recreation, or enter one in Settings.
    """
    return resolve_setting_value(db, "device_name")
