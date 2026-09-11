import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatDialog } from '@angular/material/dialog';
import { of } from 'rxjs';

import { SyncService } from '../../core/services/sync.service';
import { SettingsService } from '../../core/services/settings.service';
import { NotificationService } from '../../core/services/notification.service';
import { ExportService } from '../../core/services/export.service';
import { SyncPreview, SyncReviewRow, SyncTableDiff } from '../../core/models/sync.model';
import { buildReviewRows } from './review-rows';
import { SyncPageComponent } from './sync-page.component';

/**
 * Characterization spec for the merge-review table, written before D13 extracts it from the
 * sync page's template. Everything is asserted through the DOM, because the template wiring is
 * what moves. These tests must pass unchanged on both sides of the split.
 */

const PREVIEW = {
  exported_by: 'field-laptop',
  exported_at: '2026-08-01T10:00:00Z',
  summary: {},
  diff: {},
} as SyncPreview;

const SITE_META = {
  id: 'site-1',
  created_at: '2026-01-01',
  updated_at: '2026-01-02',
  created_by: 'them',
  updated_by: 'them',
};

const EMPTY_DIFF: SyncTableDiff = { new: [], identical: [], updated: [], independent_duplicate: [], conflict: [] };

/** Rows come from the real builder so the spec also covers how a preview becomes rows. */
function reviewRows(): Record<string, SyncReviewRow[]> {
  return buildReviewRows({
    ...PREVIEW,
    diff: {
      sites: {
        ...EMPTY_DIFF,
        new: [{ incoming: { ...SITE_META, site_code: 'NOBGO01', country: 'Norway', city: 'Bergen' } }],
        updated: [
          {
            incoming: { ...SITE_META, id: 'site-2', site_code: 'NOOSL01', country: 'Norway', city: 'Oslo' },
            local: { ...SITE_META, id: 'site-2', site_code: 'NOOSL01', country: 'Norway', city: 'Olso' },
          },
        ],
      },
      nanopore_runs: {
        ...EMPTY_DIFF,
        conflict: [
          {
            incoming: { ...SITE_META, id: 'run-1', barcode: 'barcode02' },
            local: { ...SITE_META, id: 'run-1', barcode: 'barcode01' },
          },
        ],
      },
    },
  });
}

describe('SyncPageComponent — merge review table', () => {
  let fixture: ComponentFixture<SyncPageComponent>;
  let component: SyncPageComponent;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [SyncPageComponent, NoopAnimationsModule],
      providers: [
        {
          provide: SyncService,
          useValue: { exportData: jest.fn(), preview: jest.fn(), apply: jest.fn(), fetchSnapshot: jest.fn(), restore: jest.fn() },
        },
        { provide: SettingsService, useValue: { get: () => of({ value: 'device-1' }) } },
        {
          provide: NotificationService,
          useValue: { message: jest.fn(), success: jest.fn(), error: jest.fn() },
        },
        {
          provide: ExportService,
          useValue: { exportExcel: jest.fn(), previewExcelImport: jest.fn(), importExcel: jest.fn() },
        },
      ],
    });
    TestBed.overrideProvider(MatDialog, { useValue: { open: jest.fn() } });
    fixture = TestBed.createComponent(SyncPageComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  const el = (): HTMLElement => fixture.nativeElement;

  function showPreview(rows = reviewRows()): void {
    component.preview.set(PREVIEW);
    component.rowsByTable.set(rows);
    fixture.detectChanges();
  }

  it('renders one section per table, with its label and row count', () => {
    showPreview();

    const titles = Array.from(el().querySelectorAll('.table-title')).map((t) =>
      (t.textContent ?? '').replace(/\s+/g, ' ').trim(),
    );
    expect(titles).toHaveLength(2);
    expect(titles[0]).toContain('Sites');
    expect(titles[0]).toContain('(2 rows)');
    expect(titles[1]).toContain('Nanopore runs');
    expect(titles[1]).toContain('(1 row)');
  });

  it('badges each row with its category', () => {
    showPreview();

    const badges = Array.from(el().querySelectorAll('.cat-badge')).map((b) => b.textContent?.trim());
    expect(badges).toEqual(['New', 'Updated', 'Conflict']);
  });

  it('labels each row by its table key fields', () => {
    showPreview();

    expect(el().textContent).toContain('NOBGO01 / Norway / Bergen');
    expect(el().textContent).toContain('barcode02');
  });

  it('shows mine vs theirs for each changed field', () => {
    showPreview();

    const changeTables = el().querySelectorAll('.changes-table');
    expect(changeTables).toHaveLength(2);
    const updated = changeTables[0];
    expect(updated.textContent).toContain('city');
    expect(updated.textContent).toContain('Olso'); // mine (the local typo)
    expect(updated.textContent).toContain('Oslo'); // theirs
  });

  it('lists the values of a new row, without the sync metadata fields', () => {
    showPreview();

    const newBlock = el().querySelector('.new-record-values')!;
    expect(newBlock.textContent).toContain('site_code');
    expect(newBlock.textContent).toContain('NOBGO01');
    expect(newBlock.textContent).not.toContain('created_at');
    expect(newBlock.textContent).not.toContain('updated_by');
  });

  it('gives every row an action select', () => {
    showPreview();

    expect(el().querySelectorAll('.action-select')).toHaveLength(3);
  });

  it('offers the apply bar when there are changes, and the all-clear when not', () => {
    showPreview();
    expect(el().textContent).toContain('Apply selected changes');

    showPreview({});
    expect(el().textContent).not.toContain('Apply selected changes');
    expect(el().textContent).toContain('No changes — this device is already up to date');
  });
});
