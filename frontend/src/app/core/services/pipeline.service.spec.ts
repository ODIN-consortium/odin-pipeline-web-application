import { TestBed } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { PipelineService } from './pipeline.service';
import {
  MergeDecisionPayload,
  MergeDecisionStatus,
  PipelineLaunchPayload,
  PipelineRun,
} from '../models/pipeline.model';

const RUN: PipelineRun = {
  id: 'run-uuid-1',
  pipeline_type: 'taxprofiler',
  status: 'queued',
  run_accessions: ['ERR123456'],
  params: { resume: 'false' },
  pid: null,
  log_file: '/tmp/run.log',
  exit_code: null,
  started_at: null,
  finished_at: null,
  output_path: '/tmp/output',
  work_dir: '/tmp/work',
  created_at: '2025-01-01T00:00:00.000Z',
  updated_at: '2025-01-01T00:00:00.000Z',
  created_by: 'test',
  error_hint: null,
  confidence_report_targets: [],
};

const LAUNCH_PAYLOAD: PipelineLaunchPayload = {
  pipeline_type: 'taxprofiler',
  run_accessions: ['ERR123456'],
  resume: false,
};

const MERGE_STATUS: MergeDecisionStatus = {
  run_accession: 'ERR123456',
  decision_made: true,
  auto_merge: true,
  related_runs: [],
};

describe('PipelineService', () => {
  let service: PipelineService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [HttpClientTestingModule],
    });
    service = TestBed.inject(PipelineService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  // ── launch ────────────────────────────────────────────────────────────────

  describe('launch()', () => {
    it('sends POST /api/pipeline/runs with the payload', () => {
      service.launch(LAUNCH_PAYLOAD).subscribe();
      const req = http.expectOne('/api/pipeline/runs');
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toEqual(LAUNCH_PAYLOAD);
      req.flush(RUN, { status: 201, statusText: 'Created' });
    });

    it('returns the created PipelineRun', (done) => {
      service.launch(LAUNCH_PAYLOAD).subscribe((run) => {
        expect(run).toEqual(RUN);
        done();
      });
      http.expectOne('/api/pipeline/runs').flush(RUN, { status: 201, statusText: 'Created' });
    });
  });

  // ── list ──────────────────────────────────────────────────────────────────

  describe('list()', () => {
    it('sends GET /api/pipeline/runs', () => {
      service.list().subscribe();
      const req = http.expectOne('/api/pipeline/runs');
      expect(req.request.method).toBe('GET');
      req.flush([RUN]);
    });

    it('returns an array of runs', (done) => {
      service.list().subscribe((runs) => {
        expect(runs).toEqual([RUN]);
        done();
      });
      http.expectOne('/api/pipeline/runs').flush([RUN]);
    });
  });

  // ── active ────────────────────────────────────────────────────────────────

  describe('active()', () => {
    it('sends GET /api/pipeline/runs/active', () => {
      service.active().subscribe();
      const req = http.expectOne('/api/pipeline/runs/active');
      expect(req.request.method).toBe('GET');
      req.flush(RUN);
    });
  });

  // ── get ───────────────────────────────────────────────────────────────────

  describe('get()', () => {
    it('sends GET /api/pipeline/runs/:id', () => {
      service.get('run-uuid-1').subscribe();
      const req = http.expectOne('/api/pipeline/runs/run-uuid-1');
      expect(req.request.method).toBe('GET');
      req.flush(RUN);
    });

    it('returns the run', (done) => {
      service.get('run-uuid-1').subscribe((r) => {
        expect(r).toEqual(RUN);
        done();
      });
      http.expectOne('/api/pipeline/runs/run-uuid-1').flush(RUN);
    });
  });

  // ── cancel ────────────────────────────────────────────────────────────────

  describe('cancel()', () => {
    it('sends DELETE /api/pipeline/runs/:id', () => {
      service.cancel('run-uuid-1').subscribe();
      const req = http.expectOne('/api/pipeline/runs/run-uuid-1');
      expect(req.request.method).toBe('DELETE');
      req.flush(null, { status: 204, statusText: 'No Content' });
    });
  });

  // ── deleteRecord ──────────────────────────────────────────────────────────

  describe('deleteRecord()', () => {
    it('sends DELETE /api/pipeline/runs/:id/record', () => {
      service.deleteRecord('run-uuid-1').subscribe();
      const req = http.expectOne('/api/pipeline/runs/run-uuid-1/record');
      expect(req.request.method).toBe('DELETE');
      req.flush(null, { status: 204, statusText: 'No Content' });
    });
  });

  // ── deleteWorkdir ─────────────────────────────────────────────────────────

  describe('deleteWorkdir()', () => {
    it('sends DELETE /api/pipeline/runs/:id/workdir', () => {
      service.deleteWorkdir('run-uuid-1').subscribe();
      const req = http.expectOne('/api/pipeline/runs/run-uuid-1/workdir');
      expect(req.request.method).toBe('DELETE');
      req.flush(null, { status: 204, statusText: 'No Content' });
    });
  });

  // ── merge candidates ──────────────────────────────────────────────────────

  describe('getMergeCandidates()', () => {
    it('sends GET /api/pipeline/nanopore/:ra/merge-candidates', () => {
      service.getMergeCandidates('ERR123456').subscribe();
      const req = http.expectOne('/api/pipeline/nanopore/ERR123456/merge-candidates');
      expect(req.request.method).toBe('GET');
      req.flush(MERGE_STATUS);
    });

    it('returns the MergeDecisionStatus', (done) => {
      service.getMergeCandidates('ERR123456').subscribe((s) => {
        expect(s).toEqual(MERGE_STATUS);
        done();
      });
      http.expectOne('/api/pipeline/nanopore/ERR123456/merge-candidates').flush(MERGE_STATUS);
    });
  });

  // ── setMergeDecision ──────────────────────────────────────────────────────

  describe('setMergeDecision()', () => {
    it('sends PUT /api/pipeline/nanopore/:ra/merge-decision', () => {
      const payload: MergeDecisionPayload = { auto_merge: true };
      service.setMergeDecision('ERR123456', payload).subscribe();
      const req = http.expectOne('/api/pipeline/nanopore/ERR123456/merge-decision');
      expect(req.request.method).toBe('PUT');
      expect(req.request.body).toEqual(payload);
      req.flush(null, { status: 204, statusText: 'No Content' });
    });
  });

  // ── clearMergeDecision ────────────────────────────────────────────────────

  describe('clearMergeDecision()', () => {
    it('sends DELETE /api/pipeline/nanopore/:ra/merge-decision', () => {
      service.clearMergeDecision('ERR123456').subscribe();
      const req = http.expectOne('/api/pipeline/nanopore/ERR123456/merge-decision');
      expect(req.request.method).toBe('DELETE');
      req.flush(null, { status: 204, statusText: 'No Content' });
    });
  });
});
