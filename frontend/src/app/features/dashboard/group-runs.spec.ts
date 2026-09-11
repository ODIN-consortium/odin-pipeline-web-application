import { TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatDialog } from '@angular/material/dialog';
import { Router } from '@angular/router';
import { of } from 'rxjs';

import { DiscoveryService } from '../../core/services/discovery.service';
import { NanoporeRunAccessionsService } from '../../core/services/nanopore-run-accessions.service';
import { PipelineService } from '../../core/services/pipeline.service';
import { SettingsService } from '../../core/services/settings.service';
import { BiomemeRunsService } from '../../core/services/biomeme-runs.service';
import { NotificationService } from '../../core/services/notification.service';
import { WarningDismissalService } from '../../core/services/warning-dismissal.service';
import { NanoporeDiscoveryResult, NanoporeRunStatus } from '../../core/models/discovery.model';
import { DashboardPageComponent } from './dashboard-page.component';
import { DisplayItem, groupRuns } from './group-runs';

/**
 * Tests for the dashboard's run grouping.
 *
 * groupRuns decides whether two sequencing runs appear as one continued run, as two runs of the
 * same sample, or as unrelated — the difference between merging real data and not. It was ~125
 * lines inside a computed on DashboardPageComponent, the largest piece of untested logic in the
 * frontend; these tests were written against that version first and passed unchanged once it moved
 * out, which is the evidence the extraction preserved it. Three mutations were used to confirm
 * they were not vacuous.
 *
 * They now call the pure function directly. The last describe covers the one thing that is not
 * pure: that the component actually feeds it the right two signals.
 */

const EVIDENCE = { flow_cell_id: 'FC1', time_gap_hours: 2, kit: 'SQK-LSK114', run_name_match: true };

function run(accession: string, over: Partial<NanoporeRunStatus> = {}): NanoporeRunStatus {
  return {
    run_accession: accession,
    run_path: null,
    run_name: accession,
    sample_name: null,
    in_metadata: true,
    on_disk: true,
    barcodes: [],
    is_excluded: false,
    status: 'ready',
    related_run_accessions: [],
    auto_merge: null,
    last_pipeline_run_status: null,
    last_pipeline_run_id: null,
    last_pipeline_run_type: null,
    last_pipeline_run_extract_target: null,
    pipeline_runs: [],
    confidence_report_targets: [],
    output_on_disk: false,
    artic_on_disk: false,
    postprocessing_fresh: null,
    metadata_warnings: [],
    continuation_run_accessions: [],
    continuation_confidence: null,
    continuation_evidence: null,
    ...over,
  } as NanoporeRunStatus;
}

