import os

import numpy as np
import pandas as pd
from openpyxl import load_workbook

# pathCurated = os.path.join('..', 'testdata')
# edna_curated_files = glob.glob(os.path.join(pathCurated, 'Synthetic*.xlsx'), recursive=True)
# assay_codes = pd.read_csv(os.path.join('..', 'config', 'assay-codes.txt'), sep='\t')
location_loc = 'B107'
location_tag_loc = 'B108'
lon_loc = 'B110'
lat_loc = 'B109'
DEFAULT_BASE_PATH = r''

#: A sample well counts as a detection only when it amplified at least this many cycles
#: EARLIER (lower Cq) than the strongest amplifying no-template control of the same
#: fluorophore and target in its run. Lower Cq means more template, so a well at or after
#: the NTC is indistinguishable from the blank. Two cycles is the margin the lab applied
#: when confirming positives ("greater than 2 Cq difference" kept, "within 1 Cq of NTC"
#: rejected). Override with
#: the ODIN_NTC_MARGIN_CQ environment variable.
NTC_MARGIN_CQ = float(os.environ.get('ODIN_NTC_MARGIN_CQ', '2.0'))

#: quality_flag of a sample well whose assay the lab has declared unreliable for that run
#: (e.g. primer/probe stocks contaminated with positive-control DNA). Such a well is never
#: a detection and never a negative either: the result is unknown. The list is
#: ``assay-exclusions.csv`` beside ``assay-params.csv`` (or ``ODIN_ASSAY_EXCLUSIONS_PATH``),
#: one row per run and target: ``run_name, target_id, reason`` — ``target_id`` ``*`` or empty
#: excludes every target of the run. No file, no exclusions.
EXCLUDED_FLAG = 5
_EXCLUSION_COLUMNS = ('run_name', 'target_id', 'reason')


