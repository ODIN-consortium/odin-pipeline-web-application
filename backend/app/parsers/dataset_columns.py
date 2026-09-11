"""What each column of the Enlighten datasets means — read from ``dataset_columns.yaml``.

The texts are configuration, not code: they live in ``pipeline/config/dataset_columns.yaml``
beside ``odin.config`` (baked into the image at ``/app/config/``), and a deployment can
overlay its own wording with ``$ODIN_PIPELINE_ROOT/config/dataset_columns.yaml`` or a file
named in ``ODIN_DATASET_COLUMNS_FILE``. ``postprocess_common.write_feather`` writes the
result into each feather as Arrow field metadata, and Enlighten (enlweb-api ≥ 2026.9) shows
it as the column's info tooltip.

The file has ``shared`` blocks of columns several datasets take from the same source, and one
entry per dataset that ``include``s blocks and lists its own ``columns``. A column's value is
the description text, or a mapping with ``description`` and any further items (``unit``…) —
every item becomes one line of the tooltip. ``tests/test_dataset_columns.py`` holds the
contract: each dataset's writer emits exactly the columns the file describes, so a pipeline
change that adds or drops a column fails the suite until the file says what the column is.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from ..utils import coerce_path

logger = logging.getLogger(__name__)

ENV_VAR = "ODIN_DATASET_COLUMNS_FILE"
FILE_NAME = "dataset_columns.yaml"

#: The datasets the post-processors write, as named in the file.
DATASETS = ("taxprofiler", "amr", "amr_summary", "biomeme")

#: Where the file shipped with the code is: the repo checkout, then the Docker image.
BUNDLED_CANDIDATES = (
    Path(__file__).resolve().parents[3] / "pipeline" / "config" / FILE_NAME,
    Path("/app/config") / FILE_NAME,
)

#: column name -> {"description": ..., "unit": ..., ...}
ColumnMetadata = dict[str, dict[str, str]]


def bundled_file() -> Path:
    """The ``dataset_columns.yaml`` shipped with this code."""
    for candidate in BUNDLED_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"{FILE_NAME} is missing from the installation; looked in "
        + ", ".join(str(c) for c in BUNDLED_CANDIDATES)
    )


def deployment_file() -> Path | None:
    """The deployment's overlay, if any: ``ODIN_DATASET_COLUMNS_FILE``, else
    ``$ODIN_PIPELINE_ROOT/config/dataset_columns.yaml`` when that file exists."""
    named = os.environ.get(ENV_VAR, "").strip()
    if named:
        return Path(coerce_path(named))
    root = os.environ.get("ODIN_PIPELINE_ROOT", "").strip()
    if root:
        candidate = Path(coerce_path(root)) / "config" / FILE_NAME
        if candidate.is_file():
            return candidate
    return None


def load_dataset_columns(
    path: Path, base: Mapping[str, ColumnMetadata] | None = None
) -> dict[str, ColumnMetadata]:
    """Parse one ``dataset_columns.yaml`` into ``{dataset: {column: items}}``.

    With *base* (the bundled file's result) the file is an overlay: its entries replace
    the base's column by column, datasets it does not mention are kept. Raises
    ``ValueError`` naming the file and the entry for anything malformed.
    """
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    if not isinstance(doc, Mapping) or not isinstance(doc.get("datasets"), Mapping):
        raise ValueError(f"{path}: expected a mapping with a top-level 'datasets' mapping")

    shared = _blocks(doc.get("shared") or {}, path)
    result: dict[str, ColumnMetadata] = {k: dict(v) for k, v in (base or {}).items()}
    for name, spec in doc["datasets"].items():
        if name not in DATASETS:
            raise ValueError(f"{path}: unknown dataset {name!r}; the writers know {list(DATASETS)}")
        if not isinstance(spec, Mapping):
            raise ValueError(f"{path}: dataset {name!r} must be a mapping")
        merged: ColumnMetadata = dict(result.get(name, {}))
        for block in spec.get("include") or []:
            if block not in shared:
                raise ValueError(
                    f"{path}: dataset {name!r} includes unknown shared block {block!r}"
                )
            merged.update(shared[block])
        merged.update(_columns(spec.get("columns") or {}, path, f"dataset {name!r}"))
        result[name] = merged
    return result


def _blocks(raw: Any, path: Path) -> dict[str, ColumnMetadata]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path}: 'shared' must be a mapping of block name -> columns")
    return {
        str(block): _columns(columns, path, f"shared block {block!r}")
        for block, columns in raw.items()
    }


def _columns(raw: Any, path: Path, where: str) -> ColumnMetadata:
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"{path}: {where}: 'columns' must be a mapping of column name -> description"
        )
    return {
        str(column): _items(value, path, f"{where}, column {column!r}")
        for column, value in raw.items()
    }


def _items(value: Any, path: Path, where: str) -> dict[str, str]:
    if isinstance(value, str):
        value = {"description": value}
    if not isinstance(value, Mapping) or not str(value.get("description", "")).strip():
        raise ValueError(
            f"{path}: {where}: give a description text, or a mapping with a non-empty 'description'"
        )
    items = {str(k): str(v).strip() for k, v in value.items()}
    if any(not v for v in items.values()):
        raise ValueError(f"{path}: {where}: every item needs a non-empty value")
    return items


def column_metadata(dataset: str) -> ColumnMetadata:
    """What to write into *dataset*'s feather: the bundled file, with the deployment's
    overlay on top. A malformed overlay is reported and ignored — post-processing must
    not fail over wording — whereas the bundled file is covered by the test suite and
    a fault in it is a bug that should surface."""
    if dataset not in DATASETS:
        raise KeyError(f"unknown dataset {dataset!r}; known: {list(DATASETS)}")
    result = load_dataset_columns(bundled_file())
    overlay = deployment_file()
    if overlay is not None:
        try:
            result = load_dataset_columns(overlay, base=result)
        except (OSError, ValueError) as exc:
            logger.warning(
                f"Ignoring column descriptions in {overlay}: {exc}. Using the bundled file only."
            )
    return result[dataset]
