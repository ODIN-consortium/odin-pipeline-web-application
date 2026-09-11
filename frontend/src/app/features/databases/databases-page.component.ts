import { Component, DestroyRef, inject, OnInit, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { ReactiveFormsModule, FormBuilder, Validators } from '@angular/forms';
import { MatTableModule } from '@angular/material/table';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatCardModule } from '@angular/material/card';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';

import { forkJoin } from 'rxjs';

import { MatDialog } from '@angular/material/dialog';

import { DatabasesService } from '../../core/services/databases.service';
import { NotificationService } from '../../core/services/notification.service';
import { DatabaseEntry, EffectiveDatabaseRow } from '../../core/models/database-entry.model';
import { ConfirmDialogComponent } from '../../shared/components/confirm-dialog.component';
import { EmptyStateComponent } from '../../shared/components/empty-state.component';

interface ToolOption {
  value: string;
  label: string;
  defaultParams: string;
  pathHint: string;
}

const TOOL_OPTIONS: ToolOption[] = [
  {
    value: 'kraken2',
    label: 'kraken2',
    defaultParams: '--quick',
    pathHint: 'e.g. /home/user/store_dir/combined_pathogens',
  },
];

@Component({
  selector: 'app-databases-page',
  standalone: true,
  imports: [
    CommonModule,
    ReactiveFormsModule,
    MatTableModule,
    MatButtonModule,
    MatIconModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatCardModule,
    MatTooltipModule,
    MatProgressSpinnerModule,
    EmptyStateComponent,
  ],
  templateUrl: './databases-page.component.html',
  styleUrl: './databases-page.component.css',
})
export class DatabasesPageComponent implements OnInit {
  private readonly svc = inject(DatabasesService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly notify = inject(NotificationService);
  private readonly fb = inject(FormBuilder);
  private readonly dialog = inject(MatDialog);

  readonly entries = signal<EffectiveDatabaseRow[]>([]);
  readonly dbEntries = signal<DatabaseEntry[]>([]);
  readonly loading = signal(true);
  readonly saving = signal(false);
  readonly editingEntry = signal<DatabaseEntry | null>(null);
  readonly sourceIsFile = signal(false);
  readonly sourceFile = signal<string | null>(null);
  readonly columns = ['tool', 'db_name', 'db_params', 'db_path', 'actions'];
  readonly columnsReadOnly = ['tool', 'db_name', 'db_params', 'db_path'];

  readonly toolOptions = TOOL_OPTIONS;
  readonly selectedToolOption = signal<ToolOption | null>(TOOL_OPTIONS[0]);

  readonly form = this.fb.group({
    tool: [TOOL_OPTIONS[0].value, Validators.required],
    db_name: ['db1', Validators.required],
    db_params: [TOOL_OPTIONS[0].defaultParams],
    db_path: ['', Validators.required],
  });

  ngOnInit(): void {
    this.load();
  }

  onToolChange(value: string): void {
    const opt = TOOL_OPTIONS.find((o) => o.value === value) ?? null;
    this.selectedToolOption.set(opt);
    if (opt) {
      this.form.patchValue({ db_params: opt.defaultParams });
    }
  }

  load(): void {
    this.loading.set(true);
    forkJoin({ effective: this.svc.listEffective(), db: this.svc.list() })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
      next: ({ effective, db }) => {
        this.sourceIsFile.set(effective.source === 'file');
        this.sourceFile.set(effective.file);
        this.entries.set(effective.entries);
        this.dbEntries.set(db);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  addEntry(): void {
    if (this.form.invalid) return;
    this.saving.set(true);
    const v = this.form.getRawValue();
    this.svc
      .create({
        tool: v.tool!,
        db_name: v.db_name!,
        db_params: v.db_params || undefined,
        db_path: v.db_path!,
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.saving.set(false);
          this.resetForm();
          this.load();
          this.notify.success('Database entry added.');
        },
        error: (err) => {
          this.saving.set(false);
          this.notify.error(err, 'Failed to save.');
        },
      });
  }

  saveEntry(): void {
    const editing = this.editingEntry();
    if (editing) {
      this.updateEntry(editing);
    } else {
      this.addEntry();
    }
  }

  startEdit(row: EffectiveDatabaseRow): void {
    const entry = this.dbEntries().find(
      (e) => e.tool === row.tool && e.db_name === row.db_name && e.db_path === row.db_path,
    );
    if (!entry) return;
    this.editingEntry.set(entry);
    const opt = TOOL_OPTIONS.find((o) => o.value === entry.tool) ?? TOOL_OPTIONS[0];
    this.selectedToolOption.set(opt);
    this.form.setValue({
      tool: entry.tool,
      db_name: entry.db_name,
      db_params: entry.db_params ?? '',
      db_path: entry.db_path,
    });
  }

  cancelEdit(): void {
    this.editingEntry.set(null);
    this.resetForm();
  }

  updateEntry(entry: DatabaseEntry): void {
    if (this.form.invalid) return;
    this.saving.set(true);
    const v = this.form.getRawValue();
    this.svc
      .update(entry.id, {
        tool: v.tool!,
        db_name: v.db_name!,
        // null (not undefined) so clearing the field actually blanks it — an
        // undefined value is dropped from the JSON and read as "leave unchanged".
        db_params: v.db_params || null,
        db_path: v.db_path!,
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.saving.set(false);
          this.editingEntry.set(null);
          this.resetForm();
          this.load();
          this.notify.success('Database entry updated.');
        },
        error: (err) => {
          this.saving.set(false);
          this.notify.error(err, 'Failed to update.');
        },
      });
  }

  private resetForm(): void {
    this.form.reset({
      tool: TOOL_OPTIONS[0].value,
      db_name: 'db1',
      db_params: TOOL_OPTIONS[0].defaultParams,
      db_path: '',
    });
    this.selectedToolOption.set(TOOL_OPTIONS[0]);
  }

  deleteEntry(row: EffectiveDatabaseRow): void {
    const entry = this.dbEntries().find(
      (e) => e.tool === row.tool && e.db_name === row.db_name && e.db_path === row.db_path,
    );
    if (!entry) return;
    // Every other page confirms before deleting; this one used to be the exception.
    this.dialog
      .open(ConfirmDialogComponent, {
        data: {
          title: 'Delete database entry',
          message: `Delete database entry "${entry.tool} / ${entry.db_name}"?`,
        },
        width: '360px',
      })
      .afterClosed()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((confirmed) => {
        if (confirmed) this.reallyDelete(entry);
      });
  }

  private reallyDelete(entry: DatabaseEntry): void {
    this.svc
      .delete(entry.id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
      next: () => {
        this.load();
        this.notify.success('Entry deleted.');
      },
      error: (err) => {
        this.notify.error(err, 'Delete failed.');
      },
    });
  }
}
