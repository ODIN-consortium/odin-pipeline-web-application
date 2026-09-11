"""
Pydantic schemas for request/response validation.
No ORM — these are pure data-transfer objects.
All IDs are UUID strings. Sync columns (created_at, updated_at, created_by,
updated_by) are present on Read schemas but never sent by clients
on create/update.
"""

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .utils import is_yyyymmdd

# ─────────────────────────────────────────────────────────────────────────────
# LookupValue
# ─────────────────────────────────────────────────────────────────────────────


class LookupValueRead(BaseModel):
    id: str
    list: str
    code: str
    description: Optional[str] = None
    external_code: Optional[str] = None


class LookupValueCreate(BaseModel):
    code: str
    description: Optional[str] = None
    external_code: Optional[str] = None


class LookupValueUpdate(BaseModel):
    description: Optional[str] = None
    external_code: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# ConfigValue
# ─────────────────────────────────────────────────────────────────────────────


class ConfigValueRead(BaseModel):
    key: str
    value: Optional[str] = None
    updated_at: str
    source: Optional[str] = None  # "env" when overridden by an environment variable


class ConfigValueUpdate(BaseModel):
    value: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Database entries (taxprofiler --databases CSV)
# ─────────────────────────────────────────────────────────────────────────────


class DatabaseEntryRead(BaseModel):
    id: str
    tool: str
    db_name: str
    db_params: Optional[str] = None
    db_path: str
    created_at: str
    updated_at: str
    created_by: Optional[str] = None


class DatabaseEntryCreate(BaseModel):
    tool: str
    db_name: str
    db_params: Optional[str] = None
    db_path: str


class DatabaseEntryUpdate(BaseModel):
    tool: Optional[str] = None
    db_name: Optional[str] = None
    db_params: Optional[str] = None
    db_path: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Site
# ─────────────────────────────────────────────────────────────────────────────


class SiteCreate(BaseModel):
    site: Optional[str] = None
    country: str
    country_code: Optional[str] = None
    city_code: Optional[str] = None
    city: Optional[str] = None
    location: Optional[str] = None
    longitude: Optional[float] = None
    latitude: Optional[float] = None
    comments: Optional[str] = None


class SiteUpdate(BaseModel):
    site: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None
    city_code: Optional[str] = None
    city: Optional[str] = None
    location: Optional[str] = None
    longitude: Optional[float] = None
    latitude: Optional[float] = None
    comments: Optional[str] = None


class SiteRead(BaseModel):
    id: str
    site_code: str
    site: Optional[str] = None
    country: str
    country_code: Optional[str] = None
    city_code: Optional[str] = None
    city: Optional[str] = None
    location: Optional[str] = None
    longitude: Optional[float] = None
    latitude: Optional[float] = None
    comments: Optional[str] = None
    created_at: str
    updated_at: str
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Sample
# ─────────────────────────────────────────────────────────────────────────────


