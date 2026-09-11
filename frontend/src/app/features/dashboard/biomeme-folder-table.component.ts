import { ChangeDetectionStrategy, Component, input, output, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';

import { BiomemeDiscoveryResult, BiomemeFolderStatus } from '../../core/models/discovery.model';
import { RunStatusChipComponent } from '../../shared/components/run-status-chip.component';

/**
 * The Biomeme half of the dashboard: discovered folders, their registration state, and the files
 * inside each one.
 *
 * Split out of `dashboard-page`'s template, which was 535 lines. This half was worth extracting
 * because its interface is small — a result, two flags and three actions. The nanopore half was
 * deliberately left in place: it touches around thirty members of the page, so lifting it would
 * trade one long template for an equally long list of inputs and outputs.
 *
 * Which folder is expanded is owned here, since nothing outside the table has ever asked.
 */
@Component({
  selector: 'app-biomeme-folder-table',
  standalone: true,
  imports: [
    CommonModule,
    RouterModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    RunStatusChipComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './biomeme-folder-table.component.html',
  styleUrls: ['./run-tables.css'],
})
export class BiomemeFolderTableComponent {
  readonly result = input<BiomemeDiscoveryResult | null>(null);
  readonly loading = input(false);
  readonly hideExcluded = input(false);

  readonly launchWizardRequested = output<BiomemeFolderStatus | null>();
  readonly excludeToggled = output<BiomemeFolderStatus>();

  readonly expanded = signal<BiomemeFolderStatus | null>(null);

  toggle(folder: BiomemeFolderStatus): void {
    this.expanded.set(this.expanded() === folder ? null : folder);
  }
}
