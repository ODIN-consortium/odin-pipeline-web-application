import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatSelectModule } from '@angular/material/select';
import { MatTableModule } from '@angular/material/table';

import { SyncReviewRow, TABLE_LABELS } from '../../core/models/sync.model';
import { actionOptions, rowLabel } from './review-rows';

/**
 * The merge review table for one syncable table: category badge, key-field label, the
 * mine-vs-theirs changes breakdown, and the action select. The rows pass in by reference
 * and the select writes each row's `action` in place — the sync page reads the same row
 * objects when the operator applies, so nothing needs to be emitted back.
 */
@Component({
  selector: 'app-sync-review-table',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatFormFieldModule,
    MatIconModule,
    MatSelectModule,
    MatTableModule,
  ],
  templateUrl: './sync-review-table.component.html',
  styleUrls: ['./sync-review-table.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SyncReviewTableComponent {
  readonly table = input.required<string>();
  readonly rows = input.required<SyncReviewRow[]>();

  readonly displayedColumns = ['category', 'label', 'changes', 'action'];

  tableLabel(): string {
    return TABLE_LABELS[this.table()] ?? this.table();
  }

  getRowLabel(row: Record<string, unknown>): string {
    return rowLabel(row, this.table());
  }

  readonly actionOptions = actionOptions;
}
