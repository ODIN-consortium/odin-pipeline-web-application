"""Every taxonomy row carries its file's read totals, and old feathers can be backfilled.

The figures divide a group's reads by the file's classified reads; until now that
denominator sat only on the root row of the same report, where nothing but a figure script
would find it. `add_read_totals` puts it on every row, 0 for a file that classified
nothing, and recomputes rather than fills, so a merge with rows written before the columns
existed comes out complete. The backfill command applies the same function to files on
disk and regenerates the target-group cut from the result.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backend.app.parsers.kraken_postprocessor import (
    READ_TOTAL_COLUMNS,
    add_pathogens,
    add_read_totals,
    create_kraken_dataset,
    create_target_groups,
    merge_with_existing_file,
)
from backend.app.parsers.postprocess_common import write_feather
from backend.app.pipeline.scripts import add_read_totals as backfill
from backend.tests.test_kraken_pathogen_labelling import REPORT, _yaml

RUN = "20260601_1200_MN00000_FBD00001_abcdef12"

# Kraken2's report puts the file's unclassified reads on the tax-0 row and everything it
# placed anywhere in the tree on the root row (tax 1). Two files: one with the fixture's
# Salmonella/Vibrio tree, one with reads but no root row at all.
EMPTY_REPORT = " 100.00\t47\t47\tU\t0\tunclassified\n"

# Salmonella enterica (direct 10) with subsp. enterica (direct 30) carrying serovar Typhi
# (direct 0) and its strain CT18 (direct 10), beside serovar Typhimurium (direct 10): every
# clade count is the sum of the direct counts below it, as in a real Kraken2 report. The
# shared REPORT fixture is not additive, so the identity cannot be checked on it.
ADDITIVE_REPORT = (
    " 90.00\t900\t900\tU\t0\tunclassified\n"
    " 10.00\t100\t0\tR\t1\troot\n"
    " 10.00\t100\t0\tR1\t131567\t  cellular organisms\n"
    " 10.00\t100\t0\tR2\t2\t    Bacteria\n"
    " 10.00\t100\t0\tP\t1224\t      Pseudomonadota\n"
    " 10.00\t100\t0\tC\t1236\t        Gammaproteobacteria\n"
    " 10.00\t100\t0\tO\t91347\t          Enterobacterales\n"
    " 10.00\t100\t0\tF\t543\t            Enterobacteriaceae\n"
    " 10.00\t100\t40\tG\t590\t              Salmonella\n"
    "  6.00\t60\t10\tS\t28901\t                Salmonella enterica\n"
    "  4.00\t40\t30\tS1\t59201\t                  Salmonella enterica subsp. enterica\n"
    "  1.00\t10\t0\tS2\t90370\t                    Salmonella enterica subsp. enterica serovar Typhi\n"
    "  1.00\t10\t10\tS3\t220341\t                      Salmonella enterica subsp. enterica serovar Typhi str. CT18\n"
    "  1.00\t10\t10\tS2\t90371\t                    Salmonella enterica subsp. enterica serovar Typhimurium\n"
)


def _frame(tmp_path: Path, reports: dict[str, str]) -> pd.DataFrame:
    folder = tmp_path / "kraken2"
    folder.mkdir(exist_ok=True)
    for barcode, text in reports.items():
        (folder / f"{barcode}_{RUN}_db.kraken2.kraken2.report.txt").write_text(text)
    return create_kraken_dataset(str(tmp_path), prefix="barcode")


# ── the function ──────────────────────────────────────────────────────────────


def test_every_row_of_a_file_carries_its_root_count(tmp_path: Path) -> None:
    df = add_read_totals(_frame(tmp_path, {"barcode01": REPORT}))

    assert (df["classified_reads"] == 100).all()
    assert (df["total_reads"] == 1000).all()


def test_total_minus_classified_is_the_unclassified_row(tmp_path: Path) -> None:
    df = add_read_totals(_frame(tmp_path, {"barcode01": REPORT}))
    unclassified = df.loc[df["NCBI tax ID"].astype(str) == "0", "No of Frags root"].iloc[0]

    assert (df["total_reads"] - df["classified_reads"]).iloc[0] == unclassified == 900


def test_a_file_with_no_root_row_gets_zero_classified_and_its_reads_as_total(tmp_path: Path) -> None:
    df = add_read_totals(_frame(tmp_path, {"barcode01": REPORT, "barcode08": EMPTY_REPORT}))
    empty = df[df["file_name"].str.startswith("barcode08")]
    full = df[df["file_name"].str.startswith("barcode01")]

    assert empty["classified_reads"].unique().tolist() == [0]
    assert empty["total_reads"].unique().tolist() == [47]
    assert full["classified_reads"].unique().tolist() == [100]


def test_the_columns_are_int64_and_never_missing(tmp_path: Path) -> None:
    df = add_read_totals(_frame(tmp_path, {"barcode01": REPORT, "barcode08": EMPTY_REPORT}))

    assert str(df["classified_reads"].dtype) == "int64"
    assert str(df["total_reads"].dtype) == "int64"
    assert df[["classified_reads", "total_reads"]].notna().all().all()


def test_recomputing_replaces_stale_values(tmp_path: Path) -> None:
    df = _frame(tmp_path, {"barcode01": REPORT})
    df["classified_reads"] = 5
    df["total_reads"] = 6

    out = add_read_totals(df)

    assert (out["classified_reads"] == 100).all()
    assert (out["total_reads"] == 1000).all()
    assert add_read_totals(out).equals(out)


def test_a_frame_without_file_name_is_refused_rather_than_stripped(tmp_path: Path) -> None:
    """Returning it untouched would let stale totals through; losing them would be worse."""
    df = _frame(tmp_path, {"barcode01": REPORT}).drop(columns=["file_name"])
    df["classified_reads"] = 5

    with pytest.raises(ValueError, match="file_name"):
        add_read_totals(df)
    assert (df["classified_reads"] == 5).all()  # the input is not mutated


def test_an_empty_frame_gets_the_columns_empty(tmp_path: Path) -> None:
    df = _frame(tmp_path, {"barcode01": REPORT}).iloc[0:0]

    out = add_read_totals(df)

    assert list(READ_TOTAL_COLUMNS) == [c for c in out.columns if c in READ_TOTAL_COLUMNS]
    assert len(out) == 0 and str(out["classified_reads"].dtype) == "int64"


def test_taxid_stored_as_text_is_matched(tmp_path: Path) -> None:
    """The writer casts NCBI tax ID to text before saving; old feathers hold it that way."""
    df = _frame(tmp_path, {"barcode01": REPORT})
    df["NCBI tax ID"] = df["NCBI tax ID"].astype(str)

    assert (add_read_totals(df)["classified_reads"] == 100).all()


def test_a_merge_with_an_older_file_fills_the_old_rows(tmp_path: Path) -> None:
    """Rows written before the columns existed get theirs from the root rows beside them."""
    old = _frame(tmp_path, {"barcode01": REPORT})
    old["run_accession"] = "OLD_RUN"
    old_file = tmp_path / "Norway_taxprofiler.feather"
    write_feather(old, old_file)  # no totals in it

    new = _frame(tmp_path, {"barcode08": EMPTY_REPORT})
    new["run_accession"] = RUN
    merged = add_read_totals(merge_with_existing_file(new, {RUN}, str(old_file)))

    assert merged[["classified_reads", "total_reads"]].notna().all().all()
    by_file = merged.groupby("file_name")["classified_reads"].first()
    assert by_file[by_file.index.str.startswith("barcode01")].iloc[0] == 100
    assert by_file[by_file.index.str.startswith("barcode08")].iloc[0] == 0
    assert str(merged["classified_reads"].dtype) == "int64"


def test_the_target_group_cut_inherits_the_totals(tmp_path: Path) -> None:
    df = _frame(tmp_path, {"barcode01": REPORT})
    df = add_pathogens(df, _yaml(tmp_path, [(28901, "Salmonella enterica", "Salmonella spp.")]))
    df = add_read_totals(df)

    ((_, cut),) = create_target_groups([("Norway_taxprofiler", df)])

    assert "classified_reads" in cut.columns
    assert (cut["classified_reads"] == 100).all()
    assert not (cut["NCBI tax ID"].astype(str) == "1").any()  # no root rows to compute from


def test_direct_reads_over_labelled_rows_equal_clade_reads_over_listed_taxids(tmp_path: Path) -> None:
    """The F3 identity on a species with two serovars, one of them with a strain below it.

    Labelling covers the whole clade, so summing the clade column over labelled rows counts
    the serovars inside their species again. The direct column over the same rows, and the
    clade column over the listed TaxID alone, both give the group's reads.
    """
    df = _frame(tmp_path, {"barcode01": ADDITIVE_REPORT})
    df = add_pathogens(df, _yaml(tmp_path, [(28901, "Salmonella enterica", "Salmonella spp.")]))
    df = add_read_totals(df)
    labelled = df[df["Priority pathogen group"] == "Salmonella spp."]
    denominator = labelled["classified_reads"].iloc[0]

    direct_rpm = labelled["No of Frags"].sum() / denominator * 1e6
    listed_rpm = df.loc[df["NCBI tax ID"].astype(str) == "28901", "No of Frags root"].sum() / denominator * 1e6
    clade_over_labelled = labelled["No of Frags root"].sum() / denominator * 1e6

    assert len(labelled) == 5  # the species and its four descendants
    assert direct_rpm == listed_rpm == pytest.approx(60 / 100 * 1e6)
    assert clade_over_labelled == pytest.approx(130 / 100 * 1e6)  # the trap the descriptions warn about


# ── the backfill command ──────────────────────────────────────────────────────


def _written(tmp_path: Path, name: str, reports: dict[str, str]) -> Path:
    """A feather as post-processing wrote it before the columns existed, plus its cut."""
    df = _frame(tmp_path, reports)
    df["run_accession"] = RUN
    df = add_pathogens(df, _yaml(tmp_path, [(28901, "Salmonella enterica", "Salmonella spp.")]))
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    write_feather(df, data / f"{name}.feather")
    ((cut_name, cut),) = create_target_groups([(name, df)])
    write_feather(cut, data / f"{cut_name}.feather")
    return data


def test_backfill_fills_the_full_file_and_regenerates_the_cut(tmp_path: Path, capsys) -> None:
    data = _written(tmp_path, "Norway_taxprofiler", {"barcode01": REPORT, "barcode08": EMPTY_REPORT})

    assert backfill.main([str(data)]) == 0

    full = pd.read_feather(data / "Norway_taxprofiler.feather")
    cut = pd.read_feather(data / "Norway_taxprofiler_target_group.feather")
    assert full[["classified_reads", "total_reads"]].notna().all().all()
    assert set(cut.columns) == set(full.columns)
    assert (cut["classified_reads"] == 100).all()
    out = capsys.readouterr().out
    assert "Norway_taxprofiler.feather" in out and "written" in out
    assert "no classified reads (rate undefined): barcode08_" in out
    assert not list(data.glob("*.tmp"))


def test_backfill_is_idempotent(tmp_path: Path, capsys) -> None:
    data = _written(tmp_path, "Norway_taxprofiler", {"barcode01": REPORT})
    backfill.main([str(data)])
    first = pd.read_feather(data / "Norway_taxprofiler.feather")

    assert backfill.main([str(data)]) == 0
    assert "unchanged" in capsys.readouterr().out
    assert pd.read_feather(data / "Norway_taxprofiler.feather").equals(first)


def test_backfill_writes_the_current_column_descriptions(tmp_path: Path) -> None:
    import pyarrow.ipc as ipc

    data = _written(tmp_path, "Norway_taxprofiler", {"barcode01": REPORT})
    backfill.main([str(data)])

    schema = ipc.open_file(str(data / "Norway_taxprofiler.feather")).schema
    assert b"denominator of reads per million" in schema.field("classified_reads").metadata[b"description"]
    assert b"Do not sum it" in schema.field("No of Frags root").metadata[b"description"]


def test_backfill_dry_run_writes_nothing(tmp_path: Path, capsys) -> None:
    data = _written(tmp_path, "Norway_taxprofiler", {"barcode01": REPORT})
    before = (data / "Norway_taxprofiler.feather").read_bytes()

    assert backfill.main([str(data), "--dry-run"]) == 0

    assert (data / "Norway_taxprofiler.feather").read_bytes() == before
    assert "would write" in capsys.readouterr().out


def test_backfill_handles_ssu_files_and_skips_target_group_files(tmp_path: Path) -> None:
    data = _written(tmp_path, "Norway_ssu", {"barcode01": REPORT})

    assert [p.name for p in backfill.full_files(data)] == ["Norway_ssu.feather"]
    assert backfill.main([str(data)]) == 0
    assert "classified_reads" in pd.read_feather(data / "Norway_ssu_target_group.feather").columns


def test_backfill_can_write_to_another_folder_and_leaves_the_source_alone(tmp_path: Path, capsys) -> None:
    data = _written(tmp_path, "Norway_taxprofiler", {"barcode01": REPORT, "barcode08": EMPTY_REPORT})
    before = {p.name: p.read_bytes() for p in data.glob("*.feather")}
    out = tmp_path / "backfilled"

    assert backfill.main([str(data), "--out", str(out)]) == 0

    assert {p.name: p.read_bytes() for p in data.glob("*.feather")} == before
    assert sorted(p.name for p in out.glob("*.feather")) == [
        "Norway_taxprofiler.feather", "Norway_taxprofiler_target_group.feather",
    ]
    full = pd.read_feather(out / "Norway_taxprofiler.feather")
    assert full[["classified_reads", "total_reads"]].notna().all().all()
    assert "classified_reads" in pd.read_feather(out / "Norway_taxprofiler_target_group.feather").columns
    assert f"written to {out}" in capsys.readouterr().out


def test_backfill_reports_a_directory_without_feathers(tmp_path: Path, capsys) -> None:
    assert backfill.main([str(tmp_path)]) == 1
    assert "No *_taxprofiler.feather" in capsys.readouterr().err
