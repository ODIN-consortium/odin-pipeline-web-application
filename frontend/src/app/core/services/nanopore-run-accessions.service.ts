import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  NanoporeRunAccession,
  NanoporeRunAccessionCreate,
  NanoporeRunAccessionUpdate,
} from '../models/run.model';

@Injectable({ providedIn: 'root' })
export class NanoporeRunAccessionsService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/nanopore-run-accessions';

  list(pending?: boolean): Observable<NanoporeRunAccession[]> {
    let params = new HttpParams();
    if (pending !== undefined) params = params.set('pending', String(pending));
    return this.http.get<NanoporeRunAccession[]>(this.base, { params });
  }

  get(id: string): Observable<NanoporeRunAccession> {
    return this.http.get<NanoporeRunAccession>(`${this.base}/${id}`);
  }

  create(data: NanoporeRunAccessionCreate): Observable<NanoporeRunAccession> {
    return this.http.post<NanoporeRunAccession>(this.base, data);
  }

  update(id: string, data: NanoporeRunAccessionUpdate): Observable<NanoporeRunAccession> {
    return this.http.patch<NanoporeRunAccession>(`${this.base}/${id}`, data);
  }

  link(
    id: string,
    run_accession: string,
    barcode_ids?: string[],
  ): Observable<NanoporeRunAccession> {
    return this.http.post<NanoporeRunAccession>(`${this.base}/${id}/link`, {
      run_accession,
      barcode_ids,
    });
  }

  delete(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${id}`);
  }
}
