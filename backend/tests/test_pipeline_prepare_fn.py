"""Characterization tests for the prepare_fn built by _make_prepare_fn.

prepare_fn is the background step that runs inside the executor thread before
nextflow starts: it clears a stale work dir, concatenates FASTQ input, builds the
shell command into cmd_holder, and writes the run manifest. It does heavy disk
work in production, so the samplesheet builders, command builders and manifest
writer are patched here — what these tests pin is the *dispatch*: which builder
each pipeline type uses, where -resume is appended, and the work-dir clearing rule.

Nothing covered this before; the AMR and SSU branches were byte-identical apart
from the command builder, which is exactly the kind of duplication a table-driven
dispatch can silently get wrong.
"""

import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.api import pipeline as pipeline_api

_NOW = "2025-01-01T00:00:00.000Z"
RA = "20260610_0800_MN00000_FAX00001_aaaa1111"


def _set_config(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        """INSERT OR REPLACE INTO config_values
           (id, key, value, created_at, updated_at, created_by)
           VALUES (?,?,?,?,?,?)""",
        (str(uuid.uuid4()), key, value, _NOW, _NOW, "test"),
    )
    db.commit()


def _make_prepare(
    db: sqlite3.Connection,
    tmp_path: Path,
    pipeline_type: str,
    cmd_holder: list[str],
    *,
    resume: bool = False,
    artic_outdir: str | None = None,
):
    """Build a prepare_fn for *pipeline_type* with everything pointed at tmp_path."""
    output_dir = str(tmp_path / "output")
    _set_config(db, "output_dir", output_dir)
    work_tmp = tmp_path / "work_tmp"
    work_tmp.mkdir(exist_ok=True)
    log_path = tmp_path / "run.log"
    plan = pipeline_api.LaunchPlan(
        db_factory=lambda: db,
        pipeline_type=pipeline_type,
        run_accessions=[RA],
        auto_merge=False,
        minknow_dir=str(tmp_path / "minknow"),
        output_dir_stored=output_dir,
        file_identifier="DemoSample_" + RA,
        tmp_dir=work_tmp,
        primary_ra=RA,
        clade="cladeii",
        scheme_version="v1.0.0",
        resume=resume,
        log_path=log_path,
        artic_outdir=artic_outdir,
    )
    return pipeline_api._make_prepare_fn(plan, cmd_holder)


@pytest.fixture(autouse=True)
def _no_manifest_writes():
    """The manifest writer touches the DB and disk — irrelevant to dispatch."""
    with patch.object(pipeline_api, "_write_run_manifest"):
        yield


# ── taxprofiler ───────────────────────────────────────────────────────────────


