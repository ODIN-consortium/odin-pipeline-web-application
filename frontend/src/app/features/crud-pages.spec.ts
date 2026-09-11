import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Type } from '@angular/core';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatDialog } from '@angular/material/dialog';
import { Observable, Subject, of, throwError } from 'rxjs';

import { ConfirmDialogComponent } from '../shared/components/confirm-dialog.component';
import { NotificationService } from '../core/services/notification.service';
import { BiomemeRunsService } from '../core/services/biomeme-runs.service';
import { SamplesService } from '../core/services/samples.service';
import { SitesService } from '../core/services/sites.service';
import { BiomemeRun } from '../core/models/biomeme-run.model';
import { Sample } from '../core/models/sample.model';
import { Site } from '../core/models/site.model';
import { BiomemeRunsPageComponent } from './biomeme-runs/biomeme-runs-page.component';
import { SamplesPageComponent } from './samples/samples-page.component';
import { SitesPageComponent } from './sites/sites-page.component';

/**
 * Characterization spec for the three CRUD table pages, written before C1 extracts what they
 * share.
 *
 * `sites-page`, `samples-page` and `biomeme-runs-page` each write out the same
 * `load() / openForm() / deleteX()` triplet — the largest duplication in the frontend. The claim
 * C1 rests on is that the three behave identically, so they are characterized *together*: the
 * shared suite below runs against all three, and anything a page does differently has to be
 * stated as a difference in its own config or its own test.
 *
 * The assertions are deliberately made from outside the component — rendered rows, which service
 * method was called, what the confirmation was asked — never how a page stores its list. Two of
 * them hold a `MatTableDataSource` and one holds a signal today, and the extraction unifies that;
 * a spec that reached for either would have to be rewritten by the very change it exists to check.
 *
 * It has since outgrown pure characterization: the sort and load-failure tests describe behaviour
 * the extraction introduced, and are kept here because they belong to the same three pages.
 */

const SITE = { id: 'site-1', site_code: 'NOBGO01', country: 'Norway', city: 'Bergen' } as Site;
const SAMPLE = { id: 'sample-1', sample_code: 'NOBGO01_water', sample_type: 'water' } as Sample;
const RUN = { id: 'run-1', biomeme_run_name: 'BM-001', sample_code: 'NOBGO01_water' } as BiomemeRun;

interface PageCase {
  name: string;
  component: Type<unknown>;
  service: Type<unknown>;
  /** A row the list endpoint returns. */
  row: Record<string, unknown>;
  /** The heading on the "add" button, used to reach it without a test-only selector. */
  addButton: string;
  /** What the delete confirmation should say. */
  confirmTitle: string;
  confirmMessage: string;
  /** What the success notification should say. */
  deletedMessage: string;
  /** Words from the empty state, so the assertion does not depend on the element used. */
  emptyState: string;
  /** The fallback the failure notification carries when the list request fails. */
  loadFailedMessage: string;
  /** Whether the table carries a MatSort. Only two of the three do. */
  sorted: boolean;
  /**
   * Two records in the reverse of the table's default sort order, so applying that sort has a
   * visible effect and leaving it unapplied has an equally visible one.
   */
  unsorted: Record<string, unknown>[];
  /** How a record names itself in the rendered row — the same field the page's `describe` uses. */
  describe: (row: Record<string, unknown>) => string;
}

const PAGES: PageCase[] = [
  {
    name: 'SitesPageComponent',
    component: SitesPageComponent,
    service: SitesService,
    row: SITE,
    addButton: 'Add site',
    confirmTitle: 'Delete site',
    confirmMessage: 'Delete site "NOBGO01"?',
    deletedMessage: 'Site deleted',
    emptyState: 'No sites yet',
    loadFailedMessage: 'Could not load sites',
    sorted: true,
    unsorted: [
      { ...SITE, id: 'site-1', country: 'Norway', site_code: 'NOBGO01' },
      { ...SITE, id: 'site-2', country: 'Denmark', site_code: 'DKCPH01' },
    ],
    describe: (row) => row['site_code'] as string,
  },
  {
    name: 'SamplesPageComponent',
    component: SamplesPageComponent,
    service: SamplesService,
    row: SAMPLE,
    addButton: 'Add sample',
    confirmTitle: 'Delete sample',
    confirmMessage: 'Delete sample "NOBGO01_water"?',
    deletedMessage: 'Sample deleted',
    emptyState: 'No samples yet',
    loadFailedMessage: 'Could not load samples',
    sorted: true,
    unsorted: [
      { ...SAMPLE, id: 'sample-1', sample_code: 'ZZZ_water' },
      { ...SAMPLE, id: 'sample-2', sample_code: 'AAA_water' },
    ],
    describe: (row) => row['sample_code'] as string,
  },
  {
    name: 'BiomemeRunsPageComponent',
    component: BiomemeRunsPageComponent,
    service: BiomemeRunsService,
    row: RUN,
    addButton: 'Add run',
    confirmTitle: 'Delete run',
    confirmMessage: 'Delete run "BM-001"?',
    deletedMessage: 'Run deleted',
    emptyState: 'No Biomeme runs yet',
    loadFailedMessage: 'Could not load Biomeme runs',
    sorted: false,
    unsorted: [
      { ...RUN, id: 'run-1', biomeme_run_name: 'BM-002' },
      { ...RUN, id: 'run-2', biomeme_run_name: 'BM-001' },
    ],
    describe: (row) => row['biomeme_run_name'] as string,
  },
];

