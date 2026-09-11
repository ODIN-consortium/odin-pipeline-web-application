import { TestBed } from '@angular/core/testing';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';

import { ExcelImportResult } from '../../core/services/export.service';
import { ImportPreviewDialogComponent } from './import-preview-dialog.component';

const EMPTY = { created: 0, updated: 0, unchanged: 0, deleted: 0, skipped: 0 };

function preview(over: Partial<ExcelImportResult> = {}): ExcelImportResult {
  return {
    detail: 'Preview only — nothing was written.',
    mode: 'merge',
    dry_run: true,
    summary: { sites: { ...EMPTY }, samples: { ...EMPTY } },
    problems: [],
    problem_count: 0,
    ...over,
  };
}

describe('ImportPreviewDialogComponent', () => {
  let close: jest.Mock;

  function setup(data: ExcelImportResult) {
    close = jest.fn();
    TestBed.configureTestingModule({
      imports: [ImportPreviewDialogComponent, NoopAnimationsModule],
      providers: [
        { provide: MatDialogRef, useValue: { close } },
        { provide: MAT_DIALOG_DATA, useValue: data },
      ],
    });
    const fixture = TestBed.createComponent(ImportPreviewDialogComponent);
    fixture.detectChanges();
    return fixture;
  }

  const text = (fixture: ReturnType<typeof setup>): string =>
    fixture.nativeElement.textContent ?? '';

  it('hides sheets the workbook said nothing about', () => {
    const fixture = setup(
      preview({
        summary: {
          sites: { ...EMPTY, created: 2 },
          samples: { ...EMPTY },
          nanopore: { ...EMPTY },
          biomeme: { ...EMPTY },
        },
      }),
    );
    expect(fixture.componentInstance.outcomes().map((o) => o.sheet)).toEqual(['sites']);
  });

  it('lists every problem, not just the first few', () => {
    // The snackbar this replaces could only name three.
    const problems = Array.from({ length: 22 }, (_, i) => ({
      sheet: 'samples',
      row: i + 3,
      reason: `something wrong on row ${i + 3}`,
    }));
    const fixture = setup(preview({ problems, problem_count: 22 }));

    expect(fixture.componentInstance.problems()).toHaveLength(22);
    expect(text(fixture)).toContain('22 row(s) cannot be imported');
    expect(text(fixture)).toContain('something wrong on row 24');
  });

  it('says when problems were truncated rather than quietly listing fewer', () => {
    const fixture = setup(
      preview({
        problems: [{ sheet: 'samples', row: 3, reason: 'bad' }],
        problem_count: 500,
        problems_truncated: 499,
      }),
    );
    expect(text(fixture)).toContain('499 more not listed');
  });

  it('warns about deletions in replace mode, with the count', () => {
    const fixture = setup(
      preview({ mode: 'replace', summary: { sites: { ...EMPTY, deleted: 7 } } }),
    );
    const body = text(fixture);
    expect(body).toContain('Replace mode');
    expect(body).toContain('7 existing row(s)');
  });

  it('does not raise the deletion alarm when nothing would be deleted', () => {
    const fixture = setup(
      preview({ mode: 'replace', summary: { sites: { ...EMPTY, created: 1 } } }),
    );
    expect(text(fixture)).not.toContain('will delete');
    expect(fixture.nativeElement.querySelector('.replace-danger')).toBeNull();
  });

  it('states the mode even when replace would delete nothing', () => {
    // Previously the dialog said nothing about replace mode unless it had deletions to report,
    // so an operator importing a superset had no indication of which mode they were in.
    const fixture = setup(
      preview({ mode: 'replace', summary: { sites: { ...EMPTY, created: 1 } } }),
    );
    expect(text(fixture)).toContain('replace');
    expect(text(fixture)).toContain('rows missing from the workbook are deleted');
  });

  it('states merge mode too, and that it deletes nothing', () => {
    const fixture = setup(preview({ summary: { sites: { ...EMPTY, updated: 1 } } }));
    expect(text(fixture)).toContain('merge');
    expect(text(fixture)).toContain('nothing is deleted');
  });

  it('names the deletion in the confirm button, not only in a banner', () => {
    // A warning elsewhere is easy to click past; the button itself has to say it.
    const fixture = setup(
      preview({ mode: 'replace', summary: { sites: { ...EMPTY, created: 4, deleted: 7 } } }),
    );
    const button = fixture.nativeElement.querySelector('mat-dialog-actions button:last-child');
    expect(button.textContent).toContain('Delete 7');
    expect(button.textContent).toContain('import 4');
  });

  it('tells the operator a backup is taken, and what it can and cannot do', () => {
    const fixture = setup(preview({ summary: { sites: { ...EMPTY, created: 1 } } }));
    const body = text(fixture);
    expect(body).toContain('backup');
    // Must not imply an undo: replaying a snapshot cannot remove rows an import added.
    expect(body).toContain('cannot remove rows an import adds');
  });

  it('offers nothing to apply when the workbook would write nothing', () => {
    // Every row identical, or every row unimportable: there is no action to confirm.
    const fixture = setup(preview({ summary: { sites: { ...EMPTY, unchanged: 5 } } }));
    expect(fixture.componentInstance.canApply()).toBe(false);
  });

  it('counts only writes as work to apply, not identical or skipped rows', () => {
    const fixture = setup(
      preview({
        summary: { sites: { ...EMPTY, created: 2, updated: 3, unchanged: 9, skipped: 4 } },
        problem_count: 4,
      }),
    );
    expect(fixture.componentInstance.totalWrites()).toBe(5);
  });

  it('explains itself when no recognisable rows were found', () => {
    // The failure mode that used to report success while importing nothing.
    const fixture = setup(preview({ summary: {} }));
    expect(text(fixture)).toContain('No recognisable rows were found');
    expect(fixture.componentInstance.canApply()).toBe(false);
  });

  it('closes with true only when the operator confirms', () => {
    const fixture = setup(preview({ summary: { sites: { ...EMPTY, created: 1 } } }));
    fixture.componentInstance.apply();
    expect(close).toHaveBeenCalledWith(true);

    fixture.componentInstance.cancel();
    expect(close).toHaveBeenLastCalledWith(false);
  });
});

