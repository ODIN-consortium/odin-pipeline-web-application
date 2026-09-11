"""
AMR (Antimicrobial Resistance) post-processing service.

After a wf-metagenomics AMR pipeline run completes, this module:

  1. Searches the Nextflow output directory for AMR JSON files (one per barcode).
  2. Parses each JSON and enriches results with sample metadata from the SQLite database.
  3. Merges the new data with any existing amr_data.feather (incremental update).
  4. Writes amr_data.feather and amr_data_summary_by_resistance.feather to
     enlighten_data_path.
"""

from __future__ import annotations

import glob
import json
import logging
import os
from pathlib import Path

import pandas as pd

from ..pipeline.replicates import pooled_metadata_rows, replicate_label
from .dataset_columns import column_metadata
from .kraken_parser import read_kraken2_report
from .kraken_postprocessor import (
    KRAKEN2_COLUMN_NAMES,
    add_metadata,
    merge_with_existing_file,
    save_subsets,
)
from .postprocess_common import (
    deduplicate_rows,
    load_postprocess_metadata,
    make_postprocess_logger,
    write_feather,
    write_postprocess_sentinel,
)

logger = logging.getLogger(__name__)

# Log-line prefix for AMR post-processing output (highlighted by the frontend).
_AMR_LOG_MARKER = "[ODIN-POST-AMR]"


# ── Drug-class short codes ────────────────────────────────────────────────────
# JOIN KEY — codes are assigned per dataset, and a code lengthens only when a colliding
# class is present in that dataset, so one class may carry different codes in two
# feathers; a fixed code table kept elsewhere agrees with these codes only on the data it
# was made from. Consumers
# join and group on `resistance`, never on `short_name` — short_name is an axis label
# (its column description says so).
#
# 'y' counts as a vowel: it is a consonant in "glycopeptide" only by spelling, and
# treating it as one collapses that class and "glycylcycline" onto the same code.
_VOWELS = frozenset("aeiouy")


def _consonant_skeleton(name: str) -> str:
    """First letter followed by every consonant after it."""
    return name[0] + "".join(c for c in name[1:] if c.isalpha() and c not in _VOWELS)


def _code_candidates(name: str):
    """Codes for `name`, best first.

    The first choice is the usual four characters. Where that is already taken, the
    fourth character is replaced by the *next* consonant rather than a fifth being
    appended, so codes stay one width and line up on an axis::

        cephalosporin  c p h | l s p r n   ->  cphl, then cphs, cphp, cphr, cphn
        cephalexin     c p h | l x n       ->  cphl, then cphx, cphn

    Only once every consonant has been tried does the code lengthen, and that is a
    backstop rather than the normal path.

    A name with fewer than four consonants (e.g. "penam" -> "pnm") cannot fill four
    characters from its skeleton, so the plain name is used instead; that keeps
    "penam" and "penem" apart without naming either of them in the code.
    """
    skeleton = _consonant_skeleton(name)
    base = skeleton if len(skeleton) >= 4 else name
    prefix = base[:3]
    for char in base[3:]:                       # same width, later consonant
        yield prefix + char
    for length in range(5, len(base) + 1):      # backstop: lengthen
        yield base[:length]
    yield base.ljust(4, "x")


def assign_drug_codes(names) -> dict:
    """Map each drug-class name to a short code, lengthening to avoid collisions.

    Four characters where that is unambiguous; where two classes would share a code
    the later one takes another consonant, and so on ("cephalosporin" -> 'cphl',
    "cephalexin" -> 'cphlx'). Nothing about the vocabulary is hard-coded, so a study
    with entirely different drug classes is handled the same way.

    Names are sorted before codes are handed out, so the mapping depends only on
    *which* classes are present, never on the order the files happened to be read
    in. It does still depend on the set: a class that shares a 4-character code with
    another only lengthens when that other class is present too. Codes are therefore
    comparable within a dataset, and `resistance` remains the key to use across them.
    """
    codes, taken = {}, set()
    for name in sorted({n for n in names if n}):
        for candidate in _code_candidates(name.lower().strip()):
            if candidate not in taken:
                codes[name] = candidate
                taken.add(candidate)
                break
        else:
            codes[name] = next(iter(_code_candidates(name.lower().strip())))
    return codes


