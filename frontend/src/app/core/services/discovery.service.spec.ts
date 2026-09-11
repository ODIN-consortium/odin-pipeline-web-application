import { TestBed } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { DiscoveryService } from './discovery.service';
import { NanoporeDiscoveryResult, BiomemeDiscoveryResult } from '../models/discovery.model';

const BARCODE_DEFAULTS = {
  sample_id: null,
  sample_code: null,
  sampling_date: null,
  protocol_id: null,
  sequencing_kit_id: null,
  minknow_sample_id: null,
  is_excluded: false,
};

const RUN_DEFAULTS = {
  run_path: null,
  run_name: null,
  sample_name: null,
  is_excluded: false,
  related_run_accessions: [],
  auto_merge: null,
  last_pipeline_run_status: null,
  last_pipeline_run_id: null,
  last_pipeline_run_type: null,
  last_pipeline_run_extract_target: null,
  pipeline_runs: [],
  confidence_report_targets: [],
  output_on_disk: false,
  artic_on_disk: false,
  postprocessing_fresh: null,
  metadata_warnings: [],
  continuation_run_accessions: [],
  continuation_confidence: null,
  continuation_evidence: null,
};

const NANOPORE_RESULT: NanoporeDiscoveryResult = {
  minknow_dir: '/data/minknow',
  scanned_at: '2024-06-01T10:00:00.000Z',
  runs: [
    {
      ...RUN_DEFAULTS,
      run_accession: 'ERR123456',
      in_metadata: true,
      on_disk: true,
      barcodes: [
        { ...BARCODE_DEFAULTS, barcode: 'barcode01', in_metadata: true, fastq_count: 120, status: 'ready' },
        { ...BARCODE_DEFAULTS, barcode: 'barcode02', in_metadata: true, fastq_count: 0, status: 'no_files' },
      ],
      status: 'partial',
    },
    {
      ...RUN_DEFAULTS,
      run_accession: 'ERR999999',
      in_metadata: false,
      on_disk: true,
      barcodes: [
        { ...BARCODE_DEFAULTS, barcode: 'barcode01', in_metadata: false, fastq_count: 45, status: 'not_in_metadata' },
      ],
      status: 'not_in_metadata',
    },
  ],
};

const BIOMEME_RESULT: BiomemeDiscoveryResult = {
  biomeme_dir: '/data/biomeme',
  scanned_at: '2024-06-01T10:00:00.000Z',
  folders: [
    {
      folder_path: 'NO/20240601',
      country_code: 'NO',
      folder_date: '20240601',
      file_count: 1,
      registered_count: 1,
      is_excluded: false,
      status: 'ready',
      files: [
        { run_name: 'run-2024-06-01', registered: true, sample_code: 'NOBGOPark_water', sampling_date: '20240601' },
      ],
    },
    {
      folder_path: 'NO/20240501',
      country_code: 'NO',
      folder_date: '20240501',
      file_count: 3,
      registered_count: 0,
      is_excluded: false,
      status: 'not_in_metadata',
      files: [
        { run_name: 'run-2024-05-01', registered: false, sample_code: null, sampling_date: null },
      ],
    },
  ],
};

