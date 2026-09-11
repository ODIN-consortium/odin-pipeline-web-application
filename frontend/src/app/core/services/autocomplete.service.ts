import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { RunDefaults } from '../models/config-value.model';

@Injectable({ providedIn: 'root' })
export class AutocompleteService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/autocomplete';

  siteIds(): Observable<string[]> {
    return this.http.get<string[]>(`${this.base}/site-ids`);
  }

  countryCodes(): Observable<string[]> {
    return this.http.get<string[]>(`${this.base}/country-codes`);
  }

  protocolIds(): Observable<string[]> {
    return this.http.get<string[]>(`${this.base}/protocol-ids`);
  }

  sequencingKitIds(): Observable<string[]> {
    return this.http.get<string[]>(`${this.base}/sequencing-kit-ids`);
  }

  lastRunDefaults(): Observable<RunDefaults> {
    return this.http.get<RunDefaults>(`${this.base}/last-run-defaults`);
  }
}
