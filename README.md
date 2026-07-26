# RECAST Minimal Reproduction Checkout

[中文说明](README_CN.md)

This directory is a minimal local checkout for reproducing the RECAST experiments. It does not depend on a machine-specific absolute pathname. The complete historical directory and original artifacts are preserved in the sibling directory `RECAST-backup-20260726-full/`.

## Layout

- `run_new_mem.py`: main RECAST experiment entry point
- `codex_fairness_audit/`: fair-attribution reruns for A-MEM, mem-0, Naive-RAG, and related baselines
- `scripts/`: data preparation, scoring, and judge-validation scripts
- `STALE/`: STALE data and evaluator
- `models/all-MiniLM-L6-v2/`: local embedding model
- `core/`, `memory/`, `query/`, `retrieval/`, `store_layer/`, `write/`: core implementation modules
- `EXPERIMENTS_CATALOG.md`: experiment results, configurations, and reproduction commands
- `MINIMAL_REPRODUCTION_FILE_MANIFEST.tsv`: file manifest for this checkout

## Set the project root

From the parent directory containing `RECAST/`:

```bash
export PROJECT_ROOT="$(pwd)"
cd "$PROJECT_ROOT/RECAST"
```

When running from another location, set `PROJECT_ROOT` explicitly. The code derives default data, model, and output paths from the checkout location.

## Python environment

Use Python 3.10+ with the project dependencies, for example:

```bash
python -m pip install -r requirements.txt  # if such a file is provided
```

An existing Conda environment may also be used. Before running an experiment, verify that `openai`, `numpy`, and `sentence-transformers` can be imported.

## Before running experiments

The full experiments in the catalog call external model APIs and may incur charges. Start with offline checks:

```bash
python run_new_mem.py --help
python codex_fairness_audit/run_fair_attribution_rerun.py --help
```

The complete commands are documented in `EXPERIMENTS_CATALOG.md`. Configure API credentials, models, and a budget before running any paid experiment.

## Git

This directory is an independently initialized Git repository. Its initial commit describes this minimal reproduction checkout. The complete original repository remains in the backup directory and has not been deleted.
