# RECAST 实验结果目录

整理日期：2026-07-25。按论文逻辑顺序排列，仅收录最终保留结果。

---

## 本机可复现路径约定

本目录适用于任意本地 checkout。命令中的 `$PROJECT_ROOT` 表示项目集合的根目录，应在复现前设置：

```bash
export PROJECT_ROOT="$(pwd)"
# 如果当前目录是 RECAST，则使用：
# export PROJECT_ROOT="$(git rev-parse --show-toplevel)/.."
```

结果文件路径中的 `$PROJECT_ROOT` 是机器无关的根路径变量；不会修改已记录的实验结果内容。当前代码使用 `--global-temperature`，嵌入模型统一使用 `$PROJECT_ROOT/RECAST/models/all-MiniLM-L6-v2`。评分和真实 API 命令仍仅作记录，按本地成本与密钥情况决定是否运行。

## 维度说明

STALE benchmark 的三个评估维度（本文使用缩写）：

| 缩写 | 维度（dim） | 含义 |
|------|------------|------|
| SR | dim1 — **State Resolution** | 系统能否识别旧记忆已过时 |
| PR | dim2 — **Premise Resistance** | 面对预设旧状态仍成立的对抗性问题，能否拒绝错误前提 |
| IPA | dim3 — **Implicit Policy Adaptation** | 行动推荐是否基于当前状态而非过时状态 |

评判器：`qwen3.6-plus`，两种 rubric：

- **strict**（主要口径）：推理链须显式引用新证据（M\_new）
- **lenient**（参考）：方向正确即通过，无需引用链

---

## 一、STALE 主对比实验

### 1.1 RECAST（主结果）

**配置**：dispatch-fixed 版本，per-session pool synthesis，deepseek-v4-flash，--no-thinking

**答案目录**：`$PROJECT_ROOT/RECAST/runs/16e6a32/fix400/`（400个样本，每个子目录含 `answer.json` + `trace.json`）

**评分文件（strict，主口径）**：
- `$PROJECT_ROOT/RECAST/runs/rescore_strict/dispatch_fix_T1_strict.json`
- `$PROJECT_ROOT/RECAST/runs/rescore_strict/dispatch_fix_T2_strict.json`

**评分文件（lenient，参考）**：
- `$PROJECT_ROOT/RECAST/runs/rescore_lenient/recast_main_T1_lenient.json`
- `$PROJECT_ROOT/RECAST/runs/rescore_lenient/recast_main_T2_lenient.json`

**Strict 分数**（n=200 T1 (DC) + 200 T2 (IC)）：

| | SR | PR | IPA | **总体** |
|---|---|---|---|---|
| T1 (DC)（直接冲突） | 72.0% | 64.0% | 55.0% | **63.7%** |
| T2 (IC)（间接冲突） | 62.0% | 52.5% | 50.0% | **54.8%** |
| **合并** | — | — | — | **59.3%** |

**Lenient 分数**（参考，`recast_main_T{1,2}_lenient.json`）：

| | SR | PR | IPA | **总体** |
|---|---|---|---|---|
| T1 (DC) | 89.5% | 85.5% | 67.0% | **80.7%** |
| T2 (IC) | 86.0% | 83.0% | 67.5% | **78.8%** |

⚠️ README 中的 T1 (DC) 77.3% / T2 (IC) 72.3% 是旧版本残留数字，与当前 `recast_main_*_lenient.json` 文件不符，请以文件为准。

**复现命令**（从 `$PROJECT_ROOT` 运行）���
```bash
git -C RECAST checkout 16e6a32
python -m RECAST.run_new_mem \
  --run-name fix400 \
  --n-samples 0 \
  --workers 4 \
  --no-thinking \
  --global-temperature 0.3 \
  --embedding-model-path RECAST/models/all-MiniLM-L6-v2 \
  --embedding-device cpu
```

**评分命令**（从 STALE 评测目录运行）：
```bash
cd $PROJECT_ROOT/RECAST/STALE/STALE/Evaluation
python full_eval_performance.py \
  --answers-path $PROJECT_ROOT/RECAST/runs/16e6a32/fix400/answers.json \
  --dataset-path $PROJECT_ROOT/RECAST/STALE/STALE/outputs/STALE_MAIN.json \
  --output-path $PROJECT_ROOT/RECAST/runs/rescore_strict/dispatch_fix_T1_strict.json \
  --model-method dispatch_fix_strict \
  --scorer qwen3.6-plus \
  --type T1
```

---

### 1.2 CupMem（重新评测的 baseline）

**配置**：同 backbone（deepseek-v4-flash，--no-thinking），同 judge（qwen3.6-plus）

**答案目录**：`$PROJECT_ROOT/RECAST/runs/step2b_cupmem_t03/`

**评分文件（strict）**：
- `$PROJECT_ROOT/RECAST/runs/rescore_strict/cupmem_scores_T1_strict.json`
- `$PROJECT_ROOT/RECAST/runs/rescore_strict/cupmem_scores_T2_strict.json`

**评分文件（lenient）**：
- `$PROJECT_ROOT/RECAST/runs/rescore_lenient/cupmem_T1_lenient.json`
- `$PROJECT_ROOT/RECAST/runs/rescore_lenient/cupmem_T2_lenient.json`

**Strict 分数**（n=200 T1 (DC) + 200 T2 (IC)）：

| | SR | PR | IPA | **总体** |
|---|---|---|---|---|
| T1 (DC) | 67.5% | 61.5% | 46.5% | **58.5%** |
| T2 (IC) | 48.5% | 51.0% | 42.5% | **47.3%** |
| **合并** | — | — | — | **52.9%** |

**Lenient 分数**（参考，`cupmem_T{1,2}_lenient.json`，n=200 each）：

| | SR | PR | IPA | **总体** |
|---|---|---|---|---|
| T1 (DC) | 88.0% | 87.0% | 65.0% | **80.0%** |
| T2 (IC) | 74.5% | 76.5% | 65.0% | **72.0%** |

**注**：CupMem 原始论文数字（o4-mini backbone + Gemini judge）仅供参考，不与本表可比。

