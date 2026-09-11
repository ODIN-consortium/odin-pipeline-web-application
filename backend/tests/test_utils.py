"""
Tests for backend.app.utils — path normalization utilities.
"""

from unittest import mock

import pytest

from backend.app.utils import (
    _detect_linux_convention,
    build_update,
    coerce_path,
    normalize_for_storage,
)

# ─────────────────────────────────────────────────────────────────────────────
# normalize_for_storage — pure string transform, no platform check
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Backslash separators
        (r"D:\ODIN\data", "/d/ODIN/data"),
        (r"C:\Users\testuser\minknow", "/c/Users/testuser/minknow"),
        # Forward-slash separators (Git Bash / Python on Windows)
        ("D:/ODIN/data", "/d/ODIN/data"),
        ("C:/Users/testuser/minknow", "/c/Users/testuser/minknow"),
        # Mixed separators
        (r"D:/ODIN\data\sub", "/d/ODIN/data/sub"),
        # Uppercase drive letter
        ("E:/some/path", "/e/some/path"),
        # Windows "Copy as path" wraps the value in double quotes
        (r'"D:\ODIN\data"', "/d/ODIN/data"),
        ('"D:/ODIN/data"', "/d/ODIN/data"),
    ],
)
def test_normalize_for_storage_converts_windows_path(raw, expected):
    assert normalize_for_storage(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "/d/ODIN/data",  # already Git Bash form
        "/mnt/d/ODIN/data",  # already WSL2 form — left unchanged
        "https://enlighten.local",  # URL — untouched
        "profile-name",  # plain string — untouched
        "data/relative",  # relative path — untouched
    ],
)
def test_normalize_for_storage_noop_for_non_windows(raw):
    assert normalize_for_storage(raw) == raw


# ─────────────────────────────────────────────────────────────────────────────
# _detect_linux_convention — reads WSL env vars at import time
# ─────────────────────────────────────────────────────────────────────────────


def test_detect_linux_convention_wsl2_via_distro_name():
    with mock.patch.dict("os.environ", {"WSL_DISTRO_NAME": "Ubuntu"}, clear=True):
        assert _detect_linux_convention() == "wsl2"


def test_detect_linux_convention_wsl2_via_wsl_interop():
    with mock.patch.dict("os.environ", {"WSL_INTEROP": "/run/WSL/1_interop"}, clear=True):
        assert _detect_linux_convention() == "wsl2"


def test_detect_linux_convention_docker_via_dockerenv():
    with (
        mock.patch.dict("os.environ", {}, clear=True),
        mock.patch("backend.app.utils.os.path.exists", side_effect=lambda p: p == "/.dockerenv"),
    ):
        assert _detect_linux_convention() == "wsl2"


def test_detect_linux_convention_podman_via_containerenv():
    # Podman does not create /.dockerenv — it creates /run/.containerenv.
    # Regression: missing this marker downgraded coerce_path() to a no-op,
    # silently breaking post-processing under rootless Podman.
    with (
        mock.patch.dict("os.environ", {}, clear=True),
        mock.patch(
            "backend.app.utils.os.path.exists",
            side_effect=lambda p: p == "/run/.containerenv",
        ),
    ):
        assert _detect_linux_convention() == "wsl2"


def test_detect_linux_convention_docker_or_native():
    with (
        mock.patch.dict("os.environ", {}, clear=True),
        mock.patch("backend.app.utils.os.path.exists", return_value=False),
    ):
        assert _detect_linux_convention() == "gitbash"


# ─────────────────────────────────────────────────────────────────────────────
# coerce_path on Linux (Docker / native) — gitbash convention, always no-op
# ─────────────────────────────────────────────────────────────────────────────


def test_coerce_path_linux_gitbash_already_gitbash_is_noop():
    with (
        mock.patch("backend.app.utils.platform.system", return_value="Linux"),
        mock.patch("backend.app.utils._LINUX_CONVENTION", "gitbash"),
    ):
        assert coerce_path("/d/ODIN/data") == "/d/ODIN/data"


def test_coerce_path_linux_gitbash_mnt_path_is_noop():
    with (
        mock.patch("backend.app.utils.platform.system", return_value="Linux"),
        mock.patch("backend.app.utils._LINUX_CONVENTION", "gitbash"),
    ):
        assert coerce_path("/mnt/d/ODIN/data") == "/mnt/d/ODIN/data"


