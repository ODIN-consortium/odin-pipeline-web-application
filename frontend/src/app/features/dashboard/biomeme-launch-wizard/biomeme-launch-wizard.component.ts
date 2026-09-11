import { DestroyRef, Component, inject, OnInit, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSelectModule } from '@angular/material/select';
import { MatTooltipModule } from '@angular/material/tooltip';
import { forkJoin } from 'rxjs';

import { BiomemeRunsService } from '../../../core/services/biomeme-runs.service';
import { NotificationService } from '../../../core/services/notification.service';
import { SamplesService } from '../../../core/services/samples.service';
import { Sample } from '../../../core/models/sample.model';
import { BiomemeFolderStatus, BiomemeFileStatus } from '../../../core/models/discovery.model';

export interface BiomemeLaunchWizardData {
  folder: BiomemeFolderStatus | null;   // null = all folders
  allFolders: BiomemeFolderStatus[];
}

export interface BiomemeLaunchWizardResult {
  registered: boolean;
  metadataEdited: boolean;
  launched: boolean;
  pipelineRunId?: string;
}

type WizardStep = 'loading' | 'register' | 'launch' | 'edit' | 'error';

interface RegisterEntry {
  run_name: string;
  selectedSampleId: string | null;
}

interface EditEntry {
  run_id: string;
  run_name: string;
  selectedSampleId: string | null;
  originalSampleId: string | null;
}