**复现命令**（从 `$PROJECT_ROOT` 运行；需要 OPENAI_API_KEY 和 OPENAI_BASE_URL 指向 DeepSeek）：
```bash
cd $PROJECT_ROOT
python cup_mem/run_cup_mem_batch.py \
  --run-name step2b_cupmem_t03 \
  --n-samples 0 \
  --workers 4 \
  --global-temperature 0.3 \
  --data-path $PROJECT_ROOT/RECAST/STALE/STALE/outputs/STALE_MAIN.json \
  --embedding-model-path $PROJECT_ROOT/RECAST/models/all-MiniLM-L6-v2 \
  --output-root $PROJECT_ROOT/RECAST/runs
# 评分：
cd $PROJECT_ROOT/RECAST/STALE/STALE/Evaluation
python full_eval_performance.py \
  --answers-path $PROJECT_ROOT/RECAST/runs/step2b_cupmem_t03/answers_T1.json \
  --dataset-path $PROJECT_ROOT/RECAST/STALE/STALE/outputs/STALE_MAIN.json \
  --output-path $PROJECT_ROOT/RECAST/runs/rescore_strict/cupmem_scores_T1_strict.json \
  --model-method cupmem_strict --scorer qwen3.6-plus --type T1
```

---

### 1.3 A-MEM v0.2.6（smoke test，n=5 DC + 5 IC）

原始全量复现（400样本）存在 reader 文本物化缺陷：检索到的记忆 ID 未转换为内容文本传给答案模型，答案近乎全为 "No."，该全量数据已废弃。修复 reader 路径后，对 5 DC + 5 IC 样本做 smoke test，使用 attribution-aware 提示词（见第三节）。

**答案目录**：`$PROJECT_ROOT/RECAST/codex_fairness_audit/runs/amem_full_fair_smoke10_deepseek_20260724/`

**评分文件**：
- `scores_T1_strict.json` / `scores_T1_lenient.json`
- `scores_T2_strict.json` / `scores_T2_lenient.json`

**分数**（n=5 per conflict type；仅供比较性参考，不用于排名）：

| | SR | PR | IPA | **均值** |
|---|---|---|---|---|
| DC (n=5) strict | 40.0% | 20.0% | 20.0% | **26.7%** |
| DC (n=5) lenient | 60.0% | 100.0% | 20.0% | **60.0%** |
| IC (n=5) strict | 0.0% | 0.0% | 40.0% | **13.3%** |
| IC (n=5) lenient | 40.0% | 40.0% | 40.0% | **40.0%** |

**失败来源**：M\_new 文本在 14/15 DC 检索集、10/15 IC 检索集中出现——检索阶段大体有效。主要失败在答案生成阶段：答案模型选择了旧状态、或将 M\_new 视为无关信息，而非以其为根据更新判断。这是 A-MEM 架构本身的推理缺陷，而非检索问题。

**复现命令**（从 `$PROJECT_ROOT/RECAST` 运行）：
```bash
cd $PROJECT_ROOT/RECAST
python codex_fairness_audit/run_fair_attribution_rerun.py \
  --method amem \
  --output-dir codex_fairness_audit/runs/amem_full_fair_smoke10_deepseek_20260724 \
  --t1-uids 89b77229,7ee76c41,1a85388f,f6d12075,d9545076 \
  --t2-uids d806d94c,feef3933,14897e47,c9cc370e,2c711459
# 评分：
cd $PROJECT_ROOT/RECAST/STALE/STALE/Evaluation
python full_eval_performance.py \
  --answers-path $PROJECT_ROOT/RECAST/codex_fairness_audit/runs/amem_full_fair_smoke10_deepseek_20260724/answers_T1.json \
  --dataset-path $PROJECT_ROOT/RECAST/STALE/STALE/outputs/STALE_MAIN.json \
  --output-path $PROJECT_ROOT/RECAST/codex_fairness_audit/runs/amem_full_fair_smoke10_deepseek_20260724/scores_T1_strict.json \
  --model-method amem_smoke_strict --scorer qwen3.6-plus --type T1
```

---

### 1.4 Naive-RAG（嵌入向量 + cosine top-10）

**答案目录**：`$PROJECT_ROOT/naive_rag/runs/naive_rag_full/`（400样本）

**评分文件（strict）**：
- `$PROJECT_ROOT/naive_rag/runs/naive_rag_full/scores_T1_strict.json`
- `$PROJECT_ROOT/naive_rag/runs/naive_rag_full/scores_T2_strict.json`

**Strict ��数**（n=200 T1 (DC) + 200 T2 (IC)）：

| | SR | PR | IPA | **总体** |
|---|---|---|---|---|
| T1 (DC) | 5.0% | 3.5% | 35.5% | **14.7%** |
| T2 (IC) | 2.5% | 0.5% | 21.0% | **8.0%** |
| **合并** | — | — | — | **11.3%** |

**Lenient 分数**（参考，`naive_rag_T{1,2}_lenient.json`，n=200 each）：

| | SR | PR | IPA | **总体** |
|---|---|---|---|---|
| T1 (DC) | 58.0% | 7.0% | 49.5% | **38.2%** |
| T2 (IC) | 48.0% | 3.0% | 32.5% | **27.8%** |

**复现命令**（从 `$PROJECT_ROOT` 运行）：
```bash
cd $PROJECT_ROOT
python naive_rag/run_naive_rag_stale.py \
  --run-name naive_rag_full \
  --workers 8 \
  --embedding-model-path $PROJECT_ROOT/RECAST/models/all-MiniLM-L6-v2
# 评分：
cd $PROJECT_ROOT/RECAST/STALE/STALE/Evaluation
python full_eval_performance.py \
  --answers-path $PROJECT_ROOT/naive_rag/runs/naive_rag_full/answers_T1.json \
  --dataset-path $PROJECT_ROOT/RECAST/STALE/STALE/outputs/STALE_MAIN.json \
  --output-path $PROJECT_ROOT/naive_rag/runs/naive_rag_full/scores_T1_strict.json \
  --model-method naive_rag_strict --scorer qwen3.6-plus --type T1
```

---

### 1.5 mem-0 v0.1.100（pilot，n=5 DC + 5 IC）

