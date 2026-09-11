import { ChangeDetectionStrategy, Component, DestroyRef, inject, OnInit, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { interval, switchMap, startWith, Subscription } from 'rxjs';

import { MatExpansionModule } from '@angular/material/expansion';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatTooltipModule } from '@angular/material/tooltip';

import { PipelineService } from '../../core/services/pipeline.service';
import { NotificationService } from '../../core/services/notification.service';
import { PipelineRun } from '../../core/models/pipeline.model';
import { RunStatusChipComponent } from '../../shared/components/run-status-chip.component';
import { EmptyStateComponent } from '../../shared/components/empty-state.component';
import {
  LazyTextAccordionDialogComponent,
  LazyTextAccordionDialogData,
} from '../../shared/components/lazy-text-accordion-dialog.component';

@Component({
  selector: 'app-pipeline-runs-page',
  standalone: true,
  imports: [
    CommonModule,
    MatExpansionModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatDialogModule,
    MatTooltipModule,
    RunStatusChipComponent,
    EmptyStateComponent,
  ],
  templateUrl: './pipeline-runs-page.component.html',
  styleUrl: './pipeline-runs-page.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PipelineRunsPageComponent implements OnInit {
  private readonly pipelineService = inject(PipelineService);
  private readonly notify = inject(NotificationService);
  private readonly dialog = inject(MatDialog);
  private readonly destroyRef = inject(DestroyRef);

  readonly runs = signal<PipelineRun[]>([]);
  readonly loading = signal(true);
  readonly expandedRunId = signal<string | null>(null);
  readonly logBuffer = signal('');
  readonly logStreaming = signal(false);
  private logSub: Subscription | null = null;

  ngOnInit(): void {
    interval(5000)
      .pipe(
        startWith(0),
        takeUntilDestroyed(this.destroyRef),
        switchMap(() => this.pipelineService.list()),
      )
      .subscribe({
        next: (runs) => {
          this.runs.set(runs);
          this.loading.set(false);
        },
        error: () => this.loading.set(false),
      });
  }

  refresh(): void {
    this.loading.set(true);
    this.pipelineService.list().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (runs) => {
        this.runs.set(runs);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  onPanelOpen(run: PipelineRun): void {
    this.closeLog();
    this.expandedRunId.set(run.id);
    this.logBuffer.set('');
    this.logStreaming.set(true);
    // The service owns the SSE lifecycle (reconnect, terminal status, close-on-
    // unsubscribe); takeUntilDestroyed means navigating away drops the connection.
    this.logSub = this.pipelineService
      .streamLogs(run.id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((ev) => {
        if (ev.kind === 'line') {
          this.logBuffer.update((buf) => buf + ev.data + '\n');
        } else {
          this.logStreaming.set(false);
          if (ev.data !== 'done') {
            this.logBuffer.update((buf) => buf + `\n[Pipeline ${ev.data}]\n`);
          }
        }
      });
  }

  onPanelClose(run: PipelineRun): void {
    if (this.expandedRunId() === run.id) {
      this.closeLog();
    }
  }

  private closeLog(): void {
    this.logSub?.unsubscribe();
    this.logSub = null;
    this.expandedRunId.set(null);
    this.logStreaming.set(false);
  }

  cancelRun(run: PipelineRun): void {
    this.pipelineService.cancel(run.id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        this.notify.success('Cancel signal sent.');
        this.refresh();
      },
      error: (err) => {
        this.notify.error(err, 'Cancel failed.');
      },
    });
  }

  deleteRecord(run: PipelineRun): void {
    this.pipelineService.deleteRecord(run.id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        this.notify.success('Run record deleted.');
        if (this.expandedRunId() === run.id) {
          this.closeLog();
        }
        this.runs.update((rs) => rs.filter((r) => r.id !== run.id));
      },
      error: (err) => {
        this.notify.error(err, 'Delete failed.');
      },
    });
  }

  openManifest(run: PipelineRun): void {
    const label = (run.run_accessions ?? []).join(', ') || run.pipeline_type;
    this.dialog.open<LazyTextAccordionDialogComponent, LazyTextAccordionDialogData>(
      LazyTextAccordionDialogComponent,
      {
        data: {
          icon: 'description',
          title: 'Run manifest',
          label,
          entries: [
            { key: run.id, label: run.pipeline_type, load: () => this.pipelineService.getManifest(run.id) },
          ],
          emptyMessage: 'No manifest for this run.',
          notFoundMessage:
            'run_manifest.txt not found. It is written at launch time — older runs may not have one.',
        },
        width: '720px',
        maxWidth: '95vw',
      },
    );
  }

  openConfidenceReport(run: PipelineRun): void {
    const label = (run.run_accessions ?? []).join(', ') || run.pipeline_type;
    this.dialog.open<LazyTextAccordionDialogComponent, LazyTextAccordionDialogData>(
      LazyTextAccordionDialogComponent,
      {
        data: {
          icon: 'biotech',
          title: 'Confidence report',
          label,
          entries: [
            { key: run.id, label: run.pipeline_type, load: () => this.pipelineService.getConfidenceReport(run.id) },
          ],
          emptyMessage: 'No confidence report for this run.',
          notFoundMessage:
            'Confidence report not found. Read extraction may not have run for this pipeline run.',
        },
        width: '720px',
        maxWidth: '95vw',
      },
    );
  }

  deleteWorkdir(run: PipelineRun): void {
    this.pipelineService.deleteWorkdir(run.id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        this.notify.success('Work directory deleted.');
      },
      error: (err) => {
        this.notify.error(err, 'Delete work dir failed.');
      },
    });
  }

  launchSquirrel(run: PipelineRun): void {
    const runAccession = (run.run_accessions ?? [])[0];
    if (!runAccession) {
      this.notify.action('Cannot determine run accession for this mpox run.', 'OK', 4000);
      return;
    }
    this.pipelineService
      .launch({
        pipeline_type: 'squirrel',
        run_accessions: [runAccession],
        source_run_id: run.id,
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (squirrelRun) => {
          this.notify.action(`Squirrel launched (ID: ${squirrelRun.id.slice(0, 8)}).`, 'OK', 5000);
          this.refresh();
        },
        error: (err) => {
          // This endpoint can answer with a nested {detail: {message}}, which the shared
          // extraction in NotificationService deliberately does not guess at.
          const detail = err?.error?.detail;
          const nested = typeof detail === 'string' ? null : detail?.message;
          if (nested) {
            this.notify.message(nested, 6000);
          } else {
            this.notify.error(err, 'Squirrel launch failed.');
          }
        },
      });
  }
}
