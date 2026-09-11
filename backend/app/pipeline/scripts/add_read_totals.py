"""Backfill ``classified_reads`` and ``total_reads`` into existing taxonomy feathers.

Post-processing writes the two columns on every row it saves, old rows included, but only
when a country is post-processed again. This command does the same for feathers that
already exist, without a pipeline run and without reading a Kraken2 report: every full
file holds one root row per report, which is all the totals need.

For every ``<name>_taxprofiler.feather`` and ``<name>_ssu.feather`` in the directory:

1. the full file is read and ``kraken_postprocessor.add_read_totals`` applied, which drops
   and recomputes the columns, so running this twice, or on a file post-processing already
   wrote, changes nothing;
2. it is written back with the current column descriptions
   (``dataset_columns.column_metadata("taxprofiler")``), to a temporary file renamed over
   the original, so a failure leaves the old file intact;
3. its ``_target_group`` file is regenerated from it rather than patched, so the two
   cannot disagree;
4. it reports: row count unchanged, every file has a total, files with no classified reads
   listed by name.

Run it where the feathers are, with post-processing idle — in the API container
(``python -m backend.app.pipeline.scripts.add_read_totals /path/to/enlighten/data``) or on a
host that mounts the Enlighten data directory. ``--dry-run`` reports without writing;
``--out DIR`` writes the backfilled files into DIR and leaves the originals untouched.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

from ...parsers.dataset_columns import column_metadata
from ...parsers.kraken_postprocessor import add_read_totals, create_target_groups
from ...parsers.postprocess_common import write_feather

FULL_SUFFIXES = ("_taxprofiler.feather", "_ssu.feather")
TARGET_GROUP_SUFFIX = "_target_group.feather"


def full_files(directory: Path) -> list[Path]:
    """The full taxonomy feathers in *directory*: not the target-group cuts, sorted."""
    return sorted(
        p
        for p in directory.iterdir()
        if p.name.endswith(FULL_SUFFIXES) and not p.name.endswith(TARGET_GROUP_SUFFIX)
    )


def _write_atomically(df: pd.DataFrame, path: Path, columns) -> None:
    tmp = path.with_name(path.name + ".tmp")
    write_feather(df, tmp, columns)
    os.replace(tmp, path)


def backfill_file(path: Path, *, dry_run: bool = False, out_dir: Path | None = None) -> dict:
    """Backfill one full feather and regenerate its target-group file.

    Written in place, or into *out_dir* when given (same file names, source untouched).
    Returns a report dict: ``rows``, ``files``, ``files_without_total`` (should be empty),
    ``files_without_classified`` (names, rate undefined), ``target_group_rows``, and
    ``changed`` (whether the totals differ from what the file held, if it held any).
    """
    before = pd.read_feather(path)
    after = add_read_totals(before)
    if len(after) != len(before):
        raise RuntimeError(f"{path.name}: row count changed from {len(before)} to {len(after)}")

    had_columns = all(c in before.columns for c in ("classified_reads", "total_reads"))
    changed = (
        not had_columns
        or not before["classified_reads"].equals(after["classified_reads"])
        or not before["total_reads"].equals(after["total_reads"])
    )
    per_file = after.groupby("file_name")["classified_reads"].first()
    report = {
        "file": path.name,
        "rows": len(after),
        "files": int(after["file_name"].nunique()),
        "files_without_total": sorted(after.loc[after["total_reads"].isna(), "file_name"].unique()),
        "files_without_classified": sorted(per_file[per_file == 0].index),
        "changed": bool(changed),
    }

    stem = path.name[: -len(".feather")]
    target_groups = create_target_groups([(stem, after)])
    report["target_group_rows"] = len(target_groups[0][1]) if target_groups else 0

    if not dry_run:
        columns = column_metadata("taxprofiler")
        target = (out_dir / path.name) if out_dir else path
        _write_atomically(after, target, columns)
        if target_groups:
            name, frame = target_groups[0]
            _write_atomically(frame, target.with_name(name + ".feather"), columns)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill classified_reads and total_reads into existing taxonomy feathers."
    )
    parser.add_argument("directory", type=Path, help="the Enlighten data directory")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    parser.add_argument(
        "--out", type=Path, default=None,
        help="write the backfilled files here instead of in place (created if missing)",
    )
    args = parser.parse_args(argv)

    directory: Path = args.directory
    if not directory.is_dir():
        print(f"Not a directory: {directory}", file=sys.stderr)
        return 2
    files = full_files(directory)
    if not files:
        print(f"No *_taxprofiler.feather or *_ssu.feather in {directory}", file=sys.stderr)
        return 1

    out_dir: Path | None = args.out
    if out_dir is not None and not args.dry_run:
        if out_dir.resolve() == directory.resolve():
            out_dir = None  # the same folder: write in place as usual
        else:
            out_dir.mkdir(parents=True, exist_ok=True)

    status = 0
    for path in files:
        try:
            r = backfill_file(path, dry_run=args.dry_run, out_dir=out_dir)
        except Exception as exc:  # one bad file must not stop the others
            print(f"{path.name}: FAILED, left unchanged: {exc}", file=sys.stderr)
            status = 1
            continue
        if args.dry_run:
            verb = "would write"
        elif out_dir is not None:
            verb = f"written to {out_dir}"
        else:
            verb = "written" if r["changed"] else "unchanged"
        print(
            f"{r['file']}: {r['rows']} rows, {r['files']} files, "
            f"{r['target_group_rows']} target-group rows — {verb}"
        )
        if r["files_without_total"]:
            print(f"  ERROR: files without a total: {r['files_without_total']}", file=sys.stderr)
            status = 1
        for name in r["files_without_classified"]:
            print(f"  no classified reads (rate undefined): {name}")
    return status


if __name__ == "__main__":
    sys.exit(main())
