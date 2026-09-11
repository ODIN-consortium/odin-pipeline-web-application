import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { of } from 'rxjs';

import { SitesService } from '../../../core/services/sites.service';
import { LookupValuesService } from '../../../core/services/lookup-values.service';
import { NotificationService } from '../../../core/services/notification.service';
import { Site } from '../../../core/models/site.model';
import { LookupValue } from '../../../core/models/lookup-value.model';
import { SiteFormComponent } from './site-form.component';

/**
 * Characterization spec for the site form, written before its §F1 conversion (signals + OnPush,
 * deleting the NG0100 detectChanges workaround). Everything is driven through the DOM so the
 * template wiring — the autofill chain in particular — is what is pinned.
 */

const COUNTRIES: LookupValue[] = [
  { list: 'country_code', code: 'NO', description: 'Norway' },
  { list: 'country_code', code: 'NG', description: 'Nigeria' },
] as LookupValue[];

const CITIES: LookupValue[] = [
  { list: 'city_code', code: 'BGO', description: 'Bergen' },
  { list: 'city_code', code: 'OSL', description: 'Oslo' },
] as LookupValue[];

const EXISTING_SITE = {
  id: 'site-1',
  site_code: 'NOBGO01',
  site: '01',
  country: 'Norway',
  country_code: 'NO',
  city: 'Bergen',
  city_code: 'BGO',
  location: 'Harbour',
  latitude: 60.39,
  longitude: 5.32,
  comments: 'old comment',
} as unknown as Site;

describe('SiteFormComponent', () => {
  let fixture: ComponentFixture<SiteFormComponent>;
  let component: SiteFormComponent;
  let create: jest.Mock;
  let update: jest.Mock;

  function setup(data: Site | null = null) {
    create = jest.fn().mockReturnValue(of(EXISTING_SITE));
    update = jest.fn().mockReturnValue(of(EXISTING_SITE));

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [SiteFormComponent, NoopAnimationsModule],
      providers: [
        { provide: SitesService, useValue: { create, update } },
        {
          provide: LookupValuesService,
          useValue: { countryCodes: () => of(COUNTRIES), cityCodes: () => of(CITIES) },
        },
        { provide: NotificationService, useValue: { error: jest.fn() } },
        { provide: MatDialogRef, useValue: { close: jest.fn() } },
        { provide: MAT_DIALOG_DATA, useValue: data },
      ],
    });
    fixture = TestBed.createComponent(SiteFormComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  }

  function input(controlName: string): HTMLInputElement {
    return fixture.nativeElement.querySelector(
      `input[formcontrolname="${controlName}"], textarea[formcontrolname="${controlName}"]`,
    ) as HTMLInputElement;
  }

  function type(controlName: string, value: string): void {
    const field = input(controlName);
    field.value = value;
    field.dispatchEvent(new Event('input'));
    fixture.detectChanges();
  }

  it('renders the country suggestions once the lookups arrive', () => {
    setup();

    input('country').dispatchEvent(new Event('focusin'));
    fixture.detectChanges();

    const options = Array.from(document.querySelectorAll('mat-option')).map((o) =>
      o.textContent?.trim(),
    );
    expect(options).toEqual(['Norway', 'Nigeria']);
  });

  it('autofills the country code once the typed name matches exactly one country', () => {
    setup();

    type('country', 'nor'); // Norway, not Nigeria

    expect(component.form.get('country_code')!.value).toBe('NO');
    expect(component.form.get('site_code')!.value).toBe('NO');
  });

  it('does not overwrite a hand-picked country code while the name is typed', () => {
    setup();

    type('country_code', 'SE');
    type('country', 'norway');

    expect(component.form.get('country_code')!.value).toBe('SE');
  });

  it('derives the site ID from country code, city code and site letter, uppercasing input', () => {
    setup();

    type('country_code', 'no');
    type('city_code', 'bgo');
    type('site', 'x1');

    expect(component.form.get('site_code')!.value).toBe('NOBGOX1');
    expect(input('site_code').value).toBe('NOBGOX1');
  });

  it('creates a site from the filled fields, omitting the empty ones', () => {
    setup();
    type('country', 'Norway');
    type('country_code', 'NO');

    component.save();

    expect(create).toHaveBeenCalledTimes(1);
    expect(create.mock.calls[0][0]).toMatchObject({ country: 'Norway', country_code: 'NO' });
    expect(create.mock.calls[0][0]).not.toHaveProperty('comments');
  });

  it('updates an existing site, clearing an emptied optional field with null', () => {
    setup(EXISTING_SITE);
    type('comments', '');

    component.save();

    expect(update).toHaveBeenCalledTimes(1);
    expect(update.mock.calls[0][0]).toBe('site-1');
    expect(update.mock.calls[0][1].comments).toBeNull();
  });

  it('never clears the derivation components on update, even when emptied', () => {
    // toUpdatePayload({keepIfEmpty}) — the UI-layer stopgap for §J: site_code derives from
    // these, so an emptied control means "leave unchanged", not "clear".
    setup(EXISTING_SITE);
    type('country_code', '');

    component.save();

    expect(update.mock.calls[0][1]).not.toHaveProperty('country_code');
  });

  it('refuses to save without a country', () => {
    setup();

    component.save();

    expect(create).not.toHaveBeenCalled();
  });
});
