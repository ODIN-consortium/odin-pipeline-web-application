"""pipeline.replicates — one grouping rule for the dashboard, the launch and the inputs.

Every consumer that has to say what the barcodes of one sample within a run are — a
group to pool, a metadata error, or a half-flagged group — asks this module, so the
tests here pin the rule itself. The consumers' tests (test_replicate_pooling.py) only
check that each of them asks.
"""

import math

import pytest

from backend.app.pipeline.replicates import (
    POOLED,
    plan_inputs,
    pooled_metadata_rows,
    replicate_groups,
    replicate_label,
)

RUN = "20260601_1200_MN00000_FBD00001_abcdef12"
RERUN = "20260602_0900_MN00000_FBD00001_abcdef34"


def _row(barcode, replicate=None, *, run=RUN, sample="AAAVWP_DW", date="20250916",
         protocol="P2", kit="RB_GEN", replicate_type=None):
    return {
        "run_accession": run,
        "barcode": barcode,
        "replicate": replicate,
        "replicate_type": replicate_type,
        "sample_code": sample,
        "sampling_date": date,
        "protocol_id": protocol,
        "sequencing_kit_id": kit,
    }


# ── grouping ──────────────────────────────────────────────────────────────────


def test_a_fully_numbered_group_is_pooled_in_replicate_order():
    rows = [_row("barcode01", 3), _row("barcode07", 1), _row("barcode08", 2)]

    (group,) = replicate_groups(rows)

    assert group.numbered
    assert group.problem is None
    assert group.barcodes == ["barcode07", "barcode08", "barcode01"]
    assert group.pooled_name == "barcode07-barcode08-barcode01"
    assert "_" not in group.pooled_name  # the Kraken2 report key is the text before the first '_'


def test_two_barcodes_without_numbers_are_a_metadata_error_not_a_pool():
    (group,) = replicate_groups([_row("barcode08"), _row("barcode12")])

    assert not group.numbered
    assert group.problem == "unnumbered"
    text = group.describe_problem()
    assert "barcode08, barcode12" in text
    assert "replicate number" in text
    assert "AAAVWP_DW (20250916)" in text


def test_a_half_numbered_group_cannot_be_pooled():
    (group,) = replicate_groups([_row("barcode01", 1), _row("barcode02")])

    assert group.problem == "partial"
    assert "barcode01 but not on barcode02" in group.describe_problem()


def test_a_repeated_number_cannot_be_pooled():
    (group,) = replicate_groups([_row("barcode01", 1), _row("barcode02", 1), _row("barcode03", 2)])

    assert group.problem == "duplicate"
    assert "replicate 1 on barcode01, barcode02" in group.describe_problem()


def test_a_single_barcode_is_never_a_problem_even_when_numbered():
    """Its partner may have been excluded as noise; a group of one is just a barcode."""
    (group,) = replicate_groups([_row("barcode01", 1)])

    assert group.problem is None
    assert not group.numbered


def test_different_samples_protocols_and_kits_are_different_groups():
    rows = [
        _row("barcode01", sample="A"),
        _row("barcode02", sample="B"),
        _row("barcode03", sample="A", protocol="P5"),
        _row("barcode04", sample="A", date="20250917"),
    ]
    groups = replicate_groups(rows)

    assert len(groups) == 4
    assert all(g.problem is None for g in groups)


def test_rows_without_a_sample_or_date_are_left_out():
    rows = [_row("barcode01", sample=None), _row("barcode02", date=None), _row("", 1)]

    assert replicate_groups(rows) == []


def test_groups_are_per_accession_by_default():
    """The same sample on one barcode in each of two accessions is two groups of one."""
    groups = replicate_groups([_row("barcode01"), _row("barcode01", run=RERUN)])

    assert len(groups) == 2
    assert all(len(g.members) == 1 for g in groups)


def test_across_a_merged_run_set_a_barcode_is_one_member():
    """A continuation repeats every barcode; the group must not count it twice."""
    rows = [
        _row("barcode01", 1), _row("barcode02", 2),
        _row("barcode01", 1, run=RERUN), _row("barcode02", 2, run=RERUN),
    ]
    (group,) = replicate_groups(rows, by_run=False)

    assert group.run_accession is None
    assert group.barcodes == ["barcode01", "barcode02"]
    assert group.numbered


