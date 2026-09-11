import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  SyncPreview,
  SyncApplyRequest,
  SyncApplyResult,
  SyncRestoreResult,
} from '../models/sync.model';

@Injectable({ providedIn: 'root' })
export class SyncService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/sync';

  /**
   * Trigger a file download of the full database snapshot.
   * Uses a hidden anchor click so the browser handles the Save dialog.
   */
  /**
   * Fetch the snapshot as a blob.
   *
   * Unlike {@link exportData}, this goes through HttpClient so the caller can tell whether the
   * data actually arrived. That matters for the automatic backup taken before an import: a
   * backup that silently failed is worse than none, because it is believed in.
   */
  fetchSnapshot(): Observable<Blob> {
    return this.http.get(`${this.base}/export`, { responseType: 'blob' });
  }

  exportData(): void {
    const a = document.createElement('a');
    a.href = `${this.base}/export`;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  /**
   * Make the database match a snapshot: write everything in it, delete everything else.
   *
   * The counterpart to {@link apply}, not a variant of it. A merge adds the other device's
   * information and never deletes; a restore puts this device back to a recorded state, so it
   * must. Separate methods because the consequences are opposite.
   */
  restore(snapshot: unknown): Observable<SyncRestoreResult> {
    return this.http.post<SyncRestoreResult>(`${this.base}/restore`, snapshot);
  }

  /** Preview what changes would result from merging the given import data. */
  preview(importData: object): Observable<SyncPreview> {
    return this.http.post<SyncPreview>(`${this.base}/preview`, importData);
  }

  /** Apply the given decisions from a preview. */
  apply(request: SyncApplyRequest): Observable<SyncApplyResult> {
    return this.http.post<SyncApplyResult>(`${this.base}/apply`, request);
  }
}