describe.each(PAGES)('$name', (page) => {
  let list: jest.Mock;
  let remove: jest.Mock;
  let notifySuccess: jest.Mock;
  let notifyError: jest.Mock;
  let dialogOpen: jest.Mock;
  /** What each dialog resolves to; keyed so a test can answer the two kinds separately. */
  let confirmResult: unknown;
  let formResult: unknown;

  function setup(listResult: Observable<unknown[]> = of([page.row])): ComponentFixture<unknown> {
    list = jest.fn().mockReturnValue(listResult);
    remove = jest.fn().mockReturnValue(of(undefined));
    notifySuccess = jest.fn();
    notifyError = jest.fn();
    confirmResult = false;
    formResult = false;
    dialogOpen = jest.fn().mockImplementation((component: Type<unknown>) => ({
      afterClosed: () => of(component === ConfirmDialogComponent ? confirmResult : formResult),
    }));

    TestBed.configureTestingModule({
      imports: [page.component, NoopAnimationsModule],
      providers: [
        { provide: page.service, useValue: { list, delete: remove } },
        { provide: NotificationService, useValue: { success: notifySuccess, error: notifyError } },
      ],
    });
    // Not a plain provider: the pages import MatDialogModule, whose own MatDialog provider would
    // otherwise win and open the real dialogs.
    TestBed.overrideProvider(MatDialog, { useValue: { open: dialogOpen } });
    const fixture = TestBed.createComponent(page.component);
    fixture.detectChanges();
    return fixture;
  }

  const rows = (f: ComponentFixture<unknown>): Element[] =>
    Array.from(f.nativeElement.querySelectorAll('tr[mat-row]'));

  const buttonLabelled = (f: ComponentFixture<unknown>, label: string): HTMLButtonElement => {
    const match = Array.from<HTMLButtonElement>(f.nativeElement.querySelectorAll('button')).find(
      (b) => (b.textContent ?? '').includes(label),
    );
    if (!match) throw new Error(`no button labelled ${label}`);
    return match;
  };

  /**
   * The delete button of the first row, found by its icon rather than its position — an earlier
   * version fell back to `buttons[1]`, which would have quietly tested the edit button if the
   * actions were ever reordered.
   */
  const deleteButton = (f: ComponentFixture<unknown>): HTMLButtonElement => {
    const match = Array.from<HTMLButtonElement>(rows(f)[0].querySelectorAll('button')).find(
      (b) => b.querySelector('mat-icon')?.textContent?.trim() === 'delete',
    );
    if (!match) throw new Error('no delete button in the first row');
    return match;
  };

  // ── loading ────────────────────────────────────────────────────────────────

  it('loads the list on init and renders a row for each record', () => {
    const fixture = setup();
    expect(list).toHaveBeenCalledTimes(1);
    expect(rows(fixture)).toHaveLength(1);
  });

  it('shows a spinner while loading and the table once loaded', () => {
    const pending = new Subject<unknown[]>();
    const fixture = setup(pending);
    expect(fixture.nativeElement.querySelector('mat-spinner')).toBeTruthy();
    expect(fixture.nativeElement.querySelector('table')).toBeFalsy();

    pending.next([page.row]);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('mat-spinner')).toBeFalsy();
    expect(rows(fixture)).toHaveLength(1);
  });

  it('stops loading when the list request fails, rather than spinning forever', () => {
    const fixture = setup(throwError(() => new Error('network')));
    expect(fixture.nativeElement.querySelector('mat-spinner')).toBeFalsy();
    expect(rows(fixture)).toHaveLength(0);
  });

  it('reports a failed list instead of claiming there is nothing there', () => {
    // An empty list and an unreachable server both render no rows. Saying "click Add to get
    // started" in the second case invites the operator to enter data the server cannot store.
    const fixture = setup(throwError(() => new Error('network')));
    expect(notifyError).toHaveBeenCalledWith(expect.any(Error), page.loadFailedMessage);
    expect(fixture.nativeElement.textContent).toContain('Could not load');
    expect(fixture.nativeElement.textContent).not.toContain(page.emptyState);
  });

  it('drops the stale rows when a reload fails, rather than showing them as current', () => {
    const fixture = setup();
    expect(rows(fixture)).toHaveLength(1);

    list.mockReturnValue(throwError(() => new Error('network')));
    formResult = true;
    buttonLabelled(fixture, page.addButton).click();
    fixture.detectChanges();

    expect(rows(fixture)).toHaveLength(0);
    expect(fixture.nativeElement.textContent).toContain('Could not load');
  });

  it('shows the empty state when there are no records', () => {
    const fixture = setup(of([]));
    expect(fixture.nativeElement.textContent).toContain(page.emptyState);
  });

  // ── opening the form ───────────────────────────────────────────────────────

  it('opens an empty form from the add button', () => {
    const fixture = setup();
    buttonLabelled(fixture, page.addButton).click();
    expect(dialogOpen).toHaveBeenCalledTimes(1);
    expect(dialogOpen.mock.calls[0][1]).toMatchObject({ data: null });
  });

  it('opens the form on the clicked record when a row is clicked', () => {
    const fixture = setup();
    rows(fixture)[0].dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(dialogOpen.mock.calls[0][1]).toMatchObject({ data: page.row });
  });

  it('reloads after the form saves', () => {
    const fixture = setup();
    formResult = true;
    buttonLabelled(fixture, page.addButton).click();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('does not reload when the form is cancelled', () => {
    const fixture = setup();
    formResult = false;
    buttonLabelled(fixture, page.addButton).click();
    expect(list).toHaveBeenCalledTimes(1);
  });

  // ── deleting ───────────────────────────────────────────────────────────────

  it('asks for confirmation, naming the record, before deleting', () => {
    const fixture = setup();
    deleteButton(fixture).click();
    expect(dialogOpen).toHaveBeenCalledWith(
      ConfirmDialogComponent,
      expect.objectContaining({
        data: { title: page.confirmTitle, message: page.confirmMessage },
      }),
    );
    expect(remove).not.toHaveBeenCalled();
  });

  it('deletes nothing when the confirmation is declined', () => {
    const fixture = setup();
    confirmResult = false;
    deleteButton(fixture).click();
    expect(remove).not.toHaveBeenCalled();
    expect(list).toHaveBeenCalledTimes(1);
  });

  it('deletes, reports success and reloads once confirmed', () => {
    const fixture = setup();
    confirmResult = true;
    deleteButton(fixture).click();
    expect(remove).toHaveBeenCalledWith(page.row['id']);
    expect(notifySuccess).toHaveBeenCalledWith(page.deletedMessage);
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('reports a failed delete and does not reload', () => {
    const fixture = setup();
    confirmResult = true;
    remove.mockReturnValue(throwError(() => new Error('conflict')));
    deleteButton(fixture).click();
    expect(notifyError).toHaveBeenCalledWith(expect.any(Error), 'Delete failed');
    expect(list).toHaveBeenCalledTimes(1);
  });

  it('does not open the form when the delete button inside a row is clicked', () => {
    // The row itself opens the form, so the action buttons must stop the event.
    const fixture = setup();
    deleteButton(fixture).click();
    expect(dialogOpen).toHaveBeenCalledTimes(1);
    expect(dialogOpen.mock.calls[0][0]).toBe(ConfirmDialogComponent);
  });

  // ── sorting ────────────────────────────────────────────────────────────────

  it(`${page.sorted ? 'applies' : 'does not apply'} the table's default sort to the loaded rows`, () => {
    // The user-visible end of the MatSort wiring. Worth asserting directly: the sort used to be
    // attached from inside a setTimeout, so it only took effect a macrotask after the rows
    // arrived; it is now driven by the viewChild query signal, which resolves when the table
    // does. Both orderings below are the server's order reversed, so a page that never attaches
    // its sort fails this, and one that sorts when it should not fails it too.
    const fixture = setup(of(page.unsorted));
    fixture.detectChanges();
    const rendered = rows(fixture).map((r) => r.textContent ?? '');
    const expected = page.sorted ? [...page.unsorted].reverse() : page.unsorted;
    expected.forEach((record, i) => {
      expect(rendered[i]).toContain(page.describe(record));
    });
  });
});
