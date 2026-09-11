import { TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { of } from 'rxjs';

import { RunsService } from '../../../core/services/runs.service';
import { SamplesService } from '../../../core/services/samples.service';
import { LookupValuesService } from '../../../core/services/lookup-values.service';
import { DiscoveryService } from '../../../core/services/discovery.service';
import { NanoporeRunAccessionsService } from '../../../core/services/nanopore-run-accessions.service';
import { NotificationService } from '../../../core/services/notification.service';
import { NanoporeRun } from '../../../core/models/run.model';
import { RunFormComponent, RunFormData } from './run-form.component';

/**
 * Characterization spec for the run form's four save modes, written before D11 replaces the
 * `Record<string, unknown>` + `delete payload[key]` construction with typed per-branch payloads.
 *
 * What is pinned is what the API can observe: which endpoint is called, which meaningful fields
 * arrive with which values, which keys must NOT be present in each mode (the create contract
 * takes exactly ONE of accession_id / run_accession / label), and that clearing an optional field
 * on edit sends an explicit null (the clear-optional-fields contract). Junk the backend silently
 * drops (derived read-only fields) is deliberately not pinned.
 */

const EXISTING_RUN = {
  id: 'run-1',
  accession_id: 'acc-1',
  run_accession: 'ERR100',
  label: null,
  sample_id: 'sample-1',
  sample_code: 'NOBGO01_WW',
  sampling_date: '20240601',
  barcode: 'barcode03',
  protocol_id: 'P2',
  sequencing_kit_id: 'RB_GEN',
  type: null,
  runName: 'Run A',
  sampleName: 'Sample A',
  comments: 'old comment',
} as unknown as NanoporeRun;

/** Dialog data for each mode — the one place the D11 dialog-contract change should touch. */
function dialogDataFor(mode: 'create' | 'add-to-group' | 'edit'): RunFormData {
  if (mode === 'edit') return { mode: 'edit', run: EXISTING_RUN };
  if (mode === 'add-to-group') {
    return {
      mode: 'add-to-group',
      group: {
        accession_id: 'acc-9',
        run_accession: 'ERR900',
        label: null,
        protocol_id: 'P2',
        sequencing_kit_id: 'RB_GEN',
      },
    };
  }
  return { mode: 'create' };
}

describe('RunFormComponent — save() per mode', () => {
  let create: jest.Mock;
  let update: jest.Mock;
  let close: jest.Mock;

  /** The group a barcode gets moved to in the group-move tests. */
  const TARGET_GROUP = {
    id: 'acc-9',
    run_accession: 'ERR900',
    label: null,
    protocol_id: 'P5',
    sequencing_kit_id: 'RB_AMP',
    runName: 'DemoExperiment',
    sampleName: 'MpoxDemoRun',
    comments: 'target comment',
  };

  function setup(mode: 'create' | 'add-to-group' | 'edit') {
    create = jest.fn().mockReturnValue(of(EXISTING_RUN));
    update = jest.fn().mockReturnValue(of(EXISTING_RUN));
    close = jest.fn();

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [RunFormComponent, NoopAnimationsModule],
      providers: [
        { provide: RunsService, useValue: { create, update } },
        {
          provide: SamplesService,
          useValue: {
            list: () =>
              of([
                { id: 'sample-1', sample_code: 'NOBGO01_WW', sampling_date: '20240601' },
                { id: 'sample-2', sample_code: 'NOBGO01_DW', sampling_date: '20240602' },
              ]),
          },
        },
        { provide: LookupValuesService, useValue: { getList: () => of([]) } },
        { provide: DiscoveryService, useValue: { getRunInfo: () => of() } },
        { provide: NanoporeRunAccessionsService, useValue: { list: () => of([TARGET_GROUP]) } },
        { provide: NotificationService, useValue: { error: jest.fn() } },
        { provide: MatDialogRef, useValue: { close } },
        { provide: MAT_DIALOG_DATA, useValue: dialogDataFor(mode) },
      ],
    });
    const fixture = TestBed.createComponent(RunFormComponent);
    fixture.detectChanges();
    return fixture.componentInstance;
  }

  const createdPayload = () => create.mock.calls[0][0];

  // ── create, run accession known ────────────────────────────────────────────

  it('creates with the run accession, and never a label or group id', () => {
    const component = setup('create');
    component.form.patchValue({ run_accession: 'ERR200', barcode: 'barcode01' });

    component.save();

    expect(create).toHaveBeenCalledTimes(1);
    expect(createdPayload()).toMatchObject({ run_accession: 'ERR200', barcode: 'barcode01' });
    expect(createdPayload()).not.toHaveProperty('label');
    expect(createdPayload()).not.toHaveProperty('accession_id');
  });

  it('omits optional fields the operator left empty on create', () => {
    const component = setup('create');
    component.form.patchValue({ run_accession: 'ERR200', barcode: 'barcode01' });

    component.save();

    expect(createdPayload()).not.toHaveProperty('comments');
    expect(createdPayload()).not.toHaveProperty('protocol_id');
    expect(createdPayload()).not.toHaveProperty('sample_id');
  });

  it('carries the chosen sample and metadata through on create', () => {
    const component = setup('create');
    component.form.patchValue({
      run_accession: 'ERR200',
      barcode: 'barcode01',
      sample_id: 'sample-2',
      protocol_id: 'P3',
      sequencing_kit_id: 'LSK114',
      type: 'clinical',
      runName: 'MyRun',
      sampleName: 'MySample',
      comments: 'fresh',
    });

    component.save();

    expect(createdPayload()).toMatchObject({
      run_accession: 'ERR200',
      barcode: 'barcode01',
      sample_id: 'sample-2',
      protocol_id: 'P3',
      sequencing_kit_id: 'LSK114',
      type: 'clinical',
      runName: 'MyRun',
      sampleName: 'MySample',
      comments: 'fresh',
    });
  });

  // ── create, pre-run label ──────────────────────────────────────────────────

  it('creates a pending entry with the label, and never a run accession', () => {
    const component = setup('create');
    component.toggleMode('pending');
    component.form.patchValue({ label: 'Kenya-May-2026', barcode: 'barcode01' });

    component.save();

    expect(createdPayload()).toMatchObject({ label: 'Kenya-May-2026', barcode: 'barcode01' });
    expect(createdPayload()).not.toHaveProperty('run_accession');
    expect(createdPayload()).not.toHaveProperty('accession_id');
  });

  // ── add to an existing group ───────────────────────────────────────────────

  it("adds to a group by the group's id, never by accession or label", () => {
    const component = setup('add-to-group');
    component.form.patchValue({ barcode: 'barcode05' });

    component.save();

    expect(createdPayload()).toMatchObject({ accession_id: 'acc-9', barcode: 'barcode05' });
    expect(createdPayload()).not.toHaveProperty('run_accession');
    expect(createdPayload()).not.toHaveProperty('label');
  });

  // ── edit ───────────────────────────────────────────────────────────────────

  it('updates by id, keeping the group-reassignment field and never the immutable ones', () => {
    const component = setup('edit');
    component.form.patchValue({ comments: 'new comment' });

    component.save();

    expect(update).toHaveBeenCalledTimes(1);
    expect(update.mock.calls[0][0]).toBe('run-1');
    const payload = update.mock.calls[0][1];
    expect(payload).toMatchObject({ accession_id: 'acc-1', barcode: 'barcode03', comments: 'new comment' });
    expect(payload).not.toHaveProperty('run_accession');
    expect(payload).not.toHaveProperty('label');
  });

  it('moving a barcode adopts the target group\'s metadata instead of exporting the old group\'s', () => {
    // The bug this pins: the dialog opens prefilled with the OLD group's run-level fields, and
    // the backend applies whatever run-level fields arrive to the NEW group. A pure group move
    // therefore stamped the old group's name, protocol and kit onto the group it moved into.
    // Selecting a group must re-describe those fields as the selected group's own values, so an
    // unedited move sends exactly what the target already stores and the backend writes nothing.
    const component = setup('edit');

    component.form.get('accession_id')!.setValue('acc-9');

    expect(component.form.getRawValue()).toMatchObject({
      protocol_id: 'P5',
      sequencing_kit_id: 'RB_AMP',
      runName: 'DemoExperiment',
      sampleName: 'MpoxDemoRun',
      comments: 'target comment',
    });

    component.save();

    expect(update.mock.calls[0][1]).toMatchObject({
      accession_id: 'acc-9',
      protocol_id: 'P5',
      sequencing_kit_id: 'RB_AMP',
      runName: 'DemoExperiment',
      sampleName: 'MpoxDemoRun',
      comments: 'target comment',
    });
  });

  it('a field edited after picking the group still applies to that group', () => {
    const component = setup('edit');

    component.form.get('accession_id')!.setValue('acc-9');
    component.form.patchValue({ sequencing_kit_id: 'LSK114' });

    component.save();

    expect(update.mock.calls[0][1]).toMatchObject({
      accession_id: 'acc-9',
      sequencing_kit_id: 'LSK114',
      protocol_id: 'P5',
    });
  });

  it('sends an explicit null for an optional field the operator cleared', () => {
    // The clear-optional-fields contract: omitted means "leave alone", null means "clear".
    const component = setup('edit');
    component.form.patchValue({ comments: '' });

    component.save();

    expect(update.mock.calls[0][1].comments).toBeNull();
  });

  // ── gating and the close handshake ─────────────────────────────────────────

  it('refuses to save while the form is invalid', () => {
    const component = setup('create');
    component.form.patchValue({ barcode: 'barcode01' }); // run_accession missing

    component.save();

    expect(create).not.toHaveBeenCalled();
    expect(update).not.toHaveBeenCalled();
  });

  it('closes the dialog with true so the page reloads', () => {
    const component = setup('edit');
    component.form.patchValue({ comments: 'x' });

    component.save();

    expect(close).toHaveBeenCalledWith(true);
  });
});
