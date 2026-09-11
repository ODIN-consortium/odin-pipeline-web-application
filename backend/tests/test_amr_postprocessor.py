"""The AMR dataset: drug-class codes, read counts, depth normalisation, and the summary.

Two things about the wf-metagenomics AMR JSON drive most of what is asserted here. A
gene's total is repeated on every entry of its "meta" list, so counts have to be
aggregated rather than carried per alignment; and "SEQUENCE" is the read identifier, so
reads and alignments are separate numbers.

_create_amr_summary must not aggregate a "resistance" column while grouping by
"resistance": pandas rejects that once reset_index() runs ("'resistance' is both an
index level and a column label, which is ambiguous"). The exception is caught and
logged as a warning, so the failure mode is a summary file that is silently absent
rather than an error.
"""

import json
import logging

import pandas as pd
import pytest

from backend.app.parsers.amr_postprocessor import (
    _create_amr_summary,
    _make_drug_code,
    add_short_names,
    assign_drug_codes,
    parse_amr_json_file,
)


def test_create_amr_summary_does_not_raise(tmp_path):
    df = pd.DataFrame(
        {
            "resistance": ["tetracycline", "tetracycline", "beta-lactam"],
            "run_accession": ["run1", "run2", "run1"],
            "barcode": ["barcode01", "barcode02", "barcode01"],
            "sequence": ["ACGT", "ACGT", "TTTT"],
            "coverage_percent": [95.0, 90.0, 88.0],
            "identity_percent": [99.0, 98.0, 97.0],
            "sample_code": ["S1", "S2", "S1"],
        }
    )
    messages = []
    _create_amr_summary(df, str(tmp_path), messages.append)

    out_file = tmp_path / "amr_data_summary_by_resistance.feather"
    assert out_file.exists()
    assert not any("Warning" in m for m in messages)

    summary = pd.read_feather(out_file)
    assert set(summary["resistance"]) == {"tetracycline", "beta-lactam"}
    tet_row = summary.loc[summary["resistance"] == "tetracycline"].iloc[0]
    assert tet_row["n_detections"] == 2
    assert tet_row["run_accession"] == 2
    assert tet_row["barcode"] == 2
    # "count" would be read-counts, which this summary does not carry.
    assert "count" not in summary.columns


