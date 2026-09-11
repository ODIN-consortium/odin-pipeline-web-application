import { DestroyRef, Component, inject, OnInit, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatExpansionModule } from '@angular/material/expansion';
import { Observable } from 'rxjs';

/** One collapsible entry whose plain-text body is fetched lazily on open. */
export interface LazyTextEntry {
  key: string;
  label: string;
  load: () => Observable<string>;
}

export interface LazyTextAccordionDialogData {
  icon: string;
  title: string;
  /** Sub-label shown after the title, e.g. the run accession. */
  label: string;
  entries: LazyTextEntry[];
  /** Shown when there are no entries at all. */
  emptyMessage: string;
  /** Fallback error text when a fetch fails without a server-provided message. */
  notFoundMessage: string;
}

interface EntryState {
  key: string;
  label: string;
  load: () => Observable<string>;
  loading: boolean;
  loaded: boolean;
  content: string;
  error: string;
}

@Component({
  selector: 'app-lazy-text-accordion-dialog',
  standalone: true,
  imports: [
    MatDialogModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatExpansionModule,
  ],
  template: `
    <h2 mat-dialog-title>
      <mat-icon style="vertical-align:middle;margin-right:6px;">{{ data.icon }}</mat-icon>
      {{ data.title }} — {{ data.label }}
    </h2>

    <mat-dialog-content>
      @if (states().length === 0) {
        <p style="color:#888;font-size:0.875rem;margin:8px 0;">{{ data.emptyMessage }}</p>
      } @else {
        <mat-accordion multi>
          @for (state of states(); track state.key; let i = $index) {
            <mat-expansion-panel [expanded]="i === 0" (opened)="load(i)">
              <mat-expansion-panel-header>
                <mat-panel-title>{{ state.label }}</mat-panel-title>
              </mat-expansion-panel-header>

              @if (state.loading) {
                <div style="display:flex;align-items:center;gap:8px;padding:16px 0">
                  <mat-spinner diameter="20"></mat-spinner>
                  <span style="color:#888;font-size:0.85rem;">Loading…</span>
                </div>
              } @else if (state.error) {
                <p style="color:#c62828;font-size:0.875rem;margin:8px 0;">{{ state.error }}</p>
              } @else {
                <pre class="accordion-pre">{{ state.content }}</pre>
              }
            </mat-expansion-panel>
          }
        </mat-accordion>
      }
    </mat-dialog-content>

    <mat-dialog-actions align="end">
      <button mat-button mat-dialog-close>Close</button>
    </mat-dialog-actions>
  `,
  styles: [`
    mat-dialog-content {
      max-height: 65vh;
    }
    .accordion-pre {
      overflow: auto;
      max-height: 45vh;
      background: #f5f5f5;
      color: #212121;
      font-size: 0.8rem;
      padding: 12px;
      border-radius: 4px;
      white-space: pre;
      font-family: 'Consolas', 'Courier New', monospace;
    }
  `],
})
export class LazyTextAccordionDialogComponent implements OnInit {
  readonly data = inject<LazyTextAccordionDialogData>(MAT_DIALOG_DATA);
  private readonly destroyRef = inject(DestroyRef);

  readonly states = signal<EntryState[]>([]);

  ngOnInit(): void {
    this.states.set(
      this.data.entries.map((e) => ({
        key: e.key,
        label: e.label,
        load: e.load,
        loading: false,
        loaded: false,
        content: '',
        error: '',
      })),
    );
    // The first panel is expanded by default — load it eagerly.
    if (this.data.entries.length > 0) {
      this.load(0);
    }
  }

  /** Fetch the body for the panel at `index`, once. */
  load(index: number): void {
    const current = this.states()[index];
    if (!current || current.loaded || current.loading) {
      return;
    }
    this.patch(index, { loading: true });
    current
      .load()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
      next: (text) => this.patch(index, { loading: false, loaded: true, content: text }),
      error: (err) =>
        this.patch(index, {
          loading: false,
          loaded: true,
          error: err?.error ?? this.data.notFoundMessage,
        }),
    });
  }

  private patch(index: number, changes: Partial<EntryState>): void {
    this.states.update((list) =>
      list.map((state, i) => (i === index ? { ...state, ...changes } : state)),
    );
  }
}