describe('groupRuns', () => {
  const items = (runs: NanoporeRunStatus[], hideExcluded = false): DisplayItem[] =>
    groupRuns(runs, hideExcluded);

  const accessionsOf = (item: { kind: string; runs?: NanoporeRunStatus[]; run?: NanoporeRunStatus }) =>
    item.kind === 'single' ? [item.run!.run_accession] : item.runs!.map((r) => r.run_accession);

  // ── the base case ──────────────────────────────────────────────────────────

  it('shows unlinked runs as separate rows, in discovery order', () => {
    const result = items([run('ERR3'), run('ERR1'), run('ERR2')]);
    expect(result.map((i) => i.kind)).toEqual(['single', 'single', 'single']);
    expect(result.flatMap(accessionsOf)).toEqual(['ERR3', 'ERR1', 'ERR2']);
  });

  // ── continuation groups ────────────────────────────────────────────────────

  it('groups two runs that name each other as continuations', () => {
    const result = items([
      run('ERR1', { continuation_run_accessions: ['ERR2'] }),
      run('ERR2', { continuation_run_accessions: ['ERR1'] }),
    ]);
    expect(result).toHaveLength(1);
    expect(result[0].kind).toBe('continuation-group');
    expect(accessionsOf(result[0]).sort()).toEqual(['ERR1', 'ERR2']);
  });

  it('groups a chain transitively, so A-B and B-C become one group of three', () => {
    // The property that makes this a connected-components problem rather than a pairing one.
    const result = items([
      run('ERR1', { continuation_run_accessions: ['ERR2'] }),
      run('ERR2', { continuation_run_accessions: ['ERR1', 'ERR3'] }),
      run('ERR3', { continuation_run_accessions: ['ERR2'] }),
    ]);
    expect(result).toHaveLength(1);
    expect(accessionsOf(result[0]).sort()).toEqual(['ERR1', 'ERR2', 'ERR3']);
  });

  it('treats a link as mutual even when only one side declares it', () => {
    const result = items([
      run('ERR1', { continuation_run_accessions: ['ERR2'] }),
      run('ERR2'),
    ]);
    expect(result).toHaveLength(1);
    expect(accessionsOf(result[0]).sort()).toEqual(['ERR1', 'ERR2']);
  });

  it('ignores a link to a run that is not in the list', () => {
    const result = items([run('ERR1', { continuation_run_accessions: ['ERR-GONE'] })]);
    expect(result.map((i) => i.kind)).toEqual(['single']);
  });

  // ── the separation the algorithm exists to preserve ─────────────────────────

  it('keeps related links out of continuation groups', () => {
    // The comment in the source: a sample-related link must not pull an unrelated run into a
    // continuation group. ERR1-ERR2 continue each other; ERR3 merely shares a sample with ERR2.
    const result = items([
      run('ERR1', { continuation_run_accessions: ['ERR2'] }),
      run('ERR2', { continuation_run_accessions: ['ERR1'], related_run_accessions: ['ERR3'] }),
      run('ERR3', { related_run_accessions: ['ERR2'] }),
    ]);
    const continuation = result.find((i) => i.kind === 'continuation-group')!;
    expect(accessionsOf(continuation).sort()).toEqual(['ERR1', 'ERR2']);
    expect(result.filter((i) => i.kind === 'sample-group')).toHaveLength(0);
    expect(result.filter((i) => i.kind === 'single').flatMap(accessionsOf)).toEqual(['ERR3']);
  });

  it('forms a sample group only from runs no continuation group already claimed', () => {
    const result = items([
      run('ERR1', { related_run_accessions: ['ERR2'], sample_name: 'S1' }),
      run('ERR2', { related_run_accessions: ['ERR1'], sample_name: 'S1' }),
    ]);
    expect(result).toHaveLength(1);
    expect(result[0].kind).toBe('sample-group');
  });

  it('does not group a run with itself when its component has only one member', () => {
    const result = items([run('ERR1', { continuation_run_accessions: ['ERR-GONE'] }), run('ERR2')]);
    expect(result.every((i) => i.kind === 'single')).toBe(true);
  });

  // ── group attributes ───────────────────────────────────────────────────────

  it.each([
    ['every run says merge', true, true, true],
    ['every run says separate', false, false, false],
    ['the runs disagree', true, false, null],
    ['a run has not decided', true, null, null],
  ])('reports the merge decision as unanimous or undecided: %s', (_name, a, b, expected) => {
    const result = items([
      run('ERR1', { continuation_run_accessions: ['ERR2'], auto_merge: a as boolean | null }),
      run('ERR2', { continuation_run_accessions: ['ERR1'], auto_merge: b as boolean | null }),
    ]);
    expect((result[0] as { mergeDecision: boolean | null }).mergeDecision).toBe(expected);
  });

  it('takes the likely evidence when any run in the group is confident', () => {
    const likely = { ...EVIDENCE, flow_cell_id: 'FC-LIKELY' };
    const result = items([
      run('ERR1', {
        continuation_run_accessions: ['ERR2'],
        continuation_confidence: 'possible',
        continuation_evidence: EVIDENCE,
      }),
      run('ERR2', {
        continuation_run_accessions: ['ERR1'],
        continuation_confidence: 'likely',
        continuation_evidence: likely,
      }),
    ]);
    const group = result[0] as { confidence: string; evidence: { flow_cell_id: string } };
    expect(group.confidence).toBe('likely');
    expect(group.evidence.flow_cell_id).toBe('FC-LIKELY');
  });

  it('falls back to possible, and to a placeholder when no run carries evidence', () => {
    const result = items([
      run('ERR1', { continuation_run_accessions: ['ERR2'] }),
      run('ERR2', { continuation_run_accessions: ['ERR1'] }),
    ]);
    const group = result[0] as { confidence: string; evidence: { flow_cell_id: string } };
    expect(group.confidence).toBe('possible');
    expect(group.evidence.flow_cell_id).toBe('unknown');
  });

  it('names a sample group after its first named run, or "Multiple runs"', () => {
    const named = items([
      run('ERR1', { related_run_accessions: ['ERR2'] }),
      run('ERR2', { related_run_accessions: ['ERR1'], sample_name: 'Bergen water' }),
    ]);
    expect((named[0] as { sampleName: string }).sampleName).toBe('Bergen water');

    const unnamed = items([
      run('ERR1', { related_run_accessions: ['ERR2'] }),
      run('ERR2', { related_run_accessions: ['ERR1'] }),
    ]);
    expect((unnamed[0] as { sampleName: string }).sampleName).toBe('Multiple runs');
  });

  // ── excluded runs ──────────────────────────────────────────────────────────

  it('drops excluded runs before grouping, so hiding one can dissolve its group', () => {
    // Not merely a display filter: an excluded run is removed before adjacency is built, so the
    // pair it belonged to stops being a pair.
    const runs = [
      run('ERR1', { continuation_run_accessions: ['ERR2'] }),
      run('ERR2', { continuation_run_accessions: ['ERR1'], is_excluded: true }),
    ];
    expect(items(runs, false)[0].kind).toBe('continuation-group');

    const hidden = items(runs, true);
    expect(hidden).toHaveLength(1);
    expect(hidden[0].kind).toBe('single');
    expect(accessionsOf(hidden[0])).toEqual(['ERR1']);
  });

  // ── ordering ───────────────────────────────────────────────────────────────

  it('places each group at the position of its earliest member', () => {
    const result = items([
      run('ERR1'),
      run('ERR2', { continuation_run_accessions: ['ERR4'] }),
      run('ERR3'),
      run('ERR4', { continuation_run_accessions: ['ERR2'] }),
    ]);
    expect(result.map((i) => accessionsOf(i).sort().join('+'))).toEqual([
      'ERR1',
      'ERR2+ERR4',
      'ERR3',
    ]);
  });
});

