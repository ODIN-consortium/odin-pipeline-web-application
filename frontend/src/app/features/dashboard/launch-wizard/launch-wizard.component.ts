import {
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  inject,
  OnInit,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import {
  ReactiveFormsModule,
  FormBuilder,
  FormArray,
  FormGroup,
  Validators,
  AbstractControl,
  ValidationErrors,
} from '@angular/forms';
import { MAT_DIALOG_DATA, MatDialog, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSelectModule } from '@angular/material/select';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatTabsModule } from '@angular/material/tabs';
import { MatDividerModule } from '@angular/material/divider';

import { DiscoveryService } from '../../../core/services/discovery.service';
import { NotificationService } from '../../../core/services/notification.service';
import { PipelineService } from '../../../core/services/pipeline.service';
import { SitesService } from '../../../core/services/sites.service';
import { LookupValuesService } from '../../../core/services/lookup-values.service';
import { ConfirmDialogComponent } from '../../../shared/components/confirm-dialog.component';
import { WarningStripComponent } from '../../../shared/components/warning-strip.component';
import {
  BarcodeRegisterInfo,
  NanoporeReadiness,
  NanoporeRegisterPayload,
} from '../../../core/models/discovery.model';
import { LookupValue } from '../../../core/models/lookup-value.model';
import { Site } from '../../../core/models/site.model';
import { ExtractTarget, PipelineRun, PipelineType } from '../../../core/models/pipeline.model';
import { WarningDismissalService } from '../../../core/services/warning-dismissal.service';
import { SiteRegisterFormComponent } from './site-register-form.component';
import {
  BarcodeMetadataTableComponent,
  NOISE_THRESHOLD,
} from './barcode-metadata-table.component';
import {
  BarcodeRowValue,
  YYYYMMDD,
  isBarcodeRowComplete,
  startedButIncompleteBarcodes,
} from './barcode-row';

// Re-exported so existing importers (specs) keep working; the definitions moved to
// barcode-row.ts when the barcode table became its own component.
export { YYYYMMDD, isBarcodeRowComplete, isBarcodeRowStarted } from './barcode-row';
export type { BarcodeRowValue } from './barcode-row';

export interface LaunchWizardData {
  run_accession: string;
  artic_on_disk: boolean;
  on_disk: boolean;
}

export interface LaunchWizardResult {
  registered: boolean;
  readiness: NanoporeReadiness;
  pipelineRun?: PipelineRun;
}

type Step = 'loading' | 'register' | 'launch' | 'error';

