/** One unimportable row, as reported by `POST /api/export/excel/import`. */
export interface ImportProblem {
  sheet: string;
  row: number | null;
  reason: string;
}

/** The structured body the import endpoint returns with 422. */
export interface ImportProblemDetail {
  message: string;
  problem_count: number;
  problems: ImportProblem[];
  problems_truncated?: number;
}

/** How many individual rows to name before summarising the rest. */
const MAX_LISTED = 3;

/**
 * Turn an import failure into something a person can read.
 *
 * The endpoint answers a rejected upload with a structured body — a summary line plus a row
 * of `{sheet, row, reason}` for every problem — because a bare count cannot tell the operator
 * what to fix. Passing that object through `String()` produced "[object Object]", which is
 * how this function came to exist.
 *
 * Other failures from the same endpoint (a 400 for a non-xlsx file, a 409 for a constraint
 * conflict) carry a plain string, so that case passes straight through.
 *
 * This is a stopgap: the full list belongs in the preview dialog planned on top of C2/C3/C4,
 * where the operator can read every row and decide whether to import the rest. Until then it
 * names the first few and says how many more there are, rather than hiding the difference
 * between three bad rows and three hundred.
 */
export function formatImportFailure(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail.trim() !== '') {
    return detail;
  }
  if (!isProblemDetail(detail)) {
    return fallback;
  }

  const listed = detail.problems
    .slice(0, MAX_LISTED)
    .map((p) => `${p.sheet} row ${p.row ?? '?'}: ${p.reason}`);
  const remaining = detail.problem_count - listed.length;
  if (remaining > 0) {
    listed.push(`…and ${remaining} more`);
  }
  return listed.length ? `${detail.message} — ${listed.join('; ')}` : detail.message;
}

function isProblemDetail(value: unknown): value is ImportProblemDetail {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const candidate = value as Partial<ImportProblemDetail>;
  return (
    typeof candidate.message === 'string' &&
    typeof candidate.problem_count === 'number' &&
    Array.isArray(candidate.problems)
  );
}
