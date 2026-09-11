import { Clearable } from './update.model';
export interface NanoporeRunAccession {
  id: string;
  run_accession: string | null;
  label: string | null;
  protocol_id?: string;
  sequencing_kit_id?: string;
  runName?: string;
  sampleName?: string;
  comments?: string;
  created_at?: string;
  updated_at?: string;
  created_by?: string;
  updated_by?: string;
}

export type NanoporeRunAccessionCreate = Omit<
  NanoporeRunAccession,
  'id' | 'created_at' | 'updated_at'
>;
export type NanoporeRunAccessionUpdate = Clearable<NanoporeRunAccessionCreate>;

export interface NanoporeRun {
  id: string;
  accession_id: string;          // UUID FK → nanopore_run_accessions.id
  run_accession: string | null;  // null for pending entries
  label: string | null;          // human-readable tag for pending entries
  sample_id?: string; // FK → samples.id (UUID)
  sample_code?: string; // derived from sample relationship
  sampling_date: string | null; // derived from samples join; null when absent (backend: Optional[str])
  barcode: string;
  minknow_sample_id?: string; // {sample_code}_{protocol_id}_{sequencing_kit_id}
  alias?: string;
  protocol_id?: string;
  sequencing_kit_id?: string;
  type?: string;
  runName?: string;
  sampleName?: string;
  comments?: string;
  created_at?: string;
  updated_at?: string;
  created_by?: string;
  updated_by?: string;
}

/**
 * Mirrors the backend's `NanoporeRunCreate` exactly. Provide exactly ONE of
 * `accession_id` (add the barcode to an existing group), `run_accession`
 * (create/upsert the group by folder name) or `label` (create a pending group).
 * Derived fields (`sample_code`, `alias`, `minknow_sample_id`, `sampling_date`)
 * are computed server-side and never accepted.
 */
export interface NanoporeRunCreate {
  accession_id?: string;
  run_accession?: string;
  label?: string;
  sample_id?: string;
  barcode: string;
  protocol_id?: string;
  sequencing_kit_id?: string;
  type?: string;
  runName?: string;
  sampleName?: string;
  comments?: string;
}

/**
 * Mirrors the backend's `NanoporeRunUpdate`: the create fields minus the two
 * immutable group identifiers — `accession_id` stays, as the group-reassignment
 * field. Omitted key = leave alone; explicit null = clear.
 */
export type NanoporeRunUpdate = Clearable<Omit<NanoporeRunCreate, 'run_accession' | 'label'>>;
