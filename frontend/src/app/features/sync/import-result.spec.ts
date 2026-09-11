import { ExcelImportResult } from '../../core/services/export.service';
import { describeImportResult } from './import-result';

const EMPTY = { created: 0, updated: 0, unchanged: 0, deleted: 0, skipped: 0 };
const result = (summary: ExcelImportResult['summary']): ExcelImportResult => ({
  detail: '',
  mode: 'merge',
  summary,
});

describe('describeImportResult', () => {
  it('totals across sheets and names each kind of outcome', () => {
    const text = describeImportResult(
      result({
        sites: { ...EMPTY, created: 2 },
        samples: { ...EMPTY, created: 3, updated: 4, unchanged: 9, skipped: 1 },
      }),
    );
    expect(text).toContain('5 added');
    expect(text).toContain('4 changed');
    expect(text).toContain('9 already up to date');
    expect(text).toContain('1 could not be imported');
  });

  it('omits the outcomes that did not happen', () => {
    const text = describeImportResult(result({ sites: { ...EMPTY, created: 1 } }));
    expect(text).toContain('1 added');
    expect(text).not.toContain('changed');
    expect(text).not.toContain('deleted');
  });

  it('reports an import that changed nothing rather than claiming success vaguely', () => {
    // The restamping bug made this indistinguishable from a real update.
    expect(describeImportResult(result({ sites: { ...EMPTY, unchanged: 3 } }))).toContain(
      '3 already up to date',
    );
  });

  it('handles an empty summary', () => {
    expect(describeImportResult(result({}))).toBe('Import complete — no changes.');
  });

  it('reports deletions from replace mode', () => {
    expect(describeImportResult(result({ sites: { ...EMPTY, deleted: 6 } }))).toContain(
      '6 deleted',
    );
  });
});
