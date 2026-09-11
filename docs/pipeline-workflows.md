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

#### Read totals on every row

Every row of the taxprofiler and SSU datasets carries its file's two read totals,
`classified_reads` (the root row's clade count: the denominator of reads per million) and
`total_reads` (classified plus unclassified), the same two columns `amr_data` has. They are
computed per `file_name` after the merge with the existing feather, so rows written before the
columns existed are filled on the next post-processing of that country. A file in which nothing
was classified gets 0 and has no rate. Feathers that are not post-processed again can be
backfilled in place, once, where the files are:

```bash
python -m backend.app.pipeline.scripts.add_read_totals /path/to/enlighten/data               # in place
python -m backend.app.pipeline.scripts.add_read_totals /path/to/enlighten/data --dry-run     # report only
python -m backend.app.pipeline.scripts.add_read_totals /path/to/enlighten/data --out /other  # originals untouched
```

It needs no Kraken2 report: the full feather holds every report row, including the root and
unclassified rows the totals come from. It recomputes (safe to repeat), writes each file to a
temporary name and renames it, and regenerates the `_target_group` file from the full one. Run
it with post-processing idle.

To form a pathogen group's reads, sum `No of Frags` (direct reads) over the rows labelled with
the group, or `No of Frags root` over the group's listed TaxIDs only. Summing `No of Frags
root` over the labelled rows counts a species' serovars twice, because the label covers the
whole clade.

### wf-metagenomics AMR — antimicrobial resistance gene profiling

- Pipeline: `epi2me-labs/wf-metagenomics` with `--amr --amr_db card`
- Databases: CARD (for AMR gene detection); PlusPF-8 (for the taxonomic classification component of the same workflow)
- Does **not** require `databases.csv` or a samplesheet CSV
- Launched by: `start_nextflow_amr.sh`

Reference command:
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

Reference command:
```bash
nextflow run epi2me-labs/wf-metagenomics \
  --fastq '<run_path>/fastq_pass/' \
  --database_set 'SILVA_138_1' \
  --out_dir '.../nanopore_processed/outputs_wf_metagenomics_ssu/<run_accession>'
```

## Replicates

Several barcodes of one run can carry the same sample — separate filters, separate
extracts, or one extract sequenced again. The metadata says so with a **replicate number**
on each barcode (Runs → edit barcode, or the `replicate` column of the metadata workbook's
nanopore sheet), and the pipelines treat a numbered group as one sample:

- **Taxprofiler and SSU always pool.** The group's FASTQ files are concatenated into one
  input before classification, so the sample is classified once at the combined depth.
  The samplesheet row, the Kraken2 report and the dataset rows are named after the
  members, joined with `-` (e.g. `barcode07-barcode08-barcode01`, in replicate order), and
  the dataset's `replicate` column reads `pooled`.
- **AMR follows the `amr_replicates` setting** (Settings, or `ODIN_AMR_REPLICATES`).
  `pool`, the default, does the same as above, so a sample's AMR profile comes from the
  same reads as its taxonomy. `per_barcode` screens each library on its own; every
  member then gets its own rows, with its replicate number in `replicate`. The choice is
  resolved when a run is launched and written to its `run_manifest.txt`, so a later
  change of the setting does not alter what an existing run folder means. This matters
  because wf-metagenomics passes or fails each input on its own: pooling can turn
  replicate libraries that were each too shallow into one input that is screened.
- **Mpox never pools.** artic-mpxv-nf works per barcode.

A sample on several barcodes **without** replicate numbers is a metadata error until
someone says otherwise — two samples sharing a label, or a mistyped date — and the
dashboard and launch wizard show it as a warning. Taxprofiler, SSU and AMR refuse to
launch over it (pre-launch validation), as they do over a group that is only partly
numbered or repeats a number. A fully numbered group is shown as a note saying it will be
pooled. Runs merged as continuations of one flow cell share their barcodes, so a group
spans the merged set; the numbers must agree across the accessions.

## Design provenance

The three-workflow structure and the choice of `epi2me-labs/wf-metagenomics` for AMR and SSU analysis were defined by the WP3 bioinformatics team in August 2025, together with the output directory structure, the reference nextflow commands, and the key distinction that the AMR and SSU workflows do not require a samplesheet or databases CSV (unlike taxprofiler).
