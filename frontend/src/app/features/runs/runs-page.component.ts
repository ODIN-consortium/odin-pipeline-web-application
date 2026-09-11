import { Component, computed, DestroyRef, inject, OnInit, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { forkJoin } from 'rxjs';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { RunsService } from '../../core/services/runs.service';
import { NotificationService } from '../../core/services/notification.service';
import { NanoporeRunAccessionsService } from '../../core/services/nanopore-run-accessions.service';
import { NanoporeRun, NanoporeRunAccession } from '../../core/models/run.model';
import { RunFormComponent, RunFormData } from './run-form/run-form.component';
import { ConfirmDialogComponent } from '../../shared/components/confirm-dialog.component';

interface RunGroup {
  kind: 'linked' | 'pending';
  accession_id: string;
  run_accession: string | null;
  label: string | null;
  runName?: string | null;
  sampleName?: string | null;
  protocol_id?: string;
  sequencing_kit_id?: string;
  runs: NanoporeRun[];
  /** For pending cards: the run_accession the user is typing to link */
  linkInput?: string;
}

@Component({
  selector: 'app-runs-page',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatButtonModule,
    MatIconModule,
    MatDialogModule,
    MatTooltipModule,
    MatProgressSpinnerModule,
    MatChipsModule,
    MatFormFieldModule,
    MatInputModule,
  ],
  templateUrl: './runs-page.component.html',
  styles: [`
    .page-container { padding: 16px; max-width: 900px; }
    .action-bar { display: flex; align-items: center; margin-bottom: 16px; }
    .spacer { flex: 1; }
    .run-card {
      border-radius: 6px;
      margin-bottom: 16px;
      overflow: hidden;
      background: white;
    }
    .card-header {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 10px 16px;
      background: #f5f5f5;
      border-bottom: 1px solid #e0e0e0;
    }
    .collapse-chevron { font-size: 20px; width: 20px; height: 20px; color: #666; transition: transform 0.2s; flex-shrink: 0; }
    .collapse-chevron.rotated { transform: rotate(-90deg); }
    .header-title {
      display: flex;
      align-items: center;
      gap: 6px;
      flex: 1;
      min-width: 0;
    }
    .header-icon { font-size: 20px; width: 20px; height: 20px; color: #1976d2; }
    .run-accession { font-family: monospace; font-size: 0.9rem; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .title-stack { display: flex; flex-direction: column; justify-content: center; gap: 1px; min-width: 0; overflow: hidden; }
    .run-display-name { font-weight: 600; font-size: 0.95rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .run-accession-small { font-family: monospace; font-size: 0.72rem; color: #999; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .run-label { font-size: 0.95rem; font-weight: 500; }
    .pending-chip { background: #fff3e0 !important; color: #e65100 !important; font-size: 0.75rem !important; height: 22px !important; }
    .header-meta { display: flex; gap: 10px; font-size: 0.8rem; color: #666; align-items: center; }
    .link-panel {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 16px;
      background: #fff8e1;
      border-bottom: 1px solid #ffe082;
    }
    .link-input { flex: 1; }
    .barcode-list { padding: 0 4px; }
    .barcode-row {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 6px 12px;
      border-bottom: 1px solid #f0f0f0;
      font-size: 0.9rem;
    }
    .barcode-row:last-child { border-bottom: none; }
    .bc-code { font-family: monospace; min-width: 90px; color: #333; }
    .bc-sample { flex: 1; color: #555; }
    .bc-date { color: #888; font-size: 0.82rem; }
    .bc-actions { display: flex; gap: 0; margin-left: auto; }
  `],
})
export class RunsPageComponent implements OnInit {
  private readonly runsService = inject(RunsService);
  private readonly nraService = inject(NanoporeRunAccessionsService);
  private readonly dialog = inject(MatDialog);
  private readonly notify = inject(NotificationService);
  private readonly destroyRef = inject(DestroyRef);

  readonly loading = signal(false);
  readonly collapsedGroups = signal(new Set<string>());
  readonly linkingIds = signal(new Set<string>());
  private readonly runs = signal<NanoporeRun[]>([]);
  private readonly accessions = signal<NanoporeRunAccession[]>([]);

