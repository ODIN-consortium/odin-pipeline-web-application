import { Clearable } from './update.model';
export interface Sample {
  id: string;
  sample_code: string;
  site_id?: string;
  sample_type?: string;
  depth?: string;
  elevation?: string;
  sampling_date: string;
  comments_sampling?: string;
  partner_sample_code?: string;
  date_extraction?: string;
  nucleic_acid_concentration?: string;
  extract_volume?: string;
  comments_extraction?: string;
  elution_volume?: string;
  comments?: string;
  created_at?: string;
  updated_at?: string;
  created_by?: string;
  updated_by?: string;
}

export type SampleCreate = Omit<Sample, 'id' | 'sample_code' | 'created_at' | 'updated_at'>;
export type SampleUpdate = Clearable<SampleCreate>;
