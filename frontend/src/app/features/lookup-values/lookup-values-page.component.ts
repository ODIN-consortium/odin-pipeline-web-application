import { Component, DestroyRef, inject, OnInit, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatTableModule } from '@angular/material/table';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatInputModule } from '@angular/material/input';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';

import { LookupValuesService } from '../../core/services/lookup-values.service';
import { NotificationService } from '../../core/services/notification.service';
import { LookupValue } from '../../core/models/lookup-value.model';
import { ConfirmDialogComponent } from '../../shared/components/confirm-dialog.component';

interface LookupListDef {
  name: string;
  label: string;
  hasExternalCode: boolean;
}

const LOOKUP_LISTS: LookupListDef[] = [
  { name: 'sample_type',       label: 'Sample types',       hasExternalCode: false },
  { name: 'protocol_id',       label: 'Protocol IDs',       hasExternalCode: false },
  { name: 'sequencing_kit_id', label: 'Sequencing kit IDs', hasExternalCode: true  },
  { name: 'mpox_type',         label: 'Mpox sample types',  hasExternalCode: false },
];

@Component({
  selector: 'app-lookup-values-page',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    MatTableModule,
    MatExpansionModule,
    MatInputModule,
    MatFormFieldModule,
    MatDialogModule,
  ],
  templateUrl: './lookup-values-page.component.html',
  styles: [`
    .page-container { padding: 24px; max-width: 900px; }
    .hint-text { color: #666; margin-bottom: 16px; }
    .lookup-table { width: 100%; margin-bottom: 8px; }
    .inline-input { border: 1px solid #ccc; border-radius: 4px; padding: 2px 6px; font-size: 14px; width: 100%; box-sizing: border-box; }
    .code-fixed { font-family: monospace; font-size: 13px; color: #333; }
    .add-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 8px 0; }
    .add-field-sm { width: 100px; }
    .add-field-md { width: 160px; }
    .add-field-lg { width: 260px; }
  `],
})
export class LookupValuesPageComponent implements OnInit {
  private readonly lookupValuesService = inject(LookupValuesService);
  private readonly notify = inject(NotificationService);
  private readonly dialog = inject(MatDialog);
  private readonly destroyRef = inject(DestroyRef);

  readonly lookupLists = LOOKUP_LISTS;
  readonly lookupData = signal<Record<string, LookupValue[] | undefined>>({});
  readonly lookupLoading = signal<Record<string, boolean>>({});
  editingId: Record<string, string | null> = {};
  editBuffer: Record<string, { description: string; external_code: string }> = {};
  newEntry: Record<string, { code: string; description: string; external_code: string }> = {};

  constructor() {
    for (const l of LOOKUP_LISTS) {
      this.editingId[l.name] = null;
      this.editBuffer[l.name] = { description: '', external_code: '' };
      this.newEntry[l.name] = { code: '', description: '', external_code: '' };
    }
  }

  ngOnInit() {
    for (const l of LOOKUP_LISTS) {
      this.loadList(l.name);
    }
  }

  columnsFor(listDef: LookupListDef): string[] {
    return listDef.hasExternalCode
      ? ['code', 'description', 'external_code', 'actions']
      : ['code', 'description', 'actions'];
  }

  loadList(name: string) {
    if (Object.prototype.hasOwnProperty.call(this.lookupData(), name)) return;
    this.lookupLoading.update(s => ({ ...s, [name]: true }));
    this.lookupValuesService.getList(name).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (rows) => {
        this.lookupData.update(s => ({ ...s, [name]: rows }));
        this.lookupLoading.update(s => ({ ...s, [name]: false }));
      },
      error: (err) => {
        this.lookupData.update(s => ({ ...s, [name]: [] }));
        this.lookupLoading.update(s => ({ ...s, [name]: false }));
        this.notify.action(`Failed to load ${name}: ${err?.status ?? ''} ${err?.error?.detail ?? err?.message ?? 'Unknown error'}`, 'OK', 6000);
      },
    });
  }

  startEdit(listDef: LookupListDef, row: LookupValue) {
    this.editingId[listDef.name] = row.id;
    this.editBuffer[listDef.name] = {
      description: row.description ?? '',
      external_code: row.external_code ?? '',
    };
  }

  cancelEdit(listName: string) {
    this.editingId[listName] = null;
  }

  saveEdit(listName: string, id: string) {
    const buf = this.editBuffer[listName];
    // null (not undefined) so clearing a field actually blanks it — an undefined
    // value is dropped from the JSON and read by the backend as "leave unchanged".
    this.lookupValuesService.update(listName, id, {
      description: buf.description || null,
      external_code: buf.external_code || null,
    }).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (updated) => {
        this.lookupData.update(s => {
          const list = [...(s[listName] ?? [])];
          const idx = list.findIndex(r => r.id === id);
          if (idx >= 0) list[idx] = updated;
          return { ...s, [listName]: list };
        });
        this.editingId[listName] = null;
        this.notify.success('Entry updated');
      },
      error: (err) => this.notify.error(err, 'Update failed'),
    });
  }

  deleteEntry(listName: string, row: LookupValue) {
    this.dialog.open(ConfirmDialogComponent, {
      data: { title: 'Delete entry', message: `Delete '${row.code}' from ${listName}?` },
      width: '360px',
    }).afterClosed().pipe(takeUntilDestroyed(this.destroyRef)).subscribe((confirmed) => {
      if (!confirmed) return;
      this.lookupValuesService.delete(listName, row.id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
        next: () => {
          this.lookupData.update(s => ({ ...s, [listName]: (s[listName] ?? []).filter(r => r.id !== row.id) }));
          this.notify.success('Entry deleted');
        },
        error: (err) => this.notify.error(err, 'Delete failed'),
      });
    });
  }

  addEntry(listName: string) {
    const n = this.newEntry[listName];
    if (!n.code.trim()) return;
    this.lookupValuesService.create(listName, {
      code: n.code.trim(),
      description: n.description.trim() || undefined,
      external_code: n.external_code.trim() || undefined,
    }).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (created) => {
        this.lookupData.update(s => ({ ...s, [listName]: [...(s[listName] ?? []), created] }));
        this.newEntry[listName] = { code: '', description: '', external_code: '' };
        this.notify.success('Entry added');
      },
      error: (err) => this.notify.error(err, 'Add failed'),
    });
  }
}
