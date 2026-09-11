import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  inject,
  input,
  model,
  output,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { FormGroup, ReactiveFormsModule } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatAutocompleteModule } from '@angular/material/autocomplete';
import { MatExpansionModule } from '@angular/material/expansion';

import { LookupValuesService } from '../../../core/services/lookup-values.service';
import { LookupValue } from '../../../core/models/lookup-value.model';

/**
 * The launch wizard's "register a new site" panel: country/city autocompletes that fill in each
 * other's codes, the derived site-ID preview, and the Save site button.
 *
 * The wizard's own FormGroup passes in whole — this component reads and writes its site controls
 * (`country`, `country_code`, `city`, `city_code`, `site`, `site_code`, `location`, `latitude`,
 * `longitude`) but never submits anything itself: saving stays with the wizard, which also owns
 * what happens after (refreshing the site list and adopting the new site into barcode rows).
 */
@Component({
  selector: 'app-site-register-form',
  standalone: true,
  imports: [
    CommonModule,
    ReactiveFormsModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatAutocompleteModule,
    MatExpansionModule,
  ],
  templateUrl: './site-register-form.component.html',
  styleUrls: ['./site-register-form.component.css', './wizard-shared.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SiteRegisterFormComponent implements OnInit {
  /** The wizard's form; the site controls live on it. */
  readonly form = input.required<FormGroup>();
  /** True while the wizard's save-site request is in flight. */
  readonly saving = input(false);
  /** Whether the panel is open; the wizard collapses it after a successful save. */
  readonly expanded = model(false);
  /** The operator pressed Save site. */
  readonly save = output<void>();

  private readonly lookupValues = inject(LookupValuesService);
  private readonly destroyRef = inject(DestroyRef);

  private allCountryCodes: LookupValue[] = [];
  private allCityCodes: LookupValue[] = [];
  readonly filteredCountryCodes = signal<LookupValue[]>([]);
  readonly filteredCityCodes = signal<LookupValue[]>([]);

  private countryCodeAutoFilled = false;
  private cityCodeAutoFilled = false;

  ngOnInit(): void {
    this.lookupValues.countryCodes().pipe(takeUntilDestroyed(this.destroyRef)).subscribe((lv) => {
      this.allCountryCodes = lv;
      this.filteredCountryCodes.set(lv);
    });
    this.lookupValues.cityCodes().pipe(takeUntilDestroyed(this.destroyRef)).subscribe((lv) => {
      this.allCityCodes = lv;
      this.filteredCityCodes.set(lv);
    });

    this.form()
      .get('country_code')!
      .valueChanges.pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((v) => {
        this.filteredCountryCodes.set(this.filterLookup(this.allCountryCodes, v));
        this.countryCodeAutoFilled = false;
      });
    this.form()
      .get('city_code')!
      .valueChanges.pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((v) => {
        this.filteredCityCodes.set(this.filterLookup(this.allCityCodes, v));
        this.cityCodeAutoFilled = false;
      });
    this.form()
      .get('country')!
      .valueChanges.pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((v) => {
        // Filter country code suggestions by typed country name too
        this.filteredCountryCodes.set(this.filterLookupByDescription(this.allCountryCodes, v));
      });
    this.form()
      .get('city')!
      .valueChanges.pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((v) => {
        this.filteredCityCodes.set(this.filterLookupByDescription(this.allCityCodes, v));
      });
  }

  onCountryInput(): void {
    const country = (this.form().get('country')!.value ?? '').trim().toLowerCase();
    if (country.length < 3) return;
    const matches = this.allCountryCodes.filter((lv) =>
      (lv.description ?? '').toLowerCase().startsWith(country),
    );
    const currentCode = this.form().get('country_code')!.value;
    if (matches.length === 1 && (!currentCode || this.countryCodeAutoFilled)) {
      this.countryCodeAutoFilled = true;
      this.form().patchValue({ country_code: matches[0].code }, { emitEvent: false });
      this.updateSiteID();
    }
  }

  onCityInput(): void {
    const city = (this.form().get('city')!.value ?? '').trim().toLowerCase();
    if (city.length < 3) return;
    const matches = this.allCityCodes.filter((lv) =>
      (lv.description ?? '').toLowerCase().startsWith(city),
    );
    const currentCode = this.form().get('city_code')!.value;
    if (matches.length === 1 && (!currentCode || this.cityCodeAutoFilled)) {
      this.cityCodeAutoFilled = true;
      this.form().patchValue({ city_code: matches[0].code }, { emitEvent: false });
      this.updateSiteID();
    }
  }

  /** Called when user selects a country name from the autocomplete panel. */
  onCountryNameSelected(description: string): void {
    const match = this.allCountryCodes.find(
      (lv) => (lv.description ?? '').toLowerCase() === description.toLowerCase(),
    );
    if (match) {
      this.form().patchValue({ country_code: match.code }, { emitEvent: false });
      this.updateSiteID();
    }
  }

  /** Called when user selects a city name from the autocomplete panel. */
  onCityNameSelected(description: string): void {
    const match = this.allCityCodes.find(
      (lv) => (lv.description ?? '').toLowerCase() === description.toLowerCase(),
    );
    if (match) {
      this.form().patchValue({ city_code: match.code }, { emitEvent: false });
      this.updateSiteID();
    }
  }

  toUpper(field: string): void {
    const ctrl = this.form().get(field)!;
    const val: string = ctrl.value ?? '';
    const upper = val.toUpperCase();
    if (val !== upper) ctrl.setValue(upper, { emitEvent: false });
  }

  updateSiteID(): void {
    const cc = this.form().get('country_code')!.value ?? '';
    const cityCode = this.form().get('city_code')!.value ?? '';
    const site = this.form().get('site')!.value ?? '';
    const derived = `${cc}${cityCode}${site}`.toUpperCase();
    this.form().get('site_code')!.setValue(derived, { emitEvent: false });
  }

  private filterLookup(options: LookupValue[], value: string | null | undefined): LookupValue[] {
    if (!value) return options;
    const lc = value.toLowerCase();
    return options.filter(
      (o) => o.code.toLowerCase().includes(lc) || (o.description ?? '').toLowerCase().includes(lc),
    );
  }

  private filterLookupByDescription(
    options: LookupValue[],
    value: string | null | undefined,
  ): LookupValue[] {
    if (!value) return options;
    const lc = value.toLowerCase();
    return options.filter((o) => (o.description ?? '').toLowerCase().includes(lc));
  }
}
