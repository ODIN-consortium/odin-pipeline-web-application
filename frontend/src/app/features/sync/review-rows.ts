/**
 * Pure helpers behind the merge-review table: turning a sync preview into reviewable
 * rows, and describing them. Moved out of the sync page (D13) so they can be tested
 * directly, like `snapshot-diff.ts` and `import-result.ts` beside them.
 */

import {
  SyncAction,
  SyncEntry,
  SyncPreview,
  SyncReviewRow,
  SYNCABLE_TABLE_NAMES,
  TABLE_KEY_FIELDS,
} from '../../core/models/sync.model';

/** Fields that are sync metadata, not user data. */
const META_FIELDS = new Set(['id', 'created_at', 'updated_at', 'created_by', 'updated_by']);

function changedFields(entry: SyncEntry): string[] {
  if (!entry.local) return [];
  return Object.keys(entry.incoming).filter(
    (k) => !META_FIELDS.has(k) && entry.incoming[k] !== entry.local![k],
  );
}

/** The user-data fields of a new row, precomputed so the template needs no filtering. */
function newFields(entry: SyncEntry): { field: string; value: unknown }[] {
  return Object.keys(entry.incoming)
    .filter((k) => !META_FIELDS.has(k))
    .map((field) => ({ field, value: entry.incoming[field] }));
}

/** The row "label": its key fields joined, empty components dropped. */
export function rowLabel(row: Record<string, unknown>, table: string): string {
  const keys = TABLE_KEY_FIELDS[table] ?? ['id'];
  return keys
    .map((k) => row[k])
    .filter((v) => v !== null && v !== undefined && v !== '')
    .join(' / ');
}

/**
 * Flatten a preview's diff into reviewable rows per table, each carrying a default
 * action the operator can override: additions and newer versions default to accept,
 * duplicates and conflicts default to keeping this device's row.
 */
export function buildReviewRows(preview: SyncPreview): Record<string, SyncReviewRow[]> {
  const result: Record<string, SyncReviewRow[]> = {};
  for (const table of SYNCABLE_TABLE_NAMES) {
    const diff = preview.diff[table];
    if (!diff) continue;
    const rows: SyncReviewRow[] = [];

    for (const entry of diff.new) {
      rows.push({ category: 'new', entry, action: 'accept', changedFields: [], newFields: newFields(entry) });
    }
    for (const entry of diff.updated) {
      rows.push({
        category: 'updated',
        entry,
        action: 'accept',
        changedFields: changedFields(entry),
        newFields: [],
      });
    }
    for (const entry of diff.independent_duplicate) {
      rows.push({
        category: 'independent_duplicate',
        entry,
        action: 'keep_mine',
        changedFields: changedFields(entry),
        newFields: [],
      });
    }
    for (const entry of diff.conflict) {
      rows.push({
        category: 'conflict',
        entry,
        action: 'keep_mine',
        changedFields: changedFields(entry),
        newFields: [],
      });
    }

    if (rows.length > 0) {
      result[table] = rows;
    }
  }
  return result;
}

/** The actions an operator may pick for a row, by its category. */
export function actionOptions(
  category: SyncReviewRow['category'],
): { value: SyncAction; label: string }[] {
  switch (category) {
    case 'new':
      return [
        { value: 'accept', label: 'Accept (insert)' },
        { value: 'skip', label: 'Skip' },
      ];
    case 'updated':
      return [
        { value: 'accept', label: 'Accept (use newer)' },
        { value: 'skip', label: 'Skip (keep mine)' },
      ];
    case 'independent_duplicate':
      return [
        { value: 'keep_mine', label: 'Keep mine' },
        { value: 'use_theirs', label: 'Use theirs' },
      ];
    case 'conflict':
      return [
        { value: 'keep_mine', label: 'Keep mine (safe)' },
        { value: 'use_theirs', label: 'Use theirs (overwrite)' },
      ];
  }
}
