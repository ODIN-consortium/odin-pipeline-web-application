import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatIconModule } from '@angular/material/icon';
import { MatTableModule } from '@angular/material/table';

import { RestoreDiff } from './snapshot-diff';

/** What the dialog needs to describe the restore it is asking about. */
export interface RestoreConfirmData {
  filename: string;
  exportedAt: string;
  exportedBy: string | null;
  diff: RestoreDiff;
}

/**
 * Confirm a restore.
 *
 * Restore is the only operation in ODIN that deletes data the operator never named — a merge
 * only ever adds, and even a replace-mode spreadsheet import deletes solely within the sheets it
 * covers. So this asks more insistently than anything else: it leads with the number of rows that
 * disappear, shows which tables they come from, states when and where the snapshot was taken (an
 * old file is the likeliest mistake), and needs an explicit tick before the button becomes usable.
 *
 * The counts come from comparing the two snapshots on primary key, the same basis the restore
 * itself uses, so the number shown is the number that happens.
 */
@Component({
  selector: 'app-restore-confirm-dialog',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatDialogModule,
    MatButtonModule,
    MatCheckboxModule,
    MatIconModule,
    MatTableModule,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './restore-confirm-dialog.component.html',
  styles: [
    `
      .danger {
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
      .provenance {
        margin: 0 0 16px;
        font-size: 13px;
        color: #555;
      }
      .provenance code {
        word-break: break-all;
      }
      table {
        width: 100%;
        margin-bottom: 12px;
      }
      .deleting {
        color: #8e1c1c;
        font-weight: 500;
      }
      .acknowledge {
        margin-top: 8px;
      }
    `,
  ],
})
export class RestoreConfirmDialogComponent {
  readonly dialogRef = inject(MatDialogRef<RestoreConfirmDialogComponent>);
  readonly data: RestoreConfirmData = inject(MAT_DIALOG_DATA);

  readonly acknowledged = signal(false);
  readonly columns = ['table', 'added', 'changed', 'unchanged', 'deleted'] as const;

  readonly diff = computed(() => this.data.diff);
  readonly totalDeleted = computed(() => this.diff().totalDeleted);
  readonly totalUnchanged = computed(() => this.diff().totalUnchanged);

  /** Rows whose content actually moves: inserted, overwritten or removed. */
  readonly totalWritten = computed(() => this.diff().totalAdded + this.diff().totalChanged);

  /**
   * The database already matches the file.
   *
   * Judged on rows that would actually move. Counting every row the snapshot contains made this
   * unreachable — restoring the same file twice claimed hundreds of rows and offered to do it.
   */
  readonly isNoOp = computed(() => this.diff().totalAffected === 0);

  /** The tick is only demanded when something is actually destroyed. */
  readonly needsAcknowledgement = computed(() => this.totalDeleted() > 0);

  readonly canRestore = computed(
    () => !this.isNoOp() && (!this.needsAcknowledgement() || this.acknowledged()),
  );

  restore(): void {
    this.dialogRef.close(true);
  }

  cancel(): void {
    this.dialogRef.close(false);
  }
}
