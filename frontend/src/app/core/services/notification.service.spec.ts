import { TestBed } from '@angular/core/testing';
import { MatSnackBar } from '@angular/material/snack-bar';
import { NotificationService } from './notification.service';

describe('NotificationService', () => {
  let service: NotificationService;
  let open: jest.Mock;

  beforeEach(() => {
    open = jest.fn();
    TestBed.configureTestingModule({
      providers: [{ provide: MatSnackBar, useValue: { open } }],
    });
    service = TestBed.inject(NotificationService);
  });

  /** The single argument list every call must produce: (message, 'OK', { duration }). */
  const lastCall = () => open.mock.calls[open.mock.calls.length - 1];

  describe('success', () => {
    it('shows the message with the dismiss action', () => {
      service.success('Site saved.');
      const [message, action] = lastCall();
      expect(message).toBe('Site saved.');
      expect(action).toBe('OK');
    });

    it('is dismissed automatically', () => {
      service.success('Site saved.');
      expect(lastCall()[2].duration).toBeGreaterThan(0);
    });
  });

  describe('error', () => {
    // The idiom this service replaces was written 58 times by hand:
    //   snackBar.open(err?.error?.detail ?? 'Save failed', 'OK', { duration: 4000 })
    // Each copy had to remember the shape of a FastAPI error body, and several got the
    // fallback or the duration subtly different.
    it("uses the backend's detail message when there is one", () => {
      service.error({ error: { detail: 'site_code already exists' } }, 'Save failed');
      expect(lastCall()[0]).toBe('site_code already exists');
    });

    it('falls back when the response carries no detail', () => {
      service.error({ error: {} }, 'Save failed');
      expect(lastCall()[0]).toBe('Save failed');
    });

    it('falls back when the error is not an HTTP response at all', () => {
      service.error(new Error('network down'), 'Save failed');
      expect(lastCall()[0]).toBe('Save failed');
    });

    it('falls back on null and undefined', () => {
      service.error(null, 'Save failed');
      expect(lastCall()[0]).toBe('Save failed');
      service.error(undefined, 'Save failed');
      expect(lastCall()[0]).toBe('Save failed');
    });

    it('ignores a detail that is not a string', () => {
      // FastAPI returns a list of objects for 422 validation errors, which is not
      // presentable as-is — the fallback is more use than "[object Object]".
      service.error({ error: { detail: [{ loc: ['body'], msg: 'field required' }] } }, 'Save failed');
      expect(lastCall()[0]).toBe('Save failed');
    });

    it('ignores a blank detail', () => {
      service.error({ error: { detail: '   ' } }, 'Save failed');
      expect(lastCall()[0]).toBe('Save failed');
    });

    it('stays visible longer than a success message', () => {
      service.success('ok');
      const successDuration = lastCall()[2].duration;
      service.error({ error: { detail: 'boom' } }, 'Save failed');
      expect(lastCall()[2].duration).toBeGreaterThan(successDuration);
    });
  });

  describe('long messages', () => {
    // The site-in-use refusal explains why the edit was rejected and what to do instead. At
    // 4 seconds it vanished before it could be read.
    const LONG =
      "Cannot change city_code on site 'NOBGOPark': 3 sample(s) reference it, and their " +
      'sample_code — which also names pipeline output directories on disk — is derived from ' +
      'it. Create a new site instead.';

    it('waits to be dismissed instead of timing out', () => {
      service.error({ error: { detail: LONG } }, 'Save failed');
      expect(lastCall()[2].duration).toBeUndefined();
    });

    it('still offers the dismiss action', () => {
      service.error({ error: { detail: LONG } }, 'Save failed');
      expect(lastCall()[1]).toBe('OK');
    });

    it('leaves short messages on their timer', () => {
      service.error({ error: { detail: 'Save failed' } }, 'Save failed');
      expect(lastCall()[2].duration).toBeGreaterThan(0);
    });

    it('applies to any kind of notification, not just errors', () => {
      service.message(LONG, 8000);
      expect(lastCall()[2].duration).toBeUndefined();
    });
  });

  describe('action', () => {
    it('shows a custom action label and returns the ref for onAction()', () => {
      const ref = { onAction: () => undefined };
      open.mockReturnValue(ref);

      const returned = service.action('Pipeline launched.', 'Go to Pipeline Runs', 10000);

      const [message, action, config] = lastCall();
      expect(message).toBe('Pipeline launched.');
      expect(action).toBe('Go to Pipeline Runs');
      expect(config.duration).toBe(10000);
      // Returned so the caller can react to the button — the reason this method exists
      // rather than components injecting MatSnackBar for this one case.
      expect(returned).toBe(ref);
    });
  });

  describe('message', () => {
    it('shows arbitrary text with a caller-chosen duration', () => {
      service.message('A long explanation the user needs time to read', 8000);
      const [text, action, config] = lastCall();
      expect(text).toBe('A long explanation the user needs time to read');
      expect(action).toBe('OK');
      expect(config.duration).toBe(8000);
    });
  });
});
