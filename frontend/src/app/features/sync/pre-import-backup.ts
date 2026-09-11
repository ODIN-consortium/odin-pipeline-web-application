import { Observable, map } from 'rxjs';

import { SyncService } from '../../core/services/sync.service';
import { fileTimestamp, saveBlob } from '../../core/utils/download-blob';

/** The filename an automatic pre-import backup is saved under. */
export function backupFilename(now?: Date): string {
  return `odin-backup-before-import-${fileTimestamp(now)}.json`;
}

/**
 * Download a snapshot of the database before an import writes to it.
 *
 * Automatic rather than offered. A prompt gets clicked through, and the operator most likely to
 * need the backup is the one least likely to pause for it.
 *
 * A downloaded JSON file rather than a snapshot inside the container: the people running ODIN in
 * the field are not expected to know `docker cp`, so a file they cannot reach is not a backup
 * they have. This one lands in their downloads, and the app can read it back through
 * Sync → Import. It also needs no retention policy, since the files are theirs to keep or
 * delete.
 *
 * **Scope, and the reason it is enough.** The snapshot covers the seven syncable tables, and
 * both import paths — Excel and JSON — write only within that set, so it captures everything an
 * import can damage. What it is *not* is a point-in-time revert: replaying it re-asserts the old
 * values and brings back deleted rows, but it cannot remove rows an import added, because
 * sync/apply has no prune. The UI says that rather than implying an undo button.
 *
 * Emits once the data has arrived and the download has been handed to the browser, so callers
 * can treat it as a precondition and abandon the import if it fails. Whether the operator then
 * *keeps* the file is not something a web page can verify.
 */
export function backupBeforeImport(sync: SyncService): Observable<string> {
  const filename = backupFilename();
  return sync.fetchSnapshot().pipe(
    map((blob) => {
      saveBlob(blob, filename);
      return filename;
    }),
  );
}