def _load_assay_exclusions(assay_params_path: str) -> pd.DataFrame:
    path = os.environ.get('ODIN_ASSAY_EXCLUSIONS_PATH', '') or os.path.join(
        os.path.dirname(os.path.abspath(assay_params_path)), 'assay-exclusions.csv')
    if not os.path.exists(path):
        return pd.DataFrame(columns=list(_EXCLUSION_COLUMNS))
    exclusions = pd.read_csv(path, dtype=str, comment='#').fillna('')
    missing = set(_EXCLUSION_COLUMNS) - set(exclusions.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    exclusions['run_name'] = exclusions['run_name'].str.strip()
    exclusions['target_id'] = exclusions['target_id'].str.strip().str.lower()
    return exclusions[exclusions['run_name'] != '']


def _excluded_wells(df: pd.DataFrame, exclusions: pd.DataFrame) -> pd.Series:
    """Rows of ``df`` (one run) whose run and target are listed in the exclusions."""
    mask = pd.Series(False, index=df.index)
    for row in exclusions.itertuples(index=False):
        hit = df['Run Name'] == row.run_name
        if row.target_id not in ('', '*'):
            hit &= df['Target ID'].astype(str).str.strip().str.lower() == row.target_id
        mask |= hit
    return mask


def _run_metadata(metadata_df, run_name):
    """Metadata rows for one Biomeme run (``biomeme_run_name == run_name``).

    A sample's ``Biomeme_sample_ID`` is the comma-separated list of well labels
    (``1``..``8``, ``NTC``..) that belong to it in ONE run's Excel file. The labels
    are reused in every run, so any well -> sample lookup must be built from a
    single run's rows. Built across runs, the last metadata row containing e.g. "5"
    would decide which wells "5" is grouped with everywhere.
    """
    if metadata_df is None or 'biomeme_run_name' not in metadata_df.columns:
        return metadata_df
    run_meta = metadata_df[metadata_df['biomeme_run_name'] == run_name]
    if run_meta.empty:
        print(f"WARNING: no biomeme metadata rows for run {run_name!r}; "
              f"its wells cannot be grouped into samples")
    return run_meta


def _is_control_label(label) -> bool:
    """Control wells: no-template controls (``NTC``, ``NTC1``…) and the positive control
    (``POS``/``Pos``). They are shared by every sample in a run and are never replicates."""
    upper = str(label).upper()
    return 'NTC' in upper or 'POS' in upper


#: A metadata well token may name a control by its WELL NUMBER when the run file mislabels
#: it: ``NTC@9`` = well 9 of the file is a no-template control called NTC, whatever label
#: the file gives it (a sample number, or nothing — the operator forgot to label it in the
#: Biomeme app). The raw file stays as exported; the correction sits in the metadata, where
#: it is logged. The relabelled well then behaves like any control: it sets the NTC floor,
#: is written once per run and is never a replicate.
CONTROL_WELL_SEP = '@'


def _control_override(token):
    """``'NTC@9'`` -> ``('NTC', 9)``; ``None`` for an ordinary token. Malformed is an error."""
    token = str(token).strip()
    if CONTROL_WELL_SEP not in token:
        return None
    label, _, well_no = token.partition(CONTROL_WELL_SEP)
    label, well_no = label.strip(), well_no.strip()
    if not _is_control_label(label) or not well_no.isdigit():
        raise ValueError(
            f"Biomeme well token {token!r}: expected <control label>@<well number>, e.g. NTC@9")
    return label, int(well_no)


def _token_label(token) -> str:
    """The well label a metadata token stands for (``'NTC@9'`` -> ``'NTC'``, ``'5'`` -> ``'5'``)."""
    override = _control_override(token)
    return override[0] if override else str(token).strip()


def _control_well_overrides(run_meta):
    """``({well number: control label}, {sample labels})`` from one run's metadata rows.

    A well may not be given two different control labels (metadata error)."""
    overrides, sample_labels = {}, set()
    if run_meta is None or 'Biomeme_sample_ID' not in run_meta.columns:
        return overrides, sample_labels
    for group in run_meta['Biomeme_sample_ID'].dropna().astype(str):
        for token in (s.strip() for s in group.split(',') if s.strip()):
            override = _control_override(token)
            if override is None:
                if not _is_control_label(token):
                    sample_labels.add(token)
                continue
            label, well_no = override
            if overrides.get(well_no, label) != label:
                raise ValueError(
                    f"Biomeme well {well_no} is named both {overrides[well_no]!r} and {label!r}")
            overrides[well_no] = label
    return overrides, sample_labels


def _apply_control_well_overrides(raw_edna: pd.DataFrame, run_meta) -> pd.DataFrame:
    """Relabel the wells the metadata names as controls by number (``NTC@9``)."""
    overrides, sample_labels = _control_well_overrides(run_meta)
    if not overrides or raw_edna.empty:
        return raw_edna
    run_name = raw_edna['Run Name'].iloc[0]
    for well_no, label in overrides.items():
        hit = raw_edna['Well Number'] == well_no
        if not hit.any():
            print(f"WARNING: run {run_name!r}: the metadata names well {well_no} {label!r}, "
                  f"but the file has no such well")
            continue
        clash = set(raw_edna.loc[hit, 'Biomeme replicate']) & sample_labels
        if clash:
            raise ValueError(
                f"Biomeme run {run_name!r}: well {well_no} is named {label!r} by the metadata, "
                f"but its file label {sorted(clash)} is also listed as a sample well")
        raw_edna.loc[hit, 'Biomeme replicate'] = label
    return raw_edna


_SAMPLE_LEVEL_COLUMNS = ('sample_code', 'Biomeme_sample_ID', 'dilution_factor', 'partner_sample_code')


def _run_level_metadata(run_meta) -> dict:
    """The metadata a control well of a run can carry: every column on which all the run's
    metadata rows agree (run name, sampling date, country…), and never a sample-level
    field — a run with a single sample does not make that sample's code the control's."""
    values = {}
    for col in run_meta.columns:
        distinct = run_meta[col].dropna().unique()
        values[col] = distinct[0] if col not in _SAMPLE_LEVEL_COLUMNS and len(distinct) == 1 else None
    return values


def _well_group_map(run_meta):
    """Map each well label in a run's metadata to its sample group key (the row's
    ``Biomeme_sample_ID`` string). Raises if two rows of the same run claim a label —
    that is a metadata error, not something to resolve by last-writer-wins."""
    mapping = {}
    if run_meta is None or 'Biomeme_sample_ID' not in run_meta.columns:
        return mapping
    for _, meta_row in run_meta.iterrows():
        group = str(meta_row['Biomeme_sample_ID'])
        for label in (_token_label(s) for s in group.split(',') if s.strip()):
            if _is_control_label(label):
                # Controls are shared by every sample in the run, so each sample row
                # lists them. Which group a control well lands in does not matter: it
                # never counts as a replicate and never enters a sample's mean.
                mapping.setdefault(label, group)
                continue
            if label in mapping and mapping[label] != group:
                run = meta_row.get('biomeme_run_name', '?')
                raise ValueError(
                    f"Biomeme run {run!r}: well {label!r} is listed in two metadata rows "
                    f"({mapping[label]!r} and {group!r})"
                )
            mapping[label] = group
    return mapping


def extract_edna_cq_values(edna_ws): # -> dict:
    """
    Extract and return required data from Excel sheet
    :param edna_file: Filename of Excel sheet containing eDNA PCR results
    :return: date, (date is returned as a string, as the qPCR data only has
                    a string representation and not a proper date format.
                    Pandas is probably our best bet for interpreting the date correctly.)
             run_name, (str - name given to the run)
             wells, (list - numerical list of the wells analyzed)
             sample_id, (list - numerical list of the samples analyzed)
             channels, (list - list of strings indicating which fluorophore has been used)
             cq, (list - numerical list of the cq values obtained by the analysis)
    """
    # Read edna excel
    # wb = load_workbook(edna_file)
    ws = edna_ws
    runtype = ws['A12'].value
    run_name = "UNDEFINED"
    date = np.datetime64('NaT')

    wells = []
    sample_id = []
    channels = []
    cq = []
    target_id = []

    if runtype == 'Cq':  # Then this is not a melt file
        run_name = ws['B1'].value
        date = ws['B2'].value
        # lon = ws[lon_loc].value
        # lat = ws[lat_loc].value
        # location_tag = ws[location_tag_loc].value
        # location = ws[location_loc].value

        wells_r = ws['B7:AB7']
        channels_r = ws['B8:AB8']
        sample_r = ws['B10:AB10']
        target_id_r = ws['B11:AB11']
        cq_r = ws['B12:AB12']

        for cell in wells_r[0]:
            try:
                wells.append(int(cell.value))
            except (ValueError, TypeError):
                wells.append(0)

        for cell in sample_r[0]:
            sample_id.append(str(cell.value).strip())

        for cell in channels_r[0]:
            channels.append(str(cell.value).strip())

        for cell in target_id_r[0]:
            target_id.append(str(cell.value).strip())

        for cell in cq_r[0]:
            try:
                cq.append(float(cell.value))
            except (ValueError, TypeError):
                cq.append(0.0)

    cq_dict = {'Date': date,
               'Run Name': run_name,
               'Well Number': wells,
               'Biomeme replicate': sample_id,  # Renamed from 'Assay'
               'Fluorophore': channels,
               'Target ID': target_id,
               'Cq': cq
               # 'lon': lon,
               # 'lat': lat,
               # 'location': location,
               # 'location_tag': location_tag
               }

    # print(f'{run_name}, , {date}, {location}, {location_tag}, {lat}, {lon}')

    return cq_dict


def extract_edna(edna_file: str, metadata_df: pd.DataFrame, row_metadata: dict = None) -> pd.DataFrame:
    wb = load_workbook(edna_file)
    sheets_list = []

    # Load assay-params.csv: check env var override first, then ../config relative to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    _env_path = os.environ.get('ODIN_ASSAY_PARAMS_PATH', '')
    assay_params_path = _env_path if _env_path else os.path.join(script_dir, '..', 'config', 'assay-params.csv')
    assay_params_df = pd.read_csv(assay_params_path)
    assay_params_df['_target_id_lower'] = assay_params_df['Target_ID'].astype(str).str.lower()
    exclusions = _load_assay_exclusions(assay_params_path)

    for sheet in wb:
        cq_dict = extract_edna_cq_values(sheet)
        raw_edna = pd.DataFrame.from_dict(cq_dict)
        raw_edna['Date'] = pd.to_datetime(raw_edna['Date'], format='mixed', yearfirst=True, dayfirst=False)
        raw_edna['Fluorophore'] = raw_edna['Fluorophore'].astype(str)
        raw_edna['Well Number'] = raw_edna['Well Number'].astype(int)
        # This run's metadata rows, once: the well grouping and the control-by-number
        # tokens (NTC@9) are both built from them.
        run_meta = _run_metadata(metadata_df, raw_edna['Run Name'].iloc[0]) if len(raw_edna) else None
        raw_edna = _apply_control_well_overrides(raw_edna, run_meta)

        abundance_df = raw_edna.copy()
        abundance_df = abundance_df[
            abundance_df['Target ID'].notna() &
            (abundance_df['Target ID'].astype(str).str.strip() != '') &
            (abundance_df['Target ID'].astype(str).str.lower() != 'none')
        ]
        # Create a temporary lowercased column for merging
        abundance_df['_target_id_lower'] = abundance_df['Target ID'].astype(str).str.lower()

        if not abundance_df.empty:
            abundance_df['Assay Date'] = abundance_df['Date']
            abundance_df = abundance_df.groupby([
                'Biomeme replicate',
                'Well Number',
                'Fluorophore',
                'Run Name',
                'Assay Date',
                'Target ID',
                '_target_id_lower'
                ],
                as_index=False, group_keys=True).mean()

            abundance_df = abundance_df.merge(
                assay_params_df,
                left_on='_target_id_lower',
                right_on='_target_id_lower',
                how='left',
                suffixes=('', '_assay')
            )

            abundance_df['Target ID'] = abundance_df['Target_ID']

            valid = (
                (abundance_df['Cq'] > 0) &
                abundance_df['B'].notnull() &
                abundance_df['M'].notnull()
            )
            abundance_df['Abundance'] = 10 ** ((abundance_df.loc[valid, 'Cq'] - abundance_df.loc[valid, 'B']) / abundance_df.loc[valid, 'M'])

            # 'Abundance diluted' is filled after the metadata merge below, which is where
            # the sample's dilution_factor first becomes available. Looking for the factor
            # here would never find it, and the correction would silently never apply.
            abundance_df['Abundance diluted'] = abundance_df['Abundance']

            abundance_df = abundance_df.drop(columns=['_target_id_lower'])

            # Group wells into samples using the metadata rows for THIS run only
            # (see _run_metadata for why).
            well_group = _well_group_map(run_meta)
            if well_group:
                abundance_df['Biomeme_sample_ID_group'] = abundance_df['Biomeme replicate'].map(well_group)
            else:
                abundance_df['Biomeme_sample_ID_group'] = abundance_df['Biomeme replicate']
            # Sample wells only: NTC and POS control wells belong to the group (the
            # metadata lists them on every sample) but they are controls, not replicates.
            # 'is_control' goes to the output: it is the row-type marker of the dataset.
            abundance_df['is_control'] = abundance_df['Biomeme replicate'].map(_is_control_label).astype(bool)
            not_ntc_mask = ~abundance_df['is_control']

            # Mean Abundance per sample group, Target ID, Run Name and Fluorophore over the
            # sample's own amplifying wells. An amplifying NTC, or the always-amplifying
            # positive control, must not be averaged into the sample's abundance.
            mean_abundance = abundance_df[
                not_ntc_mask &
                (abundance_df['Cq'] > 0)
            ].groupby(
                ['Biomeme_sample_ID_group', 'Target ID', 'Run Name', 'Fluorophore']
            )['Abundance'].mean().reset_index().rename(columns={'Abundance': 'Mean Abundance'})
            abundance_df = abundance_df.merge(
                mean_abundance,
                left_on=['Biomeme_sample_ID_group', 'Target ID', 'Run Name', 'Fluorophore'],
                right_on=['Biomeme_sample_ID_group', 'Target ID', 'Run Name', 'Fluorophore'],
                how='left'
            )
            abundance_df = abundance_df.drop(columns=['Biomeme_sample_ID_group'])

            # log1pMeanAbundance = ln(Mean Abundance + 1) (numpy.log1p), only if Mean Abundance > 0.
            # Named for the transform rather than a bare "log", which reads as log10: a value
            # of 9.06 is about 8,600 copies, not 1.1 billion.
            abundance_df['log1pMeanAbundance'] = abundance_df['Mean Abundance'].apply(
                lambda x: np.log1p(x) if pd.notna(x) and x > 0 else np.nan
            )

            # Add quality_flag column: 0 = not checked, 1 = good, 2 = questionable, 3 = bad, 4 = below NTC threshold
            abundance_df['quality_flag'] = 0  # default

            # Positive-well count per sample group — the same per-run grouping as above.
            # Without metadata every well maps to NaN and the flag stays 0 ("not checked").
            abundance_df['sample_group'] = abundance_df['Biomeme replicate'].map(well_group)
            # For each group+Target ID, count non-zero Cq values (excluding NTC)
            group_counts = abundance_df[not_ntc_mask & (abundance_df['Cq'] > 0)].groupby([
                'sample_group', 'Target ID'
            ])['Cq'].count().reset_index().rename(columns={'Cq': 'group_nonzero_cq_count'})
            abundance_df = abundance_df.merge(group_counts, on=['sample_group', 'Target ID'], how='left')
            # Assign quality flag
            abundance_df['quality_flag'] = 0  # default
            abundance_df.loc[abundance_df['group_nonzero_cq_count'] == 1, 'quality_flag'] = 2
            abundance_df.loc[abundance_df['group_nonzero_cq_count'] > 1, 'quality_flag'] = 1
            abundance_df.loc[abundance_df['Cq'] == 0, 'quality_flag'] = 3

            # --- NTC rule ---
            # An amplifying no-template control means the assay produces signal from
            # nothing (contamination, primer-dimer). A sample well is trusted only when it
            # amplified at least NTC_MARGIN_CQ cycles EARLIER (lower Cq) than the strongest
            # NTC of the same fluorophore and target in this run; a well at or after that
            # point is flagged 4 and never counts as presence. Note the direction: Cq is
            # inverse to abundance, so the untrustworthy wells are those at or ABOVE the
            # NTC's Cq. Comparing the other way round rejects the strongest positives and
            # keeps the wells weaker than the blank.
            ntc_mask = abundance_df['Biomeme replicate'].str.contains('NTC', case=False, na=False)
            ntc_rows = abundance_df[ntc_mask & (abundance_df['Cq'] > 0)]
            abundance_df['quality_flag'] = abundance_df['quality_flag'].astype(int)
            ntc_floor = ntc_rows.groupby(['Run Name', 'Fluorophore', 'Target ID'])['Cq'].min()
            for (run, fluor, target), ntc_cq in ntc_floor.items():
                match_mask = (
                    (abundance_df['Run Name'] == run) &
                    (abundance_df['Fluorophore'] == fluor) &
                    (abundance_df['Target ID'] == target) &
                    ~ntc_mask &
                    (abundance_df['Cq'] > 0) &
                    (abundance_df['Cq'] >= ntc_cq - NTC_MARGIN_CQ)
                )
                abundance_df.loc[match_mask, 'quality_flag'] = 4
            # Control wells are not scored: flags 1, 2 and 4 describe a sample's wells and
            # the NTC rule is made of the controls, not applied to them. A control keeps
            # 3 when it did not amplify and 0 ("not checked") otherwise.
            control_mask = abundance_df['is_control']
            abundance_df.loc[control_mask, 'quality_flag'] = np.where(
                abundance_df.loc[control_mask, 'Cq'] == 0, 3, 0)
            # --- Excluded assays ---
            # A run's assay the lab has declared unreliable (assay-exclusions.csv): its
            # sample wells are flagged 5 whatever they show. Not a detection, not a
            # negative — the controls of the run are still reported as they are.
            excluded = _excluded_wells(abundance_df, exclusions) & ~control_mask
            abundance_df.loc[excluded, 'quality_flag'] = EXCLUDED_FLAG
            # Remove the drop statement here. Do NOT drop columns yet.

            # Add string column 'presence' with new logic:
            # Control wells (NTC, POS) carry no presence — the column selects detections.
            abundance_df['presence'] = abundance_df.apply(
                lambda row: (
                    None if row['is_control']
                    else (
                        'true' if row['quality_flag'] == 1 and pd.notna(row['Mean Abundance']) and row['Mean Abundance'] > 0
                        else 'true_x' if row['quality_flag'] == 2 and pd.notna(row['Mean Abundance']) and row['Mean Abundance'] > 0
                        else 'false'
                    )
                ),
                axis=1
            )
            # Remove only new grouping columns (drop once, after all logic)
            abundance_df = abundance_df.drop(columns=['group_nonzero_cq_count', 'sample_group'])

            # --- Merge the metadata: one row per sample well, one row per control well ---
            # A sample's rows are its numbered wells with the sample's metadata attached.
            # A control well (NTC…, POS) is a property of the run, not of a sample: it is
            # written ONCE per run, with sample_code empty and only the run-level metadata
            # (see _run_level_metadata), whether or not any sample's Biomeme_sample_ID lists
            # it. Copying a control once per sample that lists it multiplies 120 physical
            # wells into several hundred rows, and dropping the ones no sample lists removes
            # exactly the wells the NTC rule acted on — a file's NTC3, or NTC where the
            # metadata says NTC1,NTC2 — from the dataset that shows their consequences.
            if metadata_df is not None and 'biomeme_run_name' in metadata_df.columns and 'Biomeme_sample_ID' in metadata_df.columns:
                run_name = abundance_df['Run Name'].iloc[0]
                run_meta = metadata_df[metadata_df['biomeme_run_name'] == run_name]
                listed = set()
                for _, meta_row in run_meta.iterrows():
                    if pd.isna(meta_row['Biomeme_sample_ID']):
                        continue
                    sample_ids = [_token_label(s) for s in str(meta_row['Biomeme_sample_ID']).split(',') if s.strip()]
                    listed.update(sample_ids)
                    filtered = abundance_df[
                        ~abundance_df['is_control'] &
                        abundance_df['Biomeme replicate'].isin(sample_ids)
                    ].copy()
                    for col in metadata_df.columns:
                        filtered[col] = meta_row[col]
                    sheets_list.append(filtered)
                if not run_meta.empty:
                    controls = abundance_df[abundance_df['is_control']].copy()
                    for col, value in _run_level_metadata(run_meta).items():
                        controls[col] = value
                    sheets_list.append(controls)
                    unlisted_controls = sorted(set(controls['Biomeme replicate']) - listed)
                    if unlisted_controls:
                        print(f"WARNING: run {run_name!r}: control wells {unlisted_controls} are not in any "
                              f"sample's Biomeme_sample_ID; kept as control rows")
                    unlisted_wells = sorted(set(abundance_df.loc[~abundance_df['is_control'], 'Biomeme replicate']) - listed)
                    if unlisted_wells:
                        print(f"WARNING: run {run_name!r}: wells {unlisted_wells} are not in any sample's "
                              f"Biomeme_sample_ID and are dropped")
            else:
                sheets_list.append(abundance_df)
        else:
            sheets_list.append(abundance_df)
    if not sheets_list:
        # Nothing matched the metadata (a run the metadata does not know, or an empty
        # file): an empty frame, not a crash in concat.
        return pd.DataFrame()
    abundance_list = pd.concat(sheets_list, ignore_index=True)
    # Dilution: the sample's dilution_factor comes from the metadata (biomeme_runs), so it
    # can only be applied now that the metadata is merged. The factor is the fraction of the
    # original strength that went into the reaction (0.5 = diluted 1:2, 0.01 = 1/100), and
    # 'Abundance diluted' is the abundance corrected back to the undiluted sample:
    # Abundance / dilution_factor. Rows without a factor (or a factor of 0) keep Abundance.
    if 'dilution_factor' in abundance_list.columns:
        factor = pd.to_numeric(abundance_list['dilution_factor'], errors='coerce')
        usable = factor.notna() & (factor > 0)
        abundance_list['Abundance diluted'] = np.where(
            usable, abundance_list['Abundance'] / factor.where(usable, 1.0), abundance_list['Abundance'])
    # Control wells carry their Cq and nothing abundance-derived: the group mean was merged
    # onto them too (the metadata lists them in the sample's group), so log1pMeanAbundance
    # must be blanked with the rest or another sample's value shows through.
    if 'is_control' not in abundance_list.columns:
        abundance_list['is_control'] = False
    abundance_list['is_control'] = abundance_list['is_control'].fillna(False).astype(bool)
    abundance_list.loc[abundance_list['is_control'],
                       ['Abundance', 'Abundance diluted', 'Mean Abundance', 'log1pMeanAbundance']] = np.nan

    # # --- CORRECTED LOGIC: Merge site metadata from metadata_df ---
    # # Assume metadata_df contains the 'sites' information
    # if metadata_df is not None:
    #     # Look for site metadata columns in metadata_df
    #     site_cols = ['site', 'site_ID', 'location']
    #     # Find a suitable merge key
    #     merge_key = None
    #     for key in ['Biomeme replicate', 'Sample_ID', 'Biomeme_sample_ID', 'sample_id']:
    #         if key in abundance_list.columns and key in metadata_df.columns:
    #             merge_key = key
    #             break
    #     if not merge_key:
    #         # Fallback: try to merge on 'Biomeme replicate' <-> 'sample_id' if possible
    #         if 'Biomeme replicate' in abundance_list.columns and 'sample_id' in metadata_df.columns:
    #             merge_key = 'Biomeme replicate'
    #             metadata_df = metadata_df.rename(columns={'sample_id': 'Biomeme replicate'})
    #     if merge_key and all(col in metadata_df.columns for col in site_cols):
    #         abundance_list = abundance_list.merge(
    #             metadata_df[site_cols + [merge_key]],
    #             on=merge_key,
    #             how='left'
    #         )
    #     else:
    #         # If no merge key or site columns found, just add empty columns
    #         for col in site_cols:
    #             abundance_list[col] = np.nan
    # else:
    #     # If no metadata_df, add empty columns
    #     for col in ['site', 'site_ID', 'location']:
    #         abundance_list[col] = np.nan

    return abundance_list.copy()
