import { TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { of } from 'rxjs';

import { RunsService } from '../../core/services/runs.service';
import { NanoporeRunAccessionsService } from '../../core/services/nanopore-run-accessions.service';
import { DiscoveryService } from '../../core/services/discovery.service';
import { NotificationService } from '../../core/services/notification.service';
import { NanoporeRun, NanoporeRunAccession } from '../../core/models/run.model';
import { RunsPageComponent } from './runs-page.component';

/**
 * The Runs page is where replicate numbers are entered, so it is also where their effect
 * must be visible: each barcode row shows its number, and each run card shows what the
 * numbers mean — a note for a group that will be pooled, a warning for one that cannot be
 * and blocks a launch. The texts come from the backend; the page only places them.
 */

const RA = 'ERR100';
const RA2 = 'ERR200';

const ACCESSIONS = [
  { id: 'acc-1', run_accession: RA, label: null, protocol_id: 'P2', sequencing_kit_id: 'RB_GEN' },
  { id: 'acc-2', run_accession: RA2, label: null, protocol_id: 'P2', sequencing_kit_id: 'RB_GEN' },
] as unknown as NanoporeRunAccession[];

const RUNS = [
  run('run-1', 'acc-1', RA, 'barcode01', 1, 'collection'),
  run('run-2', 'acc-1', RA, 'barcode02', 2, 'collection'),
  run('run-3', 'acc-2', RA2, 'barcode05', null, null),
  run('run-4', 'acc-2', RA2, 'barcode06', null, null),
];

function run(
  id: string,
  accession_id: string,
  run_accession: string,
  barcode: string,
  replicate: number | null,
  replicate_type: string | null,
): NanoporeRun {
  return {
    id,
    accession_id,
    run_accession,
    label: null,
    sample_id: 'sample-1',
    sample_code: 'NOBGO01_WW',
    sampling_date: '20240601',
    barcode,
    protocol_id: 'P2',
    sequencing_kit_id: 'RB_GEN',
    replicate,
    replicate_type,
  } as unknown as NanoporeRun;
}

const MESSAGES = {
  [RA]: {
    warnings: [],
    notes: [
      'NOBGO01_WW (20240601): replicates barcode01, barcode02 will be pooled before classification.',
    ],
  },
  [RA2]: {
    warnings: [
      'Sample NOBGO01_WW (20240601): barcodes barcode05, barcode06 are linked in run ERR200.',
    ],
    notes: [],
  },
};

describe('RunsPageComponent — replicate groups', () => {
  let text: string;
  let root: HTMLElement;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [RunsPageComponent, NoopAnimationsModule],
      providers: [
        { provide: RunsService, useValue: { list: () => of(RUNS) } },
        { provide: NanoporeRunAccessionsService, useValue: { list: () => of(ACCESSIONS) } },
        { provide: DiscoveryService, useValue: { nanoporeReplicateMessages: () => of(MESSAGES) } },
        { provide: NotificationService, useValue: { action: jest.fn(), error: jest.fn() } },
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(RunsPageComponent);
    fixture.detectChanges();
    root = fixture.nativeElement;
    text = root.textContent ?? '';
  });

  it('shows the pooling note on the run whose group is numbered', () => {
    expect(text).toContain('barcode01, barcode02 will be pooled');
    expect(root.querySelector('.info-tone')).not.toBeNull();
  });

  it('shows the warning on the run whose group is not numbered, with nothing to dismiss', () => {
    expect(text).toContain('barcode05, barcode06 are linked');
    expect(root.querySelector('.warning-strip:not(.info-tone)')).not.toBeNull();
    expect(root.querySelector('.warning-dismiss-btn')).toBeNull();
  });

  it('labels each numbered barcode with its replicate number and type', () => {
    const chips = Array.from(root.querySelectorAll('.bc-rep')).map((c) => c.textContent?.trim());
    expect(chips).toEqual(['replicate 1 · collection', 'replicate 2 · collection']);
  });
});