describe('ImportPreviewDialogComponent — changed rows', () => {
  // The preview knew THAT a row changes but showed only counts; the operator asked to
  // see what. Each changed row lists its fields as "field: old → new".
  function create(over: Partial<ExcelImportResult>) {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [ImportPreviewDialogComponent, NoopAnimationsModule],
      providers: [
        { provide: MatDialogRef, useValue: { close: jest.fn() } },
        { provide: MAT_DIALOG_DATA, useValue: preview(over) },
      ],
    });
    const fixture = TestBed.createComponent(ImportPreviewDialogComponent);
    fixture.detectChanges();
    return fixture;
  }

  it('lists each changed row with its field-level diff', () => {
    const fixture = create({
      summary: { sites: { ...EMPTY, updated: 1 } },
      change_count: 1,
      changes: [
        {
          sheet: 'sites',
          row: 2,
          fields: [{ field: 'location', old: 'Old location', new: 'New location' }],
        },
      ],
    });

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('1 row(s) will be changed');
    expect(text).toContain('sites row 2');
    expect(text).toContain('location: Old location → New location');
  });

  it('shows cleared values as an em dash', () => {
    const fixture = create({
      summary: { sites: { ...EMPTY, updated: 1 } },
      change_count: 1,
      changes: [{ sheet: 'sites', row: 2, fields: [{ field: 'comments', old: 'note', new: null }] }],
    });

    expect((fixture.nativeElement as HTMLElement).textContent).toContain('comments: note → —');
  });
});
