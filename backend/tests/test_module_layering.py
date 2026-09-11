"""Guards against re-introducing the api-layer import cycle.

`api/__init__.py` imports every router, so any module that the routers import must
not import back into `api/` — doing so makes `import backend.app.db.queries` (or
`pipeline.command_builder`) fail on a partially-initialised module whenever it is
imported before the api package. That used to be worked around with function-local
imports; settings resolution now lives in `app.settings_resolver`, which every layer
can import at module level.

These tests fail loudly if a lower layer starts importing the HTTP layer again, or if
importing one of those modules first stops working.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

_APP = Path(__file__).parents[1] / "app"

# Packages that the api routers import, and which therefore must not import api back.
_LOWER_LAYERS = ("db", "pipeline", "parsers")

# Modules that must be importable on their own, before anything imports the api package.
_STANDALONE_IMPORTS = [
    "backend.app.settings_resolver",
    "backend.app.db.queries",
    "backend.app.pipeline.command_builder",
    "backend.app.pipeline.executor",
    "backend.app.parsers.postprocess_common",
    "backend.app.parsers.kraken_postprocessor",
]

_API_IMPORT_RE = re.compile(r"^\s*from\s+\.{2,}api[\s.]|^\s*from\s+backend\.app\.api[\s.]", re.M)


def _lower_layer_sources() -> list[Path]:
    return [p for layer in _LOWER_LAYERS for p in (_APP / layer).glob("*.py")]


def test_lower_layers_do_not_import_the_api_package() -> None:
    offenders = [
        f"{path.parent.name}/{path.name}"
        for path in _lower_layer_sources()
        if _API_IMPORT_RE.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        f"{offenders} import from api/, which creates a circular import. "
        "Move the shared logic into a layer-neutral module (see app/settings_resolver.py)."
    )


def test_lower_layer_sources_were_actually_scanned() -> None:
    """Guard the guard: a bad glob would make the check above vacuously pass."""
    assert len(_lower_layer_sources()) >= 8


@pytest.mark.parametrize("module", _STANDALONE_IMPORTS)
def test_module_imports_in_a_fresh_interpreter(module: str) -> None:
    """Each module must import cleanly when it is the first thing loaded.

    Run in a subprocess because the rest of the suite has already imported app.main,
    which resolves the whole package graph and would hide the problem.
    """
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"import {module} failed:\n{result.stderr}"
