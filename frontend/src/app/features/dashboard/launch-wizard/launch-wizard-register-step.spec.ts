import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MAT_DIALOG_DATA, MatDialog, MatDialogRef } from '@angular/material/dialog';
import { of } from 'rxjs';

import { DiscoveryService } from '../../../core/services/discovery.service';
import { PipelineService } from '../../../core/services/pipeline.service';
import { SitesService } from '../../../core/services/sites.service';
import { LookupValuesService } from '../../../core/services/lookup-values.service';
import { NotificationService } from '../../../core/services/notification.service';
import { WarningDismissalService } from '../../../core/services/warning-dismissal.service';
import { NanoporeReadiness } from '../../../core/models/discovery.model';
import { Site } from '../../../core/models/site.model';
import { LookupValue } from '../../../core/models/lookup-value.model';
import { LaunchWizardComponent } from './launch-wizard.component';

/**
 * Characterization spec for the register step's template, written before D9 splits it into
 * `SiteRegisterFormComponent` and `BarcodeMetadataTableComponent`.
 *
 * Everything here is exercised through the DOM — typed into inputs, clicked on buttons — because
 * the template wiring is exactly what the extraction moves. These tests must pass unchanged on
 * both sides of the split.
 */

const COUNTRIES: LookupValue[] = [
  { list: 'country_code', code: 'NO', description: 'Norway' },
  { list: 'country_code', code: 'NG', description: 'Nigeria' },
] as LookupValue[];

const CITIES: LookupValue[] = [
  { list: 'city_code', code: 'BGO', description: 'Bergen' },
  { list: 'city_code', code: 'OSL', description: 'Oslo' },
] as LookupValue[];

const LISTS: Record<string, Partial<LookupValue>[]> = {
  protocol_id: [{ code: 'P2', description: 'Protocol 2' }],
  sequencing_kit_id: [{ code: 'RB_GEN', description: 'Rapid barcoding' }],
  sample_type: [
    { code: 'water', description: 'Water' },
    { code: 'sediment', description: 'Sediment' },
  ],
  mpox_type: [{ code: 'clinical', description: 'Clinical' }],
};

const SITES: Site[] = [{ id: 'site-1', site_code: 'NOOSL01' } as Site];

/** barcode01 arrives complete from existing metadata; barcode02 is likely noise (400 reads). */
const READINESS = {
  run_accession: 'ERR1',
  barcodes_on_disk: ['barcode01', 'barcode02', 'barcode03'],
  barcodes: [
    {
      barcode: 'barcode01',
      read_count: 52341,
      site_id: 'site-1',
      sample_type: 'water',
      sampling_date: '20240601',
      type: null,
    },
    { barcode: 'barcode02', read_count: 400 },
  ],
  existing_protocol_id: 'P2',
  existing_sequencing_kit_id: 'RB_GEN',
  existing_site_id: null,
  missing_site: false,
  related_run_accessions: [],
  continuation_run_accessions: [],
  metadata_warnings: [],
  merge_decision_needed: false,
  auto_merge: null,
  action: 'register_launch',
  status: 'partial',
} as unknown as NanoporeReadiness;

/** No prefilled rows and no read counts: every row starts empty and visible. */
const READINESS_BLANK = {
  ...READINESS,
  barcodes: [],
} as unknown as NanoporeReadiness;

