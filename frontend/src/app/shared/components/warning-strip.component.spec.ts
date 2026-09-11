import { Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { WarningStripComponent } from './warning-strip.component';

/**
 * The strip has two tones. A warning is something to act on, so each line can be
 * dismissed and dismissed lines can be brought back. A note (the replicate groups a
 * launch will pool) is only information: nothing to dismiss, nothing to restore.
 */
@Component({
  standalone: true,
  imports: [WarningStripComponent],
  template: `
    <app-warning-strip
      [tone]="tone()"
      [dismissible]="dismissible()"
      [warnings]="lines"
      [hiddenCount]="hidden()"
      (dismiss)="dismissed.push($event)"
    />
  `,
})
class HostComponent {
  readonly tone = signal<'warn' | 'info'>('warn');
  readonly hidden = signal(0);
  readonly dismissible = signal(true);
  lines = ['first line', 'second line'];
  dismissed: string[] = [];
}

describe('WarningStripComponent', () => {
  let fixture: ComponentFixture<HostComponent>;
  let host: HostComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [HostComponent] }).compileComponents();
    fixture = TestBed.createComponent(HostComponent);
    host = fixture.componentInstance;
    fixture.detectChanges();
  });

  const el = (): HTMLElement => fixture.nativeElement;

  it('renders every line', () => {
    const text = el().textContent ?? '';
    expect(text).toContain('first line');
    expect(text).toContain('second line');
  });

  it('a warning has a dismiss button per line, which emits the line', () => {
    const buttons = el().querySelectorAll<HTMLButtonElement>('.warning-dismiss-btn');
    expect(buttons.length).toBe(2);

    buttons[1].click();

    expect(host.dismissed).toEqual(['second line']);
  });

  it('a warning offers to show dismissed lines when there are any', () => {
    host.hidden.set(3);
    fixture.detectChanges();

    expect(el().querySelector('.warning-restore-chip')?.textContent).toContain('(3)');
  });

  it('a note has no dismiss button and no restore chip, whatever the hidden count', () => {
    host.tone.set('info');
    host.hidden.set(3);
    fixture.detectChanges();

    expect(el().querySelectorAll('.warning-dismiss-btn').length).toBe(0);
    expect(el().querySelector('.warning-restore-chip')).toBeNull();
    expect(el().querySelector('.info-tone')).not.toBeNull();
    expect(el().querySelector('mat-icon')?.textContent?.trim()).toBe('info');
  });

  it('a warning a page cannot dismiss shows no cross and no restore chip', () => {
    host.dismissible.set(false);
    host.hidden.set(2);
    fixture.detectChanges();

    expect(el().querySelectorAll('.warning-dismiss-btn').length).toBe(0);
    expect(el().querySelector('.warning-restore-chip')).toBeNull();
    expect(el().querySelector('.warning-strip')).not.toBeNull();
  });

  it('renders nothing for an empty list', () => {
    const empty = TestBed.createComponent(HostComponent);
    empty.componentInstance.lines = [];
    empty.detectChanges();

    expect(empty.nativeElement.querySelector('.warning-strip')).toBeNull();
  });
});