def _make_drug_code(name: str) -> str:
    """The 4-character code for one name, with no collision resolution.

    Only meaningful for a single name in isolation; use assign_drug_codes() when
    several classes are in play, because that is what lengthens a colliding code.
    """
    name = name.lower().strip()
    if not name:
        return ""
    return next(iter(_code_candidates(name)))


def _codes_are_distinct(names) -> dict:
    """Return {code: [names]} for every code that more than one name maps to.

    Uses assign_drug_codes(), so this reports only collisions that lengthening
    could not resolve — two classes with the same consonant skeleton.
    """
    codes = assign_drug_codes(names)
    by_code = {}
    for name, code in codes.items():
        by_code.setdefault(code, []).append(name)
    return {c: sorted(n) for c, n in by_code.items() if len(n) > 1}


def add_short_names(df: pd.DataFrame) -> pd.DataFrame:
    """Fill ``short_name`` from every drug class present in ``df``.

    Codes have to be assigned over the whole dataset, not one file at a time: a
    class that only appears in one barcode still has to get the same code as it
    would anywhere else in the same feather.
    """
    if df.empty or "resistance" not in df.columns:
        return df
    classes = {
        t.strip()
        for value in df["resistance"].dropna()
        for t in str(value).split(";")
        if t.strip()
    }
    codes = assign_drug_codes(classes)
    unresolved = _codes_are_distinct(classes)
    if unresolved:
        logger.warning(
            f"{_AMR_LOG_MARKER} drug classes share a short code even after "
            f"lengthening: {unresolved}. short_name is ambiguous for these; group "
            f"on 'resistance' instead."
        )
    df["short_name"] = df["resistance"].map(
        lambda v: ";".join(codes.get(t.strip(), "") for t in str(v).split(";") if t.strip())
        if pd.notna(v)
        else None
    )
    return df


# ── Read counts ───────────────────────────────────────────────────────────────
# wf-metagenomics reports one entry in a gene's ``meta`` list per alignment, and repeats
# the gene's total in ``count`` on every one of them. One row per alignment would
# therefore carry a column that is already a total, and summing it over the rows of a
# gene squares the read count. The parser aggregates the alignments instead and emits one
# row per (barcode, gene, drug-class profile), so every count on a row is that row's own
# and sums correctly.
#
# ``SEQUENCE`` is the read identifier, not a reference accession. One read can align to a
# gene more than once, so alignments overstate reads and the two are counted separately.
#
# A gene's ``meta`` entries do not all have to agree on ``RESISTANCE``: CARD descriptions
# such as "From the Lahey list of beta-lactamases." are shared by several genes, and the
# workflow keys ``results`` on the description. ``resistance`` is part of the row key so
# those stay apart, because ``count`` alone could not be split between them.


def _distinct_reads(hits: list[dict]) -> int:
    """How many reads produced these alignments.

    Alignments carrying the same ``SEQUENCE`` come from one read. An alignment with no
    identifier cannot be matched to any other and counts as one read of its own.
    """
    identified = {h["SEQUENCE"] for h in hits if h.get("SEQUENCE")}
    anonymous = sum(1 for h in hits if not h.get("SEQUENCE"))
    return len(identified) + anonymous


def _mean(values) -> float | None:
    """Mean of the numeric values, or None when there are none."""
    numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return sum(numbers) / len(numbers) if numbers else None


def _kraken2_report_beside(amr_json_path: str, barcode: str) -> str:
    """The barcode's Kraken2 report from the same wf-metagenomics AMR run.

    ``<run>/amr/<barcode>.amr.json`` sits beside ``<run>/kraken2/<barcode>.kraken2.report.txt``
    — the same reads, classified by the same run with the same database.

    Raises when the run wrote no such report. ``--amr`` always runs the classification
    step, so a missing report means the output directory is not the one it looks like,
    and a read count nobody can normalise is worse than no row at all.
    """
    run_dir = os.path.dirname(os.path.dirname(os.path.abspath(amr_json_path)))
    candidate = os.path.join(run_dir, "kraken2", f"{barcode}.kraken2.report.txt")
    if not os.path.isfile(candidate):
        raise FileNotFoundError(
            f"No Kraken2 report for barcode '{barcode}': expected '{candidate}' beside "
            f"'{amr_json_path}'. It is the only denominator for rpm — a taxprofiler run "
            f"over the same reads classifies a several-fold different number of them."
        )
    return candidate


