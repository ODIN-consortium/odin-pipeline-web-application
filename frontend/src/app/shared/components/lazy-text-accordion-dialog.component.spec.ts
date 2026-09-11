import { TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MAT_DIALOG_DATA } from '@angular/material/dialog';
import { of, throwError, Subject } from 'rxjs';

import {
  LazyTextAccordionDialogComponent,
  LazyTextAccordionDialogData,
} from './lazy-text-accordion-dialog.component';

function setup(data: LazyTextAccordionDialogData) {
  TestBed.configureTestingModule({
    imports: [LazyTextAccordionDialogComponent, NoopAnimationsModule],
    providers: [{ provide: MAT_DIALOG_DATA, useValue: data }],
  });
  const fixture = TestBed.createComponent(LazyTextAccordionDialogComponent);
  fixture.detectChanges();
  return fixture;
}

const base = {
  icon: 'description',
  title: 'Run manifest',
  label: 'ERR123456',
  emptyMessage: 'nothing here',
  notFoundMessage: 'not found',
};

describe('LazyTextAccordionDialogComponent', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('renders one state per entry, preserving order, and eagerly loads only the first', () => {
    const amrLoad = jest.fn(() => of('AMR BODY'));
    const taxLoad = jest.fn(() => of('TAX BODY'));
    const fixture = setup({
      ...base,
      entries: [
        { key: 'amr', label: 'amr', load: amrLoad },
        { key: 'tax', label: 'taxprofiler', load: taxLoad },
      ],
    });

    const states = fixture.componentInstance.states();
    expect(states.map((s) => s.key)).toEqual(['amr', 'tax']);
    expect(amrLoad).toHaveBeenCalledTimes(1);
    expect(taxLoad).not.toHaveBeenCalled();
    expect(fixture.componentInstance.states()[0].content).toBe('AMR BODY');
    expect(fixture.componentInstance.states()[1].loaded).toBe(false);
  });

  it('lazily loads a panel when opened, only once', () => {
    const taxLoad = jest.fn(() => of('TAX BODY'));
    const fixture = setup({
      ...base,
      entries: [
        { key: 'amr', label: 'amr', load: () => of('AMR') },
        { key: 'tax', label: 'taxprofiler', load: taxLoad },
      ],
    });

    fixture.componentInstance.load(1);
    fixture.componentInstance.load(1); // second open must not refetch
    expect(taxLoad).toHaveBeenCalledTimes(1);
    expect(fixture.componentInstance.states()[1].content).toBe('TAX BODY');
  });

  it('shows the fallback message when a load errors without a server message', () => {
    const fixture = setup({
      ...base,
      entries: [{ key: 'old', label: 'old', load: () => throwError(() => ({})) }],
    });
    const state = fixture.componentInstance.states()[0];
    expect(state.loaded).toBe(true);
    expect(state.error).toBe('not found');
  });

  it('prefers a server-provided error message', () => {
    const fixture = setup({
      ...base,
      entries: [{ key: 'x', label: 'x', load: () => throwError(() => ({ error: 'boom' })) }],
    });
    expect(fixture.componentInstance.states()[0].error).toBe('boom');
  });

  it('shows a loading state until the observable emits', () => {
    const subject = new Subject<string>();
    const fixture = setup({
      ...base,
      entries: [{ key: 'x', label: 'x', load: () => subject.asObservable() }],
    });
    expect(fixture.componentInstance.states()[0].loading).toBe(true);
    subject.next('DONE');
    subject.complete();
    expect(fixture.componentInstance.states()[0].loading).toBe(false);
    expect(fixture.componentInstance.states()[0].content).toBe('DONE');
  });

  it('renders the empty message when there are no entries', () => {
    const fixture = setup({ ...base, entries: [] });
    expect(fixture.componentInstance.states().length).toBe(0);
    expect(fixture.nativeElement.textContent).toContain('nothing here');
  });
});
