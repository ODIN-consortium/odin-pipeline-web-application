"""The workbook's replicate column, through the Excel import and back out through export.

A replicate group is the barcodes of one run that carry the same sample on the same date —
the set that will be pooled. The importer checks the group as a whole before writing any of
it, and each row's own cells where it handles the row.
"""

import io
import sqlite3

import openpyxl
from fastapi.testclient import TestClient

from backend.tests.test_export import (
    _NANOPORE_HEADER,
    _NANOPORE_ROW,
    _make_xlsx,
    _nanopore_sheet,
    _reasons,
    _upload,
)

# _NANOPORE_ROW has no value for the header's last column (minknow_sample_id), so a
# replicate value needs that gap padded or it lands in the wrong column.
_HEADER = [*_NANOPORE_HEADER, "replicate", "replicate_type"]


def _row(barcode: str, replicate=None, replicate_type=None) -> list:
    r = list(_NANOPORE_ROW)
    r[1] = barcode
    return [*r, None, replicate, replicate_type]


def _stored(db: sqlite3.Connection) -> dict[str, tuple]:
    rows = db.execute(
        "SELECT nr.barcode, nr.replicate, nr.replicate_type FROM nanopore_runs nr "
        "JOIN nanopore_run_accessions nra ON nra.id = nr.accession_id "
        "WHERE nra.run_accession = 'ERR777001' ORDER BY nr.barcode"
    ).fetchall()
    return {r["barcode"]: (r["replicate"], r["replicate_type"]) for r in rows}


# ── a well-formed group ───────────────────────────────────────────────────────


def test_a_numbered_group_is_imported_with_its_numbers(client: TestClient, db) -> None:
    xlsx = _make_xlsx(_nanopore_sheet(
        [_row("barcode01", 1), _row("barcode02", 2), _row("barcode03", 3)], _HEADER,
    ))

    r = _upload(client, xlsx, import_valid_rows=True)

    assert r.status_code == 200, r.text
    assert r.json()["summary"]["nanopore"]["created"] == 3
    assert _stored(db) == {
        "barcode01": (1, None), "barcode02": (2, None), "barcode03": (3, None),
    }


def test_excel_floats_are_whole_numbers(client: TestClient, db) -> None:
    # Excel hands integer cells over as floats; 2.0 is the number 2, not a problem.
    xlsx = _make_xlsx(_nanopore_sheet([_row("barcode01", 1.0), _row("barcode02", 2.0)], _HEADER))

    r = _upload(client, xlsx, import_valid_rows=True)

    assert r.status_code == 200, r.text
    assert _stored(db)["barcode02"] == (2, None)


def test_replicate_type_is_stored_lowercased(client: TestClient, db) -> None:
    xlsx = _make_xlsx(_nanopore_sheet(
        [_row("barcode01", 1, "Collection"), _row("barcode02", 2, "collection")], _HEADER,
    ))

    r = _upload(client, xlsx, import_valid_rows=True)

    assert r.status_code == 200, r.text
    assert _stored(db)["barcode01"] == (1, "collection")


# ── rows the importer refuses, each with its reason ───────────────────────────


def test_a_replicate_that_is_not_a_whole_number_is_skipped_with_its_row(client: TestClient, db) -> None:
    xlsx = _make_xlsx(_nanopore_sheet([_row("barcode01", "first")], _HEADER))

    r = _upload(client, xlsx, import_valid_rows=True)

    assert r.status_code == 200, r.text
    assert r.json()["summary"]["nanopore"]["skipped"] == 1
    problem = r.json()["problems"][0]
    assert "replicate 'first' is not a whole number" in problem["reason"]
    assert problem["sheet"] == "nanopore" and problem["row"] == 2
    assert _stored(db) == {}


def test_zero_and_negative_numbers_are_refused(client: TestClient) -> None:
    xlsx = _make_xlsx(_nanopore_sheet([_row("barcode01", 0), _row("barcode02", -1)], _HEADER))

    r = _upload(client, xlsx, import_valid_rows=True)

    assert r.json()["summary"]["nanopore"]["skipped"] == 2
    assert all("not a positive whole number" in reason for reason in _reasons(r))


