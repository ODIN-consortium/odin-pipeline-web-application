-- ODIN migration 003 — nanopore_runs gains replicate and replicate_type.
--
-- WHY
--   Several barcodes of one run can carry the same sampling occasion: several filters of
--   one water sample, several extracts of one material, or one extract sequenced twice.
--   The samples table keeps one row per occasion — UNIQUE (sample_code, sampling_date) —
--   so the only way to record such a group is on the barcodes themselves. Without a
--   replicate number, a group of barcodes on one sample is indistinguishable from a
--   metadata mistake, and the launch warning for it can only be dismissed, never resolved.
--
--   replicate      NULL when the barcode is its sample's only library in the run; 1, 2, 3 …
--                  when it is one of a group. Every numbered group is pooled before
--                  classification.
--   replicate_type why the group exists: 'collection' (separate filters), 'extraction'
--                  (separate extracts) or 'technical' (one extract sequenced again). The
--                  first two are real replicates worth comparing one by one; descriptive
--                  only, and NULL whenever replicate is.
--
-- WHO NEEDS THIS
--   Every database created before this migration. The application applies it itself when
--   it starts (database.apply_schema_and_migrations, position kept in PRAGMA user_version),
--   so there is nothing to run by hand, and a fresh install gets the columns from schema.sql.
--
--   Run it manually only against a database the application no longer starts against:
--
--     sqlite3 odin.db < backend/app/migrations/003_nanopore_runs_replicate.sql
--
--   The runner skips each ALTER whose column already exists. By hand, "duplicate column
--   name" means that statement is already done — carry on with the next.
--
-- RULES FOR MIGRATION FILES THE RUNNER APPLIES (003 onwards)
--   Statements only, separated by ';' — no BEGIN or COMMIT, the runner supplies the
--   transaction. ALTER TABLE … ADD COLUMN is made safe to repeat by the runner; anything
--   else must either be safe to repeat or never need to be.

ALTER TABLE nanopore_runs ADD COLUMN replicate INTEGER
    CHECK (replicate IS NULL OR replicate >= 1);

ALTER TABLE nanopore_runs ADD COLUMN replicate_type TEXT
    CHECK (replicate_type IS NULL
           OR replicate_type IN ('collection', 'extraction', 'technical'));
