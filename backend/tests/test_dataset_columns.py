"""dataset_columns.yaml describes exactly the columns the writers emit — no more, no less.

The descriptions travel inside every feather (Arrow field metadata) and Enlighten shows
them as the column's tooltip. They are configuration (pipeline/config/dataset_columns.yaml),
so nothing in the code checks them at runtime; these tests are the contract instead. Each
dataset is built the way its post-processor builds it — real parsers, real metadata row,
real pathogen labelling — and its column set must equal the set the file describes. A
pipeline that starts emitting a new column, or stops emitting one, fails here until the
file says so.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import pytest

from backend.app.parsers import dataset_columns as dc
from backend.app.parsers.amr_postprocessor import (
    _create_amr_summary,
    add_short_names,
    parse_amr_json_file,
)
from backend.app.parsers.kraken_postprocessor import (
    add_metadata,
    add_pathogens,
    add_read_totals,
    create_kraken_dataset,
)
from backend.app.parsers.postprocess_common import load_postprocess_metadata, write_feather
from backend.app.pipeline.scripts.decode import extract_edna
from backend.app.pipeline.scripts.traverse_biomeme import SITE_METADATA_COLUMNS
from backend.tests.test_decode import write_run
from backend.tests.test_kraken_pathogen_labelling import REPORT, _yaml
from backend.tests.test_postprocess_common import (  # noqa: F401  (fixture)
    _register_barcode,
    db_file,
)

RUN = "20260601_1200_MN00000_FBD00001_abcdef12"


@pytest.fixture(autouse=True)
def no_deployment_overlay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The bundled file alone: no ODIN_DATASET_COLUMNS_FILE, and a pipeline root without one."""
    monkeypatch.delenv(dc.ENV_VAR, raising=False)
    monkeypatch.setenv("ODIN_PIPELINE_ROOT", str(tmp_path / "root"))


def _metadata_row(db_path: str, tmp_path: Path) -> dict:
    """The nanopore run's metadata row as the post-processors receive it."""
    _register_barcode(db_path, RUN, "barcode01")
    rows = load_postprocess_metadata(
        db_path, [RUN], (tmp_path / "log.txt").open("a").write, skip_note="-"
    )
    assert len(rows) == 1
    return dict(rows[0])


def _assert_describes_exactly(dataset: str, emitted: set[str]) -> None:
    described = set(dc.column_metadata(dataset))
    assert emitted - described == set(), (
        f"{dataset}: the writer emits columns dataset_columns.yaml does not describe: "
        f"{sorted(emitted - described)}"
    )
    assert described - emitted == set(), (
        f"{dataset}: dataset_columns.yaml describes columns the writer no longer emits: "
        f"{sorted(described - emitted)}"
    )


# ── the contract, dataset by dataset ─────────────────────────────────────────


def test_taxprofiler_columns_are_described_exactly(db_file: str, tmp_path: Path) -> None:  # noqa: F811
    (tmp_path / "kraken2").mkdir()
    (tmp_path / "kraken2" / "barcode01_RUN_db.kraken2.kraken2.report.txt").write_text(REPORT)
    df = create_kraken_dataset(str(tmp_path), prefix="barcode")
    df = add_metadata(df, _metadata_row(db_file, tmp_path))
    df["incomplete_data"] = False
    df = add_pathogens(df, _yaml(tmp_path, [(666, "Vibrio cholerae", "Vibrio cholerae")]))
    df = add_read_totals(df)
    _assert_describes_exactly("taxprofiler", set(df.columns))


def _amr_json(tmp_path: Path) -> str:
    """One barcode of a wf-metagenomics AMR run, with the Kraken2 report beside it.

    The parser needs that report: it is the denominator of `rpm`, and a barcode with
    genes but no report is refused rather than written unnormalised.
    """
    run = tmp_path / "amr_run"
    (run / "amr").mkdir(parents=True, exist_ok=True)
    (run / "kraken2").mkdir(parents=True, exist_ok=True)
    (run / "kraken2" / "barcode01.kraken2.report.txt").write_text(
        " 50.00\t500\t500\tU\t0\tunclassified\n"
        " 50.00\t500\t2\tR\t1\troot\n"
    )
    path = run / "amr" / "barcode01.amr.json"
    path.write_text(
        json.dumps(
            {
                "barcode01": {
                    "pass": True,
                    "results": {
                        "tetracycline efflux pump": {
                            "count": 7,
                            "meta": [
                                {
                                    "RESISTANCE": "tetracycline;beta-lactam",
                                    "SEQUENCE": "ARO:3000165",
                                    "START": 1,
                                    "END": 1200,
                                    "%COVERAGE": 95.0,
                                    "%IDENTITY": 99.0,
                                }
                            ],
                        }
                    },
                }
            }
        )
    )
    return str(path)


def test_amr_columns_are_described_exactly(db_file: str, tmp_path: Path) -> None:  # noqa: F811
    df = pd.DataFrame(parse_amr_json_file(_amr_json(tmp_path), run_accession=RUN))
    df["incomplete_data"] = False
    df = add_metadata(df, _metadata_row(db_file, tmp_path))
    df = add_short_names(df)
    _assert_describes_exactly("amr", set(df.columns))


def test_amr_summary_columns_are_described_exactly(db_file: str, tmp_path: Path) -> None:  # noqa: F811
    df = pd.DataFrame(parse_amr_json_file(_amr_json(tmp_path), run_accession=RUN))
    df = add_metadata(df, _metadata_row(db_file, tmp_path))
    _create_amr_summary(df, str(tmp_path), lambda _m: None)
    summary = pd.read_feather(tmp_path / "amr_data_summary_by_resistance.feather")
    _assert_describes_exactly("amr_summary", set(summary.columns))


