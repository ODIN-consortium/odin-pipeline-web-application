/** Per-barcode data returned by GET /api/discovery/nanopore/run-info */
export interface BarcodeInfoDisk {
  barcode: string;
  read_count: number;
  fastq_file_count: number;
  /** True when read_count >= barcode_min_reads setting (or setting == 0). */
  is_used: boolean;
}

/** MinKNOW run metadata returned by GET /api/discovery/nanopore/run-info */
export interface MinknowRunInfo {
  run_accession: string;
  instrument: string | null;
  flow_cell_id: string | null;
  run_name: string | null;
  sample_name: string | null;
  /** Raw ONT protocol string from final_summary_*.txt */
  sequencing_kit_raw: string | null;
  /** Matched sequencing_kit_id lookup code, or null if no match */
  sequencing_kit_id: string | null;
  run_started: string | null;
  barcodes: BarcodeInfoDisk[];
}