原始全量复现（400样本）使用了单批次 ingestion（~175K tokens），API 异常导致记忆构建失败，该全量数据已废弃。修复为逐 batch session ingestion（每 8 个 session 一批）后，对 5 DC + 5 IC 做 pilot，使用 attribution-aware 提示词（见第三节）。

**答案目录**：`$PROJECT_ROOT/RECAST/codex_fairness_audit/runs/mem0_fair_10/`

**评分文件**：
- `scores_T1_strict.json`
- `scores_T2_strict.json`

**分数**（n=5 per conflict type；仅供比较性参考，不用于排名）：

| | SR | PR | IPA | **均值** |
|---|---|---|---|---|
| DC (n=5) strict | 0.0% | 0.0% | 0.0% | **0.0%** |
| IC (n=5) strict | 0.0% | 0.0% | 0.0% | **0.0%** |

**失败来源**：mem-0 将新旧状态存储为并列独立条目，无任何"哪条是当前状态"的标记。答案模型拿到的 memories 中新旧两条并列，无法确定当前状态，答案无法通过 strict grounding 要求。这是架构性状态混淆，换提示词无法解决（attribution-aware 提示词同样全 0%）。

**注**：mem-0 使用基础版（vector store + Qdrant），无图扩展（Mem0^g）。

**复现命令**（从 `$PROJECT_ROOT/RECAST` 运行）：
```bash
cd $PROJECT_ROOT/RECAST
python codex_fairness_audit/run_fair_attribution_rerun.py \
  --method mem0 \
  --output-dir codex_fairness_audit/runs/mem0_fair_10 \
  --t1-uids 89b77229,7ee76c41,1a85388f,f6d12075,d9545076 \
  --t2-uids d806d94c,feef3933,14897e47,c9cc370e,2c711459
# 评分：
cd $PROJECT_ROOT/RECAST/STALE/STALE/Evaluation
python full_eval_performance.py \
  --answers-path $PROJECT_ROOT/RECAST/codex_fairness_audit/runs/mem0_fair_10/answers_T1.json \
  --dataset-path $PROJECT_ROOT/RECAST/STALE/STALE/outputs/STALE_MAIN.json \
  --output-path $PROJECT_ROOT/RECAST/codex_fairness_audit/runs/mem0_fair_10/scores_T1_strict.json \
  --model-method mem0_pilot_strict --scorer qwen3.6-plus --type T1
```

---

### 1.6 重跑方差分析（同 30 UID，3次独立运行）

**目的**：量化 RECAST 在相同配置下多次运行的得分波动幅度。

**UID 集**：15 T1 (DC) + 15 T2 (IC)，与跨 backbone 实验使用相同子集（但含 `a53e0e26` 代替部分跨 backbone UID，共 30 个）。

**Run 1**：从主 dispatch_fix 400 样本结果（`$PROJECT_ROOT/RECAST/runs/rescore_strict/dispatch_fix_T{1,2}_strict.json`）中提取同 UID 子集的得分。

**Run 2 / Run 3**：
- 答案目录：`$PROJECT_ROOT/RECAST/runs/budget_plan_t15_t15/variance_run2_30/`
- 答案目录：`$PROJECT_ROOT/RECAST/runs/budget_plan_t15_t15/variance_run3_30/`
- 评分文件：各目录下 `scores_T{1,2}_strict.json` / `scores_T{1,2}_lenient.json`

**Strict 分数**（n=15 per T type per run，相同 backbone deepseek-v4-flash + temperature=0.3）：

| Run | T1 (DC)-SR | T1 (DC)-PR | T1 (DC)-IPA | **T1 (DC)** | T2 (IC)-SR | T2 (IC)-PR | T2 (IC)-IPA | **T2 (IC)** |
|-----|-------|-------|--------|--------|-------|-------|--------|--------|
| Run 1（dispatch_fix 子集） | 73.3% | 53.3% | 53.3% | **60.0%** | 53.3% | 53.3% | 60.0% | **55.6%** |
| Run 2 | 50.0% | 46.7% | 46.7% | **47.8%** | 53.3% | 50.0% | 46.7% | **50.0%** |
| Run 3 | 60.0% | 53.3% | 53.3% | **55.6%** | 56.7% | 56.7% | 60.0% | **57.8%** |
| **均值** | 61.1% | 51.1% | 51.1% | **54.4%** | 54.4% | 53.3% | 55.6% | **54.4%** |
| **±（range/2）** | ±11.7pp | ±3.3pp | ±3.3pp | **±6.1pp** | ±1.7pp | ±3.3pp | ±6.7pp | **±3.9pp** |

**Lenient 分数**（n=15 per T type per run）：

| Run | T1 (DC) | T2 (IC) |
|-----|---------|---------|
| Run 2 | **76.7%** | **77.8%** |
| Run 3 | **84.4%** | **83.3%** |

**结论**：
- T1 (DC) strict 在 3 次独立运行中跨度为 47.8%–60.0%（±6.1pp），T2 (IC) 为 50.0%–57.8%（±3.9pp）。
- 波动主要集中在 SR 和 IPA 维度；PR 最稳定（±3.3pp）。
- 30 样本子集与全量 200 样本有抽样差异（子集 run1 均值 54.4% vs 全量 59.3%），说明子集不具代表性，方差估计仅为参考。
- 波动来源：deepseek-v4-flash temperature=0.3 非零温度 + judge 随机性，两者叠加。