@Component({
  selector: 'app-biomeme-launch-wizard',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatDialogModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatSelectModule,
    MatTooltipModule,
    ],
  templateUrl: './biomeme-launch-wizard.component.html',
  styleUrl: './biomeme-launch-wizard.component.css',
})
export class BiomemeLaunchWizardComponent implements OnInit {
  readonly data: BiomemeLaunchWizardData = inject(MAT_DIALOG_DATA);
  private readonly ref = inject(
    MatDialogRef<BiomemeLaunchWizardComponent, BiomemeLaunchWizardResult>,
  );
  private readonly biomemeService = inject(BiomemeRunsService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly samplesService = inject(SamplesService);
  private readonly notify = inject(NotificationService);

  readonly step = signal<WizardStep>('loading');
  readonly samples = signal<Sample[]>([]);
  readonly registerEntries = signal<RegisterEntry[]>([]);
  readonly editEntries = signal<EditEntry[]>([]);
  readonly submitting = signal(false);
  readonly launching = signal(false);
  readonly savingEdits = signal(false);
  readonly metadataEdited = signal(false);
  readonly errorMsg = signal('');
  readonly newlyRegistered = signal(0);

  get title(): string {
    return this.data.folder
      ? `Launch Biomeme Pipeline — ${this.data.folder.folder_path}`
      : 'Launch Biomeme Pipeline';
  }

  /** All non-excluded folders to consider */
  get activeFolders(): BiomemeFolderStatus[] {
    const folders = this.data.allFolders.filter((f) => !f.is_excluded);
    return this.data.folder ? folders.filter((f) => f.folder_path === this.data.folder!.folder_path) : folders;
  }

  get totalFilesOnDisk(): number {
    return this.activeFolders.reduce((s, f) => s + f.file_count, 0);
  }

  get totalRegistered(): number {
    return this.activeFolders.reduce((s, f) => s + f.registered_count, 0);
  }

  get unregisteredFiles(): BiomemeFileStatus[] {
    return this.activeFolders.flatMap((f) => f.files.filter((file) => !file.registered));
  }

  get selectedCount(): number {
    return this.registerEntries().filter((e) => !!e.selectedSampleId).length;
  }

  get totalRegisteredAfter(): number {
    return this.totalRegistered + this.newlyRegistered();
  }

  ngOnInit(): void {
    this.samplesService
      .list()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
      next: (samples) => {
        this.samples.set(samples);
        const entries = this.unregisteredFiles.map((f) => ({
          run_name: f.run_name,
          selectedSampleId: null,
        }));
        this.registerEntries.set(entries);
        this.step.set(entries.length === 0 ? 'launch' : 'register');
      },
      error: (err) => {
        this.errorMsg.set(err?.error?.detail ?? 'Failed to load samples.');
        this.step.set('error');
      },
    });
  }

  sampleLabel(s: Sample): string {
    return `${s.sample_code} — ${this.formatDate(s.sampling_date)}`;
  }

  formatDate(d: string): string {
    if (!d || d.length !== 8) return d;
    return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`;
  }

  setEntry(runName: string, sampleId: string | null): void {
    this.registerEntries.update((entries) =>
      entries.map((e) => (e.run_name === runName ? { ...e, selectedSampleId: sampleId } : e)),
    );
  }

  registerAndContinue(): void {
    const selected = this.registerEntries().filter((e) => !!e.selectedSampleId);
    if (selected.length === 0) {
      this.step.set('launch');
      return;
    }
    this.submitting.set(true);
    const perRunSampleIds: Record<string, string> = {};
    for (const e of selected) {
      perRunSampleIds[e.run_name] = e.selectedSampleId!;
    }
    this.biomemeService
      .register(
        selected.map((e) => e.run_name),
        perRunSampleIds,
      )
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          this.newlyRegistered.set(result.created.length);
          this.submitting.set(false);
          this.step.set('launch');
          if (result.created.length > 0) {
            this.notify.action(`Registered ${result.created.length} run(s).`, 'OK', 3000);
          }
        },
        error: (err) => {
          this.submitting.set(false);
          this.errorMsg.set(err?.error?.detail ?? 'Registration failed.');
          this.step.set('error');
        },
      });
  }

  skipToLaunch(): void {
    this.step.set('launch');
  }

  launch(): void {
    this.launching.set(true);
    this.biomemeService
      .launch()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
      next: (result) => {
        this.launching.set(false);
        this.notify.action(`Biomeme pipeline queued (ID: ${result.id.slice(0, 8)}). Track in Pipeline Runs.`, 'OK', 5000);
        this.ref.close({
          registered: this.newlyRegistered() > 0,
          metadataEdited: this.metadataEdited(),
          launched: true,
          pipelineRunId: result.id,
        });
      },
      error: (err) => {
        this.launching.set(false);
        this.errorMsg.set(err?.error?.detail ?? 'Launch failed.');
        this.step.set('error');
      },
    });
  }

  goToEdit(): void {
    const registeredNames = new Set(
      this.activeFolders.flatMap((f) => f.files.filter((file) => file.registered).map((file) => file.run_name)),
    );
    this.biomemeService
      .list()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
      next: (runs) => {
        const entries = runs
          .filter((r) => registeredNames.has(r.biomeme_run_name))
          .map((r) => ({
            run_id: r.id,
            run_name: r.biomeme_run_name,
            selectedSampleId: r.sample_id ?? null,
            originalSampleId: r.sample_id ?? null,
          }));
        this.editEntries.set(entries);
        this.step.set('edit');
      },
      error: (err) => {
        this.errorMsg.set(err?.error?.detail ?? 'Failed to load biomeme runs.');
        this.step.set('error');
      },
    });
  }

  setEditEntry(runId: string, sampleId: string | null): void {
    this.editEntries.update((entries) =>
      entries.map((e) => (e.run_id === runId ? { ...e, selectedSampleId: sampleId } : e)),
    );
  }

  saveEdits(): void {
    const changed = this.editEntries().filter((e) => e.selectedSampleId !== e.originalSampleId);
    if (changed.length === 0) {
      this.step.set('launch');
      return;
    }
    this.savingEdits.set(true);
    forkJoin(
      // null (not undefined) so de-selecting a sample actually unlinks it — an
      // undefined value is dropped from the JSON and read as "leave unchanged".
      changed.map((e) => this.biomemeService.update(e.run_id, { sample_id: e.selectedSampleId ?? null })),
    )
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
      next: () => {
        this.savingEdits.set(false);
        this.metadataEdited.set(true);
        this.notify.action(`Updated ${changed.length} run(s).`, 'OK', 3000);
        this.step.set('launch');
      },
      error: (err) => {
        this.savingEdits.set(false);
        this.errorMsg.set(err?.error?.detail ?? 'Save failed.');
        this.step.set('error');
      },
    });
  }

  cancel(): void {
    this.ref.close(
      this.metadataEdited()
        ? { registered: false, metadataEdited: true, launched: false }
        : undefined,
    );
  }
}
