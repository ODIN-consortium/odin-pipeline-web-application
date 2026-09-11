import { Component, DestroyRef, inject, OnInit, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { ReactiveFormsModule, FormBuilder, AbstractControl } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatDividerModule } from '@angular/material/divider';
import { MatCardModule } from '@angular/material/card';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatSelectModule } from '@angular/material/select';
import { catchError, forkJoin, of } from 'rxjs';

import { SettingsService } from '../../core/services/settings.service';
import { NotificationService } from '../../core/services/notification.service';
import { formatImportFailure } from '../../core/utils/import-problems';
import { SettingDef } from '../../core/models/config-value.model';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [
    CommonModule,
    ReactiveFormsModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatDividerModule,
    MatCardModule,
    MatTooltipModule,
    MatSlideToggleModule,
    MatSelectModule,
  ],
  templateUrl: './settings.component.html',
  styles: [`
    .page-container { padding: 24px; max-width: 900px; }
    .action-bar { display: flex; align-items: center; margin-bottom: 16px; }
    .spacer { flex: 1 1 auto; }
    .full-width { width: 100%; }
    .hint-text { color: #666; margin-bottom: 8px; }
    .path-warning { display: flex; align-items: center; gap: 4px; color: #e65100; font-size: 12px; margin: 4px 0 10px 2px; }
    .path-warning-icon { font-size: 16px; width: 16px; height: 16px; }
    .env-badge { display: flex; align-items: center; gap: 4px; color: #555; font-size: 12px; margin: 4px 0 10px 2px; }
    .env-badge-icon { font-size: 16px; width: 16px; height: 16px; }
  `],
})
export class SettingsComponent implements OnInit {
  private readonly settingsService = inject(SettingsService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly notify = inject(NotificationService);
  private readonly fb = inject(FormBuilder);

  readonly loading = signal(false);
  readonly saving = signal(false);
  readonly importingSettings = signal(false);
  readonly settingDefs = signal<SettingDef[]>([]);
  readonly fileWarnings = signal<Record<string, string>>({});
  readonly envKeys = signal<Set<string>>(new Set());
  // Computed defaults for unset settings — rendered as input placeholders, NOT
  // as field content: a default shown as content is indistinguishable from a
  // configured value (and used to get persisted by Save all as a side effect).
  readonly defaultValues = signal<Record<string, string>>({});
  showSecrets: Record<string, boolean> = {};

  form = this.fb.group({});

  ngOnInit() {
    this._loadSettings();
  }

  private _loadSettings() {
    this.loading.set(true);
    forkJoin({
      defs: this.settingsService.getDefinitions(),
      values: this.settingsService.getAll(),
    }).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: ({ defs, values }) => {
        this.settingDefs.set(defs);
        for (const def of defs) {
          this.form.addControl(def.key, this.fb.control(def.type === 'boolean' ? '0' : ''));
        }
        const patch: Record<string, string> = {};
        const envSet = new Set<string>();
        const defaults: Record<string, string> = {};
        for (const v of values) {
          if (v.source === 'default') {
            // Unset — show the computed default as a placeholder, keep the
            // control empty so it can never be saved by accident.
            patch[v.key] = '';
            if (v.value) defaults[v.key] = v.value;
          } else {
            patch[v.key] = v.value ?? '';
          }
          if (v.source === 'env') envSet.add(v.key);
        }
        this.defaultValues.set(defaults);
        this.form.patchValue(patch);
        for (const key of envSet) {
          this.form.get(key)?.disable();
        }
        this.envKeys.set(envSet);
        this.loading.set(false);
        this._loadHealth();
      },
      error: () => this.loading.set(false),
    });
  }

  private _loadHealth() {
    // refreshHealth (not getHealth) so the app-level banner updates too.
    this.settingsService.refreshHealth().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (health) => {
        const warnings: Record<string, string> = {};
        for (const issue of health.issues) {
          if (issue.issue === 'not_found') {
            if (issue.key === 'nextflow_profile') {
              warnings[issue.key] = `Profile '${issue.value}' not found in the Nextflow config file`;
            } else {
              warnings[issue.key] = `File not found on disk: ${issue.value}`;
            }
          } else if (issue.issue === 'missing' && issue.key === 'nextflow_profile') {
            warnings[issue.key] = `A profile is set but no Nextflow config file is configured`;
          }
        }
        this.fileWarnings.set(warnings);
      },
    });
  }

  toggleSecret(key: string) {
    this.showSecrets[key] = !this.showSecrets[key];
  }

  saveAll() {
    // Save only fields the user actually touched. The form is pre-filled with
    // computed defaults for unset settings, so saving every field would
    // materialise those defaults into explicit stored values — turning e.g.
    // the optional databases.csv default location into a "configured but
    // missing" health issue the user never asked for. Disabled controls
    // (env-overridden) are excluded as before.
    const entries = Object.entries(this.form.controls as Record<string, AbstractControl>)
      .filter(([, ctrl]) => ctrl.dirty && !ctrl.disabled)
      .map(([key, ctrl]) => [key, ctrl.value as string] as const);
    if (!entries.length) {
      this.notify.message('No changes to save', 3000);
      return;
    }
    this.saving.set(true);
    forkJoin(
      entries.map(([key, val]) =>
        this.settingsService.set(key, val || null).pipe(catchError(() => of(null)))
      )
    ).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        this.saving.set(false);
        this.form.markAsPristine();
        this._loadHealth();
        this.notify.success('Settings saved');
      },
      error: () => this.saving.set(false),
    });
  }


  exportSettingsFile() {
    window.open('/api/settings/file/export', '_blank');
  }

  importSettingsFile(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;

    this.importingSettings.set(true);
    this.settingsService.importFile(file).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (res) => {
        const s = res.summary;
        let msg = res.detail || 'Settings import completed';
        if (s) {
          msg = `${msg} (imported: ${s.imported}, env-skipped: ${s.skipped_env}, invalid-skipped: ${s.skipped_invalid})`;
        }
        this.notify.message(msg, 8000);
        this._loadSettings();
      },
      error: (err) => {
        this.notify.message(
          formatImportFailure(err?.error?.detail, 'Settings import failed'),
          8000,
        );
      },
      complete: () => {
        this.importingSettings.set(false);
        input.value = '';
      },
    });
  }

}

