import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';

import { PipelineService } from '../../../core/services/pipeline.service';

export interface PostprocessLogDialogData {
  runId: string;        // pipeline_run id
  runAccession: string; // for display
  enlightenUrl?: string | null;
}

@Component({
  selector: 'app-postprocess-log-dialog',
  standalone: true,
  imports: [MatDialogModule, MatButtonModule, MatIconModule, MatProgressSpinnerModule],
  template: `
    <h2 mat-dialog-title>
      <mat-icon style="vertical-align:middle;margin-right:6px;">lightbulb</mat-icon>
      Post-processing log — {{ data.runAccession }}
    </h2>

    <mat-dialog-content>
      <div class="log-header">
        @if (streaming()) {
          <mat-spinner diameter="16" style="display:inline-block;vertical-align:middle;margin-right:8px;"></mat-spinner>
          <span style="color:#888;font-size:0.85rem;">Streaming post-processing output…</span>
        } @else {
          <mat-icon style="color:#2e7d32;vertical-align:middle;font-size:18px;">check_circle</mat-icon>
          <span style="color:#2e7d32;font-size:0.85rem;margin-left:4px;">Post-processing complete.</span>
        }
      </div>
      <pre class="log-pre" #logEl>{{ logBuffer() }}</pre>
    </mat-dialog-content>

    @if (!streaming()) {
      <div style="padding: 0 24px 8px; font-size: 0.82rem; color: #888;">
        @if (data.enlightenUrl) {
          Post-processing complete. Open Enlighten to explore the results.
        } @else {
          Post-processing complete. Set the <strong>Enlighten URL</strong> in Settings to enable the Open in Enlighten button.
        }
      </div>
    }

    <mat-dialog-actions align="end">
      @if (!streaming() && data.enlightenUrl) {
        <a mat-flat-button color="primary" [href]="data.enlightenUrl" target="_blank" rel="noopener">
          <mat-icon>lightbulb</mat-icon> Open in Enlighten
        </a>
      }
      <button mat-button mat-dialog-close>Close</button>
    </mat-dialog-actions>
  `,
  styles: [`
    mat-dialog-content {
      max-height: 60vh;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .log-header {
      display: flex;
      align-items: center;
      min-height: 24px;
    }
    .log-pre {
      flex: 1;
      overflow-y: auto;
      background: #1e1e1e;
      color: #d4d4d4;
      font-size: 0.78rem;
      padding: 12px;
      border-radius: 4px;
      white-space: pre-wrap;
      word-break: break-all;
      min-height: 200px;
      max-height: 50vh;
    }
  `],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PostprocessLogDialogComponent {
  readonly data = inject<PostprocessLogDialogData>(MAT_DIALOG_DATA);
  readonly ref = inject(MatDialogRef<PostprocessLogDialogComponent>);
  private readonly pipelineService = inject(PipelineService);

  readonly logBuffer = signal('');
  readonly streaming = signal(true);

  constructor() {
    // The service owns the SSE lifecycle; takeUntilDestroyed closes the connection
    // when the dialog does.
    this.pipelineService
      .streamLogs(this.data.runId)
      .pipe(takeUntilDestroyed(inject(DestroyRef)))
      .subscribe((ev) => {
        if (ev.kind === 'line') {
          // Only show [ODIN-POST] lines and blank separators
          if (ev.data.includes('[ODIN-POST]') || ev.data.trim() === '') {
            this.logBuffer.update((buf) => buf + ev.data + '\n');
          }
        } else {
          this.streaming.set(false);
          const note = ev.data === 'done' ? '[Post-processing finished]' : `[Pipeline ${ev.data}]`;
          this.logBuffer.update((buf) => buf + `\n${note}\n`);
        }
      });
  }
}
