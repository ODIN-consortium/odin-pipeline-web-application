"""
Tests for backend.app.pipeline.command_builder — pure command construction.

These tests do not call subprocess or touch the Nextflow binary.  They verify
that the shell command strings contain the correct flags, paths, and options
given known inputs.
"""

import csv
import sqlite3
import uuid
from pathlib import Path

import pytest

from backend.app.pipeline.command_builder import (
    build_mpox_cmd,
    build_outdir,
    build_taxprofiler_cmd,
    build_wf_metagenomics_amr_cmd,
    build_wf_metagenomics_ssu_cmd,
    generate_databases_csv,
)

# ── helpers ───────────────────────────────────────────────────────────────────

_NOW = "2025-01-01T00:00:00.000Z"


def _make_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.row_factory = sqlite3.Row
    schema = (Path(__file__).parents[1] / "app" / "schema.sql").read_text(encoding="utf-8")
    schema = "\n".join(line for line in schema.splitlines() if "journal_mode" not in line.lower())
    con.executescript(schema)
    return con


def _set_config(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT OR REPLACE INTO config_values (id, key, value, created_at, updated_at, created_by)"
        " VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), key, value, _NOW, _NOW, "test"),
    )
    con.commit()


def _db_with_output(tmp_path: Path) -> sqlite3.Connection:
    con = _make_db()
    _set_config(con, "output_dir", str(tmp_path / "output"))
    return con


# ── build_outdir ──────────────────────────────────────────────────────────────


def test_build_outdir_basic() -> None:
    result = build_outdir("/mnt/d/output", "outputs_taxprofiler", "SAMPLE1", "RUN1")
    assert result == "/mnt/d/output/nanopore_processed/outputs_taxprofiler/SAMPLE1/RUN1"


def test_build_outdir_without_run_accession() -> None:
    result = build_outdir("/mnt/d/output", "outputs_taxprofiler", "SAMPLE1")
    assert result == "/mnt/d/output/nanopore_processed/outputs_taxprofiler/SAMPLE1"


def test_build_outdir_strips_trailing_slash() -> None:
    result = build_outdir("/mnt/d/output/", "subdir", "SAMPLE1")
    assert "//" not in result
    assert result.endswith("/subdir/SAMPLE1")


def test_build_outdir_custom_subdir() -> None:
    result = build_outdir("/base", "outputs_mpox", "ID")
    assert result.endswith("/outputs_mpox/ID")


# ── generate_databases_csv ────────────────────────────────────────────────────


def test_generate_databases_csv_empty_raises(tmp_path: Path) -> None:
    con = _make_db()
    with pytest.raises(ValueError, match="No database entries"):
        generate_databases_csv(con, tmp_path)


def test_generate_databases_csv_creates_file(tmp_path: Path) -> None:
    con = _make_db()
    con.execute(
        "INSERT INTO databases (id, tool, db_name, db_params, db_path, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), "kraken2", "PlusPF-8", "--quick", "/mnt/d/db/k2", _NOW, _NOW),
    )
    con.commit()

    csv_path = generate_databases_csv(con, tmp_path)
    assert csv_path.exists()

    with csv_path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert len(rows) == 1
    assert rows[0]["tool"] == "kraken2"
    assert rows[0]["db_name"] == "PlusPF-8"
    assert rows[0]["db_params"] == "--quick"


def test_generate_databases_csv_multiple_entries(tmp_path: Path) -> None:
    con = _make_db()
    for i, (tool, name) in enumerate([("kraken2", "db1"), ("diamond", "nr")]):
        con.execute(
            "INSERT INTO databases (id, tool, db_name, db_path, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), tool, name, f"/mnt/d/db/{i}", _NOW, _NOW),
        )
    con.commit()

    csv_path = generate_databases_csv(con, tmp_path)
    with csv_path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2


def test_generate_databases_csv_resolves_relative_db_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = _make_db()
    monkeypatch.setenv("ODIN_DATABASE_PATH", "/ODIN/databases")
    con.execute(
        "INSERT INTO databases (id, tool, db_name, db_params, db_path, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), "kraken2", "custom", "", "custom_pathogen_db", _NOW, _NOW),
    )
    con.commit()

    csv_path = generate_databases_csv(con, tmp_path)
    with csv_path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert rows[0]["db_path"] == "/ODIN/databases/custom_pathogen_db"


def test_generate_databases_csv_relative_db_path_without_base_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = _make_db()
    monkeypatch.delenv("ODIN_DATABASE_PATH", raising=False)
    monkeypatch.delenv("ODIN_PIPELINE_ROOT", raising=False)
    con.execute(
        "INSERT INTO databases (id, tool, db_name, db_params, db_path, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), "kraken2", "custom", "", "custom_pathogen_db", _NOW, _NOW),
    )
    con.commit()

    with pytest.raises(ValueError, match="ODIN_DATABASE_PATH"):
        generate_databases_csv(con, tmp_path)


# ── build_taxprofiler_cmd ─────────────────────────────────────────────────────


