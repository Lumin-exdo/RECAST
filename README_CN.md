# RECAST

**基于溯因推断的冲突感知状态追踪系统**

[English](README.md)

RECAST 是一个面向 LLM 智能体的无模式记忆系统，能够自动检测已存储信念何时变得过时。当用户分享新信息时，RECAST 向后推理，识别哪些现有记忆与之矛盾——无需预定义模式，也无需用户手动管理记忆。

## 核心思路

智能体记忆系统面临一个根本性问题：用户的生活会改变，但存储的记忆不会。一月份写入的记忆（"住在旧金山"）到三月份可能已经悄然失效（"刚在多伦多签了年租约"）。大多数系统要么忽视这一问题，要么依赖用户手动更新。

RECAST 通过**溯因冲突检测**解决这一问题：

1. 当新陈述到来时，RECAST 生成*影响假设*——在这条新信息出现之前，什么情况必须是真实的？
2. 每条假设通过**溯因判断**与已存储记忆进行核验，推断新证据是否削弱或否定了现有信念。
3. 证据在每条记忆的**证据池**中累积。当池置信度超过阈值时，记忆被标记为 *stale*（明确过时）或 *uncertain*（被削弱但未确认）。
4. 在查询时，**前提检查**识别问题是否基于错误或过时的假设；答案生成时结合压缩后的**用户画像摘要**消解歧义。

## 处理流程

```
statement_extraction      ← 从对话中提取用户相关事实
      ↓
hypothetical_filter       ← 过滤假设性内容，保留事实性陈述
      ↓
impact_hypothesis         ← 生成"此前必须是什么"的假设
      ↓
abductive_judgment        ← 针对已存储记忆验证假设
      ↓
pool_synthesis            ← 累积证据，决定 stale/uncertain/active
      ↓
impression_update         ← 维护压缩的全局用户画像摘要
      ↓
  [ 查询阶段 ]
      ↓
premise_check             ← 标记查询中的过时前提
      ↓
answer_generation         ← 基于当前记忆状态和画像摘要生成回答
```

## 评测

在 [STALE](https://github.com/STALEproj/STALE) 基准上进行评测。STALE 专门测试记忆系统能否正确处理用户画像中的时态冲突。

- **T1**：直接冲突（新事实直接与存储记忆矛盾）
- **T2**：间接链式冲突（新事实通过多跳推理隐含矛盾）
- **dim1**：召回——系统是否知道旧记忆已过时？
- **dim2**：对抗探测——系统能否拒绝基于过时记忆的问题假设？
- **dim3**：行动合规——系统是否基于当前状态而非过时状态给出建议？

## 快速开始

```bash
git clone https://github.com/Lumin-exdo/RECAST.git
cd RECAST
python -m venv venv && source venv/bin/activate
pip install openai sentence-transformers numpy

cp .env.example .env
# 在 .env 中填写 TARGET_MODEL、OPENAI_API_KEY、OPENAI_BASE_URL
```

STALE 数据集和嵌入模型需单独下载（参见 STALE 仓库）。

## 运行

```bash
# 在 RECAST/ 的父目录下执行
python -m RECAST.run_new_mem \
  --data-path /path/to/STALE_MAIN.json \
  --embedding-model-path /path/to/all-MiniLM-L6-v2 \
  --run-name my_run \
  --uids uid1,uid2,uid3 \
  --workers 10 \
  --no-thinking
```

结果写入 `RECAST/runs/{commit}/{run-name}/{sample_idx}/answer.json`。

## 注意事项

- `--no-thinking` 对 DeepSeek 模型必须开启（v4-flash/v4-pro 默认启用思考模式）
- `--workers` 应根据可用内存调整（每个并行样本约占 8GB）
- 多机器结果可通过 `--commit-override` 强制指定运行路径后合并
