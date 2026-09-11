"""
Tests for pipeline/scripts/decode.py — grouping of Biomeme wells into samples.

A sample's ``Biomeme_sample_ID`` in the metadata is the comma-separated list of
well labels that belong to it in ONE run's Excel file. Labels (1..8, NTC..) are
reused in every run, so the lookup must be built per run. These tests build small
Biomeme-layout workbooks and call the real ``extract_edna``.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from openpyxl import Workbook

from backend.app.pipeline.scripts.decode import extract_edna

TARGET = "Khe"
FLUOR = "red"
ASSAY_PARAMS_CSV = "Pathogen,Target_ID,M,B,E,T\nKlebsiella pneumoniae,Khe,-3.22,36.66,1.05,180\n"


@pytest.fixture(autouse=True)
def assay_params(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "assay-params.csv"
    path.write_text(ASSAY_PARAMS_CSV, encoding="utf-8")
    monkeypatch.setenv("ODIN_ASSAY_PARAMS_PATH", str(path))


def write_run(path: Path, run_name: str, wells: list[tuple[int, str, float]]) -> str:
    """Write a one-sheet workbook in the Biomeme Cq-export layout decode.py reads.

    wells: (well_number, label, cq). Row 7 = wells, 8 = fluorophore, 10 = sample
    label, 11 = target, 12 = Cq; A12 == 'Cq' marks a Cq sheet.
    """
    wb = Workbook()
    ws = wb.active
    ws["B1"] = run_name
    ws["B2"] = "2025-09-11 09:09:38"
    ws["A12"] = "Cq"
    for i, (well_no, label, cq) in enumerate(wells):
        col = 2 + i
        ws.cell(7, col, well_no)
        ws.cell(8, col, FLUOR)
        ws.cell(10, col, label)
        ws.cell(11, col, TARGET)
        ws.cell(12, col, cq)
    wb.save(path)
    return str(path)


def metadata(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["biomeme_run_name", "Biomeme_sample_ID", "sample_code"])


def well(df: pd.DataFrame, label: str) -> pd.Series:
    rows = df[df["Biomeme replicate"] == label]
    assert len(rows) == 1, f"expected one row for well {label!r}, got {len(rows)}"
    return rows.iloc[0]


# Run A: wells 5 and 6 are DIFFERENT samples; run B: wells 5 and 6 are ONE sample.
# Run B's metadata row comes last, which is what made the old global lookup merge
# run A's wells 5 and 6.
META = metadata(
    [
        ("RUN_A", "5,NTC", "S1"),
        ("RUN_A", "6,NTC", "S2"),
        ("RUN_B", "5,6,NTC", "S3"),
    ]
)
RUN_A_WELLS = [(5, "5", 0.0), (6, "6", 38.13), (9, "NTC", 0.0)]
RUN_B_WELLS = [(5, "5", 30.0), (6, "6", 31.0), (9, "NTC", 0.0)]

# Run N: the NTC amplifies at Cq 33. Well 1 amplified 5 cycles earlier (real signal),
# well 2 one cycle earlier (inside the 2-cycle margin), well 3 two cycles later (weaker
# than the blank). Each is its own single-well sample, so an untouched well is flag 2.
META_NTC = metadata([("RUN_N", "1,NTC", "S1"), ("RUN_N", "2,NTC", "S2"), ("RUN_N", "3,NTC", "S3")])
RUN_N_WELLS = [(1, "1", 28.0), (2, "2", 32.0), (3, "3", 35.0), (9, "NTC", 33.0)]


# ── Dilution factor: lives in the metadata, so it is applied after the merge — looked for
# any earlier it is never found, leaving 'Abundance diluted' equal to 'Abundance'. The
# factor is the fraction of original strength in the reaction, so the corrected abundance
# is Abundance / factor: a 1:2 dilution doubles the copies ────────────────────────────────


def test_abundance_diluted_corrects_for_the_sample_dilution(tmp_path: Path) -> None:
    meta = pd.DataFrame(
        [("RUN_D", "5,NTC", "S1", 0.5), ("RUN_D", "6,NTC", "S2", None)],
        columns=["biomeme_run_name", "Biomeme_sample_ID", "sample_code", "dilution_factor"],
    )
    wells = [(5, "5", 30.0), (6, "6", 31.0), (9, "NTC", 0.0)]
    out = extract_edna(write_run(tmp_path / "d.xlsx", "RUN_D", wells), meta)
    w5, w6 = well(out, "5"), well(out, "6")
    assert w5["Abundance diluted"] == pytest.approx(w5["Abundance"] / 0.5)
    assert w6["Abundance diluted"] == pytest.approx(w6["Abundance"])   # no factor: unchanged


def test_without_a_dilution_column_diluted_equals_abundance(tmp_path: Path) -> None:
    out = extract_edna(write_run(tmp_path / "b.xlsx", "RUN_B", RUN_B_WELLS), META)
    w = well(out, "5")
    assert w["Abundance diluted"] == pytest.approx(w["Abundance"])


# ── NTC rule: a well counts only when it amplified ≥ NTC_MARGIN_CQ cycles earlier than the
# run's strongest amplifying NTC — Cq is inverse to abundance, so the direction matters ───


def test_well_well_before_the_ntc_is_kept(tmp_path: Path) -> None:
    out = extract_edna(write_run(tmp_path / "n.xlsx", "RUN_N", RUN_N_WELLS), META_NTC)
    assert well(out, "1")["quality_flag"] == 2          # single well: questionable, not rejected
    assert well(out, "1")["presence"] == "true_x"


def test_well_within_the_margin_of_the_ntc_is_rejected(tmp_path: Path) -> None:
    out = extract_edna(write_run(tmp_path / "n.xlsx", "RUN_N", RUN_N_WELLS), META_NTC)
    assert well(out, "2")["quality_flag"] == 4
    assert well(out, "2")["presence"] == "false"


def test_well_later_than_the_ntc_is_rejected(tmp_path: Path) -> None:
    out = extract_edna(write_run(tmp_path / "n.xlsx", "RUN_N", RUN_N_WELLS), META_NTC)
    assert well(out, "3")["quality_flag"] == 4
    assert well(out, "3")["presence"] == "false"
    # the NTC row (one per run) carries no presence
    assert out[out["Biomeme replicate"] == "NTC"]["presence"].isna().all()


def test_non_amplifying_ntc_imposes_no_rule(tmp_path: Path) -> None:
    wells = [(1, "1", 28.0), (2, "2", 32.0), (3, "3", 35.0), (9, "NTC", 0.0)]
    out = extract_edna(write_run(tmp_path / "n.xlsx", "RUN_N", wells), META_NTC)
    assert [well(out, w)["quality_flag"] for w in ("1", "2", "3")] == [2, 2, 2]


def test_strongest_ntc_sets_the_floor(tmp_path: Path) -> None:
    # Two NTCs at 33 and 37: the floor is 33, so a well at 34 (earlier than one NTC, later
    # than the other) is rejected.
    meta = metadata([("RUN_N", "1,NTC1,NTC2", "S1"), ("RUN_N", "2,NTC1,NTC2", "S2")])
    wells = [(1, "1", 30.0), (2, "2", 34.0), (9, "NTC1", 37.0), (10, "NTC2", 33.0)]
    out = extract_edna(write_run(tmp_path / "n.xlsx", "RUN_N", wells), meta)
    assert well(out, "1")["quality_flag"] == 2
    assert well(out, "2")["quality_flag"] == 4


def test_non_amplifying_well_does_not_inherit_neighbour_sample_mean(tmp_path: Path) -> None:
    out = extract_edna(write_run(tmp_path / "a.xlsx", "RUN_A", RUN_A_WELLS), META)

    w5, w6 = well(out, "5"), well(out, "6")
    assert w5["sample_code"] == "S1" and w6["sample_code"] == "S2"
    assert pd.isna(w5["Mean Abundance"])
    assert w5["presence"] == "false"
    assert w6["Mean Abundance"] == pytest.approx(w6["Abundance"])
    assert w6["quality_flag"] == 2 and w6["presence"] == "true_x"


def test_two_wells_of_one_sample_share_their_own_mean(tmp_path: Path) -> None:
    out = extract_edna(write_run(tmp_path / "b.xlsx", "RUN_B", RUN_B_WELLS), META)

    w5, w6 = well(out, "5"), well(out, "6")
    expected = (w5["Abundance"] + w6["Abundance"]) / 2
    assert w5["sample_code"] == "S3" and w6["sample_code"] == "S3"
    assert w5["Mean Abundance"] == pytest.approx(expected)
    assert w6["Mean Abundance"] == pytest.approx(expected)
    assert (w5["quality_flag"], w6["quality_flag"]) == (1, 1)
    assert (w5["presence"], w6["presence"]) == ("true", "true")


def test_well_listed_in_two_rows_of_same_run_raises(tmp_path: Path) -> None:
    bad = metadata([("RUN_A", "5,NTC", "S1"), ("RUN_A", "5,6,NTC", "S2")])
    with pytest.raises(ValueError, match="well '5' is listed in two metadata rows"):
        extract_edna(write_run(tmp_path / "a.xlsx", "RUN_A", RUN_A_WELLS), bad)


def test_same_label_in_different_runs_is_not_a_conflict(tmp_path: Path) -> None:
    extract_edna(write_run(tmp_path / "a.xlsx", "RUN_A", RUN_A_WELLS), META)
    extract_edna(write_run(tmp_path / "b.xlsx", "RUN_B", RUN_B_WELLS), META)


def test_positive_control_is_shared_and_never_a_replicate(tmp_path: Path) -> None:
    """Every sample row lists the run's POS well; it is not a conflict, not a replicate
    and not part of any sample's mean, and a well labelled 'Pos' by the device is the same."""
    meta = metadata([("RUN_D", "1,2,POS,NTC", "S5"), ("RUN_D", "3,POS,NTC", "S6")])
    wells = [(1, "1", 30.0), (2, "2", 31.0), (3, "3", 32.0), (8, "Pos", 20.0), (9, "NTC", 0.0)]
    out = extract_edna(write_run(tmp_path / "d.xlsx", "RUN_D", wells), meta)

    w1, w2, w3 = well(out, "1"), well(out, "2"), well(out, "3")
    assert w1["Mean Abundance"] == pytest.approx((w1["Abundance"] + w2["Abundance"]) / 2)
    assert w3["Mean Abundance"] == pytest.approx(w3["Abundance"])
    assert w3["quality_flag"] == 2          # one replicate, not two — POS does not count


