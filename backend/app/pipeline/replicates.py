"""Replicate groups: which barcodes of a run carry the same sample, and what that means.

A sampling occasion normally has one barcode in a run. When it has several, the
``replicate`` column of ``nanopore_runs`` says why: numbered barcodes are replicates of
one sample and are pooled before classification; unnumbered ones are a metadata error
(two samples sharing a label, a mistyped date) until someone says otherwise.

One grouping rule, used by three consumers that must agree with each other:

- the dashboard and launch wizard (``api/discovery.py``), which show a note for a group
  that will be pooled and a warning for one that cannot be;
- pre-launch validation (``api/pipeline.py``), which refuses to launch over a group
  that cannot be pooled;
- the samplesheet builders (``pipeline/samplesheet.py``) and the post-processors, which
  concatenate a group's reads under one name and attach the sample's metadata to the
  result.

The group key is the sample within the run: ``sample_code``, ``sampling_date``,
``protocol_id`` and ``sequencing_kit_id``. Two library preps of one sample (different
protocol or kit) are two groups, as they are two ``sample_id`` values.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# The ``replicate`` value of a dataset row built from a replicate group's pooled reads.
# The row's ``barcode`` then names the members.
POOLED = "pooled"

# Joins the member barcodes into the pooled input's name. The name becomes a directory
# under wf-metagenomics' ``--fastq``, the ``sample`` of a taxprofiler samplesheet row and
# the prefix of the reports both workflows write, so it must be safe in a path and in a
# shell, and must contain no underscore: the Kraken2 post-processing keys reports on the
# text before the first one.
POOL_SEPARATOR = "-"


@dataclass(frozen=True)
class Member:
    barcode: str
    replicate: int | None
    replicate_type: str | None


@dataclass(frozen=True)
class ReplicateGroup:
    """The barcodes of one sample within one run, or within one merged run set."""

    run_accession: str | None  # None when the group spans a merged run set
    sample_code: str
    sampling_date: str
    protocol_id: str | None
    sequencing_kit_id: str | None
    members: tuple[Member, ...]  # numbered members first, by number; then by barcode
    # Barcodes that carry different replicate numbers in different accessions of a
    # merged run set. Always empty for a group built within one accession.
    inconsistent: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return f"{self.sample_code} ({self.sampling_date})"

    @property
    def barcodes(self) -> list[str]:
        return [m.barcode for m in self.members]

    @property
    def numbered(self) -> bool:
        """Several barcodes, every one numbered, no number used twice: pooled as one."""
        return len(self.members) > 1 and self.problem is None

    @property
    def problem(self) -> str | None:
        """Why the group can be neither pooled nor passed through, or None.

        A single barcode is never a problem, whatever its number: its partner may have
        been excluded, and a group of one is just a barcode.
        """
        if self.inconsistent:
            return "inconsistent"
        if len(self.members) < 2:
            return None
        numbers = [m.replicate for m in self.members if m.replicate is not None]
        if not numbers:
            return "unnumbered"
        if len(numbers) < len(self.members):
            return "partial"
        if len(set(numbers)) < len(numbers):
            return "duplicate"
        return None

    @property
    def pooled_name(self) -> str:
        """The name of the pooled input: the member barcodes in replicate order."""
        return POOL_SEPARATOR.join(self.barcodes)

    @property
    def replicate_type(self) -> str:
        """The members' types, ';'-joined when they differ; '' when none is set."""
        return ";".join(sorted({m.replicate_type for m in self.members if m.replicate_type}))

    def describe_problem(self) -> str:
        """The operator-facing sentence for ``problem``; '' when there is none."""
        kind = self.problem
        if kind is None:
            return ""
        where = f"in run {self.run_accession}" if self.run_accession else "in the merged runs"
        prep = ""
        if self.protocol_id or self.sequencing_kit_id:
            prep = " for" + (f" protocol {self.protocol_id}" if self.protocol_id else "") + (
                f" kit {self.sequencing_kit_id}" if self.sequencing_kit_id else ""
            )
        if kind == "unnumbered":
            return (
                f"Sample {self.label}: barcodes {', '.join(self.barcodes)} are linked {where}"
                f"{prep}. If they are replicates of one sample, give each a replicate number "
                f"(1, 2, …) and they will be pooled; otherwise correct the sample or the date "
                f"on the barcode that is wrong."
            )
        if kind == "partial":
            numbered = [m.barcode for m in self.members if m.replicate is not None]
            unnumbered = [m.barcode for m in self.members if m.replicate is None]
            return (
                f"Sample {self.label}: replicate numbers on {', '.join(numbered)} but not on "
                f"{', '.join(unnumbered)} {where}. Number every barcode of the group, or none."
            )
        if kind == "duplicate":
            by_number: dict[int, list[str]] = defaultdict(list)
            for m in self.members:
                if m.replicate is not None:
                    by_number[m.replicate].append(m.barcode)
            repeated = "; ".join(
                f"replicate {n} on {', '.join(bcs)}"
                for n, bcs in sorted(by_number.items())
                if len(bcs) > 1
            )
            return (
                f"Sample {self.label}: {repeated} {where}. Replicate numbers within a group "
                f"must be distinct."
            )
        return (
            f"Sample {self.label}: {', '.join(self.inconsistent)} carry different replicate "
            f"numbers in the runs being merged. Give each barcode the same number in every "
            f"accession of the run."
        )

    def describe_pooling(self) -> str:
        """The note shown for a group that will be pooled."""
        return (
            f"{self.label}: replicates {', '.join(self.barcodes)} will be pooled before "
            f"classification."
        )


