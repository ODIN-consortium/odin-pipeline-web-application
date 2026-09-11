"""
Serial pipeline executor with FIFO queue.

Jobs are executed one at a time (memory constraints) but any number can be
queued.  The worker thread picks them up in submission order.

Public API
----------
  launch(pipeline_run_id, cmd, log_path, db_path) -> None
      Enqueue the job and return immediately (never blocks).
      The worker thread updates pipeline_runs.status / pid / exit_code.

  cancel(pipeline_run_id, db_path) -> None
      If the run is currently executing: send SIGTERM.
      If the run is still queued: mark it cancelled and skip it when the
      worker reaches it.

  current_run_id() -> str | None
      Return the id of the currently executing pipeline_run, or None.

  queue_size() -> int
      Return the number of jobs waiting to start (not counting the running one).

  tail_log(log_path, last_line) -> tuple[list[str], int]
      Return new lines since last_line and the new last_line index.
      Used by the SSE endpoint to stream incremental output.
"""

from __future__ import annotations

import logging
import os
import platform
import queue
import re
import signal
import sqlite3
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..database import connect_sqlite_with_retry
from ..utils import coerce_path_for_shell, utc_now_str
from .execution_env import wrap_cmd

logger = logging.getLogger(__name__)

# Log-line prefix for post-processing output; the frontend highlights these lines.
_POSTPROCESS_MARKER = "[ODIN-POST]"

# ── Module-level state ───────────────────────────────────────────────────────

_lock = threading.Lock()
_current_run_id: Optional[str] = None
_current_process: Optional["subprocess.Popen[bytes]"] = None  # type: ignore[name-defined]


@dataclass(frozen=True)
class Job:
    """One queued pipeline job, as handed from `launch()` to the worker thread.

    When `cmd_holder` is given, `cmd` is ignored: `prepare_fn` appends the real
    command to the holder in the worker thread (it can only be built after the
    slow input preparation), and the worker reads it from there.
    """

    run_id: str
    cmd: "str | list[str]"
    log_path: Path
    db_path: str
    prepare_fn: Optional[Callable[[], None]] = None
    cmd_holder: Optional[list] = None
    postprocess_fn: Optional[Callable[[], None]] = None
    cleanup_fn: Optional[Callable[[], None]] = None
    extra_env: Optional[dict] = None


# FIFO queue of Job records, drained one at a time by the single worker thread
_job_queue: "queue.Queue[Job]" = queue.Queue()
# run_ids that were cancelled while still waiting in the queue
_cancelled_ids: set[str] = set()
# worker thread (started lazily on first launch)
_worker_thread: Optional[threading.Thread] = None


def current_run_id() -> Optional[str]:
    with _lock:
        return _current_run_id


def queue_size() -> int:
    """Number of jobs waiting to start (does not count the currently running job)."""
    return _job_queue.qsize()


def _open_db(db_path: str) -> sqlite3.Connection:
    return connect_sqlite_with_retry(db_path, context="pipeline worker")


def _set_status(
    db_path: str,
    run_id: str,
    status: str,
    *,
    pid: Optional[int] = None,
    exit_code: Optional[int] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
) -> None:
    con = _open_db(db_path)
    try:
        fields = ["status = ?", "updated_at = ?"]
        values: list = [status, utc_now_str()]
        if pid is not None:
            fields.append("pid = ?")
            values.append(pid)
        if exit_code is not None:
            fields.append("exit_code = ?")
            values.append(exit_code)
        if started_at is not None:
            fields.append("started_at = ?")
            values.append(started_at)
        if finished_at is not None:
            fields.append("finished_at = ?")
            values.append(finished_at)
        values.append(run_id)
        con.execute(f"UPDATE pipeline_runs SET {', '.join(fields)} WHERE id = ?", values)
        con.commit()
    finally:
        con.close()


def _safe_set_status(db_path: str, run_id: str, status: str, **kwargs) -> None:
    """Best-effort status update that never raises into the worker loop."""
    try:
        _set_status(db_path, run_id, status, **kwargs)
    except Exception:
        logger.exception("failed to update run status", extra={"run_id": run_id, "status": status})


def _append_log(log_path: Path, text: str) -> None:
    """Append *text* to the run log verbatim (callers include their own newlines)."""
    with log_path.open("ab") as fh:
        fh.write(text.encode())


def _fail_before_start(job: Job, note: str) -> None:
    """Record a failure that happened before the subprocess started.

    Writes *note* to the run log so the SSE stream surfaces it, marks the run
    failed, and releases the current-process slot.
    """
    global _current_process
    _append_log(job.log_path, note)
    _safe_set_status(job.db_path, job.run_id, "failed", finished_at=utc_now_str())
    with _lock:
        _current_process = None