describe('DiscoveryService', () => {
  let service: DiscoveryService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [HttpClientTestingModule],
    });
    service = TestBed.inject(DiscoveryService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  describe('nanopore()', () => {
    it('sends GET /api/discovery/nanopore', () => {
      service.nanopore().subscribe();
      const req = http.expectOne('/api/discovery/nanopore');
      expect(req.request.method).toBe('GET');
      req.flush(NANOPORE_RESULT);
    });

    it('returns NanoporeDiscoveryResult', (done) => {
      service.nanopore().subscribe((result) => {
        expect(result).toEqual(NANOPORE_RESULT);
        done();
      });
      http.expectOne('/api/discovery/nanopore').flush(NANOPORE_RESULT);
    });

    it('includes minknow_dir and scanned_at', (done) => {
      service.nanopore().subscribe((result) => {
        expect(result.minknow_dir).toBe('/data/minknow');
        expect(result.scanned_at).toBe('2024-06-01T10:00:00.000Z');
        done();
      });
      http.expectOne('/api/discovery/nanopore').flush(NANOPORE_RESULT);
    });

    it('returns runs array with expected structure', (done) => {
      service.nanopore().subscribe((result) => {
        expect(result.runs.length).toBe(2);
        const first = result.runs[0];
        expect(first.run_accession).toBe('ERR123456');
        expect(first.in_metadata).toBe(true);
        expect(first.on_disk).toBe(true);
        expect(first.status).toBe('partial');
        expect(first.barcodes.length).toBe(2);
        done();
      });
      http.expectOne('/api/discovery/nanopore').flush(NANOPORE_RESULT);
    });

    it('returns barcode-level detail', (done) => {
      service.nanopore().subscribe((result) => {
        const bc = result.runs[0].barcodes[0];
        expect(bc.barcode).toBe('barcode01');
        expect(bc.fastq_count).toBe(120);
        expect(bc.status).toBe('ready');
        done();
      });
      http.expectOne('/api/discovery/nanopore').flush(NANOPORE_RESULT);
    });

    it('handles null minknow_dir', (done) => {
      const noDir: NanoporeDiscoveryResult = { ...NANOPORE_RESULT, minknow_dir: null, runs: [] };
      service.nanopore().subscribe((result) => {
        expect(result.minknow_dir).toBeNull();
        expect(result.runs).toEqual([]);
        done();
      });
      http.expectOne('/api/discovery/nanopore').flush(noDir);
    });
  });

  describe('biomeme()', () => {
    it('sends GET /api/discovery/biomeme', () => {
      service.biomeme().subscribe();
      const req = http.expectOne('/api/discovery/biomeme');
      expect(req.request.method).toBe('GET');
      req.flush(BIOMEME_RESULT);
    });

    it('returns BiomemeDiscoveryResult', (done) => {
      service.biomeme().subscribe((result) => {
        expect(result).toEqual(BIOMEME_RESULT);
        done();
      });
      http.expectOne('/api/discovery/biomeme').flush(BIOMEME_RESULT);
    });

    it('includes biomeme_dir and scanned_at', (done) => {
      service.biomeme().subscribe((result) => {
        expect(result.biomeme_dir).toBe('/data/biomeme');
        expect(result.scanned_at).toBe('2024-06-01T10:00:00.000Z');
        done();
      });
      http.expectOne('/api/discovery/biomeme').flush(BIOMEME_RESULT);
    });

    it('returns folders array with expected structure', (done) => {
      service.biomeme().subscribe((result) => {
        expect(result.folders.length).toBe(2);
        const first = result.folders[0];
        expect(first.folder_path).toBe('NO/20240601');
        expect(first.file_count).toBe(1);
        expect(first.registered_count).toBe(1);
        expect(first.status).toBe('ready');
        expect(first.files[0].run_name).toBe('run-2024-06-01');
        expect(first.files[0].registered).toBe(true);
        expect(first.files[0].sample_code).toBe('NOBGOPark_water');
        done();
      });
      http.expectOne('/api/discovery/biomeme').flush(BIOMEME_RESULT);
    });

    it('handles not_in_metadata folder with unregistered files', (done) => {
      service.biomeme().subscribe((result) => {
        const folder = result.folders[1];
        expect(folder.status).toBe('not_in_metadata');
        expect(folder.registered_count).toBe(0);
        expect(folder.files[0].registered).toBe(false);
        expect(folder.files[0].sample_code).toBeNull();
        expect(folder.files[0].sampling_date).toBeNull();
        done();
      });
      http.expectOne('/api/discovery/biomeme').flush(BIOMEME_RESULT);
    });

    it('handles null biomeme_dir', (done) => {
      const noDir: BiomemeDiscoveryResult = { ...BIOMEME_RESULT, biomeme_dir: null, folders: [] };
      service.biomeme().subscribe((result) => {
        expect(result.biomeme_dir).toBeNull();
        expect(result.folders).toEqual([]);
        done();
      });
      http.expectOne('/api/discovery/biomeme').flush(noDir);
    });
  });
});