def test_amplifying_ntc_is_not_averaged_into_sample(tmp_path: Path) -> None:
    meta = metadata([("RUN_C", "7,8,NTC", "S4")])
    wells = [(7, "7", 30.0), (8, "8", 31.0), (9, "NTC", 33.68)]
    out = extract_edna(write_run(tmp_path / "c.xlsx", "RUN_C", wells), meta)

    w7, w8 = well(out, "7"), well(out, "8")
    expected = (w7["Abundance"] + w8["Abundance"]) / 2
    assert w7["Mean Abundance"] == pytest.approx(expected)
    assert w8["Mean Abundance"] == pytest.approx(expected)
    assert pd.isna(well(out, "NTC")["Mean Abundance"])


def test_ntc_rows_carry_no_abundance_derived_values(tmp_path: Path) -> None:
    out = extract_edna(write_run(tmp_path / "b.xlsx", "RUN_B", RUN_B_WELLS), META)

    ntc = well(out, "NTC")
    for col in ("Abundance", "Abundance diluted", "Mean Abundance", "log1pMeanAbundance"):
        assert pd.isna(ntc[col]), f"NTC row has {col} = {ntc[col]!r}"
    assert ntc["presence"] is None or pd.isna(ntc["presence"])


# ── Control wells: one row per physical control well per run. Copying a control once per
# sample that lists it multiplies the well count several-fold, and dropping the ones no
# sample lists removes exactly the wells the NTC rule acted on ─────────────────────────────