def _run_prepare_step(job: Job) -> bool:
    """Run the optional preparation step (samplesheet build / FASTQ concat).

    Returns False when preparation failed and the run has been marked failed.
    """
    if job.prepare_fn is None:
        return True
    _append_log(job.log_path, "[ODIN] Preparing inputs (concatenating FASTQ files)...\n")
    try:
        job.prepare_fn()
    except Exception as exc:
        _fail_before_start(job, f"[ODIN] Preparation failed: {exc}\n")
        return False
    _append_log(job.log_path, "[ODIN] Inputs ready.\n\n")
    return True


def _resolve_command(job: Job) -> "str | list[str] | None":
    """Return the command to execute, or None when the run has been failed.

    With a cmd_holder, prepare_fn was responsible for producing the command; an
    empty holder means it silently produced nothing, which cannot be run.
    """
    if job.cmd_holder is None:
        return job.cmd
    if not job.cmd_holder:
        _fail_before_start(job, "[ODIN] No command produced by preparation step.\n")
        return None
    return job.cmd_holder[0]


def _run_postprocess(job: Job) -> None:
    """Run post-processing in the worker thread; a failure is logged, not fatal.

    The pipeline itself already succeeded at this point, so the run stays 'done'.
    """
    if job.postprocess_fn is None:
        return
    try:
        job.postprocess_fn()
    except Exception as exc:
        _append_log(job.log_path, f"\n{_POSTPROCESS_MARKER} Post-processing failed: {exc}\n")


def _run_cleanup(job: Job) -> None:
    """Run the cleanup hook (temp workspace removal); failures are logged only."""
    if job.cleanup_fn is None:
        return
    try:
        job.cleanup_fn()
    except Exception as exc:
        _append_log(job.log_path, f"\n[ODIN] Cleanup failed: {exc}\n")


def _job_env(job: Job) -> dict:
    """The environment for a pipeline subprocess: process env + job extras +
    computed defaults.

    NXF_ASSETS gets its default here rather than in docker-compose: Nextflow's
    own fallback is $NXF_HOME/assets *inside* the container (lost on recreate),
    and a compose-level fallback cannot be derived from ODIN_PIPELINE_ROOT in a
    way podman-compose understands (it does not expand variables inside
    ${VAR:-...} fallbacks). Deriving it at launch time serves both engines and
    keeps the pipeline asset cache on the host.
    """
    env = {**os.environ, **(job.extra_env or {})}
    root = env.get("ODIN_PIPELINE_ROOT", "").strip()
    if not env.get("NXF_ASSETS", "").strip() and root:
        env["NXF_ASSETS"] = coerce_path_for_shell(root).rstrip("/") + "/nf/assets"
    return env


def _run_pipeline(job: Job) -> None:
    """Execute one pipeline job (called by the worker thread).

    Sets/clears _current_process but does NOT touch _current_run_id —
    the worker thread manages that.
    """
    global _current_process

    job.log_path.parent.mkdir(parents=True, exist_ok=True)
    _safe_set_status(job.db_path, job.run_id, "running", started_at=utc_now_str())

    # Everything from preparation onward runs inside the try, so that the finally
    # below reaches `_run_cleanup` on *every* exit path. It previously started after
    # the two early returns, which meant a failed preparation — or a preparation that
    # produced no command — left the temporary FASTQ concat workspace on disk. That
    # workspace lives under ODIN_TMP_DIR, which docker-compose bind-mounts
    # host:container identically for DooD, so the leak accumulated on the host and
    # survived container restarts. A bad input fails on every retry, so it accumulated
    # fast, and concatenated nanopore FASTQ is measured in gigabytes.
    try:
        if not _run_prepare_step(job):
            return
        cmd = _resolve_command(job)
        if cmd is None:
            return

        # If cmd is already a list, run it directly (no shell wrapping — used for
        # Python subprocesses that share the backend's venv and need native paths).
        wrapped: list[str] = cmd if isinstance(cmd, list) else wrap_cmd(cmd)

        log_fh = job.log_path.open("ab")
        try:
            # Write the resolved command first so it appears in the SSE log stream
            log_fh.write(f"[ODIN] Command: {' '.join(wrapped)}\n\n".encode())
            log_fh.flush()
            proc = subprocess.Popen(
                wrapped,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                # No stdin — pipeline must be fully non-interactive
                stdin=subprocess.DEVNULL,
                # New session so the process isn't killed if uvicorn --reload restarts
                start_new_session=True,
                env=_job_env(job),
            )
        except Exception:
            log_fh.close()
            raise

        with _lock:
            _current_process = proc

        _safe_set_status(job.db_path, job.run_id, "running", pid=proc.pid)

        exit_code = proc.wait()
        log_fh.close()
        _safe_set_status(
            job.db_path,
            job.run_id,
            "done" if exit_code == 0 else "failed",
            exit_code=exit_code,
            finished_at=utc_now_str(),
        )

        if exit_code == 0:
            _run_postprocess(job)
    except Exception as exc:
        # Write the exception to the log so the SSE stream surfaces it
        _append_log(job.log_path, f"\nERROR: executor caught exception: {exc}\n")
        _safe_set_status(job.db_path, job.run_id, "failed", finished_at=utc_now_str())
    finally:
        _run_cleanup(job)
        with _lock:
            _current_process = None


