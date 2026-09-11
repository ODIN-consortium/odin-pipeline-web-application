"""
Pure Kraken2 report parsing functions.

Ported from pipeline_scripts/scripts/kraken_parser.py — no CLI scaffolding,
no logging setup, no argparse side-effects.  These are pure data-transformation
functions and must stay that way so they can be imported without side-effects.
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

    The input frame is not modified: a copy is returned, as the docstring's
    "returns an enriched frame" contract implies.

    https://bisonnet.bucknell.edu/files/2021/05/Kraken2-Help-Sheet.pdf
    """
    df = df.copy()
    ordered_ranks = list(rank_map.values())

    for col in ordered_ranks:
        df[col] = ""

    current_lineage = {rank: "" for rank in ordered_ranks}

    for idx, row in df.iterrows():
        rank_code = row["Rank code"]
        sci_name = row["Scientific name"].strip()

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

    Strips leading/trailing whitespace from all string columns.
    """
    df0 = pd.read_csv(path, sep="\t", names=kraken2_column_names)
    df_obj = df0.select_dtypes("object")
    df0[df_obj.columns] = df_obj.apply(lambda x: x.str.strip())
    return df0
