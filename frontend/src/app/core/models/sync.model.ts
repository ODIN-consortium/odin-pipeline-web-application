// ─────────────────────────────────────────────────────────────────────────────
// Models for the sync API (Phase 2 / 3 / 4)
// ─────────────────────────────────────────────────────────────────────────────

/** A row entry in a diff category. */
export interface SyncEntry {
  /** The incoming row from the import file. */
  incoming: Record<string, unknown>;
  /** The existing local row (present for updated / dup / conflict). */
  local?: Record<string, unknown>;
  /** For conflict: the third row that owns the clashing UNIQUE key. */
  conflict_row?: Record<string, unknown>;
}

/** The five merge categories for one table. */
export interface SyncTableDiff {
  new: SyncEntry[];
  identical: SyncEntry[];
  updated: SyncEntry[];
  independent_duplicate: SyncEntry[];
  conflict: SyncEntry[];
}

/** Per-table count summary from the preview response. */
export interface SyncTableSummary {
  new: number;
  identical: number;
  updated: number;
  independent_duplicate: number;
  conflict: number;
}

/** Full response from POST /api/sync/preview */
export interface SyncPreview {
  exported_by: string;
  exported_at: string;
  summary: Record<string, SyncTableSummary>;
  diff: Record<string, SyncTableDiff>;
}

/** Request body for POST /api/sync/apply */
export interface SyncApplyRequest {
  /** The original parsed import JSON, sent back verbatim. */
  import_data: object;
  /** table_name → { incoming_row_id → action } */
  decisions: Record<string, Record<string, string>>;
}

/** Per-table counts from the apply response. */
export interface SyncTableApplied {
  inserted: number;
  updated: number;
  skipped: number;
}

/** Full response from POST /api/sync/apply */
export interface SyncApplyResult {
  applied: Record<string, SyncTableApplied>;
  total_inserted: number;
  total_updated: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// UI state helpers
// ─────────────────────────────────────────────────────────────────────────────

/** Valid actions for a row in the merge GUI. */
export type SyncAction = 'accept' | 'skip' | 'keep_mine' | 'use_theirs';

/** A row being reviewed in the merge GUI, with the user's chosen action. */
export interface SyncReviewRow {
  category: 'new' | 'updated' | 'independent_duplicate' | 'conflict';
  entry: SyncEntry;
  action: SyncAction;
  /** Fields that differ between incoming and local (for updated / dup). */
  changedFields: string[];
  /** For a new row: its user-data fields, metadata already filtered out. */
  newFields: { field: string; value: unknown }[];
}

/** Human-readable display label for each syncable table. */
export const TABLE_LABELS: Record<string, string> = {
  lookup_values: 'Lookup values',
  databases: 'Databases',
  sites: 'Sites',
  samples: 'Samples',
  nanopore_run_accessions: 'Nanopore run accessions',
  nanopore_runs: 'Nanopore runs',
  biomeme_runs: 'Biomeme runs',
};

/** Ordered list of syncable tables (matches server definition). */
export const SYNCABLE_TABLE_NAMES = Object.keys(TABLE_LABELS);

/** Key fields to show as the row "label" in the diff table. */
export const TABLE_KEY_FIELDS: Record<string, string[]> = {
  lookup_values: ['list', 'code', 'description'],
  databases: ['tool', 'db_name'],
  sites: ['site_code', 'country', 'city'],
  samples: ['sample_code', 'sampling_date'],
  nanopore_run_accessions: ['run_accession', 'label'],
  nanopore_runs: ['barcode'],
  biomeme_runs: ['biomeme_run_name'],
};

/** Result of `POST /api/sync/restore`. */
export interface SyncRestoreResult {
  detail: string;
  exported_at: string;
  exported_by: string | null;
  tables: Record<string, { restored: number; deleted: number }>;
  total_restored: number;
  total_deleted: number;
}
