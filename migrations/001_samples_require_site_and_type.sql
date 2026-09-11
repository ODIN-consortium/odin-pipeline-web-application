-- ODIN migration 001 — samples.site_id and samples.sample_type become NOT NULL,
--                      and samples gains YYYYMMDD format checks on its two date columns.
--
-- WHY
--   sample_code is derived from site_id + sample_type and is half of the sync merge key
--   (UNIQUE (sample_code, sampling_date)). A sample whose code could not be derived
--   correctly yields a key that collides or duplicates when data from two devices is
--   merged. Enforcing the inputs in the schema is what makes the derived value meaningful,
--   and it binds every writer -- unlike an application-layer check, and unlike a FOREIGN KEY,
--   which is only enforced on connections that set PRAGMA foreign_keys = ON.
--
-- WHO NEEDS THIS
--   Only an ODIN database created before 2026-07-30. A fresh install gets the constraints
--   straight from schema.sql and must NOT run this script. Check first:
--
--     sqlite3 odin.db "SELECT sql FROM sqlite_master WHERE name='samples'"
--
--   If that output already contains "site_id ... NOT NULL", you are done.
--
-- HOW TO RUN
--   1. Stop the application (docker compose down).
--   2. Back up the database. It lives in a Docker volume, not on the host:
--        docker run --rm -v odin-app-data:/data -v "$PWD":/backup alpine \
--          cp /data/odin.db /backup/odin.db.bak
--   3. Run the PRE-FLIGHT section below and read the output. If any row is reported, STOP
--      and fix the data first -- the migration will refuse to run rather than discard it.
--   4. Run the MIGRATION section.
--   5. Restart, and confirm the app starts and the samples page loads.
--
--   SQLite cannot ALTER TABLE ... ADD CONSTRAINT, so the migration is the standard
--   table-rebuild: create, copy, drop, rename. There are no indexes, triggers or views on
--   samples to recreate (verified 2026-07-30), which keeps it to those four steps.


-- ─────────────────────────────────────────────────────────────────────────────
-- PRE-FLIGHT -- run this first and read the output. Nothing here modifies data.
-- ─────────────────────────────────────────────────────────────────────────────

-- (1) Samples with no site. Each needs a site assigned, or deleting, before migrating.
SELECT 'BLOCKER: sample with no site_id' AS problem, id, sample_code, sampling_date
FROM samples WHERE site_id IS NULL;

-- (2) Samples with no sample_type. Same choice: fill it in, or delete the row.
SELECT 'BLOCKER: sample with no sample_type' AS problem, id, sample_code, sampling_date
FROM samples WHERE sample_type IS NULL OR TRIM(sample_type) = '';

-- (3) Samples whose site_id points at a site that no longer exists. FK enforcement is
--     per-connection and off by default, so these can exist in an older database.
SELECT 'BLOCKER: sample references a missing site' AS problem, s.id, s.sample_code, s.site_id
FROM samples s LEFT JOIN sites si ON si.id = s.site_id
WHERE s.site_id IS NOT NULL AND si.id IS NULL;

-- (4) Dates that are not YYYYMMDD. These would fail the new CHECK constraints.
SELECT 'BLOCKER: sampling_date is not YYYYMMDD' AS problem, id, sample_code, sampling_date
FROM samples
WHERE sampling_date NOT GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]';

SELECT 'BLOCKER: date_extraction is not YYYYMMDD' AS problem, id, sample_code, date_extraction
FROM samples
WHERE date_extraction IS NOT NULL
  AND TRIM(date_extraction) <> ''
  AND date_extraction NOT GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]';

-- (5) Informational, not a blocker: sample_type values that are not in lookup_values. The
--     schema does not constrain this (lookup_values is keyed by (list, code), so it would
--     need a composite FK or a trigger); it is validated in the API and, since 2026-07-30,
--     skipped rather than inserted by the seed and import paths. Worth reviewing anyway.
SELECT 'REVIEW: sample_type not in lookup_values' AS problem, s.id, s.sample_code, s.sample_type
FROM samples s
WHERE s.sample_type IS NOT NULL
  AND s.sample_type NOT IN (SELECT code FROM lookup_values WHERE list = 'sample_type');


-- ─────────────────────────────────────────────────────────────────────────────
-- MIGRATION -- only run this once the pre-flight reports no BLOCKER rows.
--
-- Normalising empty strings to NULL first would defeat the point, so it is not done: an
-- empty sample_type is a blocker to be resolved deliberately, not silently rewritten.
-- ─────────────────────────────────────────────────────────────────────────────

PRAGMA foreign_keys = OFF;   -- so dropping samples does not trip nanopore/biomeme FKs

BEGIN TRANSACTION;

CREATE TABLE samples_migration_001 (
    id                       TEXT PRIMARY KEY,
    sample_code              TEXT NOT NULL,
    site_id                  TEXT NOT NULL REFERENCES sites(id),
    sample_type              TEXT NOT NULL,
    depth                    TEXT,
    elevation                TEXT,
    sampling_date            TEXT NOT NULL
        CHECK (sampling_date GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'),
    comments_sampling        TEXT,
    partner_sample_code      TEXT,
    date_extraction          TEXT
        CHECK (date_extraction IS NULL
               OR date_extraction GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'),
    nucleic_acid_concentration TEXT,
    extract_volume           TEXT,
    comments_extraction      TEXT,
    elution_volume           TEXT,
    comments                 TEXT,
    created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    created_by               TEXT,
    updated_by               TEXT,
    UNIQUE (sample_code, sampling_date)
);

-- Columns listed explicitly rather than SELECT *: if this database predates a column, the
-- copy fails loudly here instead of shifting values into the wrong columns.
INSERT INTO samples_migration_001 (
    id, sample_code, site_id, sample_type, depth, elevation, sampling_date,
    comments_sampling, partner_sample_code, date_extraction, nucleic_acid_concentration,
    extract_volume, comments_extraction, elution_volume, comments,
    created_at, updated_at, created_by, updated_by
)
SELECT
    id, sample_code, site_id, sample_type, depth, elevation, sampling_date,
    comments_sampling, partner_sample_code, date_extraction, nucleic_acid_concentration,
    extract_volume, comments_extraction, elution_volume, comments,
    created_at, updated_at, created_by, updated_by
FROM samples;

DROP TABLE samples;
ALTER TABLE samples_migration_001 RENAME TO samples;

COMMIT;

PRAGMA foreign_keys = ON;

-- Verify: this must return no rows. It checks every FK in the database, so it also proves
-- nanopore_runs.sample_id and biomeme_runs.sample_id were not orphaned by the rebuild.
PRAGMA foreign_key_check;

-- And confirm the constraints are actually present now.
SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'samples';
