import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import {
  DatabaseEntry,
  DatabaseEntryCreate,
  DatabaseEntryUpdate,
  EffectiveDatabasesResult,
} from '../models/database-entry.model';

@Injectable({ providedIn: 'root' })
export class DatabasesService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/databases';

  list(): Observable<DatabaseEntry[]> {
    return this.http.get<DatabaseEntry[]>(this.base);
  }

  listEffective(): Observable<EffectiveDatabasesResult> {
    return this.http.get<EffectiveDatabasesResult>(`${this.base}/effective`);
  }

  create(payload: DatabaseEntryCreate): Observable<DatabaseEntry> {
    return this.http.post<DatabaseEntry>(this.base, payload);
  }

  update(id: string, payload: DatabaseEntryUpdate): Observable<DatabaseEntry> {
    return this.http.put<DatabaseEntry>(`${this.base}/${id}`, payload);
  }

  delete(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${id}`);
  }
}
