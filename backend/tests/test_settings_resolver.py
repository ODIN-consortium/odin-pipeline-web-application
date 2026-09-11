"""Tests for the shared settings-resolution chain.

`resolve_setting_value` is the single implementation of env var → DB → computed
default. Two adapters sit on top and differ only where they must:

  * `lookup_setting`        — native paths (for pathlib), blank value = "off"
  * `command_builder._get`  — shell paths (for Nextflow commands), blank = "unset"

Both were previously separate hand-rolled copies of the chain, so the blank-value
divergence was invisible. It is now a named parameter, and pinned here.
"""

import sqlite3
import uuid

import pytest

from backend.app.pipeline.command_builder import _get
from backend.app.settings_resolver import (
    get_defaults,
    lookup_setting,
    resolve_setting_value,
)

_NOW = "2025-01-01T00:00:00.000Z"

# A key with a path default derived from ODIN_PIPELINE_ROOT.
_PATH_KEY = "minknow_dir"
# A key with no computed default and no env-var override, for "nothing resolves" cases.
_BARE_KEY = "barcode_min_reads"
# A key whose value can be forced by an environment variable.
_ENV_KEY = "nextflow_profile"
_ENV_VAR = "NEXTFLOW_PROFILE"


def _set_config(db: sqlite3.Connection, key: str, value) -> None:
    db.execute(
        """INSERT OR REPLACE INTO config_values
           (id, key, value, created_at, updated_at, created_by)
           VALUES (?,?,?,?,?,?)""",
        (str(uuid.uuid4()), key, value, _NOW, _NOW, "test"),
    )
    db.commit()


@pytest.fixture(autouse=True)
def _no_ambient_env_override(monkeypatch: pytest.MonkeyPatch):
    """The repo .env may set NEXTFLOW_PROFILE — tests opt in explicitly instead."""
    monkeypatch.delenv(_ENV_VAR, raising=False)


# ── priority order ────────────────────────────────────────────────────────────


def test_env_var_wins_over_a_db_value(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_config(db, _ENV_KEY, "odin")
    monkeypatch.setenv(_ENV_VAR, "odin_big")

    assert resolve_setting_value(db, _ENV_KEY) == "odin_big"


def test_db_value_wins_over_the_computed_default(db: sqlite3.Connection) -> None:
    _set_config(db, _PATH_KEY, "/d/somewhere_else/minknow")

    assert resolve_setting_value(db, _PATH_KEY) == "/d/somewhere_else/minknow"


def test_missing_row_uses_the_computed_default(db: sqlite3.Connection) -> None:
    assert resolve_setting_value(db, _PATH_KEY) == get_defaults()[_PATH_KEY]


def test_nothing_resolves_returns_none(db: sqlite3.Connection) -> None:
    """No row, no env var, no computed default for this key."""
    assert resolve_setting_value(db, _BARE_KEY) is None


# ── unset, NULL and blank all mean "use the default" ──────────────────────────


@pytest.mark.parametrize("stored", [None, ""])
def test_null_and_blank_both_use_the_computed_default(
    db: sqlite3.Connection, stored
) -> None:
    """Clearing a setting and never setting it are the same request.

    Blank rows only occur in databases written before writes normalised empty to
    NULL; both must resolve identically so old and new databases behave the same.
    """
    _set_config(db, _PATH_KEY, stored)

    assert resolve_setting_value(db, _PATH_KEY) == get_defaults()[_PATH_KEY]


def test_clearing_a_setting_stores_null_not_an_empty_string(
    client, db: sqlite3.Connection
) -> None:
    """The write side of the same rule, via the API."""
    client.put(f"/api/settings/{_PATH_KEY}", json={"value": "/d/somewhere/minknow"})

    client.put(f"/api/settings/{_PATH_KEY}", json={"value": ""})

    row = db.execute(
        "SELECT value FROM config_values WHERE key = ?", (_PATH_KEY,)
    ).fetchone()
    assert row["value"] is None
    assert resolve_setting_value(db, _PATH_KEY) == get_defaults()[_PATH_KEY]


# ── the two adapters ──────────────────────────────────────────────────────────


def test_both_adapters_read_the_same_configured_value(db: sqlite3.Connection) -> None:
    """Same source, same path — only the coercion target differs by platform."""
    _set_config(db, _PATH_KEY, "/d/odin_test/mydata")

    assert lookup_setting(db, _PATH_KEY).endswith("odin_test/mydata")
    assert _get(db, _PATH_KEY).endswith("odin_test/mydata")


def test_both_adapters_treat_a_blank_setting_as_unset(db: sqlite3.Connection) -> None:
    """Blank means "use the default" on both sides — they no longer disagree.

    Previously lookup_setting read a blank value as "feature off" (None) while command
    building fell back to the computed default, so the same stored value meant two
    different things depending on which code path read it.
    """
    _set_config(db, _PATH_KEY, "")

    assert lookup_setting(db, _PATH_KEY).endswith("minknow")
    assert _get(db, _PATH_KEY).endswith("/minknow")


def test_get_returns_the_caller_default_when_nothing_resolves(
    db: sqlite3.Connection,
) -> None:
    assert _get(db, _BARE_KEY, "fallback") == "fallback"
    assert _get(db, _BARE_KEY) == ""


def test_lookup_setting_returns_none_when_nothing_resolves(
    db: sqlite3.Connection,
) -> None:
    assert lookup_setting(db, _BARE_KEY) is None


def test_get_applies_env_overrides_too(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Command building honours env vars — it is the same chain, not a parallel one."""
    monkeypatch.setenv(_ENV_VAR, "odin_epi2me")

    assert _get(db, _ENV_KEY) == "odin_epi2me"


def test_minknow_dir_env_var_wins_over_db_and_default(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # minknow_dir was the one scanned-directory setting without an env var, so it could
    # only be pointed elsewhere through the Settings page — where nothing reminds the
    # operator that the directory must also be mounted identically for DooD. The env var
    # lives in .env next to the compose mount that makes it usable.
    monkeypatch.setenv("ODIN_MINKNOW_DIR", "/mnt/d/sequencing/minknow")
    _set_config(db, "minknow_dir", "/somewhere/else")

    assert resolve_setting_value(db, "minknow_dir") == "/mnt/d/sequencing/minknow"