def read_barcode_read_totals(report_path: str) -> tuple[int, int]:
    """The two read totals for one barcode, from its Kraken2 report.

    Returns ``(classified_reads, total_reads)``: the clade count on the root row
    (NCBI tax ID 1), and that plus the unclassified row (tax ID 0) — every read the step
    was given.

    Both are written to the feather and neither is turned into a rate here. Which of them
    normalises the AMR counts is a question about what the numerator is commensurate
    with, and the figures answer it: a stored ratio would fix one answer into the file,
    and no later analysis could get the raw numbers back. The taxonomy datasets store
    ``No of Frags root`` the same way and let the consumer form RPM.

    The share Kraken2 can classify varies from 2% to 95% across these barcodes, so the
    two totals are not interchangeable and the column names say which is which.

    Only the AMR run's own report answers this. A taxprofiler run over the same input
    classifies a different number of reads — several-fold different, and by a factor that
    varies per barcode — so its totals are not a substitute.

    Raises when the report cannot be read, has no root row, or covers no reads at all.
    """
    report = read_kraken2_report(report_path, KRAKEN2_COLUMN_NAMES)
    root = report[report["NCBI tax ID"] == 1]
    if root.empty:
        raise ValueError(
            f"Kraken2 report has no root row (tax ID 1), so it gives no read total to "
            f"normalise against: '{report_path}'"
        )
    classified = int(root["No of Frags root"].iloc[0])
    unclassified_row = report[report["NCBI tax ID"] == 0]
    unclassified = int(unclassified_row["No of Frags root"].iloc[0]) if not unclassified_row.empty else 0
    total = classified + unclassified
    if total <= 0:
        raise ValueError(
            f"Kraken2 report covers no reads at all, so there is nothing to normalise "
            f"against, yet the AMR output lists genes for this barcode: '{report_path}'"
        )
    return classified, total


# ── JSON parsing ──────────────────────────────────────────────────────────────


