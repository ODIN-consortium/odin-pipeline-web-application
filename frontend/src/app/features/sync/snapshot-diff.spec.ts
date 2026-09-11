import { Snapshot, diffSnapshots } from './snapshot-diff';

const snapshot = (tables: Snapshot['tables']): Snapshot => ({
  version: '1',
  exported_at: '2026-07-31T10:00:00.000Z',
  exported_by: 'BGO-2009',
  tables,
});

const row = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  site: 'Park',
  updated_at: '2026-07-31T09:00:00.000Z',
  ...over,
});

describe('diffSnapshots', () => {
  it('counts rows the snapshot does not contain as deletions', () => {
    // The number that matters: what disappears if the operator proceeds.
    const diff = diffSnapshots(
      snapshot({ sites: [row('a'), row('b'), row('c')] }),
      snapshot({ sites: [row('a')] }),
    );
    expect(diff.totalDeleted).toBe(2);
    expect(diff.totalUnchanged).toBe(1);
  });

  it('separates rows that would change from rows already identical', () => {
    // A restore writes every row, but only the different ones actually move.
    const diff = diffSnapshots(
      snapshot({ sites: [row('a'), row('b', { site: 'Beach' })] }),
      snapshot({ sites: [row('a'), row('b', { site: 'Harbour' })] }),
    );
    expect(diff.totalUnchanged).toBe(1);
    expect(diff.totalChanged).toBe(1);
    expect(diff.totalAffected).toBe(1);
  });

  it('reports no effect at all when the database already matches', () => {
    // The case the operator hits by restoring the same file twice: it must offer nothing.
    const same = { sites: [row('a')], samples: [row('s')] };
    const diff = diffSnapshots(snapshot(same), snapshot(same));
    expect(diff.totalAffected).toBe(0);
    expect(diff.totalUnchanged).toBe(2);
  });

  it('notices a difference in any column, not just the ones shown on screen', () => {
    const diff = diffSnapshots(
      snapshot({ sites: [row('a', { comments: null })] }),
      snapshot({ sites: [row('a', { comments: 'note' })] }),
    );
    expect(diff.totalChanged).toBe(1);
  });

  it('counts a differing timestamp as a change, because a restore imposes it', () => {
    // Deliberately unlike a merge, which ignores audit columns: a restore writes them verbatim.
    const diff = diffSnapshots(
      snapshot({ sites: [row('a', { updated_at: '2026-07-31T11:00:00.000Z' })] }),
      snapshot({ sites: [row('a')] }),
    );
    expect(diff.totalChanged).toBe(1);
  });

  it('treats a column present on only one side as a difference', () => {
    const diff = diffSnapshots(
      snapshot({ sites: [{ id: 'a' }] }),
      snapshot({ sites: [{ id: 'a', site: 'Park' }] }),
    );
    expect(diff.totalChanged).toBe(1);
  });

  it('treats a table missing locally as rows to add back', () => {
    const diff = diffSnapshots(snapshot({}), snapshot({ sites: [row('a')] }));
    expect(diff.totalDeleted).toBe(0);
    expect(diff.totalAdded).toBe(1);
    expect(diff.totalAffected).toBe(1);
  });

  it('ignores tables the snapshot does not mention, because a restore leaves them alone', () => {
    const diff = diffSnapshots(
      snapshot({ sites: [row('a')], samples: [row('s')] }),
      snapshot({ sites: [row('a')] }),
    );
    expect(diff.tables.map((t) => t.table)).toEqual(['sites']);
  });

  it('puts the tables losing most rows first', () => {
    const diff = diffSnapshots(
      snapshot({ sites: [row('a'), row('b')], samples: [row('s'), row('t'), row('u')] }),
      snapshot({ sites: [], samples: [] }),
    );
    expect(diff.tables.map((t) => t.table)).toEqual(['samples', 'sites']);
  });

  it('counts an emptied table as deleting all of it', () => {
    const diff = diffSnapshots(snapshot({ sites: [row('a'), row('b')] }), snapshot({ sites: [] }));
    expect(diff.totalDeleted).toBe(2);
    expect(diff.totalAdded).toBe(0);
  });
});
