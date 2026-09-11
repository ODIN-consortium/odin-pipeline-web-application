import { Injectable, signal } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class WarningDismissalService {
  private readonly storageKey = 'odin.dismissedWarnings.v1';
  private readonly dismissed = signal<Set<string>>(this.loadFromStorage());

  buildRunWarningKey(runAccession: string, warning: string): string {
    return `${runAccession}::${warning}`;
  }

  isDismissed(key: string): boolean {
    return this.dismissed().has(key);
  }

  dismiss(key: string): void {
    this.update((next) => next.add(key));
  }

  dismissMany(keys: string[]): void {
    this.update((next) => {
      for (const key of keys) {
        next.add(key);
      }
    });
  }

  undismissMany(keys: string[]): void {
    this.update((next) => {
      for (const key of keys) {
        next.delete(key);
      }
    });
  }

  getDismissedKeysForRun(runAccession: string): string[] {
    const prefix = `${runAccession}::`;
    return Array.from(this.dismissed()).filter((key) => key.startsWith(prefix));
  }

  countDismissedForRun(runAccession: string): number {
    return this.getDismissedKeysForRun(runAccession).length;
  }

  undismissRunWarnings(runAccession: string): void {
    this.undismissMany(this.getDismissedKeysForRun(runAccession));
  }

  private update(mutator: (next: Set<string>) => void): void {
    const next = new Set(this.dismissed());
    mutator(next);
    this.dismissed.set(next);
    this.persistToStorage(next);
  }

  private loadFromStorage(): Set<string> {
    if (typeof localStorage === 'undefined') {
      return new Set<string>();
    }
    try {
      const raw = localStorage.getItem(this.storageKey);
      if (!raw) {
        return new Set<string>();
      }
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        return new Set<string>();
      }
      return new Set(parsed.filter((entry): entry is string => typeof entry === 'string'));
    } catch {
      return new Set<string>();
    }
  }

  private persistToStorage(values: Set<string>): void {
    if (typeof localStorage === 'undefined') {
      return;
    }
    try {
      localStorage.setItem(this.storageKey, JSON.stringify(Array.from(values)));
    } catch {
      // Ignore localStorage failures (private mode, quota, etc.).
    }
  }
}
