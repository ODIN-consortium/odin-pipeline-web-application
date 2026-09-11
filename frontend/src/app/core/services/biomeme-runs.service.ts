import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  BiomemeRun,
  BiomemeRunCreate,
  BiomemeRunUpdate,
  BiomemeLaunchResult,
  DiscoveredBiomemeRun,
} from '../models/biomeme-run.model';

@Injectable({ providedIn: 'root' })
export class BiomemeRunsService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/biomeme-runs';
  private readonly biomemeBase = '/api/biomeme';

  list(siteId?: string): Observable<BiomemeRun[]> {
    let params = new HttpParams();
    if (siteId != null) params = params.set('site_id', siteId);
    return this.http.get<BiomemeRun[]>(this.base, { params });
  }

  get(id: string): Observable<BiomemeRun> {
    return this.http.get<BiomemeRun>(`${this.base}/${id}`);
  }

  create(run: BiomemeRunCreate): Observable<BiomemeRun> {
    return this.http.post<BiomemeRun>(this.base, run);
  }

  update(id: string, run: BiomemeRunUpdate): Observable<BiomemeRun> {
    return this.http.patch<BiomemeRun>(`${this.base}/${id}`, run);
  }

  delete(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${id}`);
  }

  /** Scan biomeme_input_data directory and return annotated file list. */
  discover(): Observable<DiscoveredBiomemeRun[]> {
    return this.http.get<DiscoveredBiomemeRun[]>(`${this.biomemeBase}/discover`);
  }

  /** Bulk-create biomeme_runs rows for the given run names. Idempotent. */
  register(
    biomeme_run_names: string[],
    per_run_sample_ids?: Record<string, string>,
  ): Observable<{ created: string[]; skipped: string[] }> {
    return this.http.post<{ created: string[]; skipped: string[] }>(
      `${this.biomemeBase}/register`,
      { biomeme_run_names, per_run_sample_ids: per_run_sample_ids ?? null },
    );
  }

  /** Launch the biomeme processing script as a background job. */
  launch(createdBy?: string): Observable<BiomemeLaunchResult> {
    return this.http.post<BiomemeLaunchResult>(`${this.biomemeBase}/launch`, {
      created_by: createdBy ?? null,
    });
  }
}
