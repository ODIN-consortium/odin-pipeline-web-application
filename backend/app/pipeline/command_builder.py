"""
Nextflow / pipeline command builder.

Reads config_values from the DB and constructs shell command strings for each
pipeline type.  Returns a plain string that executor.wrap_cmd() will execute
in the appropriate environment.

Pipeline types
--------------
  taxprofiler          — nf-core/taxprofiler
  wf_metagenomics_amr  — epi2me-labs/wf-metagenomics (--amr)
  wf_metagenomics_ssu  — epi2me-labs/wf-metagenomics (SSU / Silva)
  mpox                 — artic-network/artic-mpxv-nf  + squirrel

Config keys (all stored in config_values table)
------
  output_dir             — root output directory
  minknow_dir            — MinKNOW data directory
  databases_file         — path to databases.csv (taxprofiler)
  pathogens_file         — path to pathogens Excel file (for Enlighten datasets)
  enlighten_data_path    — Feather output path for Enlighten
  database_set_amr       — database set for AMR pipeline (default: PlusPF-8)
  amr_db                 — AMR database (default: card)
  database_set_ssu       — database set for SSU pipeline (default: SILVA_138_1)
  mpox_default_clade     — default clade (default: cladeii, matching squirrel's own default)
  mpox_default_scheme    — default scheme version (default: artic-inrb-mpox/2500/v1.0.0)
  store_dir              — nextflow store_dir for caching (mpox amplicon schemes, wf-metagenomics models/databases);
                           defaults to {output_dir}/store_dir when not set
  nextflow_profile       — Nextflow -profile value (default: odin)
  nextflow_config_file   — path to odin.config

Environment variable overrides (take precedence over DB settings)
-----------------------------------------------------------------
  NF_BIN                 — path to the nextflow executable (default: "nextflow" on PATH)
  TAXPROFILER_DIR        — path to a local nf-core/taxprofiler checkout (default: pull from GitHub)
  TAXPROFILER_REVISION   — GitHub revision/tag to use when pulling taxprofiler (default: 1.2.6)
  NEXTFLOW_CONFIG_FILE   — path to odin.config; fallback when nextflow_config_file is not set in DB
  ODIN_STORE_DIR         — override store_dir for all pipelines (wf-metagenomics, mpox);
                           set this to a fast named-volume path in Docker, or a local path for dev;
                           takes precedence over the store_dir DB config value
  ODIN_WORK_DIR          — override the Nextflow work directory base for all pipelines;
                           set this to a WSL2-native Linux path (e.g. /home/<user>/odin_work) to
                           avoid v9fs getcwd() failures when Docker containers run on a Windows
                           filesystem mount; each run gets its own subdirectory under this base
  ODIN_LOG_DIR           — override the pipeline_logs directory for all log files;
                           when set, both ODIN streaming logs (.log) and the Nextflow rolling log
                           (.nextflow.log) are written here instead of {output_dir}/pipeline_logs;
                           useful in dev: set to a local repo path so logs are always accessible
  SQUIRREL_IMAGE         — squirrel Docker image for mpox post-processing;
                           defaults to articnetworkorg/squirrel:1.3.2
"""

from __future__ import annotations

import csv
import os
import re
import shlex
import sqlite3
import sys
from pathlib import Path

from ..settings_resolver import lookup_setting, resolve_setting_value
from ..utils import coerce_path, coerce_path_for_shell, normalize_for_storage

# ── Config helpers ────────────────────────────────────────────────────────────


def _get(db: sqlite3.Connection, key: str, default: str = "") -> str:
    """Resolve a setting in shell-path form, falling back to *default*.

    The shell-path twin of `lookup_setting` over the same
    `resolve_setting_value` chain, differing only where it must:
    `coerce_path_for_shell` because these values are embedded in Nextflow commands
    that run through the execution shell, a caller-supplied default instead of None,
    and treating a blank stored value as "not configured" so command building still
    gets a usable path.
    """
    value = resolve_setting_value(db, key)
    return coerce_path_for_shell(value) if value else default


def _sp(path) -> str:
    """Convert any path (Windows, Git Bash, WSL, or Python Path) to shell-ready form."""
    return coerce_path_for_shell(normalize_for_storage(str(path)))


