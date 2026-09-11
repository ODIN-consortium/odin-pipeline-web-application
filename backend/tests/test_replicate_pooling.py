"""Replicate groups through the application: shown, refused, pooled, recorded.

The rule lives in pipeline.replicates (test_replicates.py). Here each consumer is
checked to apply it: the dashboard's messages, the pre-launch check, the two input
builders, the AMR setting and its record in the manifest and the run params, and the
two post-processors that attach the pooled row's metadata.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.app.api import pipeline as pipeline_api
from backend.app.api.discovery import _build_replicate_message_maps
from backend.app.api.pipeline import _validate_pre_launch
from backend.app.db.queries import get_amr_replicates
from backend.app.parsers.amr_postprocessor import run_amr_postprocessing
from backend.app.parsers.kraken_postprocessor import run_taxprofiler_postprocessing
from backend.app.pipeline.samplesheet import (
    build_taxprofiler_samplesheet,
    prepare_metagenomics_input,
)
from backend.app.schemas import PipelineLaunchPayload
from backend.tests.test_kraken_pathogen_labelling import REPORT, _yaml
from backend.tests.test_postprocess_common import db_file  # noqa: F401  (fixture)
from backend.tests.test_samplesheet import (
    RUN,
    SAMPLE,
    SITE,
    _fastq_path,
    _minknow_stored,
    _output_stored,
    _write_fastq_gz,
)

RA = "20260610_0800_MN00000_FAX00001_aabb1234"
RA2 = "20260611_0800_MN00000_FAX00002_ccdd5678"
_NOW = "2026-01-01T00:00:00Z"


# ── helpers ───────────────────────────────────────────────────────────────────


def _register(client: TestClient, barcodes: dict[str, int | None], *, run_accession: str = RA,
              sample: dict | None = None) -> None:
    """One sample (on the shared site) and the given barcodes of one run, each with its
    replicate number."""
    created = client.post("/api/sites", json=SITE)
    site = created.json() if created.status_code == 201 else client.get("/api/sites").json()[0]
    sample_row = client.post(
        "/api/samples", json={**(sample or SAMPLE), "site_id": site["id"]}
    ).json()
    for barcode, replicate in barcodes.items():
        r = client.post(
            "/api/nanopore-runs",
            json={
                **RUN,
                "run_accession": run_accession,
                "barcode": barcode,
                "replicate": replicate,
                "replicate_type": "collection" if replicate else None,
                "sample_id": sample_row["id"],
            },
        )
        assert r.status_code == 201, r.text


def _rows(csv_path: Path) -> list[list[str]]:
    import csv

    with csv_path.open(newline="") as f:
        return list(csv.reader(f))[1:]


# ── the dashboard and the wizard ──────────────────────────────────────────────


def test_a_numbered_group_is_a_note_and_an_unnumbered_one_a_warning(
    client: TestClient, db: sqlite3.Connection
) -> None:
    _register(client, {"barcode01": 1, "barcode02": 2})
    _register(client, {"barcode03": None, "barcode04": None}, sample={**SAMPLE, "sampling_date": "20240602"})

    warnings, notes = _build_replicate_message_maps(db)

    assert notes[RA] == [
        "NOBGOHarbour_water (20240601): replicates barcode01, barcode02 will be pooled before classification."
    ]
    (warning,) = warnings[RA]
    assert "barcode03, barcode04" in warning and "replicate number" in warning


def test_discovery_and_readiness_carry_the_notes(client: TestClient) -> None:
    _register(client, {"barcode01": 1, "barcode02": 2})

    runs = client.get("/api/discovery/nanopore").json()["runs"]
    (run,) = [r for r in runs if r["run_accession"] == RA]
    assert run["metadata_notes"] and "will be pooled" in run["metadata_notes"][0]
    assert run["metadata_warnings"] == []

    readiness = client.get(f"/api/discovery/nanopore/{RA}/readiness").json()
    assert readiness["metadata_notes"] == run["metadata_notes"]


def test_the_replicate_messages_endpoint_serves_the_runs_page(client: TestClient) -> None:
    """One call for every run, keyed by accession, with both kinds of message."""
    _register(client, {"barcode01": 1, "barcode02": 2})
    _register(client, {"barcode03": None, "barcode04": None}, run_accession=RA2,
              sample={**SAMPLE, "sampling_date": "20240602"})

    body = client.get("/api/discovery/nanopore/replicate-messages").json()

    assert set(body) == {RA, RA2}
    assert body[RA]["warnings"] == [] and "will be pooled" in body[RA]["notes"][0]
    assert body[RA2]["notes"] == [] and "barcode03, barcode04" in body[RA2]["warnings"][0]


def test_a_sample_on_one_barcode_produces_neither(client: TestClient, db: sqlite3.Connection) -> None:
    _register(client, {"barcode01": None})

    assert _build_replicate_message_maps(db) == ({}, {})


# ── the launch ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pipeline_type", ["taxprofiler", "wf_metagenomics_amr", "wf_metagenomics_ssu"])
def test_an_unnumbered_group_blocks_the_metagenomic_launches(
    client: TestClient, db: sqlite3.Connection, pipeline_type: str
) -> None:
    _register(client, {"barcode01": None, "barcode02": None})

    errors = _validate_pre_launch(db, pipeline_type, [RA], "/nonexistent")

    assert any("barcode01, barcode02" in e and "replicate number" in e for e in errors)


def test_a_half_numbered_group_blocks_the_launch(client: TestClient, db: sqlite3.Connection) -> None:
    _register(client, {"barcode01": 1, "barcode02": None})

    errors = _validate_pre_launch(db, "taxprofiler", [RA], "/nonexistent")

    assert any("barcode01 but not on barcode02" in e for e in errors)


def test_a_numbered_group_does_not_block_the_launch(client: TestClient, db: sqlite3.Connection) -> None:
    _register(client, {"barcode01": 1, "barcode02": 2})

    errors = _validate_pre_launch(db, "taxprofiler", [RA], "/nonexistent")

    assert not any("Replicate" in e or "replicate" in e for e in errors)


def test_mpox_is_not_blocked_because_it_never_pools(client: TestClient, db: sqlite3.Connection) -> None:
    _register(client, {"barcode01": None, "barcode02": None})

    errors = _validate_pre_launch(db, "mpox", [RA], "/nonexistent")

    assert not any("replicate" in e.lower() for e in errors)


def test_a_misspelt_amr_replicates_value_blocks_an_amr_launch(
    client: TestClient, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(client, {"barcode01": None})
    monkeypatch.setenv("ODIN_AMR_REPLICATES", "pooled")

    errors = _validate_pre_launch(db, "wf_metagenomics_amr", [RA], "/nonexistent")

    assert any("amr_replicates is 'pooled'" in e for e in errors)
    assert not any("amr_replicates" in e for e in _validate_pre_launch(db, "taxprofiler", [RA], "/nonexistent"))


# ── the setting ───────────────────────────────────────────────────────────────


def test_amr_replicates_defaults_to_pool(db: sqlite3.Connection) -> None:
    assert get_amr_replicates(db) == "pool"


def test_amr_replicates_reads_the_setting_and_the_env_override(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.execute(
        "INSERT INTO config_values (id, key, value, created_at, updated_at, created_by)"
        " VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), "amr_replicates", "per_barcode", _NOW, _NOW, "test"),
    )
    db.commit()
    assert get_amr_replicates(db) == "per_barcode"

    monkeypatch.setenv("ODIN_AMR_REPLICATES", "pool")
    assert get_amr_replicates(db) == "pool"


def test_launch_params_record_the_choice_for_amr_only() -> None:
    amr = PipelineLaunchPayload(pipeline_type="wf_metagenomics_amr", run_accessions=[RA])
    tax = PipelineLaunchPayload(pipeline_type="taxprofiler", run_accessions=[RA])

    assert pipeline_api._build_launch_params(amr, False, None, "per_barcode")["amr_replicates"] == "per_barcode"
    assert "amr_replicates" not in pipeline_api._build_launch_params(tax, False, None, None)


def test_a_requeued_amr_run_keeps_the_value_it_was_launched_with(db: sqlite3.Connection) -> None:
    assert pipeline_api._amr_replicates_for(db, "wf_metagenomics_amr", {"amr_replicates": "per_barcode"}) == "per_barcode"
    assert pipeline_api._amr_replicates_for(db, "wf_metagenomics_amr", {}) == "pool"
    assert pipeline_api._amr_replicates_for(db, "taxprofiler", {"amr_replicates": "per_barcode"}) is None


def test_the_launch_plan_pools_unless_amr_says_per_barcode(tmp_path: Path) -> None:
    def plan(pipeline_type, amr_replicates):
        return pipeline_api.LaunchPlan(
            db_factory=lambda: None, pipeline_type=pipeline_type, run_accessions=[RA],
            auto_merge=False, minknow_dir="", output_dir_stored="", file_identifier="x",
            tmp_dir=tmp_path, primary_ra=RA, clade=None, scheme_version=None, resume=False,
            log_path=tmp_path / "log", amr_replicates=amr_replicates,
        )

    assert plan("taxprofiler", None).pool_replicates
    assert plan("wf_metagenomics_amr", "pool").pool_replicates
    assert not plan("wf_metagenomics_amr", "per_barcode").pool_replicates


# ── the manifest ──────────────────────────────────────────────────────────────


def _manifest(db, tmp_path, client, **kw) -> str:
    _register(client, {"barcode01": 1, "barcode02": 2})
    pipeline_api._write_run_manifest(
        db, str(tmp_path), "outputs_wf_metagenomics_amr", "id", "", "wf_metagenomics_amr",
        [RA], False, provisional=False, **kw,
    )
    return (
        tmp_path / "nanopore_processed" / "outputs_wf_metagenomics_amr" / "id" / "run_manifest.txt"
    ).read_text(encoding="utf-8")


def test_the_manifest_records_how_amr_replicates_were_treated(db, tmp_path, client) -> None:
    text = _manifest(db, tmp_path, client, amr_replicates="per_barcode")

    assert "AMR replicates: screened per barcode (amr_replicates = per_barcode)" in text
    assert "replicate" in text.split("Barcodes:")[1].splitlines()[1]  # the table header
    assert "barcode01  1 " in text and "barcode02  2 " in text


def test_the_manifest_says_nothing_about_amr_replicates_for_other_pipelines(db, tmp_path, client) -> None:
    assert "AMR replicates" not in _manifest(db, tmp_path, client)


# ── the inputs ────────────────────────────────────────────────────────────────


def test_taxprofiler_writes_one_row_for_a_replicate_group_fed_by_every_member(
    db: sqlite3.Connection, client: TestClient, tmp_path: Path
) -> None:
    _register(client, {"barcode01": 2, "barcode02": 1})
    _register(client, {"barcode03": None}, sample={**SAMPLE, "sampling_date": "20240602"})
    for bc in ("barcode01", "barcode02", "barcode03"):
        _write_fastq_gz(_fastq_path(tmp_path, RA, bc))

    csv_path, _, notes, _ = build_taxprofiler_samplesheet(
        db, [RA], auto_merge=False, minknow_dir_stored=_minknow_stored(tmp_path),
        output_dir_stored=_output_stored(tmp_path), file_identifier="t", tmp_dir=tmp_path / "tmp",
    )

    rows = _rows(csv_path)
    assert [r[0] for r in rows] == ["barcode02-barcode01", "barcode03"]
    assert rows[0][3].endswith("barcode02-barcode01_" + RA + ".fastq.gz")
    (pooled,) = (tmp_path / "tmp").glob("barcode02-barcode01_*.fastq.gz")
    (single,) = (tmp_path / "tmp").glob("barcode03_*.fastq.gz")
    assert pooled.stat().st_size == 2 * single.stat().st_size  # two gzip members, one each
    assert notes == ["[ODIN] Pooling replicates: barcode02-barcode01 = barcode02 + barcode01 (NOBGOHarbour_water (20240601))"]


def test_taxprofiler_refuses_a_group_it_cannot_pool(
    db: sqlite3.Connection, client: TestClient, tmp_path: Path
) -> None:
    _register(client, {"barcode01": None, "barcode02": None})
    _write_fastq_gz(_fastq_path(tmp_path, RA, "barcode01"))

    with pytest.raises(ValueError, match="cannot be processed"):
        build_taxprofiler_samplesheet(
            db, [RA], auto_merge=False, minknow_dir_stored=_minknow_stored(tmp_path),
            output_dir_stored=_output_stored(tmp_path), file_identifier="t", tmp_dir=tmp_path / "tmp",
        )


def test_metagenomics_pools_into_one_directory_or_keeps_barcodes_apart(
    db: sqlite3.Connection, client: TestClient, tmp_path: Path
) -> None:
    _register(client, {"barcode01": 1, "barcode02": 2})
    for bc in ("barcode01", "barcode02"):
        _write_fastq_gz(_fastq_path(tmp_path, RA, bc))

    def prepare(pool: bool, tag: str):
        return prepare_metagenomics_input(
            db, [RA], auto_merge=False, minknow_dir_stored=_minknow_stored(tmp_path),
            output_dir_stored=_output_stored(tmp_path), file_identifier=tag,
            tmp_dir=tmp_path / tag, pool_replicates=pool,
        )

    pooled_dir, _, _, pooled_notes = prepare(True, "pooled")
    assert sorted(p.name for p in pooled_dir.iterdir()) == ["barcode01-barcode02"]
    assert (pooled_dir / "barcode01-barcode02" / "barcode01-barcode02_pooled.fastq.gz").exists()
    assert len(pooled_notes) == 1

    apart_dir, _, _, apart_notes = prepare(False, "apart")
    assert sorted(p.name for p in apart_dir.iterdir()) == ["barcode01", "barcode02"]
    assert apart_notes == []


# ── the post-processors ───────────────────────────────────────────────────────


def _register_group(db_path: str, run_accession: str, barcodes: dict[str, int | None]) -> None:
    """One sample on several barcodes, written straight into a post-processing database."""
    con = sqlite3.connect(db_path)
    site_id, sample_id, nra_id = (str(uuid.uuid4()) for _ in range(3))
    con.execute(
        "INSERT INTO sites (id, site_code, site, country, country_code, city_code, city,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (site_id, "NOBGN01", "01", "Norway", "NO", "BGN", "", _NOW, _NOW),
    )
    con.execute(
        "INSERT INTO samples (id, site_id, sample_code, sample_type, sampling_date,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (sample_id, site_id, "NOBGN01_water", "water", "20260601", _NOW, _NOW),
    )
    con.execute(
        "INSERT INTO nanopore_run_accessions (id, run_accession, protocol_id, sequencing_kit_id,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (nra_id, run_accession, "P2", "RB_GEN", _NOW, _NOW),
    )
    for barcode, replicate in barcodes.items():
        con.execute(
            "INSERT INTO nanopore_runs (id, accession_id, sample_id, barcode, replicate,"
            " replicate_type, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), nra_id, sample_id, barcode, replicate,
             "collection" if replicate else None, _NOW, _NOW),
        )
    con.commit()
    con.close()


def _amr_output(tmp_path: Path, names: list[str]) -> Path:
    out = tmp_path / "amr_out"
    (out / "amr").mkdir(parents=True)
    (out / "kraken2").mkdir()
    for name in names:
        (out / "kraken2" / f"{name}.kraken2.report.txt").write_text(
            " 50.00\t500\t500\tU\t0\tunclassified\n 50.00\t500\t2\tR\t1\troot\n"
        )
        (out / "amr" / f"{name}.amr.json").write_text(json.dumps({
            name: {"pass": True, "results": {"tet(A)": {"count": 1, "meta": [
                {"RESISTANCE": "tetracycline", "SEQUENCE": f"read-{name}", "%COVERAGE": 90.0, "%IDENTITY": 99.0}
            ]}}}
        }))
    return out


def test_amr_pooled_output_becomes_one_row_set_whose_replicate_is_pooled(
    db_file: str, tmp_path: Path  # noqa: F811
) -> None:
    _register_group(db_file, RA, {"barcode01": 1, "barcode02": 2})
    out = _amr_output(tmp_path, ["barcode01-barcode02"])
    enlighten = tmp_path / "enlighten"

    run_amr_postprocessing([RA], str(out), str(enlighten), db_file, tmp_path / "log.txt",
                           pool_replicates=True)

    df = pd.read_feather(enlighten / "amr_data.feather")
    assert df["barcode"].tolist() == ["barcode01-barcode02"]
    assert df["replicate"].tolist() == ["pooled"]
    assert df["replicate_type"].tolist() == ["collection"]
    assert df["alias"].tolist() == ["NOBGN01_water_barcode01-barcode02"]
    assert df["sample_key"].tolist() == ["NOBGN01_water_P2_RB_GEN_20260601"]
    assert "Pooled replicates: barcode01-barcode02" in (tmp_path / "log.txt").read_text()


def test_amr_per_barcode_output_keeps_each_member_with_its_number(
    db_file: str, tmp_path: Path  # noqa: F811
) -> None:
    _register_group(db_file, RA, {"barcode01": 1, "barcode02": 2})
    out = _amr_output(tmp_path, ["barcode01", "barcode02"])
    enlighten = tmp_path / "enlighten"

    run_amr_postprocessing([RA], str(out), str(enlighten), db_file, tmp_path / "log.txt",
                           pool_replicates=False)

    df = pd.read_feather(enlighten / "amr_data.feather").sort_values("barcode")
    assert df["barcode"].tolist() == ["barcode01", "barcode02"]
    assert df["replicate"].tolist() == ["1", "2"]
    assert "per barcode" in (tmp_path / "log.txt").read_text()


def test_amr_pooled_mode_reports_a_missing_pooled_file_rather_than_reading_members(
    db_file: str, tmp_path: Path  # noqa: F811
) -> None:
    """A per-barcode output read in pooled mode is a mismatch, not something to patch over."""
    _register_group(db_file, RA, {"barcode01": 1, "barcode02": 2})
    out = _amr_output(tmp_path, ["barcode01", "barcode02"])
    enlighten = tmp_path / "enlighten"

    run_amr_postprocessing([RA], str(out), str(enlighten), db_file, tmp_path / "log.txt",
                           pool_replicates=True)

    assert not (enlighten / "amr_data.feather").exists()
    assert "no AMR JSON files found for: ['" + RA + "/barcode01-barcode02']" in (tmp_path / "log.txt").read_text()


def test_an_unnumbered_barcode_of_the_run_still_has_an_empty_replicate(
    db_file: str, tmp_path: Path  # noqa: F811
) -> None:
    _register_group(db_file, RA, {"barcode05": None})
    out = _amr_output(tmp_path, ["barcode05"])
    enlighten = tmp_path / "enlighten"

    run_amr_postprocessing([RA], str(out), str(enlighten), db_file, tmp_path / "log.txt")

    df = pd.read_feather(enlighten / "amr_data.feather")
    assert df["replicate"].tolist() == [""]
    assert df["replicate_type"].tolist() == [""]


def test_taxprofiler_pooled_report_is_read_once_under_the_pooled_row(
    db_file: str, tmp_path: Path  # noqa: F811
) -> None:
    _register_group(db_file, RA, {"barcode01": 1, "barcode02": 2})
    out = tmp_path / "tax_out" / "kraken2"
    out.mkdir(parents=True)
    (out / f"barcode01-barcode02_{RA}_db.kraken2.kraken2.report.txt").write_text(REPORT)
    enlighten = tmp_path / "enlighten"

    run_taxprofiler_postprocessing(
        [RA], str(tmp_path / "tax_out"), str(enlighten),
        _yaml(tmp_path, [(666, "Vibrio cholerae", "Vibrio cholerae")]), db_file, tmp_path / "log.txt",
    )

    df = pd.read_feather(enlighten / "Norway_taxprofiler.feather")
    assert set(df["barcode"]) == {"barcode01-barcode02"}
    assert set(df["replicate"]) == {"pooled"}
    assert set(df["alias"]) == {"NOBGN01_water_barcode01-barcode02"}
    # one row per report line, not one per member
    assert len(df) == REPORT.count("\n")