def test_taxprofiler_builds_its_own_command(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    cmd_holder: list[str] = []
    prepare = _make_prepare(db, tmp_path, "taxprofiler", cmd_holder)

    with (
        patch.object(
            pipeline_api.samplesheet,
            "build_taxprofiler_samplesheet",
            return_value=(tmp_path / "sheet.csv", None, "", [RA]),
        ),
        patch.object(
            pipeline_api.cb, "generate_databases_csv", return_value=tmp_path / "dbs.csv"
        ),
        patch.object(
            pipeline_api.cb, "build_taxprofiler_cmd", return_value=("nextflow tax", "out")
        ) as build_cmd,
    ):
        prepare()

    assert cmd_holder == ["nextflow tax"]
    assert build_cmd.call_count == 1


def test_taxprofiler_appends_resume_flag(db: sqlite3.Connection, tmp_path: Path) -> None:
    cmd_holder: list[str] = []
    prepare = _make_prepare(db, tmp_path, "taxprofiler", cmd_holder, resume=True)

    with (
        patch.object(
            pipeline_api.samplesheet,
            "build_taxprofiler_samplesheet",
            return_value=(tmp_path / "sheet.csv", None, "", [RA]),
        ),
        patch.object(
            pipeline_api.cb, "generate_databases_csv", return_value=tmp_path / "dbs.csv"
        ),
        patch.object(
            pipeline_api.cb, "build_taxprofiler_cmd", return_value=("nextflow tax", "out")
        ),
    ):
        prepare()

    assert cmd_holder == ["nextflow tax -resume"]


def test_merge_note_is_written_to_the_run_log(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """A merge note from the samplesheet builder reaches the operator's run log."""
    cmd_holder: list[str] = []
    prepare = _make_prepare(db, tmp_path, "taxprofiler", cmd_holder)

    with (
        patch.object(
            pipeline_api.samplesheet,
            "build_taxprofiler_samplesheet",
            return_value=(tmp_path / "sheet.csv", None, "[ODIN] merged 2 runs", [RA]),
        ),
        patch.object(
            pipeline_api.cb, "generate_databases_csv", return_value=tmp_path / "dbs.csv"
        ),
        patch.object(
            pipeline_api.cb, "build_taxprofiler_cmd", return_value=("nextflow tax", "out")
        ),
    ):
        prepare()

    assert "[ODIN] merged 2 runs" in (tmp_path / "run.log").read_text()


# ── wf-metagenomics AMR / SSU — same shape, different builder ──────────────────


@pytest.mark.parametrize(
    ("pipeline_type", "builder_name"),
    [
        ("wf_metagenomics_amr", "build_wf_metagenomics_amr_cmd"),
        ("wf_metagenomics_ssu", "build_wf_metagenomics_ssu_cmd"),
    ],
)
def test_metagenomics_uses_the_builder_for_its_type(
    db: sqlite3.Connection, tmp_path: Path, pipeline_type: str, builder_name: str
) -> None:
    cmd_holder: list[str] = []
    prepare = _make_prepare(db, tmp_path, pipeline_type, cmd_holder)
    fastq_dir = tmp_path / "fastq"
    fastq_dir.mkdir()
    other_builder = (
        "build_wf_metagenomics_ssu_cmd"
        if builder_name.endswith("amr_cmd")
        else "build_wf_metagenomics_amr_cmd"
    )

    with (
        patch.object(
            pipeline_api.samplesheet,
            "prepare_metagenomics_input",
            return_value=(fastq_dir, None, [RA]),
        ),
        patch.object(
            pipeline_api.cb, builder_name, return_value=(f"nextflow {pipeline_type}", "out")
        ) as used,
        patch.object(pipeline_api.cb, other_builder) as unused,
    ):
        prepare()

    assert cmd_holder == [f"nextflow {pipeline_type}"]
    assert used.call_count == 1
    unused.assert_not_called()


def test_metagenomics_passes_the_fastq_dir_and_identifier(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    cmd_holder: list[str] = []
    prepare = _make_prepare(db, tmp_path, "wf_metagenomics_amr", cmd_holder)
    fastq_dir = tmp_path / "fastq"
    fastq_dir.mkdir()

    with (
        patch.object(
            pipeline_api.samplesheet,
            "prepare_metagenomics_input",
            return_value=(fastq_dir, None, [RA]),
        ),
        patch.object(
            pipeline_api.cb, "build_wf_metagenomics_amr_cmd", return_value=("nf amr", "out")
        ) as build_cmd,
    ):
        prepare()

    _db_arg, fastq_arg, identifier_arg = build_cmd.call_args.args
    assert fastq_arg == str(fastq_dir)
    assert identifier_arg == "DemoSample_" + RA


# ── mpox — nextflow + squirrel chained, resume inside the chain ────────────────


def test_mpox_chains_squirrel_after_nextflow(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    cmd_holder: list[str] = []
    prepare = _make_prepare(db, tmp_path, "mpox", cmd_holder)
    run_dir = tmp_path / "minknow" / RA
    (run_dir / "fastq_pass").mkdir(parents=True)

    with (
        patch.object(
            pipeline_api.samplesheet, "build_mpox_samplesheet", return_value=tmp_path / "s.csv"
        ),
        patch.object(pipeline_api.cb, "build_mpox_cmd", return_value=("nf mpox", "squirrel!", "out")),
    ):
        prepare()

    assert cmd_holder == ["nf mpox && squirrel!"]


def test_mpox_resume_applies_to_the_nextflow_step_only(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """-resume must land on the nextflow command, not after the squirrel step."""
    cmd_holder: list[str] = []
    prepare = _make_prepare(db, tmp_path, "mpox", cmd_holder, resume=True)
    run_dir = tmp_path / "minknow" / RA
    (run_dir / "fastq_pass").mkdir(parents=True)

    with (
        patch.object(
            pipeline_api.samplesheet, "build_mpox_samplesheet", return_value=tmp_path / "s.csv"
        ),
        patch.object(pipeline_api.cb, "build_mpox_cmd", return_value=("nf mpox", "squirrel!", "out")),
    ):
        prepare()

    assert cmd_holder == ["nf mpox -resume && squirrel!"]


def test_mpox_missing_run_dir_raises(db: sqlite3.Connection, tmp_path: Path) -> None:
    """The executor marks the run failed — prepare_fn must not swallow this."""
    cmd_holder: list[str] = []
    prepare = _make_prepare(db, tmp_path, "mpox", cmd_holder)

    with (
        patch.object(
            pipeline_api.samplesheet, "build_mpox_samplesheet", return_value=tmp_path / "s.csv"
        ),
        pytest.raises(FileNotFoundError),
    ):
        prepare()

    assert cmd_holder == []


# ── squirrel ──────────────────────────────────────────────────────────────────


def test_squirrel_uses_the_artic_outdir(db: sqlite3.Connection, tmp_path: Path) -> None:
    cmd_holder: list[str] = []
    prepare = _make_prepare(
        db, tmp_path, "squirrel", cmd_holder, artic_outdir="/out/artic/run"
    )

    with patch.object(
        pipeline_api.cb, "build_squirrel_cmd", return_value=("squirrel cmd", "out")
    ) as build_cmd:
        prepare()

    assert cmd_holder == ["squirrel cmd"]
    assert build_cmd.call_args.args[1] == "/out/artic/run"


def test_squirrel_without_artic_outdir_raises(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    cmd_holder: list[str] = []
    prepare = _make_prepare(db, tmp_path, "squirrel", cmd_holder, artic_outdir=None)

    with pytest.raises(ValueError, match="artic_outdir"):
        prepare()


def test_squirrel_resume_is_not_appended(db: sqlite3.Connection, tmp_path: Path) -> None:
    """squirrel is not a nextflow run — -resume must never be appended."""
    cmd_holder: list[str] = []
    prepare = _make_prepare(
        db, tmp_path, "squirrel", cmd_holder, resume=True, artic_outdir="/out/artic/run"
    )

    with patch.object(
        pipeline_api.cb, "build_squirrel_cmd", return_value=("squirrel cmd", "out")
    ):
        prepare()

    assert cmd_holder == ["squirrel cmd"]


# ── stale work-dir clearing ───────────────────────────────────────────────────


def test_stale_work_dir_is_cleared_on_a_fresh_launch(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    cmd_holder: list[str] = []
    prepare = _make_prepare(db, tmp_path, "wf_metagenomics_amr", cmd_holder)
    stale = tmp_path / "stale_work"
    (stale / "task").mkdir(parents=True)
    fastq_dir = tmp_path / "fastq"
    fastq_dir.mkdir()

    with (
        patch.object(pipeline_api.cb, "resolve_work_dir", return_value=str(stale)),
        patch.object(
            pipeline_api.samplesheet,
            "prepare_metagenomics_input",
            return_value=(fastq_dir, None, [RA]),
        ),
        patch.object(
            pipeline_api.cb, "build_wf_metagenomics_amr_cmd", return_value=("nf amr", "out")
        ),
    ):
        prepare()

    assert not stale.exists()


def test_work_dir_is_kept_when_resuming(db: sqlite3.Connection, tmp_path: Path) -> None:
    """The nextflow cache is what -resume reuses — clearing it would defeat resume."""
    cmd_holder: list[str] = []
    prepare = _make_prepare(db, tmp_path, "wf_metagenomics_amr", cmd_holder, resume=True)
    work = tmp_path / "kept_work"
    (work / "task").mkdir(parents=True)
    fastq_dir = tmp_path / "fastq"
    fastq_dir.mkdir()

    with (
        patch.object(pipeline_api.cb, "resolve_work_dir", return_value=str(work)),
        patch.object(
            pipeline_api.samplesheet,
            "prepare_metagenomics_input",
            return_value=(fastq_dir, None, [RA]),
        ),
        patch.object(
            pipeline_api.cb, "build_wf_metagenomics_amr_cmd", return_value=("nf amr", "out")
        ),
    ):
        prepare()

    assert (work / "task").exists()
