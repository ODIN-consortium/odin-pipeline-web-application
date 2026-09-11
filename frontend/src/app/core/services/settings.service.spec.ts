import { TestBed } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { SettingsService } from './settings.service';
import { ConfigValue } from '../models/config-value.model';

const SETTING: ConfigValue = {
  key: 'output_dir',
  value: '/opt/odin/output',
  updated_at: '2025-01-01T00:00:00.000Z',
};

const ALL_SETTINGS: ConfigValue[] = [
  { key: 'output_dir', value: '/opt/odin/output', updated_at: '2025-01-01T00:00:00.000Z' },
  { key: 'minknow_dir', value: '/mnt/data/minknow', updated_at: '2025-01-01T00:00:00.000Z' },
];

describe('SettingsService', () => {
  let service: SettingsService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [HttpClientTestingModule],
    });
    service = TestBed.inject(SettingsService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  // ── getAll ────────────────────────────────────────────────────────────────

  describe('getAll()', () => {
    it('sends GET /api/settings', () => {
      service.getAll().subscribe();
      const req = http.expectOne('/api/settings');
      expect(req.request.method).toBe('GET');
      req.flush(ALL_SETTINGS);
    });

    it('returns an array of ConfigValues', (done) => {
      service.getAll().subscribe((settings) => {
        expect(settings).toEqual(ALL_SETTINGS);
        done();
      });
      http.expectOne('/api/settings').flush(ALL_SETTINGS);
    });
  });

  // ── get ───────────────────────────────────────────────────────────────────

  describe('get()', () => {
    it('sends GET /api/settings/:key', () => {
      service.get('output_dir').subscribe();
      const req = http.expectOne('/api/settings/output_dir');
      expect(req.request.method).toBe('GET');
      req.flush(SETTING);
    });

    it('returns the ConfigValue', (done) => {
      service.get('output_dir').subscribe((s) => {
        expect(s).toEqual(SETTING);
        done();
      });
      http.expectOne('/api/settings/output_dir').flush(SETTING);
    });
  });

  // ── set ───────────────────────────────────────────────────────────────────

  describe('set()', () => {
    it('sends PUT /api/settings/:key with the value', () => {
      service.set('output_dir', '/new/path').subscribe();
      const req = http.expectOne('/api/settings/output_dir');
      expect(req.request.method).toBe('PUT');
      expect(req.request.body).toEqual({ value: '/new/path' });
      req.flush({ ...SETTING, value: '/new/path' });
    });

    it('sends null value when clearing a setting', () => {
      service.set('output_dir', null).subscribe();
      const req = http.expectOne('/api/settings/output_dir');
      expect(req.request.body).toEqual({ value: null });
      req.flush({ ...SETTING, value: null });
    });

    it('returns the updated ConfigValue', (done) => {
      const updated = { ...SETTING, value: '/new/path' };
      service.set('output_dir', '/new/path').subscribe((s) => {
        expect(s).toEqual(updated);
        done();
      });
      http.expectOne('/api/settings/output_dir').flush(updated);
    });
  });
});
