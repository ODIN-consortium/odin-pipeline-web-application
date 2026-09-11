/**
 * The launch wizard's barcode-row predicates, shared between the wizard (payload building, save
 * gating) and the barcode metadata table extracted from its template. A separate module so the
 * two components can both import them without importing each other.
 */

/** Value shape of one row in the barcodes FormArray. */
export interface BarcodeRowValue {
  barcode?: string | null;
  read_count?: number | null;
  site_id?: string | null;
  sample_type?: string | null;
  sampling_date?: string | null;
  type?: string | null;
}

/** ODIN stores sampling dates as YYYYMMDD, with no separators. */
export const YYYYMMDD = /^\d{8}$/;

/**
 * Is this barcode row filled in enough to register?
 *
 * A row needs a site, a sample type and a well-formed date; anything less is a row the operator
 * has not finished, and is silently left out of the request rather than rejected. This rule was
 * written four times — twice over raw form values and twice over `FormGroup` controls — so it
 * takes the value shape, and the control call sites hand it `getRawValue()`.
 */
export function isBarcodeRowComplete(row: BarcodeRowValue): boolean {
  return !!row.site_id && !!row.sample_type && YYYYMMDD.test(row.sampling_date ?? '');
}

/**
 * Has the operator put anything in this row?
 *
 * Distinguishes a row left untouched from one begun and left incomplete. Only the second is worth
 * mentioning when a save drops it: an empty row is not discarded work.
 */
export function isBarcodeRowStarted(row: BarcodeRowValue): boolean {
  return !!row.site_id || !!row.sample_type || !!row.sampling_date || !!row.type;
}

/**
 * The barcodes of rows begun but not saveable. A sample needs a site, a type and a date because
 * the database requires all three — `samples.site_id`, `sample_type` and `sampling_date` are
 * NOT NULL and `sample_code` is derived from them — so a partial row cannot be stored at all.
 * It can, however, be reported rather than silently dropped: the table warns before the click,
 * and the wizard counts the dropped rows into its save confirmation.
 */
export function startedButIncompleteBarcodes(rows: BarcodeRowValue[]): string[] {
  return rows
    .filter((b) => isBarcodeRowStarted(b) && !isBarcodeRowComplete(b))
    .map((b) => b.barcode as string);
}
