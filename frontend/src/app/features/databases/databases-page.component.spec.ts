import { TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatDialog } from '@angular/material/dialog';
import { of } from 'rxjs';

import { DatabasesService } from '../../core/services/databases.service';
import { NotificationService } from '../../core/services/notification.service';
import { DatabaseEntry } from '../../core/models/database-entry.model';
import { DatabasesPageComponent } from './databases-page.component';

/**
 * Pins the delete confirmation. This page used to be the one place in the app that deleted
 * on the first click — flagged during C1, fixed once the user agreed to the behaviour change.
 */

const ENTRY = {
  id: 'db-1',
  tool: 'kraken2',
  db_name: 'db1',
  db_params: '--quick',
  db_path: '/data/k2',
} as DatabaseEntry;

const ROW = { tool: 'kraken2', db_name: 'db1', db_params: '--quick', db_path: '/data/k2' };

describe('DatabasesPageComponent — delete confirmation', () => {
  let del: jest.Mock;
  let afterClosed: jest.Mock;
  let open: jest.Mock;

  function setup(confirmed: boolean) {
    del = jest.fn().mockReturnValue(of(void 0));
    afterClosed = jest.fn().mockReturnValue(of(confirmed));
    open = jest.fn().mockReturnValue({ afterClosed });

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [DatabasesPageComponent, NoopAnimationsModule],
      providers: [
        {
          provide: DatabasesService,
          useValue: {
            listEffective: () =>
              of({ source: 'db', file: null, entries: [{ ...ROW, source: 'db' }] }),
            list: () => of([ENTRY]),
            delete: del,
          },
        },
        { provide: NotificationService, useValue: { success: jest.fn(), error: jest.fn() } },
      ],
    });
    TestBed.overrideProvider(MatDialog, { useValue: { open } });
    const fixture = TestBed.createComponent(DatabasesPageComponent);
    fixture.detectChanges();
    return fixture.componentInstance;
  }

  it('asks before deleting, naming the entry', () => {
    const component = setup(true);

    component.deleteEntry(ROW);

    expect(open).toHaveBeenCalledTimes(1);
    expect(open.mock.calls[0][1].data.message).toContain('kraken2 / db1');
    expect(del).toHaveBeenCalledWith('db-1');
  });

  it('deletes nothing when the dialog is cancelled', () => {
    const component = setup(false);

    component.deleteEntry(ROW);

    expect(open).toHaveBeenCalledTimes(1);
    expect(del).not.toHaveBeenCalled();
  });
});
