import { ChangeDetectionStrategy, Component, DestroyRef, inject, OnInit, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Subject, EMPTY } from 'rxjs';
import { catchError, switchMap } from 'rxjs/operators';
import { CommonModule } from '@angular/common';
import { ReactiveFormsModule, FormBuilder, Validators } from '@angular/forms';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule, MatSuffix } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatSelectModule } from '@angular/material/select';
import { MatAutocompleteModule } from '@angular/material/autocomplete';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';

import { RunsService } from '../../../core/services/runs.service';
import { NotificationService } from '../../../core/services/notification.service';
import { SamplesService } from '../../../core/services/samples.service';
import { LookupValuesService } from '../../../core/services/lookup-values.service';
import { DiscoveryService } from '../../../core/services/discovery.service';
import { NanoporeRunAccessionsService } from '../../../core/services/nanopore-run-accessions.service';
import { NanoporeRun, NanoporeRunAccession, NanoporeRunCreate, NanoporeRunUpdate } from '../../../core/models/run.model';
import { toCreatePayload, toUpdatePayload } from '../../../core/utils/form-payload';
import { Sample } from '../../../core/models/sample.model';
import { LookupValue } from '../../../core/models/lookup-value.model';
import { MinknowRunInfo } from '../../../core/models/minknow-run-info.model';

/** The slice of a run group the form needs to add a barcode to it. */
export interface RunGroupRef {
  accession_id: string;
  run_accession: string | null;
  label: string | null;
  protocol_id?: string;
  sequencing_kit_id?: string;
}

/**
 * What the dialog is opened for. The three modes used to be sniffed out of a
 * `NanoporeRun | null` by which fields happened to be set — an `id` meant edit, an
 * `accession_id` without one meant add-to-group — which forced the caller to forge a partial
 * run with an `as unknown as` cast. Naming the mode makes the contract checkable.
 */
export type RunFormData =
  | { mode: 'create' }
  | { mode: 'add-to-group'; group: RunGroupRef }
  | { mode: 'edit'; run: NanoporeRun };