class TestDrugCodes:
    """short_name: a display abbreviation of `resistance`, assigned per dataset.

    Nothing about the drug-class vocabulary is hard-coded, so a study with entirely
    different classes is handled the same way. Codes are handed out over the sorted
    set of classes present, so the same set always yields the same mapping.
    """

    @pytest.mark.parametrize("name,expected", [
        ("fluoroquinolone", "flrq"), ("carbapenem", "crbp"),
        ("cephalosporin", "cphl"), ("cephamycin", "cphm"),
        ("aminoglycoside", "amng"), ("aminocoumarin", "amnc"),
        ("glycopeptide", "glcp"), ("glycylcycline", "glcl"),
        ("nitroimidazole", "ntrm"), ("nitrofuran", "ntrf"),
    ])
    def test_a_lone_class_gets_first_letter_plus_three_consonants(self, name, expected):
        assert _make_drug_code(name) == expected

    def test_y_is_a_vowel_so_the_glyco_classes_differ(self):
        """Treating 'y' as a consonant collapses both onto 'glyc'.

        Glycopeptide (cell wall, e.g. vancomycin) and glycylcycline (protein
        synthesis, e.g. tigecycline) are different drug classes.
        """
        assert _make_drug_code("glycopeptide") != _make_drug_code("glycylcycline")

    def test_a_class_with_too_few_consonants_falls_back_to_its_name(self):
        """'penam' and 'penem' share the skeleton 'pnm'; the plain name separates them."""
        codes = assign_drug_codes(["penam", "penem"])
        assert codes["penam"] != codes["penem"]
        assert {len(c) for c in codes.values()} == {4}

    def test_a_clash_swaps_the_fourth_character_rather_than_growing(self):
        codes = assign_drug_codes(["cephalosporin", "cephalexin"])
        assert set(codes.values()) == {"cphl", "cphs"}
        assert {len(c) for c in codes.values()} == {4}

    def test_codes_lengthen_only_once_the_four_character_forms_run_out(self):
        """Names with the same consonant skeleton exhaust cphl/cphx/cphn first."""
        names = ["cephalexin", "cephalexina", "cephalexine", "cephalexino"]
        codes = assign_drug_codes(names)
        assert len(set(codes.values())) == len(names)
        assert max(len(c) for c in codes.values()) > 4

    def test_the_same_set_always_yields_the_same_mapping(self):
        """Names are sorted before codes are assigned, so file order cannot matter."""
        names = ["tetracycline", "cephalexin", "cephalosporin", "penam", "penem"]
        first = assign_drug_codes(names)
        for shuffled in ([*reversed(names)], sorted(names), names[2:] + names[:2]):
            assert assign_drug_codes(shuffled) == first

    def test_a_code_widens_only_when_the_clashing_class_is_present(self):
        """The trade-off of having no fixed vocabulary, pinned so it stays deliberate."""
        assert assign_drug_codes(["cephalosporin"])["cephalosporin"] == "cphl"
        assert assign_drug_codes(["cephalosporin", "cephalexin"])["cephalosporin"] == "cphs"

    def test_short_names_are_assigned_across_the_whole_dataset(self):
        """One file's classes must not decide the codes another file's rows get."""
        df = pd.DataFrame({"resistance": ["cephalosporin;tetracycline", "cephalexin", None]})

        out = add_short_names(df)

        assert out.loc[0, "short_name"] == "cphs;ttrc"
        assert out.loc[1, "short_name"] == "cphl"
        assert pd.isna(out.loc[2, "short_name"])

    def test_an_empty_frame_is_returned_untouched(self):
        assert add_short_names(pd.DataFrame()).empty

    def test_a_collision_never_stops_the_data_being_written(self, caplog):
        """short_name is a convenience; `resistance` is always unambiguous."""
        df = pd.DataFrame({"resistance": ["cephalexin", "cephalexin "]})

        with caplog.at_level(logging.WARNING):
            out = add_short_names(df)

        assert len(out) == 2
        assert out["short_name"].notna().all()


# ── Grain and read counts ─────────────────────────────────────────────────────
# wf-metagenomics repeats a gene's total in "count" on every one of its "meta" entries.
# The parser aggregates those entries, so a row's counts are its own and sum correctly.

ROOT_REPORT = (
    " 50.00\t500\t500\tU\t0\tunclassified\n"
    " 50.00\t500\t2\tR\t1\troot\n"
    " 49.80\t498\t498\tD\t2\t  Bacteria\n"
)


def _amr_run(tmp_path, results, barcode="barcode01", report=ROOT_REPORT):
    """Lay out one wf-metagenomics AMR run and return the path of its JSON."""
    run = tmp_path / "run"
    (run / "amr").mkdir(parents=True)
    json_path = run / "amr" / f"{barcode}.amr.json"
    body = {"pass": True, "results": results} if results else {"pass": False}
    json_path.write_text(json.dumps({barcode: body}))
    if report is not None:
        (run / "kraken2").mkdir(parents=True)
        (run / "kraken2" / f"{barcode}.kraken2.report.txt").write_text(report)
    return str(json_path)


def _hit(read, resistance="tetracycline", coverage=90.0, identity=98.0):
    return {
        "RESISTANCE": resistance,
        "SEQUENCE": read,
        "START": 1,
        "END": 1200,
        "%COVERAGE": coverage,
        "%IDENTITY": identity,
    }