def parse_amr_json_file(file_path: str, run_accession: str | None = None) -> list[dict]:
    """
    Parse a single AMR JSON file produced by epi2me-labs/wf-metagenomics --amr.

    Returns one record per (barcode, gene, drug-class profile), with the gene's alignments
    aggregated into ``read_count`` and ``alignment_count``. A barcode in which the workflow
    found no gene at all gets one row of its own, carrying the read totals and nothing else,
    so that it can be counted as analysed.

``classified_reads`` and ``total_reads`` come from the Kraken2 report the same AMR run
    wrote beside the JSON. Both are raw counts: the rate is formed by whoever normalises,
    not stored here. A barcode with genes but no readable report raises, so every row
    carries real read totals or none is written.
    """
    try:
        with open(file_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        logger.warning(f"Could not read AMR JSON '{file_path}': {exc}")
        return []

    records: list[dict] = []
    for barcode_key, barcode_data in data.items():
        if not isinstance(barcode_data, dict):
            continue
        pass_status = barcode_data.get("pass")
        results = barcode_data.get("results", {})
        if not results:
            # ``pass`` is False exactly when abricate's results table was empty, so this
            # barcode is a measurement — no gene cleared the identity and coverage
            # thresholds — rather than a gap. One row records it, with no gene and no
            # reads, so a denominator can count the barcode and tell "nothing found"
            # from "not analysed". Without it every count drawn from this file is of
            # barcodes that detected something, which is not the same question.
            #
            # It needs the read totals as much as any other row: "no gene in 47 reads"
            # and "no gene in a million" are different findings, and only the report
            # separates them. Where there is no readable report the barcode is skipped
            # rather than written with empty totals, keeping the guarantee that every
            # row carries real ones.
            try:
                classified_reads, total_reads = read_barcode_read_totals(
                    _kraken2_report_beside(file_path, barcode_key)
                )
            except (FileNotFoundError, ValueError) as exc:
                logger.warning(
                    f"{_AMR_LOG_MARKER} barcode '{barcode_key}' of '{file_path}' has no AMR "
                    f"gene and no usable read total, so it is not recorded and will be "
                    f"missing from any count of barcodes analysed: {exc}"
                )
                continue
            records.append(
                {
                    "run_accession": run_accession,
                    "barcode": barcode_key,
                    "pass_status": pass_status,
                    "gene_description": None,
                    "multi_resistance": False,
                    "resistance": None,
                    "short_name": None,
                    "read_count": 0,
                    "alignment_count": 0,
                    "classified_reads": classified_reads,
                    "total_reads": total_reads,
                    "coverage_percent": None,
                    "identity_percent": None,
                    "file": file_path,
                }
            )
            continue
        classified_reads, total_reads = read_barcode_read_totals(
            _kraken2_report_beside(file_path, barcode_key)
        )
        for gene_description, gene_data in results.items():
            if not isinstance(gene_data, dict):
                continue
            by_resistance: dict[str | None, list[dict]] = {}
            for meta in gene_data.get("meta", []):
                by_resistance.setdefault(meta.get("RESISTANCE"), []).append(meta)
            for resistance_all, hits in by_resistance.items():
                tokens = resistance_all.split(";") if resistance_all else [None]
                reads = _distinct_reads(hits)
                records.append(
                    {
                        "run_accession": run_accession,
                        "barcode": barcode_key,
                        "pass_status": pass_status,
                        "gene_description": gene_description,
                        "multi_resistance": len(tokens) > 1 if tokens else False,
                        "resistance": resistance_all,
                        "short_name": None,  # filled by add_short_names() over the whole dataset
                        "read_count": reads,
                        "alignment_count": len(hits),
                        "classified_reads": classified_reads,
                        "total_reads": total_reads,
                        "coverage_percent": _mean(h.get("%COVERAGE") for h in hits),
                        "identity_percent": _mean(h.get("%IDENTITY") for h in hits),
                        "file": file_path,
                    }
                )
    return records


def _find_amr_json(output_path: str, barcode: str) -> list[str]:
    """Find {barcode}.amr.json for one run, inside that run's own output directory.

    ``output_path`` is this launch's output directory
    (``outputs_wf_metagenomics_amr/{sampleName}_{slug}``), so the workflow writes the
    barcode's JSON directly under ``amr/``. That exact location is authoritative: when it
    holds the file, nothing below it is searched, so a nested directory cannot contribute a
    second run's output under this run's accession. The recursive pattern remains only as a
    fallback for a layout that nests the workflow output, and never leaves ``output_path``.
    """
    direct = os.path.join(output_path, "amr", f"{barcode}.amr.json")
    if os.path.isfile(direct):
        return [direct]
    nested = glob.glob(
        os.path.join(output_path, "**", "amr", f"{barcode}.amr.json"), recursive=True
    )
    return sorted(set(nested))


# ── Main entry-point ──────────────────────────────────────────────────────────


def run_amr_postprocessing(
    run_accessions: list[str],
    output_path: str,
    enlighten_data_path: str,
    db_path: str,
    log_file: Path,
    *,
    pool_replicates: bool = True,
) -> None:
    """
    Post-process a completed wf-metagenomics AMR run.

    Searches output_path recursively for AMR JSON files, enriches them with
    sample/site metadata from the SQLite database, merges with any existing
    amr_data.feather, and writes updated Feather files to enlighten_data_path.

    ``pool_replicates`` must say what the launch did (the ``amr_replicates``
    setting at the time): pooled, a replicate group's JSON is named after its
    barcodes and is read once under a row whose ``replicate`` is ``pooled``;
    per barcode, each member's JSON is read under its own row, which keeps its
    replicate number.

    Appends progress to log_file.  Never raises — errors are logged so the
    pipeline_run stays in 'done' status even if post-processing fails.
    """

    _log = make_postprocess_logger(log_file, _AMR_LOG_MARKER)

    _log("Starting AMR post-processing...")

    # ── 1. Get metadata from SQLite ───────────────────────────────────────────
    metadata_rows = load_postprocess_metadata(
        db_path, run_accessions, _log, skip_note="Skipping AMR post-processing."
    )
    if not metadata_rows:
        return
    if pool_replicates:
        metadata_rows = pooled_metadata_rows(metadata_rows)
        for row in metadata_rows:
            if row["replicate"] == "pooled":
                _log(f"Pooled replicates: {row['barcode']} ({row['sample_code']} {row['sampling_date']})")
    else:
        _log("Replicate barcodes screened per barcode (amr_replicates = per_barcode).")

    # ── 2. Parse AMR JSON files and build combined DataFrame ──────────────────
    combined_df = pd.DataFrame()
    processed_run_accessions: set[str] = set()
    missing_files: set[str] = set()

    for row in metadata_rows:
        run_accession = row["run_accession"]
        barcode = row["barcode"]
        if not barcode:
            continue

        _log(f"Processing run_accession={run_accession}, barcode={barcode}")
        files = _find_amr_json(output_path, barcode)
        if not files:
            missing_files.add(f"{run_accession}/{barcode}")
            continue

        for file_path in files:
            records = parse_amr_json_file(file_path, run_accession=run_accession)
            if not records:
                continue
            tmp = pd.DataFrame(records)
            tmp["incomplete_data"] = False
            # Attach metadata columns from the DB row
            tmp = add_metadata(tmp, row)
            frames = [f for f in [combined_df, tmp] if not f.empty]
            combined_df = pd.concat(frames, axis=0)

        processed_run_accessions.add(run_accession)

    if missing_files:
        _log(f"Warning: no AMR JSON files found for: {sorted(missing_files)}")

    if combined_df.empty:
        _log("No AMR data parsed. Post-processing complete with no output written.")
        return

    combined_df = add_short_names(combined_df)
    if "replicate" in combined_df.columns:
        combined_df["replicate"] = combined_df["replicate"].map(replicate_label)

    # ── 3. Deduplicate ────────────────────────────────────────────────────────
    combined_df = deduplicate_rows(
        combined_df, _log, ignore_columns={"incomplete_data", "run_accession"}
    )

    # ── 4. Merge with existing amr_data.feather ───────────────────────────────
    os.makedirs(enlighten_data_path, exist_ok=True)
    amr_feather = os.path.join(enlighten_data_path, "amr_data.feather")
    _log(f"Merging with existing data in {enlighten_data_path} ...")
    merged_df = merge_with_existing_file(combined_df, processed_run_accessions, amr_feather)

    # ── 5. Save main dataset ──────────────────────────────────────────────────
    save_subsets([("amr_data", merged_df)], enlighten_data_path, "feather", column_metadata("amr"))
    _log(f"Saved amr_data.feather ({len(merged_df)} rows)")

    # ── 6. Build and save resistance summary ─────────────────────────────────
    _create_amr_summary(merged_df, enlighten_data_path, _log)

    _log("AMR post-processing complete.")

    write_postprocess_sentinel(output_path)


def _create_amr_summary(df: pd.DataFrame, output_path: str, log_fn) -> None:
    """Summarise by resistance gene and save amr_data_summary_by_resistance.feather."""
    if "resistance" not in df.columns:
        log_fn("No 'resistance' column — skipping summary.")
        return

    agg_spec: dict = {
        "run_accession": "nunique",
        "barcode": "nunique",
    }
    for col, agg in (
        ("read_count", "sum"),
        ("alignment_count", "sum"),
        ("coverage_percent", "mean"),
        ("identity_percent", "mean"),
    ):
        if col in df.columns:
            agg_spec[col] = agg
    if "sample_code" in df.columns:
        agg_spec["sample_code"] = lambda x: "; ".join(sorted(set(x.dropna().astype(str))))

    try:
        grouped = df.groupby("resistance")
        # "n_detections" is the group size — how many gene rows of amr_data carry this
        # resistance profile. It counts genes, not reads; "read_count" beside it holds
        # the reads, and the two differ by orders of magnitude.
        #
        # It is inserted rather than folded into agg_spec because an aggregation
        # keyed on "resistance" produces a column of that name, which collides with
        # the groupby index and makes reset_index() raise.
        summary = grouped.agg(agg_spec).round(2)
        summary.insert(0, "n_detections", grouped.size())
        summary.sort_index(inplace=True)
        out_file = os.path.join(output_path, "amr_data_summary_by_resistance.feather")
        write_feather(summary.reset_index(), out_file, column_metadata("amr_summary"))
        log_fn(f"Saved resistance summary ({len(summary)} rows) → {out_file}")
    except Exception as exc:
        log_fn(f"Warning: could not create resistance summary: {exc}")
