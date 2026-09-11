"""Unit tests for Pydantic schemas — validators run without a DB or HTTP client."""

import pytest
from pydantic import ValidationError

from backend.app.schemas import (
    NanoporeRunCreate,
    SampleCreate,
    SiteCreate,
    SiteRead,
    SiteUpdate,
)

# ── SampleCreate.sampling_date validator ─────────────────────────────────────


@pytest.mark.parametrize(
    "valid_date",
    ["20240101", "20000101", "20991231"],
)
def test_sampling_date_valid(valid_date: str) -> None:
    obj = SampleCreate(sampling_date=valid_date)
    assert obj.sampling_date == valid_date


@pytest.mark.parametrize(
    "bad_date",
    ["2024-01-01", "240101", "20240101T00:00:00", "notadate", "", "2024010"],
)
def test_sampling_date_invalid(bad_date: str) -> None:
    with pytest.raises(ValidationError):
        SampleCreate(sampling_date=bad_date)


# ── SiteCreate — derived-only fields are absent ───────────────────────────────


def test_site_create_has_no_site_code_field() -> None:
    """SiteCreate must not expose site_code — it is server-derived."""
    assert not hasattr(SiteCreate, "site_code") or "site_code" not in SiteCreate.model_fields


def test_site_update_has_no_site_code_field() -> None:
    assert "site_code" not in SiteUpdate.model_fields


def test_sample_create_has_no_sample_code_field() -> None:
    from backend.app.schemas import SampleCreate, SampleUpdate

    assert "sample_code" not in SampleCreate.model_fields
    assert "sample_code" not in SampleUpdate.model_fields


def test_nanopore_run_create_has_no_derived_fields() -> None:
    from backend.app.schemas import NanoporeRunCreate, NanoporeRunUpdate

    for field in ("minknow_sample_id", "alias"):
        assert field not in NanoporeRunCreate.model_fields
        assert field not in NanoporeRunUpdate.model_fields


# ── SiteRead — derived fields ARE present ────────────────────────────────────


def test_site_read_has_site_code() -> None:
    assert "site_code" in SiteRead.model_fields


def test_site_read_construction() -> None:
    obj = SiteRead(
        id="uuid-1",
        site_code="NOBGOPark",
        country="Norway",
        created_at="2024-01-01T00:00:00.000Z",
        updated_at="2024-01-01T00:00:00.000Z",
    )
    assert obj.site_code == "NOBGOPark"
    assert obj.country == "Norway"


# ── NanoporeRunCreate — optional sample_id ───────────────────────────────────


def test_nanopore_run_create_minimal() -> None:
    obj = NanoporeRunCreate(run_accession="ERR1", barcode="BC01")
    assert obj.run_accession == "ERR1"
    assert obj.sample_id is None


def test_site_create_country_required() -> None:
    with pytest.raises(ValidationError):
        SiteCreate()  # country is required
