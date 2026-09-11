import { TestBed } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { RunsService } from './runs.service';
import { NanoporeRun, NanoporeRunCreate, NanoporeRunUpdate } from '../models/run.model';

const RUN: NanoporeRun = {
  id: 'uuid-r1',
  run_accession: 'ERR123456',
  sample_id: 'uuid-s1',
  sample_code: 'NOBGOPark_water',
  sampling_date: '20240601',
  barcode: 'BC01',
  minknow_sample_id: 'NOBGOPark_water_SQK-LSK114_SQK-LSK114',
  alias: 'NOBGOPark_water_BC01',
  protocol_id: 'SQK-LSK114',
  sequencing_kit_id: 'SQK-LSK114',
  type: 'GridION',
  created_at: '2024-01-01T00:00:00.000Z',
  updated_at: '2024-01-01T00:00:00.000Z',
};

describe('RunsService', () => {
  let service: RunsService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [HttpClientTestingModule],
    });
    service = TestBed.inject(RunsService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  describe('list()', () => {
    it('sends GET /api/nanopore-runs with no params', () => {
      service.list().subscribe();
      const req = http.expectOne('/api/nanopore-runs');
      expect(req.request.method).toBe('GET');
      req.flush([RUN]);
    });

    it('sends run_accession param when provided', () => {
      service.list('ERR123456').subscribe();
      const req = http.expectOne('/api/nanopore-runs?run_accession=ERR123456');
      expect(req.request.method).toBe('GET');
      req.flush([RUN]);
    });

    it('sends sample_code param when provided', () => {
      service.list(undefined, 'NOBGOPark_water').subscribe();
      const req = http.expectOne('/api/nanopore-runs?sample_code=NOBGOPark_water');
      expect(req.request.method).toBe('GET');
      req.flush([RUN]);
    });

    it('returns an array of runs', (done) => {
      service.list().subscribe((runs) => {
        expect(runs).toEqual([RUN]);
        done();
      });
      http.expectOne('/api/nanopore-runs').flush([RUN]);
    });
  });

  describe('get()', () => {
    it('sends GET /api/nanopore-runs/:id', () => {
      service.get('uuid-r1').subscribe();
      const req = http.expectOne('/api/nanopore-runs/uuid-r1');
      expect(req.request.method).toBe('GET');
      req.flush(RUN);
    });

    it('returns the run', (done) => {
      service.get('uuid-r1').subscribe((r) => {
        expect(r.alias).toBe('NOBGOPark_water_BC01');
        done();
      });
      http.expectOne('/api/nanopore-runs/uuid-r1').flush(RUN);
    });
  });

  describe('create()', () => {
    it('sends POST /api/nanopore-runs with payload', () => {
      const payload: NanoporeRunCreate = {
        run_accession: 'ERR123456',
        barcode: 'BC01',
      };
      service.create(payload).subscribe();
      const req = http.expectOne('/api/nanopore-runs');
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toEqual(payload);
      req.flush(RUN);
    });

    it('returns the created run', (done) => {
      service
        .create({ run_accession: 'ERR1', barcode: 'BC01' })
        .subscribe((r) => {
          expect(r.id).toBe('uuid-r1');
          done();
        });
      http.expectOne('/api/nanopore-runs').flush(RUN);
    });
  });

  describe('update()', () => {
    it('sends PATCH /api/nanopore-runs/:id', () => {
      const update: NanoporeRunUpdate = { comments: 'Updated' };
      service.update('uuid-r1', update).subscribe();
      const req = http.expectOne('/api/nanopore-runs/uuid-r1');
      expect(req.request.method).toBe('PATCH');
      expect(req.request.body).toEqual(update);
      req.flush({ ...RUN, comments: 'Updated' });
    });
  });

  describe('delete()', () => {
    it('sends DELETE /api/nanopore-runs/:id', () => {
      service.delete('uuid-r1').subscribe();
      const req = http.expectOne('/api/nanopore-runs/uuid-r1');
      expect(req.request.method).toBe('DELETE');
      req.flush(null);
    });
  });
});