class TestGeneRows:
    def test_a_gene_becomes_one_row_however_many_alignments_it_has(self, tmp_path):
        path = _amr_run(tmp_path, {"tetA": {"count": 3, "meta": [_hit(f"read{i}") for i in range(3)]}})
        rows = parse_amr_json_file(path, run_accession="run1")
        assert len(rows) == 1
        assert rows[0]["read_count"] == 3
        assert rows[0]["alignment_count"] == 3

    def test_a_read_aligning_twice_is_one_read_and_two_alignments(self, tmp_path):
        path = _amr_run(
            tmp_path,
            {"tetA": {"count": 3, "meta": [_hit("readA"), _hit("readA"), _hit("readB")]}},
        )
        (row,) = parse_amr_json_file(path, run_accession="run1")
        assert row["read_count"] == 2
        assert row["alignment_count"] == 3

    def test_an_alignment_without_a_read_id_still_counts_as_a_read(self, tmp_path):
        path = _amr_run(
            tmp_path, {"tetA": {"count": 2, "meta": [_hit(None), _hit(None)]}}
        )
        (row,) = parse_amr_json_file(path, run_accession="run1")
        assert row["read_count"] == 2

    def test_one_description_covering_two_profiles_stays_two_rows(self, tmp_path):
        # CARD descriptions such as "From the Lahey list of beta-lactamases." are shared
        # by several genes, and their meta entries then disagree on RESISTANCE. The gene
        # total could not be split between them, so the profile is part of the row key.
        path = _amr_run(
            tmp_path,
            {
                "From the Lahey list of beta-lactamases.": {
                    "count": 3,
                    "meta": [
                        _hit("readA", "cephalosporin"),
                        _hit("readB", "cephalosporin;penam"),
                        _hit("readC", "cephalosporin;penam"),
                    ],
                }
            },
        )
        rows = parse_amr_json_file(path, run_accession="run1")
        by_profile = {r["resistance"]: r for r in rows}
        assert by_profile["cephalosporin"]["read_count"] == 1
        assert by_profile["cephalosporin;penam"]["read_count"] == 2
        assert by_profile["cephalosporin"]["multi_resistance"] is False
        assert by_profile["cephalosporin;penam"]["multi_resistance"] is True

    def test_summing_read_count_over_rows_gives_the_reads_in_the_barcode(self, tmp_path):
        # The regression the per-alignment layout caused: summing a column that was
        # already a gene total squared it. Three genes of 2, 3 and 4 reads are 9 reads,
        # not 4 + 9 + 16.
        path = _amr_run(
            tmp_path,
            {
                f"gene{n}": {"count": n, "meta": [_hit(f"g{n}r{i}") for i in range(n)]}
                for n in (2, 3, 4)
            },
        )
        rows = parse_amr_json_file(path, run_accession="run1")
        assert sum(r["read_count"] for r in rows) == 9

    def test_coverage_and_identity_are_averaged_over_the_alignments(self, tmp_path):
        path = _amr_run(
            tmp_path,
            {
                "tetA": {
                    "count": 2,
                    "meta": [_hit("readA", coverage=80.0, identity=90.0),
                             _hit("readB", coverage=90.0, identity=100.0)],
                }
            },
        )
        (row,) = parse_amr_json_file(path, run_accession="run1")
        assert row["coverage_percent"] == 85.0
        assert row["identity_percent"] == 95.0


