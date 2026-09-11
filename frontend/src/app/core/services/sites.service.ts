import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { Site, SiteCreate, SiteUpdate } from '../models/site.model';

@Injectable({ providedIn: 'root' })
export class SitesService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/sites';

  list(q?: string): Observable<Site[]> {
    let params = new HttpParams();
    if (q) params = params.set('q', q);
    return this.http.get<Site[]>(this.base, { params });
  }

  get(id: string): Observable<Site> {
    return this.http.get<Site>(`${this.base}/${id}`);
  }

  create(site: SiteCreate): Observable<Site> {
    return this.http.post<Site>(this.base, site);
  }

  update(id: string, site: SiteUpdate): Observable<Site> {
    return this.http.patch<Site>(`${this.base}/${id}`, site);
  }

  delete(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${id}`);
  }
}
