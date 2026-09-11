import { ChangeDetectionStrategy, Component, OnInit, inject, viewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatTableModule } from '@angular/material/table';
import { MatSort, MatSortModule } from '@angular/material/sort';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatDialogModule } from '@angular/material/dialog';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';

import { SamplesService } from '../../core/services/samples.service';
import { crudList } from '../../core/utils/crud-list';
import { Sample } from '../../core/models/sample.model';
import { SampleFormComponent } from './sample-form/sample-form.component';
import { EmptyStateComponent } from '../../shared/components/empty-state.component';

@Component({
  selector: 'app-samples-page',
  standalone: true,
  imports: [
    CommonModule,
    MatTableModule,
    MatSortModule,
    MatButtonModule,
    MatIconModule,
    MatDialogModule,
    MatTooltipModule,
    MatProgressSpinnerModule,
    EmptyStateComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './samples-page.component.html',
})
export class SamplesPageComponent implements OnInit {
  readonly sort = viewChild(MatSort);

  readonly crud = crudList<Sample>({
    service: inject(SamplesService),
    form: SampleFormComponent,
    formWidth: '600px',
    noun: 'sample',
    describe: (sample) => sample.sample_code,
    emptyMessage: "No samples yet. Click 'Add sample' to get started.",
    sort: this.sort,
    // Compound sort: Sample code sorts by code -> date
    sortingDataAccessor: (item, property) => {
      switch (property) {
        case 'sample_code': return `${item.sample_code ?? ''}|${item.sampling_date ?? ''}`;
        default:            return (item as unknown as Record<string, unknown>)[property] as string ?? '';
      }
    },
  });

  readonly columns = [
    'sample_code',
    'sample_type',
    'sampling_date',
    'partner_sample_code',
    'depth',
    'nucleic_acid_concentration',
    'actions',
  ];

  ngOnInit() {
    this.crud.load();
  }
}
