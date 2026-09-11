import { DestroyRef, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';

import { NotificationService } from '../services/notification.service';
import { saveDialogForm } from './dialog-save';

describe('saveDialogForm', () => {
  let destroyRef: DestroyRef;
  let close: jest.Mock;
  let notifyError: jest.Mock;
  let notify: NotificationService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    destroyRef = TestBed.inject(DestroyRef);
    close = jest.fn();
    notifyError = jest.fn();
    notify = { error: notifyError } as unknown as NotificationService;
  });

  const opts = <T,>(over: Partial<Parameters<typeof saveDialogForm<T>>[0]> = {}) => ({
    form: { invalid: false },
    saving: signal(false),
    dialogRef: { close },
    notify,
    destroyRef,
    request: () => of({} as T),
    ...over,
  });

  it('sends nothing and leaves the dialog open when the form is invalid', () => {
    const request = jest.fn();
    const o = opts({ form: { invalid: true }, request });

    saveDialogForm(o);

    expect(request).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
    expect(o.saving()).toBe(false);
  });

  it('closes with true on success so the opener knows to reload', () => {
    saveDialogForm(opts());
    expect(close).toHaveBeenCalledWith(true);
  });

  it('marks saving while the request is in flight and clears it on failure', () => {
    const subject = new Subject<unknown>();
    const o = opts({ request: () => subject.asObservable() });

    saveDialogForm(o);
    expect(o.saving()).toBe(true);

    subject.error({ error: { detail: 'boom' } });
    expect(o.saving()).toBe(false);
  });

  it('reports the failure and keeps the dialog open', () => {
    saveDialogForm(opts({ request: () => throwError(() => ({ error: { detail: 'boom' } })) }));

    expect(notifyError).toHaveBeenCalledTimes(1);
    expect(notifyError.mock.calls[0][1]).toBe('Save failed');
    expect(close).not.toHaveBeenCalled();
  });

  it('uses a caller-supplied fallback message', () => {
    saveDialogForm(
      opts({ request: () => throwError(() => new Error('x')), errorFallback: 'Link failed' }),
    );
    expect(notifyError.mock.calls[0][1]).toBe('Link failed');
  });

  it('builds the request lazily, so an invalid form cannot send one', () => {
    // The request factory is deferred rather than an already-created Observable: a cold
    // Observable would be harmless, but a hot one (or an eagerly built payload) would not.
    let built = 0;
    saveDialogForm(opts({ form: { invalid: true }, request: () => { built++; return of({}); } }));
    expect(built).toBe(0);
  });

  it('unsubscribes when the component is destroyed', () => {
    // None of the five hand-written copies did this, so closing a dialog mid-request left the
    // callback to run against a destroyed component.
    let unsubscribed = false;
    const never = new Observable<unknown>(() => () => {
      unsubscribed = true;
    });

    saveDialogForm(opts({ request: () => never }));
    expect(unsubscribed).toBe(false);

    TestBed.resetTestingModule(); // destroys the injector that owns destroyRef
    expect(unsubscribed).toBe(true);
  });
});