**复现命令**（从 `$PROJECT_ROOT` 运行）：
```bash
cd $PROJECT_ROOT
python -m RECAST.run_new_mem \
  --run-name variance_run2_30 \
  --uids 1a85388f,b17c5c02,a4b2e2fd,7ee76c41,fdada4cc,eacb64ff,e229c5cd,d9545076,7a7621e2,a53e0e26,d74f7f3e,b35794f3,89b77229,2006d545,f6d12075,feef3933,28daa975,830a2e06,fbe6fd55,60604200,14897e47,06071a3e,d806d94c,2d92d1c2,993152aa,a2a3e641,27a52329,2c711459,c9cc370e,c03f7b53 \
  --workers 2 \
  --no-thinking \
  --global-temperature 0.3 \
  --output-dir RECAST/runs/budget_plan_t15_t15/variance_run2_30 \
  --embedding-model-path RECAST/models/all-MiniLM-L6-v2 \
  --embedding-device cpu
# 评分：
cd $PROJECT_ROOT/RECAST/STALE/STALE/Evaluation
python full_eval_performance.py \
  --answers-path $PROJECT_ROOT/RECAST/runs/budget_plan_t15_t15/variance_run2_30/answers_T1.json \
  --dataset-path $PROJECT_ROOT/RECAST/STALE/STALE/outputs/STALE_MAIN.json \
  --output-path $PROJECT_ROOT/RECAST/runs/budget_plan_t15_t15/variance_run2_30/scores_T1_strict.json \
  --model-method variance_run2_strict --scorer qwen3.6-plus --type T1
```

---

## 二、消融实验（全部使用 strict judge）

所有消融评分文件在 `$PROJECT_ROOT/RECAST/runs/rescore_strict/`。

写阶段消融（完整重跑写阶段，修改指定组件）——复现从 `$PROJECT_ROOT` 运行 `python -m RECAST.run_new_mem`，加下表所列 flag：

| 消融名 | 移除/替换的组件 | 关键 flag | T1 (DC) strict | T2 (IC) strict |
|--------|--------------|-----------|-----------|-----------|
| **Ablation E** | 去掉假说生成（hyp gen） | `--skip-hypothesis-gen` | 67.3% | 44.5% |
| **Ablation D** | LLM 判断改为嵌入相似度 | `--judgment-via-embedding` | 59.0% | 40.3% |
| **Ablation C** | 去掉全局印象更新（impression\_update） | `--no-impression` | 74.0% | 50.5% |

读阶段/池消融（复用主运行的写阶段 trace，仅重跑查询阶段）——答案目录在 `$PROJECT_ROOT/RECAST/runs/{name}/`：

| 消融名 | 移除的组件 | 答案目录 | 评分文件（T1 (DC)/T2 (IC) strict） | T1 (DC) strict | T2 (IC) strict |
|--------|----------|---------|----------------------|-----------|-----------|
| **A-PoolReset** | 跨 session 证据累积（每 session 后清空池） | `runs/a_poolreset/` | `a_poolreset_T1_strict.json` | 62.3% | 50.7% |
| **A-NoPool** | 证据池（立即单次判断，无累积） | `runs/a_nopool/` | `a_nopool_T1_strict.json` | 62.7% | 48.0% |
| **A-NoImp** | 全局印象（读阶段不读 impression） | `runs/a_noimp/` | `a_noimp_T1_strict.json` | 64.2% | 53.2% |
| **Ablation F** | 查询假说扩展（query hypothesis） | 复用主 trace | `ablation_f_T1_strict.json` | 67.7% | 52.3% |
| **A-NaiveAnswer** | 结构化 CoT（改用朴素答案提示词） | 复用主 trace | `a_naive_T1_strict.json` | 53.2% | 40.3% |

**Lenient 分数**（全部来自 `$PROJECT_ROOT/RECAST/runs/rescore_lenient/`）：

⚠️ 注意样本量不一致：部分消融 lenient 文件仅有 30 样本子集（n=90 = 30×3 dims），全量为 n=600（200×3 dims）。

**写阶段消融 lenient**（n=600，200样本）：

| 消融名 | T1 (DC)-SR | T1 (DC)-PR | T1 (DC)-IPA | **T1 (DC)** | T2 (IC)-SR | T2 (IC)-PR | T2 (IC)-IPA | **T2 (IC)** |
|--------|-------|-------|--------|--------|-------|-------|--------|--------|
| Ablation E（无假说生成） | 78.5% | 72.5% | 72.5% | **74.5%** | 62.0% | 50.0% | 58.0% | **56.7%** |
| Ablation D（嵌入代替判断） | 82.0% | 66.5% | 58.0% | **68.8%** | 27.5% | 22.0% | 24.0% | **24.5%** |
| Ablation C（无印象更新） | 91.5% | 86.0% | 74.0% | **83.8%** | 77.0% | 74.0% | 62.0% | **71.0%** |

**读阶段/池消融 lenient**：

| 消融名 | n | T1 (DC)-SR | T1 (DC)-PR | T1 (DC)-IPA | **T1 (DC)** | T2 (IC)-SR | T2 (IC)-PR | T2 (IC)-IPA | **T2 (IC)** |
|--------|---|-------|-------|--------|--------|-------|-------|--------|--------|
| A-PoolReset | 200 T1 (DC) / 30 T2 (IC)¹ | 88.5% | 77.5% | 65.5% | **77.2%** | 86.7% | 83.3% | 66.7% | **78.9%** |
| A-NoPool | 200 each | 86.0% | 75.5% | 64.0% | **75.2%** | 81.0% | 68.5% | 60.5% | **70.0%** |
| A-NoImp | 200 T1 (DC)² / 30 T2 (IC) | 72.0% | 65.5% | 53.5% | **63.7%**² | 96.7% | 76.7% | 60.0% | **77.8%** |
| Ablation F（无查询假说） | 200 each | 90.5% | 84.0% | 70.5% | **81.7%** | 87.0% | 81.5% | 64.5% | **77.7%** |
| A-NaiveAnswer | 30 each¹ | 90.0% | 46.7% | 60.0% | **65.6%** | 86.7% | 43.3% | 66.7% | **65.6%** |

¹ 该消融 lenient 文件仅对应 30 样本子集（n=90），非全量 200。  
² A-NoImp T1 (DC) lenient 整体（63.7%）低于 strict（64.2%），说明该 lenient 文件使用的答案集与 strict 文件来源不同，两者不完全可比。

**关键发现**：
- A-NaiveAnswer T2 (IC) PR 从 52.5%（strict）/ 83.0%（lenient RECAST）跌至 22.5%（strict）/ 43.3%（lenient）：结构化 4 步 CoT 对抗鲁棒性的关键
- Ablation E T2 (IC) strict −10.3pp，lenient −22.1pp：假说生成对间接冲突至关重要
- Ablation D T2 (IC) lenient 仅 24.5%（strict 40.3%）：嵌入相似度判断在 T2 (IC) 上严重退化