def test_coerce_path_linux_gitbash_relative_path_is_noop():
    with (
        mock.patch("backend.app.utils.platform.system", return_value="Linux"),
        mock.patch("backend.app.utils._LINUX_CONVENTION", "gitbash"),
    ):
        assert coerce_path("data/minknow") == "data/minknow"


# ─────────────────────────────────────────────────────────────────────────────
# coerce_path on Linux (WSL2) — converts /d/ to /mnt/d/
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "stored, expected",
    [
        ("/d/ODIN/data", "/mnt/d/ODIN/data"),
        ("/c/Users/testuser/minknow", "/mnt/c/Users/testuser/minknow"),
        ("/e/some/path", "/mnt/e/some/path"),
    ],
)
def test_coerce_path_linux_wsl2_converts_gitbash_to_mnt(stored, expected):
    with (
        mock.patch("backend.app.utils.platform.system", return_value="Linux"),
        mock.patch("backend.app.utils._LINUX_CONVENTION", "wsl2"),
    ):
        assert coerce_path(stored) == expected


def test_coerce_path_linux_wsl2_already_mnt_path_is_noop():
    # User stored /mnt/d/... directly — already correct for WSL2
    with (
        mock.patch("backend.app.utils.platform.system", return_value="Linux"),
        mock.patch("backend.app.utils._LINUX_CONVENTION", "wsl2"),
    ):
        assert coerce_path("/mnt/d/ODIN/data") == "/mnt/d/ODIN/data"


# ─────────────────────────────────────────────────────────────────────────────
# coerce_path on Windows — converts stored Linux Git Bash paths back
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "stored, expected",
    [
        ("/d/ODIN/data", "D:/ODIN/data"),
        ("/c/Users/testuser/minknow", "C:/Users/testuser/minknow"),
        ("/e/some/path", "E:/some/path"),
    ],
)
def test_coerce_path_windows_converts_gitbash_to_windows(stored, expected):
    with mock.patch("backend.app.utils.platform.system", return_value="Windows"):
        assert coerce_path(stored) == expected


def test_coerce_path_windows_leaves_windows_path_unchanged():
    with mock.patch("backend.app.utils.platform.system", return_value="Windows"):
        assert coerce_path(r"D:\ODIN\data") == r"D:\ODIN\data"


def test_coerce_path_windows_converts_wsl2_path():
    # /mnt/d/... stored from a WSL2 env is converted to D:/... on Windows
    with mock.patch("backend.app.utils.platform.system", return_value="Windows"):
        assert coerce_path("/mnt/d/ODIN/data") == "D:/ODIN/data"


# ─────────────────────────────────────────────────────────────────────────────
# coerce_path on macOS / other — always unchanged
# ─────────────────────────────────────────────────────────────────────────────


def test_coerce_path_other_platform_unchanged():
    with mock.patch("backend.app.utils.platform.system", return_value="Darwin"):
        assert coerce_path(r"D:\ODIN\data") == r"D:\ODIN\data"


# ─────────────────────────────────────────────────────────────────────────────
# build_update — the audit-stamped single-row UPDATE builder
# ─────────────────────────────────────────────────────────────────────────────


def test_build_update_sets_the_given_fields_and_stamps():
    sql, params = build_update("samples", {"comments": "hi"}, "row-1", "NOW", "device-b")
    assert sql == "UPDATE samples SET comments=?, updated_at=?, updated_by=? WHERE id=?"
    assert params == ["hi", "NOW", "device-b", "row-1"]


def test_build_update_preserves_field_order():
    sql, _ = build_update("sites", {"city": "Bergen", "country": "Norway"}, "r", "NOW", "d")
    assert sql.index("city=?") < sql.index("country=?")


def test_build_update_rejects_an_empty_field_set():
    """An empty dict produced `SET , updated_at=?` — invalid SQL, raised far from the cause.

    The docstring always required non-empty; nothing enforced it. Callers that may have
    nothing to write must skip the call rather than issue a stamp-only UPDATE, which would
    bump updated_at for no change and mislead sync's `incoming_is_newer` comparison.
    """
    with pytest.raises(ValueError, match="at least one field"):
        build_update("samples", {}, "row-1", "NOW", "device-b")
