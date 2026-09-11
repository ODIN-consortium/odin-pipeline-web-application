# ODIN Pipeline Web Application

Browser-based interface for running ODIN pipelines. Replaces the interactive bash scripts with a form-driven UI that discovers data, manages metadata, and guides users through pipeline execution.

> ⚠️ **Security:** ODIN has **no login by default** and mounts the host Docker socket, so network access to the app is equivalent to root on the host. You can require a login (HTTP Basic Auth) by setting `ODIN_AUTH_USER`/`ODIN_AUTH_PASSWORD` — do that whenever the machine is reachable by anyone else, and never expose it to an untrusted network. ODIN is provided as-is, without warranty or guaranteed support, so review **[SECURITY.md](SECURITY.md)** and assess it for your own environment before deploying.

## Quick Start

Clone the repository:

```bash
git clone https://github.com/ODIN-consortium/odin-pipeline-web-application.git
cd odin-pipeline-web-application
```

To run from source without Docker, see the [Development](#development) section below.

### Docker Compose (Docker Engine)

**Prerequisites:** create your `.env` file (the images build locally from the included Dockerfiles — no registry login needed):

```bash
cp .env.example .env
# Edit .env — set ODIN_PIPELINE_ROOT
```

**Corporate network (SSL inspection proxy):** Place your corporate CA certificate in the `ca/` directory as a `.crt` or `.cer` file before building:

```bash
cp /path/to/corp_ca.cer ca/
```

```bash
docker compose up -d
```

Open **http://localhost** (frontend) — API at **http://localhost:8080**.

### Authentication (optional)

ODIN has no login by default. Set both `ODIN_AUTH_USER` and `ODIN_AUTH_PASSWORD` in `.env` before `docker compose up` to require HTTP Basic Auth across the whole app — both the frontend (UI + the `/api/` proxy) and the backend API enforce the same credentials, so it applies even to requests that reach the API directly (e.g. another container on the Compose network). Leaving them unset runs without a login. This is strongly recommended whenever the machine is reachable by anyone else — see [SECURITY.md](SECURITY.md). Basic Auth transmits the credential in effectively cleartext, so also terminate TLS in front if you expose ODIN beyond loopback or a trusted LAN.

---

## Volume Mounts

`docker-compose.yml` mounts several paths into the API container:

| Mount | Purpose |
|---|---|
| `/var/run/docker.sock:/var/run/docker.sock` | Docker socket — lets Nextflow spawn pipeline tool containers as siblings on the host (Docker-outside-of-Docker) |
| `${ODIN_PIPELINE_ROOT}:${ODIN_PIPELINE_ROOT}` | All ODIN data (MinKNOW output, results, databases, input sheets) — must be **identical** on both sides |
| `${ODIN_TMP_DIR}:${ODIN_TMP_DIR}` | Temporary FASTQ concatenation workspace — identical mount required for DooD |
| `${ODIN_WORK_DIR}:${ODIN_WORK_DIR}` | Nextflow work directories — use a WSL2-native Linux path to avoid slow v9fs I/O |
| `odin-app-data:/app/data` | App database (`odin.db`) and app state — persisted on Docker-managed Linux storage |
| `./pipeline/seed:/app/seed:ro` | CSV seed files (sites, samples, run accessions) loaded on first startup |
| `./pipeline/config/odin.config:/app/config/odin.config:ro` | Nextflow profile configuration (overrides the default baked into the image) |

> **Important:** Because Nextflow (running inside the container) passes file paths to the host Docker daemon when spawning sibling pipeline containers, all data paths must be mounted at the **identical** absolute path inside and outside the container. Configure `ODIN_PIPELINE_ROOT` and the other path variables in `.env`, then adjust the volumes block in `docker-compose.yml` if your data lives on a different drive.

If your Kraken2/Bracken databases live **outside** `ODIN_PIPELINE_ROOT` (e.g. on a different drive), uncomment the `ODIN_DATABASE_PATH` volume line in `docker-compose.yml` and set the variable in `.env`.


### Kraken2 Database Setup

The Kraken2 database directory is bind-mounted into the container using the same identical-path pattern as `ODIN_PIPELINE_ROOT`. This ensures Nextflow can resolve the path when spawning sibling containers.

**1. Set `ODIN_DATABASE_PATH` in `.env`:**

Point it to the host directory containing your database folder(s):

```bash
ODIN_DATABASE_PATH=/ODIN/databases
```

The directory must exist on the host before running `docker compose up`. It should be on a fast filesystem (ext4) — Kraken2 memory-maps the ~4 GB `hash.k2d` file and performs poorly on network/NTFS mounts.

**2. Populate the database directory** (choose one method):

#### Option A — Copy from an existing build

If you already have the database built locally:

```bash
mkdir -p /ODIN/databases/custom_pathogen_db
cp /path/to/built_db/*.k2d /ODIN/databases/custom_pathogen_db/
cp /path/to/built_db/*.kmer_distrib /ODIN/databases/custom_pathogen_db/
```

Only the `.k2d` files and Bracken `.kmer_distrib` file are needed (~4.2 GB total). The `genomes/`, `library/`, and `taxonomy/` directories from the build are not required at runtime.

#### Option B — Copy from a Windows filesystem path

```bash
mkdir -p /ODIN/databases/custom_pathogen_db
cp /mnt/<drive>/path/to/database/*.k2d /ODIN/databases/custom_pathogen_db/
cp /mnt/<drive>/path/to/database/*.kmer_distrib /ODIN/databases/custom_pathogen_db/
```

**3. Verify:**

```bash
ls -lh /ODIN/databases/custom_pathogen_db/
```

Expected output (~4.2 GB total):
```
hash.k2d                       4.2G
opts.k2d                         64
taxo.k2d                       14.8K
database150mers.kmer_distrib    2.2K
```

**4. Configure in the app:** Set the database path in Settings to `/ODIN/databases/custom_pathogen_db` (the full path to the directory containing the `.k2d` files).

> **Why identical-path mounts?** In Docker-outside-of-Docker mode, Nextflow passes file paths to the host Docker daemon. Both the API container and the Nextflow-spawned process containers must resolve database paths against the same host filesystem location.

### Temporary FASTQ Concatenation Workspace

Before Nextflow starts, ODIN concatenates FASTQ inputs into a temporary workspace. For best performance, place this on an ext4 filesystem.

Set this optional variable in `.env`:

```bash
ODIN_TMP_DIR=/var/tmp/odin_concat
```

Behavior:

- Per-run temp directories are created under `ODIN_TMP_DIR`.
- Concatenated intermediate `.fastq.gz` files are deleted automatically when the run finishes (success or failure).
- If `ODIN_TMP_DIR` is unset, fallback order is `TMP_DIR`, then `TMPDIR`, then `/var/tmp`.

> **Running on Windows with Docker Desktop?** See [docs/container-resources.md](docs/container-resources.md)
> for instructions on expanding WSL2 memory/CPU limits, moving Docker image storage off C:,
> and configuring Nextflow resource profiles for large sequencing runs.
>
> **AMR / SSU pipelines failing?** The `wf-metagenomics` pipeline requires at least 4 CPUs.
> If it fails with *"Process requirement exceeds available CPUs — req: 4; avail: N"*,
> check Docker Desktop **Settings → Resources → CPUs** or add `resourceLimits = [cpus: N]`
> to the `odin_epi2me` profile in `config/odin.config`.
> See [docs/container-resources.md](docs/container-resources.md) for details.

---

## Repository Structure

```
odin_pipeline/
  backend/
    app/
      main.py              # FastAPI application entry point
      database.py          # sqlite3 connection + get_db() dependency
      schema.sql           # DDL — single source of truth for all tables
      schemas.py           # Pydantic request/response models
      utils.py             # Path normalisation (Windows/Git Bash/WSL2)
      api/                 # REST API routers (one file per resource)
        sites.py           #   /api/sites
        samples.py         #   /api/samples
        nanopore_run_accessions.py  # /api/nanopore-run-accessions
        nanopore_runs.py   #   /api/nanopore-runs
        biomeme_runs.py    #   /api/biomeme-runs
        discovery.py       #   /api/discovery/scan (MinKNOW)
        biomeme_discovery.py #  /api/discovery/biomeme
        pipeline.py        #   /api/pipeline/runs
        databases.py       #   /api/databases
        settings.py        #   /api/settings
        lookup_values.py   #   /api/lookup-values
        export.py          #   /api/export
        autocomplete.py    #   /api/autocomplete
      pipeline/            # Nextflow orchestration
        command_builder.py # Build Nextflow commands per pipeline type
        executor.py        # FIFO job queue, single background worker thread
        execution_env.py   # Wraps command for Windows (wsl bash -lc)
        samplesheet.py     # Generate CSV samplesheet for taxprofiler
        log_hints.py       # Error pattern matching for SSE log hints
      parsers/             # Pure data-transformation functions
        discovery.py       # Filesystem scanning (MinKNOW, Biomeme)
        kraken_parser.py   # Parse Kraken2 reports
        kraken_postprocessor.py # Annotate Kraken2 with pathogen file, write Feather
        amr_postprocessor.py    # AMR pipeline post-processing
      seed/                # CSV seed data for lookup values and settings
    tests/                 # pytest suite (in-memory SQLite per test)
  frontend/
    src/app/
      core/
        models/            # TypeScript interfaces mirroring backend schemas
        services/          # Angular HttpClient wrappers
      features/
        sites/             # Site list + form
        samples/           # Sample list + form
        runs/              # Nanopore run list + form
        biomeme-runs/      # Biomeme run list + form
        settings/          # Settings page
        discovery/         # Dashboard — file scanning and pipeline launch wizard
      shared/components/   # Shared UI components (nav bar, etc.)
    jest.config.js         # Jest + jest-preset-angular test config
    proxy.conf.json        # Dev proxy: /api → localhost:8080
    nginx.conf             # Production nginx config (Docker mode)
  pipeline/
    config/
      odin.config          # Nextflow profiles (odin, odin_big)
      databases.csv        # Example taxprofiler database definitions
    seed/                  # CSV seed files for initial data population
  ca/                      # Place corporate CA cert here (.crt/.cer) — gitignored
  .env.example             # Template — copy to .env and fill in paths
  docker-compose.yml       # Docker Engine configuration
  Dockerfile.backend       # FastAPI + Nextflow + Java image
  Dockerfile.frontend      # Node build + nginx production image
  pyproject.toml           # Python dependencies + ruff + pytest config
```

---

## Development

### Backend

[uv](https://docs.astral.sh/uv/) is the recommended tool for local development.

```bash
# Install uv (once) — https://docs.astral.sh/uv/getting-started/installation/
curl -LsSf https://astral.sh/uv/install.sh | sh

# From odin_pipeline/
uv sync --group dev           # creates .venv and installs all dependencies
uv run uvicorn backend.app.main:app --reload --port 8080
```

Interactive API docs: **http://localhost:8080/docs**

Common dev commands:

```bash
uv run pytest                 # run backend tests
uv run ruff check backend/    # lint Python code
uv run ruff format backend/   # format Python code
uv run uvicorn backend.app.main:app --reload --port 8080  # start backend
```

### Frontend

```bash
cd frontend
npm install
npm start        # dev server at http://localhost:4200, proxied to :8080
npm test         # run Jest tests
```

### Build frontend for production

```bash
cd frontend
npm run build    # output → frontend/dist/odin-frontend/browser/
```

In production the built frontend is served by the nginx container (`odin-app-client`).

---

## Environment Variables

All environment variables can be set in a `.env` file (copied from `.env.example`) or passed directly to the process / Docker container.

### Path format

All path variables accept any format — the backend normalises them automatically:

| Format | Example |
|---|---|
| Windows | `<drive>:/ODIN/pipeline` or `<drive>:\ODIN\pipeline` |
| Git Bash | `/<drive>/ODIN/pipeline` |
| WSL2 / Linux | `/mnt/<drive>/ODIN/pipeline` |

**`ODIN_PIPELINE_ROOT`** is an exception — it is used verbatim as the Docker volume mount path in `docker-compose.yml`. The required format depends on context:

- **Docker on Windows** — use WSL2/Linux format: `/mnt/<drive>/ODIN/pipeline`
- **Running locally on Windows** — any format works (the backend normalises it)

### Required

| Variable | Description |
|---|---|
| `ODIN_PIPELINE_ROOT` | Root directory for all ODIN data. Subdirectories `minknow/`, `output/`, `biomeme/`, `tools/`, `input_sheets/` are auto-populated as default Settings values. Used verbatim as the Docker volume mount path — must be in WSL2/Linux format (`/mnt/<drive>/...`) when running in Docker on Windows. See Path format above. |

### Optional — application

| Variable | Default | Description |
|---|---|---|
| `ODIN_DATA_DIR` | `~/.odin-app` | Directory for the SQLite database (`odin.db`) when running the backend directly outside containers. In Docker compose, this is fixed to `/app/data` inside the container and backed by the `odin-app-data` named volume. |
| `CORS_ORIGINS` | `http://localhost:4200,http://localhost:8080` | Comma-separated list of allowed CORS origins. |

### Optional — Nextflow / pipeline execution

| Variable | Default | Description |
|---|---|---|
| `NF_BIN` | `nextflow` (from PATH) | Full path to the Nextflow executable. Useful locally when Nextflow lives inside WSL or is not on PATH. |
| `NEXTFLOW_CONFIG_FILE` | `/app/config/odin.config` (Docker), *(unset locally)* | Path to the Nextflow config file passed via `-c`. In Docker pre-set to `/app/config/odin.config` (mounted from `./config/odin.config`). Locally, set this or configure it in the Settings UI. Also sets the `nextflow_config_file` DB key. |
| `NEXTFLOW_PROFILE` | *(unset — defaults to `odin` in code)* | Nextflow `-profile` value. Also sets the `nextflow_profile` DB key. |
| `ODIN_DATABASES_FILE` | `$ODIN_PIPELINE_ROOT/input_sheets/databases.csv` | Path to the Kraken2/Bracken databases CSV. Also sets the `databases_file` DB key. |
| `ODIN_PATHOGENS_FILE` | *(unset)* | Path to the pathogens Excel file used by post-processing. Also sets the `pathogens_file` DB key. |
| `ODIN_EXTRACT_TARGETS_FILE` | `$ODIN_PIPELINE_ROOT/config/extract_targets.csv` | Path to the read-extraction targets CSV. Also sets the `extract_targets_file` DB key. |
| `ODIN_TMP_DIR` | `TMP_DIR` → `TMPDIR` → `/var/tmp` | Base directory for temporary concatenated FASTQ inputs created before pipeline launch. Use an ext4 path to avoid slow temporary I/O on Windows-mounted drives. Intermediate files are removed automatically after each run. |
| `ODIN_WORK_DIR` | *(unset)* | Nextflow work directory base. Use a WSL2-native Linux path (e.g. `/mnt/wsl/odin-work`) to avoid slow v9fs I/O on Windows-mounted drives. |
| `ODIN_LOG_DIR` | *(unset — logs next to output dir)* | Override directory for ODIN streaming logs and `.nextflow.log` files. |
| `ODIN_STORE_DIR` | `$ODIN_PIPELINE_ROOT/nf/store` | Nextflow `storeDir` for caching downloaded pipeline assets (mpox, wf-metagenomics). |
| `NXF_ASSETS` | `$ODIN_PIPELINE_ROOT/nf/assets` | Nextflow pipeline asset cache directory. |
| `TAXPROFILER_DIR` | *(unset — pulls from GitHub)* | Path to a local `nf-core/taxprofiler` checkout. Only needed for development / testing local pipeline changes. |
| `TAXPROFILER_REVISION` | `1.2.6` | GitHub tag to use when pulling taxprofiler from the hub. Update when upgrading to a new stable release. |
| `SQUIRREL_IMAGE` | `articnetworkorg/squirrel:1.3.2` | Docker image used for mpox Squirrel phylogenetic post-processing. |

> **Priority:** Environment variables always take precedence over values stored in the Settings UI (DB), which in turn take precedence over computed defaults derived from `ODIN_PIPELINE_ROOT`. This applies uniformly to all settings.

---

## API Reference

The FastAPI app exposes an OpenAPI spec at `/docs` (Swagger UI) and `/redoc`.

| Resource | Endpoints |
|---|---|
| Sites | `GET/POST /api/sites`, `GET/PATCH/DELETE /api/sites/{id}` |
| Samples | `GET/POST /api/samples`, `GET/PATCH/DELETE /api/samples/{id}` |
| Nanopore run accessions | `GET/POST /api/nanopore-run-accessions`, `GET/PATCH/DELETE /api/nanopore-run-accessions/{id}` |
| Nanopore runs | `GET/POST /api/nanopore-runs`, `GET/PATCH/DELETE /api/nanopore-runs/{id}` |
| Biomeme runs | `GET/POST /api/biomeme-runs`, `GET/PATCH/DELETE /api/biomeme-runs/{id}` |
| Discovery | `GET /api/discovery/nanopore`, `GET /api/discovery/biomeme` |
| Pipeline runs | `GET/POST /api/pipeline/runs`, `GET /api/pipeline/runs/{id}`, `GET /api/pipeline/runs/{id}/logs` (SSE) |
| Databases | `GET/POST /api/databases`, `GET/PUT/DELETE /api/databases/{id}`, `GET /api/databases/effective` |
| Settings | `GET /api/settings`, `GET/PUT /api/settings/{key}`, `GET /api/settings/health` |
| Lookup values | `GET /api/lookup-values/{list_name}` |
| Export | `GET /api/export/excel` |
| Sync | `GET /api/sync/export`, `POST /api/sync/preview`, `POST /api/sync/apply` |
| Autocomplete | `GET /api/autocomplete/site-ids` (and other fields) |

Derived fields (`site_code`, `sample_code`, `minknow_sample_id`, `alias`) are always computed server-side and never accepted from clients.

---

## Database

SQLite at `$ODIN_DATA_DIR/odin.db` (defaults to `~/.odin-app/odin.db` when the env var is unset; `/app/data/odin.db` in Docker mode).

Core tables: `sites`, `samples`, `nanopore_run_accessions`, `nanopore_runs`, `biomeme_runs`, `pipeline_runs`, `lookup_values`, `config_values`. Discovery tables: `nanopore_disk_cache`, `nanopore_run_exclusions`, `nanopore_barcode_exclusions`, `nanopore_merge_decisions`, `biomeme_folder_exclusions`.

All tables use UUID string primary keys and include sync columns (`created_at`, `updated_at`, `created_by`, `updated_by`, `is_deleted`). Deletes are soft — rows are flagged `is_deleted = 1`.

The database is **outside** the repository — `git pull` and re-running the install script never affect stored metadata.

---


## Third-party pipeline workflows

ODIN launches external Nextflow workflows and container images — currently including
`nf-core/taxprofiler`, `epi2me-labs/wf-metagenomics`, `artic-network/artic-mpxv-nf`, and the
`articnetworkorg/squirrel` container image. These are not bundled with ODIN; they are fetched
at runtime from their upstream sources.

Each has its own license, independent of ODIN's AGPL-3.0 license, and some may be more
restrictive — for example, permitting research use only. Review the current license at its
source before using a workflow.

## License

Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0). See [LICENSE](LICENSE) for the full text.
