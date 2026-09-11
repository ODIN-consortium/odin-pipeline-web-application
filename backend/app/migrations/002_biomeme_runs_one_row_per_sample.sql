-- ODIN migration 002 — biomeme_runs becomes one row per (run, sample):
--                      UNIQUE (biomeme_run_name)  ->  UNIQUE (biomeme_run_name, sample_id).
--
-- WHY
--   One Biomeme run file holds several samples, each identified within the run by its
--   well list (biomeme_sample_id, e.g. "5,6,NTC"). With biomeme_run_name UNIQUE, the Excel
--   import treated a second sample row of the same run as an *update* of the first (last
--   row won), seeding kept the first row only, and the processing script — which groups
--   wells into samples from this table — silently lost every other sample in the run.
--   Found 2026-09-17: the demo database held 35 rows for 35 runs, and the Biomeme
--   processing wrote 231 well-rows where the same 25 run files give 654.
--
-- WHO NEEDS THIS
--   Only an ODIN database created before 2026-09-17. A fresh install gets the constraint
--   straight from schema.sql and must NOT run this script. Check first:
--
--     sqlite3 odin.db "SELECT sql FROM sqlite_master WHERE name='biomeme_runs'"
--
--   If that output already contains "UNIQUE (biomeme_run_name, sample_id)", you are done.
--
-- WHAT IT CANNOT DO
--   The samples that were dropped at import are not in the database, so the migration
--   cannot bring them back. After migrating, re-import the metadata workbook
--   (Metadata -> Import, merge mode): every (run, sample) row of its biomeme sheet then
--   gets its own row. Registering samples from the Dashboard also works, one run at a time.
--
-- HOW TO RUN
--   1. Stop the application (docker compose down).
--   2. Back up the database. It lives in a Docker volume, not on the host:
--        docker run --rm -v odin-app-data:/data -v "$PWD":/backup alpine \
--          cp /data/odin.db /backup/odin.db.bak
--   3. Run the PRE-FLIGHT section below and read the output. Only a BLOCKER row stops you.
--   4. Run the MIGRATION section.
--   5. Restart, confirm the Biomeme runs page loads, then re-import the workbook.
--
--   SQLite cannot ALTER TABLE ... DROP CONSTRAINT, so the migration is the standard
--   table-rebuild: create, copy, drop, rename. There are no indexes, triggers or views on
--   biomeme_runs to recreate (verified 2026-09-17).


-- ─────────────────────────────────────────────────────────────────────────────
-- PRE-FLIGHT -- run this first and read the output. Nothing here modifies data.
-- ─────────────────────────────────────────────────────────────────────────────

-- (1) Informational: how many runs are stored. Each currently carries at most one sample;
--     the difference to the workbook's biomeme sheet is what the re-import will add.
SELECT 'INFO: biomeme runs stored (one sample each)' AS note, COUNT(*) AS runs
FROM biomeme_runs;

-- (2) Informational: runs registered without a sample link.
SELECT 'INFO: run row with no linked sample' AS note, id, biomeme_run_name
FROM biomeme_runs WHERE sample_id IS NULL;

-- (3) Rows whose sample_id points at a sample that no longer exists. FK enforcement is
--     per-connection and off by default, so these can exist in an older database. Fix or
--     delete them first; the rebuild would otherwise carry the dangling reference forward.
SELECT 'BLOCKER: biomeme run references a missing sample' AS problem,
       br.id, br.biomeme_run_name, br.sample_id
FROM biomeme_runs br LEFT JOIN samples s ON s.id = br.sample_id
WHERE br.sample_id IS NOT NULL AND s.id IS NULL;


-- ─────────────────────────────────────────────────────────────────────────────
-- MIGRATION -- only run this once the pre-flight reports no BLOCKER rows.
-- ─────────────────────────────────────────────────────────────────────────────

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

CREATE TABLE biomeme_runs_migration_002 (
    id                TEXT PRIMARY KEY,
    biomeme_run_name  TEXT NOT NULL,
    sample_id         TEXT REFERENCES samples(id),
    biomeme_sample_id TEXT,
    dilution_factor   REAL,
    comments          TEXT,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    created_by        TEXT,
    updated_by        TEXT,
    UNIQUE (biomeme_run_name, sample_id)
);

-- Columns listed explicitly rather than SELECT *: if this database predates a column, the
-- copy fails loudly here instead of shifting values into the wrong columns.
INSERT INTO biomeme_runs_migration_002 (
    id, biomeme_run_name, sample_id, biomeme_sample_id, dilution_factor, comments,
    created_at, updated_at, created_by, updated_by
)
SELECT
    id, biomeme_run_name, sample_id, biomeme_sample_id, dilution_factor, comments,
    created_at, updated_at, created_by, updated_by
FROM biomeme_runs;

DROP TABLE biomeme_runs;
ALTER TABLE biomeme_runs_migration_002 RENAME TO biomeme_runs;

COMMIT;

PRAGMA foreign_keys = ON;

-- Verify: this must return no rows.
PRAGMA foreign_key_check;

-- And confirm the new constraint is present.
SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'biomeme_runs';
