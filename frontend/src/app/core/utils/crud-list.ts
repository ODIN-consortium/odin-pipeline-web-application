import { DestroyRef, Signal, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ComponentType } from '@angular/cdk/portal';
import { MatDialog } from '@angular/material/dialog';
import { MatSort } from '@angular/material/sort';
import { MatTableDataSource } from '@angular/material/table';
import { Observable } from 'rxjs';

import { ConfirmDialogComponent } from '../../shared/components/confirm-dialog.component';
import { NotificationService } from '../services/notification.service';

/** The slice of a CRUD service a list page uses. All three entity services satisfy it. */
export interface CrudListService<T> {
  list(): Observable<T[]>;
  delete(id: string): Observable<void>;
}

/** What varies between one CRUD list page and the next. */
export interface CrudListOptions<T> {
  service: CrudListService<T>;
  /** The dialog opened to add or edit a record. */
  form: ComponentType<unknown>;
  formWidth: string;
  /**
   * Lower-case singular noun for the record: 'site', 'sample', 'run'. Drives the confirmation
   * ("Delete site", `Delete site "NOBGO01"?`) and the success message ("Site deleted").
   */
  noun: string;
  /** Names one record in the delete confirmation — usually its code. */
  describe: (row: T) => string;
  /** Shown when the list is genuinely empty. Replaced by a failure message if the load failed. */
  emptyMessage: string;
  /** Plural for the failure message; defaults to the noun with an "s". */
  plural?: string;
  /**
   * The page's `viewChild(MatSort)` query. Given one, the sort is attached as soon as the table
   * exists; omitted, the list stays in the order the server sent.
   */
  sort?: Signal<MatSort | undefined>;
  sortingDataAccessor?: MatTableDataSource<T>['sortingDataAccessor'];
}

/**
 * The list/add/edit/delete machinery every CRUD table page shares.
 *
 * `sites-page`, `samples-page` and `biomeme-runs-page` each wrote out the same triplet: load into
 * a table with a loading flag, open a form dialog and reload if it saved, confirm a delete then
 * delete/notify/reload. Only the service, the form, the noun and how a record names itself
 * differed — and those are the four things this takes as options.
 *
 * Both list representations are kept because both are needed: `dataSource` is what MatTable
 * binds to and what MatSort drives, while `rows` is a signal, so an OnPush template can ask
 * whether the list is empty without reaching into `dataSource.data` — a plain property read that
 * would not mark the view dirty on its own.
 */
export class CrudList<T extends { id: string }> {
  readonly loading = signal(false);
  readonly rows = signal<T[]>([]);
  readonly dataSource = new MatTableDataSource<T>();
  readonly isEmpty = computed(() => this.rows().length === 0);
  readonly loadFailed = signal(false);

  /**
   * What to show in place of the table. An empty list and a failed request both render nothing,
   * so without this the page invites the operator to add their first record when in fact the
   * server could not be reached.
   */
  readonly emptyMessage = computed(() =>
    this.loadFailed()
      ? `Could not load ${this.options.plural ?? `${this.options.noun}s`}. ` +
        'The server could not be reached, or returned an error.'
      : this.options.emptyMessage,
  );

  constructor(
    private readonly options: CrudListOptions<T>,
    private readonly dialog: MatDialog,
    private readonly notify: NotificationService,
    private readonly destroyRef: DestroyRef,
  ) {
    if (options.sortingDataAccessor) {
      this.dataSource.sortingDataAccessor = options.sortingDataAccessor;
    }
  }

  load(): void {
    this.loading.set(true);
    this.options.service
      .list()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (data) => {
          this.rows.set(data);
          this.dataSource.data = data;
          this.loadFailed.set(false);
          this.loading.set(false);
        },
        error: (err: unknown) => {
          // Clear the rows: showing the last successful list under a failure message would
          // suggest the page is current when it is not.
          this.rows.set([]);
          this.dataSource.data = [];
          this.loadFailed.set(true);
          this.loading.set(false);
          this.notify.error(err, `Could not load ${this.options.plural ?? `${this.options.noun}s`}`);
        },
      });
  }

  /** Open the form on a record, or with no argument to add one. Reloads only if it saved. */
  openForm(row?: T): void {
    this.dialog
      .open(this.options.form, { width: this.options.formWidth, data: row ?? null })
      .afterClosed()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((saved) => {
        if (saved) this.load();
      });
  }

  remove(row: T): void {
    const { noun } = this.options;
    this.dialog
      .open(ConfirmDialogComponent, {
        data: {
          title: `Delete ${noun}`,
          message: `Delete ${noun} "${this.options.describe(row)}"?`,
        },
        width: '360px',
      })
      .afterClosed()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((confirmed) => {
        if (confirmed) this.deleteRow(row);
      });
  }

  private deleteRow(row: T): void {
    const capitalised = this.options.noun[0].toUpperCase() + this.options.noun.slice(1);
    this.options.service
      .delete(row.id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.notify.success(`${capitalised} deleted`);
          this.load();
        },
        error: (err: unknown) => this.notify.error(err, 'Delete failed'),
      });
  }
}

/**
 * Build a {@link CrudList} for a page. Call it from a field initializer, where Angular's
 * injection context is available:
 *
 * ```ts
 * readonly sort = viewChild(MatSort);
 * readonly crud = crudList<Site>({ service: inject(SitesService), sort: this.sort, ... });
 * ```
 *
 * Attaching the sort through the query signal replaces the
 * `setTimeout(() => this.dataSource.sort = this.sort)` all three pages used. The timeout existed
 * because the table is behind `@if (loading())`, so the old decorator-based `@ViewChild` was
 * still undefined when the rows arrived. A signal query updates when the table actually appears,
 * so the assignment can wait for that rather than for a macrotask and hope.
 */
export function crudList<T extends { id: string }>(options: CrudListOptions<T>): CrudList<T> {
  const list = new CrudList<T>(
    options,
    inject(MatDialog),
    inject(NotificationService),
    inject(DestroyRef),
  );
  const sort = options.sort;
  if (sort) {
    effect(() => {
      const resolved = sort();
      if (resolved) list.dataSource.sort = resolved;
    });
  }
  return list;
}
