"""
Platform utilities for path normalization.
"""

import os
import platform
import re
from datetime import datetime, timezone
from pathlib import Path

# Matches Windows absolute paths: D:\path  or  D:/path
_WIN_PATH_RE = re.compile(r"^([A-Za-z]):[/\\](.*)", re.DOTALL)
# Matches Linux Git Bash / Docker mount paths: /d/path  (single letter, not /mnt/)
_LINUX_GITBASH_RE = re.compile(r"^/([a-z])/(.+)", re.DOTALL)
# Matches WSL2 / Docker Desktop mount paths: /mnt/d/path
_WSL_PATH_RE = re.compile(r"^/mnt/([a-z])/(.+)", re.DOTALL)


def _detect_linux_convention() -> str:
    """
    Determine which Linux path convention is in use, called once at import time.

    WSL2 sets ``WSL_DISTRO_NAME`` automatically (e.g. ``"Ubuntu"``).
    Containers mount Windows drives at ``/mnt/<drive>/...`` — the same layout
    as WSL2 — so they share the same path convention. Docker marks its
    containers with ``/.dockerenv``; Podman does NOT create that file — it
    creates ``/run/.containerenv`` (and sets ``container=podman``). Missing the
    Podman marker silently downgraded coerce_path() to a no-op, which broke
    post-processing and the output-overwrite guard under rootless Podman.
    Native Linux (e.g. CI runners) uses Git Bash / Docker volume convention
    (``/d/path``).

    Returns ``"wsl2"`` or ``"gitbash"``.
    """
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return "wsl2"
    # Docker Desktop on Windows mounts drives at /mnt/<drive> — same as WSL2.
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        return "wsl2"
    return "gitbash"


# Computed once at startup — no per-call filesystem probing needed.
_LINUX_CONVENTION: str = _detect_linux_convention()


def coerce_path_for_shell(path) -> str:
    """
    Convert any path form to the format expected by the *execution shell*.

    When the backend runs on Windows, pipelines run via ``wsl bash -lc``, so
    every path that appears in a shell command (or in files that the shell
    command reads, e.g. samplesheet CSVs) must be in WSL format
    (``/mnt/d/...``).

    On all other platforms the behaviour is identical to ``coerce_path``.

    Accepts Windows paths (``D:\\...`` or ``D:/...``), Git Bash paths
    (``/d/...``), WSL paths (``/mnt/d/...``), and WSL-internal paths
    (``/home/...``) — all are handled correctly.
    """
    stored = normalize_for_storage(str(path).strip())  # → Git Bash / /mnt/... / other

    if platform.system() == "Windows":
        # The command will run in WSL → /mnt/d/... format required
        m = _LINUX_GITBASH_RE.match(stored)
        if m:
            return f"/mnt/{m.group(1)}/{m.group(2)}"
        return stored  # already /mnt/... or WSL-internal path

    if platform.system() == "Linux" and _LINUX_CONVENTION == "wsl2":
        m = _LINUX_GITBASH_RE.match(stored)
        if m:
            return f"/mnt/{m.group(1)}/{m.group(2)}"
        return stored

    return stored  # Docker / native Linux / macOS: no-op


def get_csv_delimiter() -> str:
    """CSV field delimiter used by seed files. Default: ';' (Norwegian/European Excel)."""
    return os.getenv("ODIN_CSV_DELIMITER", ";")