  readonly groups = computed<RunGroup[]>(() => {
    const allRuns = this.runs();
    const allAccessions = this.accessions();

    // Seed the map from all NRAs — this makes empty groups visible.
    const groupMap = new Map<string, RunGroup>();
    for (const nra of allAccessions) {
      groupMap.set(nra.id, {
        kind: nra.run_accession ? 'linked' : 'pending',
        accession_id: nra.id,
        run_accession: nra.run_accession,
        label: nra.label,
        runName: nra.runName ?? null,
        sampleName: nra.sampleName ?? null,
        protocol_id: nra.protocol_id,
        sequencing_kit_id: nra.sequencing_kit_id,
        runs: [],
        linkInput: '',
      });
    }
    // Fill in barcode rows.
    for (const r of allRuns) {
      let g = groupMap.get(r.accession_id);
      if (!g) {
        // Orphan run (NRA missing from list) — show it anyway.
        g = {
          kind: r.run_accession ? 'linked' : 'pending',
          accession_id: r.accession_id,
          run_accession: r.run_accession,
          label: r.label,
          runName: null,
          sampleName: null,
          protocol_id: r.protocol_id,
          sequencing_kit_id: r.sequencing_kit_id,
          runs: [],
          linkInput: '',
        };
        groupMap.set(r.accession_id, g);
      }
      g.runs.push(r);
    }
    // Sort: linked first (alphabetically), then pending (alphabetically by label).
    return Array.from(groupMap.values()).sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === 'linked' ? -1 : 1;
      const nameA = a.run_accession ?? a.label ?? '';
      const nameB = b.run_accession ?? b.label ?? '';
      return nameA.localeCompare(nameB);
    });
  });

  toggleCollapse(id: string): void {
    const s = new Set(this.collapsedGroups());
    if (s.has(id)) { s.delete(id); } else { s.add(id); }
    this.collapsedGroups.set(s);
  }

  ngOnInit() {
    this.load();
  }

  load() {
    this.loading.set(true);
    forkJoin({
      runs: this.runsService.list(),
      accessions: this.nraService.list(),
    }).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: ({ runs, accessions }) => {
        this.runs.set(runs);
        this.accessions.set(accessions);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  openForm(run?: NanoporeRun, group?: RunGroup) {
    const data: RunFormData = run
      ? { mode: 'edit', run }
      : group
        ? {
            mode: 'add-to-group',
            group: {
              accession_id: group.accession_id,
              run_accession: group.run_accession,
              label: group.label,
              protocol_id: group.protocol_id,
              sequencing_kit_id: group.sequencing_kit_id,
            },
          }
        : { mode: 'create' };
    const ref = this.dialog.open(RunFormComponent, {
      width: '560px',
      data,
    });
    ref.afterClosed().pipe(takeUntilDestroyed(this.destroyRef)).subscribe((saved) => {
      if (saved) this.load();
    });
  }

  linkGroup(g: RunGroup) {
    const accession = (g.linkInput ?? '').trim();
    if (!accession) return;
    this.linkingIds.update((s) => new Set([...s, g.accession_id]));
    this.nraService.link(g.accession_id, accession).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        this.linkingIds.update((s) => { const n = new Set(s); n.delete(g.accession_id); return n; });
        this.notify.action(`Linked to ${accession}`, 'OK', 3000);
        this.load();
      },
      error: (err) => {
        this.linkingIds.update((s) => { const n = new Set(s); n.delete(g.accession_id); return n; });
        this.notify.error(err, 'Link failed');
      },
    });
  }

  deleteGroup(g: RunGroup) {
    const name = g.run_accession ?? g.label ?? g.accession_id;
    this.dialog.open(ConfirmDialogComponent, {
      data: { title: 'Delete group', message: `Delete empty group "${name}"?` },
      width: '360px',
    }).afterClosed().pipe(takeUntilDestroyed(this.destroyRef)).subscribe((confirmed) => {
      if (!confirmed) return;
      this.nraService.delete(g.accession_id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
        next: () => {
          this.notify.action(`Group "${name}" deleted`, 'OK', 3000);
          this.load();
        },
        error: (err) =>
          this.notify.error(err, 'Delete failed'),
      });
    });
  }

  deleteRun(run: NanoporeRun) {
    const label = run.run_accession
      ? `${run.run_accession} / ${run.barcode}`
      : `${run.label ?? run.accession_id} / ${run.barcode}`;
    this.dialog.open(ConfirmDialogComponent, {
      data: { title: 'Delete run', message: `Delete run "${label}"?` },
      width: '360px',
    }).afterClosed().pipe(takeUntilDestroyed(this.destroyRef)).subscribe((confirmed) => {
      if (!confirmed) return;
      this.runsService.delete(run.id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
        next: () => {
          this.notify.success('Run deleted');
          this.load();
        },
        error: (err) =>
          this.notify.error(err, 'Delete failed'),
      });
    });
  }
}

