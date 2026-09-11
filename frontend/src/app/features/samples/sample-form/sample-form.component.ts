import { ChangeDetectionStrategy, Component, DestroyRef, OnInit, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { ReactiveFormsModule, FormBuilder, Validators } from '@angular/forms';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatSelectModule } from '@angular/material/select';
import { MatAutocompleteModule } from '@angular/material/autocomplete';

import { SamplesService } from '../../../core/services/samples.service';
import { NotificationService } from '../../../core/services/notification.service';
import { SitesService } from '../../../core/services/sites.service';
import { LookupValuesService } from '../../../core/services/lookup-values.service';
import { Sample, SampleCreate, SampleUpdate } from '../../../core/models/sample.model';
import { toCreatePayload, toUpdatePayload } from '../../../core/utils/form-payload';
import { saveDialogForm } from '../../../core/utils/dialog-save';
import { Site } from '../../../core/models/site.model';
import { LookupValue } from '../../../core/models/lookup-value.model';

function todayYYYYMMDD(): string {
  const d = new Date();
  return (
    d.getFullYear().toString() +
    String(d.getMonth() + 1).padStart(2, '0') +
    String(d.getDate()).padStart(2, '0')
  );
}

@Component({
  selector: 'app-sample-form',
  standalone: true,
  imports: [
    CommonModule,
    ReactiveFormsModule,
    MatDialogModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
    MatSelectModule,
    MatAutocompleteModule,
  ],
  templateUrl: './sample-form.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: [
    '.sample-form { display: flex; flex-direction: column; gap: 4px; min-width: 500px; padding-top: 8px; }',
  ],
})
export class SampleFormComponent implements OnInit {
  private readonly fb = inject(FormBuilder);
  private readonly samplesService = inject(SamplesService);
  private readonly sitesService = inject(SitesService);
  private readonly lookupValues = inject(LookupValuesService);
  private readonly notify = inject(NotificationService);
  readonly dialogRef = inject(MatDialogRef<SampleFormComponent>);
  readonly data: Sample | null = inject(MAT_DIALOG_DATA);
  private readonly destroyRef = inject(DestroyRef);

  readonly isEdit = !!this.data;
  readonly saving = signal(false);
  readonly sites = signal<Site[]>([]);
  readonly sampleTypes = signal<LookupValue[]>([]);
  private sitesById = new Map<string, string>();

  form = this.fb.group({
    site_id: [this.data?.site_id ?? (null as string | null)],
    sample_type: [this.data?.sample_type ?? (null as string | null)],
    sample_code: [{ value: this.data?.sample_code ?? '', disabled: true }], // auto-derived, read-only
    sampling_date: [
      this.data?.sampling_date ?? todayYYYYMMDD(),
      [Validators.required, Validators.pattern(/^\d{8}$/)],
    ],
    partner_sample_code: [this.data?.partner_sample_code ?? ''],
    depth: [this.data?.depth ?? ''],
    elevation: [this.data?.elevation ?? ''],
    nucleic_acid_concentration: [this.data?.nucleic_acid_concentration ?? ''],
    extract_volume: [this.data?.extract_volume ?? ''],
    elution_volume: [this.data?.elution_volume ?? ''],
    date_extraction: [this.data?.date_extraction ?? '', Validators.pattern(/^\d{8}$/)],
    comments_sampling: [this.data?.comments_sampling ?? ''],
    comments_extraction: [this.data?.comments_extraction ?? ''],
    comments: [this.data?.comments ?? ''],
  });

  ngOnInit() {
    this.sitesService.list().pipe(takeUntilDestroyed(this.destroyRef)).subscribe((ss) => {
      this.sites.set(ss);
      this.sitesById = new Map(ss.map((s) => [s.id, s.site_code]));
    });
    this.lookupValues.getList('sample_type').pipe(takeUntilDestroyed(this.destroyRef)).subscribe((lv) => {
      this.sampleTypes.set(lv);
    });
  }

  updateSampleCode() {
    const siteId = this.form.get('site_id')!.value;
    const sampleType = this.form.get('sample_type')!.value;
    if (siteId && sampleType) {
      const siteID = this.sitesById.get(siteId) ?? '';
      this.form.get('sample_code')!.setValue(`${siteID}_${sampleType}`, { emitEvent: false });
    }
  }

  save(): void {
    // Only the payload is sample-specific; the guard, in-flight flag, close-on-success and
    // failure handling live in saveDialogForm.
    const raw = { ...this.form.value, sample_code: this.form.get('sample_code')!.value };
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
          ? this.samplesService.update(
              this.data!.id,
              toUpdatePayload<SampleUpdate>(raw, { keepIfEmpty: ['site_id', 'sample_type'] }),
            )
          : this.samplesService.create(toCreatePayload<SampleCreate>(raw)),
    });
  }
}
