import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  inject,
  signal,
  computed,
  OnInit,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';

import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatDividerModule } from '@angular/material/divider';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';

import { SyncService } from '../../core/services/sync.service';
import { NotificationService } from '../../core/services/notification.service';
import { ExportService } from '../../core/services/export.service';
import { formatImportFailure } from '../../core/utils/import-problems';
import { ExcelImportResult } from '../../core/services/export.service';
import { MatDialog } from '@angular/material/dialog';
import { ImportPreviewDialogComponent } from './import-preview-dialog.component';
import { describeImportResult } from './import-result';
import { backupBeforeImport } from './pre-import-backup';
import { Snapshot, diffSnapshots } from './snapshot-diff';
import { RestoreConfirmDialogComponent } from './restore-confirm-dialog.component';
import { SyncReviewTableComponent } from './sync-review-table.component';
import { buildReviewRows } from './review-rows';
import { SettingsService } from '../../core/services/settings.service';
import { SyncPreview, SyncReviewRow } from '../../core/models/sync.model';

@Component({
  selector: 'app-sync-page',
  standalone: true,
  imports: [
    CommonModule,
    RouterLink,
    MatButtonModule,
    MatCardModule,
    MatDividerModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    MatSlideToggleModule,
    SyncReviewTableComponent,
  ],
  templateUrl: './sync-page.component.html',
  styleUrls: ['./sync-page.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SyncPageComponent implements OnInit {
  private readonly syncService = inject(SyncService);
  private readonly settingsService = inject(SettingsService);
  private readonly notify = inject(NotificationService);
  private readonly exportService = inject(ExportService);
  private readonly dialog = inject(MatDialog);
  private readonly destroyRef = inject(DestroyRef);

  // ── State ────────────────────────────────────────────────────────────────

  deviceNameMissing = signal(false);
  selectedFileName = signal<string>('');
  loadingPreview = signal(false);
  loadingApply = signal(false);

  // Spreadsheet import/export. Moved here from Settings: this is metadata moving in and
  // out of the device, which is what this page is for — Settings is configuration.
  readonly importing = signal(false);
  readonly importReplaceMode = signal(false);
  readonly restoring = signal(false);

  preview = signal<SyncPreview | null>(null);

  /** Parsed raw import JSON, retained so we can POST it back in /apply. */
  private rawImportData: object | null = null;

  /** Review rows keyed by table name. */
  rowsByTable = signal<Record<string, SyncReviewRow[]>>({});

  // ── Derived ──────────────────────────────────────────────────────────────

  hasChanges = computed(() => Object.keys(this.rowsByTable()).length > 0);
  tablesWithRows = computed(() => Object.keys(this.rowsByTable()));

  // ── Lifecycle ────────────────────────────────────────────────────────────

  ngOnInit(): void {
    this.settingsService.get('device_name').pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (s) => this.deviceNameMissing.set(!s.value?.trim()),
      error: () => this.deviceNameMissing.set(false),
    });
  }

  // ── Event handlers ───────────────────────────────────────────────────────

  onExport(): void {
    this.syncService.exportData();
  }

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;

    this.selectedFileName.set(file.name);
    this.preview.set(null);
    this.rowsByTable.set({});

    const reader = new FileReader();
    reader.onload = () => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(reader.result as string);
      } catch {
        this.notify.message(
          'Could not parse file — make sure it is a valid ODIN JSON export.',
          5000,
        );
        return;
      }
      this.rawImportData = parsed as object;
      this.loadingPreview.set(true);

      this.syncService.preview(this.rawImportData!).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
        next: (p) => {
          this.preview.set(p);
          this.rowsByTable.set(buildReviewRows(p));
          this.loadingPreview.set(false);
        },
        error: (err) => {
          this.loadingPreview.set(false);
          this.notify.error(err, 'Preview failed');
        },
      });
    };
    reader.readAsText(file);

    // Reset the input so the same file can be re-selected after clearing
    input.value = '';
  }

  onApply(): void {
    if (!this.rawImportData) return;

    // Collect decisions from the review rows
    const decisions: Record<string, Record<string, string>> = {};
    for (const [table, rows] of Object.entries(this.rowsByTable())) {
      decisions[table] = {};
      for (const row of rows) {
        const id = String(row.entry.incoming['id']);
        decisions[table][id] = row.action;
      }
    }

    this.loadingApply.set(true);
    // Same protection as the Excel path: a JSON merge overwrites rows too, so it gets the
    // automatic backup as a precondition rather than only the more obviously destructive one.
    this.backupThen(() => this.applyDecisions(decisions));
  }

  private applyDecisions(decisions: Record<string, Record<string, string>>): void {
    this.syncService
      .apply({ import_data: this.rawImportData!, decisions })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          this.loadingApply.set(false);
          const ins = result.total_inserted;
          const upd = result.total_updated;
          const msg =
            ins === 0 && upd === 0
              ? 'No changes were applied.'
              : `Done — inserted ${ins}, updated ${upd}.`;
          this.notify.success(msg);
          this.onClearPreview();
        },
        error: (err) => {
          this.loadingApply.set(false);
          this.notify.error(err, 'Apply failed');
        },
      });
  }

  onClearPreview(): void {
    this.preview.set(null);
    this.rowsByTable.set({});
    this.rawImportData = null;
    this.selectedFileName.set('');
  }

  exportExcel() {
    this.exportService.exportExcel();
  }

  importExcel(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;

    const mode = this.importReplaceMode() ? 'replace' : 'merge';
    this.importing.set(true);

    // Preview first, always. The backend runs the real import and rolls it back, so what the
    // dialog shows is what applying will do. The window.confirm this replaces could only ask
    // "continue?" about replace mode, without saying how many rows would be deleted — and a
    // rejected upload could only report the first three problems in a snackbar.
    this.exportService
      .previewExcelImport(file, mode)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (preview) => {
          this.importing.set(false);
          input.value = '';
          this.confirmAndApply(file, mode, preview);
        },
        error: (err) => {
          this.importing.set(false);
          input.value = '';
          // A workbook can still be unreadable rather than merely wrong — a non-xlsx file,
          // or one openpyxl cannot parse.
          this.notify.message(formatImportFailure(err?.error?.detail, 'Import failed'), 8000);
        },
      });
  }

  private confirmAndApply(file: File, mode: string, preview: ExcelImportResult): void {
    this.dialog
      // Same reason as the restore dialog: open at the top, where the Replace warning is.
      .open(ImportPreviewDialogComponent, {
        data: preview,
        width: '760px',
        autoFocus: 'first-heading',
      })
      .afterClosed()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((confirmed) => {
        if (!confirmed) return;
        this.applyExcelImport(file, mode);
      });
  }

  private applyExcelImport(file: File, mode: string): void {
    this.importing.set(true);
    this.backupThen(() =>
      // importValidRows: the operator has seen the problem list and chosen to proceed, which
      // is exactly what that flag means to the API.
      this.exportService
        .importExcel(file, mode, true)
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({
        next: (res) => this.notify.message(describeImportResult(res), 8000),
        error: (err) =>
          this.notify.message(formatImportFailure(err?.error?.detail, 'Import failed'), 8000),
        complete: () => this.importing.set(false),
      }),
    );
  }

  /**
   * Take the automatic backup, then run *action* — and only then.
   *
   * The backup is a precondition, not a side effect: if the snapshot cannot be fetched, the
   * import does not happen. A backup that silently failed would be worse than none, because it
   * would be believed in.
   */
  private backupThen(action: () => void): void {
    backupBeforeImport(this.syncService)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (filename) => {
          this.notify.message(`Backup saved to your downloads as ${filename}.`, 6000);
          action();
        },
        error: (err) => {
          this.importing.set(false);
          this.loadingApply.set(false);
          this.restoring.set(false);
          this.notify.error(
            err,
            'Could not save a backup first, so nothing was imported. Check your connection and try again.',
          );
        },
      });
  }

  /**
   * Restore from a snapshot file.
   *
   * Deliberately a separate action from "Merge from another device" even though both take the
   * same file: a merge adds the other side's information, a restore makes this database match
   * the file. Opposite consequences, so they are not one control with a mode.
   */
  restoreFromSnapshot(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    input.value = '';

    const reader = new FileReader();
    reader.onload = () => {
      let parsed: Snapshot;
      try {
        parsed = JSON.parse(reader.result as string) as Snapshot;
      } catch {
        this.notify.message(
          'Could not parse file — make sure it is a JSON snapshot downloaded from ODIN.',
          5000,
        );
        return;
      }
      if (!parsed?.tables) {
        this.notify.message(
          'That file is not an ODIN snapshot — it has no tables to restore from.',
          5000,
        );
        return;
      }
      this.confirmRestore(file.name, parsed);
    };
    reader.readAsText(file);
  }

  /** Compare against the current state so the confirmation can name the number of deletions. */
  private confirmRestore(filename: string, snapshot: Snapshot): void {
    this.restoring.set(true);
    this.syncService
      .fetchSnapshot()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: async (blob) => {
          this.restoring.set(false);
          const current = JSON.parse(await blob.text()) as Snapshot;
          const diff = diffSnapshots(current, snapshot);
          this.dialog
            .open(RestoreConfirmDialogComponent, {
              width: '680px',
              // Focus the title, not the first tabbable control. The default lands on the
              // acknowledge checkbox at the foot of the dialog, and the browser scrolls it into
              // view — hiding the deletion warning the tick is supposed to be acknowledging.
              autoFocus: 'first-heading',
              data: {
                filename,
                exportedAt: snapshot.exported_at,
                exportedBy: snapshot.exported_by,
                diff,
              },
            })
            .afterClosed()
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe((confirmed) => {
              if (confirmed) this.applyRestore(snapshot);
            });
        },
        error: (err) => {
          this.restoring.set(false);
          this.notify.error(err, 'Could not read the current data to compare against');
        },
      });
  }

  private applyRestore(snapshot: Snapshot): void {
    this.restoring.set(true);
    // The same precondition as every other write: back up first, and abandon if that fails —
    // which also makes the restore itself reversible.
    this.backupThen(() =>
      this.syncService
        .restore(snapshot)
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({
        // Worded as rows *written*, because the backend counts every row it wrote while the
        // confirmation counted only the ones that moved. Both are true; only one is a surprise.
        next: (result) =>
          this.notify.message(
            `Restore complete — this database now matches the snapshot ` +
              `(${result.total_restored} row(s) written, ${result.total_deleted} deleted).`,
            8000,
          ),
        error: (err) => this.notify.error(err, 'Restore failed'),
        complete: () => this.restoring.set(false),
      }),
    );
  }
}
