import { Component, EventEmitter, Input, Output } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';

@Component({
  selector: 'app-merge-decision-buttons',
  standalone: true,
  imports: [MatButtonModule, MatIconModule, MatTooltipModule],
  template: `
    <div class="merge-buttons" (click)="$event.stopPropagation()">
      @if (decision === true) {
        <button mat-flat-button color="primary" class="merge-btn"
          matTooltip="Merge ON — click to undo"
          (click)="decisionChange.emit(null)">
          <mat-icon class="btn-icon">call_merge</mat-icon>
          Yes, merge
        </button>
      } @else {
        <button mat-stroked-button class="merge-btn"
          matTooltip="Merge"
          (click)="decisionChange.emit(true)">
          <mat-icon class="btn-icon">call_merge</mat-icon>
          Yes, merge
        </button>
      }
      @if (decision === false) {
        <button mat-flat-button color="warn" class="merge-btn"
          matTooltip="Keep separate — click to undo"
          (click)="decisionChange.emit(null)">
          <mat-icon class="btn-icon">call_split</mat-icon>
          No, keep separate
        </button>
      } @else {
        <button mat-stroked-button class="merge-btn"
          matTooltip="Keep runs separate"
          (click)="decisionChange.emit(false)">
          <mat-icon class="btn-icon">call_split</mat-icon>
          No, keep separate
        </button>
      }
      @if (decision !== null && decision !== undefined) {
        <button mat-icon-button class="clear-btn"
          matTooltip="Clear merge decision"
          (click)="decisionChange.emit(null)">
          <mat-icon class="btn-icon">close</mat-icon>
        </button>
      }
    </div>
  `,
  styles: [
    `
      .merge-buttons {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        flex-wrap: wrap;
      }
      .merge-btn {
        min-width: 0;
        line-height: 28px;
        padding: 0 10px;
        font-size: 0.8rem;
      }
      .clear-btn {
        width: 28px;
        height: 28px;
        line-height: 28px;
      }
      .btn-icon {
        font-size: 16px;
        height: 16px;
        width: 16px;
        vertical-align: middle;
      }
    `,
  ],
})
export class MergeDecisionButtonsComponent {
  /** Current merge decision: true = merge, false = keep separate, null = undecided. */
  @Input({ required: true }) decision!: boolean | null;
  /** Emits the new decision when a button is clicked. */
  @Output() decisionChange = new EventEmitter<boolean | null>();
}
