# ODIN Configuration Reference

ODIN is a browser-based application for managing and launching Oxford Nanopore and Biomeme sequencing pipelines. It consists of:

- **FastAPI backend** — REST API, SQLite metadata database, pipeline execution engine.
- **Angular frontend** — dashboard, run registration, launch wizard, settings UI.
- **Nextflow pipelines** — taxprofiler (Kraken2/Bracken), wf-metagenomics (AMR, SSU), artic-mpxv-nf (mpox), squirrel (mpox phylogenetics).

The backend is typically run inside a Docker container alongside a Docker socket mount (Docker-outside-of-Docker) so that Nextflow can spawn sibling containers on the host Docker daemon.

---

## Expected directory layout

Setting `ODIN_PIPELINE_ROOT` is the only required configuration. Everything else is derived automatically if you follow this layout:

```
$ODIN_PIPELINE_ROOT/
  minknow/                         ← MinKNOW sequencing output (scanned by discovery)
  output/                          ← Nextflow pipeline results
  biomeme/                         ← Biomeme data files
  enlighten/
    data/                          ← Feather files written after post-processing
  seed/                            ← Optional startup seed files (deployment-specific)
    sites.csv                      ← Site metadata loaded on first startup
    samples.csv                    ← Sample metadata loaded on first startup
    nanopore.csv                   ← Nanopore run accessions loaded on first startup
  store/                           ← Nextflow storeDir (EPI2ME models, mpox schemes)
  databases/
    <db_name>/                     ← One subdirectory per Kraken2/Bracken database
      hash.k2d                     ← Kraken2 hash (~4–8 GB)
      opts.k2d
      taxo.k2d
      database150mers.kmer_distrib ← Bracken distribution file
  input_sheets/
    databases.csv                  ← Taxprofiler database definitions (auto-detected)
    pathogens.xlsx                 ← Pathogen annotation file (auto-detected, optional)
```

All paths under `ODIN_PIPELINE_ROOT` are used as identical host:container bind mounts so that Nextflow (running via Docker-outside-of-Docker) can resolve them against the host filesystem.

---

## Environment variables

All variables can be placed in a `.env` file (copied from `.env.example`) or passed directly to the container / process. Variables marked **[env only]** are not available in the Settings UI.

### Priority order

For settings that exist in both the Settings UI (database) and as environment variables, the resolution order is:

1. **Environment variable** (always wins)
2. **Settings UI / database** (edited via the web interface)
3. **Computed default** from `ODIN_PIPELINE_ROOT`

### Path format

All path variables accept any format — the backend normalises them:

| Format | Example |
|---|---|
| Windows | `<drive>:/ODIN` or `<drive>:\ODIN` |
| Git Bash | `/<drive>/ODIN` |
| WSL2 / Linux | `/mnt/<drive>/ODIN` |

`ODIN_PIPELINE_ROOT` is an exception: it is used **verbatim** as the Docker volume mount path. When running Docker on Windows it must be in WSL2/Linux format (`/mnt/<drive>/...`).

---

### Required

| Variable | Description |
|---|---|
| `ODIN_PIPELINE_ROOT` | Root directory for all ODIN data. All subdirectory paths (`minknow/`, `output/`, `biomeme/`, `enlighten/data/`, `databases/`, `input_sheets/`) are derived from this. Must be in WSL2/Linux format when used as a Docker volume mount on Windows. **Required — there is no default**: the backend exits at startup without it, and `docker compose up` fails because the value is used verbatim as a bind-mount path. |

---

### Application

| Variable | Default | Description |
|---|---|---|
| `ODIN_DATA_DIR` | `~/.odin-app` | Directory for the SQLite database (`odin.db`). In Docker compose this is fixed to `/app/data` (backed by the `odin-app-data` named volume). **[env only]** |
| `CORS_ORIGINS` | `http://localhost:4200, http://localhost:8080` | Comma-separated list of allowed CORS origins. **[env only]** |
| `LOG_LEVEL` | `INFO` | Python logging level for the `odin` logger (`DEBUG`, `INFO`, `WARNING`, `ERROR`). **[env only]** |
| `ODIN_SEED_DIR` | `$ODIN_PIPELINE_ROOT/seed` | Directory containing CSV seed files (`sites.csv`, `samples.csv`, `nanopore.csv`). Loaded on startup; existing rows are never overwritten. Place deployment-specific seed files under `$ODIN_PIPELINE_ROOT/seed/` — that directory is already mounted inside the container via the `ODIN_PIPELINE_ROOT` bind mount, so no separate volume is needed. **[env only]** |
| `ODIN_CSV_DELIMITER` | `;` | Column delimiter used when parsing seed CSV files. **[env only]** |
| `ODIN_CSV_DECIMAL` | `,` | Decimal separator used when parsing numeric columns in seed CSVs. **[env only]** |

