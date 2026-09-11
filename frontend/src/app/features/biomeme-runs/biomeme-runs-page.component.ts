import { ChangeDetectionStrategy, Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatTableModule } from '@angular/material/table';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatDialogModule } from '@angular/material/dialog';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';

import { BiomemeRunsService } from '../../core/services/biomeme-runs.service';
import { crudList } from '../../core/utils/crud-list';
import { BiomemeRun } from '../../core/models/biomeme-run.model';
import { BiomemeRunFormComponent } from './biomeme-run-form/biomeme-run-form.component';

@Component({
  selector: 'app-biomeme-runs-page',
  standalone: true,
  imports: [
    CommonModule,
    MatTableModule,
    MatButtonModule,
    MatIconModule,
    MatDialogModule,
    MatTooltipModule,
    MatProgressSpinnerModule,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './biomeme-runs-page.component.html',
})
export class BiomemeRunsPageComponent implements OnInit {
  /** No sort query: this table is not sortable, and the rows keep the order the server sent. */
  readonly crud = crudList<BiomemeRun>({
    service: inject(BiomemeRunsService),
    form: BiomemeRunFormComponent,
    formWidth: '520px',
    noun: 'run',
    describe: (run) => run.biomeme_run_name,
    emptyMessage: 'No Biomeme runs yet. Use the Dashboard to register files from disk.',
    plural: 'Biomeme runs',
  });

  readonly columns = [
    'biomeme_run_name',
    'sample_code',
    'sampling_date',
    'biomeme_sample_id',
    'dilution_factor',
    'actions',
  ];

  ngOnInit() {
    this.crud.load();
  }
}
