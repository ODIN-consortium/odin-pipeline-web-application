import { Component, DestroyRef, OnInit, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink, RouterOutlet } from '@angular/router';
import { NavComponent } from './shared/components/nav/nav.component';
import { SettingsService } from './core/services/settings.service';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, NavComponent, MatIconModule, MatButtonModule],
  template: `
    <app-nav></app-nav>

    @if (issues().length > 0 && !dismissed()) {
      <div class="settings-warning-banner">
        <mat-icon class="banner-icon">warning</mat-icon>
        <div class="banner-body">
          <strong>Configuration issues detected:</strong>
          <ul>
            @for (issue of issues(); track issue.key) {
              <li>
                <strong>{{ issue.label }}</strong>
                @if (issue.key === '_kraken2_database_volume' && issue.issue === 'missing') {
                  — <code>ODIN_DATABASE_PATH</code> not set in .env. Set it to the host directory containing your Kraken2 database.
                } @else if (issue.key === '_kraken2_database_volume' && issue.issue === 'not_found') {
                  — {{ issue.value }}
                } @else if (issue.issue === 'missing') {
                  — not configured.
                } @else if (issue.issue === 'not_found') {
                  — path not found: <code>{{ issue.value }}</code>
                } @else if (issue.issue === 'warning') {
                  — not configured (optional, results will lack annotation).
                } @else {
                  — could not create directory: <code>{{ issue.value }}</code>
                }
              </li>
            }
          </ul>
          <a routerLink="/settings">Go to Settings</a> to fix these before running pipelines.
        </div>
        <button mat-icon-button class="banner-dismiss" (click)="dismissed.set(true)" aria-label="Dismiss">
          <mat-icon>close</mat-icon>
        </button>
      </div>
    }

    <router-outlet></router-outlet>
  `,
  styles: [`
    .settings-warning-banner {
      display: flex;
      align-items: flex-start;
      gap: 12px;
      background: #fff3cd;
      border-bottom: 2px solid #ffc107;
      padding: 12px 20px;
      font-size: 14px;
    }
    .banner-icon {
      color: #e65100;
      margin-top: 2px;
      flex-shrink: 0;
    }
    .banner-body {
      flex: 1;
    }
    .banner-body ul {
      margin: 4px 0 6px 16px;
      padding: 0;
    }
    .banner-body a {
      color: #1565c0;
      font-weight: 500;
    }
    .banner-dismiss {
      flex-shrink: 0;
      margin-top: -4px;
    }
  `],
})
export class AppComponent implements OnInit {
  private readonly settingsService = inject(SettingsService);
  private readonly destroyRef = inject(DestroyRef);

  // Live view of the shared health state — updated by every refreshHealth()
  // call (settings saves, imports), not just the fetch at app start.
  readonly issues = this.settingsService.healthIssues;
  readonly dismissed = signal(false);

  ngOnInit(): void {
    this.settingsService
      .refreshHealth()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        error: () => {
          /* silently ignore — backend may not be ready */
        },
      });
  }
}