def _number(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def replicate_groups(rows: Iterable[Mapping[str, Any]], *, by_run: bool = True) -> list[ReplicateGroup]:
    """Group barcode rows by the sample they carry.

    ``rows`` are mappings with ``run_accession``, ``barcode``, ``sample_code``,
    ``sampling_date``, ``protocol_id``, ``sequencing_kit_id``, ``replicate`` and
    ``replicate_type`` — the shape of ``db.queries.fetch_run_barcodes`` and of
    ``get_metadata_rows_from_db``. Rows without a barcode, a sample code or a date are
    left out: an unlinked barcode is a different error, reported elsewhere.

    With ``by_run`` the key includes the accession, which is the unit the replicate
    numbers are entered and validated in. Without it the rows of a merged run set
    (continuations of one flow cell) fold into one group per sample, a barcode being one
    member however many accessions it appears in; a barcode numbered differently across
    them makes the group ``inconsistent``.

    Groups come back in first-seen order, one per sample, singletons included.
    """
    keyed: dict[tuple, dict[str, dict[str, Any]]] = {}
    for row in rows:
        barcode = (row["barcode"] or "").strip()
        sample_code = row["sample_code"]
        sampling_date = row["sampling_date"]
        if not barcode or not sample_code or not sampling_date:
            continue
        key = (
            row["run_accession"] if by_run else None,
            sample_code,
            sampling_date,
            row["protocol_id"] or None,
            row["sequencing_kit_id"] or None,
        )
        members = keyed.setdefault(key, {})
        entry = members.setdefault(barcode, {"numbers": set(), "types": set()})
        entry["numbers"].add(_number(row["replicate"]))
        if row["replicate_type"]:
            entry["types"].add(row["replicate_type"])

    groups: list[ReplicateGroup] = []
    for (run_accession, sample_code, sampling_date, protocol_id, kit), members in keyed.items():
        inconsistent: list[str] = []
        built: list[Member] = []
        for barcode, entry in members.items():
            numbers = entry["numbers"]
            if len(numbers) > 1:
                inconsistent.append(barcode)
            built.append(
                Member(
                    barcode,
                    next(iter(numbers)) if len(numbers) == 1 else None,
                    ";".join(sorted(entry["types"])) or None,
                )
            )
        built.sort(key=lambda m: (m.replicate is None, m.replicate or 0, m.barcode))
        groups.append(
            ReplicateGroup(
                run_accession,
                sample_code,
                sampling_date,
                protocol_id,
                kit,
                tuple(built),
                tuple(sorted(inconsistent)),
            )
        )
    return groups


def plan_inputs(groups: Iterable[ReplicateGroup], *, pool: bool) -> dict[str, list[str]]:
    """Map each input name to the barcodes concatenated into it.

    With ``pool`` every numbered group becomes one input under its pooled name; every
    other barcode is an input of its own. Without it each barcode is its own input.
    Raises ``ValueError`` on a group with a problem, naming it, because pre-launch
    validation should have refused it and silently processing it would hide the error.
    """
    inputs: dict[str, list[str]] = {}
    for group in groups:
        if group.problem is not None:
            raise ValueError(f"Replicate group cannot be processed. {group.describe_problem()}")
        if pool and group.numbered:
            inputs[group.pooled_name] = group.barcodes
        else:
            for barcode in group.barcodes:
                inputs[barcode] = [barcode]
    return inputs


def pooled_metadata_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The metadata rows a post-processor should attach when replicate groups were pooled.

    Takes the per-barcode rows of ``get_metadata_rows_from_db`` and replaces each
    numbered group's rows with one row for the pooled input: the first member's row,
    with ``barcode`` and ``alias`` naming the pool, ``replicate`` set to ``"pooled"`` and
    ``replicate_type`` the members' types. Rows outside a numbered group pass through
    unchanged, so a lone numbered barcode keeps its number.

    Rows are grouped per accession, like the samplesheet that produced the output, and
    the result keeps the input's order with each pool at its first member's position.
    """
    rows = [dict(r) for r in rows]
    pools: dict[tuple[str | None, str], ReplicateGroup] = {}
    for group in replicate_groups(rows, by_run=True):
        if group.numbered:
            for barcode in group.barcodes:
                pools[(group.run_accession, barcode)] = group

    out: list[dict[str, Any]] = []
    written: set[tuple[str | None, str]] = set()
    for row in rows:
        group = pools.get((row["run_accession"], row["barcode"]))
        if group is None:
            out.append(row)
            continue
        key = (group.run_accession, group.pooled_name)
        if key in written:
            continue
        written.add(key)
        pooled = dict(row)
        pooled["barcode"] = group.pooled_name
        pooled["alias"] = f"{group.sample_code}_{group.pooled_name}"
        pooled["replicate"] = POOLED
        pooled["replicate_type"] = group.replicate_type
        out.append(pooled)
    return out


def replicate_label(value: Any) -> str:
    """The dataset's text form of ``replicate``: '', a number, or ``pooled``.

    The datasets store it as text so that a pooled row and a numbered row fit one
    column; the database keeps the integer.
    """
    if value is None or value == "" or value == POOLED:
        return value or ""
    try:
        if value != value:  # NaN, which a concat of frames can introduce
            return ""
    except TypeError:
        pass
    return str(int(value))