---

## 三、公平归因诊断（fair-attribution diagnostic）

**目的**：验证 baseline 的低 strict 分是纯格式归因问题，还是也反映了检索/状态选择失败。

完整数据来自论文附录表（tex 第 1140-1200 行）。所有诊断均使用 strict judge + qwen3.6-plus，同 UID 子集比较。

### 公平归因的实现机制

**脚本**：`$PROJECT_ROOT/RECAST/codex_fairness_audit/run_fair_attribution_rerun.py`

核心思想：**保留各 baseline 自己的检索结果，只替换答案生成提示词**。如果换了提示词分数大幅提升，说明原始分数是格式问题；如果不提升，说明是检索/记忆状态本身不足。

**Step 1：用各 baseline 自己的检索逻辑取回 memories**

每个 baseline 走独立的检索路径：
- `retrieved_by_naive()`：all-MiniLM-L6-v2 嵌入 + cosine top-10 sessions
- `retrieved_by_mem0()`：逐 batch session ingestion（每 8 个 session 一批，修复了全量运行时的单批次缺陷）→ `mem.search(..., limit=10)`
- `retrieved_by_amem()`：重新构建 AgenticMemorySystem → `search_agentic(..., k=10)`，并修复了 reader 的文本物化路径

检索结果是各 baseline **自己的记忆存储**里检索出来的内容，没有人工注入 M_old/M_new 的 ground truth。

**Step 2：用统一的 `ATTRIBUTION_PROMPT` 生成答案（替代各 baseline 自己的 `ANSWER_PROMPT`）**

原始提示词（如 mem-0 的）要求"简洁回答（concisely with just the answer）"，不引导推理链，导致答案是 "No." 类，strict judge 无法判断模型是否真的识别了 M_new。

`ATTRIBUTION_PROMPT` 强制模型做 4 步推理，再输出 JSON：

```
1. State the specific assumption the question makes about the user's current
   situation. Include implicit assumptions.
2. Check that assumption against the retrieved memories and recent context.
   Do not assume the question's premise is true. Prefer the most concrete and
   recent evidence available.
3. If the assumption is unsupported, outdated, or contradicted, open the answer
   by naming that discrepancy.
4. Ground the rest of the answer in evidence you can cite from the retrieved
   memories or recent context.

Output JSON only:
{
  "assumption": "...",
  "evidence_check": "...",
  "answer": "..."
}
```

最终 `answer.json` 里的 `dim_response` 是这三个字段展开后的可读文本：

```
Assumption check: ...
Evidence check: ...
Answer: ...
```

这个结构**刻意对齐了 strict judge 所要求的格式**：先识别前提、再核验 M_new 证据、再给答案。

**为什么 mem-0 fair-attribution 仍然全 0%**

提示词只能解决格式问题。mem-0 的根本问题是**存储了新旧两条状态但不区分哪条是当前状态**：

```
- User used to work at company A
- User now works at company B
```

两条并列，模型的 `evidence_check` 写出来也是含糊的，strict judge 仍然无法通过"显式引用 M_new"要求。这是架构性问题，换提示词无法修复。

---

### 3.1 Naive-RAG 公平归因重评（60样本）

**答案目录**：`$PROJECT_ROOT/RECAST/codex_fairness_audit/runs/naive_rag_fair_60/`（60个答案）

**重评脚本**：`$PROJECT_ROOT/RECAST/codex_fairness_audit/run_fair_attribution_rerun.py`

**复现命令**（从 `$PROJECT_ROOT/RECAST` 运行）：
```bash
cd $PROJECT_ROOT/RECAST
python codex_fairness_audit/run_fair_attribution_rerun.py \
  --method naive_rag \
  --output-dir codex_fairness_audit/runs/naive_rag_fair_60 \
  --t1-uids 89b77229,7ee76c41,1a85388f,f6d12075,d9545076,e229c5cd,eacb64ff,fdada4cc,a4b2e2fd,2006d545,d74f7f3e,b17c5c02,b35794f3,7a7621e2,34d402c0,6ff5a576,e72a2ba5,93a1c511,f7fb891b,79e4cc40,2ba8e3f4,26e99c95,dae22057,eee1a643,e51c1d33,e1703b4d,9867971c,8aeb8778,a6170008,3305ce57 \
  --t2-uids d806d94c,feef3933,14897e47,c9cc370e,2c711459,993152aa,c03f7b53,60604200,06071a3e,2d92d1c2,fbe6fd55,28daa975,27a52329,830a2e06,a2a3e641,da38532d,48707e03,f50107f1,ea1bd523,855155ad,1469bde3,5a4781fe,5ae24023,87ea8043,14ed299f,4ad50bc6,5372c535,d13024ef,c2cc2d39,53d876a2
# 评分：
cd $PROJECT_ROOT/RECAST/STALE/STALE/Evaluation
python full_eval_performance.py \
  --answers-path $PROJECT_ROOT/RECAST/codex_fairness_audit/runs/naive_rag_fair_60/answers_T1.json \
  --dataset-path $PROJECT_ROOT/RECAST/STALE/STALE/outputs/STALE_MAIN.json \
  --output-path $PROJECT_ROOT/RECAST/codex_fairness_audit/runs/naive_rag_fair_60/scores_T1_strict.json \
  --model-method naive_rag_fair --scorer qwen3.6-plus --type T1
```

| 任务 | 条件 | SR | PR | IPA | **均值** |
|------|------|----|----|-----|---------|
| DC | Naive-RAG 原始（同 UID） | 0.0% | 6.7% | 43.3% | **16.7%** |
| DC | Naive-RAG 公平归因重评 | 53.3% | 36.7% | 23.3% | **37.8%** |
| IC | Naive-RAG 原始（同 UID） | 0.0% | 0.0% | 3.3% | **1.1%** |
| IC | Naive-RAG 公平归因重评 | 33.3% | 13.3% | 10.0% | **18.9%** |

