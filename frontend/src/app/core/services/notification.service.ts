import { Injectable, inject } from '@angular/core';
import { MatSnackBar, MatSnackBarRef, TextOnlySnackBar } from '@angular/material/snack-bar';

/** How long each kind of notification stays on screen. */
const SUCCESS_DURATION_MS = 3000;
const ERROR_DURATION_MS = 4000;
/**
 * Above this length a notification stays until dismissed rather than timing out.
 * Roughly a line and a half — beyond that the reader loses the race with the timer.
 */
const LONG_MESSAGE_CHARS = 120;

/**
 * The one place that shows a snackbar.
 *
 * This replaces the idiom
 * `snackBar.open(err?.error?.detail ?? 'Save failed', 'OK', { duration: 4000 })`,
 * which was hand-written 58 times across 15 components. Every copy had to know the shape of
 * a FastAPI error body, and they had drifted: durations of 2500, 3000, 4000 and 8000 for
 * comparable situations, and fallbacks differing only by a trailing full stop.
 *
 * Components inject this instead of `MatSnackBar` so the presentation stays in one file.
 */
@Injectable({ providedIn: 'root' })
export class NotificationService {
  private readonly snackBar = inject(MatSnackBar);

  /** Confirm something worked, e.g. `success('Site saved.')`. */
  success(message: string): void {
    this.show(message, SUCCESS_DURATION_MS);
  }

  /**
   * Report a failed request, preferring the backend's own explanation.
   *
   * `fallback` is used whenever the error carries nothing presentable — a network failure, a
   * non-HTTP exception, or a 422 whose `detail` is FastAPI's array of validation objects,
   * which would otherwise render as "[object Object]".
   */
  error(err: unknown, fallback: string): void {
    this.show(errorDetail(err) ?? fallback, ERROR_DURATION_MS);
  }

  /**
   * Show arbitrary text for a caller-chosen duration.
   *
   * For the cases that are neither a plain success nor a request failure — a long explanation
   * the operator needs time to read, for instance.
   */
  message(text: string, durationMs: number): void {
    this.show(text, durationMs);
  }

  /**
   * Show a notification whose button does something other than dismiss.
   *
   * Returns the ref so the caller can subscribe to `onAction()`. This exists so that no
   * component needs to inject `MatSnackBar` directly — otherwise the "one place that shows a
   * snackbar" property would hold for most of the app and quietly not for the dashboard.
   */
  action(
    message: string,
    actionLabel: string,
    durationMs: number,
  ): MatSnackBarRef<TextOnlySnackBar> {
    return this.snackBar.open(message, actionLabel, { duration: durationMs });
  }

  private show(message: string, durationMs: number): void {
    // A message longer than a glance waits for the reader instead of timing out. The
    // site-in-use refusal, for instance, explains why the edit was rejected and what to do
    // instead — it was disappearing before it could be read. Dismissing is the 'OK' button.
    const config = message.length > LONG_MESSAGE_CHARS ? {} : { duration: durationMs };
    this.snackBar.open(message, 'OK', config);
  }
}

/**
 * Pull a human-readable message out of an HttpErrorResponse-shaped value.
 *
 * Narrowed from `unknown` rather than cast, because this is fed straight from RxJS error
 * callbacks and genuinely can be anything.
 */
function errorDetail(err: unknown): string | null {
  if (typeof err !== 'object' || err === null) {
    return null;
  }
  const body = (err as { error?: unknown }).error;
  if (typeof body !== 'object' || body === null) {
    return null;
  }
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail !== 'string' || detail.trim() === '') {
    return null;
  }
  return detail;
}
