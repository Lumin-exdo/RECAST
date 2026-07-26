# RECAST Minimal Reproduction Checkout

[中文说明](README_CN.md)

This directory is a minimal local checkout for reproducing the RECAST experiments. It does not depend on a machine-specific absolute pathname. The complete historical directory and original artifacts are preserved in the sibling directory `RECAST-backup-20260726-full/`.

## Reviewer reproduction reference

Set the parent checkout once:

```bash
export PROJECT_ROOT="$(pwd)"
cd "$PROJECT_ROOT"
```

The main strict results (200 T1 direct-conflict and 200 T2 indirect-conflict samples per system) are:

| System | T1 | T2 | Overall | Main conclusion |
|---|---:|---:|---:|---|
| RECAST | 63.7% | 54.8% | 59.3% | Best overall; write-time abductive conflict handling and structured query reasoning are effective. |
| CupMem | 58.5% | 47.3% | 52.9% | Strong baseline, below RECAST on both conflict types. |
| A-MEM | 26.7%* | 13.3%* | — | Retrieval usually finds new evidence, but answer generation often follows stale state. |
| Naive-RAG | 14.7% | 8.0% | 11.3% | Retrieval without state tracking is ineffective for staleness resistance. |
| mem-0 | 0.0%* | 0.0%* | 0.0% | Parallel old/new memories create architectural state ambiguity. |

`*` = 5 T1 + 5 T2 smoke/pilot samples; not a full-size ranking comparison. Lenient reference scores are RECAST 80.7%/78.8% and CupMem 80.0%/72.0% (T1/T2).

### Reproduction commands

These commands may call paid external APIs. Run `--help` first and configure credentials yourself.

**RECAST main:**

```bash
cd "$PROJECT_ROOT"
python -m RECAST.run_new_mem --run-name fix400 --n-samples 0 --workers 4 --no-thinking --global-temperature 0.3 --embedding-model-path RECAST/models/all-MiniLM-L6-v2 --embedding-device cpu
```

**CupMem baseline:**

```bash
cd "$PROJECT_ROOT"
python cup_mem/run_cup_mem_batch.py --run-name step2b_cupmem_t03 --n-samples 0 --workers 4 --global-temperature 0.3 --data-path "$PROJECT_ROOT/RECAST/STALE/STALE/outputs/STALE_MAIN.json" --embedding-model-path "$PROJECT_ROOT/RECAST/models/all-MiniLM-L6-v2" --output-root "$PROJECT_ROOT/RECAST/runs"
```

**A-MEM and mem-0 fairness reruns:**

```bash
cd "$PROJECT_ROOT/RECAST"
python codex_fairness_audit/run_fair_attribution_rerun.py --method amem --output-dir codex_fairness_audit/runs/amem_full_fair_smoke10_deepseek_20260724 --t1-uids 89b77229,7ee76c41,1a85388f,f6d12075,d9545076 --t2-uids d806d94c,feef3933,14897e47,c9cc370e,2c711459
python codex_fairness_audit/run_fair_attribution_rerun.py --method mem0 --output-dir codex_fairness_audit/runs/mem0_fair_10 --t1-uids 89b77229,7ee76c41,1a85388f,f6d12075,d9545076 --t2-uids d806d94c,feef3933,14897e47,c9cc370e,2c711459
```

**Naive-RAG:**

```bash
cd "$PROJECT_ROOT"
python naive_rag/run_naive_rag_stale.py --run-name naive_rag_full --workers 8 --embedding-model-path "$PROJECT_ROOT/RECAST/models/all-MiniLM-L6-v2"
```

**LongMemEval knowledge-update:**

```bash
cd "$PROJECT_ROOT/RECAST"
python scripts/prepare_longmemeval.py --input "$PROJECT_ROOT/LongMemEval/data/longmemeval_s.json" --output /tmp/longmemeval_ku_recast.json --types knowledge-update
cd "$PROJECT_ROOT"
python -m RECAST.run_new_mem --run-name lme_ku_full --data-path /tmp/longmemeval_ku_recast.json --n-samples 0 --workers 4 --no-thinking --global-temperature 0.3 --embedding-model-path RECAST/models/all-MiniLM-L6-v2 --embedding-device cpu
```

**Judge cross-validation:**

```bash
cd "$PROJECT_ROOT/RECAST"
python scripts/validate_judge_real.py
```

**Strict scoring template:**

```bash
cd "$PROJECT_ROOT/RECAST/STALE/STALE/Evaluation"
python full_eval_performance.py --answers-path <answers.json> --dataset-path "$PROJECT_ROOT/RECAST/STALE/STALE/outputs/STALE_MAIN.json" --output-path <scores.json> --model-method <label> --scorer qwen3.6-plus --type T1
```

### Ablation conclusions

Strict T2 scores: no query hypothesis 52.3%, no impression update 53.2%, per-session pool reset 50.7%, no evidence pool 48.0%, no hypothesis generation 44.5%, embedding judgment 40.3%. The evidence pool and query-time structured reasoning are therefore substantive contributors; these are component ablations, not alternative systems.

The complete per-dimension tables, UID lists, output paths, lenient commands, and provenance notes remain in [`EXPERIMENTS_CATALOG.md`](EXPERIMENTS_CATALOG.md).

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
