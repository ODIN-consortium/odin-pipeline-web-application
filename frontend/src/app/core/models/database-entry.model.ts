export interface DatabaseEntry {
  id: string;
  tool: string;
  db_name: string;
  db_params: string | null;
  db_path: string;
  created_at: string;
  updated_at: string;
  created_by: string | null;
}

export interface DatabaseEntryCreate {
  tool: string;
  db_name: string;
  db_params?: string;
  db_path: string;
  created_by?: string;
}

export interface DatabaseEntryUpdate {
  tool?: string;
  db_name?: string;
  db_params?: string | null;
  db_path?: string;
}

export interface EffectiveDatabaseRow {
  tool: string;
  db_name: string;
  db_params: string;
  db_path: string;
}

export interface EffectiveDatabasesResult {
  source: 'file' | 'db';
  file: string | null;
  entries: EffectiveDatabaseRow[];
}
