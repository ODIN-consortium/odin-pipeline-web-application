import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';

/**
 * A dismissible list of metadata warnings with an optional "show dismissed" chip.
 *
 * The same markup lived three times — the dashboard's group header, its expanded run
 * detail, and the launch wizard — each wired to WarningDismissalService through its own
 * page. This component is deliberately dumb: the pages keep the key-building and
 * dismissal logic, it only renders and emits. `stopPropagation` on both buttons because
 * two of the three call sites sit inside clickable rows.
 *
 * Two looks, both preserved verbatim from where they came: 'strip' is the dashboard's
 * inline amber bar, 'banner' the wizard's dialog banner.
 */
@Component({
  selector: 'app-warning-strip',
  standalone: true,
  imports: [MatButtonModule, MatIconModule, MatTooltipModule],
  template: `
    @if (warnings().length > 0) {
      <div [class]="variant() === 'banner' ? 'warning-banner' : 'warning-strip'">
        <mat-icon class="icon-warn">warning_amber</mat-icon>
        <div class="warning-lines">
          @for (warning of warnings(); track warning) {
            <div class="warning-line">
              <span>{{ warning }}</span>
              <button
                mat-icon-button
                class="warning-dismiss-btn"
                matTooltip="Dismiss warning"
                (click)="dismiss.emit(warning); $event.stopPropagation()"
              >
                <mat-icon>close</mat-icon>
              </button>
            </div>
          }
        </div>
      </div>
    }
    @if (hiddenCount() > 0) {
      <div class="warning-restore-row" [class.warning-restore-row-banner]="variant() === 'banner'">
        <button
          mat-stroked-button
          class="warning-restore-chip"
          (click)="restore.emit(); $event.stopPropagation()"
        >
          <mat-icon>visibility</mat-icon>
          Show dismissed warnings ({{ hiddenCount() }})
        </button>
      </div>
    }
  `,
  styles: [
    `
      .warning-strip {
        display: flex;
        align-items: flex-start;
        gap: 8px;
        padding: 8px 12px;
        border-radius: 4px;
        background: #fff8e1;
        color: #8d6e63;
        font-size: 0.83rem;
        margin: 0 0 6px;
      }
      .warning-banner {
        display: flex;
        align-items: flex-start;
        gap: 8px;
        color: #e65100;
        background: #fff3e0;
        padding: 12px;
        border-radius: 4px;
        margin-bottom: 16px;
      }
      .icon-warn {
        color: #e65100;
      }
      .warning-lines {
        flex: 1;
        min-width: 0;
      }
      .warning-line {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 10px;
      }
      .warning-dismiss-btn {
        width: 24px;
        height: 24px;
        min-width: 24px;
        line-height: 24px;
        color: #8d6e63;
      }
      .warning-dismiss-btn mat-icon {
        font-size: 16px;
        height: 16px;
        width: 16px;
      }
      .warning-restore-row {
        margin: 0 0 8px;
        display: flex;
        justify-content: flex-end;
      }
      .warning-restore-row-banner {
        margin: -8px 0 12px;
      }
      .warning-restore-chip {
        font-size: 0.78rem;
        line-height: 1;
        padding: 0 8px;
      }
      .warning-restore-chip mat-icon {
        font-size: 16px;
        height: 16px;
        width: 16px;
        margin-right: 4px;
      }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class WarningStripComponent {
  readonly warnings = input.required<string[]>();
  readonly hiddenCount = input(0);
  readonly variant = input<'strip' | 'banner'>('strip');
  readonly dismiss = output<string>();
  readonly restore = output<void>();
}
