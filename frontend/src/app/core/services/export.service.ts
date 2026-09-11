import { Injectable, inject } from '@angular/core';
import { ImportProblem } from '../utils/import-problems';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

/** Per-sheet outcome. `unchanged` distinguishes "matched but identical" from "rewritten". */
export interface ExcelSheetSummary {
  created: number;
  updated: number;
  unchanged: number;
  deleted: number;
  skipped: number;
}

/** One field an import would change on a matched row. */
export interface ImportChangedField {
  field: string;
  old: unknown;
  new: unknown;
}

/** One matched row an import would change, with the exact fields. */
export interface ImportChange {
  sheet: string;
  row: number | null;
  fields: ImportChangedField[];
}

export interface ExcelImportResult {
  detail: string;
  mode: string;
  /** True when this was a preview: the import ran and was rolled back, nothing was written. */
  dry_run?: boolean;
  summary?: Record<string, ExcelSheetSummary>;
  problems?: ImportProblem[];
  problem_count?: number;
  problems_truncated?: number;
  changes?: ImportChange[];
  change_count?: number;
  changes_truncated?: number;
}

@Injectable({ providedIn: 'root' })
export class ExportService {
  private readonly http = inject(HttpClient);

  exportExcel(): void {
    window.open('/api/export/excel', '_blank');
  }

  /**
   * Report what a workbook would do, writing nothing.
   *
   * The backend runs the real import and rolls it back, so this cannot disagree with what
   * {@link importExcel} then does.
   */
  previewExcelImport(file: File, mode: string): Observable<ExcelImportResult> {
    return this.http.post<ExcelImportResult>(
      `/api/export/excel/import?mode=${mode}&dry_run=true`,
      this.body(file),
    );
  }

  /**
   * Apply a workbook.
   *
   * `importValidRows` corresponds to the operator having seen the preview and chosen to go
   * ahead: without it the API refuses any upload containing an unimportable row.
   */
  importExcel(file: File, mode: string, importValidRows = false): Observable<ExcelImportResult> {
    return this.http.post<ExcelImportResult>(
      `/api/export/excel/import?mode=${mode}&import_valid_rows=${importValidRows}`,
      this.body(file),
    );
  }

  private body(file: File): FormData {
    const formData = new FormData();
    formData.append('file', file);
    return formData;
  }
}
