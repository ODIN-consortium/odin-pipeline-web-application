# Container Resource Configuration

This page covers how to tune the CPU and memory resources available to ODIN
pipelines when running in Docker Desktop on Windows or macOS.

## Pipeline CPU requirements

ODIN uses different Nextflow profiles for different pipelines:

| Profile | Pipelines | Default CPU limit |
|---|---|---|
| `odin` | taxprofiler | `executor.cpus = 4`, `resourceLimits cpus: 4` |
| `odin_epi2me` | wf-metagenomics (AMR, SSU) | none — inherits from Docker |
| `odin_big` | taxprofiler (large runs) | `executor.cpus = 16` |

The `wf-metagenomics` pipeline (`odin_epi2me` profile) requires at least **4 CPUs**
for its `fastcat` / `bgzip` steps. If Docker has fewer CPUs available, the pipeline
fails immediately with:

```
Process requirement exceeds available CPUs -- req: 4; avail: 2
```

## Checking and increasing Docker Desktop CPU allocation

In Docker Desktop go to **Settings → Resources → CPUs** and verify the allocation
is at least 4. Increase it if not.

On Windows with the WSL2 backend, Docker Desktop typically makes all host CPUs
available. However, a `.wslconfig` file in your Windows home directory can cap
WSL2 resource usage — check for `%USERPROFILE%\.wslconfig` if Docker shows fewer
CPUs than expected:

```ini
# Example .wslconfig — remove or increase these limits if present
[wsl2]
processors=4
memory=8GB
```

After changing `.wslconfig`, restart WSL2 for the new limits to take effect:

```powershell
wsl --shutdown
```

## Capping per-process CPU requests in odin.config (workaround)

If you cannot increase the Docker CPU allocation (e.g. enforced by IT policy),
you can instead tell Nextflow to silently cap any process CPU request to what is
actually available.

Open `config/odin.config` and add `executor.cpus` and `resourceLimits` to the
`odin_epi2me` profile. Set `N` to the number of CPUs Docker actually has:

```groovy
odin_epi2me {
    // ... existing settings unchanged ...
    executor.name           = 'local'
    executor.cpus           = N          // ← add: tell Nextflow how many CPUs are available
    process {
        beforeScript = _beforeScript
        resourceLimits = [cpus: N]       // ← add: silently cap per-process requests
        withName: '.*MULTIQC' {
            stageInMode = 'copy'
        }
    }
}
```

`resourceLimits` instructs Nextflow to reduce any process CPU request that exceeds
the limit rather than aborting. The pipeline will run slower but will complete.

> **Note:** changes to the `odin` or `odin_big` profiles do not affect the AMR/SSU
> pipelines — only `odin_epi2me` is used by `wf-metagenomics`.

## Memory allocation

The PlusPF-8 Kraken2 database used by `wf-metagenomics` requires roughly 8 GB of
RAM to load into memory. The pipeline prints a note about this at startup:

```
Note: Memory available to the workflow must be slightly higher than size
of the database PlusPF-8 index (8GB) or consider to use --kraken2_memory_mapping
```

If your machine has less than ~10 GB free, either pass `--kraken2_memory_mapping`
in the pipeline parameters to use memory-mapped I/O (slower but lower peak RSS),
or increase Docker Desktop memory under **Settings → Resources → Memory**.

## Moving Docker image storage off the system drive (Windows)

By default Docker Desktop stores images and container layers inside the WSL2
distribution (`ext4.vhdx`) under `%LOCALAPPDATA%\Docker\wsl\`. On machines with a
small C: drive this can fill up quickly.

To relocate the storage, shut down Docker Desktop and move the WSL distribution:

```powershell
wsl --shutdown
wsl --export docker-desktop-data D:\docker-data\docker-desktop-data.tar
wsl --unregister docker-desktop-data
wsl --import docker-desktop-data D:\docker-data D:\docker-data\docker-desktop-data.tar --version 2
```

Restart Docker Desktop afterwards. New images and containers will be stored on D:.
