"""Regression tests for utils.resolve_sample_id (sample_code -> sample UUID).

Consolidated from four divergent copies in the 2026-07-24 maintainability
review. The reference lookup (pipeline_scripts) prefers an exact sampling_date
match and is otherwise date-centric; the no-date fallback must therefore pick
the newest sample by *sampling_date* (never by updated_at).
"""

import sqlite3

from backend.app.utils import resolve_sample_id

_SITE_ID = "site-for-resolve-tests"


def _insert_sample(
    db: sqlite3.Connection,
    sample_id: str,
    sample_code: str,
    sampling_date: str,
    updated_at: str = "2024-01-01T00:00:00Z",
) -> None:
    """Insert a sample directly, with the columns the schema requires.

    site_id and sample_type are NOT NULL (sample_code is derived from them and is half of
    the sync merge key), so these fixtures create a parent site once and reference it. The
    values are irrelevant to what these tests check — the lookup is by sample_code and
    sampling_date — but the row has to be a legal one.
    """
    db.execute(
        "INSERT OR IGNORE INTO sites (id, site_code, country, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (_SITE_ID, "NOBGN", "Norway", "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"),
    )
    db.execute(
        "INSERT INTO samples "
        "(id, sample_code, site_id, sample_type, sampling_date, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            sample_id,
            sample_code,
            _SITE_ID,
            "WW",
            sampling_date,
            "2024-01-01T00:00:00Z",
            updated_at,
        ),
    )


def test_exact_date_match_wins(db: sqlite3.Connection) -> None:
    _insert_sample(db, "s1", "NOBGN_WW", "20240101")
    _insert_sample(db, "s2", "NOBGN_WW", "20240601")
    assert resolve_sample_id(db, "NOBGN_WW", "20240101") == "s1"
    assert resolve_sample_id(db, "NOBGN_WW", "20240601") == "s2"


def test_specified_date_is_not_second_guessed(db: sqlite3.Connection) -> None:
    _insert_sample(db, "s1", "NOBGN_WW", "20240101")
    # A date was given but does not match -> None (never falls back when a date
    # is specified).
    assert resolve_sample_id(db, "NOBGN_WW", "20240202") is None


def test_no_date_falls_back_to_newest_sampling_date_not_updated_at(
    db: sqlite3.Connection,
) -> None:
    # The OLDER-sampling_date row is given the NEWER updated_at, so the two
    # ordering rules diverge: sampling_date DESC -> "new"; the old updated_at
    # DESC rule would have wrongly returned "old". No date -> fallback path.
    _insert_sample(db, "old", "NOBGN_WW", "20240101", updated_at="2024-12-31T00:00:00Z")
    _insert_sample(db, "new", "NOBGN_WW", "20240601", updated_at="2024-06-01T00:00:00Z")
    assert resolve_sample_id(db, "NOBGN_WW", None) == "new"


def test_unknown_or_missing_inputs_return_none(db: sqlite3.Connection) -> None:
    assert resolve_sample_id(db, "MISSING", None) is None
    assert resolve_sample_id(db, None, "20240101") is None
