import { TestBed } from '@angular/core/testing';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';

import { RestoreConfirmData, RestoreConfirmDialogComponent } from './restore-confirm-dialog.component';
import { RestoreDiff, TableRestoreDiff, diffSnapshots } from './snapshot-diff';

/** Build a diff the way `diffSnapshots` would, so the totals stay consistent with the parts. */
function diffOf(...tables: Array<Partial<TableRestoreDiff> & { table: string }>): RestoreDiff {
  const full = tables.map((t) => ({ added: 0, changed: 0, unchanged: 0, deleted: 0, ...t }));
  const total = (pick: (t: TableRestoreDiff) => number) =>
    full.reduce((sum, t) => sum + pick(t), 0);
  const totalAdded = total((t) => t.added);
  const totalChanged = total((t) => t.changed);
  const totalDeleted = total((t) => t.deleted);
  return {
    tables: full,
    totalAdded,
    totalChanged,
    totalUnchanged: total((t) => t.unchanged),
    totalDeleted,
    totalAffected: totalAdded + totalChanged + totalDeleted,
  };
}

function data(over: Partial<RestoreConfirmData> = {}): RestoreConfirmData {
  return {
    filename: 'odin-backup-before-import-20260731T110705.json',
    exportedAt: '2026-07-31T10:00:00.000Z',
    exportedBy: 'BGO-2009',
    diff: diffOf({ table: 'sites', added: 2, changed: 3, unchanged: 40, deleted: 2 }),
    ...over,
  };
}

describe('RestoreConfirmDialogComponent', () => {
  let close: jest.Mock;

  function setup(d: RestoreConfirmData) {
    close = jest.fn();
    TestBed.configureTestingModule({
      imports: [RestoreConfirmDialogComponent, NoopAnimationsModule],
      providers: [
        { provide: MatDialogRef, useValue: { close } },
        { provide: MAT_DIALOG_DATA, useValue: d },
      ],
    });
    const fixture = TestBed.createComponent(RestoreConfirmDialogComponent);
    fixture.detectChanges();
    return fixture;
  }

  const text = (f: ReturnType<typeof setup>): string => f.nativeElement.textContent ?? '';

  it('leads with the number of rows that disappear', () => {
    expect(text(setup(data()))).toContain('2 row(s) will be deleted');
  });

  it('requires an explicit acknowledgement before restore is possible', () => {
    // Restore is the only action that deletes data the operator never named, so a click alone
    // is not enough.
    const fixture = setup(data());
    expect(fixture.componentInstance.canRestore()).toBe(false);

    fixture.componentInstance.acknowledged.set(true);
    expect(fixture.componentInstance.canRestore()).toBe(true);
  });

  it('does not demand acknowledgement when nothing is deleted', () => {
    const fixture = setup(data({ diff: diffOf({ table: 'sites', changed: 5 }) }));
    expect(fixture.componentInstance.needsAcknowledgement()).toBe(false);
    expect(fixture.componentInstance.canRestore()).toBe(true);
  });

  it('shows where the snapshot came from, since an old file is the likeliest mistake', () => {
    const body = text(setup(data()));
    expect(body).toContain('odin-backup-before-import-20260731T110705.json');
    expect(body).toContain('2026-07-31T10:00:00.000Z');
    expect(body).toContain('BGO-2009');
  });

  it('counts only the rows that move, not every row in the snapshot', () => {
    // 40 identical rows are written too, but saying "restore 45" overstates what happens.
    const fixture = setup(data());
    const button = fixture.nativeElement.querySelector('mat-dialog-actions button:last-child');
    expect(button.textContent).toContain('Delete 2');
    expect(button.textContent).toContain('write 5');
  });

  it('offers nothing when the database already matches the snapshot', () => {
    const fixture = setup(data({ diff: diffOf({ table: 'sites', unchanged: 12 }) }));
    expect(fixture.componentInstance.isNoOp()).toBe(true);
    expect(fixture.componentInstance.canRestore()).toBe(false);
    expect(text(fixture)).toContain('already matches the snapshot');
    expect(text(fixture)).toContain('12 row(s) are identical');
  });

  it('reports a no-op when the same snapshot is restored a second time', () => {
    // The whole point of restoring is that the database then equals the file; asked again, the
    // dialog must say so rather than offer to write every row over itself. Built through the real
    // diff so the two halves cannot drift apart.
    const file = {
      version: '1',
      exported_at: '2026-07-31T10:00:00.000Z',
      exported_by: 'BGO-2009',
      tables: { sites: [{ id: 'a', site: 'Park' }], samples: [{ id: 's', sample_type: 'water' }] },
    };
    const fixture = setup(data({ diff: diffSnapshots(file, file) }));
    expect(fixture.componentInstance.isNoOp()).toBe(true);
    expect(fixture.componentInstance.canRestore()).toBe(false);
  });

  it('says what a restore does not touch, and that it is itself reversible', () => {
    const body = text(setup(data()));
    expect(body).toContain('Pipeline history');
    expect(body).toContain('backup of your current data is downloaded');
  });

  it('closes with true only after confirming', () => {
    const fixture = setup(data());
    fixture.componentInstance.restore();
    expect(close).toHaveBeenCalledWith(true);
    fixture.componentInstance.cancel();
    expect(close).toHaveBeenLastCalledWith(false);
  });
});
