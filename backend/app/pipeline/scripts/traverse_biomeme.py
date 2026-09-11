"""
traverse_biomeme.py — ODIN biomeme processing script (backend package version).

Copied from pipeline_scripts/scripts/traverse_biomeme.py and adapted for use
inside the ODIN backend container:
  - Added ``load_metadata_from_db()`` to replace Excel-based metadata loading.
  - ``--metadata-master`` made optional; ``--db-path`` added.
  - Import of ``decode.py`` changed to absolute package path.
  - ``decode.py`` must not be modified.
"""

from __future__ import annotations

import os
import warnings
from argparse import ArgumentParser
from pathlib import Path
from typing import Optional, Union

import pandas as pd
from pandas import DataFrame

warnings.filterwarnings("ignore", message="Data Validation extension is not supported and will be removed")


class ODINProcessTraverser:
    def __init__(
        self,
        data_root: str,
        metadata_master: str = "",
        method: str = "biomeme",
        verbose: bool = False,
    ):
        self.data_root = data_root
        self.metadata_master = metadata_master
        self.metadata_dfs: Optional[dict[str, DataFrame]] = None
        self.method = method
        self.verbose = verbose

    # ── Metadata loading ──────────────────────────────────────────────────

    def load_metadata(self) -> bool:
        """Load metadata from Excel file (original path)."""
        try:
            self.metadata_dfs = self.read_metadata_excel()
            return self.metadata_dfs is not None
        except Exception as e:
            print(f"Error loading metadata: {e}")
            return False

    def load_metadata_from_db(self, db_path: str, run_names: Optional[list[str]] = None) -> bool:
        """Load metadata from the ODIN SQLite database.

        Replaces ``load_metadata()`` / ``read_metadata_excel()`` when running
        inside the ODIN backend.  Builds the same ``metadata_dfs`` dict that the
        rest of the class consumes.

        Args:
            db_path:    Absolute path to odin.db.
            run_names:  Optional list of ``biomeme_run_name`` values to filter.
                        When given, only matching rows are returned from the
                        biomeme query (reduces memory use for large datasets).
        """
        import sqlite3

        try:
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row

            # sites — alias site_code as site_ID (original script uses site_ID)
            #         alias site_code as site   (display name — no separate column yet)
            sites_df = pd.read_sql_query(
                """SELECT site_code AS site_ID,
                          site_code AS site,
                          location,
                          city,
                          country,
                          country_code,
                          longitude,
                          latitude
                   FROM sites""",
                con,
            )

            # samples — join site_code so process_metadata can look up site_ID
            samples_df = pd.read_sql_query(
                """SELECT sa.sample_code,
                          si.site_code AS site_ID,
                          sa.sample_type,
                          sa.sampling_date
                   FROM samples sa
                   LEFT JOIN sites si ON si.id = sa.site_id""",
                con,
            )

            # biomeme_runs — one row per device run in the schema
            # sampling_date comes from the linked sample row
            if run_names:
                placeholders = ",".join("?" * len(run_names))
                biomeme_df = pd.read_sql_query(
                    f"""SELECT br.biomeme_run_name,
                               sa.sample_code,
                               sa.sampling_date,
                               br.biomeme_sample_id AS Biomeme_sample_ID,
                               br.dilution_factor
                        FROM biomeme_runs br
                        LEFT JOIN samples sa ON sa.id = br.sample_id
                        WHERE br.biomeme_run_name IN ({placeholders})""",
                    con,
                    params=run_names,
                )
            else:
                biomeme_df = pd.read_sql_query(
                    """SELECT br.biomeme_run_name,
                              sa.sample_code,
                              sa.sampling_date,
                              br.biomeme_sample_id AS Biomeme_sample_ID,
                              br.dilution_factor
                       FROM biomeme_runs br
                       LEFT JOIN samples sa ON sa.id = br.sample_id""",
                    con,
                )

            con.close()

            # Convert sampling_date to datetime (same as Excel reader does)
            if "sampling_date" in biomeme_df.columns:
                biomeme_df["sampling_date"] = pd.to_datetime(
                    biomeme_df["sampling_date"], format="%Y%m%d", errors="coerce"
                )
            if "sampling_date" in samples_df.columns:
                samples_df["sampling_date"] = pd.to_datetime(
                    samples_df["sampling_date"], format="%Y%m%d", errors="coerce"
                )

            self.metadata_dfs = {
                "sites": sites_df,
                "samples": samples_df,
                "biomeme": biomeme_df,
                # lookup_tables not used by biomeme processing
            }
            return True

        except Exception as e:
            print(f"Error loading metadata from DB: {e}")
            return False

    # ── Site metadata lookup ──────────────────────────────────────────────

    def get_site_metadata(
        self, sites_df: DataFrame, site_ID: str
    ) -> tuple[
        Optional[str],
        Optional[str],
        Optional[str],
        Optional[str],
        Optional[str],
        Optional[str],
        Optional[float],
        Optional[float],
    ]:
        try:
            if "site_ID" not in sites_df.columns:
                print("site_ID column not found in sites metadata")
                return None, None, None, None, None, None, None, None

            site_row = sites_df[sites_df["site_ID"] == site_ID]
            if site_row.empty:
                print(f"Site_ID '{site_ID}' not found in sites metadata")
                return None, None, None, None, None, None, None, None

            site = str(site_row["site"].iloc[0]) if "site" in site_row.columns else None
            location = str(site_row["location"].iloc[0]) if "location" in site_row.columns else None
            city = str(site_row["city"].iloc[0]) if "city" in site_row.columns else None
            country = str(site_row["country"].iloc[0]) if "country" in site_row.columns else None
            sample_type = str(site_row["sample_type"].iloc[0]) if "sample_type" in site_row.columns else None
            longitude = float(site_row["longitude"].iloc[0]) if "longitude" in site_row.columns else None
            latitude = float(site_row["latitude"].iloc[0]) if "latitude" in site_row.columns else None

            return site, site_ID, location, city, country, sample_type, longitude, latitude
        except Exception as e:
            print(f"Error looking up site metadata: {e}")
            return None, None, None, None, None, None, None, None

    # ── Metadata processing ───────────────────────────────────────────────

    def process_metadata(self, sites_df: DataFrame, biomeme_df: DataFrame) -> pd.DataFrame:
        samples_df = self.metadata_dfs.get("samples")
        if samples_df is None or "sample_code" not in samples_df.columns or "site_ID" not in samples_df.columns:
            print("Samples tab missing or missing required columns")
            return biomeme_df

        biomeme_df = biomeme_df.copy()
        for col in ("site", "site_ID", "location", "longitude", "latitude", "city", "country", "sample_type", "country_code"):
            biomeme_df[col] = None

        for idx, biomeme_row in biomeme_df.iterrows():
            sample_code = biomeme_row.get("sample_code", None)
            if sample_code is None:
                continue
            sample_row = samples_df[samples_df["sample_code"] == sample_code]
            if sample_row.empty:
                continue
            site_id = sample_row["site_ID"].iloc[0]
            site, site_ID, location, city, country, sample_type, longitude, latitude = self.get_site_metadata(
                sites_df, site_id
            )
            biomeme_df.at[idx, "site"] = site
            biomeme_df.at[idx, "site_ID"] = site_ID
            biomeme_df.at[idx, "location"] = location
            biomeme_df.at[idx, "longitude"] = longitude
            biomeme_df.at[idx, "latitude"] = latitude
            biomeme_df.at[idx, "city"] = city
            biomeme_df.at[idx, "country"] = country
            biomeme_df.at[idx, "sample_type"] = sample_type
            site_row_df = sites_df[sites_df["site_ID"] == site_id]
            country_code = (
                site_row_df["country_code"].iloc[0]
                if not site_row_df.empty and "country_code" in site_row_df.columns
                else None
            )
            biomeme_df.at[idx, "country_code"] = country_code

        return biomeme_df

    # ── Excel metadata reader (original — kept for standalone use) ────────

    def read_metadata_excel(self) -> Optional[dict[str, DataFrame]]:
        try:
            if not isinstance(self.data_root, str):
                raise TypeError(f"data_root must be a string, got {type(self.data_root)}")
            if not isinstance(self.metadata_master, str):
                raise TypeError(f"metadata_master must be a string, got {type(self.metadata_master)}")

            file_path = os.path.join(self.data_root, self.metadata_master)
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"Excel file not found at path: {file_path}")

            excel_file = pd.ExcelFile(file_path)
            sheet_names: list[str] = excel_file.sheet_names
            dfs: dict[str, DataFrame] = {}

            for sheet in sheet_names:
                if sheet.lower() == "sites":
                    skiprows = [1]
                elif sheet.lower() in ["samples", "nanopore", "biomeme"]:
                    skiprows = [1] if sheet.lower() == "samples" else [0, 2]
                else:
                    skiprows = None

                if sheet.lower() in ["samples", "biomeme", "nanopore"]:
                    df = pd.read_excel(
                        excel_file,
                        sheet_name=sheet,
                        skiprows=skiprows,
                        dtype={"sampling_date": str},
                    )
                    if "sampling_date" in df.columns:
                        df["sampling_date"] = pd.to_datetime(df["sampling_date"], format="%Y%m%d", errors="coerce")
                    if sheet.lower() == "samples" and "site_ID" in df.columns:
                        df = df[df["site_ID"].notna()]
                else:
                    df = pd.read_excel(excel_file, sheet_name=sheet, skiprows=skiprows)
                df = df.dropna(how="all")
                dfs[sheet] = df

            return dfs

        except Exception as e:
            print(f"Error occurred while reading the excel file: {e}")
            return None

    # ── Directory traversal ───────────────────────────────────────────────

    def scan_curated_path(self, curated_dir: str) -> list[str]:
        files_list: list[str] = []
        print("Scanning curated_path directory...")
        try:
            if not isinstance(curated_dir, str):
                raise TypeError(f"curated_dir must be a string, got {type(curated_dir)}")
            if not os.path.exists(curated_dir):
                print(f"Directory '{curated_dir}' does not exist!")
                return files_list
            if not os.path.isdir(curated_dir):
                raise NotADirectoryError(f"Path is not a directory: {curated_dir}")
            for root, dirs, files in os.walk(curated_dir):
                for file in files:
                    rel_path = os.path.relpath(os.path.join(root, file), curated_dir)
                    files_list.append(rel_path)
            return files_list
        except Exception as e:
            print(f"Error occurred: {e}")
            return files_list

    def traverse_directory(
        self, path: Optional[Union[str, Path]] = None
    ) -> dict[str, dict[str, list[str]]]:
        """Walk biomeme_input_data and return {country_code: {sampling_date: [files]}}."""
        raw_data: dict[str, dict[str, list[str]]] = {}
        try:
            base_path = os.path.join(self.data_root, "biomeme_input_data")
            if path is not None:
                base_path = path if isinstance(path, str) else str(path)

            if not os.path.exists(base_path):
                raise FileNotFoundError(f"Directory does not exist: {base_path}")
            if not os.path.isdir(base_path):
                raise NotADirectoryError(f"Path is not a directory: {base_path}")

            with os.scandir(base_path) as country_entries:
                for country_entry in country_entries:
                    if country_entry.is_dir(follow_symlinks=False):
                        country_code = country_entry.name
                        country_dict: dict[str, list[str]] = {}
                        with os.scandir(country_entry.path) as date_entries:
                            for date_entry in date_entries:
                                if date_entry.is_dir(follow_symlinks=False):
                                    sampling_date = date_entry.name
                                    files = [e.name for e in os.scandir(date_entry.path) if e.is_file()]
                                    country_dict[sampling_date] = files
                        raw_data[country_code] = country_dict
        except Exception as e:
            print(f"Error occurred: {e}")
        return raw_data

    # ── Main execution ────────────────────────────────────────────────────

    def run(self) -> Optional[dict[str, dict[str, list[str]]]]:
        try:
            if not self.load_metadata():
                raise ValueError("Failed to read metadata excel file")

            country_sampling_dates = self.traverse_directory()
            if not country_sampling_dates:
                attempted_path = os.path.join(self.data_root, "biomeme_input_data")
                print(
                    f"No country_codes found or error during directory traversal.\n"
                    f"Tried: {attempted_path}\n"
                    f"Exists: {os.path.exists(attempted_path)}"
                )
                return None

            if "sites" not in self.metadata_dfs or "biomeme" not in self.metadata_dfs:
                print("Required worksheets not found in metadata")
                return None

            biomeme_df_with_meta = self.process_metadata(
                self.metadata_dfs["sites"], self.metadata_dfs["biomeme"]
            )
            self.metadata_dfs["biomeme"] = biomeme_df_with_meta
            return country_sampling_dates

        except Exception as e:
            print(f"Error in main execution: {e}")
            return None

    def run_with_db(self, db_path: str, run_names: Optional[list[str]] = None) -> Optional[dict[str, dict[str, list[str]]]]:
        """Like ``run()`` but loads metadata from SQLite instead of Excel."""
        try:
            if not self.load_metadata_from_db(db_path, run_names=run_names):
                raise ValueError("Failed to load metadata from database")

            country_sampling_dates = self.traverse_directory()
            if not country_sampling_dates:
                attempted_path = os.path.join(self.data_root, "biomeme_input_data")
                print(
                    f"No country_codes found or error during directory traversal.\n"
                    f"Tried: {attempted_path}\n"
                    f"Exists: {os.path.exists(attempted_path)}"
                )
                return None

            if "sites" not in self.metadata_dfs or "biomeme" not in self.metadata_dfs:
                print("Required metadata tables not loaded")
                return None

            biomeme_df_with_meta = self.process_metadata(
                self.metadata_dfs["sites"], self.metadata_dfs["biomeme"]
            )
            self.metadata_dfs["biomeme"] = biomeme_df_with_meta
            return country_sampling_dates

        except Exception as e:
            print(f"Error in main execution: {e}")
            return None


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    import sys

    from backend.app.pipeline.scripts.decode import extract_edna

    parser = ArgumentParser(description="ODIN Biomeme Processing Tool")
    parser.add_argument("--data-root", type=str, required=True, help="Path to the data root directory")
    parser.add_argument(
        "--metadata-master",
        type=str,
        default="",
        help="Relative path to metadata Excel file (not needed when --db-path is used)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="",
        help="Path to odin.db SQLite database (use instead of --metadata-master)",
    )
    parser.add_argument("--enlighten-data-path", type=str, required=True, help="Enlighten feather output directory")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    args, _ = parser.parse_known_args()

    DATA_ROOT = args.data_root
    ENLIGHTEN_DATA_PATH = args.enlighten_data_path
    BIOMEME_INPUT_PATH = os.path.join(DATA_ROOT, "biomeme_input_data")

    if not args.db_path and not args.metadata_master:
        print("Error: provide either --db-path or --metadata-master")
        sys.exit(1)

    try:
        traverser = ODINProcessTraverser(DATA_ROOT, args.metadata_master, method="biomeme", verbose=args.verbose)

        if args.db_path:
            country_sampling_dates = traverser.run_with_db(args.db_path)
        else:
            country_sampling_dates = traverser.run()

        if country_sampling_dates:
            expanded_data = []
            for country_code, sampling_dates_dict in country_sampling_dates.items():
                for sampling_date, files_list in sampling_dates_dict.items():
                    if not files_list:
                        expanded_data.append({"country_code": country_code, "sampling_date": sampling_date, "file_name": None})
                    else:
                        for file_name in files_list:
                            expanded_data.append({"country_code": country_code, "sampling_date": sampling_date, "file_name": file_name})

            expanded_df = pd.DataFrame(expanded_data)
            if expanded_df is None:
                sys.exit(1)

            biomeme_metadata_df = (
                traverser.metadata_dfs["biomeme"]
                if traverser.metadata_dfs and "biomeme" in traverser.metadata_dfs
                else None
            )
            all_results = []
            for idx, row in expanded_df.iterrows():
                country_code = row.get("country_code", None)
                sampling_date = row.get("sampling_date", None)
                file_name = row.get("file_name", None)
                if country_code and sampling_date and not isinstance(file_name, str):
                    print(
                        f"[traverse_biomeme] Skipping empty folder: "
                        f"{os.path.join(BIOMEME_INPUT_PATH, str(country_code), str(sampling_date))}"
                    )
                    continue
                if (
                    country_code
                    and sampling_date
                    and isinstance(file_name, str)
                    and file_name.endswith(".xlsx")
                    and file_name != "metadata.xlsx"
                ):
                    excel_file_path = os.path.join(BIOMEME_INPUT_PATH, str(country_code), str(sampling_date), file_name)
                    try:
                        result_df = extract_edna(excel_file_path, metadata_df=biomeme_metadata_df, row_metadata=row.to_dict())
                        if (
                            biomeme_metadata_df is not None
                            and "country_code" in biomeme_metadata_df.columns
                            and "biomeme_run_name" in biomeme_metadata_df.columns
                        ):
                            for meta_idx, meta_row in result_df.iterrows():
                                meta_country_code = meta_row.get("country_code", None)
                                if meta_country_code is not None and str(meta_country_code) != str(country_code):
                                    print(
                                        f"Country code mismatch for '{file_name}' in folder '{country_code}'. "
                                        f"Metadata country_code: '{meta_country_code}'"
                                    )
                                    sys.exit(1)
                        print(f"Processed {excel_file_path}")
                        all_results.append(result_df)
                    except Exception as e:
                        print(f"Error processing {excel_file_path}: {e}")

            if all_results:
                all_results_df = pd.concat(all_results, ignore_index=True)

                for col in ["sampling_date", "run_date", "Date", "Assay Date"]:
                    if col in all_results_df.columns:
                        if pd.api.types.is_datetime64_any_dtype(all_results_df[col]):
                            all_results_df[col] = all_results_df[col].dt.tz_localize(None)
                        elif all_results_df[col].dtype == "object":
                            all_results_df[col] = all_results_df[col].apply(
                                lambda x: x.tz_localize(None) if hasattr(x, "tz_localize") else x
                            )

                excel_output_dir = os.path.join(DATA_ROOT, "biomeme_processed")
                os.makedirs(excel_output_dir, exist_ok=True)
                all_results_df.to_excel(os.path.join(excel_output_dir, "BiomemeAssay.xlsx"), index=False)

                feather_output_dir = ENLIGHTEN_DATA_PATH
                os.makedirs(feather_output_dir, exist_ok=True)
                all_results_df.to_feather(os.path.join(feather_output_dir, "BiomemeAssay.feather"))

                print(f"BiomemeAssay.xlsx written to {excel_output_dir}")
                print(f"BiomemeAssay.feather written to {feather_output_dir}")

    except Exception as e:
        print(f"Error in main execution: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
