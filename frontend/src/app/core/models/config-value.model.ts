export interface ConfigValue {
  key: string;
  value: string | null;
  updated_at: string;
  // 'env' = forced by environment variable (rendered read-only);
  // 'default' = computed default, nothing stored (rendered as placeholder);
  // null/absent = explicitly stored value.
  source?: 'env' | 'default' | null;
}

export interface RunDefaults {
  site_id?: string;
  protocol_id?: string;
  sequencing_kit_id?: string;
}

export interface SettingIssue {
  key: string;
  label: string;
  issue: 'missing' | 'create_failed' | 'not_found' | 'warning';
  value: string | null;
}

export interface SettingsHealth {
  issues: SettingIssue[];
}

export interface SelectOption {
  value: string;
  label: string;
}

export interface SettingDef {
  key: string;
  label: string;
  hint: string;
  type?: 'text' | 'password' | 'number' | 'boolean' | 'select';
  required?: boolean;
  default_suffix?: string;
  options?: SelectOption[];
}
