import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';

const STATUS_LABELS: Record<string, string> = {
  // Nanopore run / barcode statuses
  ready: 'Ready',
  partial: 'Partial',
  no_files: 'No files',
  not_on_disk: 'Not on disk',
  not_in_metadata: 'Unknown',
  excluded: 'Excluded',
  // Pipeline run statuses
  running: 'Running',
  done: 'Done',
  failed: 'Failed',
  cancelled: 'Cancelled',
  queued: 'Queued',
};

@Component({
  selector: 'app-run-status-chip',
  standalone: true,
  imports: [CommonModule, MatProgressSpinnerModule],
  template: `
    <span [class]="'status-chip status-' + status">
      @if (status === 'running') {
        <mat-spinner
          diameter="10"
          style="margin-right:4px;display:inline-block;vertical-align:middle;"
        ></mat-spinner>
      }
      {{ label ?? statusLabel(status) }}
    </span>
  `,
  styles: [
    `
      .status-chip {
        display: inline-flex;
        align-items: center;
        padding: 2px 10px;
        border-radius: 12px;
        font-size: 0.78rem;
        font-weight: 500;
        white-space: nowrap;
      }
      /* Nanopore run / barcode statuses */
      .status-ready {
        background: #e8f5e9;
        color: #2e7d32;
      }
      .status-partial {
        background: #fff3e0;
        color: #e65100;
      }
      .status-no_files {
        background: #fff3e0;
        color: #e65100;
      }
      .status-not_on_disk {
        background: #ffebee;
        color: #c62828;
      }
      .status-excluded {
        background: #eeeeee;
        color: #9e9e9e;
      }
      .status-not_in_metadata {
        background: #f3e5f5;
        color: #6a1b9a;
      }
      /* Pipeline run statuses */
      .status-running {
        background: #e3f2fd;
        color: #1565c0;
      }
      .status-done {
        background: #e8f5e9;
        color: #2e7d32;
      }
      .status-failed {
        background: #ffebee;
        color: #c62828;
      }
      .status-cancelled {
        background: #f5f5f5;
        color: #757575;
      }
      .status-queued {
        background: #fff8e1;
        color: #e65100;
      }
    `,
  ],
})
export class RunStatusChipComponent {
  @Input({ required: true }) status!: string;
  /** Optional label override; defaults to a human-readable mapping of the status value. */
  @Input() label?: string;

  statusLabel(status: string): string {
    return STATUS_LABELS[status] ?? status;
  }
}
