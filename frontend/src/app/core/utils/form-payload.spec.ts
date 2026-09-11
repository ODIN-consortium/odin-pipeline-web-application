import { toCreatePayload, toUpdatePayload } from './form-payload';

/**
 * These pin the distinction that the clear-an-optional-field bug turned on: an
 * update payload must send `null` for an emptied control, because a *missing* key
 * tells the backend to leave the stored value alone.
 */
describe('toUpdatePayload', () => {
  it('sends null for a cleared text field so the backend blanks it', () => {
    expect(toUpdatePayload({ comments: '' })).toEqual({ comments: null });
  });

  it('keeps the key present — omitting it would mean "leave unchanged"', () => {
    expect('comments' in (toUpdatePayload({ comments: '' }) as object)).toBe(true);
  });

  it('passes through values that were filled in', () => {
    expect(toUpdatePayload({ comments: 'a note', depth: '5' })).toEqual({
      comments: 'a note',
      depth: '5',
    });
  });

  it('normalises undefined to null as well', () => {
    expect(toUpdatePayload({ sample_id: undefined })).toEqual({ sample_id: null });
  });

  it('preserves an explicit null', () => {
    expect(toUpdatePayload({ sample_id: null })).toEqual({ sample_id: null });
  });

  it('does not treat 0 or false as empty', () => {
    expect(toUpdatePayload({ threshold: 0, flag: false })).toEqual({
      threshold: 0,
      flag: false,
    });
  });

  it('omits fields the endpoint must never receive', () => {
    const payload = toUpdatePayload(
      { comments: 'x', label: 'L', run_accession: 'RA' },
      { omit: ['label', 'run_accession'] },
    );
    expect(payload).toEqual({ comments: 'x' });
  });

  it('drops an empty keepIfEmpty field instead of clearing it', () => {
    const payload = toUpdatePayload(
      { site_id: '', sample_type: null, comments: '' },
      { keepIfEmpty: ['site_id', 'sample_type'] },
    );
    // site_id/sample_type absent → backend leaves them alone; comments cleared.
    expect(payload).toEqual({ comments: null });
  });

  it('still sends a filled keepIfEmpty field so it can be changed', () => {
    const payload = toUpdatePayload(
      { site_id: 'site-1', comments: '' },
      { keepIfEmpty: ['site_id'] },
    );
    expect(payload).toEqual({ site_id: 'site-1', comments: null });
  });
});

describe('toCreatePayload', () => {
  it('omits empty fields so backend defaults apply', () => {
    expect(toCreatePayload({ comments: '', depth: '5' })).toEqual({ depth: '5' });
  });

  it('omits null and undefined', () => {
    expect(toCreatePayload({ a: null, b: undefined, c: 'keep' })).toEqual({ c: 'keep' });
  });

  it('does not treat 0 or false as empty', () => {
    expect(toCreatePayload({ threshold: 0, flag: false })).toEqual({
      threshold: 0,
      flag: false,
    });
  });
});
