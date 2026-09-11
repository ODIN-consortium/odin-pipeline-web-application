import { TestBed } from '@angular/core/testing';
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
import {
  BarcodeRowValue,
  LaunchWizardComponent,
  isBarcodeRowComplete,
} from './launch-wizard.component';

/**
 * Characterization spec for the launch wizard's registration payload, written before D9 de-dupes
 * it.
 *
 * `register()` and `saveAndClose()` build the same ~30-line payload independently — the same field
 * mapping, the same fallbacks, the same filter over barcode rows — and differ only in what they do
 * with the response. Two copies of the rule deciding which barcode rows are complete enough to
 * send, in two different shapes, plus two more of the same rule over form controls.
 *
 * These tests pin what actually goes to the API from each entry point, so the shared builder can
 * be shown to send exactly what the two copies sent.
 */

const READINESS = {
  run_accession: 'ERR1',
  barcodes_on_disk: ['barcode01', 'barcode02', 'barcode03'],
  barcodes: [],
  existing_protocol_id: 'P2',
  existing_sequencing_kit_id: 'RB_GEN',
  existing_site_id: null,
  related_run_accessions: [],
  continuation_run_accessions: [],
  metadata_warnings: [],
  merge_decision_needed: false,
  auto_merge: null,
  action: 'register_launch',
  status: 'partial',
} as unknown as NanoporeReadiness;

