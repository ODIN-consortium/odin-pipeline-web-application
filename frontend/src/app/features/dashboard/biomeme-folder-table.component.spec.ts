import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { provideRouter } from '@angular/router';

import { BiomemeDiscoveryResult, BiomemeFolderStatus } from '../../core/models/discovery.model';
import { BiomemeFolderTableComponent } from './biomeme-folder-table.component';

/** First tests this table has had; it was previously 115 lines inside the dashboard's template. */

const FOLDER = {
  folder_path: '/data/biomeme/run-a',
  registered_count: 2,
  file_count: 3,
  status: 'partial',
  is_excluded: false,
  files: [
    { run_name: 'BM-001', sample_code: 'NOBGO01_water', sampling_date: '20240601', registered: true },
    { run_name: 'BM-002', sample_code: null, sampling_date: null, registered: false },
  ],
} as unknown as BiomemeFolderStatus;

function result(folders: BiomemeFolderStatus[]): BiomemeDiscoveryResult {
  return {
    biomeme_dir: '/data/biomeme',
    scanned_at: '2026-08-10T12:00:00.000Z',
    folders,
  } as BiomemeDiscoveryResult;
}

describe('BiomemeFolderTableComponent', () => {
  let fixture: ComponentFixture<BiomemeFolderTableComponent>;

  function setup(over: Partial<{ result: BiomemeDiscoveryResult | null; hideExcluded: boolean }> = {}) {
    TestBed.configureTestingModule({
      imports: [BiomemeFolderTableComponent, NoopAnimationsModule],
      providers: [provideRouter([])],
    });
    fixture = TestBed.createComponent(BiomemeFolderTableComponent);
    fixture.componentRef.setInput('result', 'result' in over ? over.result : result([FOLDER]));
    fixture.componentRef.setInput('hideExcluded', over.hideExcluded ?? false);
    fixture.detectChanges();
    return fixture;
  }

  const text = () => fixture.nativeElement.textContent ?? '';

  it('lists each discovered folder with its registration counts', () => {
    setup();
    expect(text()).toContain('/data/biomeme/run-a');
    expect(text()).toContain('2 / 3');
  });

  it('points at Settings when no biomeme directory is configured', () => {
    setup({ result: { biomeme_dir: null, folders: [] } as unknown as BiomemeDiscoveryResult });
    expect(text()).toContain('Biomeme directory is not configured');
  });

  it('says so when the directory holds no folders', () => {
    setup({ result: result([]) });
    expect(text()).toContain('No biomeme runs found');
  });

  it('hides excluded folders only when asked to', () => {
    const excluded = { ...FOLDER, folder_path: '/data/biomeme/old', is_excluded: true };
    setup({ result: result([FOLDER, excluded]) });
    expect(text()).toContain('/data/biomeme/old');

    fixture.componentRef.setInput('hideExcluded', true);
    fixture.detectChanges();
    expect(text()).not.toContain('/data/biomeme/old');
  });

  it('reveals the files in a folder once it is expanded, and hides them again', () => {
    setup();
    expect(text()).not.toContain('BM-001');

    fixture.componentInstance.toggle(FOLDER);
    fixture.detectChanges();
    expect(text()).toContain('BM-001');
    expect(text()).toContain('NOBGO01_water');

    fixture.componentInstance.toggle(FOLDER);
    fixture.detectChanges();
    expect(text()).not.toContain('BM-001');
  });

  it('asks its parent to open the launch wizard, and to exclude a folder', () => {
    // The two things the table cannot do itself: both are the page's business.
    setup();
    const launch = jest.fn();
    const exclude = jest.fn();
    fixture.componentInstance.launchWizardRequested.subscribe(launch);
    fixture.componentInstance.excludeToggled.subscribe(exclude);

    const buttons: HTMLButtonElement[] = Array.from(fixture.nativeElement.querySelectorAll('button'));
    buttons.find((b) => (b.textContent ?? '').includes('Register'))!.click();
    expect(launch).toHaveBeenCalledWith(null);

    buttons.find((b) => b.querySelector('mat-icon')?.textContent?.trim() === 'block')!.click();
    expect(exclude).toHaveBeenCalledWith(FOLDER);
  });
});
