import { ChangeDetectionStrategy, Component, DestroyRef, OnInit, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { ReactiveFormsModule, FormBuilder, Validators } from '@angular/forms';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatSelectModule } from '@angular/material/select';

import { BiomemeRunsService } from '../../../core/services/biomeme-runs.service';
import { NotificationService } from '../../../core/services/notification.service';
import { SamplesService } from '../../../core/services/samples.service';
import { BiomemeRun, BiomemeRunCreate, BiomemeRunUpdate } from '../../../core/models/biomeme-run.model';
import { toCreatePayload, toUpdatePayload } from '../../../core/utils/form-payload';
import { saveDialogForm } from '../../../core/utils/dialog-save';
import { Sample } from '../../../core/models/sample.model';

@Component({
  selector: 'app-biomeme-run-form',
  standalone: true,
  imports: [
    CommonModule,
    ReactiveFormsModule,
    MatDialogModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
    MatSelectModule,
    ],
  templateUrl: './biomeme-run-form.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: [
    '.biomeme-form { display: flex; flex-direction: column; gap: 4px; min-width: 440px; padding-top: 8px; }',
  ],
})
export class BiomemeRunFormComponent implements OnInit {
  private readonly fb = inject(FormBuilder);
  private readonly biomemeService = inject(BiomemeRunsService);
  private readonly samplesService = inject(SamplesService);
  private readonly notify = inject(NotificationService);
  readonly dialogRef = inject(MatDialogRef<BiomemeRunFormComponent>);
  readonly data: BiomemeRun | null = inject(MAT_DIALOG_DATA);
  private readonly destroyRef = inject(DestroyRef);

  readonly isEdit = !!this.data;
  readonly saving = signal(false);
  readonly samples = signal<Sample[]>([]);

  form = this.fb.group({
    biomeme_run_name: [this.data?.biomeme_run_name ?? '', Validators.required],
    sample_id: [this.data?.sample_id ?? (null as string | null)],
    sampling_date: [{ value: this.data?.sampling_date ?? '', disabled: true }],
    biomeme_sample_id: [this.data?.biomeme_sample_id ?? ''],
    dilution_factor: [this.data?.dilution_factor ?? (null as number | null)],
    comments: [this.data?.comments ?? ''],
  });

  ngOnInit() {
    this.samplesService.list().pipe(takeUntilDestroyed(this.destroyRef)).subscribe((s) => this.samples.set(s));
  }

  onSampleSelected(sampleId: string | null) {
    const sample = sampleId ? this.samples().find((s) => s.id === sampleId) : null;
    this.form.patchValue({ sampling_date: sample?.sampling_date ?? '' });
  }

  save(): void {
    const raw = this.form.getRawValue();
    saveDialogForm({
      form: this.form,
      saving: this.saving,
      dialogRef: this.dialogRef,
      notify: this.notify,
      destroyRef: this.destroyRef,
      // On update, cleared fields must be sent as null so the backend blanks them;
      // on create they are omitted so backend defaults apply. See core/utils/form-payload.
      request: () =>
        this.isEdit
          ? this.biomemeService.update(
              this.data!.id,
              toUpdatePayload<BiomemeRunUpdate>(raw, { keepIfEmpty: ['biomeme_run_name'] }),
            )
          : this.biomemeService.create(toCreatePayload<BiomemeRunCreate>(raw)),
    });
  }
}