ABUNDANCE_COLUMNS = ("Abundance", "Abundance diluted", "Mean Abundance", "log1pMeanAbundance")


def test_log1p_mean_abundance_is_ln_of_mean_plus_one_and_named_so(tmp_path: Path) -> None:
    out = extract_edna(write_run(tmp_path / "b.xlsx", "RUN_B", RUN_B_WELLS), META)
    w = well(out, "5")
    assert "logMeanAbundance" not in out.columns        # the log10-looking name is gone
    assert w["log1pMeanAbundance"] == pytest.approx(np.log1p(w["Mean Abundance"]))


def test_control_well_appears_once_per_run(tmp_path: Path) -> None:
    # RUN_A: two samples, both list NTC.
    out = extract_edna(write_run(tmp_path / "a.xlsx", "RUN_A", RUN_A_WELLS), META)
    ntc = well(out, "NTC")                                  # exactly one row
    assert bool(ntc["is_control"]) is True
    assert pd.isna(ntc["sample_code"]) and pd.isna(ntc["Biomeme_sample_ID"])
    assert ntc["biomeme_run_name"] == "RUN_A"
    assert sorted(out.loc[~out["is_control"], "Biomeme replicate"]) == ["5", "6"]
    # presence.notna() is exactly the sample-row filter
    assert (out["is_control"] == out["presence"].isna()).all()