describe('LaunchWizardComponent — registration payload', () => {
  let nanoporeRegister: jest.Mock;
  let close: jest.Mock;

  function setup(readiness: NanoporeReadiness = READINESS) {
    nanoporeRegister = jest.fn().mockReturnValue(of(readiness));
    close = jest.fn();

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [LaunchWizardComponent, NoopAnimationsModule],
      providers: [
        {
          provide: DiscoveryService,
          useValue: {
            nanoporeReadiness: () => of(readiness),
            nanoporeRegister,
          },
        },
        {
          provide: PipelineService,
          useValue: {
            getMpoxOptions: () => of({ clades: [], schemes: [] }),
            getExtractTargets: () => of({ targets: [] }),
          },
        },
        { provide: SitesService, useValue: { list: () => of([]) } },
        {
          provide: LookupValuesService,
          useValue: {
            countryCodes: () => of([]),
            cityCodes: () => of([]),
            getList: () => of([]),
          },
        },
        { provide: NotificationService, useValue: { success: jest.fn(), error: jest.fn() } },
        { provide: WarningDismissalService, useValue: new WarningDismissalService() },
        { provide: MatDialogRef, useValue: { close } },
        { provide: MAT_DIALOG_DATA, useValue: { run_accession: 'ERR1', artic_on_disk: false, on_disk: true } },
      ],
    });
    TestBed.overrideProvider(MatDialog, { useValue: { open: jest.fn() } });
    const fixture = TestBed.createComponent(LaunchWizardComponent);
    fixture.detectChanges();
    return fixture.componentInstance;
  }

  /** Fill the barcode rows; anything omitted stays as the wizard initialised it. */
  function fillRows(
    component: LaunchWizardComponent,
    rows: Array<Partial<{ site_id: string; sample_type: string; sampling_date: string; type: string }>>,
  ) {
    rows.forEach((row, i) => component.barcodesArray.at(i)?.patchValue(row));
  }

  const COMPLETE = { site_id: 'site-1', sample_type: 'water', sampling_date: '20240601' };

  const payloadOf = () => nanoporeRegister.mock.calls[0][1];

  // ── which rows are sent ────────────────────────────────────────────────────

  it('sends only the rows that carry a site, a sample type and a date', () => {
    const component = setup();
    fillRows(component, [
      COMPLETE,
      { site_id: 'site-1', sample_type: 'water' }, // no date
      { site_id: 'site-1', sampling_date: '20240601' }, // no sample type
    ]);

    component.saveAndClose();

    expect(payloadOf().barcodes).toHaveLength(1);
    expect(payloadOf().barcodes[0].barcode).toBe('barcode01');
  });

  it.each([
    ['too short', '2024060'],
    ['too long', '202406011'],
    ['not digits', '2024-06-01'],
    ['empty', ''],
  ])('rejects a sampling date that is %s', (_name, sampling_date) => {
    const component = setup();
    fillRows(component, [{ ...COMPLETE, sampling_date }]);

    component.saveAndClose();

    expect(payloadOf().barcodes).toHaveLength(0);
  });

  it('carries each complete row through with its barcode and optional type', () => {
    const component = setup();
    fillRows(component, [{ ...COMPLETE, type: 'positive-control' }]);

    component.saveAndClose();

    expect(payloadOf().barcodes[0]).toEqual({
      barcode: 'barcode01',
      sampling_date: '20240601',
      sample_type: 'water',
      site_id: 'site-1',
      type: 'positive-control',
    });
  });

  it('omits an empty type rather than sending a blank string', () => {
    const component = setup();
    fillRows(component, [{ ...COMPLETE, type: '' }]);

    component.saveAndClose();

    expect(payloadOf().barcodes[0].type).toBeUndefined();
  });

  // ── the site and sequencing fields ─────────────────────────────────────────

  it('falls back to the protocol and kit already on the run', () => {
    const component = setup();
    fillRows(component, [COMPLETE]);

    component.saveAndClose();

    expect(payloadOf().protocol_id).toBe('P2');
    expect(payloadOf().sequencing_kit_id).toBe('RB_GEN');
  });

  it('prefers a protocol and kit chosen in the form over the existing ones', () => {
    const component = setup();
    component.form.patchValue({ protocol_id: 'P3', sequencing_kit_id: 'LSK114' });
    fillRows(component, [COMPLETE]);

    component.saveAndClose();

    expect(payloadOf().protocol_id).toBe('P3');
    expect(payloadOf().sequencing_kit_id).toBe('LSK114');
  });

  it('sends the new-site fields when a site is being registered alongside', () => {
    const component = setup();
    component.form.patchValue({
      country: 'Norway',
      country_code: 'NO',
      city: 'Bergen',
      city_code: 'BGO',
      site: '01',
      location: 'Harbour',
      latitude: 60.39,
      longitude: 5.32,
    });
    fillRows(component, [COMPLETE]);

    component.saveAndClose();

    expect(payloadOf()).toMatchObject({
      country: 'Norway',
      country_code: 'NO',
      city: 'Bergen',
      city_code: 'BGO',
      site: '01',
      location: 'Harbour',
      latitude: 60.39,
      longitude: 5.32,
    });
  });

  // ── the two entry points ───────────────────────────────────────────────────

  it('builds an identical payload whether saving or registering', () => {
    // The whole reason for the extraction: two copies of the same mapping that must not drift.
    //
    // Every site field is filled, and the comparison is toStrictEqual, because both matter: an
    // earlier version patched two fields and used toEqual, which treats a dropped key and an
    // undefined one as the same. Deleting a field from one copy passed it.
    const SITE = {
      country: 'Norway',
      country_code: 'NO',
      city: 'Bergen',
      city_code: 'BGO',
      site: '01',
      location: 'Harbour',
      latitude: 60.39,
      longitude: 5.32,
      protocol_id: 'P3',
      sequencing_kit_id: 'LSK114',
    };
    const rows = [COMPLETE, { ...COMPLETE, sample_type: 'sediment', type: 'control' }];

    const saved = setup();
    saved.form.patchValue(SITE);
    fillRows(saved, rows);
    saved.saveAndClose();
    const fromSave = payloadOf();

    const registered = setup();
    registered.form.patchValue(SITE);
    fillRows(registered, rows);
    registered.register();
    const fromRegister = payloadOf();

    expect(fromRegister).toStrictEqual(fromSave);
    expect(Object.keys(fromRegister).sort()).toStrictEqual(Object.keys(fromSave).sort());
  });

  it('closes the dialog on save, and moves on to the launch step on register', () => {
    const saved = setup();
    fillRows(saved, [COMPLETE]);
    saved.saveAndClose();
    expect(close).toHaveBeenCalledWith(expect.objectContaining({ registered: true }));

    const registered = setup();
    fillRows(registered, [COMPLETE]);
    registered.register();
    expect(close).not.toHaveBeenCalled();
    expect(registered.step()).toBe('launch');
  });

  // ── what the save button offers ────────────────────────────────────────────

  it('counts the complete rows for the save button', () => {
    const component = setup();
    expect(component.completeBarcodeCount).toBe(0);

    fillRows(component, [COMPLETE, COMPLETE]);
    expect(component.completeBarcodeCount).toBe(2);
    expect(component.saveButtonLabel).toBe('Save (2 barcodes) & close');
  });

  it('allows a site-only save, with no complete barcode at all', () => {
    const component = setup();
    component.form.patchValue({ country: 'Norway' });
    component.form.markAsDirty(); // typing does this; patchValue does not
    expect(component.canSaveOnly()).toBe(true);
    expect(component.saveButtonLabel).toBe('Save site & close');
  });

  it('refuses to save when neither a barcode nor a site has been filled in', () => {
    expect(setup().canSaveOnly()).toBe(false);
  });

  it('refuses to save until something has actually changed', () => {
    // The wizard opens pre-filled from existing metadata, so a run that is already complete would
    // otherwise offer a save that writes back exactly what is there.
    const component = setup();
    component.barcodesArray.at(0).patchValue(COMPLETE);
    expect(component.completeBarcodeCount).toBe(1);
    expect(component.canSaveOnly()).toBe(false);

    component.form.markAsDirty();
    expect(component.canSaveOnly()).toBe(true);
  });

  it('refuses to save when the run has no protocol or kit to fall back on', () => {
    const component = setup({ ...READINESS, existing_protocol_id: null, existing_sequencing_kit_id: null });
    fillRows(component, [COMPLETE]);
    expect(component.canSaveOnly()).toBe(false);
  });
});

