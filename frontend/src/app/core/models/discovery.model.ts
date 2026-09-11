export type DiscoveryStatus =
  | 'ready'
  | 'partial'
  | 'no_files'
  | 'not_on_disk'
  | 'not_in_metadata'
  | 'excluded';
export type ReadinessAction =
  | 'launch'
  | 'launch_with_warn'
  | 'register_launch'
  | 'merge_decision_needed'
  | 'unavailable';

export interface BarcodeStatus {
  barcode: string;
  in_metadata: boolean;
  fastq_count: number;
  sample_id: string | null;
  sample_code: string | null;
  sampling_date: string | null;
  protocol_id: string | null;
  sequencing_kit_id: string | null;
  minknow_sample_id: string | null;
  is_excluded: boolean;
  status: DiscoveryStatus;
}

export interface PipelineRunSummary {
  id: string;
  pipeline_type: string;
  status: string;              // queued | running | completed | failed | cancelled
  created_at: string | null;
  extract_target: string | null;
}

export interface NanoporeRunStatus {
  run_accession: string;
  run_path: string | null;
  run_name: string | null;
  sample_name: string | null;
  in_metadata: boolean;
  on_disk: boolean;
  barcodes: BarcodeStatus[];
  is_excluded: boolean;
  status: DiscoveryStatus;
  related_run_accessions: string[];
  auto_merge: boolean | null;
  last_pipeline_run_status: string | null;
  last_pipeline_run_id: string | null;
  last_pipeline_run_type: string | null;
  last_pipeline_run_extract_target: string | null;
  pipeline_runs: PipelineRunSummary[];
  confidence_report_targets: string[];
  output_on_disk: boolean;
  artic_on_disk: boolean;
  postprocessing_fresh: boolean | null;
  metadata_warnings: string[];
  continuation_run_accessions: string[];
  continuation_confidence: 'likely' | 'possible' | null;
  continuation_evidence: ContinuationEvidence | null;
}

export interface NanoporeDiscoveryResult {
  minknow_dir: string | null;
  scanned_at: string;
  runs: NanoporeRunStatus[];
}

export interface BiomemeFileStatus {
  run_name: string;
  registered: boolean;
  sample_code: string | null;
  sampling_date: string | null;   // sample collection date from DB
}

export interface BiomemeFolderStatus {
  folder_path: string;            // "DC/20250915"
  country_code: string;
  folder_date: string;            // "20250915" (instrument run date from folder name)
  file_count: number;
  registered_count: number;
  is_excluded: boolean;
  status: string;                 // "ready" | "partial" | "not_in_metadata" | "excluded"
  files: BiomemeFileStatus[];
}

export interface BiomemeDiscoveryResult {
  biomeme_dir: string | null;
  scanned_at: string;
  folders: BiomemeFolderStatus[];
}

// ── Readiness ────────────────────────────────────────────────────────────────

export interface ContinuationEvidence {
  flow_cell_id: string;
  time_gap_hours: number;
  kit: string | null;
  run_name_match: string | null;
}

export interface BarcodeReadiness {
  barcode: string;
  fastq_count: number;
  in_metadata: boolean;
  is_excluded: boolean;
  sample_id: string | null;
  sample_code: string | null;
  sampling_date: string | null;
  sample_type: string | null;
  protocol_id: string | null;
  sequencing_kit_id: string | null;
  site_id: string | null;
  site_code: string | null;
  read_count: number;  // from barcode_alignment_*.tsv; 0 if absent
  type: string | null;
  status: DiscoveryStatus;
}

export interface NanoporeReadiness {
  run_accession: string;
  on_disk: boolean;
  in_metadata: boolean;
  barcodes_on_disk: string[];
  barcodes_in_metadata: string[];
  barcodes: BarcodeReadiness[];
  missing_site: boolean;
  missing_sample: boolean;
  missing_sampling_date: boolean;
  // merge decision
  merge_decision_needed: boolean;
  merge_decision_made: boolean;
  merge_decision_stale: boolean;
  auto_merge: boolean | null;
  related_run_accessions: string[];
  // site metadata (shared across run; used to pre-fill the site form)
  existing_site_id: string | null;
  existing_site_code: string | null;
  // sequencing metadata (used to pre-fill protocol/kit fields)
  existing_protocol_id: string | null;
  existing_sequencing_kit_id: string | null;
  existing_sample_type: string | null;
  // disk-derived run info
  sequencing_kit_raw: string | null;  // raw ONT protocol string from final_summary
  run_started: string | null;         // ISO datetime from final_summary
  metadata_warnings: string[];
  // continuation detection
  continuation_run_accessions: string[];
  continuation_confidence: 'likely' | 'possible' | null;
  continuation_evidence: ContinuationEvidence | null;
  action: ReadinessAction;
  status: DiscoveryStatus;
}

/** Per-barcode sampling-date supplied by the wizard registration form. */
export interface BarcodeRegisterInfo {
  barcode: string;
  sampling_date?: string; // YYYYMMDD
  sample_id?: string; // skip sample creation when already known
  // Per-barcode overrides — fall back to run-level fields when absent
  sample_type?: string;
  protocol_id?: string;
  sequencing_kit_id?: string;
  site_id?: string; // override run-level site for this barcode
  type?: string; // mpox type
}

export interface NanoporeRegisterPayload {
  country?: string;
  country_code?: string;
  city?: string;
  city_code?: string;
  site?: string;
  location?: string;
  latitude?: number | null;
  longitude?: number | null;
  site_id?: string;
  sample_type?: string;
  /** Run-level sequencing metadata (shared across all barcodes). */
  protocol_id?: string;
  sequencing_kit_id?: string;
  /** Each barcode carries its own sampling_date. */
  barcodes?: BarcodeRegisterInfo[];
  created_by?: string;
}

export interface ExcludePayload {
  reason?: string;
  created_by?: string;
}
