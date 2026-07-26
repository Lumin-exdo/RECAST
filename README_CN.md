# RECAST 最小复现目录

[English version](README.md)

本目录是 RECAST 实验的最小本地复现 checkout。它不依赖某台机器的固定绝对路径；完整历史文件和原始结果保存在同级的 `RECAST-backup-20260726-full/` 中。

## 审稿人复现参考

先设置包含 `RECAST/` 的项目根目录：

```bash
export PROJECT_ROOT="$(pwd)"
cd "$PROJECT_ROOT"
```

主要 strict 结果（每个系统 200 个 T1 直接冲突样本、200 个 T2 间接冲突样本）如下：

| 系统 | T1 | T2 | 合并 | 主要结论 |
|---|---:|---:|---:|---|
| RECAST | 63.7% | 54.8% | 59.3% | 总体最佳；写阶段溯因式冲突处理和查询阶段结构化推理有效。 |
| CupMem | 58.5% | 47.3% | 52.9% | 强 baseline，但两类冲突均低于 RECAST。 |
| A-MEM | 26.7%* | 13.3%* | — | 通常能检索到新证据，但答案生成经常继续采用过时状态。 |
| Naive-RAG | 14.7% | 8.0% | 11.3% | 没有状态跟踪的检索无法有效抵抗过时前提。 |
| mem-0 | 0.0%* | 0.0%* | 0.0% | 新旧记忆并列存储造成架构性的当前状态歧义。 |

`*` 表示 5 个 T1 + 5 个 T2 的 smoke/pilot，不是完整规模排名比较。Lenient 参考分数为 RECAST 80.7%/78.8%、CupMem 80.0%/72.0%（T1/T2）。

### 复现命令

这些命令可能调用收费外部 API。请先运行 `--help`，并自行配置密钥。

**RECAST 主实验：**

```bash
cd "$PROJECT_ROOT"
python -m RECAST.run_new_mem --run-name fix400 --n-samples 0 --workers 4 --no-thinking --global-temperature 0.3 --embedding-model-path RECAST/models/all-MiniLM-L6-v2 --embedding-device cpu
```

**CupMem baseline：**

```bash
cd "$PROJECT_ROOT"
python cup_mem/run_cup_mem_batch.py --run-name step2b_cupmem_t03 --n-samples 0 --workers 4 --global-temperature 0.3 --data-path "$PROJECT_ROOT/RECAST/STALE/STALE/outputs/STALE_MAIN.json" --embedding-model-path "$PROJECT_ROOT/RECAST/models/all-MiniLM-L6-v2" --output-root "$PROJECT_ROOT/RECAST/runs"
```

**A-MEM 与 mem-0 公平归因重评：**

```bash
cd "$PROJECT_ROOT/RECAST"
python codex_fairness_audit/run_fair_attribution_rerun.py --method amem --output-dir codex_fairness_audit/runs/amem_full_fair_smoke10_deepseek_20260724 --t1-uids 89b77229,7ee76c41,1a85388f,f6d12075,d9545076 --t2-uids d806d94c,feef3933,14897e47,c9cc370e,2c711459
python codex_fairness_audit/run_fair_attribution_rerun.py --method mem0 --output-dir codex_fairness_audit/runs/mem0_fair_10 --t1-uids 89b77229,7ee76c41,1a85388f,f6d12075,d9545076 --t2-uids d806d94c,feef3933,14897e47,c9cc370e,2c711459
```

**Naive-RAG：**

```bash
cd "$PROJECT_ROOT"
python naive_rag/run_naive_rag_stale.py --run-name naive_rag_full --workers 8 --embedding-model-path "$PROJECT_ROOT/RECAST/models/all-MiniLM-L6-v2"
```

**LongMemEval knowledge-update：**

```bash
cd "$PROJECT_ROOT/RECAST"
python scripts/prepare_longmemeval.py --input "$PROJECT_ROOT/LongMemEval/data/longmemeval_s.json" --output /tmp/longmemeval_ku_recast.json --types knowledge-update
cd "$PROJECT_ROOT"
python -m RECAST.run_new_mem --run-name lme_ku_full --data-path /tmp/longmemeval_ku_recast.json --n-samples 0 --workers 4 --no-thinking --global-temperature 0.3 --embedding-model-path RECAST/models/all-MiniLM-L6-v2 --embedding-device cpu
```

**Judge 跨模型验证：**

```bash
cd "$PROJECT_ROOT/RECAST"
python scripts/validate_judge_real.py
```

**Strict 评分模板：**

```bash
cd "$PROJECT_ROOT/RECAST/STALE/STALE/Evaluation"
python full_eval_performance.py --answers-path <answers.json> --dataset-path "$PROJECT_ROOT/RECAST/STALE/STALE/outputs/STALE_MAIN.json" --output-path <scores.json> --model-method <label> --scorer qwen3.6-plus --type T1
```

### 消融结论

Strict T2 分数：无查询假说 52.3%，无印象更新 53.2%，per-session pool reset 50.7%，无证据池 48.0%，无假说生成 44.5%，嵌入判断 40.3%。因此证据池和查询阶段结构化推理是实质性贡献；这些是组件消融，不是替代系统。

逐维度表格、UID 列表、输出路径、lenient 命令和来源说明仍完整保存在 [`EXPERIMENTS_CATALOG.md`](EXPERIMENTS_CATALOG.md) 中。

## 目录结构

- `run_new_mem.py`：RECAST 主实验入口
- `codex_fairness_audit/`：A-MEM、mem-0、Naive-RAG 等公平归因重评入口
- `scripts/`：数据准备、评分和 judge 验证脚本
- `STALE/`：STALE 数据集及评分器
- `models/all-MiniLM-L6-v2/`：本地嵌入模型
- `core/`、`memory/`、`query/`、`retrieval/`、`store_layer/`、`write/`：核心模块
- `EXPERIMENTS_CATALOG.md`：实验结果、配置和复现命令总目录
- `MINIMAL_REPRODUCTION_FILE_MANIFEST.tsv`：当前 checkout 的文件清单

## 设置项目根目录

在包含 `RECAST/` 的父目录执行：

```bash
export PROJECT_ROOT="$(pwd)"
cd "$PROJECT_ROOT/RECAST"
```

若从其他位置运行，也可以显式设置 `PROJECT_ROOT`。代码会从 checkout 位置推导默认数据、模型和输出路径。

## Python 环境

需要 Python 3.10+ 以及项目依赖，例如：

```bash
python -m pip install -r requirements.txt  # 若提供该文件
```

也可以使用已有的 Conda 环境。运行前确认 `openai`、`numpy` 和 `sentence-transformers` 可导入。

## 运行前须知

catalog 中的完整实验会调用外部模型 API，并可能产生费用。默认只建议先执行：

```bash
python run_new_mem.py --help
python codex_fairness_audit/run_fair_attribution_rerun.py --help
```

真实实验和评分命令见 `EXPERIMENTS_CATALOG.md`。运行收费实验前，请自行配置 API 密钥、模型和预算。

## Git

本目录是一个重新初始化的独立 Git 仓库，当前初始提交只描述这个最小复现 checkout。完整旧仓库仍在备份目录中，未被删除。