describe('DashboardPageComponent — grouping wiring', () => {
  /**
   * The component is created but never change-detected, so ngOnInit never runs: no disk scan, no
   * 2-minute poll timer, no HTTP. displayItems is a computed over two public signals.
   */
  function setup() {
    const noop = {};
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [DashboardPageComponent, NoopAnimationsModule],
      providers: [
        { provide: DiscoveryService, useValue: { scanNanopore: () => of(null) } },
        { provide: NanoporeRunAccessionsService, useValue: noop },
        { provide: PipelineService, useValue: noop },
        { provide: SettingsService, useValue: { get: () => of({ value: null }) } },
        { provide: BiomemeRunsService, useValue: noop },
        { provide: NotificationService, useValue: { success: jest.fn(), error: jest.fn() } },
        { provide: WarningDismissalService, useValue: { isDismissed: () => false } },
        { provide: Router, useValue: { navigate: jest.fn() } },
      ],
    });
    TestBed.overrideProvider(MatDialog, { useValue: { open: jest.fn() } });
    return TestBed.createComponent(DashboardPageComponent).componentInstance;
  }

  it('feeds the discovered runs through the grouping', () => {
    const component = setup();
    component.nanoporeResult.set({
      runs: [
        run('ERR1', { continuation_run_accessions: ['ERR2'] }),
        run('ERR2', { continuation_run_accessions: ['ERR1'] }),
      ],
    } as NanoporeDiscoveryResult);
    expect(component.displayItems().map((i) => i.kind)).toEqual(['continuation-group']);
  });

  it('re-groups when the operator hides excluded runs', () => {
    // hideExcluded is not a display filter: it changes what the grouping sees, so toggling it can
    // dissolve a group entirely.
    const component = setup();
    component.nanoporeResult.set({
      runs: [
        run('ERR1', { continuation_run_accessions: ['ERR2'] }),
        run('ERR2', { continuation_run_accessions: ['ERR1'], is_excluded: true }),
      ],
    } as NanoporeDiscoveryResult);
    expect(component.displayItems().map((i) => i.kind)).toEqual(['continuation-group']);

    component.hideExcluded.set(true);
    expect(component.displayItems().map((i) => i.kind)).toEqual(['single']);
  });

  it('groups nothing before a scan has returned', () => {
    expect(setup().displayItems()).toEqual([]);
  });
});