def test_unlisted_control_well_is_kept_and_still_sets_the_floor(tmp_path: Path) -> None:
    # The file has an NTC3 at Cq 29 that the metadata does not list. It rejects well 1
    # (28 ≥ 29 − 2) and is in the output to show why.
    meta = metadata([("RUN_U", "1,NTC", "S1")])
    wells = [(1, "1", 28.0), (8, "NTC", 0.0), (9, "NTC3", 29.0)]
    out = extract_edna(write_run(tmp_path / "u.xlsx", "RUN_U", wells), meta)
    assert well(out, "1")["quality_flag"] == 4
    ntc3 = well(out, "NTC3")
    assert bool(ntc3["is_control"]) is True
    assert ntc3["Cq"] == 29.0 and pd.isna(ntc3["sample_code"])


def test_control_rows_carry_run_level_metadata_only(tmp_path: Path) -> None:
    meta = pd.DataFrame(
        [("RUN_R", "1,NTC", "S1", "TZ", "2025-09-11", 0.5)],
        columns=["biomeme_run_name", "Biomeme_sample_ID", "sample_code", "country_code",
                 "sampling_date", "dilution_factor"],
    )
    wells = [(1, "1", 30.0), (9, "NTC", 0.0)]
    out = extract_edna(write_run(tmp_path / "r.xlsx", "RUN_R", wells), meta)
    ntc = well(out, "NTC")
    assert ntc["country_code"] == "TZ" and ntc["sampling_date"] == "2025-09-11"
    # the run's only sample does not lend its code or dilution to the control
    assert pd.isna(ntc["sample_code"]) and pd.isna(ntc["dilution_factor"])
    assert well(out, "1")["sample_code"] == "S1"


def test_run_metadata_the_samples_disagree_on_is_left_empty(tmp_path: Path) -> None:
    meta = pd.DataFrame(
        [("RUN_R", "1,NTC", "S1", "2025-09-11"), ("RUN_R", "2,NTC", "S2", "2025-09-12")],
        columns=["biomeme_run_name", "Biomeme_sample_ID", "sample_code", "sampling_date"],
    )
    wells = [(1, "1", 30.0), (2, "2", 30.0), (9, "NTC", 0.0)]
    out = extract_edna(write_run(tmp_path / "r.xlsx", "RUN_R", wells), meta)
    assert pd.isna(well(out, "NTC")["sampling_date"])