@Component({
  selector: 'app-launch-wizard',
  standalone: true,
  imports: [
    CommonModule,
    ReactiveFormsModule,
    MatDialogModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatSelectModule,
    MatSlideToggleModule,
    MatTooltipModule,
    MatTabsModule,
    MatDividerModule,
    SiteRegisterFormComponent,
    BarcodeMetadataTableComponent,
    WarningStripComponent,
  ],
  templateUrl: './launch-wizard.component.html',
  styleUrls: ['./launch-wizard.component.css', './wizard-shared.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LaunchWizardComponent implements OnInit {
  readonly data: LaunchWizardData = inject(MAT_DIALOG_DATA);
  private readonly ref = inject(MatDialogRef<LaunchWizardComponent, LaunchWizardResult>);
  private readonly discoveryService = inject(DiscoveryService);
  private readonly pipelineService = inject(PipelineService);
  private readonly sitesService = inject(SitesService);
  private readonly lookupValues = inject(LookupValuesService);
  private readonly notify = inject(NotificationService);
  private readonly dialog = inject(MatDialog);
  private readonly fb = inject(FormBuilder);
  private readonly warningDismissal = inject(WarningDismissalService);

  readonly step = signal<Step>('loading');
  readonly readiness = signal<NanoporeReadiness | null>(null);
  readonly sites = signal<Site[]>([]);
  readonly submitting = signal(false);
  readonly launching = signal(false);
  readonly errorMsg = signal('');
  readonly launchError = signal('');
  readonly resumeRun = signal(false);
  readonly saveReads = signal(false);
  readonly extractTargets = signal<ExtractTarget[]>([]);
  readonly extractTargetCtrl = this.fb.nonNullable.control('');
  readonly extractTaxonLabelCtrl = this.fb.nonNullable.control('');
  readonly extractRefAccessionCtrl = this.fb.nonNullable.control('');
  readonly squirrelOnlyMode = computed(() => !this.data.on_disk && this.data.artic_on_disk);

  getVisibleMetadataWarnings(): string[] {
    const r = this.readiness();
    if (!r) {
      return [];
    }
    return (r.metadata_warnings ?? []).filter(
      (warning) =>
        !this.warningDismissal.isDismissed(
          this.warningDismissal.buildRunWarningKey(r.run_accession, warning),
        ),
    );
  }

  getHiddenMetadataWarningCount(): number {
    const r = this.readiness();
    if (!r) {
      return 0;
    }
    const total = r.metadata_warnings?.length ?? 0;
    return Math.max(
      total - this.getVisibleMetadataWarnings().length,
      this.warningDismissal.countDismissedForRun(r.run_accession),
    );
  }

  dismissMetadataWarning(warning: string): void {
    const r = this.readiness();
    if (!r) {
      return;
    }
    this.warningDismissal.dismiss(this.warningDismissal.buildRunWarningKey(r.run_accession, warning));
  }

  restoreMetadataWarnings(): void {
    const r = this.readiness();
    if (!r) {
      return;
    }
    this.warningDismissal.undismissRunWarnings(r.run_accession);
  }

  readonly noiseCount = signal(0);
  private readonly barcodeReadCounts = new Map<string, number>();

  readonly selectedPipelineType = signal<PipelineType | null>(null);
  readonly autoMerge = signal(false);

  readonly mpoxClades = signal<string[]>([]);
  readonly mpoxSchemes = signal<string[]>([]);

  readonly mpoxCladeCtrl = this.fb.nonNullable.control('', Validators.required);
  readonly mpoxSchemeCtrl = this.fb.nonNullable.control('', Validators.required);

  isPipelineDisabled(pt: PipelineType): boolean {
    if (this.launching()) return true;
    if (pt === 'squirrel') return !this.data.artic_on_disk || this.mpoxCladeCtrl.invalid;
    if (pt === 'mpox') return !this.data.on_disk || this.mpoxCladeCtrl.invalid || this.mpoxSchemeCtrl.invalid;
    return !this.data.on_disk;
  }

  pipelineDisabledTooltip(pt: PipelineType): string {
    if (pt === 'squirrel' && !this.data.artic_on_disk) {
      return 'Run the Mpox pipeline first — squirrel requires artic consensus output on disk';
    }
    if (pt !== 'squirrel' && !this.data.on_disk) {
      return 'No FASTQ data found for this run';
    }
    return '';
  }

  readonly allProtocolIds = signal<LookupValue[]>([]);
  readonly allKitIds = signal<LookupValue[]>([]);
  readonly allSampleTypes = signal<LookupValue[]>([]);
  readonly allMpoxTypes = signal<LookupValue[]>([]);
  readonly newSiteExpanded = signal(false);
  readonly savingSite = signal(false);

  private readonly destroyRef = inject(DestroyRef);

  readonly form = this.fb.group({
    site_id: [null as string | null],
    country: [''],
    country_code: [''],
    city: [''],
    city_code: [''],
    site: [''],
    site_code: [{ value: '', disabled: true }],
    location: [''],
    latitude: [null as number | null],
    longitude: [null as number | null],
    // Run-level sequencing metadata
    protocol_id: [null as string | null],
    sequencing_kit_id: [null as string | null],
    sample_type: [null as string | null],
    barcodes: this.fb.array([] as FormGroup[]),
  });

  get barcodesArray(): FormArray {
    return this.form.get('barcodes') as FormArray;
  }

  ngOnInit(): void {
    this.sitesService.list().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: (s) => this.sites.set(s) });

    this.lookupValues.getList('protocol_id').pipe(takeUntilDestroyed(this.destroyRef)).subscribe((lv) => {
      this.allProtocolIds.set(lv);
    });
    this.lookupValues.getList('sequencing_kit_id').pipe(takeUntilDestroyed(this.destroyRef)).subscribe((lv) => {
      this.allKitIds.set(lv);
    });
    this.lookupValues.getList('sample_type').pipe(takeUntilDestroyed(this.destroyRef)).subscribe((lv) => {
      this.allSampleTypes.set(lv);
    });

    this.lookupValues.getList('mpox_type').pipe(takeUntilDestroyed(this.destroyRef)).subscribe((lv) => {
      this.allMpoxTypes.set(lv);
    });

    this.pipelineService.getMpoxOptions().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: ({ clades, schemes }) => {
        this.mpoxClades.set(clades);
        this.mpoxSchemes.set(schemes);
        this.mpoxCladeCtrl.setValue(clades[0] ?? '');
        this.mpoxSchemeCtrl.setValue(schemes[0] ?? '');
      },
    });

    this.pipelineService.getExtractTargets().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: ({ targets }) => {
        this.extractTargets.set(targets);
        this.extractTargetCtrl.setValue(targets[0]?.label ?? '');
      },
    });

    this.loadReadiness();
  }

  /**
   * Save the new site without closing the wizard, then refresh the sites list
   * and auto-select the newly created site in any barcode row that has no site yet.
   */
  saveSite(): void {
    const v = this.form.value;
    if (!v.country) return;
    this.savingSite.set(true);

    const payload = {
      country: v.country || undefined,
      country_code: v.country_code || undefined,
      city: v.city || undefined,
      city_code: v.city_code || undefined,
      site: v.site || undefined,
      location: v.location || undefined,
      latitude: v.latitude ?? null,
      longitude: v.longitude ?? null,
      barcodes: [],  // save site only, no barcodes
    };

    this.discoveryService.nanoporeRegister(this.data.run_accession, payload).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (updated) => {
        this.savingSite.set(false);
        this.readiness.set(updated);

        // Refresh sites list, then auto-select new site in empty barcode rows
        this.sitesService.list().pipe(takeUntilDestroyed(this.destroyRef)).subscribe((newSites) => {
          this.sites.set(newSites);

          // Find the site that matches the site_code we just created
          const siteCode = (this.form.get('site_code')!.value ?? '').toUpperCase();
          const newSite = newSites.find((s) => s.site_code === siteCode);
          if (newSite) {
            // Auto-select in barcode rows that have no site yet
            for (const ctrl of this.barcodesArray.controls) {
              if (!ctrl.get('site_id')?.value) {
                ctrl.get('site_id')?.setValue(newSite.id);
              }
            }
            this.barcodesArray.updateValueAndValidity();
          }

          // Collapse and clear the new site form
          this.newSiteExpanded.set(false);
          this.form.patchValue({
            country: '', country_code: '', city: '', city_code: '', site: '', location: '',
            latitude: null, longitude: null,
          });
          this.form.get('country')?.clearValidators();
          this.form.get('country')?.updateValueAndValidity();

          this.notify.success('Site saved.');
        });
      },
      error: (err) => {
        this.savingSite.set(false);
        this.notify.error(err, 'Failed to save site.');
      },
    });
  }

  goBackToRegister(): void {
    const r = this.readiness();
    if (r) {
      this.form.patchValue({
        protocol_id: r.existing_protocol_id ?? null,
        sequencing_kit_id: r.existing_sequencing_kit_id ?? null,
      });
      this.buildBarcodeArray(r);
    }
    this.selectedPipelineType.set(null);
    this.step.set('register');
  }

  /** Click handler for the pipeline launch buttons in the launch step. */
  launchWithPipeline(pt: PipelineType): void {
    this.selectedPipelineType.set(pt);
    this.confirmLaunch();
  }

  /** True when the form has enough data to save. */
  canSaveOnly(): boolean {
    if (this.submitting()) return false;
    // Nothing has been touched, so there is nothing to write. The wizard opens pre-filled from
    // existing metadata, and without this the button invited a save that would change nothing.
    if (this.form.pristine) return false;
    const r = this.readiness();
    const v = this.form.value;
    // Protocol and kit must be set (either already existing on run, or chosen in form)
    const protocolOk = !!(v.protocol_id || r?.existing_protocol_id);
    const kitOk = !!(v.sequencing_kit_id || r?.existing_sequencing_kit_id);
    if (!protocolOk || !kitOk) return false;
    // Allow save if at least one barcode is fully complete, or a new site is being registered
    return this.completeBarcodeCount > 0 || !!this.form.get('country')?.value;
  }

  get completeBarcodeCount(): number {
    return this.barcodesArray.controls.filter((r) =>
      isBarcodeRowComplete((r as FormGroup).getRawValue()),
    ).length;
  }

  get saveButtonLabel(): string {
    const n = this.completeBarcodeCount;
    if (n === 0) return 'Save site & close';
    return `Save (${n} barcode${n > 1 ? 's' : ''}) & close`;
  }

  /**
   * Build the registration request from the current form.
   *
   * Written out twice before this — identically, once in `register()` and once in
   * `saveAndClose()` — so the two could drift silently: the only difference between them is what
   * they do with the response, not what they send.
   */
  private buildRegisterPayload(): NanoporeRegisterPayload {
    const v = this.form.value;
    const r = this.readiness()!;

    const barcodes: BarcodeRegisterInfo[] = (v.barcodes ?? [])
      .filter(isBarcodeRowComplete)
      .map((b: BarcodeRowValue) => ({
        barcode: b.barcode as string,
        sampling_date: b.sampling_date as string,
        sample_type: b.sample_type as string,
        site_id: b.site_id as string,
        type: b.type || undefined,
      }));

    return {
      site_id: v.site_id || undefined,
      country: v.country || undefined,
      country_code: v.country_code || undefined,
      city: v.city || undefined,
      city_code: v.city_code || undefined,
      site: v.site || undefined,
      location: v.location || undefined,
      latitude: v.latitude ?? null,
      longitude: v.longitude ?? null,
      protocol_id: v.protocol_id || r.existing_protocol_id || undefined,
      sequencing_kit_id: v.sequencing_kit_id || r.existing_sequencing_kit_id || undefined,
      barcodes,
    };
  }

  /** Rows begun but not saveable — counted into the save confirmation when they are dropped. */
  get startedButIncompleteBarcodes(): string[] {
    return startedButIncompleteBarcodes(this.form.value.barcodes ?? []);
  }

  private get startedButIncompleteCount(): number {
    return this.startedButIncompleteBarcodes.length;
  }

  /**
   * Barcodes present on disk that the database has no metadata for, straight from the server's
   * view rather than the form's. Shown on the launch step: these are the barcodes the pipeline
   * will see without a sample behind them, whether they were left incomplete just now or never
   * touched at all.
   */
  get unregisteredBarcodes(): string[] {
    const r = this.readiness();
    if (!r) return [];
    const registered = new Set(r.barcodes_in_metadata ?? []);
    return (r.barcodes_on_disk ?? []).filter((bc) => !registered.has(bc));
  }

  /** "N row(s) were not saved…", or nothing at all when none were dropped. */
  private describeDroppedRows(count: number): string {
    if (count === 0) return '';
    return (
      ` ${count} row${count > 1 ? 's' : ''} could not be saved — a barcode needs a site, ` +
      'a sample type and an 8-digit date before it can be stored.'
    );
  }

  saveAndClose(): void {
    this.submitting.set(true);
    const payload = this.buildRegisterPayload();
    const savedCount = payload.barcodes?.length ?? 0;
    const droppedCount = this.startedButIncompleteCount;

    this.discoveryService.nanoporeRegister(this.data.run_accession, payload).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (updated) => {
        this.submitting.set(false);
        const n = savedCount;
        const saved = n === 0 ? 'Site saved.' : `Saved ${n} barcode${n > 1 ? 's' : ''}.`;
        this.notify.success(saved + this.describeDroppedRows(droppedCount));
        this.ref.close({ registered: true, readiness: updated });
      },
      error: (err) => {
        this.submitting.set(false);
        this.notify.error(err, 'Save failed.');
      },
    });
  }

  register(): void {
    if (this.form.invalid) return;
    this.submitting.set(true);
    const payload = this.buildRegisterPayload();

    this.discoveryService.nanoporeRegister(this.data.run_accession, payload).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (updated) => {
        this.readiness.set(updated);
        this.autoMerge.set(updated.auto_merge ?? false);
        this.submitting.set(false);
        // No snackbar here: the launch step names the unregistered barcodes itself, and says so
        // for as long as the operator is looking at it.
        this.step.set('launch');
      },
      error: (err) => {
        this.submitting.set(false);
        this.errorMsg.set(err?.error?.detail ?? 'Registration failed.');
        this.step.set('error');
      },
    });
  }

  private buildTaxprofilerOptions() {
    const opts: { save_reads: boolean; extract_target?: string; extract_ref_accession?: string; extract_taxon_label?: string; taxon_taxid?: string; taxon_sci_name?: string } = {
      save_reads: this.saveReads(),
    };
    if (this.saveReads()) {
      const targetLabel = this.extractTargetCtrl.value;
      opts.extract_target = targetLabel;
      if (targetLabel === 'custom') {
        opts.extract_ref_accession = this.extractRefAccessionCtrl.value || undefined;
        opts.extract_taxon_label = this.extractTaxonLabelCtrl.value || undefined;
      } else {
        const target = this.extractTargets().find(t => t.label === targetLabel);
        if (target) {
          opts.extract_ref_accession = target.ref_accession || undefined;
          opts.taxon_taxid = target.taxon_taxid || undefined;
          opts.taxon_sci_name = target.taxon_sci_name || undefined;
        }
      }
    }
    return opts;
  }

  confirmLaunch(): void {
    const pt = this.selectedPipelineType();
    if (!pt) return;
    if (pt === 'mpox' && (this.mpoxCladeCtrl.invalid || this.mpoxSchemeCtrl.invalid)) {
      this.mpoxCladeCtrl.markAsTouched();
      this.mpoxSchemeCtrl.markAsTouched();
      return;
    }
    if (pt === 'squirrel' && this.mpoxCladeCtrl.invalid) {
      this.mpoxCladeCtrl.markAsTouched();
      return;
    }

    this.launching.set(true);
    this.launchError.set('');

    const doLaunch = (overwrite = false) => {
      this.pipelineService
        .launch({
          pipeline_type: pt,
          run_accessions: [this.data.run_accession],
          clade: (pt === 'mpox' || pt === 'squirrel') ? this.mpoxCladeCtrl.value : undefined,
          scheme_version: pt === 'mpox' ? this.mpoxSchemeCtrl.value : undefined,
          overwrite,
          resume: this.resumeRun(),
          pipeline_options: pt === 'taxprofiler' ? this.buildTaxprofilerOptions() : undefined,
        })
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({
          next: (pipelineRun) => {
            this.launching.set(false);
            this.ref.close({ registered: true, readiness: this.readiness()!, pipelineRun });
            this.notify.action(`Pipeline '${pipelineRun.pipeline_type}' launched (ID: ${pipelineRun.id.slice(0, 8)}).`, 'OK', 5000);
          },
          error: (err) => {
            this.launching.set(false);
            const detail = err?.error?.detail;
            if (err?.status === 409 && detail?.code === 'output_exists') {
              const path: string = detail.output_path ?? '';
              this.dialog
                .open(ConfirmDialogComponent, {
                  data: {
                    title: 'Output already exists',
                    message: `The output directory already exists${path ? ':\n' + path : ''}. Launching will overwrite existing results.`,
                    confirmLabel: 'Overwrite & launch',
                  },
                  width: '420px',
                })
                .afterClosed()
                .pipe(takeUntilDestroyed(this.destroyRef))
                .subscribe((confirmed) => {
                  if (!confirmed) return;
                  this.launching.set(true);
                  doLaunch(true);
                });
            } else {
              this.launchError.set(detail?.message ?? detail ?? 'Launch failed.');
            }
          },
        });
    };

    // Save merge decision first if there are related runs (the toggle may have been changed)
    const r = this.readiness();
    if (r && r.related_run_accessions.length > 0) {
      this.pipelineService
        .setMergeDecision(this.data.run_accession, { auto_merge: this.autoMerge() })
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({ next: () => doLaunch(), error: () => doLaunch() });
    } else {
      doLaunch();
    }
  }

  cancel(): void {
    this.ref.close();
  }

  private loadReadiness(): void {
    if (!this.data.on_disk) {
      this.step.set('launch');
      return;
    }
    this.step.set('loading');
    this.discoveryService.nanoporeReadiness(this.data.run_accession).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (r) => {
        this.readiness.set(r);
        if (r.action === 'unavailable') {
          this.errorMsg.set('This run was not found on disk. Cannot launch.');
          this.step.set('error');
        } else if (r.action === 'register_launch') {
          this.form.patchValue({
            protocol_id: r.existing_protocol_id ?? null,
            sequencing_kit_id: r.existing_sequencing_kit_id ?? null,
          });
          this.buildBarcodeArray(r);
          this.step.set('register');
        } else {
          // Ready / launch_with_warn / merge_decision_needed
          // Skip register step — user can click "Edit metadata" in the launch step if needed
          this.autoMerge.set(r.auto_merge ?? false);
          this.step.set('launch');
        }
      },
      error: (err) => {
        this.errorMsg.set(err?.error?.detail ?? 'Could not load readiness.');
        this.step.set('error');
      },
    });
  }

  private buildBarcodeArray(r: NanoporeReadiness): void {
    // Build read-count lookup from readiness barcodes
    this.barcodeReadCounts.clear();
    let noiseN = 0;
    for (const bc of r.barcodes) {
      this.barcodeReadCounts.set(bc.barcode, bc.read_count);
      if (bc.read_count > 0 && bc.read_count < NOISE_THRESHOLD) {
        noiseN++;
      }
    }
    this.noiseCount.set(noiseN);

    const arr = this.barcodesArray;
    arr.clear();
    for (const barcode of r.barcodes_on_disk) {
      const existing = r.barcodes.find((b) => b.barcode === barcode);
      arr.push(
        this.fb.group({
          barcode: [barcode],
          read_count: [this.barcodeReadCounts.get(barcode) ?? 0],
          site_id: [existing?.site_id ?? null],
          sample_type: [existing?.sample_type ?? null],
          sampling_date: [existing?.sampling_date ?? '', [Validators.pattern(YYYYMMDD)]],
          type: [existing?.type ?? null],
        }),
      );
    }
    // Validator: at least one row with site + sample_type + date
    arr.setValidators((control: AbstractControl): ValidationErrors | null => {
      const fa = control as FormArray;
      const hasComplete = fa.controls.some((g) =>
        isBarcodeRowComplete((g as FormGroup).getRawValue()),
      );
      return hasComplete ? null : { atLeastOneComplete: true };
    });
    arr.updateValueAndValidity();

    // Set protocol/kit required only if not already saved on the run
    const needsProtocol = !r.existing_protocol_id;
    const needsKit = !r.existing_sequencing_kit_id;
    const protocolCtrl = this.form.get('protocol_id')!;
    const kitCtrl = this.form.get('sequencing_kit_id')!;
    if (needsProtocol) {
      protocolCtrl.setValidators(Validators.required);
    } else {
      protocolCtrl.clearValidators();
    }
    if (needsKit) {
      kitCtrl.setValidators(Validators.required);
    } else {
      kitCtrl.clearValidators();
    }
    protocolCtrl.updateValueAndValidity();
    kitCtrl.updateValueAndValidity();

    if (!r.missing_site) {
      this.form.get('country')?.clearValidators();
    } else {
      this.form.get('country')?.setValidators(Validators.required);
    }
    this.form.get('country')?.updateValueAndValidity();
  }

}
