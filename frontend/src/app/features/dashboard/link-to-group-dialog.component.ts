import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatSelectModule } from '@angular/material/select';

export interface LinkToGroupData {
  run_accession: string;
  pendingGroups: { id: string; label: string }[];
}

/** Attach a discovered run to a run group that already exists, chosen from the pending ones. */
@Component({
  selector: 'app-link-to-group-dialog',
  standalone: true,
  imports: [CommonModule, FormsModule, MatButtonModule, MatDialogModule, MatSelectModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>Link to existing group</h2>
    <mat-dialog-content>
      <p class="run">
        Run: <code>{{ data.run_accession }}</code>
      </p>
      <mat-select [(ngModel)]="selectedId" placeholder="Select a pending group">
        @for (g of data.pendingGroups; track g.id) {
          <mat-option [value]="g.id">{{ g.label }}</mat-option>
        }
      </mat-select>
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button mat-dialog-close>Cancel</button>
      <button mat-flat-button color="primary" [disabled]="!selectedId()" (click)="confirm()">
        Link
      </button>
    </mat-dialog-actions>
  `,
  styles: [
    `
      mat-dialog-content {
        min-width: 320px;
        padding-top: 8px;
      }
      .run {
        font-size: 0.85rem;
        color: #555;
        margin: 0 0 12px;
      }
    `,
  ],
})
export class LinkToGroupDialogComponent {
  readonly data = inject<LinkToGroupData>(MAT_DIALOG_DATA);
  private readonly ref = inject(MatDialogRef<LinkToGroupDialogComponent>);

  readonly selectedId = signal('');

  confirm(): void {
    this.ref.close(this.selectedId());
  }
}