def test_a_barcode_numbered_differently_across_accessions_is_inconsistent():
    rows = [_row("barcode01", 1), _row("barcode02", 2), _row("barcode01", 2, run=RERUN), _row("barcode02", 1, run=RERUN)]

    (group,) = replicate_groups(rows, by_run=False)

    assert group.problem == "inconsistent"
    assert group.inconsistent == ("barcode01", "barcode02")
    assert "runs being merged" in group.describe_problem()


def test_the_groups_types_are_joined_when_they_differ():
    rows = [
        _row("barcode01", 1, replicate_type="collection"),
        _row("barcode02", 2, replicate_type="collection"),
        _row("barcode03", 3, replicate_type="technical"),
    ]
    (group,) = replicate_groups(rows)

    assert group.replicate_type == "collection;technical"


def test_the_pooling_note_names_the_sample_and_the_barcodes():
    (group,) = replicate_groups([_row("barcode01", 1), _row("barcode02", 2)])

    assert group.describe_pooling() == (
        "AAAVWP_DW (20250916): replicates barcode01, barcode02 will be pooled before classification."
    )


# ── inputs ────────────────────────────────────────────────────────────────────


def test_plan_inputs_pools_numbered_groups_and_passes_the_rest_through():
    rows = [_row("barcode01", 1), _row("barcode02", 2), _row("barcode03", sample="B")]

    inputs = plan_inputs(replicate_groups(rows), pool=True)

    assert inputs == {"barcode01-barcode02": ["barcode01", "barcode02"], "barcode03": ["barcode03"]}


def test_plan_inputs_without_pooling_is_one_input_per_barcode():
    rows = [_row("barcode01", 1), _row("barcode02", 2)]

    inputs = plan_inputs(replicate_groups(rows), pool=False)

    assert inputs == {"barcode01": ["barcode01"], "barcode02": ["barcode02"]}


def test_plan_inputs_refuses_a_group_with_a_problem_whether_or_not_pooling():
    groups = replicate_groups([_row("barcode01"), _row("barcode02")])

    for pool in (True, False):
        with pytest.raises(ValueError, match="cannot be processed.*barcode01, barcode02"):
            plan_inputs(groups, pool=pool)


# ── metadata rows for the post-processors ─────────────────────────────────────


def _meta(barcode, replicate=None, *, run=RUN, sample="AAAVWP_DW", replicate_type=""):
    return {
        **_row(barcode, replicate, run=run, sample=sample, replicate_type=replicate_type),
        "alias": f"{sample}_{barcode}",
        "sample_id": "AAAVWP_DW_P2_RB_GEN",
        "country": "Tanzania",
    }


def test_pooled_metadata_rows_replace_a_group_with_one_pooled_row():
    rows = [_meta("barcode01", 2), _meta("barcode02", 1), _meta("barcode03", sample="AAAVRV_RW")]

    out = pooled_metadata_rows(rows)

    assert [r["barcode"] for r in out] == ["barcode02-barcode01", "barcode03"]
    pooled = out[0]
    assert pooled["replicate"] == POOLED
    assert pooled["alias"] == "AAAVWP_DW_barcode02-barcode01"
    assert pooled["sample_id"] == "AAAVWP_DW_P2_RB_GEN"
    assert pooled["country"] == "Tanzania"
    assert out[1]["replicate"] is None


def test_pooled_metadata_rows_keep_a_lone_numbered_barcode_as_itself():
    out = pooled_metadata_rows([_meta("barcode01", 1)])

    assert out == [_meta("barcode01", 1)]


def test_pooled_metadata_rows_pool_each_accession_of_a_merged_run_separately():
    """The Kraken2 step de-duplicates across accessions afterwards, as it does per barcode."""
    rows = [_meta("barcode01", 1), _meta("barcode02", 2), _meta("barcode01", 1, run=RERUN), _meta("barcode02", 2, run=RERUN)]

    out = pooled_metadata_rows(rows)

    assert [(r["run_accession"], r["barcode"]) for r in out] == [
        (RUN, "barcode01-barcode02"),
        (RERUN, "barcode01-barcode02"),
    ]


def test_pooled_metadata_rows_carry_the_groups_types():
    rows = [_meta("barcode01", 1, replicate_type="extraction"), _meta("barcode02", 2, replicate_type="extraction")]

    (pooled,) = pooled_metadata_rows(rows)

    assert pooled["replicate_type"] == "extraction"


# ── the dataset's text form ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value, label",
    [(None, ""), ("", ""), (2, "2"), (2.0, "2"), (math.nan, ""), (POOLED, POOLED)],
)
def test_replicate_label(value, label):
    assert replicate_label(value) == label
