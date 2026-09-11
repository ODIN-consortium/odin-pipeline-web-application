import { ChangeDetectionStrategy, Component, DestroyRef, OnInit, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { ReactiveFormsModule, FormBuilder, Validators } from '@angular/forms';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatAutocompleteModule, MatAutocompleteSelectedEvent } from '@angular/material/autocomplete';

import { SitesService } from '../../../core/services/sites.service';
import { NotificationService } from '../../../core/services/notification.service';
import { LookupValuesService } from '../../../core/services/lookup-values.service';
import { Site, SiteCreate, SiteUpdate } from '../../../core/models/site.model';
import { toCreatePayload, toUpdatePayload } from '../../../core/utils/form-payload';
import { saveDialogForm } from '../../../core/utils/dialog-save';
import { LookupValue } from '../../../core/models/lookup-value.model';

@Component({
  selector: 'app-site-form',
  standalone: true,
  imports: [
    CommonModule,
    ReactiveFormsModule,
    MatDialogModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
    MatAutocompleteModule,
  ],
  templateUrl: './site-form.component.html',
  styles: [
    '.site-form { display: flex; flex-direction: column; gap: 4px; min-width: 460px; padding-top: 8px; }',
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SiteFormComponent implements OnInit {
  private readonly fb = inject(FormBuilder);
  private readonly sitesService = inject(SitesService);
  private readonly lookupValues = inject(LookupValuesService);
  private readonly notify = inject(NotificationService);
  readonly dialogRef = inject(MatDialogRef<SiteFormComponent>);
  readonly data: Site | null = inject(MAT_DIALOG_DATA);
  private readonly destroyRef = inject(DestroyRef);

  readonly isEdit = !!this.data;
  readonly saving = signal(false);

  private allCountryCodes: LookupValue[] = [];
  private allCityCodes: LookupValue[] = [];
  readonly filteredCountries = signal<LookupValue[]>([]);
  readonly filteredCountryCodes = signal<LookupValue[]>([]);
  readonly filteredCityCodes = signal<LookupValue[]>([]);

  private countryCodeAutoFilled = false;
  private cityCodeAutoFilled = false;

  form = this.fb.group({
    site_code: [{ value: this.data?.site_code ?? '', disabled: true }],
    site: [this.data?.site ?? ''],
    country: [this.data?.country ?? '', Validators.required],
    country_code: [this.data?.country_code ?? ''],
    city_code: [this.data?.city_code ?? ''],
    city: [this.data?.city ?? ''],
    location: [this.data?.location ?? ''],
    longitude: [this.data?.longitude ?? null],
    latitude: [this.data?.latitude ?? null],
    comments: [this.data?.comments ?? ''],
  });

  ngOnInit() {
    // These lists used to be plain fields, which needed a cdr.detectChanges() after each
    // assignment: countryCodes() is shareReplay(1) and can replay synchronously inside
    // ngOnInit, mid-CD, tripping dev-mode NG0100. A signal write schedules its own
    // re-render, so the workaround went with the mutation.
    this.lookupValues.countryCodes().pipe(takeUntilDestroyed(this.destroyRef)).subscribe((lv) => {
      this.allCountryCodes = lv;
      this.filteredCountries.set(lv);
      this.filteredCountryCodes.set(lv);
    });
    this.lookupValues.cityCodes().pipe(takeUntilDestroyed(this.destroyRef)).subscribe((lv) => {
      this.allCityCodes = lv;
      this.filteredCityCodes.set(lv);
    });

    this.form.get('country_code')!.valueChanges.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((v) => {
      this.filteredCountryCodes.set(this.filterLookup(this.allCountryCodes, v));
      // If the user edits the code field directly, stop treating it as auto-filled
      this.countryCodeAutoFilled = false;
    });
    this.form.get('city_code')!.valueChanges.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((v) => {
      this.filteredCityCodes.set(this.filterLookup(this.allCityCodes, v));
      this.cityCodeAutoFilled = false;
    });
  }

  onCountryInput() {
    const country = (this.form.get('country')!.value ?? '').trim().toLowerCase();
    const matches = !country
      ? this.allCountryCodes
      : this.allCountryCodes.filter((lv) =>
          (lv.description ?? '').toLowerCase().startsWith(country),
        );
    this.filteredCountries.set(matches);
    if (country.length < 3) return;
    const currentCode = this.form.get('country_code')!.value;
    if (matches.length === 1 && (!currentCode || this.countryCodeAutoFilled)) {
      this.countryCodeAutoFilled = true;
      this.form.patchValue({ country_code: matches[0].code }, { emitEvent: false });
      this.updateSiteID();
    }
  }

  onCountrySelected(event: MatAutocompleteSelectedEvent) {
    const selected = this.allCountryCodes.find((lv) => lv.description === event.option.value);
    if (selected) {
      this.countryCodeAutoFilled = true;
      this.form.patchValue({ country_code: selected.code }, { emitEvent: false });
      this.updateSiteID();
    }
  }

  onCityInput() {
    const city = (this.form.get('city')!.value ?? '').trim().toLowerCase();
    if (city.length < 3) return;
    const matches = this.allCityCodes.filter((lv) =>
      (lv.description ?? '').toLowerCase().startsWith(city),
    );
    const currentCode = this.form.get('city_code')!.value;
    if (matches.length === 1 && (!currentCode || this.cityCodeAutoFilled)) {
      this.cityCodeAutoFilled = true;
      this.form.patchValue({ city_code: matches[0].code }, { emitEvent: false });
      this.updateSiteID();
    }
  }

  toUpper(field: string) {
    const ctrl = this.form.get(field)!;
    const val: string = ctrl.value ?? '';
    const upper = val.toUpperCase();
    if (val !== upper) ctrl.setValue(upper, { emitEvent: false });
  }

  updateSiteID() {
    const cc = this.form.get('country_code')!.value ?? '';
    const cityCode = this.form.get('city_code')!.value ?? '';
    const site = this.form.get('site')!.value ?? '';
    const derived = `${cc}${cityCode}${site}`;
    this.form.get('site_code')!.setValue(derived, { emitEvent: false });
  }

  private filterLookup(options: LookupValue[], value: string | null | undefined): LookupValue[] {
    if (!value) return options;
    const lc = value.toLowerCase();
    return options.filter(
      (o) => o.code.toLowerCase().includes(lc) || (o.description ?? '').toLowerCase().includes(lc),
    );
  }

  save(): void {
    // On update, cleared fields must be sent as null so the backend blanks them
    // (sending '' would store an empty string); on create they are omitted.
    const raw = this.form.value as Record<string, unknown>;
    saveDialogForm({
      form: this.form,
      saving: this.saving,
      dialogRef: this.dialogRef,
      notify: this.notify,
      destroyRef: this.destroyRef,
      request: () =>
        this.isEdit
          ? this.sitesService.update(
              this.data!.id,
              toUpdatePayload<SiteUpdate>(raw, {
                keepIfEmpty: ['country', 'country_code', 'city_code', 'site'],
              }),
            )
          : this.sitesService.create(toCreatePayload<SiteCreate>(raw)),
    });
  }
}
