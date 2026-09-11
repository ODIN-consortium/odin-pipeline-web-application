import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, shareReplay, tap } from 'rxjs';
import { LookupValue, LookupValueCreate, LookupValueUpdate } from '../models/lookup-value.model';

@Injectable({ providedIn: 'root' })
export class LookupValuesService {
  private readonly http = inject(HttpClient);
  private readonly cache = new Map<string, Observable<LookupValue[]>>();

  getList(listName: string): Observable<LookupValue[]> {
    return this.http.get<LookupValue[]>(`/api/lookup-values/${listName}`);
  }

  invalidate(listName: string): void {
    if (listName === 'country_code') this.cache.delete('_country_codes');
    else if (listName === 'city_code') this.cache.delete('_city_codes');
  }

  create(listName: string, body: LookupValueCreate): Observable<LookupValue> {
    return this.http.post<LookupValue>(`/api/lookup-values/${listName}`, body);
  }

  update(listName: string, id: string, body: LookupValueUpdate): Observable<LookupValue> {
    return this.http.put<LookupValue>(`/api/lookup-values/${listName}/${id}`, body);
  }

  delete(listName: string, id: string): Observable<void> {
    return this.http.delete<void>(`/api/lookup-values/${listName}/${id}`);
  }

  /** Country codes: existing site values merged with all ISO alpha-2 codes. */
  countryCodes(): Observable<LookupValue[]> {
    return this._cached('_country_codes', '/api/autocomplete/country-codes');
  }

  /** City codes: distinct values from existing sites with city name as description. */
  cityCodes(): Observable<LookupValue[]> {
    return this._cached('_city_codes', '/api/autocomplete/city-codes');
  }

  private _cached(key: string, url: string): Observable<LookupValue[]> {
    if (!this.cache.has(key)) {
      this.cache.set(
        key,
        this.http.get<LookupValue[]>(url).pipe(
          tap({ error: () => this.cache.delete(key) }),
          shareReplay(1),
        ),
      );
    }
    return this.cache.get(key)!;
  }
}
