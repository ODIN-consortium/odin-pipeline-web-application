import { Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: '',
    redirectTo: 'dashboard',
    pathMatch: 'full',
  },
  {
    path: 'sites',
    loadComponent: () =>
      import('./features/sites/sites-page.component').then((m) => m.SitesPageComponent),
  },
  {
    path: 'samples',
    loadComponent: () =>
      import('./features/samples/samples-page.component').then((m) => m.SamplesPageComponent),
  },
  {
    path: 'runs',
    loadComponent: () =>
      import('./features/runs/runs-page.component').then((m) => m.RunsPageComponent),
  },
  {
    path: 'biomeme-runs',
    loadComponent: () =>
      import('./features/biomeme-runs/biomeme-runs-page.component').then(
        (m) => m.BiomemeRunsPageComponent,
      ),
  },
  {
    path: 'settings',
    loadComponent: () =>
      import('./features/settings/settings.component').then((m) => m.SettingsComponent),
  },
  {
    path: 'lookup-values',
    loadComponent: () =>
      import('./features/lookup-values/lookup-values-page.component').then((m) => m.LookupValuesPageComponent),
  },
  {
    path: 'dashboard',
    loadComponent: () =>
      import('./features/dashboard/dashboard-page.component').then((m) => m.DashboardPageComponent),
  },
  {
    path: 'databases',
    loadComponent: () =>
      import('./features/databases/databases-page.component').then((m) => m.DatabasesPageComponent),
  },
  {
    path: 'pipeline-runs',
    loadComponent: () =>
      import('./features/pipeline-runs/pipeline-runs-page.component').then(
        (m) => m.PipelineRunsPageComponent,
      ),
  },
  {
    path: 'sync',
    loadComponent: () =>
      import('./features/sync/sync-page.component').then((m) => m.SyncPageComponent),
  },
  { path: '**', redirectTo: 'dashboard' },
];
