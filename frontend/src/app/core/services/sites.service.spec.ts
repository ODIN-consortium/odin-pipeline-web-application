import { TestBed } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { SitesService } from './sites.service';
import { Site, SiteCreate, SiteUpdate } from '../models/site.model';

const SITE: Site = {
  id: 'uuid-1',
  site_code: 'NOBGOPark',
  country: 'Norway',
  country_code: 'NO',
  city_code: 'BGO',
  site: 'Park',
  city: 'Bergen',
  created_at: '2024-01-01T00:00:00.000Z',
  updated_at: '2024-01-01T00:00:00.000Z',
};

describe('SitesService', () => {
  let service: SitesService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [HttpClientTestingModule],
    });
    service = TestBed.inject(SitesService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  describe('list()', () => {
    it('sends GET /api/sites', () => {
      service.list().subscribe();
      const req = http.expectOne('/api/sites');
      expect(req.request.method).toBe('GET');
      req.flush([SITE]);
    });

    it('sends q param when provided', () => {
      service.list('BGO').subscribe();
      const req = http.expectOne('/api/sites?q=BGO');
      expect(req.request.method).toBe('GET');
      req.flush([SITE]);
    });

    it('returns an array of sites', (done) => {
      service.list().subscribe((sites) => {
        expect(sites).toEqual([SITE]);
        done();
      });
      http.expectOne('/api/sites').flush([SITE]);
    });
  });

  describe('get()', () => {
    it('sends GET /api/sites/:id', () => {
      service.get('uuid-1').subscribe();
      const req = http.expectOne('/api/sites/uuid-1');
      expect(req.request.method).toBe('GET');
      req.flush(SITE);
    });

    it('returns the site', (done) => {
      service.get('uuid-1').subscribe((site) => {
        expect(site).toEqual(SITE);
        done();
      });
      http.expectOne('/api/sites/uuid-1').flush(SITE);
    });
  });

  describe('create()', () => {
    it('sends POST /api/sites with payload', () => {
      const payload: SiteCreate = {
        country: 'Norway',
        country_code: 'NO',
        city_code: 'BGO',
        site: 'Park',
        city: 'Bergen',
      };
      service.create(payload).subscribe();
      const req = http.expectOne('/api/sites');
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toEqual(payload);
      req.flush(SITE);
    });

    it('returns the created site', (done) => {
      service.create({ country: 'Norway' } as SiteCreate).subscribe((s) => {
        expect(s.id).toBe('uuid-1');
        done();
      });
      http.expectOne('/api/sites').flush(SITE);
    });
  });

  describe('update()', () => {
    it('sends PATCH /api/sites/:id with partial payload', () => {
      const update: SiteUpdate = { city: 'Bergen Updated' };
      service.update('uuid-1', update).subscribe();
      const req = http.expectOne('/api/sites/uuid-1');
      expect(req.request.method).toBe('PATCH');
      expect(req.request.body).toEqual(update);
      req.flush({ ...SITE, city: 'Bergen Updated' });
    });
  });

  describe('delete()', () => {
    it('sends DELETE /api/sites/:id', () => {
      service.delete('uuid-1').subscribe();
      const req = http.expectOne('/api/sites/uuid-1');
      expect(req.request.method).toBe('DELETE');
      req.flush(null);
    });
  });
});