def test_a_partly_numbered_group_is_skipped_whole(client: TestClient, db) -> None:
    # Writing the numbered half would leave the database claiming a group it does not have.
    xlsx = _make_xlsx(_nanopore_sheet([_row("barcode01", 1), _row("barcode02")], _HEADER))

    r = _upload(client, xlsx, import_valid_rows=True)

    assert r.json()["summary"]["nanopore"]["skipped"] == 2
    reasons = _reasons(r)
    assert len(reasons) == 2 and reasons[0] == reasons[1]
    assert "only partly numbered" in reasons[0]
    assert "barcode01=1, barcode02=blank" in reasons[0]
    assert _stored(db) == {}


def test_a_repeated_number_in_a_group_is_skipped_whole(client: TestClient, db) -> None:
    xlsx = _make_xlsx(_nanopore_sheet(
        [_row("barcode01", 1), _row("barcode02", 1), _row("barcode03", 2)], _HEADER,
    ))

    r = _upload(client, xlsx, import_valid_rows=True)

    assert r.json()["summary"]["nanopore"]["skipped"] == 3
    assert "repeats replicate number 1" in _reasons(r)[0]
    assert _stored(db) == {}


def test_an_unknown_replicate_type_is_skipped(client: TestClient) -> None:
    xlsx = _make_xlsx(_nanopore_sheet([_row("barcode01", 1, "biological")], _HEADER))

    r = _upload(client, xlsx, import_valid_rows=True)

    assert r.json()["summary"]["nanopore"]["skipped"] == 1
    assert "replicate_type 'biological' is not one of collection, extraction, technical" in _reasons(r)[0]


def test_a_type_without_a_number_is_skipped(client: TestClient) -> None:
    xlsx = _make_xlsx(_nanopore_sheet([_row("barcode01", None, "collection")], _HEADER))

    r = _upload(client, xlsx, import_valid_rows=True)

    assert r.json()["summary"]["nanopore"]["skipped"] == 1
    assert "given without a replicate number" in _reasons(r)[0]


def test_an_all_blank_group_is_not_a_problem(client: TestClient, db) -> None:
    # Two barcodes on one sample with no numbers is the state every pre-replicate workbook
    # is in. The importer does not judge it; the launch warning does.
    xlsx = _make_xlsx(_nanopore_sheet([_row("barcode01"), _row("barcode02")], _HEADER))

    r = _upload(client, xlsx, import_valid_rows=True)

    assert r.status_code == 200, r.text
    assert r.json()["summary"]["nanopore"]["skipped"] == 0
    assert _stored(db) == {"barcode01": (None, None), "barcode02": (None, None)}


# ── the column's presence, like every other column ────────────────────────────


def _import_once(client: TestClient, rows: list[list], header: list[str]) -> None:
    r = _upload(client, _make_xlsx(_nanopore_sheet(rows, header)), import_valid_rows=True)
    assert r.status_code == 200, r.text


def test_a_sheet_without_the_column_leaves_stored_numbers_alone(client: TestClient, db) -> None:
    _import_once(client, [_row("barcode01", 1), _row("barcode02", 2)], _HEADER)

    _import_once(client, [list(_NANOPORE_ROW), [*_NANOPORE_ROW[:1], "barcode02", *_NANOPORE_ROW[2:]]], _NANOPORE_HEADER)

    assert _stored(db) == {"barcode01": (1, None), "barcode02": (2, None)}


def test_a_present_but_blank_cell_clears_the_number(client: TestClient, db) -> None:
    # The same rule as `type` and `sample_code`: absent column means leave alone, a blank
    # cell in a present column means clear.
    _import_once(client, [_row("barcode01", 1), _row("barcode02", 2)], _HEADER)

    _import_once(client, [_row("barcode01"), _row("barcode02")], _HEADER)

    assert _stored(db) == {"barcode01": (None, None), "barcode02": (None, None)}


# ── round trip ────────────────────────────────────────────────────────────────


def test_export_carries_both_columns_so_an_exported_workbook_reimports(client: TestClient) -> None:
    _import_once(client, [_row("barcode01", 1, "extraction"), _row("barcode02", 2, "extraction")], _HEADER)

    exported = client.get("/api/export/excel")
    assert exported.status_code == 200
    ws = openpyxl.load_workbook(io.BytesIO(exported.content))["nanopore"]
    header = [c.value for c in ws[1]]
    rows = {r[header.index("barcode")]: r for r in ws.iter_rows(min_row=2, values_only=True)}

    assert "replicate" in header and "replicate_type" in header
    assert rows["barcode01"][header.index("replicate")] == 1
    assert rows["barcode01"][header.index("replicate_type")] == "extraction"