def test_controls_are_not_scored(tmp_path: Path) -> None:
    meta = metadata([("RUN_D", "1,2,POS,NTC", "S5")])
    wells = [(1, "1", 30.0), (2, "2", 31.0), (8, "Pos", 20.0), (9, "NTC", 0.0)]
    out = extract_edna(write_run(tmp_path / "d.xlsx", "RUN_D", wells), meta)
    pos, ntc = well(out, "Pos"), well(out, "NTC")
    assert pos["quality_flag"] == 0 and ntc["quality_flag"] == 3
    for col in ABUNDANCE_COLUMNS:
        assert pd.isna(pos[col]), f"POS row has {col} = {pos[col]!r}"
    assert pos["presence"] is None or pd.isna(pos["presence"])
    assert well(out, "1")["quality_flag"] == 1              # the sample is scored as before


def test_run_without_metadata_rows_yields_nothing(tmp_path: Path) -> None:
    # An unknown run has no samples to attach; its controls are not orphaned into the output.
    out = extract_edna(write_run(tmp_path / "x.xlsx", "RUN_X", RUN_A_WELLS), META)
    assert out.empty


# ── Control by well number: ``NTC@9`` in the metadata means well 9 of the file is a
# no-template control, whatever the file labels it. Two 2025 runs had their NTC labelled as a
# sample number ('9') or not at all; the raw export stays as it is and the metadata carries
# the correction, where it is logged ──────────────────────────────────────────────────────


def test_mislabelled_ntc_is_a_control_and_sets_the_floor(tmp_path: Path) -> None:
    meta = metadata([("RUN_O", "1,2,NTC@9", "S1"), ("RUN_O", "3,4,NTC@9", "S2")])
    wells = [(1, "1", 33.12), (2, "2", 33.66), (3, "3", 29.07), (4, "4", 27.35), (9, "9", 33.68)]
    out = extract_edna(write_run(tmp_path / "o.xlsx", "RUN_O", wells), meta)
    ntc = well(out, "NTC")
    assert bool(ntc["is_control"]) is True and ntc["Well Number"] == 9
    assert "9" not in set(out["Biomeme replicate"])
    assert well(out, "1")["quality_flag"] == 4 and well(out, "2")["quality_flag"] == 4
    assert well(out, "3")["quality_flag"] == 1 and well(out, "4")["quality_flag"] == 1
    assert well(out, "1")["presence"] == "false" and well(out, "3")["presence"] == "true"


def test_unlabelled_well_can_be_named_a_control(tmp_path: Path) -> None:
    # The file has NTC in well 8 (no amplification) and an unlabelled well 7 at Cq 34.47.
    meta = metadata([("RUN_U", "5,6,NTC,NTC@7", "S1")])
    wells = [(5, "5", 31.75), (6, "6", 35.76), (7, None, 34.47), (8, "NTC", 0.0)]
    out = extract_edna(write_run(tmp_path / "u.xlsx", "RUN_U", wells), meta)
    ntc = out[out["Biomeme replicate"] == "NTC"]
    assert sorted(ntc["Well Number"]) == [7, 8] and ntc["is_control"].all()
    assert well(out, "5")["quality_flag"] == 1  # 2.7 cycles before the blank
    assert well(out, "6")["quality_flag"] == 4  # later than the blank
    assert well(out, "5")["presence"] == "true"  # flag 1: the group had two amplifying wells


def test_naming_a_sample_well_a_control_raises(tmp_path: Path) -> None:
    meta = metadata([("RUN_O", "1,2,NTC@2", "S1")])
    wells = [(1, "1", 30.0), (2, "2", 31.0)]
    with pytest.raises(ValueError, match="also listed as a sample well"):
        extract_edna(write_run(tmp_path / "o.xlsx", "RUN_O", wells), meta)