describe('isBarcodeRowComplete', () => {
  /**
   * The rule was written four times before D9 — twice over raw form values, twice over FormGroup
   * controls — which is why the wizard could disagree with itself about whether a row counted.
   */
  const row = (over: Partial<BarcodeRowValue> = {}): BarcodeRowValue =>
    ({ barcode: 'barcode01', site_id: 'site-1', sample_type: 'water', sampling_date: '20240601', ...over }) as BarcodeRowValue;

  it('accepts a row with a site, a sample type and an 8-digit date', () => {
    expect(isBarcodeRowComplete(row())).toBe(true);
  });

  it.each([
    ['no site', { site_id: null }],
    ['no sample type', { sample_type: null }],
    ['no date', { sampling_date: '' }],
    ['a date with separators', { sampling_date: '2024-06-01' }],
    ['a 7-digit date', { sampling_date: '2024060' }],
    ['a 9-digit date', { sampling_date: '202406011' }],
  ])('rejects a row with %s', (_name, over) => {
    expect(isBarcodeRowComplete(row(over as Partial<BarcodeRowValue>))).toBe(false);
  });

  it('does not require the optional type', () => {
    expect(isBarcodeRowComplete(row({ type: null }))).toBe(true);
  });
});

describe('LaunchWizardComponent — rows that cannot be saved', () => {
  /**
   * A sample needs a site, a type and a date because the database requires all three, so a partly
   * filled row genuinely cannot be stored. It used to be dropped in silence: the operator saw
   * "Saved 1 barcode" and no mention of the two they had begun.
   */
  let nanoporeRegister: jest.Mock;
  let notifySuccess: jest.Mock;
  let notifyMessage: jest.Mock;

  function setup() {
    nanoporeRegister = jest.fn().mockReturnValue(of(READINESS));
    notifySuccess = jest.fn();
    notifyMessage = jest.fn();

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [LaunchWizardComponent, NoopAnimationsModule],
      providers: [
        {
          provide: DiscoveryService,
          useValue: { nanoporeReadiness: () => of(READINESS), nanoporeRegister },
        },
        {
          provide: PipelineService,
          useValue: {
            getMpoxOptions: () => of({ clades: [], schemes: [] }),
            getExtractTargets: () => of({ targets: [] }),
          },
        },
        { provide: SitesService, useValue: { list: () => of([]) } },
        {
          provide: LookupValuesService,
          useValue: { countryCodes: () => of([]), cityCodes: () => of([]), getList: () => of([]) },
        },
        {
          provide: NotificationService,
          useValue: { success: notifySuccess, error: jest.fn(), message: notifyMessage },
        },
        { provide: WarningDismissalService, useValue: new WarningDismissalService() },
        { provide: MatDialogRef, useValue: { close: jest.fn() } },
        { provide: MAT_DIALOG_DATA, useValue: { run_accession: 'ERR1', artic_on_disk: false, on_disk: true } },
      ],
    });
    TestBed.overrideProvider(MatDialog, { useValue: { open: jest.fn() } });
    const fixture = TestBed.createComponent(LaunchWizardComponent);
    fixture.detectChanges();
    return fixture;
  }

  const setupFixture = setup;
  const setupComponent = () => setup().componentInstance;

  const COMPLETE = { site_id: 'site-1', sample_type: 'water', sampling_date: '20240601' };

  it('says how many begun rows were left behind', () => {
    const component = setupComponent();
    component.barcodesArray.at(0).patchValue(COMPLETE);
    component.barcodesArray.at(1).patchValue({ site_id: 'site-1', sample_type: 'water' });
    component.barcodesArray.at(2).patchValue({ site_id: 'site-1' });

    component.saveAndClose();

    expect(notifySuccess).toHaveBeenCalledWith(expect.stringContaining('Saved 1 barcode.'));
    expect(notifySuccess).toHaveBeenCalledWith(expect.stringContaining('2 rows could not be saved'));
  });

  it('does not count untouched rows as lost work', () => {
    const component = setupComponent();
    component.barcodesArray.at(0).patchValue(COMPLETE);

    component.saveAndClose();

    expect(notifySuccess).toHaveBeenCalledWith('Saved 1 barcode.');
  });

  it('lists the rows that will be left behind before Save is pressed', () => {
    // The point the operator made: the nanopore page disables Save until it can be used, so a
    // button that is enabled and then quietly discards work is the wrong shape. Save stays enabled
    // here — it still saves the completed rows — so the warning has to come before the click.
    const fixture = setupFixture();
    const component = fixture.componentInstance;
    component.barcodesArray.at(0).patchValue(COMPLETE);
    component.barcodesArray.at(1).patchValue({ site_id: 'site-1', sample_type: 'water' });
    fixture.detectChanges();

    expect(component.startedButIncompleteBarcodes).toEqual(['barcode02']);
    // Sharpens the sentence that was always above the table rather than adding a second one.
    expect(fixture.nativeElement.textContent).toContain('barcode02 is not complete and will be skipped');
    expect(fixture.nativeElement.textContent).not.toContain('Barcodes without all three fields will be skipped');
  });

  it('leaves the hint in its calm form when every begun row is complete', () => {
    const fixture = setupFixture();
    fixture.componentInstance.barcodesArray.at(0).patchValue(COMPLETE);
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('Barcodes without all three fields will be skipped');
    expect(fixture.nativeElement.textContent).not.toContain('is not complete and will be skipped');
  });

  it('names the barcodes with no metadata on the launch step, rather than a snackbar', () => {
    // Persistent and in context beats a message that disappears: the launch step is where the
    // operator decides whether to run without them.
    const updated = {
      ...READINESS,
      action: 'launch_with_warn',
      barcodes_in_metadata: ['barcode01'],
    } as unknown as NanoporeReadiness;
    const fixture = setupFixture();
    nanoporeRegister.mockReturnValue(of(updated));
    fixture.componentInstance.barcodesArray.at(0).patchValue(COMPLETE);

    fixture.componentInstance.register();
    fixture.detectChanges();

    expect(fixture.componentInstance.unregisteredBarcodes).toEqual(['barcode02', 'barcode03']);
    expect(fixture.nativeElement.textContent).toContain('barcode02, barcode03');
    expect(fixture.nativeElement.textContent).toContain('no metadata registered');
    expect(notifyMessage).not.toHaveBeenCalled();
  });

  it('falls back to the general warning when every barcode is registered', () => {
    const updated = {
      ...READINESS,
      action: 'launch_with_warn',
      barcodes_in_metadata: ['barcode01', 'barcode02', 'barcode03'],
    } as unknown as NanoporeReadiness;
    const fixture = setupFixture();
    nanoporeRegister.mockReturnValue(of(updated));
    fixture.componentInstance.barcodesArray.at(0).patchValue(COMPLETE);

    fixture.componentInstance.register();
    fixture.detectChanges();

    expect(fixture.componentInstance.unregisteredBarcodes).toEqual([]);
    expect(fixture.nativeElement.textContent).toContain('no FASTQ files');
  });
});