class TestReadTotals:
    def test_both_totals_are_stored_and_no_rate_is(self, tmp_path):
        # ROOT_REPORT: 500 unclassified, 500 classified.
        path = _amr_run(tmp_path, {"tetA": {"count": 1, "meta": [_hit("readA")]}})
        (row,) = parse_amr_json_file(path, run_accession="run1")
        assert row["classified_reads"] == 500     # the root clade count alone
        assert row["total_reads"] == 1000         # plus the unclassified row
        assert "rpm" not in row                   # the consumer forms the rate

    def test_a_barcode_with_genes_but_no_report_fails(self, tmp_path):
        # Read counts nobody can normalise are worse than no row: beside rates from other
        # barcodes they would silently understate this sample.
        path = _amr_run(tmp_path, {"tetA": {"count": 1, "meta": [_hit("readA")]}}, report=None)
        with pytest.raises(FileNotFoundError, match="No Kraken2 report for barcode"):
            parse_amr_json_file(path, run_accession="run1")

    def test_a_report_without_a_root_row_fails(self, tmp_path):
        path = _amr_run(
            tmp_path,
            {"tetA": {"count": 1, "meta": [_hit("readA")]}},
            report=" 100.00\t500\t500\tU\t0\tunclassified\n",
        )
        with pytest.raises(ValueError, match="no root row"):
            parse_amr_json_file(path, run_accession="run1")

    def test_a_barcode_whose_classified_share_is_zero_still_has_a_total(self, tmp_path):
        # Kraken2 recognising nothing is no reason to refuse the row: total_reads still
        # counts the reads, and it is the CARD search, not Kraken2, that found the gene.
        path = _amr_run(
            tmp_path,
            {"tetA": {"count": 1, "meta": [_hit("readA")]}},
            report=" 100.00\t500\t500\tU\t0\tunclassified\n  0.00\t0\t0\tR\t1\troot\n",
        )
        (row,) = parse_amr_json_file(path, run_accession="run1")
        assert row["classified_reads"] == 0
        assert row["total_reads"] == 500

    def test_a_report_covering_no_reads_at_all_fails(self, tmp_path):
        path = _amr_run(
            tmp_path,
            {"tetA": {"count": 1, "meta": [_hit("readA")]}},
            report="  0.00\t0\t0\tU\t0\tunclassified\n  0.00\t0\t0\tR\t1\troot\n",
        )
        with pytest.raises(ValueError, match="covers no reads"):
            parse_amr_json_file(path, run_accession="run1")

    def test_a_barcode_with_no_gene_becomes_one_row_carrying_its_read_totals(self, tmp_path):
        # abricate writes pass: false when its results table is empty, so this barcode was
        # analysed and carried no resistance gene. The row is what lets a denominator count
        # it; without it every count is of barcodes that detected something.
        path = _amr_run(tmp_path, {})
        rows = parse_amr_json_file(path, run_accession="run1")
        assert len(rows) == 1
        row = rows[0]
        assert row["pass_status"] is False
        assert row["resistance"] is None and row["gene_description"] is None
        assert row["classified_reads"] == 500 and row["total_reads"] == 1000

    def test_a_barcode_with_no_gene_has_zero_reads_not_a_missing_count(self, tmp_path):
        # Consumers filter on read_count before they touch resistance, and one of them
        # raises on a missing count rather than dropping the row as below threshold.
        path = _amr_run(tmp_path, {})
        row = parse_amr_json_file(path, run_accession="run1")[0]
        assert row["read_count"] == 0
        assert row["alignment_count"] == 0

    def test_a_barcode_with_no_gene_and_no_report_is_skipped_not_written_empty(self, tmp_path, caplog):
        # "No gene in 47 reads" and "no gene in a million" are different findings, so a row
        # without real totals would be worse than no row. It is logged, not silent.
        path = _amr_run(tmp_path, {}, report=None)
        with caplog.at_level(logging.WARNING):
            assert parse_amr_json_file(path, run_accession="run1") == []
        assert "no usable read total" in caplog.text

    def test_the_rows_of_a_barcode_with_no_gene_have_the_same_columns(self, tmp_path):
        empty = parse_amr_json_file(_amr_run(tmp_path / "a", {}), run_accession="run1")[0]
        found = parse_amr_json_file(
            _amr_run(tmp_path / "b", {"tetA": {"count": 1, "meta": [_hit("readA")]}}),
            run_accession="run1",
        )[0]
        assert empty.keys() == found.keys()
