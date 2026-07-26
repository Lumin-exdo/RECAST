# RECAST 最小复现目录

本目录是 RECAST 实验的最小本地复现 checkout。它不依赖某台机器的固定绝对路径；完整历史文件和原始结果保存在同级的 `RECAST-backup-20260726-full/` 中。

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
