/**
 * Save a blob to the user's downloads with a given filename.
 *
 * Used instead of pointing an anchor at the API URL when the caller needs to know the data
 * actually arrived — a pre-import backup has to be a precondition, not a hopeful side effect.
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  // Revoking immediately can cancel the download in some browsers; a task later is enough.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** `20260731T110705` — sortable, filename-safe, no separators to quote. */
export function fileTimestamp(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}` +
    `T${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
  );
}
