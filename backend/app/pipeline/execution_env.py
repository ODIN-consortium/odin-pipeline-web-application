"""
Execution environment detection and command wrapping.

On the ODIN computer Docker runs inside WSL only (corporate constraint), but the
FastAPI process may be started on Windows, WSL, or native Linux.  This module
detects the environment at startup and provides:

  wrap_cmd(cmd)        — return the command list/string ready to spawn
  shell_executable()   — the shell to use (bash / wsl bash / None)
  is_wsl()             — True when running inside WSL
  is_windows()         — True when running on native Windows

The guiding principle is the same as utils.coerce_path: use environment
variables already set by the OS rather than probing the filesystem.
"""

from __future__ import annotations

import os
import platform
import shlex
import shutil
from typing import Sequence

# ── Detection (computed once at import) ──────────────────────────────────────

_SYSTEM = platform.system()  # "Windows" | "Linux" | "Darwin"
_IS_WSL = bool(os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"))


def is_windows() -> bool:
    return _SYSTEM == "Windows"


def is_wsl() -> bool:
    return _IS_WSL


def is_native_linux() -> bool:
    return _SYSTEM == "Linux" and not _IS_WSL


def shell_executable() -> str | None:
    """
    Return the shell binary to use for subprocess calls.

    - WSL / native Linux / macOS: "bash"
    - Windows with WSL available: "wsl" (will be prepended to commands)
    - Windows without WSL: None (use cmd.exe or PowerShell — unsupported for
      Nextflow, will raise early)
    """
    if not is_windows():
        return "bash"
    wsl = shutil.which("wsl")
    return "wsl" if wsl else None


def wrap_cmd(cmd: str | Sequence[str]) -> list[str]:
    """
    Wrap *cmd* so it runs in the correct execution environment.

    - Non-Windows (WSL / native Linux / macOS): run directly under bash.
    - Windows: prepend ``wsl --`` to delegate into WSL.
    - Windows without WSL available: raise RuntimeError.

    Always returns a list suitable for ``subprocess.Popen(cmd, ...)``.
    """
    if not is_windows():
        # Login shell (-l) sources ~/.bash_profile / ~/.profile, which sets up
        # PATH for user-installed tools like nextflow, micromamba, squirrel, etc.
        if isinstance(cmd, str):
            return ["bash", "-lc", cmd]
        # cmd is a sequence — join into a single shell invocation
        return ["bash", "-lc", shlex.join(list(cmd))]

    # Windows host — delegate to WSL
    shell = shell_executable()
    if shell is None:
        raise RuntimeError(
            "Cannot run pipeline: WSL is not available on this Windows machine. "
            "Install WSL2 and Docker Desktop (WSL backend) and try again."
        )
    # Windows host — delegate to WSL
    if isinstance(cmd, str):
        # -l: login shell so the WSL user's PATH (~/.profile etc.) is sourced
        return ["wsl", "bash", "-lc", cmd]
    return ["wsl", "bash", "-lc", shlex.join(list(cmd))]
