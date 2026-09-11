import {
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  inject,
  OnInit,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { catchError, filter, forkJoin, fromEvent, interval, map, merge, of } from 'rxjs';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterModule } from '@angular/router';
import { MatTableModule } from '@angular/material/table';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatMenuModule } from '@angular/material/menu';
import { MatSelectModule } from '@angular/material/select';

import { DisplayItem, groupRuns } from './group-runs';
import { LinkToGroupData, LinkToGroupDialogComponent } from './link-to-group-dialog.component';
import { BiomemeFolderTableComponent } from './biomeme-folder-table.component';
import { DiscoveryService } from '../../core/services/discovery.service';
import { NotificationService } from '../../core/services/notification.service';
import { NanoporeRunAccessionsService } from '../../core/services/nanopore-run-accessions.service';
import { PipelineService } from '../../core/services/pipeline.service';
import { SettingsService } from '../../core/services/settings.service';
import { BiomemeRunsService } from '../../core/services/biomeme-runs.service';
import { NanoporeRunAccession } from '../../core/models/run.model';
import {
  BarcodeStatus,
  BiomemeDiscoveryResult,
  BiomemeFolderStatus,
  NanoporeDiscoveryResult,
  NanoporeRunStatus,
  PipelineRunSummary,
} from '../../core/models/discovery.model';
import { LaunchWizardComponent, LaunchWizardData, LaunchWizardResult } from './launch-wizard/launch-wizard.component';
import {
  BiomemeLaunchWizardComponent,
  BiomemeLaunchWizardData,
  BiomemeLaunchWizardResult,
} from './biomeme-launch-wizard/biomeme-launch-wizard.component';
import { PostprocessLogDialogComponent, PostprocessLogDialogData } from './postprocess-log-dialog/postprocess-log-dialog.component';
import { RunStatusChipComponent } from '../../shared/components/run-status-chip.component';
import { MergeDecisionButtonsComponent } from '../../shared/components/merge-decision-buttons.component';
import { WarningStripComponent } from '../../shared/components/warning-strip.component';
import {
  LazyTextAccordionDialogComponent,
  LazyTextAccordionDialogData,
} from '../../shared/components/lazy-text-accordion-dialog.component';
import { WarningDismissalService } from '../../core/services/warning-dismissal.service';

// ── Dashboard display types ──────────────────────────────────────────────────

interface ItemWarningEntry {
  message: string;
  keys: string[];
}

@Component({
  selector: 'app-dashboard-page',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    RouterModule,
    MatTableModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    MatSlideToggleModule,
    MatDialogModule,
    MatMenuModule,
    MatSelectModule,
    RunStatusChipComponent,
    MergeDecisionButtonsComponent,
    BiomemeFolderTableComponent,
    WarningStripComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './dashboard-page.component.html',
  styleUrls: ['./run-tables.css', './dashboard-page.component.css'],
})
export class DashboardPageComponent implements OnInit {
  private readonly discoveryService = inject(DiscoveryService);
  private readonly nraService = inject(NanoporeRunAccessionsService);
  private readonly pipelineService = inject(PipelineService);
  private readonly settingsService = inject(SettingsService);
  private readonly biomemeRunsService = inject(BiomemeRunsService);
  private readonly dialog = inject(MatDialog);
  private readonly notify = inject(NotificationService);
  private readonly router = inject(Router);
  private readonly warningDismissal = inject(WarningDismissalService);
  private readonly destroyRef = inject(DestroyRef);

  readonly nanoporeLoading = signal(false);
  readonly biomemeLoading = signal(false);
  readonly nanoporeScanError = signal(false);
  readonly biomemeScanError = signal(false);
  readonly nanoporeResult = signal<NanoporeDiscoveryResult | null>(null);
  readonly biomemeResult = signal<BiomemeDiscoveryResult | null>(null);
  readonly expandedNanopore = signal<NanoporeRunStatus | null>(null);
  readonly enlightenUrl = signal<string | null>(null);
  readonly postprocessingRunId = signal<string | null>(null);
  readonly pendingGroups = signal<NanoporeRunAccession[]>([]);

