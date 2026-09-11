import { TestBed } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { BiomemeRunsService } from './biomeme-runs.service';
import { BiomemeRun, BiomemeRunCreate, BiomemeRunUpdate } from '../models/biomeme-run.model';

const RUN: BiomemeRun = {
  id: 'uuid-b1',
  biomeme_run_name: 'BIO_RUN_001',
  sample_id: 'uuid-s1',
  sample_code: 'NOBGOPark_water',
  sampling_date: '20240601',
  biomeme_sample_id: 'BS-001',
  dilution_factor: 10,
  created_at: '2024-01-01T00:00:00.000Z',
  updated_at: '2024-01-01T00:00:00.000Z',
};

describe('BiomemeRunsService', () => {
  let service: BiomemeRunsService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [HttpClientTestingModule],
    });
    service = TestBed.inject(BiomemeRunsService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  describe('list()', () => {
    it('sends GET /api/biomeme-runs with no params', () => {
      service.list().subscribe();
      const req = http.expectOne('/api/biomeme-runs');
      expect(req.request.method).toBe('GET');
      req.flush([RUN]);
    });

    it('sends site_id param when provided', () => {
      service.list('uuid-site-1').subscribe();
      const req = http.expectOne('/api/biomeme-runs?site_id=uuid-site-1');
      expect(req.request.method).toBe('GET');
      req.flush([RUN]);
    });

    it('returns an array of biomeme runs', (done) => {
      service.list().subscribe((runs) => {
        expect(runs).toEqual([RUN]);
        done();
      });
      http.expectOne('/api/biomeme-runs').flush([RUN]);
    });
  });

  describe('get()', () => {
    it('sends GET /api/biomeme-runs/:id', () => {
      service.get('uuid-b1').subscribe();
      const req = http.expectOne('/api/biomeme-runs/uuid-b1');
      expect(req.request.method).toBe('GET');
      req.flush(RUN);
    });

    it('returns the biomeme run', (done) => {
      service.get('uuid-b1').subscribe((r) => {
        expect(r.biomeme_run_name).toBe('BIO_RUN_001');
        done();
      });
      http.expectOne('/api/biomeme-runs/uuid-b1').flush(RUN);
    });
  });

  describe('create()', () => {
    it('sends POST /api/biomeme-runs with payload', () => {
      const payload: BiomemeRunCreate = {
        biomeme_run_name: 'BIO_RUN_001',
        sampling_date: '20240601',
      };
      service.create(payload).subscribe();
      const req = http.expectOne('/api/biomeme-runs');
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toEqual(payload);
      req.flush(RUN);
    });

    it('returns the created run', (done) => {
      service.create({ biomeme_run_name: 'BIO_001', sampling_date: '20240601' }).subscribe((r) => {
        expect(r.id).toBe('uuid-b1');
        done();
      });
      http.expectOne('/api/biomeme-runs').flush(RUN);
    });
  });

  describe('update()', () => {
    it('sends PATCH /api/biomeme-runs/:id', () => {
      const update: BiomemeRunUpdate = { comments: 'Re-run' };
      service.update('uuid-b1', update).subscribe();
      const req = http.expectOne('/api/biomeme-runs/uuid-b1');
      expect(req.request.method).toBe('PATCH');
      expect(req.request.body).toEqual(update);
      req.flush({ ...RUN, comments: 'Re-run' });
    });
  });

  describe('delete()', () => {
    it('sends DELETE /api/biomeme-runs/:id', () => {
      service.delete('uuid-b1').subscribe();
      const req = http.expectOne('/api/biomeme-runs/uuid-b1');
      expect(req.request.method).toBe('DELETE');
      req.flush(null);
    });
  });
});