@pytest.mark.parametrize("token", ["NTC@x", "5@9", "NTC@"])
def test_malformed_control_token_raises(tmp_path: Path, token: str) -> None:
    meta = metadata([("RUN_O", f"1,2,{token}", "S1")])
    wells = [(1, "1", 30.0), (2, "2", 31.0), (9, "9", 0.0)]
    with pytest.raises(ValueError, match="expected <control label>@<well number>"):
        extract_edna(write_run(tmp_path / "o.xlsx", "RUN_O", wells), meta)


def test_missing_well_is_a_warning_not_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    meta = metadata([("RUN_O", "1,2,NTC@9", "S1")])
    wells = [(1, "1", 30.0), (2, "2", 31.0)]
    out = extract_edna(write_run(tmp_path / "o.xlsx", "RUN_O", wells), meta)
    assert "file has no such well" in capsys.readouterr().out
    assert well(out, "1")["quality_flag"] == 1


# ── Excluded assays: assay-exclusions.csv beside assay-params.csv (or
# ODIN_ASSAY_EXCLUSIONS_PATH) names a run's assay the lab has declared unreliable. Its sample
# wells are flagged 5 — neither a detection nor a negative — and its controls are left alone ─


@pytest.fixture
def exclusions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def _write(text: str) -> None:
        path = tmp_path / "assay-exclusions.csv"
        path.write_text("run_name,target_id,reason\n" + text, encoding="utf-8")
        monkeypatch.setenv("ODIN_ASSAY_EXCLUSIONS_PATH", str(path))

    return _write


def test_excluded_target_wells_are_flagged_5(tmp_path: Path, exclusions) -> None:
    exclusions("RUN_B,khe,primer stocks contaminated with gBlock DNA\n")
    out = extract_edna(write_run(tmp_path / "b.xlsx", "RUN_B", RUN_B_WELLS), META)
    assert well(out, "5")["quality_flag"] == 5 and well(out, "6")["quality_flag"] == 5
    assert well(out, "5")["presence"] == "false"
    assert well(out, "NTC")["quality_flag"] == 3  # controls are not touched


def test_star_excludes_every_target_of_the_run_only(tmp_path: Path, exclusions) -> None:
    exclusions("RUN_B,*,whole run unreliable\n")
    out_b = extract_edna(write_run(tmp_path / "b.xlsx", "RUN_B", RUN_B_WELLS), META)
    out_a = extract_edna(write_run(tmp_path / "a.xlsx", "RUN_A", RUN_A_WELLS), META)
    assert (out_b.loc[~out_b["is_control"], "quality_flag"] == 5).all()
    assert 5 not in set(out_a["quality_flag"])


def test_other_target_is_not_excluded(tmp_path: Path, exclusions) -> None:
    exclusions("RUN_B,ctxA,only the cholera assay\n")
    out = extract_edna(write_run(tmp_path / "b.xlsx", "RUN_B", RUN_B_WELLS), META)
    assert well(out, "5")["quality_flag"] == 1


def test_exclusions_file_beside_assay_params_is_found(tmp_path: Path) -> None:
    # The autouse fixture put assay-params.csv in tmp_path; no env var is set for exclusions.
    (tmp_path / "assay-exclusions.csv").write_text("run_name,target_id,reason\nRUN_B,*,x\n", encoding="utf-8")
    out = extract_edna(write_run(tmp_path / "b.xlsx", "RUN_B", RUN_B_WELLS), META)
    assert well(out, "5")["quality_flag"] == 5


def test_file_without_the_columns_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "assay-exclusions.csv"
    path.write_text("run,target\nRUN_B,Khe\n", encoding="utf-8")
    monkeypatch.setenv("ODIN_ASSAY_EXCLUSIONS_PATH", str(path))
    with pytest.raises(ValueError, match="missing columns"):
        extract_edna(write_run(tmp_path / "b.xlsx", "RUN_B", RUN_B_WELLS), META)
