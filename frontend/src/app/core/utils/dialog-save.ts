import { DestroyRef, WritableSignal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Observable } from 'rxjs';

import { NotificationService } from '../services/notification.service';

/** What {@link saveDialogForm} needs from its caller. */
export interface DialogSaveOptions<T> {
  /** Checked before anything else; an invalid form submits nothing. */
  form: { invalid: boolean };
  /** Set while the request is in flight so the template can disable submission. */
  saving: WritableSignal<boolean>;
  /** Closed with `true` on success, so the opener knows to reload. */
  dialogRef: { close: (result?: unknown) => void };
  notify: NotificationService;
  destroyRef: DestroyRef;
  /** Builds and sends the request. Deferred so nothing is sent for an invalid form. */
  request: () => Observable<T>;
  /** Shown when the failure carries no message of its own. */
  errorFallback?: string;
}

/**
 * The save skeleton every dialog form shares.
 *
 * `invalid guard -> saving flag -> request -> close on success / report and re-enable on
 * failure` was written out in five forms. Only the payload differs between them, and that
 * stays with each form — a sample's payload rules are not a run's.
 *
 * Two things are fixed here rather than in five places. The subscription is now tied to
 * `DestroyRef`: none of the copies unsubscribed, so closing a dialog mid-request left the
 * callback to run against a destroyed component, against the rule in CLAUDE.md that no
 * `.subscribe()` goes without cleanup. And `saving` is a signal, so the failure path no
 * longer needs a manual `markForCheck()` to make the button usable again under OnPush.
 */
export function saveDialogForm<T>(options: DialogSaveOptions<T>): void {
  if (options.form.invalid) {
    return;
  }
  options.saving.set(true);
  options
    .request()
    .pipe(takeUntilDestroyed(options.destroyRef))
    .subscribe({
      next: () => options.dialogRef.close(true),
      error: (err: unknown) => {
        options.saving.set(false);
        options.notify.error(err, options.errorFallback ?? 'Save failed');
      },
    });
}
