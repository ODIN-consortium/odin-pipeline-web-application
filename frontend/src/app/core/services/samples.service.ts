import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { Sample, SampleCreate, SampleUpdate } from '../models/sample.model';

@Injectable({ providedIn: 'root' })
export class SamplesService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/samples';

  list(siteId?: string, q?: string): Observable<Sample[]> {
    let params = new HttpParams();
    if (siteId != null) params = params.set('site_id', siteId);
    if (q) params = params.set('q', q);
    return this.http.get<Sample[]>(this.base, { params });
  }

  get(id: string): Observable<Sample> {
    return this.http.get<Sample>(`${this.base}/${id}`);
  }

  create(sample: SampleCreate): Observable<Sample> {
    return this.http.post<Sample>(this.base, sample);
  }

  update(id: string, sample: SampleUpdate): Observable<Sample> {
    return this.http.patch<Sample>(`${this.base}/${id}`, sample);
  }

  delete(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${id}`);
  }
}
