import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import {
  BiomemeDiscoveryResult,
  ExcludePayload,
  NanoporeDiscoveryResult,
  NanoporeReadiness,
  NanoporeRegisterPayload,
  ReplicateMessages,
} from '../models/discovery.model';
import { MinknowRunInfo } from '../models/minknow-run-info.model';

@Injectable({ providedIn: 'root' })
export class DiscoveryService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/discovery';

  nanopore(forceRefresh = false): Observable<NanoporeDiscoveryResult> {
    const q = forceRefresh ? '?force_refresh=true' : '';
    return this.http.get<NanoporeDiscoveryResult>(`${this.base}/nanopore${q}`);
  }

  biomeme(forceRefresh = false): Observable<BiomemeDiscoveryResult> {
    const q = forceRefresh ? '?force_refresh=true' : '';
    return this.http.get<BiomemeDiscoveryResult>(`${this.base}/biomeme${q}`);
  }

  /** Replicate notes and warnings per run accession; runs with nothing to say are absent. */
  nanoporeReplicateMessages(): Observable<Record<string, ReplicateMessages>> {
    return this.http.get<Record<string, ReplicateMessages>>(
      `${this.base}/nanopore/replicate-messages`,
    );
  }

  nanoporeReadiness(runAccession: string): Observable<NanoporeReadiness> {
    return this.http.get<NanoporeReadiness>(`${this.base}/nanopore/${runAccession}/readiness`);
  }

  getConfidenceReport(runAccession: string, target: string): Observable<string> {
    return this.http.get(
      `${this.base}/nanopore/${encodeURIComponent(runAccession)}/confidence-report?target=${encodeURIComponent(target)}`,
      { responseType: 'text' },
    );
  }

  /** Lightweight MinKNOW metadata for a run — 404 when run not on disk. */
  getRunInfo(runAccession: string): Observable<MinknowRunInfo> {
    return this.http.get<MinknowRunInfo>(
      `${this.base}/nanopore/run-info?run_accession=${encodeURIComponent(runAccession)}`,
    );
  }

  nanoporeRegister(
    runAccession: string,
    payload: NanoporeRegisterPayload,
  ): Observable<NanoporeReadiness> {
    return this.http.post<NanoporeReadiness>(
      `${this.base}/nanopore/${runAccession}/register`,
      payload,
    );
  }

  excludeRun(runAccession: string, payload: ExcludePayload = {}): Observable<void> {
    return this.http.put<void>(`${this.base}/nanopore/${runAccession}/exclude`, payload);
  }

  unexcludeRun(runAccession: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/nanopore/${runAccession}/exclude`);
  }

  excludeBarcode(
    runAccession: string,
    barcode: string,
    payload: ExcludePayload = {},
  ): Observable<void> {
    return this.http.put<void>(
      `${this.base}/nanopore/${runAccession}/barcodes/${barcode}/exclude`,
      payload,
    );
  }

  unexcludeBarcode(runAccession: string, barcode: string): Observable<void> {
    return this.http.delete<void>(
      `${this.base}/nanopore/${runAccession}/barcodes/${barcode}/exclude`,
    );
  }

  excludeBiomemeFolder(folderPath: string, payload: ExcludePayload = {}): Observable<void> {
    return this.http.put<void>(
      `${this.base}/biomeme/folders/${encodeURIComponent(folderPath)}/exclude`,
      payload,
    );
  }

  unexcludeBiomemeFolder(folderPath: string): Observable<void> {
    return this.http.delete<void>(
      `${this.base}/biomeme/folders/${encodeURIComponent(folderPath)}/exclude`,
    );
  }
}
