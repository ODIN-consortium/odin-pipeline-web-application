import { ExcelImportResult } from '../../core/services/export.service';

/**
 * Summarise a completed import for the confirmation message.
 *
 * Kept out of the component so it can be tested directly, and so the wording lives next to the
 * preview dialog that uses the same vocabulary. `unchanged` is reported rather than folded into
 * `updated`: a workbook that changed nothing should say so, which is what the earlier
 * restamping bug made impossible.
 */
export function describeImportResult(result: ExcelImportResult): string {
  const summary = result.summary ?? {};
  const totals = Object.values(summary).reduce(
    (acc, s) => ({
      created: acc.created + s.created,
      updated: acc.updated + s.updated,
      unchanged: acc.unchanged + s.unchanged,
      deleted: acc.deleted + s.deleted,
      skipped: acc.skipped + s.skipped,
    }),
    { created: 0, updated: 0, unchanged: 0, deleted: 0, skipped: 0 },
  );

  const parts: string[] = [];
  if (totals.created) parts.push(`${totals.created} added`);
  if (totals.updated) parts.push(`${totals.updated} changed`);
  if (totals.deleted) parts.push(`${totals.deleted} deleted`);
  if (totals.unchanged) parts.push(`${totals.unchanged} already up to date`);
  if (totals.skipped) parts.push(`${totals.skipped} could not be imported`);

  return parts.length ? `Import complete — ${parts.join(', ')}.` : 'Import complete — no changes.';
}