def utc_now_str() -> str:
    """Return current UTC time as ISO 8601 string with milliseconds, e.g. '2026-01-15T12:34:56.789Z'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def append_log(log_path, message: str) -> None:
    """Append a line to a log file (binary mode). Silently ignores I/O errors."""
    try:
        Path(log_path).open("ab").write((message.rstrip() + "\n").encode())
    except OSError:
        pass


def sql_placeholders(values) -> str:
    """Return '?,?,?' placeholder string matching len(values) for SQL IN clauses."""
    return ",".join("?" * len(values))


_RUN_ACCESSION_RE = re.compile(r"[A-Za-z0-9_\-]{1,150}")


def validate_run_accession(value: str) -> bool:
    """Return True if *value* is a well-formed run accession, False otherwise.

    MinKNOW accessions look like ``20250912_0843_MN00000_FBD00001_44c4f359``.
    The pattern allows alphanumerics, underscores, and hyphens up to 150 chars.
    """
    return bool(_RUN_ACCESSION_RE.fullmatch(value))


def run_accessions_slug(accessions: list[str], max_len: int = 180) -> str:
    """Return a filesystem-safe string representing a list of run accessions.

    When the full joined string fits in *max_len* characters it is returned
    as-is (``ERR1__ERR2``).  When it would exceed *max_len* — which happens
    quickly with MinKNOW-style accessions like
    ``20250912_0843_MN00000_FBD00001_44c4f359`` (36 chars each) — it falls
    back to ``{first}__and_{n}_more``.

    Rationale for 180 chars: Windows default MAX_PATH is 260 chars.  The
    surrounding path (base output dir + pipeline subdir + file_identifier +
    ``/``) typically consumes 60-80 chars, leaving ≈180 for the accession
    slug before filenames and extensions push the total past 260.
    Linux per-component limit is 255 chars so 180 keeps individual directory
    names and filenames safely within that limit too.
    """
    slug = "__".join(accessions)
    if len(slug) <= max_len:
        return slug
    n_extra = len(accessions) - 1
    return f"{accessions[0]}__and_{n_extra}_more"


def get_csv_decimal() -> str:
    """Decimal separator used in numeric CSV fields. Default: ',' (Norwegian locale)."""
    return os.getenv("ODIN_CSV_DECIMAL", ",")


def normalize_for_storage(value: str) -> str:
    """
    Convert a Windows absolute path to Linux Git Bash format before writing
    to the database, so stored values are always platform-neutral.

    ``D:\\ODIN\\data``  →  ``/d/ODIN/data``
    ``D:/ODIN/data``   →  ``/d/ODIN/data``
    ``/d/ODIN/data``   →  ``/d/ODIN/data``   (no-op, already canonical)
    ``/mnt/d/ODIN``    →  ``/mnt/d/ODIN``    (no-op, user entered WSL2 form)
    ``https://...``    →  ``https://...``     (no-op, not a path)

    The Git Bash / Docker volume-mount convention is used as the canonical
    form (drive letter becomes the top-level directory: ``D:`` → ``/d``).
    """
    m = _WIN_PATH_RE.match(value.strip().strip('"'))
    if not m:
        return value.strip().strip('"')
    drive = m.group(1).lower()
    rest = m.group(2).replace("\\", "/").lstrip("/")
    return f"/{drive}/{rest}"


def coerce_path(stored: str) -> str:
    """
    Return a path string valid on the current platform and Linux convention.

    Stored values are always in Git Bash format (``/d/path``) after
    ``normalize_for_storage``.  This function converts them to whatever form
    the running environment expects:

    - **Docker / native Linux** (``_LINUX_CONVENTION = "gitbash"``) —
      ``/d/path`` is already correct; no-op.
    - **WSL2** (``_LINUX_CONVENTION = "wsl2"``, detected via ``WSL_DISTRO_NAME``) —
      ``/d/path`` → ``/mnt/d/path``.
    - **Windows** — ``/d/path`` → ``D:/path`` so ``pathlib.Path`` resolves correctly.
    - **macOS / other** — no-op.
    """
    system = platform.system()

    if system == "Windows":
        m = _LINUX_GITBASH_RE.match(stored)
        if m:
            return f"{m.group(1).upper()}:/{m.group(2)}"
        m = _WSL_PATH_RE.match(stored)
        if m:
            return f"{m.group(1).upper()}:/{m.group(2)}"
        return stored  # already Windows-style

    if system == "Linux" and _LINUX_CONVENTION == "wsl2":
        m = _LINUX_GITBASH_RE.match(stored)
        if m:
            return f"/mnt/{m.group(1)}/{m.group(2)}"
        return stored  # already /mnt/... or other Linux form

    return stored  # Docker/native Linux (gitbash) or macOS: no-op


def resolve_seed_dir() -> Path | None:
    """Resolve the seed-data directory.

    Priority: ``ODIN_SEED_DIR`` (used as-is) -> ``ODIN_PIPELINE_ROOT``/seed -> None.
    """
    env = os.getenv("ODIN_SEED_DIR")
    if env:
        return Path(coerce_path(env))
    root = os.getenv("ODIN_PIPELINE_ROOT")
    if root:
        return Path(coerce_path(root)) / "seed"
    return None


def resolve_log_dir(output_dir_stored: str) -> Path:
    """Return the local filesystem Path for the pipeline_logs directory.

    Priority:
      1. ``ODIN_LOG_DIR`` env var — useful in dev: set to a local repo path so
         both ODIN streaming logs and .nextflow.log are always accessible
         without mounting a volume.
      2. ``{output_dir}/pipeline_logs`` — default alongside output data.
    """
    env = os.environ.get("ODIN_LOG_DIR", "").strip()
    if env:
        return Path(coerce_path(env))
    return Path(coerce_path(normalize_for_storage(output_dir_stored))) / "pipeline_logs"


_YYYYMMDD_RE = re.compile(r"^\d{8}$")


def is_yyyymmdd(value: str) -> bool:
    """Return True if *value* is an 8-digit YYYYMMDD string (format check only)."""
    return bool(_YYYYMMDD_RE.match(value))


def resolve_sample_id(db, sample_code: str | None, sampling_date: str | None) -> str | None:
    """Resolve a sample UUID from its code and optional sampling date.

    When a ``sampling_date`` is given, only an exact ``(sample_code,
    sampling_date)`` match is returned — a specified date is never second-guessed.
    When no date is given, fall back to the most recent sample for that code
    (by ``sampling_date``). This mirrors the reference lookup in pipeline_scripts,
    which prefers an exact date match and is otherwise keyed on sample_code; it is
    deliberately date-centric (``updated_at`` edit-time is never considered).

    Callers that must not guess (seeding, per-barcode registration) match exactly
    and skip on miss rather than calling this with a null date.
    """
    if not sample_code:
        return None
    if sampling_date:
        row = db.execute(
            "SELECT id FROM samples WHERE sample_code = ? AND sampling_date = ?",
            (sample_code, sampling_date),
        ).fetchone()
        return row["id"] if row else None
    row = db.execute(
        "SELECT id FROM samples WHERE sample_code = ? ORDER BY sampling_date DESC LIMIT 1",
        (sample_code,),
    ).fetchone()
    return row["id"] if row else None


def build_update(table: str, fields: dict, record_id: str, now: str, actor: str) -> tuple[str, list]:
    """Build an audit-stamped single-row UPDATE.

    Returns ``(sql, params)`` for
    ``UPDATE <table> SET <fields...>, updated_at=?, updated_by=? WHERE id=?``.
    Field names come from trusted (Pydantic-validated) dict keys, never raw user
    input, so they are interpolated directly.

    ``fields`` must be non-empty, and that is now enforced rather than merely documented:
    an empty dict produced ``SET , updated_at=?`` — a syntax error surfacing as an opaque
    sqlite3 failure far from the caller that forgot to guard. A caller that may legitimately
    have nothing to write should skip the call (see the ``if changed:`` guards in the update
    endpoints) rather than issue a stamp-only UPDATE, which would bump ``updated_at`` for no
    change and mislead sync's ``incoming_is_newer`` comparison.
    """
    if not fields:
        raise ValueError(
            f"build_update({table!r}) requires at least one field to set; "
            "guard the call site instead of issuing an audit-only UPDATE"
        )
    set_clause = ", ".join(f"{k}=?" for k in fields)
    sql = f"UPDATE {table} SET {set_clause}, updated_at=?, updated_by=? WHERE id=?"  # noqa: S608
    params = [*fields.values(), now, actor, record_id]
    return sql, params