def _q(s: str) -> str:
    """Shell-quote a value using POSIX single-quote escaping (shlex.quote)."""
    return shlex.quote(str(s))


def _require(db: sqlite3.Connection, key: str) -> str:
    v = _get(db, key)
    if not v:
        raise ValueError(
            f"Required setting '{key}' is not configured. "
            f"Please set it in the Settings page before launching."
        )
    return v


def build_outdir(output_dir: str, subdir: str, file_identifier: str, run_accession: str = "") -> str:
    """
    Compute the Nextflow output directory path.

    Layout (matches pipeline_scripts convention):
      {output_dir}/nanopore_processed/{subdir}/{file_identifier}

    ``file_identifier`` is the sampleName from the run accession metadata.
    ``run_accession`` is only used by mpox/squirrel pipelines that pass the
    run_accession directly as file_identifier.

    Uses string concatenation (not pathlib.Path) so that WSL-format paths like
    /mnt/d/... are preserved on Windows instead of being reinterpreted as
    Windows relative paths.
    """
    base = output_dir.rstrip("/").rstrip("\\")
    result = f"{base}/nanopore_processed/{subdir}/{file_identifier}"
    if run_accession:
        result += f"/{run_accession}"
    return result


# Keep private alias for internal callers
_build_outdir = build_outdir


def autodiscover_databases() -> list[dict]:
    """Scan ODIN_DATABASE_PATH (or $ODIN_PIPELINE_ROOT/databases) for Kraken2 databases.

    A subdirectory is recognised as a database if it contains ``hash.k2d``.
    For each discovered database a ``kraken2`` row is emitted, plus a ``bracken``
    row if a ``*.kmer_distrib`` file is present.

    Returns an empty list if the databases directory does not exist or contains
    no valid database subdirectories.
    """
    db_base = os.environ.get("ODIN_DATABASE_PATH", "").strip()
    if not db_base:
        # ODIN_PIPELINE_ROOT has no default: without it there is no databases
        # directory to discover, so report none rather than guessing a path.
        pipeline_root = os.environ.get("ODIN_PIPELINE_ROOT", "").strip()
        if not pipeline_root:
            return []
        db_base = pipeline_root.rstrip("/").rstrip("\\") + "/databases"

    # Native coercion: this path is scanned with pathlib on THIS platform.
    # coerce_path_for_shell produced a Git-Bash-style path that pathlib cannot
    # stat on native Windows, silently disabling auto-discovery there.
    db_base_path = Path(coerce_path(db_base))
    if not db_base_path.is_dir():
        return []

    rows: list[dict] = []
    for subdir in sorted(db_base_path.iterdir()):
        if not subdir.is_dir():
            continue
        if not (subdir / "hash.k2d").exists():
            continue
        db_name = subdir.name
        # Use the subdirectory name as db_path (relative — resolved at write time)
        rows.append({"tool": "kraken2", "db_name": db_name, "db_params": "--quick", "db_path": db_name})
        kmer_files = list(subdir.glob("*.kmer_distrib"))
        if kmer_files:
            rows.append({"tool": "bracken", "db_name": db_name, "db_params": ";-r 150", "db_path": db_name})

    return rows