def test_taxprofiler_cmd_contains_required_flags(tmp_path: Path) -> None:
    con = _db_with_output(tmp_path)
    cmd, outdir = build_taxprofiler_cmd(con, "/samples.csv", "/databases.csv", "SAMPLE1")

    assert "nextflow" in cmd
    assert " run " in cmd
    assert "-log" in cmd
    assert "taxprofiler" in cmd
    assert "--input" in cmd
    assert "--databases" in cmd
    assert "--outdir" in cmd
    assert "--run_kraken2" in cmd
    assert "--run_krona" in cmd


def test_taxprofiler_cmd_includes_revision_by_default(tmp_path: Path) -> None:
    con = _db_with_output(tmp_path)
    cmd, _ = build_taxprofiler_cmd(con, "/s.csv", "/d.csv", "SAMPLE1")
    assert "-r " in cmd


def test_taxprofiler_cmd_with_profile(tmp_path: Path) -> None:
    con = _db_with_output(tmp_path)
    _set_config(con, "nextflow_profile", "odin")
    cmd, _ = build_taxprofiler_cmd(con, "/s.csv", "/d.csv", "SAMPLE1")
    assert "-profile odin" in cmd


def test_taxprofiler_cmd_local_dir_no_revision(tmp_path: Path) -> None:
    con = _db_with_output(tmp_path)
    _set_config(con, "taxprofiler_dir", str(tmp_path))
    cmd, _ = build_taxprofiler_cmd(con, "/s.csv", "/d.csv", "SAMPLE1")
    assert "-r " not in cmd


def test_taxprofiler_cmd_outdir_contains_file_identifier(tmp_path: Path) -> None:
    con = _db_with_output(tmp_path)
    _, outdir = build_taxprofiler_cmd(con, "/s.csv", "/d.csv", "MY_SAMPLE", "RUN_ACC")
    assert "MY_SAMPLE" in outdir
    assert "outputs_taxprofiler" in outdir
    # run_accession is NOT appended — it is already embedded in file_identifier by the caller
    assert outdir.index("outputs_taxprofiler") < outdir.index("MY_SAMPLE")


def test_taxprofiler_cmd_missing_output_dir_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ODIN_PIPELINE_ROOT", raising=False)
    con = _make_db()  # no output_dir configured
    with pytest.raises(ValueError, match="output_dir"):
        build_taxprofiler_cmd(con, "/s.csv", "/d.csv", "SAMPLE1")


# ── build_wf_metagenomics_amr_cmd ─────────────────────────────────────────────


def test_wf_metagenomics_amr_cmd_flags(tmp_path: Path) -> None:
    con = _db_with_output(tmp_path)
    cmd, outdir = build_wf_metagenomics_amr_cmd(con, "/fastq", "SAMPLE1")

    assert "wf-metagenomics" in cmd
    assert "--fastq" in cmd
    assert "--amr" in cmd
    assert "SAMPLE1" in outdir
    assert "outputs_wf_metagenomics_amr" in outdir


def test_wf_metagenomics_amr_default_database_set(tmp_path: Path) -> None:
    con = _db_with_output(tmp_path)
    cmd, _ = build_wf_metagenomics_amr_cmd(con, "/fastq", "SAMPLE1")
    assert "PlusPF-8" in cmd


def test_wf_metagenomics_amr_custom_database_set(tmp_path: Path) -> None:
    con = _db_with_output(tmp_path)
    _set_config(con, "database_set_amr", "PlusPFP-8")
    cmd, _ = build_wf_metagenomics_amr_cmd(con, "/fastq", "SAMPLE1")
    assert "PlusPFP-8" in cmd


# ── build_wf_metagenomics_ssu_cmd ─────────────────────────────────────────────


def test_wf_metagenomics_ssu_cmd_flags(tmp_path: Path) -> None:
    con = _db_with_output(tmp_path)
    cmd, outdir = build_wf_metagenomics_ssu_cmd(con, "/fastq", "SAMPLE1")

    assert "wf-metagenomics" in cmd
    assert "--fastq" in cmd
    assert "SAMPLE1" in outdir
    assert "outputs_wf_metagenomics_ssu" in outdir


def test_wf_metagenomics_ssu_no_amr_flag(tmp_path: Path) -> None:
    con = _db_with_output(tmp_path)
    cmd, _ = build_wf_metagenomics_ssu_cmd(con, "/fastq", "SAMPLE1")
    assert "--amr" not in cmd


def test_wf_metagenomics_ssu_default_database_set(tmp_path: Path) -> None:
    con = _db_with_output(tmp_path)
    cmd, _ = build_wf_metagenomics_ssu_cmd(con, "/fastq", "SAMPLE1")
    assert "SILVA_138_1" in cmd


# ── build_mpox_cmd ────────────────────────────────────────────────────────────


def test_mpox_cmd_returns_three_values(tmp_path: Path) -> None:
    con = _db_with_output(tmp_path)
    result = build_mpox_cmd(con, "/s.csv", "/fastq", "ERR1", "cladeii", "artic/v1")
    assert len(result) == 3