# ── Worker thread ─────────────────────────────────────────────────────────────


def _worker() -> None:
    """Single persistent daemon thread that drains _job_queue one job at a time."""
    global _current_run_id

    while True:
        job = _job_queue.get()

        with _lock:
            if job.run_id in _cancelled_ids:
                _cancelled_ids.discard(job.run_id)
                _job_queue.task_done()
                continue
            _current_run_id = job.run_id

        try:
            _run_pipeline(job)
        except Exception:
            logger.exception(
                "pipeline worker loop caught unexpected exception", extra={"run_id": job.run_id}
            )
            _safe_set_status(job.db_path, job.run_id, "failed", finished_at=utc_now_str())
        finally:
            with _lock:
                if _current_run_id == job.run_id:
                    _current_run_id = None
            _job_queue.task_done()


def launch(
    run_id: str,
    cmd: "str | list[str]",
    log_path: Path,
    db_path: str,
    prepare_fn: Optional[Callable[[], None]] = None,
    cmd_holder: Optional[list] = None,
    postprocess_fn: Optional[Callable[[], None]] = None,
    cleanup_fn: Optional[Callable[[], None]] = None,
    extra_env: Optional[dict] = None,
) -> None:
    """
    Enqueue a pipeline job and return immediately.

    The single worker thread picks jobs up in FIFO order.  The HTTP caller
    receives a 201/queued response straight away regardless of whether another
    job is already running.
    """
    global _worker_thread

    _job_queue.put(
        Job(
            run_id=run_id,
            cmd=cmd,
            log_path=log_path,
            db_path=db_path,
            prepare_fn=prepare_fn,
            cmd_holder=cmd_holder,
            postprocess_fn=postprocess_fn,
            cleanup_fn=cleanup_fn,
            extra_env=extra_env,
        )
    )

    # Start the worker lazily (once, daemon so it dies with the process)
    with _lock:
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread = threading.Thread(
                target=_worker,
                daemon=True,
                name="pipeline-worker",
            )
            _worker_thread.start()


def cancel(run_id: str, db_path: str) -> None:
    """Cancel a queued or running pipeline job.

    - If the job is currently executing: send SIGTERM to the process.
    - If the job is still waiting in the queue: add it to _cancelled_ids so the
      worker skips it when it reaches the front of the queue, and update the DB
      immediately.
    """
    with _lock:
        if _current_run_id == run_id and _current_process is not None:
            proc = _current_process
        else:
            # Not currently running — mark as cancelled; worker will skip it
            _cancelled_ids.add(run_id)
            _safe_set_status(db_path, run_id, "cancelled", finished_at=utc_now_str())
            return

    try:
        if platform.system() == "Windows":
            proc.terminate()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass

    _safe_set_status(db_path, run_id, "cancelled", finished_at=utc_now_str())


def shutdown() -> None:
    """
    Gracefully terminate any currently-running pipeline process.

    Called during application shutdown (e.g. ``docker stop``, SIGTERM to uvicorn).
    On Linux/Docker: sends SIGTERM to the entire process group so nextflow and all
    its children exit cleanly before the container is force-killed.
    On Windows (dev): terminates the wsl.exe wrapper process.

    The worker thread is a daemon thread, so it will die with the main process.
    The pipeline run will be re-queued on next startup via ``requeue_pending_runs``.
    """
    with _lock:
        proc = _current_process

    if proc is None:
        return

    try:
        if platform.system() == "Windows":
            proc.terminate()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def tail_log(log_path: Path, last_byte: int) -> tuple[str, int]:
    """
    Read new content from *log_path* starting at byte offset *last_byte*.
    Returns (new_text, new_byte_offset).
    Used by the SSE endpoint for incremental streaming.
    """
    if not log_path.exists():
        return "", last_byte
    with log_path.open("rb") as f:
        f.seek(last_byte)
        chunk = f.read()
    if not chunk:
        return "", last_byte
    text = chunk.decode("utf-8", errors="replace")
    text = _ANSI_ESCAPE_RE.sub("", text)
    return text, last_byte + len(chunk)