describe('LaunchWizardComponent — register step template', () => {
  let fixture: ComponentFixture<LaunchWizardComponent>;
  let component: LaunchWizardComponent;
  let nanoporeRegister: jest.Mock;
  let sitesList: jest.Mock;
  let notifySuccess: jest.Mock;

  function setup(readiness: NanoporeReadiness = READINESS) {
    nanoporeRegister = jest.fn().mockReturnValue(of(readiness));
    sitesList = jest.fn().mockReturnValue(of(SITES));
    notifySuccess = jest.fn();

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [LaunchWizardComponent, NoopAnimationsModule],
      providers: [
        {
          provide: DiscoveryService,
          useValue: { nanoporeReadiness: () => of(readiness), nanoporeRegister },
        },
        {
          provide: PipelineService,
          useValue: {
            getMpoxOptions: () => of({ clades: [], schemes: [] }),
            getExtractTargets: () => of({ targets: [] }),
          },
        },
        { provide: SitesService, useValue: { list: sitesList } },
        {
          provide: LookupValuesService,
          useValue: {
            countryCodes: () => of(COUNTRIES),
            cityCodes: () => of(CITIES),
            getList: (name: string) => of(LISTS[name] ?? []),
          },
        },
        {
          provide: NotificationService,
          useValue: { success: notifySuccess, error: jest.fn() },
        },
        { provide: WarningDismissalService, useValue: new WarningDismissalService() },
        { provide: MatDialogRef, useValue: { close: jest.fn() } },
        {
          provide: MAT_DIALOG_DATA,
          useValue: { run_accession: 'ERR1', artic_on_disk: false, on_disk: true },
        },
      ],
    });
    TestBed.overrideProvider(MatDialog, { useValue: { open: jest.fn() } });
    fixture = TestBed.createComponent(LaunchWizardComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  }

  const el = (): HTMLElement => fixture.nativeElement;

  function expandSitePanel(): void {
    (el().querySelector('mat-expansion-panel-header') as HTMLElement).click();
    fixture.detectChanges();
  }

  function input(controlName: string): HTMLInputElement {
    const found = el().querySelector(`input[formcontrolname="${controlName}"]`);
    if (!found) throw new Error(`no input for ${controlName}`);
    return found as HTMLInputElement;
  }

  function type(controlName: string, value: string): void {
    const field = input(controlName);
    field.value = value;
    field.dispatchEvent(new Event('input'));
    fixture.detectChanges();
  }

  function buttonByText(text: string): HTMLButtonElement {
    const all = Array.from(el().querySelectorAll('button'));
    const found = all.find((b) => (b.textContent ?? '').includes(text));
    if (!found) throw new Error(`no button containing "${text}"`);
    return found as HTMLButtonElement;
  }

  function visibleBarcodeRows(): HTMLTableRowElement[] {
    return Array.from(el().querySelectorAll('.barcode-form-table tbody tr'));
  }

  // ── the site panel ─────────────────────────────────────────────────────────

  it('derives the site ID preview from country code, city code and site letter, uppercased', () => {
    setup();
    expandSitePanel();

    type('country_code', 'no');
    type('city_code', 'bgo');
    type('site', '01');

    expect(component.form.get('country_code')!.value).toBe('NO');
    expect(component.form.get('city_code')!.value).toBe('BGO');
    expect(component.form.get('site_code')!.value).toBe('NOBGO01');
    expect(input('site_code').value).toBe('NOBGO01');
  });

  it('autofills the country code once the typed name matches exactly one country', () => {
    setup();
    expandSitePanel();

    type('country', 'nor'); // Norway, not Nigeria

    expect(component.form.get('country_code')!.value).toBe('NO');
    expect(component.form.get('site_code')!.value).toBe('NO');
  });

  it('does not overwrite a hand-picked country code while the name is typed', () => {
    setup();
    expandSitePanel();

    type('country_code', 'SE'); // hand-picked: not auto-filled
    type('country', 'norway');

    expect(component.form.get('country_code')!.value).toBe('SE');
  });

  it('keeps Save site disabled until a country is entered', () => {
    setup();
    expandSitePanel();

    expect(buttonByText('Save site').disabled).toBe(true);
    type('country', 'Norway');
    expect(buttonByText('Save site').disabled).toBe(false);
  });

  it('saves the site alone, adopts it in empty rows, and clears the panel', () => {
    setup(READINESS_BLANK);
    expandSitePanel();

    type('country', 'Norway');
    type('city_code', 'bgo');
    type('site', '01');
    // The refreshed list now carries the site the save created.
    const created = { id: 'site-9', site_code: 'NOBGO01' } as Site;
    sitesList.mockReturnValue(of([...SITES, created]));

    buttonByText('Save site').click();
    fixture.detectChanges();

    // Registered with no barcodes: this button saves the site only.
    expect(nanoporeRegister).toHaveBeenCalledWith(
      'ERR1',
      expect.objectContaining({ country: 'Norway', barcodes: [] }),
    );
    // Every row without a site adopts the new one.
    expect(component.barcodesArray.at(0).value.site_id).toBe('site-9');
    expect(component.barcodesArray.at(2).value.site_id).toBe('site-9');
    // The panel collapses and its fields reset for the next site.
    expect(component.newSiteExpanded()).toBe(false);
    expect(component.form.get('country')!.value).toBe('');
    expect(notifySuccess).toHaveBeenCalledWith('Site saved.');
  });

  // ── the barcode table ──────────────────────────────────────────────────────

  it('renders a row per barcode on disk, hiding likely noise by default', () => {
    setup();

    const rows = visibleBarcodeRows().map((r) => r.textContent ?? '');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toContain('barcode01');
    expect(rows[1]).toContain('barcode03');
    expect(el().textContent).toContain('Show noise barcodes (1)');
  });

  it('reveals the noise rows when toggled, with the read count struck through', () => {
    setup();

    const toggle = el().querySelector('mat-slide-toggle button') as HTMLButtonElement;
    toggle.click();
    fixture.detectChanges();

    const rows = visibleBarcodeRows();
    expect(rows).toHaveLength(3);
    const noisy = rows.find((r) => (r.textContent ?? '').includes('barcode02'))!;
    expect(noisy.querySelector('s')?.textContent).toContain('400');
  });

  it('prefills rows from existing metadata and marks them ready', () => {
    setup();

    expect(component.barcodesArray.at(0).value).toMatchObject({
      barcode: 'barcode01',
      site_id: 'site-1',
      sample_type: 'water',
      sampling_date: '20240601',
    });
    const rows = visibleBarcodeRows();
    expect(rows[0].textContent).toContain('check_circle');
    expect(rows[1].textContent).toContain('radio_button_unchecked');
  });

  it('copies a value into a row from the row above', () => {
    setup(READINESS_BLANK);

    component.barcodesArray.at(0).patchValue({ sampling_date: '20240601' });
    fixture.detectChanges();

    // Row 1's date cell: barcode / reads / site / sample type / date
    const dateCell = visibleBarcodeRows()[1].cells[4];
    (dateCell.querySelector('button.copy-btn') as HTMLButtonElement).click();
    fixture.detectChanges();

    expect(component.barcodesArray.at(1).value.sampling_date).toBe('20240601');
    expect((dateCell.querySelector('input') as HTMLInputElement).value).toBe('20240601');
  });

  it('shows the at-least-one-complete error until a row is complete', () => {
    setup(READINESS_BLANK);

    expect(el().textContent).toContain('At least one barcode must have a site, sample type, and date');

    component.barcodesArray
      .at(0)
      .patchValue({ site_id: 'site-1', sample_type: 'water', sampling_date: '20240601' });
    fixture.detectChanges();

    expect(el().textContent).not.toContain('At least one barcode must have a site');
  });
});
