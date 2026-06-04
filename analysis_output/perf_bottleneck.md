---
name: project-perf-bottleneck
description: Throughput bottleneck analysis and two optimization paths to increase json/sec from ~14 to 50-100
metadata: 
  node_type: memory
  type: project
  originSessionId: b76e2bb7-148c-45bf-9a96-3989df70c9f1
---

## 现象

服务器上 80 workers 并行跑 80 个样本，.cache json 写入速度约 14/sec，单次 80 样本完整 eval 需 ~90 分钟。

## 根本原因

每个样本内 50 个 session 必须**串行**处理（写入有状态，session N+1 依赖 session N 的 memory 状态）。80 workers 只是让 80 个样本同时跑，但每个样本内部是排队的。瓶颈 = API 响应延迟（8-15s/call）× 每样本约 290 次串行调用。

## 各阶段精确调用分布（基于 7094eb6/t1t2_v1，60 samples，完整 write+query）

每样本平均 **879 次 LLM 调用**，均值 38 分钟（当时 API 均延迟 2.6s/call；现在 8-15s/call → ~90 分钟）

| Phase | 调用/样本 | Token/样本 | 占比 | 可否换小模型 |
|-------|---------|-----------|------|------------|
| pool_synthesis | 244 | 178k | **28%** | ⚠️ 逻辑较简单，可试 |
| abductive_judgment | 173 | 466k | 20% | ❌ 需要推理 |
| impact_hypothesis | 173 | 286k | 20% | ❌ 需要推理 |
| hypothetical_filter | 205 | 330k | 23% | ✅ 三分类，可换小模型 |
| statement_extraction | 50 | 72k | 6% | ⚠️ 结构化输出 |
| impression_update | 27 | 37k | 3% | ✅ 可换 |
| premise_check | 3 | 7k | 0.3% | — |
| answer_generation | 4 | 4k | 0.4% | — |

**关键修正：filter 实际只占 23%，不是之前估计的 75%。**
之前高估是因为 filter 在 prescan 已并行化，对总耗时贡献远小于调用占比。
真正的串行瓶颈是 pool_synthesis（28%）+ abductive（20%）+ impact（20%）= **68%**，且这三项都需要强模型。

pool_synthesis 是最大单项，且逻辑相对简单（对已有证据池做汇总判断），**有机会用快速中型模型替换**。

## 优化路径

### 路径 A：hypothetical_filter 换快模型（ROI 修正为约 23%）

**Why:** filter 三分类逻辑简单，但实际调用占比 23%，并非原估计的 75%（prescan 已并行化）。
**How:** `new_writer.py` 里 `_is_factual()` 调用单独配一个 fast LLM client，用 qwen-turbo、SiliconFlow Qwen3-8B 或本地 Qwen3-1.7B（A100 本地推理零延迟）；其余 phase 保持 deepseek-v4-flash。
**预期收益:** 整体约节省 23% 时间，90 分钟 → ~70 分钟。单独价值有限，建议同时做路径 C。
**How to apply:** 实施前在 10 个样本上 AB 对比 filter 准确率。国内推荐 SiliconFlow（硅基流动）服务器快，或本地 A100 跑小模型。

### 路径 A+：pool_synthesis 换快模型（额外 28%）

**Why:** pool_synthesis 是最大单项（28%），逻辑是对已有证据列表做汇总判断，相对机械，不需要最强模型。
**How:** 与 filter 类似，给 `_synthesize_pool()` 配独立的 fast client。
**预期收益:** 与路径 A 合并可节省约 50% 时间，90 分钟 → ~45 分钟。

### 路径 B：prescan max_workers 从 16 提到 50（低 ROI）

**Why:** 代码里 `prescan_session` 的并发上限是 `min(len(indices), 16)`，来自早期 WSL 32GB 的保守设置。服务器 251GB 完全不需要此限制。
**How:** `core/sample_runner.py` 内 prescan 的 `max_workers` 改为 `min(len(indices), 50)`。
上限取 50（= session 数）的原因：prescan 是对所有 session 并行跑 extraction，workers 超过 session 数后多余线程永远空转，没有意义。但需注意：80 samples × 50 prescan workers = 4000 并发线程同时打 API，限流会比 16 时更早触发，实际收益可能低于理论值。可先保守取 `min(len(indices), 32)` 测试，再视 API 响应情况调整。
**预期收益:** 仅影响 prescan 阶段（占总时间约 20%），整体加速约 10-15%，单独实施意义不大。
**How to apply:** 配合路径 A 一起做。16 是 WSL 时代遗留值，服务器上没有理由保留。

## 当前状态（截至 2026-06-04）

- 已运行：baseline（fb52cd3）和 P1-P8（fed4de2），各 80 samples，约 90 分钟/次
- 路径 A/B 尚未实施，待下一轮迭代