@Component({
  selector: 'app-run-form',
  standalone: true,
  imports: [
    CommonModule,
    ReactiveFormsModule,
    MatDialogModule,
    MatFormFieldModule,
    MatSuffix,
    MatInputModule,
    MatButtonModule,
    MatButtonToggleModule,
    MatSelectModule,
    MatAutocompleteModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
  ],
  templateUrl: './run-form.component.html',
  styles: [
    '.run-form { display: flex; flex-direction: column; gap: 4px; min-width: 460px; padding-top: 8px; }',
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RunFormComponent implements OnInit {
  private readonly fb = inject(FormBuilder);
  private readonly runsService = inject(RunsService);
  private readonly samplesService = inject(SamplesService);
  private readonly lookupValues = inject(LookupValuesService);
  private readonly discoveryService = inject(DiscoveryService);
  private readonly nraService = inject(NanoporeRunAccessionsService);
  private readonly notify = inject(NotificationService);
  readonly dialogRef = inject(MatDialogRef<RunFormComponent>);
  readonly data: RunFormData = inject(MAT_DIALOG_DATA);
  private readonly destroyRef = inject(DestroyRef);
  private readonly runAccessionInput$ = new Subject<string>();

  readonly isEdit = this.data.mode === 'edit';
  readonly isAddToGroup = this.data.mode === 'add-to-group';
  private readonly editRun = this.data.mode === 'edit' ? this.data.run : null;
  /** What the form's controls start from: the run being edited, or the group being added to. */
  private readonly initial: Partial<NanoporeRun> =
    this.data.mode === 'edit' ? this.data.run : this.data.mode === 'add-to-group' ? this.data.group : {};
  /** Shown in the add-to-group banner. */
  readonly groupName = this.initial.run_accession ?? this.initial.label ?? '';

  readonly saving = signal(false);
  readonly samples = signal<Sample[]>([]);
  readonly accessions = signal<NanoporeRunAccession[]>([]);
  readonly protocols = signal<LookupValue[]>([]);
  readonly kits = signal<LookupValue[]>([]);
  readonly mpoxTypes = signal<LookupValue[]>([]);
  private selectedSampleCode = this.editRun?.sample_code ?? '';

  readonly runAccessionMode = signal<'known' | 'pending'>('known');
  readonly runInfo = signal<MinknowRunInfo | null>(null);
  readonly runInfoLoading = signal(false);
  readonly barcodeIsNoise = signal(false);

  form = this.fb.group({
    accession_id: [this.initial.accession_id ?? (null as string | null), this.isEdit ? Validators.required : []],
    run_accession: [this.initial.run_accession ?? '', (this.isAddToGroup || this.isEdit) ? [] : Validators.required],
    label: [null as string | null],
    sample_id: [this.editRun?.sample_id ?? (null as string | null)],
    sampling_date: [{ value: this.editRun?.sampling_date ?? '', disabled: true }],
    barcode: [this.editRun?.barcode ?? '', Validators.required],
    minknow_sample_id: [{ value: this.editRun?.minknow_sample_id ?? '', disabled: true }],
    alias: [{ value: this.editRun?.alias ?? '', disabled: true }],
    protocol_id: [this.initial.protocol_id ?? (null as string | null)],
    sequencing_kit_id: [this.initial.sequencing_kit_id ?? (null as string | null)],
    type: [this.editRun?.type ?? (null as string | null)],
    runName: [this.editRun?.runName ?? ''],
    sampleName: [this.editRun?.sampleName ?? ''],
    comments: [this.editRun?.comments ?? ''],
  });

  toggleMode(mode: 'known' | 'pending') {
    this.runAccessionMode.set(mode);
    const ra = this.form.get('run_accession')!;
    const lb = this.form.get('label')!;
    if (mode === 'known') {
      ra.setValidators(Validators.required);
      lb.clearValidators();
    } else {
      lb.setValidators(Validators.required);
      ra.clearValidators();
    }
    ra.updateValueAndValidity();
    lb.updateValueAndValidity();
    this.runInfo.set(null);
  }

  ngOnInit() {
    this.samplesService.list().pipe(takeUntilDestroyed(this.destroyRef)).subscribe((s) => {
      this.samples.set(s);
      // If editing, restore derived fields using the already-loaded sample list
      if (this.editRun?.sample_id) {
        const sample = s.find((x) => x.id === this.editRun!.sample_id);
        if (sample) this.selectedSampleCode = sample.sample_code;
      }
      this.updateDerived();
    });
    this.lookupValues.getList('protocol_id').pipe(takeUntilDestroyed(this.destroyRef)).subscribe((lv) => this.protocols.set(lv));
    this.lookupValues.getList('sequencing_kit_id').pipe(takeUntilDestroyed(this.destroyRef)).subscribe((lv) => this.kits.set(lv));
    this.lookupValues.getList('mpox_type').pipe(takeUntilDestroyed(this.destroyRef)).subscribe((lv) => this.mpoxTypes.set(lv));
    if (this.isEdit) {
      this.nraService.list().pipe(takeUntilDestroyed(this.destroyRef)).subscribe((a) => this.accessions.set(a));
      // Selecting a different group re-describes the run-level fields as that group's own
      // values. Without this, a pure move sent the OLD group's prefilled name/protocol/kit
      // along with the new accession_id — and the backend applies run-level fields to the
      // group the barcode moves INTO, so the move overwrote the target group's metadata.
      // Adopting the target's values means an unedited move sends exactly what is already
      // stored (the backend's diff filter then writes nothing), and an edit made after
      // picking the group applies to the group it visibly describes.
      this.form
        .get('accession_id')!
        .valueChanges.pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe((id) => this.adoptGroupMetadata(id));
    }
    this.runAccessionInput$.pipe(
      switchMap((accession) => {
        this.runInfoLoading.set(true);
        return this.discoveryService.getRunInfo(accession).pipe(
          catchError(() => {
            this.runInfoLoading.set(false);
            return EMPTY;
          }),
        );
      }),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe({
      next: (info) => {
        this.runInfoLoading.set(false);
        this.runInfo.set(info);
        if (info.run_name && !this.form.get('runName')!.value) {
          this.form.patchValue({ runName: info.run_name });
        }
        if (info.sample_name && !this.form.get('sampleName')!.value) {
          this.form.patchValue({ sampleName: info.sample_name });
        }
        if (info.sequencing_kit_id && !this.form.get('sequencing_kit_id')!.value) {
          this.form.patchValue({ sequencing_kit_id: info.sequencing_kit_id });
          this.updateDerived();
        }
      },
    });
  }

  private adoptGroupMetadata(accessionId: string | null): void {
    const group = this.accessions().find((a) => a.id === accessionId);
    if (!group) return;
    this.form.patchValue({
      protocol_id: group.protocol_id ?? null,
      sequencing_kit_id: group.sequencing_kit_id ?? null,
      runName: group.runName ?? '',
      sampleName: group.sampleName ?? '',
      comments: group.comments ?? '',
    });
    this.updateDerived();
  }

  onSampleSelected(sampleId: string | null) {
    if (sampleId) {
      const sample = this.samples().find((s) => s.id === sampleId);
      if (sample) {
        this.selectedSampleCode = sample.sample_code;
        this.form.patchValue({ sampling_date: sample.sampling_date });
      }
    } else {
      this.selectedSampleCode = '';
    }
    this.updateDerived();
  }

  updateDerived() {
    const barcode = this.form.get('barcode')!.value ?? '';
    const protocol = this.form.get('protocol_id')!.value ?? '';
    const kit = this.form.get('sequencing_kit_id')!.value ?? '';
    const sc = this.selectedSampleCode;

    const alias = sc && barcode ? `${sc}_${barcode}` : '';
    const minknowSampleId = sc && protocol && kit ? `${sc}_${protocol}_${kit}` : '';

    this.form.get('alias')!.setValue(alias, { emitEvent: false });
    this.form.get('minknow_sample_id')!.setValue(minknowSampleId, { emitEvent: false });
  }

  onRunAccessionBlur(): void {
    const accession = (this.form.get('run_accession')!.value ?? '').trim();
    if (!accession) return;
    this.runAccessionInput$.next(accession);
  }

  checkBarcodeNoise(): void {
    const info = this.runInfo();
    if (!info) return;
    const barcode = (this.form.get('barcode')!.value ?? '').trim();
    const bc = info.barcodes.find((b) => b.barcode === barcode);
    this.barcodeIsNoise.set(!!bc && !bc.is_used && bc.read_count > 0);
  }

  save() {
    if (this.form.invalid) return;
    this.saving.set(true);
    const raw = this.form.getRawValue();
    // The fields the API accepts from this form on both create and update. The derived
    // read-only controls (sampling_date, alias, minknow_sample_id) are display-only and
    // never leave the client — the backend computes them itself.
    const fields = {
      sample_id: raw.sample_id,
      barcode: raw.barcode,
      protocol_id: raw.protocol_id,
      sequencing_kit_id: raw.sequencing_kit_id,
      type: raw.type,
      runName: raw.runName,
      sampleName: raw.sampleName,
      comments: raw.comments,
    };
    const op =
      this.data.mode === 'edit'
        ? this.runsService.update(
            this.data.run.id,
            // accession_id rides along: it is the group-reassignment field.
            toUpdatePayload<NanoporeRunUpdate>({ accession_id: raw.accession_id, ...fields }),
          )
        : this.runsService.create(
            toCreatePayload<NanoporeRunCreate>({ ...this.groupRef(raw.run_accession, raw.label), ...fields }),
          );
    op.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        this.dialogRef.close(true);
      },
      error: (err) => {
        this.saving.set(false);
        this.notify.error(err, 'Save failed');
      },
    });
  }

  /** Exactly ONE of accession_id / run_accession / label — the create contract. */
  private groupRef(
    runAccession: string | null,
    label: string | null,
  ): Pick<NanoporeRunCreate, 'accession_id' | 'run_accession' | 'label'> {
    if (this.data.mode === 'add-to-group') return { accession_id: this.data.group.accession_id };
    return this.runAccessionMode() === 'known'
      ? { run_accession: runAccession ?? undefined }
      : { label: label ?? undefined };
  }
}
