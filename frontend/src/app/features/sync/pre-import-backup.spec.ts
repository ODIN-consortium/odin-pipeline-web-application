import { of, throwError } from 'rxjs';

import { SyncService } from '../../core/services/sync.service';
import { backupBeforeImport, backupFilename } from './pre-import-backup';

describe('backupFilename', () => {
  it('is sortable, filename-safe and says what it is', () => {
    const name = backupFilename(new Date(2026, 6, 31, 11, 7, 5));
    expect(name).toBe('odin-backup-before-import-20260731T110705.json');
  });

  it('pads single digits so names sort chronologically as text', () => {
    expect(backupFilename(new Date(2026, 0, 2, 3, 4, 5))).toContain('20260102T030405');
  });
});

describe('backupBeforeImport', () => {
  const service = (result: unknown) =>
    ({ fetchSnapshot: () => result } as unknown as SyncService);

  // jsdom implements neither of these; the download itself is the browser's job, so stubbing
  // them keeps the test on what this code decides rather than on what the browser does.
  beforeEach(() => {
    (URL as unknown as { createObjectURL: unknown }).createObjectURL = jest.fn(() => 'blob:x');
    (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = jest.fn();
  });

  it('hands the snapshot to the browser under the timestamped name', (done) => {
    const clicked: string[] = [];
    jest
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function (this: HTMLAnchorElement) {
        clicked.push(this.download);
      });

    backupBeforeImport(service(of(new Blob(['{}'])))).subscribe(() => {
      expect(clicked).toHaveLength(1);
      expect(clicked[0]).toMatch(/^odin-backup-before-import-\d{8}T\d{6}\.json$/);
      done();
    });
  });

  it('emits the filename once the snapshot has arrived', (done) => {
    backupBeforeImport(service(of(new Blob(['{}'])))).subscribe((filename) => {
      expect(filename).toMatch(/^odin-backup-before-import-\d{8}T\d{6}\.json$/);
      done();
    });
  });

  it('fails rather than emitting when the snapshot cannot be fetched', (done) => {
    // The caller treats this as a precondition and abandons the import. A backup that
    // silently failed would be worse than none, because it would be believed in.
    let emitted = false;
    backupBeforeImport(service(throwError(() => new Error('offline')))).subscribe({
      next: () => (emitted = true),
      error: () => {
        expect(emitted).toBe(false);
        done();
      },
    });
  });
});