**结论**：attribution-aware 提示词显著提升 DC-SR（+53.3pp），说明格式归因敏感性是真实的。但 IC 提升有限（+17.8pp），且 IPA 反而下降（DC：43.3%→23.3%），说明强制引用证据的提示词会改变行为而不只是揭示已有答案。差距仍大，上游检索/状态选择失败是主要原因。

### 3.2 mem-0 公平归因 pilot（10样本）

详见 §1.5。结果与复现命令均在该节。

**结论摘要**：固定 ingestion 后，mem-0 attribution-aware pilot 全维度 strict 0.0%。换提示词无帮助——根本原因是架构性状态混淆（新旧记忆并列无标记），不是格式问题。

### 3.3 A-MEM 修复后 smoke test（10样本）

详见 §1.3。结果与复现命令均在该节。

**结论摘要**：固定 reader 后，DC strict 26.7%、IC strict 13.3%，远低于 RECAST（63.7%/54.8%）。失败主要在答案阶段（检索到 M\_new 但未使用），是架构推理能力差距。

### 3.4 MemGPT smoke test（10样本，provisional）

**答案目录**：`$PROJECT_ROOT/RECAST/codex_fairness_audit/runs/memgpt_deepseek_smoke10_20260724/`

| 任务 | Rubric | SR | PR | IPA | **均值** |
|------|--------|----|----|-----|---------|
| DC | strict | 40.0% | 20.0% | 20.0% | **26.7%** |
| DC | lenient | 40.0% | 20.0% | 20.0% | **26.7%** |
| IC | strict | 0.0% | 0.0% | 20.0% | **6.7%** |
| IC | lenient | 0.0% | 20.0% | 20.0% | **13.3%** |

（仅探索性 smoke test，不用于排名）

**完整报告**：`$PROJECT_ROOT/RECAST/codex_fairness_audit/FAIR_ATTRIBUTION_RERUN_REPORT.md`

---

## 四、Judge 跨模型验证

**目的**：检验 qwen3.6-plus 的判断可靠性

**第二评判模型**：GPT-5.4（直连 OpenAI API）

**样本**：40条回答（20 T1 (DC) + 20 T2 (IC)），从 RECAST 主运行中随机抽取

**结果文件**：`$PROJECT_ROOT/RECAST/runs/judge_validation_real/results_gpt5_4_ergou.json`

**完整报告**：`$PROJECT_ROOT/RECAST/codex_judge_analysis/JUDGE_CROSS_VALIDATION_GPT54_ERGOU_CN.md`

| 维度 | 一致率 | Cohen's κ |
|------|--------|-----------|
| SR (dim1) | 80.0% | 0.375 |
| PR (dim2) | 72.5% | 0.290 |
| IPA (dim3) | 65.0% | 0.271 |
| **整体** | **72.5%** | **0.320** |

**结论**：两模型排名方向大体一致（可支持主要比较结论），但 κ=0.320 偏低，绝对分数尤其 IPA 对 judge 选择敏感（GPT-5.4 在 IPA 上比 Qwen 严格约 15pp）。

**复现命令**（从 `$PROJECT_ROOT/RECAST` 运行；需要 GPT-5.4 接入权限）：
```bash
# Pass 1：导出待标注样本 + 调用 GPT-5.4 作第二评判
cd $PROJECT_ROOT/RECAST
python scripts/validate_judge_real.py
# → 写入 runs/judge_validation_real/results_gpt5_4_ergou.json
# 需要在 .env 中配置 GPT-5.4 的 API key：
# OPENAI_API_KEY=<openai-key>
# OPENAI_BASE_URL=https://api.openai.com/v1
# TARGET_MODEL=gpt-5.4（或对应模型名）
```

---

## 五、跨 backbone 泛化实验

**说明**：在相同 30个样本子集（15 T1 (DC) + 15 T2 (IC)）上，用不同 backbone 运行 RECAST，strict + lenient 双口径评分。

**评分文件（strict）**：
- `$PROJECT_ROOT/RECAST/runs/budget_plan_t15_t15/cross_qwen35plus_30/scores_T1_strict.json`
- `$PROJECT_ROOT/RECAST/runs/budget_plan_t15_t15/cross_qwen35plus_30/scores_T2_strict.json`
- `$PROJECT_ROOT/RECAST/runs/budget_plan_t15_t15/cross_gpt4omini_30/scores_T1_strict.json`
- `$PROJECT_ROOT/RECAST/runs/budget_plan_t15_t15/cross_gpt4omini_30/scores_T2_strict.json`

**评分文件（lenient）**：
- `$PROJECT_ROOT/RECAST/runs/rescore_lenient/cross_qwen_T{1,2}_lenient.json`
- `$PROJECT_ROOT/RECAST/runs/rescore_lenient/cross_gpt4omini_T{1,2}_lenient.json`

**Strict 分数**（n=15 per T type；论文附录引用）：

| Backbone | T1 (DC)-SR | T1 (DC)-PR | T1 (DC)-IPA | **T1 (DC)** | T2 (IC)-SR | T2 (IC)-PR | T2 (IC)-IPA | **T2 (IC)** |
|----------|-------|-------|--------|--------|-------|-------|--------|--------|
| Qwen-3.5+（15样本子集） | 56.7% | 66.7% | 63.3% | **62.2%** | 60.0% | 66.7% | 60.0% | **62.2%** |
| GPT-4o-mini（15样本子集） | 80.0% | 40.0% | 53.3% | **57.8%** | 53.3% | 20.0% | 20.0% | **31.1%** |

**Lenient 分数**（n=15 per T type）：

| Backbone | 评分文件前缀 | T1 (DC)-SR | T1 (DC)-PR | T1 (DC)-IPA | **T1 (DC)** | T2 (IC)-SR | T2 (IC)-PR | T2 (IC)-IPA | **T2 (IC)** |
|----------|------------|-------|-------|--------|--------|-------|-------|--------|--------|
| DeepSeek-V4-Flash（主，全量 200×参考） | `recast_main_T{1,2}_lenient.json` | 89.5% | 85.5% | 67.0% | **80.7%** | 86.0% | 83.0% | 67.5% | **78.8%** |
| Qwen-3.5+（15样本子集） | `cross_qwen_T{1,2}_lenient.json` | 90.0% | 86.7% | 76.7% | **84.4%** | 90.0% | 93.3% | 76.7% | **86.7%** |
| GPT-4o-mini（15样本子集） | `cross_gpt4omini_T{1,2}_lenient.json` | 100.0% | 46.7% | 66.7% | **71.1%** | 93.3% | 46.7% | 40.0% | **60.0%** |