def generate_databases_csv(db: sqlite3.Connection, tmp_dir: Path) -> Path:
    """
    Write a taxprofiler-compatible databases.csv to ``tmp_dir``.

    Source priority:
      1. File pointed to by ``databases_file`` setting (env var ODIN_DATABASES_FILE
         or DB setting, defaulting to ``{ODIN_PIPELINE_ROOT}/input_sheets/databases.csv``).
         Rows are read directly from this CSV — useful for managing databases outside
         the ODIN UI.
      2. Entries stored in the ``databases`` DB table (added via the Databases page).

    In both cases relative db_path values are resolved against ODIN_DATABASE_PATH,
    and all paths are coerced to the correct format for the execution environment.
    """
    # ── Prefer file-based source if available ────────────────────────────────
    databases_file_path = lookup_setting(db, "databases_file")
    source_rows: list[dict] = []

    if databases_file_path:
        native = coerce_path(databases_file_path)
        if native and Path(native).exists():
            with open(native, newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    tool = (row.get("tool") or "").strip()
                    if not tool or tool.startswith("#"):
                        continue
                    source_rows.append({
                        "tool":      tool,
                        "db_name":   (row.get("db_name") or "").strip(),
                        "db_params": (row.get("db_params") or "").strip(),
                        "db_path":   (row.get("db_path") or "").strip(),
                    })

    # ── Fall back to DB entries ───────────────────────────────────────────────
    if not source_rows:
        db_rows = db.execute(
            "SELECT tool, db_name, db_params, db_path FROM databases ORDER BY tool, db_name"
        ).fetchall()
        source_rows = [
            {"tool": r["tool"], "db_name": r["db_name"],
             "db_params": r["db_params"] or "", "db_path": r["db_path"] or ""}
            for r in db_rows
        ]

    # ── Auto-discover databases from the databases directory ─────────────────
    if not source_rows:
        source_rows = autodiscover_databases()

    if not source_rows:
        raise ValueError(
            "No database entries are configured. "
            "Either create a databases.csv at the default location "
            "({ODIN_PIPELINE_ROOT}/input_sheets/databases.csv), set ODIN_DATABASES_FILE, "
            "or add entries on the Databases page before launching taxprofiler."
        )

    # ── Write resolved CSV ────────────────────────────────────────────────────
    csv_path = tmp_dir / "databases.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["tool", "db_name", "db_params", "db_path"])
        writer.writeheader()
        for row in source_rows:
            db_path_raw = row["db_path"].strip()
            db_path_resolved = db_path_raw

            # Allow user-friendly relative entries (e.g. "pathogen_combined") by
            # resolving them against ODIN_DATABASE_PATH at runtime.
            is_windows_abs = bool(re.match(r"^[A-Za-z]:[\\/]", db_path_raw))
            is_unix_abs = db_path_raw.startswith("/")
            if db_path_raw and not is_windows_abs and not is_unix_abs:
                db_base = os.environ.get("ODIN_DATABASE_PATH", "").strip()
                if not db_base:
                    # Derive default from ODIN_PIPELINE_ROOT/databases
                    pipeline_root = os.environ.get("ODIN_PIPELINE_ROOT", "").strip()
                    if pipeline_root:
                        db_base = pipeline_root.rstrip("/").rstrip("\\") + "/databases"
                if not db_base:
                    raise ValueError(
                        "Database path is relative but neither ODIN_DATABASE_PATH nor "
                        "ODIN_PIPELINE_ROOT is set. Set ODIN_DATABASE_PATH or use an absolute db_path."
                    )
                db_path_resolved = (
                    f"{db_base.rstrip('/').rstrip('\\')}"
                    f"/{db_path_raw.lstrip('/').lstrip('\\')}"
                )

            writer.writerow({
                "tool":      row["tool"],
                "db_name":   row["db_name"],
                "db_params": row["db_params"],
                "db_path":   coerce_path_for_shell(db_path_resolved),
            })
    return csv_path


def _nextflow(db: sqlite3.Connection) -> str:
    """Return the nextflow binary path from NF_BIN env var, defaulting to 'nextflow' on PATH."""
    return os.environ.get("NF_BIN") or "nextflow"


def _env_exports() -> str:
    """Return a shell prefix that explicitly exports ODIN pipeline env vars.

    On Windows the backend spawns Nextflow via ``wsl bash -lc "..."``.  Windows
    environment variables do NOT automatically propagate into the WSL bash
    login-shell process, so env vars read from ``.env`` by ``uv run`` never
    reach the Nextflow JVM.  Prepending explicit ``export`` statements to the
    command string is the only reliable cross-platform fix.

    Currently exported:
      NXF_ANSI_LOG — always false; disables ANSI escape codes in log output.
                     Env-var form is used because it works across all Nextflow versions.
      ODIN_CA_CERT — path to the corporate CA cert; odin.config reads this via
                     System.getenv() to build docker.runOptions and beforeScript.
      ODIN_TMP_DIR — temporary directory for pre-Nextflow FASTQ concatenation;
                     odin.config mounts it into containers via docker.runOptions.
    """
    exports: list[str] = ["export NXF_ANSI_LOG=false"]
    ca = os.environ.get("ODIN_CA_CERT", "").strip()
    if ca:
        exports.append(f"export ODIN_CA_CERT={shlex.quote(ca)}")
    tmp = os.environ.get("ODIN_TMP_DIR", "").strip()
    if tmp:
        exports.append(f"export ODIN_TMP_DIR={shlex.quote(tmp)}")
    return " && ".join(exports) + " && "


_DEFAULT_SQUIRREL_IMAGE = "articnetworkorg/squirrel:1.3.2"


def _squirrel_docker_cmd(consensus_fasta: str, squirrel_outdir: str, clade: str) -> str:
    """Build a `docker run` command for squirrel via DooD."""
    image = os.environ.get("SQUIRREL_IMAGE") or _DEFAULT_SQUIRREL_IMAGE
    artic_outdir = consensus_fasta.rsplit("/", 1)[0]
    tmp = os.environ.get("ODIN_TMP_DIR", "").strip()
    tmp_opts = f" -v {_q(tmp)}:{_q(tmp)}" if tmp else ""
    tmp_arg = f" --tempdir {_q(tmp)}" if tmp else ""
    return (
        f"docker run --rm"
        f" -v {_q(artic_outdir)}:{_q(artic_outdir)}:ro"
        f" -v {_q(squirrel_outdir)}:{_q(squirrel_outdir)}"
        f"{tmp_opts}"
        f" {shlex.quote(image)}"
        f" squirrel {_q(consensus_fasta)}"
        f" --clade {_q(clade)}"
        f" --run-apobec3-phylo --include-background"
        f" --outdir {_q(squirrel_outdir)}"
        f"{tmp_arg}"
    )


def _store_dir(db: sqlite3.Connection, output_dir: str) -> str:
    """Resolve the store_dir path.

    Priority:
      1. ODIN_STORE_DIR env var  — use a named Docker volume or a local dev path
      2. store_dir DB config value
      3. {ODIN_PIPELINE_ROOT}/nf/store  — the documented layout; this default
         used to be injected by docker-compose, but podman-compose cannot
         expand variables inside ${VAR:-...} fallbacks, so the code owns it
         now (was {ODIN_PIPELINE_ROOT}/.odin-app/store_dir before compose
         shadowed it).
      4. {output_dir}/store_dir  — last resort when ODIN_PIPELINE_ROOT is not set
    """
    env = os.environ.get("ODIN_STORE_DIR", "").strip()
    if env:
        return env
    db_val = _get(db, "store_dir").strip()
    if db_val:
        return db_val
    pipeline_root = os.environ.get("ODIN_PIPELINE_ROOT", "").strip()
    if pipeline_root:
        return coerce_path_for_shell(pipeline_root).rstrip("/") + "/nf/store"
    return output_dir.rstrip("/").rstrip("\\") + "/store_dir"


def _work_dir(outdir: str) -> str:
    """Resolve the Nextflow work directory path.

    Priority:
      1. ODIN_WORK_DIR env var — WSL2-native Linux path to avoid v9fs getcwd() failures;
                                  identifier (sampleName_runAccession) appended as subfolder
      2. {nanopore_processed}/work/{identifier}/ — sibling to outputs_* dirs so that
         recursive globs in post-processors never pick up Nextflow task intermediates

    The work dir is intentionally kept outside the pipeline output directory.
    """
    # identifier = last component of outdir, e.g. "DemoSample1_20260610_..."
    identifier = outdir.rstrip("/").rsplit("/", 1)[-1]
    base = os.environ.get("ODIN_WORK_DIR", "").strip()
    if base:
        return base.rstrip("/").rstrip("\\") + "/" + identifier + "/work"
    # outdir = .../nanopore_processed/outputs_taxprofiler/{identifier}
    # go up two levels to nanopore_processed, then into work/
    nanopore_processed = outdir.rstrip("/").rsplit("/", 2)[0]
    return nanopore_processed + "/work/" + identifier


def resolve_work_dir(db: sqlite3.Connection, subdir: str, file_identifier: str) -> str:
    """Return the Nextflow work directory path for a given run (public API).

    Used by pipeline.py to clear the work dir before a fresh (non-resume) launch
    so stale task directories with dangling symlinks don't cause failures.
    """
    output_dir = _get(db, "output_dir")
    outdir = _build_outdir(output_dir, subdir, file_identifier)
    return coerce_path_for_shell(_work_dir(outdir))


def _nf_log_path(output_dir: str) -> str:
    """Shell-format path for the shared Nextflow rolling log file.

    Priority:
      1. ODIN_LOG_DIR env var — use a local/repo path for easy access in dev
      2. {output_dir}/pipeline_logs — default, alongside the ODIN streaming logs

    The directory is pre-created by prepare_fn in pipeline.py before any
    command runs, so the log file is always on the mounted volume rather than
    inside the container.
    """
    env = os.environ.get("ODIN_LOG_DIR", "").strip()
    if env:
        return coerce_path_for_shell(env) + "/.nextflow.log"
    return output_dir.rstrip("/").rstrip("\\") + "/pipeline_logs/.nextflow.log"


# ── Per-pipeline command builders ─────────────────────────────────────────────


def _base_nextflow_parts(
    db: sqlite3.Connection, run_target: str, outdir: str, work_dir: str, nf_log: str
) -> list[str]:
    """Leading parts common to every ``nextflow run`` command: the run invocation
    plus the trace / report / work-dir flags. Callers append their pipeline-specific
    ``--flags`` and then pass the list to :func:`_finalize`."""
    return [
        f"{_nextflow(db)} -log {_q(nf_log)} run {run_target}",
        f"-with-trace {_q(outdir + '/trace.txt')}",
        f"-with-report {_q(outdir + '/report.html')}",
        f"-work-dir {_q(work_dir)}",
    ]


def _finalize(parts: list[str], profile: str, config_file: str) -> str:
    """Insert ``-profile`` (right after the run invocation) and append ``-c config``,
    then join into the final env-prefixed command string."""
    if profile:
        parts.insert(1, f"-profile {profile}")
    if config_file:
        parts.append(f"-c {_q(config_file)}")
    return _env_exports() + " ".join(parts)


def _seeded_taxprofiler_checkout(revision: str) -> str:
    """The bundled taxprofiler checkout seeded by the container entrypoint, if present.

    The image carries the pinned workflow source and the entrypoint copies it to
    ``$ODIN_PIPELINE_ROOT/nf/taxprofiler-<revision>`` — on the host, because
    Nextflow bind-mounts pipeline paths (bin/) into sibling task containers that
    resolve paths against the host filesystem.  Version-suffixed, so overriding
    TAXPROFILER_REVISION to an unbundled version simply misses here and falls
    back to the GitHub pull.  Returns "" when not seeded (e.g. bare Windows dev
    runs, or older images).
    """
    root = os.environ.get("ODIN_PIPELINE_ROOT", "").strip()
    if not root:
        return ""
    checkout = Path(coerce_path(root)) / "nf" / f"taxprofiler-{revision}"
    return str(checkout) if (checkout / "main.nf").is_file() else ""


def build_taxprofiler_cmd(
    db: sqlite3.Connection,
    samples_csv_path: str,
    databases_csv_path: str,
    file_identifier: str,
    run_accession: str = "",
    pipeline_options: dict | None = None,
) -> tuple[str, str]:
    """
    Returns (nextflow_cmd_string, outdir).

    ``samples_csv_path``   — samplesheet built by samplesheet.build_taxprofiler_samplesheet()
    ``databases_csv_path`` — databases CSV built by generate_databases_csv()
    ``run_accession``      — used to isolate the Nextflow work dir per sequencing event;
                             does not affect the output directory.
    """
    output_dir = _require(db, "output_dir")
    taxprofiler_dir = _get(db, "taxprofiler_dir") or os.environ.get("TAXPROFILER_DIR") or ""
    profile = _get(db, "nextflow_profile", "odin")
    config_file = _get(db, "nextflow_config_file")

    # Resolve workflow reference: explicit local path → seeded bundled checkout
    # → hub name.  Pin to a specific release tag when pulling from GitHub to
    # avoid regressions introduced on the main branch (e.g. missing
    # multiqc_config.yml staging in 2.0.0).  Update this when a new stable
    # release is verified.
    TAXPROFILER_REVISION = os.environ.get("TAXPROFILER_REVISION", "1.2.6")
    bundled_dir = "" if taxprofiler_dir else _seeded_taxprofiler_checkout(TAXPROFILER_REVISION)
    if taxprofiler_dir:
        workflow_ref = str(Path(taxprofiler_dir) / "taxprofiler")
        revision_flag = ""
    elif bundled_dir:
        workflow_ref = bundled_dir
        revision_flag = ""
    else:
        workflow_ref = "nf-core/taxprofiler"
        revision_flag = f"-r {TAXPROFILER_REVISION}"

    outdir = _build_outdir(output_dir, "outputs_taxprofiler", file_identifier)
    work_dir = _work_dir(outdir)
    nf_log = _nf_log_path(output_dir)
    is_local_ref = bool(taxprofiler_dir or bundled_dir)
    run_target = _q(_sp(workflow_ref) if is_local_ref else workflow_ref)

    parts = _base_nextflow_parts(db, run_target, outdir, work_dir, nf_log)
    if revision_flag:
        parts.append(revision_flag)
    parts += [
        f"--input {_q(_sp(samples_csv_path))}",
        f"--databases {_q(_sp(databases_csv_path))}",
        f"--outdir {_q(outdir)}",
        "--run_kraken2",
        "--run_krona",
    ]
    opts = pipeline_options or {}
    if opts.get("save_reads"):
        parts.append("--kraken2_save_reads --kraken2_save_readclassifications")

    return _finalize(parts, profile, config_file), outdir


def build_wf_metagenomics_amr_cmd(
    db: sqlite3.Connection,
    fastq_dir: str,
    file_identifier: str,
    run_accession: str = "",
) -> tuple[str, str]:
    output_dir = _require(db, "output_dir")
    database_set = _get(db, "database_set_amr", "PlusPF-8")
    amr_db = _get(db, "amr_db", "card")
    profile = _get(db, "nextflow_profile", "odin_epi2me")
    config_file = _get(db, "nextflow_config_file")
    store_dir = _store_dir(db, output_dir)

    outdir = _build_outdir(output_dir, "outputs_wf_metagenomics_amr", file_identifier)
    work_dir = _work_dir(outdir)
    nf_log = _nf_log_path(output_dir)

    parts = _base_nextflow_parts(db, "epi2me-labs/wf-metagenomics", outdir, work_dir, nf_log)
    parts += [
        f"--fastq {_q(_sp(fastq_dir))}",
        f"--database_set {_q(database_set)}",
        f"--store_dir {_q(store_dir)}",
        f"--amr --amr_db {_q(amr_db)}",
        f"--out_dir {_q(outdir)}",
    ]
    return _finalize(parts, profile, config_file), outdir


def build_wf_metagenomics_ssu_cmd(
    db: sqlite3.Connection,
    fastq_dir: str,
    file_identifier: str,
    run_accession: str = "",
) -> tuple[str, str]:
    output_dir = _require(db, "output_dir")
    database_set = _get(db, "database_set_ssu", "SILVA_138_1")
    profile = _get(db, "nextflow_profile", "odin_epi2me")
    config_file = _get(db, "nextflow_config_file")
    store_dir = _store_dir(db, output_dir)

    outdir = _build_outdir(output_dir, "outputs_wf_metagenomics_ssu", file_identifier)
    work_dir = _work_dir(outdir)
    nf_log = _nf_log_path(output_dir)

    parts = _base_nextflow_parts(db, "epi2me-labs/wf-metagenomics", outdir, work_dir, nf_log)
    parts += [
        f"--fastq {_q(_sp(fastq_dir))}",
        f"--database_set {_q(database_set)}",
        f"--store_dir {_q(store_dir)}",
        f"--out_dir {_q(outdir)}",
    ]
    return _finalize(parts, profile, config_file), outdir


def build_mpox_cmd(
    db: sqlite3.Connection,
    csv_path: str,
    fastq_dir: str,
    run_accession: str,
    clade: str,
    scheme_version: str,
) -> tuple[str, str, str]:
    """
    Returns (nextflow_cmd, squirrel_cmd, outdir).
    squirrel_cmd is the second step — caller chains them.
    """
    output_dir = _require(db, "output_dir")
    store_dir = _store_dir(db, output_dir)
    profile = _get(db, "nextflow_profile", "odin_epi2me")
    config_file = _get(db, "nextflow_config_file")

    artic_outdir = _build_outdir(output_dir, "outputs_wf_artic-mpxv-nf", run_accession)
    squirrel_outdir = _build_outdir(output_dir, "output_squirrel", run_accession)
    work_dir = _work_dir(artic_outdir)
    nf_log = _nf_log_path(output_dir)

    nf_parts = _base_nextflow_parts(db, "artic-network/artic-mpxv-nf", artic_outdir, work_dir, nf_log)
    nf_parts += [
        f"--fastq {_q(_sp(fastq_dir))}",
        f"--sample_sheet {_q(_sp(csv_path))}",
        f"--clade {_q(clade)}",
        f"--store_dir {_q(store_dir)}",
        f"--scheme_version {_q(scheme_version)}",
        f"--out_dir {_q(artic_outdir)}",
    ]
    nextflow_cmd = _finalize(nf_parts, profile, config_file)

    # Squirrel is run after nextflow succeeds; the consensus FASTA is at a known path
    consensus_fasta = f"{artic_outdir}/all_consensus.fasta"
    squirrel_cmd = _squirrel_docker_cmd(consensus_fasta, squirrel_outdir, clade)

    return nextflow_cmd, squirrel_cmd, artic_outdir


def build_squirrel_cmd(
    db: sqlite3.Connection,
    artic_outdir: str,
    run_accession: str,
    clade: str,
) -> tuple[str, str]:
    """
    Returns (squirrel_docker_cmd, squirrel_outdir).

    artic_outdir  — path to an existing artic-mpxv-nf output directory
                    that contains all_consensus.fasta
    run_accession — used to build the squirrel output path
    clade         — mpox clade; determines the reference and background set
    """
    output_dir = _require(db, "output_dir")
    squirrel_outdir = _build_outdir(output_dir, "output_squirrel", run_accession)
    consensus_fasta = f"{artic_outdir}/all_consensus.fasta"
    return _squirrel_docker_cmd(consensus_fasta, squirrel_outdir, clade), squirrel_outdir


def build_biomeme_cmd(db: sqlite3.Connection, db_path: str) -> tuple[list[str], dict[str, str]]:
    """Build the command to run the biomeme processing script.

    Returns ``(cmd, extra_env)`` where ``cmd`` is a list ready for
    ``subprocess.Popen`` / ``executor.launch`` and ``extra_env`` is a dict of
    environment variable overrides to pass to the subprocess (e.g.
    ``ODIN_ASSAY_PARAMS_PATH``).

    Args:
        db:      Open settings DB connection.
        db_path: Path to odin.db (passed as --db-path to the script).

    Raises:
        ValueError: If required settings are missing or assay-params.csv cannot be found.
    """
    data_root = lookup_setting(db, "biomeme_dir")
    if not data_root:
        raise ValueError(
            "Required setting 'biomeme_dir' is not configured. "
            "Please set 'Biomeme data root directory' in Settings."
        )
    enlighten_path = _require(db, "enlighten_data_path")

    # Locate assay-params.csv (priority: ODIN_ASSAY_PARAMS_PATH env var →
    # $ODIN_PIPELINE_ROOT/config/ → 'assay_params_path' setting in DB).
    assay_params_path: str | None = (
        os.environ.get("ODIN_ASSAY_PARAMS_PATH") or None
    )
    if not assay_params_path:
        _pipeline_root = os.environ.get("ODIN_PIPELINE_ROOT", "")
        if _pipeline_root:
            _candidate = Path(coerce_path(_pipeline_root)) / "config" / "assay-params.csv"
            if _candidate.exists():
                assay_params_path = str(_candidate)
    if not assay_params_path:
        assay_params_path = lookup_setting(db, "assay_params_path") or None
    if not assay_params_path:
        raise ValueError(
            "Cannot locate assay-params.csv. "
            "Set ODIN_ASSAY_PARAMS_PATH in .env, "
            "place the file at $ODIN_PIPELINE_ROOT/config/assay-params.csv, "
            "or set 'assay_params_path' in Settings."
        )

    cmd = [
        sys.executable,
        "-m", "backend.app.pipeline.scripts.traverse_biomeme",
        "--data-root", coerce_path(data_root),
        "--db-path", coerce_path(db_path),
        "--enlighten-data-path", coerce_path(enlighten_path),
    ]
    extra_env = {"ODIN_ASSAY_PARAMS_PATH": assay_params_path}
    return cmd, extra_env
