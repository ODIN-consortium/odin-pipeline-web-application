import { DestroyRef, Component, OnInit, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink, RouterLinkActive } from '@angular/router';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatBadgeModule } from '@angular/material/badge';
import { MatTooltipModule } from '@angular/material/tooltip';
import { SettingsService } from '../../../core/services/settings.service';

@Component({
  selector: 'app-nav',
  standalone: true,
  imports: [RouterLink, RouterLinkActive, MatToolbarModule, MatButtonModule, MatIconModule, MatBadgeModule, MatTooltipModule],
  template: `
    <mat-toolbar color="primary">
      <mat-icon>biotech</mat-icon>&nbsp;
      <span>ODIN Pipeline</span>
      <span class="spacer"></span>
      <a mat-button routerLink="/dashboard" routerLinkActive="active-link">
        <mat-icon>dashboard</mat-icon> Dashboard
      </a>
      <a mat-button routerLink="/sites" routerLinkActive="active-link">
        <mat-icon>place</mat-icon> Sites
      </a>
      <a mat-button routerLink="/samples" routerLinkActive="active-link">
        <mat-icon>water_drop</mat-icon> Samples
      </a>
      <a mat-button routerLink="/runs" routerLinkActive="active-link">
        <mat-icon>science</mat-icon> Nanopore
      </a>
      <a mat-button routerLink="/biomeme-runs" routerLinkActive="active-link">
        <mat-icon>device_hub</mat-icon> Biomeme
      </a>
      <a mat-button routerLink="/databases" routerLinkActive="active-link">
        <mat-icon>storage</mat-icon> Databases
      </a>
      <a mat-button routerLink="/pipeline-runs" routerLinkActive="active-link">
        <mat-icon>play_circle</mat-icon> Pipeline runs
      </a>
      <a mat-button routerLink="/lookup-values" routerLinkActive="active-link">
        <mat-icon>list</mat-icon> Lookup lists
      </a>
      <a mat-button routerLink="/sync" routerLinkActive="active-link"
         [matTooltip]="deviceNameMissing() ? 'Device name not set — set it in Settings before syncing' : ''">
        <mat-icon
          [matBadge]="deviceNameMissing() ? '!' : ''"
          matBadgeColor="warn"
          matBadgeSize="small"
          [matBadgeHidden]="!deviceNameMissing()"
          aria-hidden="false"
          matBadgeDescription="Device name not set"
        >sync</mat-icon> Sync
      </a>
      <a mat-button routerLink="/settings" routerLinkActive="active-link">
        <mat-icon>settings</mat-icon> Settings
      </a>
    </mat-toolbar>
  `,
  styles: [
    `
      .spacer {
        flex: 1 1 auto;
      }
      .active-link {
        background: rgba(255, 255, 255, 0.15);
        border-radius: 4px;
      }
      a {
        color: white;
        text-decoration: none;
      }
    `,
  ],
})
export class NavComponent implements OnInit {
  private readonly settingsService = inject(SettingsService);
  private readonly destroyRef = inject(DestroyRef);
  deviceNameMissing = signal(false);

  ngOnInit(): void {
    this.settingsService
      .get('device_name')
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (s) => this.deviceNameMissing.set(!s.value?.trim()),
        error: () => this.deviceNameMissing.set(false),
      });
  }
}
