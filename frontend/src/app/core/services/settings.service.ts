import { Injectable, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, tap } from 'rxjs';
import { ConfigValue, SettingDef, SettingIssue, SettingsHealth } from '../models/config-value.model';

export interface SettingsImportResult {
  detail: string;
  summary?: { imported: number; skipped_env: number; skipped_invalid: number };
}

@Injectable({ providedIn: 'root' })
export class SettingsService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/settings';

  getAll(): Observable<ConfigValue[]> {
    return this.http.get<ConfigValue[]>(this.base);
  }

  get(key: string): Observable<ConfigValue> {
    return this.http.get<ConfigValue>(`${this.base}/${key}`);
  }

  set(key: string, value: string | null): Observable<ConfigValue> {
    return this.http.put<ConfigValue>(`${this.base}/${key}`, { value });
  }

  // Shared health state: the app-level banner reads this signal, and every
  // refreshHealth() call updates it — so the banner reflects the latest check
  // instead of the one from app start.
  private readonly _healthIssues = signal<SettingIssue[]>([]);
  readonly healthIssues = this._healthIssues.asReadonly();

  getHealth(): Observable<SettingsHealth> {
    return this.http.get<SettingsHealth>(`${this.base}/health`);
  }

  /** Fetch health AND publish it to healthIssues. Use this instead of
      getHealth() wherever the result should also update the banner. */
  refreshHealth(): Observable<SettingsHealth> {
    return this.getHealth().pipe(tap((h) => this._healthIssues.set(h.issues)));
  }

  getDefinitions(): Observable<SettingDef[]> {
    return this.http.get<SettingDef[]>(`${this.base}/definitions`);
  }

  importFile(file: File): Observable<SettingsImportResult> {
    const formData = new FormData();
    formData.append('file', file);
    return this.http.post<SettingsImportResult>(`${this.base}/file/import`, formData);
  }
}
