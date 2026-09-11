import { Clearable } from './update.model';
export interface DiscoveredBiomemeRun {
  biomeme_run_name: string;
  country_code: string;
  sampling_date: string;   // YYYYMMDD folder name
  file_path: string;
  registered: boolean;
  run_id?: string | null;
  sample_id?: string | null;
  sample_code?: string | null;
}

export interface BiomemeLaunchResult {
  id: string;
  status: string;
  log_file?: string | null;
  created_at: string;
}

export interface BiomemeRun {
  id: string;
  biomeme_run_name: string;
  sample_id?: string; // FK → samples.id (UUID)
  sample_code?: string; // derived from sample relationship
  sampling_date: string;
  biomeme_sample_id?: string;
  dilution_factor?: number;
  comments?: string;
  created_at?: string;
  updated_at?: string;
  created_by?: string;
  updated_by?: string;
}

export type BiomemeRunCreate = Omit<BiomemeRun, 'id' | 'created_at' | 'updated_at' | 'sample_code'>;
export type BiomemeRunUpdate = Clearable<BiomemeRunCreate>;
