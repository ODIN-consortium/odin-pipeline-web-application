"""
Scan the tail of a pipeline log file and return a human-readable hint when a
known failure pattern is detected.  Returns None when no pattern matches.

Patterns are checked in priority order — the first match wins.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..utils import coerce_path

# Number of lines to read from the end of the log when scanning for hints.
# Most Nextflow error messages appear in the last few hundred lines.
_TAIL_LINES = 300

# ---------------------------------------------------------------------------
# Pattern registry
# Each entry: (regex_pattern, hint_message)
# Patterns are matched case-insensitively against individual log lines.
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # SSL / TLS certificate errors
    (
        re.compile(
            r"CERTIFICATE_VERIFY_FAILED"
            r"|self.signed certificate"
            r"|unable to get local issuer certificate"
            r"|SSL peer certificate or SSH remote key was not OK"
            r"|javax\.net\.ssl\.SSLHandshakeException"
            r"|PKIX path building failed"
            r"|sun\.security\.validator\.ValidatorException",
            re.IGNORECASE,
        ),
        "SSL certificate verification failed. "
        "Place your corporate CA certificate (PEM format) in the "
        "pipeline/ca/ folder and restart the container. "
        "See the README for details.",
    ),
    # Git LFS pointer files mistaken for real data
    (
        re.compile(
            r"not in gzip format"
            r"|malformed taxonomy file"
            r"|ZipException.*Not in GZIP"
            r"|gzip:.*not in gzip format",
            re.IGNORECASE,
        ),
        "A data file appears to be a Git LFS pointer rather than real content. "
        "Run 'git lfs pull' in the odin_demo repository to download the actual files.",
    ),
    # Docker socket / DooD permission errors
    (
        re.compile(
            r"permission denied.*docker\.sock"
            r"|connect: permission denied.*docker"
            r"|Got permission denied while trying to connect to the Docker",
            re.IGNORECASE,
        ),
        "The backend cannot reach the Docker socket. "
        "Ensure /var/run/docker.sock is mounted in docker-compose.yml "
        "and the container has permission to use it.",
    ),
    # No space left on device
    (
        re.compile(r"no space left on device", re.IGNORECASE),
        "The disk is full. Free up space or point ODIN_WORK_DIR to a volume "
        "with more capacity.",
    ),
    # Nextflow work dir not accessible from sibling containers (the original bug)
    (
        re.compile(r"\.command\.run: No such file or directory", re.IGNORECASE),
        "Nextflow task files are not reachable by pipeline tool containers. "
        "Set ODIN_WORK_DIR to a WSL2-native Linux path (e.g. /home/<user>/odin_work) "
        "in .env so the work directory is bind-mounted identically on the host.",
    ),
    # Out of memory
    (
        re.compile(
            r"out of memory"
            r"|cannot allocate memory"
            r"|java\.lang\.OutOfMemoryError",
            re.IGNORECASE,
        ),
        "A process ran out of memory. "
        "Try reducing --threads in odin.config or running on a machine with more RAM.",
    ),
    # Insufficient CPUs — most common on Docker Desktop with default 2-CPU allocation
    (
        re.compile(r"Process requirement exceeds available CPUs", re.IGNORECASE),
        "A pipeline process requires more CPUs than are available to Docker "
        "(wf-metagenomics needs at least 4). "
        "Check Docker Desktop Settings → Resources → CPUs and increase the allocation. "
        "On Windows, a .wslconfig file may also cap WSL2 CPU usage. "
        "If you cannot change the CPU allocation, open pipeline/config/odin.config "
        "and add 'executor.cpus = N' and 'resourceLimits = [cpus: N]' to the "
        "odin_epi2me profile, where N is the number of CPUs actually available. "
        "See the README Troubleshooting section for details.",
    ),
]

# ---------------------------------------------------------------------------
# Multi-line detectors — need context from more than one line
# ---------------------------------------------------------------------------

_SQUIRREL_HIGH_N_RE = re.compile(
    r"(\d+) sequences? flagged as high N content",
    re.IGNORECASE,
)


def _check_squirrel_high_n(lines: list[str]) -> str | None:
    """Detect squirrel crashing because all sequences were excluded for high N content.

    Squirrel writes suggested_to_exclude.csv, produces an empty alignment, then
    BioPython raises 'No records found in handle' when trying to read it back.
    """
    text = "\n".join(lines)
    if "No records found in handle" not in text:
        return None
    m = _SQUIRREL_HIGH_N_RE.search(text)
    count = int(m.group(1)) if m else None
    if count:
        return (
            f"Squirrel failed: all {count} consensus sequence(s) were excluded due to "
            "high N content (>20% unknown bases). "
            "This usually means the selected run had insufficient mpox reads to assemble "
            "a reliable consensus."
        )
    return (
        "Squirrel failed: no sequences remained after high-N QC filtering. "
        "The selected run may not contain mpox reads."
    )


_MULTI_LINE_DETECTORS = [
    _check_squirrel_high_n,
]


def _tail(path: Path, n: int) -> list[str]:
    """Return the last *n* lines of *path* efficiently."""
    try:
        with path.open("rb") as fh:
            # Seek backward in chunks to find enough newlines
            chunk = 1 << 14  # 16 KB
            fh.seek(0, 2)
            size = fh.tell()
            buf = b""
            pos = size
            while pos > 0 and buf.count(b"\n") <= n:
                read = min(chunk, pos)
                pos -= read
                fh.seek(pos)
                buf = fh.read(read) + buf
            lines = buf.decode("utf-8", errors="replace").splitlines()
            return lines[-n:]
    except OSError:
        return []


def detect_error_hint(log_file: str | None) -> str | None:
    """Return a hint string if a known failure pattern is found in *log_file*.

    Returns None when log_file is absent, unreadable, or no pattern matches.
    """
    if not log_file:
        return None
    path = Path(coerce_path(log_file))
    if not path.exists():
        return None
    lines = _tail(path, _TAIL_LINES)
    for detector in _MULTI_LINE_DETECTORS:
        hint = detector(lines)
        if hint:
            return hint
    text = "\n".join(lines)
    for pattern, hint in _PATTERNS:
        if pattern.search(text):
            return hint
    return None
