import { TestBed } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { SamplesService } from './samples.service';
import { Sample, SampleCreate, SampleUpdate } from '../models/sample.model';

const SAMPLE: Sample = {
  id: 'uuid-s1',
  sample_code: 'NOBGOPark_water',
  site_id: 'uuid-site-1',
  sample_type: 'water',
  sampling_date: '20240601',
  created_at: '2024-01-01T00:00:00.000Z',
  updated_at: '2024-01-01T00:00:00.000Z',
};

describe('SamplesService', () => {
  let service: SamplesService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [HttpClientTestingModule],
    });
    service = TestBed.inject(SamplesService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  describe('list()', () => {
    it('sends GET /api/samples with no params', () => {
      service.list().subscribe();
      const req = http.expectOne('/api/samples');
      expect(req.request.method).toBe('GET');
      req.flush([SAMPLE]);
    });

    it('sends site_id param when provided', () => {
      service.list('uuid-site-1').subscribe();
      const req = http.expectOne('/api/samples?site_id=uuid-site-1');
      expect(req.request.method).toBe('GET');
      req.flush([SAMPLE]);
    });

    it('sends q param when provided', () => {
      service.list(undefined, 'water').subscribe();
      const req = http.expectOne('/api/samples?q=water');
      expect(req.request.method).toBe('GET');
      req.flush([SAMPLE]);
    });

    it('returns an array of samples', (done) => {
      service.list().subscribe((samples) => {
        expect(samples).toEqual([SAMPLE]);
        done();
      });
      http.expectOne('/api/samples').flush([SAMPLE]);
    });
  });

  describe('get()', () => {
    it('sends GET /api/samples/:id', () => {
      service.get('uuid-s1').subscribe();
      const req = http.expectOne('/api/samples/uuid-s1');
      expect(req.request.method).toBe('GET');
      req.flush(SAMPLE);
    });

    it('returns the sample', (done) => {
      service.get('uuid-s1').subscribe((s) => {
        expect(s.sample_code).toBe('NOBGOPark_water');
        done();
      });
      http.expectOne('/api/samples/uuid-s1').flush(SAMPLE);
    });
  });

  describe('create()', () => {
    it('sends POST /api/samples with payload', () => {
      const payload: SampleCreate = {
        site_id: 'uuid-site-1',
        sample_type: 'water',
        sampling_date: '20240601',
      };
      service.create(payload).subscribe();
      const req = http.expectOne('/api/samples');
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toEqual(payload);
      req.flush(SAMPLE);
    });

    it('returns the created sample', (done) => {
      service.create({ sampling_date: '20240601' } as SampleCreate).subscribe((s) => {
        expect(s.id).toBe('uuid-s1');
        done();
      });
      http.expectOne('/api/samples').flush(SAMPLE);
    });
  });

  describe('update()', () => {
    it('sends PATCH /api/samples/:id', () => {
      const update: SampleUpdate = { depth: '20m' };
      service.update('uuid-s1', update).subscribe();
      const req = http.expectOne('/api/samples/uuid-s1');
      expect(req.request.method).toBe('PATCH');
      expect(req.request.body).toEqual(update);
      req.flush({ ...SAMPLE, depth: '20m' });
    });
  });

  describe('delete()', () => {
    it('sends DELETE /api/samples/:id', () => {
      service.delete('uuid-s1').subscribe();
      const req = http.expectOne('/api/samples/uuid-s1');
      expect(req.request.method).toBe('DELETE');
      req.flush(null);
    });
  });
});
