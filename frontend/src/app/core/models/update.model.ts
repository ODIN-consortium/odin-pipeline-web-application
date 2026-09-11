/**
 * Shape of an update (PATCH/PUT) payload.
 *
 * Every field is optional — an omitted key means "leave this value alone" — and
 * every field is nullable, because an explicit `null` is how the API is told to
 * *clear* an optional field. The backend models these as `Optional[...]` and read
 * the payload with `model_dump(exclude_unset=True)`, so omitted and null are
 * genuinely different requests; `Partial<T>` alone cannot express the second.
 */
export type Clearable<T> = { [K in keyof T]?: T[K] | null };
