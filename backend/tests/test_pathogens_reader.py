"""The pathogen list can be YAML (preferred) or the older Excel layout, and both annotate
Kraken2 rows identically.

The YAML is the schema of the project's pathogen_group_map.yaml: a ``pathogens`` list
with ``taxid``, ``pathogen_group``, ``scientific_name``, ``type``, ``target_group`` and
optional ``priority``. The deployment profile ships it; a field installation that still
has ``pathogens.xlsx`` keeps working through the default-path fallback.
"""

from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from backend.app.parsers.kraken_postprocessor import (
    add_pathogens,
    kraken2_to_dataframe,
    read_pathogens,
)
from backend.app.settings_resolver import get_defaults

YAML = """\
pathogens:
  - taxid: 573
    scientific_name: "Klebsiella pneumoniae"
    type: bacteria
    target_group: AMR
    priority: WP2-priority
    pathogen_group: "ESBL/Carbapenemase producing K. pneumoniae"
  - taxid: 127906
    scientific_name: "Vibrio cholerae O1"
    type: bacteria
    target_group: waterborne
    pathogen_group: "Vibrio cholerae"
  - taxid: 5807
    scientific_name: "Cryptosporidium parvum"
    type: eukaryotic
    target_group: waterborne
    pathogen_group: "Cryptosporidium"
"""