class SampleCreate(BaseModel):
    site_id: Optional[str] = None
    sample_type: Optional[str] = None
    depth: Optional[str] = None
    elevation: Optional[str] = None
    sampling_date: str
    comments_sampling: Optional[str] = None
    partner_sample_code: Optional[str] = None
    date_extraction: Optional[str] = None
    nucleic_acid_concentration: Optional[str] = None
    extract_volume: Optional[str] = None
    comments_extraction: Optional[str] = None
    elution_volume: Optional[str] = None
    comments: Optional[str] = None

    @field_validator("sampling_date")
    @classmethod
    def validate_sampling_date(cls, v: str) -> str:
        if not is_yyyymmdd(v):
            raise ValueError("sampling_date must be YYYYMMDD")
        return v

    @field_validator("date_extraction")
    @classmethod
    def validate_date_extraction(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not is_yyyymmdd(v):
            raise ValueError("date_extraction must be YYYYMMDD")
        return v


class SampleUpdate(BaseModel):
    site_id: Optional[str] = None
    sample_type: Optional[str] = None
    depth: Optional[str] = None
    elevation: Optional[str] = None
    sampling_date: Optional[str] = None
    comments_sampling: Optional[str] = None
    partner_sample_code: Optional[str] = None
    date_extraction: Optional[str] = None
    nucleic_acid_concentration: Optional[str] = None
    extract_volume: Optional[str] = None
    comments_extraction: Optional[str] = None
    elution_volume: Optional[str] = None
    comments: Optional[str] = None

    @field_validator("sampling_date")
    @classmethod
    def validate_sampling_date(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not is_yyyymmdd(v):
            raise ValueError("sampling_date must be YYYYMMDD")
        return v

    @field_validator("date_extraction")
    @classmethod
    def validate_date_extraction(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not is_yyyymmdd(v):
            raise ValueError("date_extraction must be YYYYMMDD")
        return v


class SampleRead(BaseModel):
    id: str
    sample_code: str
    site_id: Optional[str] = None
    sample_type: Optional[str] = None
    depth: Optional[str] = None
    elevation: Optional[str] = None
    sampling_date: str
    comments_sampling: Optional[str] = None
    partner_sample_code: Optional[str] = None
    date_extraction: Optional[str] = None
    nucleic_acid_concentration: Optional[str] = None
    extract_volume: Optional[str] = None
    comments_extraction: Optional[str] = None
    elution_volume: Optional[str] = None
    comments: Optional[str] = None
    created_at: str
    updated_at: str
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# NanoporeRunAccession  (run-level metadata; one row per sequencing run)
# ─────────────────────────────────────────────────────────────────────────────


class NanoporeRunAccessionCreate(BaseModel):
    run_accession: Optional[str] = None  # nullable; set after run starts
    label: Optional[str] = None          # required when run_accession is None
    protocol_id: Optional[str] = None
    sequencing_kit_id: Optional[str] = None

    runName: Optional[str] = None
    sampleName: Optional[str] = None
    comments: Optional[str] = None


class NanoporeRunAccessionUpdate(BaseModel):
    run_accession: Optional[str] = None  # set when linking post-run
    label: Optional[str] = None
    protocol_id: Optional[str] = None
    sequencing_kit_id: Optional[str] = None

    runName: Optional[str] = None
    sampleName: Optional[str] = None
    comments: Optional[str] = None


class NanoporeRunAccessionRead(BaseModel):
    id: str
    run_accession: Optional[str] = None
    label: Optional[str] = None
    protocol_id: Optional[str] = None
    sequencing_kit_id: Optional[str] = None

    runName: Optional[str] = None
    sampleName: Optional[str] = None
    comments: Optional[str] = None
    created_at: str
    updated_at: str
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# NanoporeRun  (barcode-level; many rows per nanopore_run_accessions row)
# ─────────────────────────────────────────────────────────────────────────────


class NanoporeRunCreate(BaseModel):
    # Provide ONE of: accession_id, run_accession, or label
    accession_id: Optional[str] = None   # link to existing accession by UUID
    run_accession: Optional[str] = None  # create/upsert accession with this folder name
    label: Optional[str] = None          # create pending accession with this label
    sample_id: Optional[str] = None  # UUID FK → samples.id
    barcode: str
    # Run-level fields — stored in nanopore_run_accessions; accepted here for convenience
    # so callers do not need a separate request to set run metadata.
    protocol_id: Optional[str] = None
    sequencing_kit_id: Optional[str] = None
    type: Optional[str] = None
    runName: Optional[str] = None
    sampleName: Optional[str] = None
    comments: Optional[str] = None


class NanoporeRunUpdate(BaseModel):
    accession_id: Optional[str] = None   # move barcode to a different group
    sample_id: Optional[str] = None
    barcode: Optional[str] = None
    # Run-level fields — updates nanopore_run_accessions when present
    protocol_id: Optional[str] = None
    sequencing_kit_id: Optional[str] = None
    type: Optional[str] = None
    runName: Optional[str] = None
    sampleName: Optional[str] = None
    comments: Optional[str] = None


class NanoporeRunRead(BaseModel):
    id: str
    accession_id: str
    run_accession: Optional[str] = None  # None for pending entries
    label: Optional[str] = None
    sample_id: Optional[str] = None
    sample_code: Optional[str] = None  # derived from samples join
    sampling_date: Optional[str] = None  # derived from samples join
    barcode: str
    minknow_sample_id: Optional[str] = None
    alias: Optional[str] = None
    # Run-level fields — from nanopore_run_accessions join
    protocol_id: Optional[str] = None
    sequencing_kit_id: Optional[str] = None
    type: Optional[str] = None
    runName: Optional[str] = None
    sampleName: Optional[str] = None
    comments: Optional[str] = None
    created_at: str
    updated_at: str
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# BiomemeRun
# ─────────────────────────────────────────────────────────────────────────────


class BiomemeRunCreate(BaseModel):
    biomeme_run_name: str
    sample_id: Optional[str] = None  # UUID FK → samples.id
    biomeme_sample_id: Optional[str] = None
    dilution_factor: Optional[float] = None
    comments: Optional[str] = None


class BiomemeRunUpdate(BaseModel):
    biomeme_run_name: Optional[str] = None
    sample_id: Optional[str] = None
    biomeme_sample_id: Optional[str] = None
    dilution_factor: Optional[float] = None
    comments: Optional[str] = None


class BiomemeRunRead(BaseModel):
    id: str
    biomeme_run_name: str
    sample_id: Optional[str] = None
    sample_code: Optional[str] = None  # derived from samples join
    sampling_date: Optional[str] = None  # derived from samples join
    biomeme_sample_id: Optional[str] = None
    dilution_factor: Optional[float] = None
    comments: Optional[str] = None
    created_at: str
    updated_at: str
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# PipelineRun
# ─────────────────────────────────────────────────────────────────────────────


# (Full PipelineRunRead is defined later alongside PipelineLaunchPayload)


# ─────────────────────────────────────────────────────────────────────────────
# Discovery
# ─────────────────────────────────────────────────────────────────────────────

# status values for individual barcodes
# ready            — in metadata and FASTQ files found on disk
# no_files         — in metadata, directory exists, but zero FASTQ files
# not_on_disk      — in metadata, but run directory (or barcode subdir) not found
# not_in_metadata  — found on disk but not registered in the database


class BarcodeStatus(BaseModel):
    barcode: str
    in_metadata: bool
    fastq_count: int
    sample_id: Optional[str] = None
    sample_code: Optional[str] = None
    sampling_date: Optional[str] = None
    protocol_id: Optional[str] = None
    sequencing_kit_id: Optional[str] = None
    minknow_sample_id: Optional[str] = None
    is_excluded: bool = False
    status: str  # ready | no_files | not_on_disk | not_in_metadata | excluded


class PipelineRunSummary(BaseModel):
    """Lightweight summary of one pipeline run associated with a run accession."""

    id: str
    pipeline_type: str
    status: str  # queued | running | completed | failed | cancelled
    created_at: Optional[str] = None
    extract_target: Optional[str] = None  # extract_target from pipeline_options, if set


class NanoporeRunStatus(BaseModel):
    run_accession: str
    run_path: Optional[str] = None  # relative path from minknow_dir incl. grouping folders
    run_name: Optional[str] = None   # experiment folder name from MinKNOW directory structure
    sample_name: Optional[str] = None  # sample folder name from MinKNOW directory structure
    in_metadata: bool
    on_disk: bool
    barcodes: list[BarcodeStatus]
    is_excluded: bool = False
    status: str  # ready | partial | no_files | not_on_disk | not_in_metadata | excluded
    related_run_accessions: list[str] = []
    auto_merge: Optional[bool] = None  # None = no decision / not applicable
    last_pipeline_run_status: Optional[str] = None  # done | failed | cancelled | running | queued
    last_pipeline_run_id: Optional[str] = None
    last_pipeline_run_type: Optional[str] = None
    last_pipeline_run_extract_target: Optional[str] = None  # extract_target from pipeline_options, if set
    pipeline_runs: list[PipelineRunSummary] = []  # all pipeline runs for this accession, newest first
    confidence_report_targets: list[str] = []  # extract targets with reports found on disk
    output_on_disk: bool = False  # True if any outputs_* subdir exists under nanopore_processed/{file_id}/
    artic_on_disk: bool = False  # True if all_consensus.fasta exists in outputs_wf_artic-mpxv-nf/{run_accession}/
    postprocessing_fresh: Optional[bool] = None  # True=up-to-date, False=needs re-run, None=not applicable
    metadata_warnings: list[str] = Field(default_factory=list)
    # continuation detection (pre-metadata, flow-cell based)
    continuation_run_accessions: list[str] = []
    continuation_confidence: Optional[str] = None  # 'likely' | 'possible'
    continuation_evidence: Optional["ContinuationEvidence"] = None


class NanoporeDiscoveryResult(BaseModel):
    minknow_dir: Optional[str] = None
    scanned_at: str
    runs: list[NanoporeRunStatus]


class BiomemeFileStatus(BaseModel):
    run_name: str
    registered: bool
    sample_code: Optional[str] = None
    sampling_date: Optional[str] = None  # sample collection date from DB


class BiomemeFolderStatus(BaseModel):
    folder_path: str           # "DC/20250915"
    country_code: str
    folder_date: str           # "20250915" (instrument run date from folder name)
    file_count: int
    registered_count: int
    is_excluded: bool = False
    status: str                # "ready" | "partial" | "not_in_metadata" | "excluded"
    files: list[BiomemeFileStatus]


class BiomemeDiscoveryResult(BaseModel):
    biomeme_dir: Optional[str] = None
    scanned_at: str
    folders: list[BiomemeFolderStatus]


# ─────────────────────────────────────────────────────────────────────────────
# Readiness check
# ─────────────────────────────────────────────────────────────────────────────

# action tells the frontend what the user can do with this run:
#   launch                — all metadata present, files ready; just start the pipeline
#   launch_with_warn      — partial/no_files; can still launch after user confirms
#   register_launch       — run not in metadata; show registration form, then launch
#   merge_decision_needed — continuation runs found (same flowcell); user can decide merge before launching
#   unavailable           — not on disk; cannot launch


class BarcodeReadiness(BaseModel):
    barcode: str
    fastq_count: int
    in_metadata: bool
    is_excluded: bool = False
    sample_id: Optional[str] = None  # FK → samples.id for this barcode
    sample_code: Optional[str] = None  # human-readable sample code
    sampling_date: Optional[str] = None  # from linked sample
    sample_type: Optional[str] = None  # from linked sample
    protocol_id: Optional[str] = None  # from nanopore_runs row
    sequencing_kit_id: Optional[str] = None  # from nanopore_runs row
    site_id: Optional[str] = None  # from linked sample (may differ per barcode)
    site_code: Optional[str] = None
    read_count: int = 0  # from barcode_alignment_*.tsv; 0 if file absent
    type: Optional[str] = None  # mpox type, from nanopore_runs row
    status: str  # ready | no_files | not_on_disk | not_in_metadata


class ContinuationEvidence(BaseModel):
    """Evidence linking two runs as a flow-cell continuation."""
    flow_cell_id: str
    time_gap_hours: float        # decimal hours between run_stopped and run_started
    kit: Optional[str] = None   # None → kit unknown for one run
    run_name_match: Optional[str] = None  # runName if both sides match; None otherwise


class NanoporeReadiness(BaseModel):
    run_accession: str
    on_disk: bool
    in_metadata: bool
    barcodes_on_disk: list[str]  # barcode names found on disk
    barcodes_in_metadata: list[str]  # barcode names registered in DB
    barcodes: list[BarcodeReadiness]
    # registration gap flags
    missing_site: bool
    missing_sample: bool  # any on-disk barcode has no sample linked
    missing_sampling_date: bool  # any on-disk barcode has no sampling_date
    # merge decision
    merge_decision_needed: bool = False
    merge_decision_made: bool = False
    merge_decision_stale: bool = False
    auto_merge: Optional[bool] = None
    related_run_accessions: list[str] = []
    # existing site metadata (pre-fill site form)
    existing_site_id: Optional[str] = None
    existing_site_code: Optional[str] = None
    # existing run metadata (pre-fill sequencing fields)
    existing_protocol_id: Optional[str] = None
    existing_sequencing_kit_id: Optional[str] = None
    existing_sample_type: Optional[str] = None
    # disk-derived run info (populated when run found on disk)
    sequencing_kit_raw: Optional[str] = None   # raw ONT protocol string from final_summary
    run_started: Optional[str] = None          # ISO datetime from final_summary
    metadata_warnings: list[str] = Field(default_factory=list)
    # continuation detection (pre-metadata, flow-cell based)
    continuation_run_accessions: list[str] = []
    continuation_confidence: Optional[str] = None  # 'likely' | 'possible'
    continuation_evidence: Optional[ContinuationEvidence] = None
    action: str  # launch | launch_with_warn | register_launch | merge_decision_needed | unavailable
    status: str  # ready | partial | no_files | not_on_disk | not_in_metadata


# Per-barcode registration info sent by the wizard form.
class BarcodeRegisterInfo(BaseModel):
    barcode: str
    sampling_date: Optional[str] = None  # YYYYMMDD — required if no sample_id
    sample_id: Optional[str] = None  # use existing sample directly
    # Per-barcode overrides — fall back to run-level payload fields when absent
    sample_type: Optional[str] = None
    protocol_id: Optional[str] = None
    sequencing_kit_id: Optional[str] = None
    site_id: Optional[str] = None  # override the run-level site for this barcode
    type: Optional[str] = None  # mpox type (e.g. from mpox_type lookup)


# Payload sent by the frontend when registering and launching in one step.
# All fields are optional — only send what is missing.
class NanoporeRegisterPayload(BaseModel):
    # Site (required if missing_site)
    country: Optional[str] = None
    country_code: Optional[str] = None
    city: Optional[str] = None
    city_code: Optional[str] = None
    site: Optional[str] = None
    location: Optional[str] = None  # free-text location description for new sites
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    site_id: Optional[str] = None  # use existing site if already exists
    # Sample type (shared across all barcodes; forms part of sample_code)
    sample_type: Optional[str] = None
    # Run metadata (shared across all barcodes)
    protocol_id: Optional[str] = None
    sequencing_kit_id: Optional[str] = None
    # Per-barcode info — each barcode carries its own sampling_date
    barcodes: Optional[list[BarcodeRegisterInfo]] = None
    created_by: Optional[str] = None


class ExcludePayload(BaseModel):
    reason: Optional[str] = None
    created_by: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# MinKNOW run info  (used by GET /discovery/nanopore/run-info)
# ─────────────────────────────────────────────────────────────────────────────


class BarcodeInfoRead(BaseModel):
    barcode: str
    read_count: int
    fastq_file_count: int
    is_used: bool  # True when read_count >= barcode_min_reads setting (and threshold > 0)


class MinknowRunInfoRead(BaseModel):
    run_accession: str
    instrument: Optional[str] = None
    flow_cell_id: Optional[str] = None
    run_name: Optional[str] = None
    sample_name: Optional[str] = None
    sequencing_kit_raw: Optional[str] = None
    sequencing_kit_id: Optional[str] = None  # matched lookup code, or None
    run_started: Optional[str] = None
    barcodes: list[BarcodeInfoRead] = []


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────

PIPELINE_TYPES = {"taxprofiler", "wf_metagenomics_amr", "wf_metagenomics_ssu", "mpox", "squirrel"}


class PipelineLaunchPayload(BaseModel):
    pipeline_type: str  # one of PIPELINE_TYPES
    run_accessions: list[str]  # one or more run_accessions
    # Mpox-specific (required when pipeline_type == "mpox")
    clade: Optional[str] = None
    scheme_version: Optional[str] = None
    # Squirrel-specific (required when pipeline_type == "squirrel")
    source_run_id: Optional[str] = None  # pipeline_runs.id of the completed mpox run
    created_by: Optional[str] = None
    overwrite: bool = False  # if True, launch even if output dir exists
    resume: bool = False  # if True, pass -resume to nextflow (reuse work-dir cache)
    # Optional per-pipeline flags — keys are pipeline-specific; unknown keys are ignored.
    # Supported keys per pipeline type:
    #   taxprofiler: save_reads (bool) — pass --kraken2_save_reads --kraken2_save_readclassifications
    pipeline_options: Optional[dict] = None


class PipelineRunRead(BaseModel):
    id: str
    pipeline_type: str
    status: str  # queued | running | done | failed | cancelled
    run_accessions: Optional[list[str]] = None
    params: Optional[dict] = None
    pid: Optional[int] = None
    log_file: Optional[str] = None
    exit_code: Optional[int] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    output_path: Optional[str] = None
    work_dir: Optional[str] = None
    created_at: str
    updated_at: str
    created_by: Optional[str] = None
    error_hint: Optional[str] = None  # computed from log on read; None when no known pattern detected
    confidence_report_targets: list[str] = []  # extract targets with reports found on disk


class MergeDecisionPayload(BaseModel):
    auto_merge: bool
    created_by: Optional[str] = None


class MergeCandidate(BaseModel):
    run_accession: str
    barcodes: list[str]
    sample_codes: list[str]


class MergeDecisionStatus(BaseModel):
    run_accession: str
    decision_made: bool
    auto_merge: Optional[bool] = None
    related_runs: list[MergeCandidate] = []