  toggleExcludeBiomemeFolder(folder: BiomemeFolderStatus): void {
    const action$ = folder.is_excluded
      ? this.discoveryService.unexcludeBiomemeFolder(folder.folder_path)
      : this.discoveryService.excludeBiomemeFolder(folder.folder_path);
    action$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: () => this.loadBiomeme(true) });
  }

  readonly hideExcluded = signal(false);
  readonly groupEvidenceOpen = signal<Set<string>>(new Set());
  private readonly periodicBackgroundScanMs = 120000;
  private initialBackgroundRefreshPending = false;
  private initialBackgroundRefreshDone = false;

  /**
   * Grouped display items: single runs or run-groups (continuation or same-sample).
   *
   * The grouping itself lives in `group-runs.ts` as a pure function — it is the most intricate
   * logic on this page and the only way to test it properly was to get it out of the component.
   */
  readonly displayItems = computed((): DisplayItem[] =>
    groupRuns(this.nanoporeResult()?.runs ?? [], this.hideExcluded()),
  );

  /** Set of run accessions that are part of a group (not standalone). */
  readonly groupMemberSet = computed(() => {
    const inGroup = new Set<string>();
    for (const item of this.displayItems()) {
      if (item.kind !== 'single') item.runs.forEach((r: NanoporeRunStatus) => inGroup.add(r.run_accession));
    }
    return inGroup;
  });

  readonly scanState = computed(() => {
    if (this.nanoporeLoading() || this.biomemeLoading()) {
      return { color: 'yellow', label: 'Scanning' as const };
    }
    if (this.nanoporeScanError() || this.biomemeScanError()) {
      return { color: 'red', label: 'Scan failed' as const };
    }
    if (this.nanoporeResult() && this.biomemeResult()) {
      return { color: 'green', label: 'Scan OK' as const };
    }
    return { color: 'yellow', label: 'Scan pending' as const };
  });

  /** Returns the run list for any DisplayItem (single → one-element array). */
  getRuns(item: DisplayItem): NanoporeRunStatus[] {
    return item.kind === 'single' ? [item.run] : item.runs;
  }

  groupKey(item: DisplayItem): string {
    if (item.kind === 'single') return item.run.run_accession;
    return item.runs.map((r) => r.run_accession).sort().join('|');
  }

  formatGap(hours: number): string {
    if (hours < 1) return `${Math.round(hours * 60)} min`;
    return `${hours.toFixed(1)} h`;
  }

  getRunWarnings(run: NanoporeRunStatus): string[] {
    return run.metadata_warnings ?? [];
  }

  getRunWarningKey(run: NanoporeRunStatus, warning: string): string {
    return this.warningDismissal.buildRunWarningKey(run.run_accession, warning);
  }

  getVisibleRunWarnings(run: NanoporeRunStatus): string[] {
    return this.getRunWarnings(run).filter(
      (warning) => !this.warningDismissal.isDismissed(this.getRunWarningKey(run, warning)),
    );
  }

  getHiddenRunWarningCount(run: NanoporeRunStatus): number {
    return Math.max(
      this.getRunWarnings(run).length - this.getVisibleRunWarnings(run).length,
      this.warningDismissal.countDismissedForRun(run.run_accession),
    );
  }

  dismissRunWarning(run: NanoporeRunStatus, warning: string): void {
    this.warningDismissal.dismiss(this.getRunWarningKey(run, warning));
  }

  restoreRunWarnings(run: NanoporeRunStatus): void {
    this.warningDismissal.undismissRunWarnings(run.run_accession);
  }

  private getItemWarningEntries(item: DisplayItem): ItemWarningEntry[] {
    const grouped = new Map<string, Set<string>>();
    for (const run of this.getRuns(item)) {
      for (const warning of this.getRunWarnings(run)) {
        const key = this.getRunWarningKey(run, warning);
        if (!grouped.has(warning)) {
          grouped.set(warning, new Set<string>());
        }
        grouped.get(warning)!.add(key);
      }
    }
    return Array.from(grouped.entries()).map(([message, keys]) => ({
      message,
      keys: Array.from(keys),
    }));
  }

  getItemWarnings(item: DisplayItem): string[] {
    return this.getItemWarningEntries(item)
      .filter((entry) => entry.keys.some((key) => !this.warningDismissal.isDismissed(key)))
      .map((entry) => entry.message);
  }

  getHiddenItemWarningCount(item: DisplayItem): number {
    const currentHidden = this.getItemWarningEntries(item)
      .filter((entry) => entry.keys.every((key) => this.warningDismissal.isDismissed(key))).length;
    const persistedHidden = this.getRuns(item)
      .reduce((total, run) => total + this.warningDismissal.countDismissedForRun(run.run_accession), 0);
    return Math.max(currentHidden, persistedHidden);
  }

  dismissItemWarning(item: DisplayItem, warningMessage: string): void {
    const entry = this.getItemWarningEntries(item).find((candidate) => candidate.message === warningMessage);
    if (!entry) {
      return;
    }
    this.warningDismissal.dismissMany(entry.keys);
  }

  restoreItemWarnings(item: DisplayItem): void {
    for (const run of this.getRuns(item)) {
      this.warningDismissal.undismissRunWarnings(run.run_accession);
    }
  }

  formatWarningsForTooltip(warnings: string[]): string {
    return warnings.join('\n');
  }

  toggleEvidence(key: string): void {
    const s = new Set(this.groupEvidenceOpen());
    if (s.has(key)) { s.delete(key); } else { s.add(key); }
    this.groupEvidenceOpen.set(s);
  }

  /**
   * Apply one merge decision to every run in a group.
   *
   * Was a hand-rolled countdown — `let pending = runs.length` decremented by a callback wired to
   * both next and error — which is `forkJoin`. Each request absorbs its own failure so that one
   * rejected run neither cancels its siblings nor stops the reload, matching what the countdown
   * did; the difference is that a failure is now reported rather than silently swallowed, which
   * previously left the operator believing a decision had been recorded when it had not.
   */
  setGroupMerge(runs: NanoporeRunStatus[], value: boolean | null): void {
    const requests = runs.map((run) => {
      const request$ =
        value === null
          ? this.pipelineService.clearMergeDecision(run.run_accession)
          : this.pipelineService.setMergeDecision(run.run_accession, { auto_merge: value });
      return request$.pipe(
        map(() => null),
        catchError((err: unknown) => of(err)),
      );
    });

    forkJoin(requests)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((outcomes) => {
        const failed = outcomes.filter((o) => o !== null);
        if (failed.length) {
          this.notify.error(failed[0], `Failed to set the merge decision for ${failed.length} run(s).`);
        }
        this.loadNanopore();
      });
  }

  ngOnInit(): void {
    this.initialBackgroundRefreshPending = true;
    this.scan(false);
    this.loadPendingGroups();
    this.settingsService.get('enlighten_url').pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (s) => this.enlightenUrl.set(s.value ?? null),
      error: () => {},
    });
    this.startBackgroundScanning();
  }

  openEnlighten(): void {
    const url = this.enlightenUrl();
    if (url) window.open(url, '_blank');
  }

  scan(forceRefresh = true): void {
    this.expandedNanopore.set(null);
    this.loadNanopore(undefined, forceRefresh);
    this.loadBiomeme(forceRefresh);
  }

  /**
   * Refresh in the background: on a timer, and whenever the operator comes back to the tab.
   *
   * Was a setInterval handle plus a manually added `visibilitychange` listener, both torn down in
   * ngOnDestroy — two things to remember to release, and a leak in waiting if either was missed.
   * One stream tied to DestroyRef releases both, and the component no longer needs OnDestroy.
   */
  private startBackgroundScanning(): void {
    merge(
      interval(this.periodicBackgroundScanMs),
      fromEvent(document, 'visibilitychange').pipe(filter(() => !document.hidden)),
    )
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => this.backgroundRefresh());
  }

  private backgroundRefresh(): void {
    if (this.nanoporeLoading() || this.biomemeLoading()) {
      return;
    }
    const restoreExpanded = this.expandedNanopore()?.run_accession;
    this.loadNanopore(restoreExpanded, true);
    this.loadBiomeme(true);
  }

  private tryInitialBackgroundRefresh(): void {
    if (!this.initialBackgroundRefreshPending || this.initialBackgroundRefreshDone) {
      return;
    }
    if (this.nanoporeLoading() || this.biomemeLoading()) {
      return;
    }
    this.initialBackgroundRefreshDone = true;
    // Let the cached result paint first, then refresh in the background.
    setTimeout(() => this.backgroundRefresh(), 0);
  }

  toggleNanopore(row: NanoporeRunStatus): void {
    this.expandedNanopore.set(this.expandedNanopore() === row ? null : row);
  }

  openLaunchWizard(run: NanoporeRunStatus): void {
    const ref = this.dialog.open<LaunchWizardComponent, LaunchWizardData, LaunchWizardResult>(
      LaunchWizardComponent,
      {
        data: {
          run_accession: run.run_accession,
          artic_on_disk: run.artic_on_disk,
          on_disk: run.on_disk,
        },
        width: '95vw',
        maxWidth: '1100px',
        disableClose: true,
      },
    );
    ref.afterClosed().pipe(takeUntilDestroyed(this.destroyRef)).subscribe((result) => {
      if (result?.registered) {
        this.scan();
      }
      if (result?.pipelineRun) {
        const ref2 = this.notify.action(`Pipeline launched. Track progress in Pipeline Runs.`, 'Go to Pipeline Runs', 10000);
        ref2.onAction().pipe(takeUntilDestroyed(this.destroyRef)).subscribe(() => this.router.navigate(['/pipeline-runs']));
      }
    });
  }

  openBiomemeLaunchWizard(folder: BiomemeFolderStatus | null): void {
    const allFolders = this.biomemeResult()?.folders ?? [];
    const ref = this.dialog.open<
      BiomemeLaunchWizardComponent,
      BiomemeLaunchWizardData,
      BiomemeLaunchWizardResult
    >(BiomemeLaunchWizardComponent, {
      data: { folder: folder ?? null, allFolders },
      width: '680px',
      maxWidth: '95vw',
      disableClose: true,
    });
    ref.afterClosed().pipe(takeUntilDestroyed(this.destroyRef)).subscribe((result) => {
      if (result?.registered || result?.metadataEdited) {
        this.loadBiomeme(true);
      }
      if (result?.launched) {
        const ref2 = this.notify.action(`Biomeme pipeline launched. Track progress in Pipeline Runs.`, 'Go to Pipeline Runs', 10000);
        ref2.onAction().pipe(takeUntilDestroyed(this.destroyRef)).subscribe(() => this.router.navigate(['/pipeline-runs']));
      }
    });
  }

  toggleExcludeRun(run: NanoporeRunStatus): void {
    const action$ = run.is_excluded
      ? this.discoveryService.unexcludeRun(run.run_accession)
      : this.discoveryService.excludeRun(run.run_accession);
    action$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: () => this.loadNanopore() });
  }

  setMerge(run: NanoporeRunStatus, value: boolean): void {
    if (run.auto_merge === value) {
      // Already set to this value — act as a toggle (clear)
      this.clearMerge(run);
      return;
    }
    this.pipelineService.setMergeDecision(run.run_accession, { auto_merge: value }).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => this.loadNanopore(run.run_accession),
      error: (err) =>
        this.notify.error(err, 'Failed to set merge decision.'),
    });
  }

  onRunMergeChange(run: NanoporeRunStatus, decision: boolean | null): void {
    if (decision === null) {
      this.clearMerge(run);
    } else {
      this.setMerge(run, decision);
    }
  }

  clearMerge(run: NanoporeRunStatus): void {
    this.pipelineService.clearMergeDecision(run.run_accession).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => this.loadNanopore(run.run_accession),
      error: (err) =>
        this.notify.error(err, 'Failed to clear merge decision.'),
    });
  }

  toggleExcludeBarcode(run: NanoporeRunStatus, bc: BarcodeStatus): void {
    const action$ = bc.is_excluded
      ? this.discoveryService.unexcludeBarcode(run.run_accession, bc.barcode)
      : this.discoveryService.excludeBarcode(run.run_accession, bc.barcode);
    action$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: () => this.loadNanopore(run.run_accession) });
  }

  readyBarcodes(run: NanoporeRunStatus): number {
    return run.barcodes.filter((b) => b.status === 'ready').length;
  }

  triggerPostprocess(run: NanoporeRunStatus): void {
    const runId = run.last_pipeline_run_id;
    if (!runId) return;
    this.postprocessingRunId.set(runId);
    this.pipelineService.runPostprocess(runId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        this.postprocessingRunId.set(null);
        this.dialog.open<PostprocessLogDialogComponent, PostprocessLogDialogData>(
          PostprocessLogDialogComponent,
          {
            data: { runId, runAccession: run.run_accession, enlightenUrl: this.enlightenUrl() },
            width: '700px',
            maxWidth: '95vw',
          },
        );
      },
      error: (err) => {
        this.notify.error(err, 'Failed to start post-processing.');
        this.postprocessingRunId.set(null);
      },
    });
  }

  openManifest(run: NanoporeRunStatus): void {
    const entries = (run.pipeline_runs ?? []).map((pr) => ({
      key: pr.id,
      label: this.manifestEntryLabel(pr),
      load: () => this.pipelineService.getManifest(pr.id),
    }));
    if (!entries.length) return;
    this.dialog.open<LazyTextAccordionDialogComponent, LazyTextAccordionDialogData>(
      LazyTextAccordionDialogComponent,
      {
        data: {
          icon: 'description',
          title: 'Run manifest',
          label: run.run_accession,
          entries,
          emptyMessage: 'No pipeline runs for this run.',
          notFoundMessage:
            'run_manifest.txt not found. It is written at launch time — older runs may not have one.',
        },
        width: '720px',
        maxWidth: '95vw',
      },
    );
  }

  private manifestEntryLabel(pr: PipelineRunSummary): string {
    const parts = [pr.extract_target ? `${pr.pipeline_type} (${pr.extract_target})` : pr.pipeline_type];
    if (pr.created_at) parts.push(pr.created_at.slice(0, 10));
    parts.push(pr.status);
    return parts.join(' · ');
  }

  openConfidenceReport(run: NanoporeRunStatus): void {
    const entries = (run.confidence_report_targets ?? []).map((target) => ({
      key: target,
      label: target,
      load: () => this.discoveryService.getConfidenceReport(run.run_accession, target),
    }));
    if (!entries.length) return;
    this.dialog.open<LazyTextAccordionDialogComponent, LazyTextAccordionDialogData>(
      LazyTextAccordionDialogComponent,
      {
        data: {
          icon: 'biotech',
          title: 'Confidence report',
          label: run.run_accession,
          entries,
          emptyMessage: 'No confidence reports found for this run.',
          notFoundMessage:
            'Confidence report not found. Read extraction may not have run for this pipeline run.',
        },
        width: '720px',
        maxWidth: '95vw',
      },
    );
  }

  private loadPendingGroups(): void {
    this.nraService.list(true).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (groups) => this.pendingGroups.set(groups),
      error: () => {},
    });
  }

  linkToGroup(run: NanoporeRunStatus): void {
    const pending = this.pendingGroups();
    if (!pending.length) return;
    const ref = this.dialog.open<LinkToGroupDialogComponent, LinkToGroupData, string>(
      LinkToGroupDialogComponent,
      {
        data: {
          run_accession: run.run_accession,
          pendingGroups: pending.map((g) => ({ id: g.id, label: g.label! })),
        },
        width: '400px',
      },
    );
    ref.afterClosed().pipe(takeUntilDestroyed(this.destroyRef)).subscribe((nraId) => {
      if (!nraId) return;
      this.nraService.link(nraId, run.run_accession).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
        next: () => {
          this.notify.action(`Linked to group`, 'OK', 3000);
          this.loadPendingGroups();
          this.loadNanopore(run.run_accession);
        },
        error: (err) =>
          this.notify.error(err, 'Link failed'),
      });
    });
  }

  private loadNanopore(restoreExpanded?: string, forceRefresh = false): void {
    this.nanoporeScanError.set(false);
    this.nanoporeLoading.set(true);
    this.discoveryService.nanopore(forceRefresh).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (data) => {
        this.nanoporeResult.set(data);
        this.nanoporeLoading.set(false);
        if (restoreExpanded) {
          const row = data.runs.find((r) => r.run_accession === restoreExpanded) ?? null;
          this.expandedNanopore.set(row);
        }
        this.tryInitialBackgroundRefresh();
      },
      error: () => {
        this.nanoporeScanError.set(true);
        this.nanoporeLoading.set(false);
        this.tryInitialBackgroundRefresh();
      },
    });
  }

  private loadBiomeme(forceRefresh = false): void {
    this.biomemeScanError.set(false);
    this.biomemeLoading.set(true);
    this.discoveryService.biomeme(forceRefresh).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (data) => {
        this.biomemeResult.set(data);
        this.biomemeLoading.set(false);
        this.tryInitialBackgroundRefresh();
      },
      error: () => {
        this.biomemeScanError.set(true);
        this.biomemeLoading.set(false);
        this.tryInitialBackgroundRefresh();
      },
    });
  }
}
