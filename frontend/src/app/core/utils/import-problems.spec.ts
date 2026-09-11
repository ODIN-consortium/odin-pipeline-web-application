import { formatImportFailure } from './import-problems';

describe('formatImportFailure', () => {
  const detail = (over: Partial<Parameters<typeof formatImportFailure>[0]> = {}) => ({
    message: '5 row(s) cannot be imported, so nothing was imported.',
    problem_count: 5,
    problems: [
      { sheet: 'samples', row: 3, reason: "site_code 'NOSUCHSITE' does not match any site" },
      { sheet: 'samples', row: 7, reason: "sampling_date 'nonsense' is not YYYYMMDD" },
      { sheet: 'sites', row: 9, reason: 'country is required and is empty' },
      { sheet: 'sites', row: 11, reason: 'site_code is required and is empty' },
      { sheet: 'biomeme', row: 4, reason: "sample_code 'X' does not match any sample" },
    ],
    ...over,
  });

  it('names the first few offending rows with sheet, row and reason', () => {
    const text = formatImportFailure(detail(), 'Import failed');
    expect(text).toContain('samples row 3');
    expect(text).toContain("site_code 'NOSUCHSITE' does not match any site");
    expect(text).toContain('samples row 7');
    expect(text).toContain('sites row 9');
  });

  it('says how many more there are rather than hiding them', () => {
    // The difference between three bad rows and three hundred matters to the operator.
    expect(formatImportFailure(detail(), 'Import failed')).toContain('…and 2 more');
  });

  it('omits the tail when everything fits', () => {
    const two = detail({ problem_count: 2, problems: detail().problems.slice(0, 2) });
    const text = formatImportFailure(two, 'Import failed');
    expect(text).not.toContain('more');
  });

  it('leads with the summary the backend wrote', () => {
    expect(formatImportFailure(detail(), 'Import failed')).toContain(
      '5 row(s) cannot be imported',
    );
  });

  it('passes a plain string detail straight through', () => {
    // A 400 for a non-xlsx upload, or a 409 for a constraint conflict.
    expect(formatImportFailure('Please upload an .xlsx file', 'Import failed')).toBe(
      'Please upload an .xlsx file',
    );
  });

  it('falls back rather than rendering an object it does not recognise', () => {
    // This is the regression: String(detail) on the structured body produced
    // "[object Object]" in the snackbar.
    for (const value of [null, undefined, {}, { unexpected: true }, 42, '  ']) {
      const text = formatImportFailure(value, 'Import failed');
      expect(text).toBe('Import failed');
      expect(text).not.toContain('[object Object]');
    }
  });

  it('handles a problem with no row number', () => {
    const noRow = detail({
      problem_count: 1,
      problems: [{ sheet: 'samples', row: null, reason: 'sheet has no recognisable header' }],
    });
    expect(formatImportFailure(noRow, 'Import failed')).toContain('samples row ?');
  });
});
