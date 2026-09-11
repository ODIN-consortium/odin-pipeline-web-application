import { TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatDialog } from '@angular/material/dialog';
import { provideRouter } from '@angular/router';
import { Subject, of, throwError } from 'rxjs';

import { DiscoveryService } from '../../core/services/discovery.service';
import { NanoporeRunAccessionsService } from '../../core/services/nanopore-run-accessions.service';
import { PipelineService } from '../../core/services/pipeline.service';
import { SettingsService } from '../../core/services/settings.service';
import { BiomemeRunsService } from '../../core/services/biomeme-runs.service';
import { NotificationService } from '../../core/services/notification.service';
import { WarningDismissalService } from '../../core/services/warning-dismissal.service';
import { NanoporeRunStatus } from '../../core/models/discovery.model';
import { DashboardPageComponent } from './dashboard-page.component';

/**
 * Covers the two pieces of dashboard lifecycle D10 reworked: the group merge decision, which was a
 * hand-rolled countdown, and the background refresh, which was a setInterval plus a manually
 * managed visibilitychange listener.
 */

const RUNS = [
  { run_accession: 'ERR1' } as NanoporeRunStatus,
  { run_accession: 'ERR2' } as NanoporeRunStatus,
];

describe('DashboardPageComponent', () => {
  let setMergeDecision: jest.Mock;
  let clearMergeDecision: jest.Mock;
  let nanopore: jest.Mock;
  let biomeme: jest.Mock;
  let notifyError: jest.Mock;

  function setup() {
    setMergeDecision = jest.fn().mockReturnValue(of({}));
    clearMergeDecision = jest.fn().mockReturnValue(of({}));
    nanopore = jest.fn().mockReturnValue(of({ runs: [] }));
    biomeme = jest.fn().mockReturnValue(of({ folders: [] }));
    notifyError = jest.fn();

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [DashboardPageComponent, NoopAnimationsModule],
      providers: [
        { provide: DiscoveryService, useValue: { nanopore, biomeme } },
        { provide: NanoporeRunAccessionsService, useValue: { list: () => of([]) } },
        { provide: PipelineService, useValue: { setMergeDecision, clearMergeDecision } },
        { provide: SettingsService, useValue: { get: () => of({ value: null }) } },
        { provide: BiomemeRunsService, useValue: {} },
        { provide: NotificationService, useValue: { success: jest.fn(), error: notifyError } },
        { provide: WarningDismissalService, useValue: { isDismissed: () => false } },
        // The template renders routerLinks, which need a real Router once it is change-detected.
        provideRouter([]),
      ],
    });
    TestBed.overrideProvider(MatDialog, { useValue: { open: jest.fn() } });
    return TestBed.createComponent(DashboardPageComponent);
  }

  // ── group merge decisions ──────────────────────────────────────────────────

  describe('setGroupMerge', () => {
    it('applies the decision to every run in the group, then reloads once', () => {
      const component = setup().componentInstance;
      component.setGroupMerge(RUNS, true);

      expect(setMergeDecision).toHaveBeenCalledTimes(2);
      expect(setMergeDecision).toHaveBeenCalledWith('ERR1', { auto_merge: true });
      expect(setMergeDecision).toHaveBeenCalledWith('ERR2', { auto_merge: true });
      expect(nanopore).toHaveBeenCalledTimes(1);
    });

    it('clears the decision when the value is null', () => {
      const component = setup().componentInstance;
      component.setGroupMerge(RUNS, null);

      expect(clearMergeDecision).toHaveBeenCalledTimes(2);
      expect(setMergeDecision).not.toHaveBeenCalled();
    });

    it('waits for every run before reloading', () => {
      // The countdown this replaced existed to reload exactly once, after the last response.
      const first = new Subject<unknown>();
      const component = setup().componentInstance;
      setMergeDecision.mockReturnValueOnce(first).mockReturnValueOnce(of({}));

      component.setGroupMerge(RUNS, true);
      expect(nanopore).not.toHaveBeenCalled();

      first.next({});
      first.complete();
      expect(nanopore).toHaveBeenCalledTimes(1);
    });

    it('still applies the others, and still reloads, when one run fails', () => {
      // A rejected run must not cancel its siblings: they were already sent.
      const component = setup().componentInstance;
      setMergeDecision
        .mockReturnValueOnce(throwError(() => new Error('conflict')))
        .mockReturnValueOnce(of({}));

      component.setGroupMerge(RUNS, true);

      expect(setMergeDecision).toHaveBeenCalledTimes(2);
      expect(nanopore).toHaveBeenCalledTimes(1);
    });

    it('reports a failure instead of leaving it silent', () => {
      // The countdown treated error exactly like success, so a rejected decision looked recorded.
      const component = setup().componentInstance;
      setMergeDecision.mockReturnValue(throwError(() => new Error('conflict')));

      component.setGroupMerge(RUNS, true);

      expect(notifyError).toHaveBeenCalledWith(expect.any(Error), expect.stringContaining('2 run(s)'));
    });
  });

  // ── background refresh ─────────────────────────────────────────────────────

  describe('background refresh', () => {
    it('rescans when the operator returns to the tab', () => {
      const fixture = setup();
      fixture.detectChanges(); // ngOnInit: initial scan, then the background stream
      const afterInitialScan = nanopore.mock.calls.length;

      document.dispatchEvent(new Event('visibilitychange'));

      expect(nanopore.mock.calls.length).toBeGreaterThan(afterInitialScan);
    });

    it('stops listening once the page is destroyed', () => {
      // The listener used to be removed by hand in ngOnDestroy; it is now owned by DestroyRef,
      // and this is what proves the ownership actually releases it.
      const fixture = setup();
      fixture.detectChanges();
      fixture.destroy();
      const afterDestroy = nanopore.mock.calls.length;

      document.dispatchEvent(new Event('visibilitychange'));

      expect(nanopore.mock.calls.length).toBe(afterDestroy);
    });
  });
});

