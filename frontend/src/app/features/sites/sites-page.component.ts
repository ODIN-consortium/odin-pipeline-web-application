import { ChangeDetectionStrategy, Component, OnInit, inject, viewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatTableModule } from '@angular/material/table';
import { MatSort, MatSortModule } from '@angular/material/sort';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatDialogModule } from '@angular/material/dialog';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';

import { SitesService } from '../../core/services/sites.service';
import { crudList } from '../../core/utils/crud-list';
import { Site } from '../../core/models/site.model';
import { SiteFormComponent } from './site-form/site-form.component';
import { EmptyStateComponent } from '../../shared/components/empty-state.component';

@Component({
  selector: 'app-sites-page',
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
  templateUrl: './sites-page.component.html',
})
export class SitesPageComponent implements OnInit {
  readonly sort = viewChild(MatSort);

  readonly crud = crudList<Site>({
    service: inject(SitesService),
    form: SiteFormComponent,
    formWidth: '560px',
    noun: 'site',
    describe: (site) => site.site_code,
    emptyMessage: "No sites yet. Click 'Add site' to get started.",
    sort: this.sort,
    // Compound sort: Country sorts by country -> city -> location; City sorts by city -> location
    sortingDataAccessor: (item, property) => {
      switch (property) {
        case 'country': return `${item.country ?? ''}|${item.city ?? ''}|${item.location ?? ''}`;
        case 'city':    return `${item.city ?? ''}|${item.location ?? ''}`;
        default:        return (item as unknown as Record<string, unknown>)[property] as string ?? '';
      }
    },
  });

  readonly columns = [
    'country',
    'country_code',
    'city',
    'city_code',
    'location',
    'site',
    'site_code',
    'actions',
  ];

  ngOnInit() {
    this.crud.load();
  }
}