---

### Settings UI (database-backed, also configurable via env vars)

These settings are stored in the `config_values` database table and edited through **Settings** in the UI. Environment variables always take precedence over database values.

| UI setting key | Env var override | Default (from `ODIN_PIPELINE_ROOT`) | Description |
|---|---|---|---|
| `minknow_dir` | `ODIN_MINKNOW_DIR` | `$ROOT/minknow` | Root directory containing MinKNOW run output. Scanned by Dashboard → Discovery, and read directly by the pipelines. A path outside `ODIN_PIPELINE_ROOT` must also be bind-mounted identically (host:container) — uncomment the matching volume line in `docker-compose.yml`; the setting alone is not enough, because the pipelines' task containers resolve the FASTQ paths on the host (DooD). |
| `output_dir` | — | `$ROOT/output` | Root directory for Nextflow pipeline outputs (results, reports, work dirs). |
| `biomeme_dir` | — | `$ROOT/biomeme` | Root directory for Biomeme input data. |
| `databases_file` | `ODIN_DATABASES_FILE` | `$ROOT/input_sheets/databases.csv` | Path to the CSV file defining Kraken2/Bracken databases for taxprofiler. Columns: `tool`, `db_name`, `db_params`, `db_path` (relative to `ODIN_DATABASE_PATH` or absolute). |
| `pathogens_file` | `ODIN_PATHOGENS_FILE` | `$ROOT/input_sheets/pathogens.xlsx` | Pathogens Excel file used to annotate Kraken2 results with priority and target group. Optional — results are shown without annotation if missing. |
| `enlighten_data_path` | — | `$ROOT/enlighten/data` | Directory where Feather files are written after post-processing. Enlighten reads from here. |
| `enlighten_url` | — | *(unset)* | URL of the Enlighten instance. Used for the "Open in Enlighten" button. |
| `nextflow_profile` | `NEXTFLOW_PROFILE` | `odin` | Nextflow `-profile` value. `odin` = 4 CPU / 16 GB; `odin_big` = 16 CPU / 31 GB. |
| `nextflow_config_file` | `NEXTFLOW_CONFIG_FILE` | *(baked into image at `/app/config/odin.config`)* | Path to the Nextflow config file (passed via `-c`). |
| `store_dir` | `ODIN_STORE_DIR` | *(unset — uses odin-store named volume in Docker)* | Nextflow `storeDir` for caching downloaded pipeline assets (EPI2ME models, mpox amplicon schemes). Use a WSL2-native Linux path for best performance. |
| `barcode_min_reads` | — | *(unset)* | Barcodes with fewer reads than this are flagged as noise. Set to 0 to disable. |
| `continuation_time_window_hours` | — | `48` | Maximum gap (hours) between two runs on the same flow cell to be considered a continuation. |
| `device_name` | — | *(hostname)* | Short name for this computer/operator. Recorded on every created/updated row; required before merging data from multiple devices. |

---

### Nextflow / pipeline execution

| Variable | Default | Description |
|---|---|---|
| `ODIN_DATABASE_PATH` | `$ODIN_PIPELINE_ROOT/databases` | Host directory that is bind-mounted into containers for Kraken2/Bracken database files. Mount uses the identical host:container path pattern. Must be on a fast filesystem (ext4) — Kraken2 memory-maps `hash.k2d`. **[env only]** |
| `ODIN_STORE_DIR` | *(Docker named volume `odin-store`)* | Nextflow `storeDir` for caching downloads (EPI2ME wf-metagenomics models, mpox amplicon schemes). Overrides the `store_dir` UI setting. Use a WSL2-native Linux path to avoid slow I/O on Windows filesystem mounts. |
| `ODIN_WORK_DIR` | `$output_dir/nanopore_processed/` | Base directory for Nextflow work directories. Each run gets its own subdirectory. Use a WSL2-native Linux path (e.g. `/home/<user>/odin_work`) to avoid `v9fs getcwd()` failures when spawned containers access a Windows filesystem mount. |
| `ODIN_TMP_DIR` | `/var/tmp` | Base directory for temporary concatenated FASTQ files created before pipeline launch. Intermediate files are deleted automatically after each run (success or failure). |
| `ODIN_LOG_DIR` | `$output_dir/pipeline_logs` | Directory for ODIN streaming logs (`.log`) and Nextflow rolling logs (`.nextflow.log`). |
| `NXF_ASSETS` | `$ODIN_PIPELINE_ROOT/nf/assets` | Nextflow pipeline asset cache (downloaded pipeline `bin/` scripts and configs). Must be mounted identically on host and container (DooD requirement). On Windows/WSL2, move to a WSL2-native path to avoid JGit/v9fs failures. **[env only]** |
| `NF_BIN` | `nextflow` (from PATH) | Full path to the Nextflow executable. Useful when Nextflow lives inside WSL or is not on PATH. **[env only]** |
| `TAXPROFILER_REVISION` | `1.2.6` | GitHub tag used when pulling nf-core/taxprofiler. Update when upgrading to a new stable release. **[env only]** |
| `TAXPROFILER_DIR` | *(unset — pulls from GitHub)* | Path to a local nf-core/taxprofiler checkout. Only needed for development/testing local pipeline changes. **[env only]** |
| `SQUIRREL_IMAGE` | `articnetworkorg/squirrel:1.3.2` | Docker image used for mpox phylogenetic post-processing. Override only if you need a specific version or a locally built image. **[env only]** |

