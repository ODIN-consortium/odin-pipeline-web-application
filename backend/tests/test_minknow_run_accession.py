"""extract_from_run_accession reads hyphenated instrument names and flow cell IDs of any
shape. A run whose flow cell is unknown is never merged with another loading of its flow cell.
"""

import logging

import pytest

from backend.app.db.queries import same_flow_cell
from backend.app.parsers.minknow_run_info import extract_from_run_accession


@pytest.mark.parametrize(
    "run_accession, flow_cell, started",
    [
        ("20250905_1128_MD-000000_FBD00001_1a2b3c4d", "FBD00001", "2025-09-05T11:28:00"),
        ("20250906_1521_MD-000000_FBD00002_9c0d1e2f", "FBD00002", "2025-09-06T15:21:00"),
        ("20250908_1502_MD-000000_FBD00001_5e6f7a8b", "FBD00001", "2025-09-08T15:02:00"),
        ("20250910_1426_MD-000000_FBD00003_3a4b5c6d", "FBD00003", "2025-09-10T14:26:00"),
        ("20250912_1649_MC-000000_ABC001_7e8f9a0b", "ABC001", "2025-09-12T16:49:00"),
        ("20250916_1556_MC-000000_FBD00004_1c2d3e4f", "FBD00004", "2025-09-16T15:56:00"),
        ("20250912_0843_MN00000_FBD00005_44c4f359", "FBD00005", "2025-09-12T08:43:00"),
    ],
)
def test_reads_flow_cell_and_start(run_accession: str, flow_cell: str, started: str) -> None:
    assert extract_from_run_accession(run_accession) == (flow_cell, started)


def test_non_minknow_name_is_warned(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert extract_from_run_accession("RUN001") == (None, None)
    assert "RUN001" in caplog.text


def test_hyphenated_instrument_loadings_are_one_flow_cell() -> None:
    """Without a disk-scan entry, two loadings of one flow cell are matched from their names."""
    assert same_flow_cell(
        "20250905_1128_MD-000000_FBD00001_1a2b3c4d",
        "20250908_1502_MD-000000_FBD00001_5e6f7a8b",
        {},
    )
    assert not same_flow_cell(
        "20250905_1128_MD-000000_FBD00001_1a2b3c4d",
        "20250906_1521_MD-000000_FBD00002_9c0d1e2f",
        {},
    )
