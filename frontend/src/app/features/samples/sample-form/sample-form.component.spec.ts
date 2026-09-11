import { TestBed } from '@angular/core/testing';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { Observable, of, throwError } from 'rxjs';

import { NotificationService } from '../../../core/services/notification.service';
import { LookupValuesService } from '../../../core/services/lookup-values.service';
import { SamplesService } from '../../../core/services/samples.service';
import { SitesService } from '../../../core/services/sites.service';
import { Sample } from '../../../core/models/sample.model';
import { SampleFormComponent } from './sample-form.component';

/**
 * Characterization spec for the dialog-save skeleton, written before C2 extracts it.
 *
 * Five forms reimplement `invalid guard -> saving flag -> build payload -> create/update ->
 * close on success / report and re-enable on failure`. These tests describe that skeleton as
 * it behaves today, so the extraction can be shown not to change it. They deliberately assert
 * behaviour reachable from the outside — what the service is called with, whether the dialog
 * closed, whether the form is usable again — not how the component stores its state, so they
 * survive the conversion of `saving` from a field to a signal.
 */

const EXISTING: Sample = {
  id: 'sample-1',
  sample_code: 'NOBGOPark_water',
  site_id: 'site-1',
  sample_type: 'water',
  sampling_date: '20240601',
  comments: 'original',
  created_at: '2024-01-01T00:00:00.000Z',
  updated_at: '2024-01-01T00:00:00.000Z',
} as Sample;

describe('SampleFormComponent — save', () => {
  let create: jest.Mock;
  let update: jest.Mock;
  let close: jest.Mock;
  let notifyError: jest.Mock;

  function setup(data: Sample | null) {
    create = jest.fn().mockReturnValue(of({}));
    update = jest.fn().mockReturnValue(of({}));
    close = jest.fn();
    notifyError = jest.fn();

    TestBed.configureTestingModule({
      imports: [SampleFormComponent, NoopAnimationsModule],
      providers: [
        { provide: SamplesService, useValue: { create, update } },
        { provide: SitesService, useValue: { list: () => of([]) } },
        { provide: LookupValuesService, useValue: { getList: () => of([]) } },
        { provide: NotificationService, useValue: { error: notifyError, success: jest.fn() } },
        { provide: MatDialogRef, useValue: { close } },
        { provide: MAT_DIALOG_DATA, useValue: data },
      ],
    });
    const fixture = TestBed.createComponent(SampleFormComponent);
    fixture.detectChanges();
    return fixture;
  }

  /** `saving` is read through the template so the assertion survives field -> signal.
   *
   * The save button carries no type or class — it is identified by its label, which also
   * flips to "Saving…" while a request is in flight.
   */
  const submitButton = (fixture: ReturnType<typeof setup>): HTMLButtonElement => {
    const buttons: HTMLButtonElement[] = Array.from(
      fixture.nativeElement.querySelectorAll('mat-dialog-actions button'),
    );
    const button = buttons.find((b) => /Sav(e|ing)/.test(b.textContent ?? ''));
    if (!button) {
      throw new Error(
        `save button not found among: ${buttons.map((b) => b.textContent?.trim()).join(', ')}`,
      );
    }
    return button;
  };

  describe('create mode', () => {
    it('sends a create payload and closes the dialog on success', () => {
      const fixture = setup(null);
      const component = fixture.componentInstance;
      component.form.patchValue({ sampling_date: '20240601', comments: 'hello' });

      component.save();

      expect(create).toHaveBeenCalledTimes(1);
      expect(update).not.toHaveBeenCalled();
      expect(close).toHaveBeenCalledWith(true);
    });

    it('omits empty fields so backend defaults apply', () => {
      const fixture = setup(null);
      fixture.componentInstance.form.patchValue({ sampling_date: '20240601', depth: '' });

      fixture.componentInstance.save();

      expect(Object.keys(create.mock.calls[0][0])).not.toContain('depth');
    });
  });

  describe('edit mode', () => {
    it('sends an update for the existing id and closes on success', () => {
      const fixture = setup(EXISTING);

      fixture.componentInstance.save();

      expect(update).toHaveBeenCalledTimes(1);
      expect(update.mock.calls[0][0]).toBe('sample-1');
      expect(create).not.toHaveBeenCalled();
      expect(close).toHaveBeenCalledWith(true);
    });

    it('sends null for a cleared field so the backend blanks it', () => {
      const fixture = setup(EXISTING);
      fixture.componentInstance.form.patchValue({ comments: '' });

      fixture.componentInstance.save();

      expect(update.mock.calls[0][1].comments).toBeNull();
    });

    it('keeps a cleared derived input rather than blanking it', () => {
      // site_id/sample_type feed sample_code, so clearing them is not a supported edit —
      // the keepIfEmpty guard exists because the schema did not enforce it at the time.
      const fixture = setup(EXISTING);
      fixture.componentInstance.form.patchValue({ site_id: null, sample_type: null });

      fixture.componentInstance.save();

      expect(update.mock.calls[0][1].site_id).toBeUndefined();
      expect(update.mock.calls[0][1].sample_type).toBeUndefined();
    });
  });

  describe('guards and failure', () => {
    it('does nothing at all when the form is invalid', () => {
      const fixture = setup(null);
      fixture.componentInstance.form.patchValue({ sampling_date: 'not-a-date' });

      fixture.componentInstance.save();

      expect(create).not.toHaveBeenCalled();
      expect(update).not.toHaveBeenCalled();
      expect(close).not.toHaveBeenCalled();
    });

    it('reports the failure, keeps the dialog open and re-enables submission', () => {
      const fixture = setup(null);
      create.mockReturnValue(throwError(() => ({ error: { detail: 'boom' } })));
      fixture.componentInstance.form.patchValue({ sampling_date: '20240601' });

      fixture.componentInstance.save();
      fixture.detectChanges();

      expect(notifyError).toHaveBeenCalledTimes(1);
      expect(notifyError.mock.calls[0][1]).toBe('Save failed');
      expect(close).not.toHaveBeenCalled();
      expect(submitButton(fixture).disabled).toBe(false);
    });

    it('blocks submission while the request is in flight', () => {
      const fixture = setup(null);
      // Never settles, so the in-flight state is observable.
      create.mockReturnValue(new Observable(() => undefined));
      fixture.componentInstance.form.patchValue({ sampling_date: '20240601' });

      fixture.componentInstance.save();
      fixture.detectChanges();

      expect(submitButton(fixture).disabled).toBe(true);
    });
  });
});
