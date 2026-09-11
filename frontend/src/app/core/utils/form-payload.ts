/**
 * Helpers for turning a reactive-form value into an API payload.
 *
 * Angular form controls yield `''` for an empty text input, which is not what the
 * API wants in either direction — and the difference between create and update is
 * easy to get wrong:
 *
 * - **Create (POST)**: omit empty fields entirely so the backend applies its own
 *   defaults and stores NULL rather than an empty string.
 * - **Update (PATCH/PUT)**: send `null` for empty fields. The backend uses
 *   `model_dump(exclude_unset=True)`, so an *omitted* key means "leave this alone"
 *   while an explicit `null` means "clear it". Filtering empty values out of an
 *   update payload therefore makes it impossible to clear an optional field —
 *   which is exactly the bug these helpers exist to prevent.
 */

/** Values a form control can hold before it becomes an API payload. */
type FormValues = Record<string, unknown>;

/**
 * Build a create payload: drop empty fields so backend defaults apply.
 *
 * `undefined` is dropped too, since `JSON.stringify` would remove it anyway.
 */
export function toCreatePayload<T>(raw: FormValues): T {
  return Object.fromEntries(
    Object.entries(raw).filter(([, v]) => v !== '' && v !== null && v !== undefined),
  ) as T;
}

/** Options for {@link toUpdatePayload}. */
export interface UpdatePayloadOptions {
  /** Fields the endpoint must never receive (derived or immutable, e.g. `label`). */
  omit?: readonly string[];
  /**
   * Fields where an empty control means "leave unchanged" rather than "clear".
   *
   * For a field the record cannot do without — one that other columns are derived
   * from, such as `site_id` / `sample_type` behind `sample_code` — clearing is not
   * a supported edit, and sending null would blank the column while leaving the
   * derived value stale. Dropping the key keeps the stored value instead.
   */
  keepIfEmpty?: readonly string[];
}

/**
 * Build an update payload: empty fields become `null` so the backend clears them.
 */
export function toUpdatePayload<T>(raw: FormValues, options: UpdatePayloadOptions = {}): T {
  const omit = new Set(options.omit ?? []);
  const keepIfEmpty = new Set(options.keepIfEmpty ?? []);
  const isEmpty = (v: unknown): boolean => v === '' || v === undefined || v === null;
  return Object.fromEntries(
    Object.entries(raw)
      .filter(([k, v]) => !omit.has(k) && !(keepIfEmpty.has(k) && isEmpty(v)))
      .map(([k, v]) => [k, v === '' || v === undefined ? null : v]),
  ) as T;
}