---

### Corporate SSL inspection

Only needed when running behind a firewall that performs SSL interception (e.g. a corporate proxy with a custom CA).

| Variable | Default | Description |
|---|---|---|
| `ODIN_CA_CERT` | *(unset)* | Path to the corporate CA certificate (PEM format). When set, the cert is injected into all Nextflow-spawned containers via `docker.runOptions` in `odin.config`. The path must be accessible inside the container (i.e. under `ODIN_PIPELINE_ROOT`, which is already mounted). **[env only]** |

---

## Settings health check

`GET /api/settings/health` reports the status of all configured paths:

- **error** — a required path is missing or inaccessible.
- **warn** — an optional but recommended path is missing.
- **ok** — all expected paths exist.

The ODIN UI shows a warning banner when the health check returns errors.

---

## Pipeline types

| Pipeline | Nextflow workflow | Databases required |
|---|---|---|
| `taxprofiler` | nf-core/taxprofiler | Kraken2 + Bracken `.k2d` / `.kmer_distrib` files in `databases/` |
| `wf_metagenomics_amr` | epi2me-labs/wf-metagenomics `--amr` | CARD DB (auto-downloaded by wf-metagenomics into `store_dir`) |
| `wf_metagenomics_ssu` | epi2me-labs/wf-metagenomics SSU | Silva SSU DB (auto-downloaded into `store_dir`) |
| `mpox` | artic-network/artic-mpxv-nf + squirrel | Mpox amplicon schemes (auto-downloaded into `store_dir`) |

---

## API reference

Interactive API docs: **http://localhost:8080/docs** (Swagger UI), **http://localhost:8080/redoc**

| Resource | Endpoints |
|---|---|
| Sites | `GET/POST /api/sites`, `GET/PATCH/DELETE /api/sites/{id}` |
| Samples | `GET/POST /api/samples`, `GET/PATCH/DELETE /api/samples/{id}` |
| Nanopore run accessions | `GET/POST /api/nanopore-run-accessions`, `POST /{id}/link` |
| Nanopore runs | `GET/POST /api/nanopore-runs`, `GET/PATCH/DELETE /api/nanopore-runs/{id}` |
| Biomeme runs | `GET/POST /api/biomeme-runs`, `GET/PATCH/DELETE /api/biomeme-runs/{id}` |
| Discovery | `GET /api/discovery/scan` (MinKNOW), `GET /api/discovery/biomeme` |
| Pipelines | `GET/POST /api/pipeline/runs`, `GET /api/pipeline/runs/{id}`, `GET /api/pipeline/runs/{id}/log` (SSE) |
| Databases | `GET /api/databases` |
| Settings | `GET /api/settings`, `GET/PUT /api/settings/{key}`, `GET /api/settings/health` |
| Lookup values | `GET /api/lookup-values/{list_name}` |
| Export | `GET /api/export/excel` |
| Autocomplete | `GET /api/autocomplete/site-ids` (and other fields) |

---

## Database

SQLite at `$ODIN_DATA_DIR/odin.db` (default `~/.odin-app/odin.db`; `/app/data/odin.db` in Docker).

- All tables use UUID string primary keys.
- Deletes are soft — rows are flagged `is_deleted = 1`, never physically removed.
- WAL journal mode is enabled for concurrent read access.
- The database is **outside** the repository — `git pull` and re-running the install script never affect stored metadata.

Core tables: `sites`, `samples`, `nanopore_run_accessions`, `nanopore_runs`, `biomeme_runs`, `pipeline_runs`, `pipeline_run_accessions`, `lookup_values`, `config_values`. Discovery/exclusion tables: `nanopore_disk_cache`, `nanopore_run_exclusions`, `nanopore_barcode_exclusions`, `nanopore_merge_decisions`, `biomeme_folder_exclusions`.
