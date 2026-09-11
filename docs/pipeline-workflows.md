# ODIN Analysis Pipeline Workflows

This document records the three Nanopore analysis workflows supported by the ODIN Pipeline Scripts and the ODIN Pipeline Web Application, their pipeline tools, databases, and the design decisions behind them.

## Overview

| Workflow | Nextflow pipeline | Key database | Output directory |
|---|---|---|---|
| Taxonomic classification | `nf-core/taxprofiler` | Kraken2 PlusPF-8 | `nanopore_processed/outputs_taxprofiler/` |
| AMR gene profiling | `epi2me-labs/wf-metagenomics --amr` | CARD | `nanopore_processed/outputs_wf_metagenomics_amr/` |
| SSU rRNA classification | `epi2me-labs/wf-metagenomics` | SILVA_138_1 | `nanopore_processed/outputs_wf_metagenomics_ssu/` |

The taxprofiler and AMR/SSU workflows use different pipeline tools and different primary databases. In particular, **AMR gene profiling uses the CARD (Comprehensive Antibiotic Resistance Database), not the Kraken2 database** used for taxonomic classification.

## Workflow details

### Taxprofiler — taxonomic classification

- Pipeline: `nf-core/taxprofiler`
- Database: Kraken2 with the PlusPF-8 database set (custom ODIN pathogen reference)
- Requires: metadata Excel workbook, `databases.csv`, samplesheet CSV (`all_samples_*.csv`)
- Launched by: `start_nextflow.sh`

### wf-metagenomics AMR — antimicrobial resistance gene profiling

- Pipeline: `epi2me-labs/wf-metagenomics` with `--amr --amr_db card`
- Databases: CARD (for AMR gene detection); PlusPF-8 (for the taxonomic classification component of the same workflow)
- Does **not** require `databases.csv` or a samplesheet CSV
- Launched by: `start_nextflow_amr.sh`

Reference command (from design note, 19 Aug 2025):
```bash
nextflow run epi2me-labs/wf-metagenomics \
  --fastq '<run_path>/fastq_pass/' \
  --database_set 'PlusPF-8' \
  --amr --amr_db card \
  --out_dir '.../nanopore_processed/outputs_wf_metagenomics_amr/<run_accession>'
```

### wf-metagenomics SSU — 16S/18S rRNA classification

- Pipeline: `epi2me-labs/wf-metagenomics` (no `--amr` flag)
- Database: SILVA_138_1
- Does **not** require `databases.csv` or a samplesheet CSV
- Launched by: `start_nextflow_ssu.sh`

Reference command (from design note, 19 Aug 2025):
```bash
nextflow run epi2me-labs/wf-metagenomics \
  --fastq '<run_path>/fastq_pass/' \
  --database_set 'SILVA_138_1' \
  --out_dir '.../nanopore_processed/outputs_wf_metagenomics_ssu/<run_accession>'
```

## Design provenance

The three-workflow structure and the choice of `epi2me-labs/wf-metagenomics` for AMR and SSU analysis were defined by the WP3 bioinformatics team in an internal design note dated 19 August 2025. The note specified the output directory structure, the reference nextflow commands, and the key distinction that the AMR and SSU scripts do not require a samplesheet or databases CSV (unlike taxprofiler). The initial `start_nextflow_amr.sh` script implementing this design was committed to the pipeline_scripts repository by Gro Fonnes on 20 August 2025.