def test_mpox_cmd_nextflow_part(tmp_path: Path) -> None:
    con = _db_with_output(tmp_path)
    nf_cmd, squirrel_cmd, outdir = build_mpox_cmd(
        con, "/s.csv", "/fastq", "ERR1", "cladeii", "artic-inrb-mpox/2500/v1.0.0"
    )

    assert "artic-mpxv-nf" in nf_cmd
    assert "--clade" in nf_cmd
    assert "cladeii" in nf_cmd
    assert "--scheme_version" in nf_cmd
    assert "--fastq" in nf_cmd
    assert "--sample_sheet" in nf_cmd


def test_mpox_cmd_squirrel_part(tmp_path: Path) -> None:
    con = _db_with_output(tmp_path)
    nf_cmd, squirrel_cmd, outdir = build_mpox_cmd(
        con, "/s.csv", "/fastq", "ERR1", "cladeii", "artic/v1"
    )
    assert "squirrel" in squirrel_cmd
    assert "--run-apobec3-phylo" in squirrel_cmd


def test_mpox_cmd_outdir_contains_run_accession(tmp_path: Path) -> None:
    con = _db_with_output(tmp_path)
    _, _, outdir = build_mpox_cmd(con, "/s.csv", "/fastq", "ERR999", "cladeii", "artic/v1")
    assert "ERR999" in outdir


# ── bundled/seeded taxprofiler checkout ───────────────────────────────────────


def _seed_checkout(root: Path, revision: str = "1.2.6") -> Path:
    checkout = root / "nf" / f"taxprofiler-{revision}"
    checkout.mkdir(parents=True)
    (checkout / "main.nf").write_text("// workflow")
    return checkout


def test_taxprofiler_cmd_prefers_seeded_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = _db_with_output(tmp_path)
    monkeypatch.setenv("ODIN_PIPELINE_ROOT", str(tmp_path))
    monkeypatch.delenv("TAXPROFILER_DIR", raising=False)
    _seed_checkout(tmp_path)
    cmd, _ = build_taxprofiler_cmd(con, "/s.csv", "/d.csv", "SAMPLE1")
    assert "taxprofiler-1.2.6" in cmd
    assert "-r " not in cmd
    assert "nf-core/taxprofiler" not in cmd


def test_taxprofiler_cmd_explicit_dir_wins_over_seeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = _db_with_output(tmp_path)
    monkeypatch.setenv("ODIN_PIPELINE_ROOT", str(tmp_path))
    _seed_checkout(tmp_path)
    _set_config(con, "taxprofiler_dir", str(tmp_path / "vendored"))
    cmd, _ = build_taxprofiler_cmd(con, "/s.csv", "/d.csv", "SAMPLE1")
    assert "vendored" in cmd
    assert "taxprofiler-1.2.6" not in cmd


def test_taxprofiler_cmd_revision_override_misses_seeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TAXPROFILER_REVISION the image did not bundle falls back to GitHub."""
    con = _db_with_output(tmp_path)
    monkeypatch.setenv("ODIN_PIPELINE_ROOT", str(tmp_path))
    monkeypatch.setenv("TAXPROFILER_REVISION", "9.9.9")
    _seed_checkout(tmp_path, "1.2.6")
    cmd, _ = build_taxprofiler_cmd(con, "/s.csv", "/d.csv", "SAMPLE1")
    assert "nf-core/taxprofiler" in cmd
    assert "-r 9.9.9" in cmd


# ── executor subprocess env defaults ──────────────────────────────────────────


def test_job_env_defaults_nxf_assets_from_root(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.pipeline.executor import Job, _job_env

    monkeypatch.setenv("ODIN_PIPELINE_ROOT", "/mnt/i/pipeline")
    monkeypatch.delenv("NXF_ASSETS", raising=False)
    job = Job(run_id="x", cmd="true", log_path=Path("/tmp/x.log"), db_path="/tmp/x.db")
    env = _job_env(job)
    assert env["NXF_ASSETS"] == "/mnt/i/pipeline/nf/assets"


def test_job_env_respects_explicit_nxf_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.pipeline.executor import Job, _job_env

    monkeypatch.setenv("ODIN_PIPELINE_ROOT", "/mnt/i/pipeline")
    monkeypatch.setenv("NXF_ASSETS", "/elsewhere/assets")
    job = Job(run_id="x", cmd="true", log_path=Path("/tmp/x.log"), db_path="/tmp/x.db")
    assert _job_env(Job(run_id="x", cmd="true", log_path=Path("/tmp/x.log"), db_path="/tmp/x.db"))["NXF_ASSETS"] == "/elsewhere/assets"


def test_store_dir_defaults_to_nf_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.pipeline.command_builder import _store_dir

    monkeypatch.setenv("ODIN_PIPELINE_ROOT", "/mnt/i/pipeline")
    monkeypatch.delenv("ODIN_STORE_DIR", raising=False)
    con = _make_db()
    assert _store_dir(con, "/out") == "/mnt/i/pipeline/nf/store"