describe('DashboardPageComponent — OnPush rendering', () => {
  /**
   * The dashboard is OnPush as of D10. Everything it renders is a signal, but the metadata
   * warnings are the one piece of state that lives in a *service* rather than the component, read
   * through component methods during template evaluation. This walks that path through the DOM:
   * if dismissing a warning failed to mark the view dirty, the line would still be on screen.
   */
  function setup(runs: unknown[]) {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [DashboardPageComponent, NoopAnimationsModule],
      providers: [
        {
          provide: DiscoveryService,
          useValue: { nanopore: () => of({ runs }), biomeme: () => of({ folders: [] }) },
        },
        { provide: NanoporeRunAccessionsService, useValue: { list: () => of([]) } },
        { provide: PipelineService, useValue: { setMergeDecision: jest.fn(), clearMergeDecision: jest.fn() } },
        { provide: SettingsService, useValue: { get: () => of({ value: null }) } },
        { provide: BiomemeRunsService, useValue: {} },
        { provide: NotificationService, useValue: { success: jest.fn(), error: jest.fn() } },
        { provide: WarningDismissalService, useValue: new WarningDismissalService() },
        provideRouter([]),
      ],
    });
    TestBed.overrideProvider(MatDialog, { useValue: { open: jest.fn() } });
    const fixture = TestBed.createComponent(DashboardPageComponent);
    fixture.detectChanges();
    return fixture;
  }

  const run = (accession: string, over: Record<string, unknown> = {}) => ({
    run_accession: accession,
    run_name: accession,
    barcodes: [],
    metadata_warnings: [],
    related_run_accessions: [],
    continuation_run_accessions: [],
    pipeline_runs: [],
    confidence_report_targets: [],
    ...over,
  });

  it('removes a dismissed warning from the view', () => {
    // The dismissible warning strip belongs to a group header, so the fixture has to be a group:
    // a single run shows its warnings as an inline tooltip with nothing to click.
    const fixture = setup([
      run('ERR1', {
        continuation_run_accessions: ['ERR2'],
        metadata_warnings: ['Sample used by two runs'],
      }),
      run('ERR2', { continuation_run_accessions: ['ERR1'] }),
    ]);
    expect(fixture.nativeElement.textContent).toContain('Sample used by two runs');

    const dismiss: HTMLButtonElement = fixture.nativeElement.querySelector('.warning-dismiss-btn');
    expect(dismiss).toBeTruthy();
    dismiss.click();
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).not.toContain('Sample used by two runs');
  });
});