def test_biomeme_columns_are_described_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assay_params = tmp_path / "assay-params.csv"
    assay_params.write_text(
        "Pathogen,Target_ID,M,B,E,T\nKlebsiella pneumoniae,Khe,-3.22,36.66,1.05,180\n"
    )
    monkeypatch.setenv("ODIN_ASSAY_PARAMS_PATH", str(assay_params))
    # The five columns traverse_biomeme's biomeme_runs query hands to the decoder.
    meta = pd.DataFrame(
        [("RUN_B", "S1", pd.Timestamp("2025-09-11"), "5,6,NTC", 0.5)],
        columns=[
            "biomeme_run_name",
            "sample_code",
            "sampling_date",
            "Biomeme_sample_ID",
            "dilution_factor",
        ],
    )
    wells = [(5, "5", 30.0), (6, "6", 31.0), (9, "NTC", 0.0)]
    decoded = extract_edna(write_run(tmp_path / "b.xlsx", "RUN_B", wells), meta)
    _assert_describes_exactly("biomeme", set(decoded.columns) | set(SITE_METADATA_COLUMNS))


# ── the file and its loader ───────────────────────────────────────────────────


def test_bundled_file_describes_every_dataset_with_non_empty_texts() -> None:
    columns = dc.load_dataset_columns(dc.bundled_file())
    assert set(columns) == set(dc.DATASETS)
    for dataset, entries in columns.items():
        assert entries, dataset
        for column, items in entries.items():
            assert items["description"].strip(), f"{dataset}.{column}"


def test_a_value_may_be_a_text_or_a_mapping_with_more_items(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "datasets:\n  biomeme:\n    columns:\n"
        "      Cq: Quantification cycle.\n"
        "      Abundance:\n        description: Copies.\n        unit: copies per reaction\n"
    )
    columns = dc.load_dataset_columns(path)["biomeme"]
    assert columns["Cq"] == {"description": "Quantification cycle."}
    assert columns["Abundance"] == {"description": "Copies.", "unit": "copies per reaction"}


def test_shared_blocks_are_included_and_the_dataset_s_own_entry_wins(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "shared:\n  sample:\n    sample_code: shared text\n    country: Country.\n"
        "datasets:\n  amr:\n    include: [sample]\n    columns:\n      sample_code: amr's own text\n"
    )
    columns = dc.load_dataset_columns(path)["amr"]
    assert columns["sample_code"]["description"] == "amr's own text"
    assert columns["country"]["description"] == "Country."


@pytest.mark.parametrize(
    "text, message",
    [
        ("datasets:\n  ssu:\n    columns: {}\n", "unknown dataset 'ssu'"),
        ("datasets:\n  amr:\n    include: [nope]\n", "unknown shared block 'nope'"),
        ("datasets:\n  amr:\n    columns:\n      count: ''\n", "non-empty 'description'"),
        (
            "datasets:\n  amr:\n    columns:\n      count:\n        unit: reads\n",
            "non-empty 'description'",
        ),
        ("just a string\n", "top-level 'datasets'"),
    ],
)
def test_malformed_files_are_refused_with_the_entry_named(
    tmp_path: Path, text: str, message: str
) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(text)
    with pytest.raises(ValueError, match=message):
        dc.load_dataset_columns(path)


def test_deployment_overlay_replaces_only_what_it_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay = tmp_path / "root" / "config" / dc.FILE_NAME
    overlay.parent.mkdir(parents=True)
    overlay.write_text(
        "datasets:\n  biomeme:\n    columns:\n      Target_ID: 'Assays of this deployment.'\n"
    )
    bundled = dc.load_dataset_columns(dc.bundled_file())["biomeme"]

    columns = dc.column_metadata("biomeme")  # found via $ODIN_PIPELINE_ROOT/config/

    assert columns["Target_ID"] == {"description": "Assays of this deployment."}
    assert columns["Cq"] == bundled["Cq"]
    assert set(columns) == set(bundled)
    assert dc.column_metadata("amr") == dc.load_dataset_columns(dc.bundled_file())["amr"]


def test_env_var_names_the_overlay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    overlay = tmp_path / "elsewhere.yaml"
    overlay.write_text("datasets:\n  amr:\n    columns:\n      count: 'Reads, per this lab.'\n")
    monkeypatch.setenv(dc.ENV_VAR, str(overlay))
    assert dc.column_metadata("amr")["count"]["description"] == "Reads, per this lab."


def test_a_malformed_overlay_is_reported_and_the_bundled_texts_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    overlay = tmp_path / "bad.yaml"
    overlay.write_text("datasets:\n  amr:\n    columns:\n      count: ''\n")
    monkeypatch.setenv(dc.ENV_VAR, str(overlay))
    with caplog.at_level(logging.WARNING):
        columns = dc.column_metadata("amr")
    assert columns == dc.load_dataset_columns(dc.bundled_file())["amr"]
    assert "Ignoring column descriptions" in caplog.text and "bad.yaml" in caplog.text


def test_unknown_dataset_is_refused() -> None:
    with pytest.raises(KeyError, match="unknown dataset 'mpox'"):
        dc.column_metadata("mpox")


def test_write_feather_carries_every_item_of_a_column(tmp_path: Path) -> None:
    import pyarrow as pa

    out = tmp_path / "x.feather"
    write_feather(
        pd.DataFrame({"lon": [5.3]}),
        out,
        {"lon": {"description": "Longitude.", "unit": "degrees east"}},
    )
    field = pa.ipc.open_file(str(out)).schema.field("lon")
    assert field.metadata == {b"description": b"Longitude.", b"unit": b"degrees east"}
