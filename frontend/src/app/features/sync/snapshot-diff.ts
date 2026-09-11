/** The JSON envelope produced by `GET /api/sync/export`. */
export interface Snapshot {
  version: string;
  exported_at: string;
  exported_by: string | null;
  tables: Record<string, Array<Record<string, unknown>>>;
}

/** What restoring a snapshot would do to one table. */
export interface TableRestoreDiff {
  table: string;
  /** In the snapshot, absent here — inserted. */
  added: number;
  /** Present here with different values — overwritten. */
  changed: number;
  /** Present here and already identical — written, but nothing moves. */
  unchanged: number;
  /** Here now, absent from the snapshot — deleted. */
  deleted: number;
}

export interface RestoreDiff {
  tables: TableRestoreDiff[];
  totalAdded: number;
  totalChanged: number;
  totalUnchanged: number;
  totalDeleted: number;
  /** Rows whose content actually moves. Zero means the database already matches the file. */
  totalAffected: number;
}

/**
 * Work out what restoring *incoming* over *current* would do.
 *
 * Computed here rather than by a second backend endpoint: both snapshots are already in hand,
 * and the comparison is on primary key — the same basis the restore itself uses. That keeps the
 * confirmation honest without another round trip to keep in step with the writer.
 *
 * Rows are compared **by value, not merely by presence**. The first version counted every row in
 * the snapshot as "restored", so restoring the same file twice announced hundreds of rows when
 * nothing would move, and the "already matches" case was unreachable. A restore does write every
 * row, but writing a row that is already identical changes nothing, and the confirmation should
 * describe what changes.
 *
 * Unlike a merge, the comparison includes the audit columns. A merge ignores them deliberately —
 * one device having a device name where another has NULL is not a real data difference. A restore
 * writes the snapshot's values verbatim, `updated_at` included, so a difference there is a
 * difference it would impose.
 *
 * Tables absent from the snapshot are left alone by a restore, so they are absent here too.
 */
export function diffSnapshots(current: Snapshot, incoming: Snapshot): RestoreDiff {
  const tables: TableRestoreDiff[] = [];

  for (const [table, incomingRows] of Object.entries(incoming.tables)) {
    const currentById = new Map(
      (current.tables[table] ?? []).map((r) => [String(r['id']), r] as const),
    );
    let added = 0;
    let changed = 0;
    let unchanged = 0;

    for (const row of incomingRows) {
      const local = currentById.get(String(row['id']));
      if (!local) {
        added++;
      } else if (rowsEqual(local, row)) {
        unchanged++;
      } else {
        changed++;
      }
    }

    const incomingIds = new Set(incomingRows.map((r) => String(r['id'])));
    const deleted = [...currentById.keys()].filter((id) => !incomingIds.has(id)).length;

    tables.push({ table, added, changed, unchanged, deleted });
  }

  const total = (pick: (t: TableRestoreDiff) => number) =>
    tables.reduce((sum, t) => sum + pick(t), 0);
  const totalAdded = total((t) => t.added);
  const totalChanged = total((t) => t.changed);
  const totalDeleted = total((t) => t.deleted);

  return {
    tables: tables
      .filter((t) => t.added || t.changed || t.unchanged || t.deleted)
      .sort((a, b) => b.deleted - a.deleted || b.changed - a.changed),
    totalAdded,
    totalChanged,
    totalUnchanged: total((t) => t.unchanged),
    totalDeleted,
    totalAffected: totalAdded + totalChanged + totalDeleted,
  };
}

/** Field-by-field equality over the union of both rows' keys. */
function rowsEqual(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const key of keys) {
    if (a[key] !== b[key]) {
      return false;
    }
  }
  return true;
}
