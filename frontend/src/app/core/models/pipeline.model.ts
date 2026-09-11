export type PipelineType = 'taxprofiler' | 'wf_metagenomics_amr' | 'wf_metagenomics_ssu' | 'mpox' | 'squirrel';
export type PipelineStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled';

export interface PipelineRun {
  id: string;
  pipeline_type: PipelineType;
  status: PipelineStatus;
  run_accessions: string[] | null;
  params: { pipeline_options?: PipelineOptions; [key: string]: unknown } | null;
  pid: number | null;
  log_file: string | null;
  exit_code: number | null;
  started_at: string | null;
  finished_at: string | null;
  output_path: string | null;
  work_dir: string | null;
  created_at: string;
  updated_at: string;
  created_by: string | null;
  error_hint: string | null;
  confidence_report_targets: string[];
}

export interface ExtractTarget {
  label: string;
  display_name: string;
  ref_accession: string;
  taxon_taxid?: string;
  taxon_sci_name?: string;
}

export interface PipelineOptions {
  /** taxprofiler: pass --kraken2_save_reads --kraken2_save_readclassifications */
  save_reads?: boolean;
  /** taxprofiler: extract reads for this clade after Kraken2 (mpox_cladei | mpox_cladeii | custom) */
  extract_target?: string;
  /** taxprofiler extract: human-readable label for the target taxon (e.g. "Mpox Clade IIb") */
  extract_taxon_label?: string;
  /** taxprofiler extract: NCBI accession for the reference genome */
  extract_ref_accession?: string;
  /** taxprofiler extract: NCBI taxonomy ID for Kraken2 report lookup */
  taxon_taxid?: string;
  /** taxprofiler extract: scientific name for the output report */
  taxon_sci_name?: string;
}

export interface PipelineLaunchPayload {
  pipeline_type: PipelineType;
  run_accessions: string[];
  clade?: string;
  scheme_version?: string;
  source_run_id?: string;
  created_by?: string;
  overwrite?: boolean;
  resume?: boolean;
  pipeline_options?: PipelineOptions;
}

export interface MergeCandidate {
  run_accession: string;
  barcodes: string[];
  sample_codes: string[];
}

export interface MergeDecisionStatus {
  run_accession: string;
  decision_made: boolean;
  auto_merge: boolean | null;
  related_runs: MergeCandidate[];
}

export interface MergeDecisionPayload {
  auto_merge: boolean;
  created_by?: string;
}

/** Response from `POST /pipeline/runs/{id}/postprocess`. */
export interface PostprocessStarted {
  detail: string;
  run_id: string;
}

/** Response from `GET /pipeline/options/mpox` — the values the Mpox pipeline accepts. */
export interface MpoxOptions {
  clades: string[];
  schemes: string[];
}

/** Response from `GET /pipeline/options/extract-targets`. */
export interface ExtractTargetOptions {
  targets: ExtractTarget[];
}
