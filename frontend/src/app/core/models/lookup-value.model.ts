export interface LookupValue {
  id: string;
  list: string;
  code: string;
  description?: string | null;
  external_code?: string | null;
}

export type LookupValueRead = LookupValue;

export interface LookupValueCreate {
  code: string;
  description?: string;
  external_code?: string;
}

export interface LookupValueUpdate {
  // Nullable: an explicit null clears the field (an omitted key leaves it unchanged).
  description?: string | null;
  external_code?: string | null;
}
