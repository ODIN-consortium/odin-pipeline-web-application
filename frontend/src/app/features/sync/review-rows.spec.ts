import { SyncPreview, SyncTableDiff } from '../../core/models/sync.model';
import { actionOptions, buildReviewRows, rowLabel } from './review-rows';

const EMPTY: SyncTableDiff = { new: [], identical: [], updated: [], independent_duplicate: [], conflict: [] };

function previewWith(diff: Record<string, Partial<SyncTableDiff>>): SyncPreview {
  const full: Record<string, SyncTableDiff> = {};
  for (const [table, d] of Object.entries(diff)) full[table] = { ...EMPTY, ...d };
  return { exported_by: 'x', exported_at: '2026-08-01', summary: {}, diff: full };
}

describe('buildReviewRows', () => {
  it('defaults additions and newer versions to accept, duplicates and conflicts to keep mine', () => {
    const rows = buildReviewRows(
      previewWith({
        sites: {
          new: [{ incoming: { id: '1', site_code: 'A' } }],
          updated: [{ incoming: { id: '2', city: 'Oslo' }, local: { id: '2', city: 'Olso' } }],
          independent_duplicate: [{ incoming: { id: '3' }, local: { id: '9' } }],
          conflict: [{ incoming: { id: '4' }, local: { id: '4' } }],
        },
      }),
    ).sites;

    expect(rows.map((r) => [r.category, r.action])).toEqual([
      ['new', 'accept'],
      ['updated', 'accept'],
      ['independent_duplicate', 'keep_mine'],
      ['conflict', 'keep_mine'],
    ]);
  });

  it('lists the changed fields, ignoring sync metadata and equal values', () => {
    const rows = buildReviewRows(
      previewWith({
        sites: {
          updated: [
            {
              incoming: { id: '2', updated_at: 'later', updated_by: 'them', city: 'Oslo', country: 'Norway' },
              local: { id: '2', updated_at: 'earlier', updated_by: 'me', city: 'Olso', country: 'Norway' },
            },
          ],
        },
      }),
    ).sites;

    expect(rows[0].changedFields).toEqual(['city']);
  });

  it('precomputes the user-data fields of a new row, metadata filtered out', () => {
    const rows = buildReviewRows(
      previewWith({
        sites: {
          new: [{ incoming: { id: '1', created_by: 'them', site_code: 'NOBGO01', city: 'Bergen' } }],
        },
      }),
    ).sites;

    expect(rows[0].newFields).toEqual([
      { field: 'site_code', value: 'NOBGO01' },
      { field: 'city', value: 'Bergen' },
    ]);
  });

  it('omits tables with nothing to review', () => {
    const result = buildReviewRows(previewWith({ sites: {}, samples: { new: [{ incoming: { id: '1' } }] } }));
    expect(Object.keys(result)).toEqual(['samples']);
  });
});

describe('rowLabel', () => {
  it('joins the table key fields and drops empty components', () => {
    expect(rowLabel({ site_code: 'NOBGO01', country: '', city: 'Bergen' }, 'sites')).toBe('NOBGO01 / Bergen');
  });

  it('falls back to the id for an unknown table', () => {
    expect(rowLabel({ id: 'row-1' }, 'mystery')).toBe('row-1');
  });
});

describe('actionOptions', () => {
  it.each([
    ['new', ['accept', 'skip']],
    ['updated', ['accept', 'skip']],
    ['independent_duplicate', ['keep_mine', 'use_theirs']],
    ['conflict', ['keep_mine', 'use_theirs']],
  ] as const)('offers the right choices for a %s row', (category, values) => {
    expect(actionOptions(category).map((o) => o.value)).toEqual([...values]);
  });
});
