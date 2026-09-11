import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTableModule } from '@angular/material/table';

import { ExcelImportResult, ExcelSheetSummary, ImportChange } from '../../core/services/export.service';
import { ImportProblem } from '../../core/utils/import-problems';

/** One row of the per-sheet summary table. */
export interface SheetOutcome extends ExcelSheetSummary {
  sheet: string;
}

/**
 * What a workbook would do, shown before anything is written.
 *
 * The endpoint previews by running the real import and rolling it back, so these numbers are
 * exactly what applying will do — not a second implementation's opinion of it.
 *
 * Replaces a snackbar that could only name the first three problems. The operator now sees
 * every one, with the sheet and row to go and fix, and decides whether to import the valid
 * remainder or cancel and correct the file first.
 */
@Component({
  selector: 'app-import-preview-dialog',
  standalone: true,
  imports: [CommonModule, MatDialogModule, MatButtonModule, MatIconModule, MatTableModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './import-preview-dialog.component.html',
  styles: [
    `
      .summary-table {
        width: 100%;
        margin-bottom: 16px;
      }
      .problems {
        max-height: 40vh;
        overflow-y: auto;
        border-top: 1px solid rgba(0, 0, 0, 0.12);
      }
      .problem-row {
        display: grid;
        grid-template-columns: 8rem 1fr;
        gap: 8px;
        padding: 6px 0;
        border-bottom: 1px solid rgba(0, 0, 0, 0.06);
        font-size: 13px;
      }
      .problem-where {
        color: #555;
        white-space: nowrap;
      }
      .change-line {
        display: block;
      }
      .nothing-to-do {
        color: #555;
      }
      .replace-danger {
        display: flex;
        align-items: flex-start;
        gap: 10px;
        margin: 0 0 16px;
        padding: 12px;
        border: 1px solid #c62828;
        border-left: 4px solid #c62828;
        border-radius: 4px;
        background: #fdecea;
        color: #8e1c1c;
      }
      .mode-line {
        margin-top: -4px;
        color: #555;
        font-size: 13px;
      }
      .backup-note {
        flex: 1;
        margin: 0 12px 0 0;
        font-size: 12px;
        color: #555;
      }
    `,
  ],
})
export class ImportPreviewDialogComponent {
  readonly dialogRef = inject(MatDialogRef<ImportPreviewDialogComponent>);
  readonly data: ExcelImportResult = inject(MAT_DIALOG_DATA);

  readonly columns = ['sheet', 'created', 'updated', 'unchanged', 'deleted', 'skipped'] as const;

  /** Only sheets the workbook actually said something about. */
  readonly outcomes = computed<SheetOutcome[]>(() =>
    Object.entries(this.data.summary ?? {})
      .map(([sheet, counts]) => ({ sheet, ...counts }))
      .filter((o) => o.created || o.updated || o.unchanged || o.deleted || o.skipped),
  );

  readonly problems = computed<ImportProblem[]>(() => this.data.problems ?? []);

  readonly changes = computed<ImportChange[]>(() => this.data.changes ?? []);

  readonly changeCount = computed(() => this.data.change_count ?? 0);

  readonly changesTruncated = computed(() => this.data.changes_truncated ?? 0);

  /** "old → new" with empties shown as an em dash, one line per field. */
  describeField(f: { field: string; old: unknown; new: unknown }): string {
    const show = (v: unknown) => (v === null || v === undefined || v === '' ? '—' : String(v));
    return `${f.field}: ${show(f.old)} → ${show(f.new)}`;
  }

  readonly problemCount = computed(() => this.data.problem_count ?? 0);

  /** Problems beyond the ones the API returned, so the count is never quietly wrong. */
  readonly problemsTruncated = computed(() => this.data.problems_truncated ?? 0);

  readonly isReplaceMode = computed(() => this.data.mode === 'replace');

  readonly totalDeleted = computed(() =>
    this.outcomes().reduce((sum, o) => sum + o.deleted, 0),
  );

  /** How many rows would actually be written. Nothing to apply means nothing to offer. */
  readonly totalWrites = computed(() =>
    this.outcomes().reduce((sum, o) => sum + o.created + o.updated + o.deleted, 0),
  );

  readonly canApply = computed(() => this.totalWrites() > 0);

  apply(): void {
    this.dialogRef.close(true);
  }

  cancel(): void {
    this.dialogRef.close(false);
  }
}
