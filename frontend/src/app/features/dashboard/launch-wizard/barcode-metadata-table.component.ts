import { ChangeDetectionStrategy, Component, input, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { AbstractControl, FormArray, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatTooltipModule } from '@angular/material/tooltip';

import { Site } from '../../../core/models/site.model';
import { LookupValue } from '../../../core/models/lookup-value.model';
import { isBarcodeRowComplete, startedButIncompleteBarcodes } from './barcode-row';

/** Noise threshold: barcodes with fewer reads are hidden by default. */
export const NOISE_THRESHOLD = 1000;

/**
 * The launch wizard's per-barcode metadata table: one row per barcode found on disk, with site,
 * sample type, sampling date and mpox type, copy-from-above buttons, and the noise toggle that
 * hides barcodes whose read count says they are probably not real samples.
 *
 * The wizard's FormGroup passes in whole and this component edits its `barcodes` FormArray in
 * place; nothing is emitted because the wizard reads the same form when it saves.
 */
@Component({
  selector: 'app-barcode-metadata-table',
  standalone: true,
  imports: [
    CommonModule,
    ReactiveFormsModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatButtonModule,
    MatIconModule,
    MatSlideToggleModule,
    MatTooltipModule,
  ],
  templateUrl: './barcode-metadata-table.component.html',
  styleUrls: ['./barcode-metadata-table.component.css', './wizard-shared.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class BarcodeMetadataTableComponent {
  /** The wizard's form; the `barcodes` FormArray lives on it. */
  readonly form = input.required<FormGroup>();
  readonly sites = input<Site[]>([]);
  readonly sampleTypes = input<LookupValue[]>([]);
  readonly mpoxTypes = input<LookupValue[]>([]);
  /** How many barcodes the readiness scan flagged as likely noise; 0 hides the toggle. */
  readonly noiseCount = input(0);

  readonly NOISE_THRESHOLD = NOISE_THRESHOLD;
  readonly noiseBarcodesHidden = signal(true);

  get barcodesArray(): FormArray {
    return this.form().get('barcodes') as FormArray;
  }

  /** Rows begun but not saveable — named in the hint before Save is ever pressed. */
  get startedButIncompleteBarcodes(): string[] {
    return startedButIncompleteBarcodes(this.form().value.barcodes ?? []);
  }

  /** True when the barcode has site, sample type, and a valid sampling date. */
  rowIsComplete(row: AbstractControl): boolean {
    return isBarcodeRowComplete((row as FormGroup).getRawValue());
  }

  /** Copy the value of a single cell from the row above. */
  copyFromAbove(rowIndex: number, field: string): void {
    if (rowIndex <= 0) return;
    const aboveVal = (this.barcodesArray.at(rowIndex - 1) as FormGroup).get(field)?.value;
    if (aboveVal !== null && aboveVal !== undefined && aboveVal !== '') {
      (this.barcodesArray.at(rowIndex) as FormGroup).get(field)?.setValue(aboveVal);
      this.barcodesArray.updateValueAndValidity();
    }
  }
}