**注**：
- 两个 backbone 均有 strict 和 lenient 评分文件（路径见上）
- GPT-4o-mini IC strict 仅 31.1%，显著低于 DC strict（57.8%），IC-PR 崩塌至 20.0%（论文附录分析：probe resistance 在 IC 场景下更依赖 backbone 推理能力）
- Haiku backbone 因 API 余额耗尽，仅完成 1/15，不纳入
- DeepSeek 主 backbone 行使用全量 200 样本的 `recast_main` lenient 文件（不是同子集），仅供量级参考

**复现命令**（从 `$PROJECT_ROOT` 运行；以 Qwen-3.5+ 为例，GPT-4o-mini 同理替换 TARGET_MODEL 和 OPENAI_BASE_URL）：
```bash
cd $PROJECT_ROOT
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export TARGET_MODEL=qwen/qwen3.5-plus-20260420
export OPENAI_API_KEY=<openrouter-key>

python -m RECAST.run_new_mem \
  --run-name cross_qwen35plus_30 \
  --uids 89b77229,7ee76c41,1a85388f,f6d12075,d9545076,e229c5cd,eacb64ff,fdada4cc,a4b2e2fd,2006d545,d74f7f3e,b17c5c02,b35794f3,7a7621e2,34d402c0,d806d94c,feef3933,14897e47,c9cc370e,2c711459,993152aa,c03f7b53,60604200,06071a3e,2d92d1c2,fbe6fd55,28daa975,27a52329,830a2e06,a2a3e641 \
  --workers 2 \
  --no-thinking \
  --global-temperature 0.3 \
  --embedding-model-path RECAST/models/all-MiniLM-L6-v2 \
  --embedding-device cpu
# 评分（lenient）：
cd $PROJECT_ROOT/RECAST/STALE/STALE/Evaluation
python full_eval_performance.py \
  --answers-path $PROJECT_ROOT/RECAST/runs/cross_qwen35plus_30/answers_T1.json \
  --dataset-path $PROJECT_ROOT/RECAST/STALE/STALE/outputs/STALE_MAIN.json \
  --output-path $PROJECT_ROOT/RECAST/runs/rescore_lenient/cross_qwen_T1_lenient.json \
  --model-method cross_qwen_lenient --scorer qwen3.6-plus --type T1 --lenient
```

---

## 六、外部 benchmark 泛化验证

### 6.1 LongMemEval-S（knowledge-update 子集）

**说明**：在 LongMemEval-S 的 78个 knowledge-update 样本上评测 RECAST，使用 LongMemEval 官方 judge prompt

**配置**：同主实验（dispatch-fixed，deepseek-v4-flash，--no-thinking）

**答案目录**：`$PROJECT_ROOT/RECAST/runs/563fe9e/lme_ku_full/`（78个样本）

**评分文件**：

| 文件 | Judge | 结果 | 创建时间 |
|------|-------|------|---------|
| `scores_lme.json` | qwen3.6-plus | 60/78 = **76.9%** | 2026-06-30 |
| `scores_lme_openrouter_gpt4o_2024_08_06.json` | GPT-4o-2024-08-06 | 57/78 = **73.1%** | 2026-07-08 |

两个数字均有对应文件，来源不同：
- **76.9%**：与主实验 STALE judge 保持一致（qwen3.6-plus），论文内部方法论统一
- **73.1%**：用 GPT-4o-2024-08-06 官方评分脚本重评，更贴近 LongMemEval benchmark 的原始评分标准，是后来的交叉验证

**注**：LongMemEval judge 独立于 STALE strict/lenient rubric，只检查答案是否包含正确更新后状态（不要求显式引用链）。此实验无同 backbone 的 baseline 对比。

**复现命令**（从 `$PROJECT_ROOT` 运行）：
```bash
# Step 1：准备数据（将 LongMemEval 格式转为 RECAST 兼容格式）
cd $PROJECT_ROOT/RECAST
python scripts/prepare_longmemeval.py \
  --input $PROJECT_ROOT/LongMemEval/data/longmemeval_s.json \
  --output /tmp/longmemeval_ku_recast.json \
  --types knowledge-update

# Step 2：用 dispatch-fixed 版本跑 78 个样本（同主实验 commit 16e6a32）
cd $PROJECT_ROOT
python -m RECAST.run_new_mem \
  --run-name lme_ku_full \
  --data-path /tmp/longmemeval_ku_recast.json \
  --n-samples 0 \
  --workers 4 \
  --no-thinking \
  --global-temperature 0.3 \
  --embedding-model-path RECAST/models/all-MiniLM-L6-v2 \
  --embedding-device cpu

# Step 3：用 LongMemEval judge 评分
cd $PROJECT_ROOT/RECAST
python scripts/score_longmemeval.py \
  --answers $PROJECT_ROOT/RECAST/runs/563fe9e/lme_ku_full/answers_LME.json \
  --data /tmp/longmemeval_ku_recast.json \
  --output $PROJECT_ROOT/RECAST/runs/563fe9e/lme_ku_full/scores_lme.json \
  --scorer qwen3.6-plus \
  --workers 8
```

---

## 七、论文纳入范围说明

以下表格记录各项实验当前在论文中的纳入状态：已移入正文的结果可以作为论文证据；其余结果暂不作为当前论文的主要证据，但保留用于审计、复核或后续补充。

| 实验 | 状态 | 原因 |
|------|------|------|
| Variance run2/run3（30个 UID，3次重跑） | 已移入正文 §1.6 | — |
| Haiku 跨 backbone | 失败 | API 余额耗尽，仅完成 1/15 |
| MemGPT/Letta smoke（10样本） | 已移入正文 §3.4 | 保留 "provisional smoke test, not for ranking" 定性 |
| A-MEM 公平归因重评 | 已移入正文 §1.3 / §3.3 | smoke test 10样本，结果可用 |
| mem-0 公平归因（10样本） | 已移入正文 §1.5 / §3.2 | pilot 10样本，全 0%，结论架构性状态混淆 |
| GPT-5-mini judge 交叉验证 | 作废 | 实际为旧缓存（非真实调用），κ≈−0.011 不可信 |

