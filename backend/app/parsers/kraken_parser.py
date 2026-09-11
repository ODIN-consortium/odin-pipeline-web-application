"""
Pure Kraken2 report parsing functions.

No CLI scaffolding, no logging setup, no argparse side-effects.  These are pure
data-transformation functions and must stay that way so they can be imported
without side-effects.
"""

import pandas as pd

# Hierarchy of ranks and their corresponding columns.
RANK_COLUMN_MAPPING = {
    "R": "Root",
    "D": "Domain",
    "K": "Kingdom",
    "P": "Phylum",
    "C": "Class",
    "O": "Order",
    "F": "Family",
    "G": "Genus",
    "S": "Species",
}

#: Depth of the row in the report tree, from the indentation of the scientific name
#: (two spaces per level). Added by read_kraken2_report; the indentation is the
#: report's only encoding of the taxonomy tree, and it is lost once names are stripped.
DEPTH_COLUMN = "Depth"

#: The TaxIDs from the root down to and including the row's own, ``;``-separated.
#: Added by enrich_kraken2_lineage from the depth; this is what makes "is this row a
#: descendant of TaxID x" an exact question (see kraken_postprocessor.add_pathogens).
LINEAGE_TAXIDS_COLUMN = "Lineage taxids"


def enrich_kraken2_lineage(df: pd.DataFrame, rank_map: dict[str, str]) -> pd.DataFrame:
    """
    Enrich a Kraken2 report DataFrame with lineage columns.

    Each row gets columns for every standard rank (Root → Species).  The values
    are filled from a running lineage tracker that is updated as rows are processed
    in document order (which Kraken2 guarantees is depth-first tree order).

    Numbered rank codes (e.g. G1, S2) represent intermediate nodes between two
    standard ranks.  They do NOT overwrite the parent rank in the lineage tracker
    but DO clear all descendant ranks so stale children don't bleed into sibling
    branches.

    When the frame carries the ``Depth`` column from read_kraken2_report, each row
    also gets ``Lineage taxids``: the TaxIDs of its ancestors and itself, kept on a
    stack that pops back to the row's depth. Unlike the name columns, this tells a
    sub-species row apart from its parent species.

    The input frame is not modified: a copy is returned, as the docstring's
    "returns an enriched frame" contract implies.

    https://bisonnet.bucknell.edu/files/2021/05/Kraken2-Help-Sheet.pdf
    """
    df = df.copy()
    ordered_ranks = list(rank_map.values())

    for col in ordered_ranks:
        df[col] = ""

    current_lineage = {rank: "" for rank in ordered_ranks}
    track_taxids = DEPTH_COLUMN in df.columns and "NCBI tax ID" in df.columns
    if track_taxids:
        df[LINEAGE_TAXIDS_COLUMN] = ""
    taxid_stack: list[tuple[int, str]] = []

    for idx, row in df.iterrows():
        rank_code = row["Rank code"]
        sci_name = row["Scientific name"].strip()

        if track_taxids:
            depth = int(row[DEPTH_COLUMN])
            while taxid_stack and taxid_stack[-1][0] >= depth:
                taxid_stack.pop()
            taxid_stack.append((depth, str(row["NCBI tax ID"])))
            df.at[idx, LINEAGE_TAXIDS_COLUMN] = ";".join(t for _, t in taxid_stack)

        base_rank = rank_code if rank_code in rank_map else rank_code[0]
        if base_rank not in rank_map:
            continue

        rank_name = rank_map[base_rank]
        is_numbered_rank = rank_code != base_rank
        if not is_numbered_rank:
            current_lineage[rank_name] = sci_name

        rank_index = ordered_ranks.index(rank_name)
        for lower_rank in ordered_ranks[rank_index + 1 :]:
            current_lineage[lower_rank] = ""

        for rank in ordered_ranks:
            df.at[idx, rank] = current_lineage[rank]

    return df


def read_kraken2_report(path: str, kraken2_column_names: list[str]) -> pd.DataFrame:
    """
    Read a Kraken2 tab-separated report file into a DataFrame.

    Records each row's depth in the report tree in the ``Depth`` column (Kraken2
    indents the scientific name two spaces per level), then strips leading/trailing
    whitespace from all string columns. The last column is the scientific name.
    """
    df0 = pd.read_csv(path, sep="\t", names=kraken2_column_names)
    if not df0.empty:
        names = df0[kraken2_column_names[-1]].astype(str)
        df0[DEPTH_COLUMN] = ((names.str.len() - names.str.lstrip(" ").str.len()) // 2).astype(int)
    df_obj = df0.select_dtypes("object")
    df0[df_obj.columns] = df_obj.apply(lambda x: x.str.strip())
    return df0
