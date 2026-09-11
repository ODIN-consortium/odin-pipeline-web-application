import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { NanoporeRun, NanoporeRunCreate, NanoporeRunUpdate } from '../models/run.model';

@Injectable({ providedIn: 'root' })
export class RunsService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/nanopore-runs';

  list(runAccession?: string, sampleCode?: string): Observable<NanoporeRun[]> {
    let params = new HttpParams();
    if (runAccession) params = params.set('run_accession', runAccession);
    if (sampleCode) params = params.set('sample_code', sampleCode);
    return this.http.get<NanoporeRun[]>(this.base, { params });
  }

  get(id: string): Observable<NanoporeRun> {
    return this.http.get<NanoporeRun>(`${this.base}/${id}`);
  }

  create(run: NanoporeRunCreate): Observable<NanoporeRun> {
    return this.http.post<NanoporeRun>(this.base, run);
  }

  update(id: string, run: NanoporeRunUpdate): Observable<NanoporeRun> {
    return this.http.patch<NanoporeRun>(`${this.base}/${id}`, run);
  }

  delete(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${id}`);
  }
}