---

## 附：评分工具调用约定

所有 strict 评分统一使用：

```bash
cd $PROJECT_ROOT/RECAST/STALE/STALE/Evaluation
python full_eval_performance.py \
  --answers-path <answers.json 路径> \
  --dataset-path $PROJECT_ROOT/RECAST/STALE/STALE/outputs/STALE_MAIN.json \
  --output-path <输出 scores.json 路径> \
  --model-method <任意标签> \
  --scorer qwen3.6-plus \
  --type T1   # 或 T2
```

Lenient 评分加 `--lenient` flag（如有）或使用 lenient judge prompt 版本。

---

## 附：关键数字速查

### Strict（主口径，n=200 per type）

| 系统 | T1 (DC)-SR | T1 (DC)-PR | T1 (DC)-IPA | **T1 (DC)** | T2 (IC)-SR | T2 (IC)-PR | T2 (IC)-IPA | **T2 (IC)** | **合并** |
|------|-------|-------|--------|--------|-------|-------|--------|--------|---------|
| **RECAST（dispatch-fixed）** | **72.0%** | **64.0%** | **55.0%** | **63.7%** | **62.0%** | **52.5%** | **50.0%** | **54.8%** | **59.3%** |
| CupMem（重评） | 67.5% | 61.5% | 46.5% | 58.5% | 48.5% | 51.0% | 42.5% | 47.3% | 52.9% |
| A-MEM v0.2.6¹ | 40.0% | 20.0% | 20.0% | 26.7% | 0.0% | 0.0% | 40.0% | 13.3% | — |
| Naive-RAG | 5.0% | 3.5% | 35.5% | 14.7% | 2.5% | 0.5% | 21.0% | 8.0% | 11.3% |
| mem-0 v0.1.100¹ | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| — | — | — | — | — | — | — | — | — | — |
| A-NaiveAnswer（消融） | 78.5% | 34.5% | 46.5% | 53.2% | 57.0% | 22.5% | 41.5% | 40.3% | 46.8% |
| Ablation F（无查询假说） | 75.0% | 68.0% | 60.0% | 67.7% | 57.0% | 53.0% | 47.0% | 52.3% | 60.0% |
| A-NoImp（无印象） | 75.5% | 64.0% | 53.0% | 64.2% | 59.5% | 50.0% | 50.0% | 53.2% | 58.7% |
| A-PoolReset（per-session） | 75.0% | 60.0% | 52.0% | 62.3% | 59.0% | 47.0% | 46.0% | 50.7% | 56.5% |
| A-NoPool（无证据池） | 73.5% | 60.0% | 54.5% | 62.7% | 56.0% | 42.0% | 46.0% | 48.0% | 55.4% |
| Ablation E（无假说生成） | 74.0% | 63.0% | 65.0% | 67.3% | 49.5% | 36.5% | 47.5% | 44.5% | 55.9% |
| Ablation D（嵌入代替判断） | 71.5% | 56.0% | 49.5% | 59.0% | 48.5% | 30.5% | 42.0% | 40.3% | 49.7% |
| Ablation C（无印象更新） | 79.5% | 76.0% | 66.5% | 74.0% | 53.5% | 50.0% | 48.0% | 50.5% | 62.3% |

### Lenient（参考，n=200 per type 除另注）

| 系统 | T1 (DC)-SR | T1 (DC)-PR | T1 (DC)-IPA | **T1 (DC)** | T2 (IC)-SR | T2 (IC)-PR | T2 (IC)-IPA | **T2 (IC)** | 备注 |
|------|-------|-------|--------|--------|-------|-------|--------|--------|------|
| **RECAST（dispatch-fixed）** | **89.5%** | **85.5%** | **67.0%** | **80.7%** | **86.0%** | **83.0%** | **67.5%** | **78.8%** | |
| CupMem（重评） | 88.0% | 87.0% | 65.0% | 80.0% | 74.5% | 76.5% | 65.0% | 72.0% | |
| A-MEM v0.2.6¹ | 60.0% | 100.0% | 20.0% | 60.0% | 40.0% | 40.0% | 40.0% | 40.0% | n=5/type smoke test |
| Naive-RAG | 58.0% | 7.0% | 49.5% | 38.2% | 48.0% | 3.0% | 32.5% | 27.8% | |
| mem-0 v0.1.100¹ | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | n=5/type pilot |
| — | — | — | — | — | — | — | — | — | |
| A-NaiveAnswer | 90.0% | 46.7% | 60.0% | 65.6% | 86.7% | 43.3% | 66.7% | 65.6% | n=30/type |
| Ablation F | 90.5% | 84.0% | 70.5% | 81.7% | 87.0% | 81.5% | 64.5% | 77.7% | n=200 |
| A-NoImp | 72.0% | 65.5% | 53.5% | 63.7% | 96.7% | 76.7% | 60.0% | 77.8% | T1 (DC) n=200，T2 (IC) n=30 ⚠️ |
| A-PoolReset | 88.5% | 77.5% | 65.5% | 77.2% | 86.7% | 83.3% | 66.7% | 78.9% | T1 (DC) n=200，T2 (IC) n=30 |
| A-NoPool | 86.0% | 75.5% | 64.0% | 75.2% | 81.0% | 68.5% | 60.5% | 70.0% | n=200 |
| Ablation E | 78.5% | 72.5% | 72.5% | 74.5% | 62.0% | 50.0% | 58.0% | 56.7% | n=200 |
| Ablation D | 82.0% | 66.5% | 58.0% | 68.8% | 27.5% | 22.0% | 24.0% | 24.5% | n=200 ⚠️T2极低 |
| Ablation C | 91.5% | 86.0% | 74.0% | 83.8% | 77.0% | 74.0% | 62.0% | 71.0% | n=200 |