def _write_xlsx(path: Path) -> None:
    """The same three entries in the Excel layout read_pathogens has always accepted."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "comprehensive_pathogen_list"
    ws.append(["title row"])
    ws.append([])
    ws.append(["domain [bacterial/viral/eukaryotic]", "Priority", "Target group",
               "Priority pathogen group", "scientific name", "TaxonomyID", "rank"])
    ws.append(["bacteria", "WP2-priority", "AMR", "ESBL/Carbapenemase producing K. pneumoniae",
               "Klebsiella pneumoniae", 573, "species"])
    ws.append(["bacteria", None, "waterborne", "Vibrio cholerae", "Vibrio cholerae O1", 127906, "strain"])
    ws.append(["eukaryotic", None, "waterborne", "Cryptosporidium", "Cryptosporidium parvum", 5807, "species"])
    wb.save(path)


_REPORT = (
    " 50.00\t50\t50\tU\t0\tunclassified\n"
    " 50.00\t50\t0\tR\t1\troot\n"
    " 50.00\t50\t0\tR1\t131567\t  cellular organisms\n"
    " 50.00\t50\t0\tR2\t2\t    Bacteria\n"
    " 50.00\t50\t0\tP\t1224\t      Pseudomonadota\n"
    " 50.00\t50\t0\tC\t1236\t        Gammaproteobacteria\n"
    " 40.00\t40\t0\tO\t91347\t          Enterobacterales\n"
    " 40.00\t40\t0\tF\t543\t            Enterobacteriaceae\n"
    " 30.00\t30\t0\tG\t570\t              Klebsiella\n"
    " 30.00\t30\t20\tS\t573\t                Klebsiella pneumoniae\n"
    " 10.00\t10\t10\tS1\t1125630\t                  Klebsiella pneumoniae subsp. pneumoniae HS11286\n"
    " 10.00\t10\t0\tG\t561\t              Escherichia\n"
    " 10.00\t10\t10\tS\t562\t                Escherichia coli\n"
    " 10.00\t10\t0\tO\t135623\t          Vibrionales\n"
    " 10.00\t10\t0\tF\t641\t            Vibrionaceae\n"
    " 10.00\t10\t0\tG\t662\t              Vibrio\n"
    " 10.00\t10\t0\tS\t666\t                Vibrio cholerae\n"
    " 10.00\t10\t10\tS1\t127906\t                  Vibrio cholerae O1\n"
)


def _kraken_rows(tmp_path: Path) -> pd.DataFrame:
    """A small Kraken2 report incl. a descendant (a K. pneumoniae strain) and a bystander,
    read the way post-processing reads it."""
    report = tmp_path / "barcode01.kraken2.report.txt"
    report.write_text(_REPORT)
    return kraken2_to_dataframe(str(report))


def test_yaml_and_excel_lists_read_to_the_same_table(tmp_path: Path) -> None:
    (tmp_path / "pathogens.yaml").write_text(YAML, encoding="utf-8")
    _write_xlsx(tmp_path / "pathogens.xlsx")

    y = read_pathogens(str(tmp_path / "pathogens.yaml"))
    x = read_pathogens(str(tmp_path / "pathogens.xlsx"))

    cols = ["Type", "Priority", "Target group", "Priority pathogen group", "scientific name", "TaxonomyID"]
    pd.testing.assert_frame_equal(
        y[cols].reset_index(drop=True).astype(str), x[cols].reset_index(drop=True).astype(str)
    )
    assert y["TaxonomyID"].tolist() == [573, 127906, 5807]


def test_yaml_and_excel_lists_annotate_kraken_rows_identically(tmp_path: Path) -> None:
    (tmp_path / "pathogens.yaml").write_text(YAML, encoding="utf-8")
    _write_xlsx(tmp_path / "pathogens.xlsx")

    from_yaml = add_pathogens(_kraken_rows(tmp_path), str(tmp_path / "pathogens.yaml"))
    from_xlsx = add_pathogens(_kraken_rows(tmp_path), str(tmp_path / "pathogens.xlsx"))

    ann = ["Type", "Priority", "Target group", "Priority pathogen group"]
    pd.testing.assert_frame_equal(from_yaml[ann].astype(str), from_xlsx[ann].astype(str))
    groups = dict(zip(from_yaml["NCBI tax ID"], from_yaml["Priority pathogen group"]))
    assert groups[573] == "ESBL/Carbapenemase producing K. pneumoniae"
    assert groups[1125630] == groups[573]          # descendant inherits its ancestor's group
    assert groups[127906] == "Vibrio cholerae"
    assert groups[562] is None                     # not in the list


def test_invisible_characters_and_surrounding_whitespace_are_stripped_from_labels(tmp_path: Path) -> None:
    """A label differing from another only by a trailing U+200B or a trailing space is a
    different group to every consumer while looking identical in an editor. The reader
    never lets one through, from YAML or from the Excel layout."""
    zwsp = "​"
    (tmp_path / "pathogens.yaml").write_text(
        f'pathogens:\n  - taxid: 573\n    pathogen_group: "ESBL/Carbapenemase producing K. pneumoniae{zwsp}"\n'
        f'    scientific_name: " Klebsiella pneumoniae "\n    type: bacteria\n    target_group: AMR\n'
        f'  - taxid: 5807\n    pathogen_group: "Cryptosporidium "\n    type: eukaryotic\n    target_group: waterborne\n',
        encoding="utf-8",
    )
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "comprehensive_pathogen_list"
    ws.append(["title row"])
    ws.append([])
    ws.append(["domain [bacterial/viral/eukaryotic]", "Priority", "Target group",
               "Priority pathogen group", "scientific name", "TaxonomyID", "rank"])
    ws.append(["bacteria", None, "AMR", f"ESBL/Carbapenemase producing K. pneumoniae{zwsp}",
               " Klebsiella pneumoniae ", 573, "species"])
    ws.append(["eukaryotic", None, "waterborne", "Cryptosporidium ", "Cryptosporidium parvum", 5807, "species"])
    wb.save(tmp_path / "pathogens.xlsx")

    for name in ("pathogens.yaml", "pathogens.xlsx"):
        df = read_pathogens(str(tmp_path / name))
        assert df["Priority pathogen group"].tolist() == [
            "ESBL/Carbapenemase producing K. pneumoniae", "Cryptosporidium"
        ], name
        assert df["scientific name"].iloc[0] == "Klebsiella pneumoniae", name


@pytest.mark.parametrize(
    "text, message",
    [
        ("pathogens: []\n", "no entries"),
        ("pathogens:\n  - scientific_name: x\n    pathogen_group: g\n", "taxid"),
        ("pathogens:\n  - taxid: 573\n", "pathogen_group"),
        ("pathogens:\n  - taxid: abc\n    pathogen_group: g\n", "taxid"),
        ("- taxid: 573\n", "'pathogens'"),
    ],
)
def test_yaml_list_problems_are_reported_not_silently_dropped(tmp_path: Path, text: str, message: str) -> None:
    p = tmp_path / "pathogens.yaml"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        read_pathogens(str(p))


def test_default_pathogens_file_prefers_yaml_and_falls_back_to_xlsx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ODIN_PIPELINE_ROOT", str(tmp_path))
    sheets = tmp_path / "input_sheets"
    sheets.mkdir()

    assert get_defaults()["pathogens_file"].endswith("/input_sheets/pathogens.yaml")   # nothing there: name the preferred file

    (sheets / "pathogens.xlsx").write_bytes(b"x")
    assert get_defaults()["pathogens_file"].endswith("/input_sheets/pathogens.xlsx")   # only the old file exists

    (sheets / "pathogens.yaml").write_text("pathogens: []\n")
    assert get_defaults()["pathogens_file"].endswith("/input_sheets/pathogens.yaml")   # both: YAML wins
