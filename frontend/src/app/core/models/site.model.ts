import { Clearable } from './update.model';
export interface Site {
  id: string;
  site_code: string;
  site?: string;
  country: string;
  country_code?: string;
  city_code?: string;
  city?: string;
  location?: string;
  longitude?: number;
  latitude?: number;
  comments?: string;
  created_at?: string;
  updated_at?: string;
  created_by?: string;
  updated_by?: string;
}

export type SiteCreate = Omit<Site, 'id' | 'site_code' | 'created_at' | 'updated_at'>;
export type SiteUpdate = Clearable<SiteCreate>;
