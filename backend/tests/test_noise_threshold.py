"""Tests for the barcode noise threshold (barcode_min_reads).

The default is 0 = warnings disabled. It used to be a hidden 1000: the settings
field renders empty when unset, so an operator had no way to see that a threshold
was being applied, and no way to tell "unset" from "deliberately 0". A threshold is
a site-specific judgement, so an unset setting now means no warnings.
"""

import sqlite3
import uuid

import pytest

from backend.app.api.discovery import _noise_threshold

_NOW = "2025-01-01T00:00:00.000Z"


def _set_config(db: sqlite3.Connection, value) -> None:
    db.execute(
        """INSERT OR REPLACE INTO config_values
           (id, key, value, created_at, updated_at, created_by)
           VALUES (?,?,?,?,?,?)""",
        (str(uuid.uuid4()), "barcode_min_reads", value, _NOW, _NOW, "test"),
    )
    db.commit()


def test_unset_setting_disables_the_warnings(db: sqlite3.Connection) -> None:
    assert _noise_threshold(db) == 0


def test_configured_threshold_is_used(db: sqlite3.Connection) -> None:
    _set_config(db, "500")

    assert _noise_threshold(db) == 500


def test_explicit_zero_disables_the_warnings(db: sqlite3.Connection) -> None:
    _set_config(db, "0")

    assert _noise_threshold(db) == 0


@pytest.mark.parametrize("value", ["", None])
def test_blank_setting_disables_the_warnings(db: sqlite3.Connection, value) -> None:
    """Clearing the field must mean "no threshold", not a hidden built-in one."""
    _set_config(db, value)

    assert _noise_threshold(db) == 0


def test_non_numeric_setting_is_ignored_rather_than_crashing(
    db: sqlite3.Connection,
) -> None:
    """A bad value must not break the run-info endpoint for the whole run."""
    _set_config(db, "not-a-number")

    assert _noise_threshold(db) == 0
