# Session 2026-06-04: answer_generation 分析与修复

---

## User

根据代码非常细致地讲现在的answer_generation是怎么做的，是什么逻辑，然后阅读analysis_output/failure_analysis_126.md，就answer_generation环节分析是在内部的哪里出错的，并提出切实解决方案

## Assistant

先读取 answer_generation 的相关代码和失败分析文档。

[读取 query/new_engine.py 和 prompt_lib/new_templates.py]

[读取 analysis_output/failure_analysis_126.md]

---

## 一、answer_generation 的完整逻辑（代码级）

### 入口：`answer_query()`（new_engine.py:116）

调用顺序是严格串行的：

```
_retrieve_for_query()
    ↓ active/uncertain/stale 各 top-8 embedding 搜索
get_global_impression()
    ↓ profile_summary（全局印象摘要）
_check_premise()
    ↓ premise_result { premise_safe, correction, usable_active_facts, outdated_facts }
_generate_answer()
    ↓ answer_result { answer }
```

最终组合成 `answer`、`verdict`、`retrieved` 三段返回。

---

### 第一步：检索（new_engine.py:24-46）

对同一查询文本做三次独立的 embedding 搜索，每次 top-8，按 `status_filter` 分流：
- `active`：已确认的当前事实
- `uncertain`：被削弱但未完全失效的记忆
- `stale`：已确认过时的记忆

这三个集合互斥，由 store 层的 status 字段决定归属。**关键：只有上游 abductive_judgment + pool_synthesis 把一条记忆标 stale，它才会出现在 stale 集合里；否则无论多旧、多矛盾，它都在 active 里。**

---

### 第二步：premise_check（new_engine.py:48-71）

把三组记忆格式化成文本（stale 附带 `stale_reason`），填入 `PREMISE_CHECK_PROMPT`（new_templates.py:306）。

prompt 要求 LLM 返回：
- `premise_safe` — 问题的隐含前提是否仍然成立
- `correction` — 如果不安全，应告知用户什么
- `usable_active_facts` — active 记忆中直接可用于回答的部分
- `outdated_facts` — 问题可能正在依赖但已经失效的记忆内容

---

### 第三步：`_generate_answer()` 的组装逻辑（new_engine.py:73-114）

这里有一个**关键的分支逻辑**，也是问题的核心所在：

```python
usable_active = premise_result.get("usable_active_facts", [])
if usable_active:
    active_facts_text = "\n".join(f"- {f}" for f in usable_active)
else:
    active_facts_text = "\n".join(f"- {item.content}" for item in active_items) or "(none)"
```

- **路径 A**：premise_check 返回了 `usable_active_facts` → 用那些经过筛选的事实
- **路径 B**（fallback）：premise_check 没返回任何 usable_active_facts → 把**所有 active_items 原封不动**地塞进去

类似的逻辑也用于 stale_facts_text：有 `outdated_facts` 就用它，否则用原始 stale_items。

然后把 7 个变量（`query_text`、`active_facts`、`uncertain_facts`、`stale_facts`、`premise_safe`、`correction`、`profile_summary`）填入 `ANSWER_GENERATION_PROMPT`（new_templates.py:398），一次 LLM 调用，输出 `{ "answer": "..." }`。

---

### 第四步：prompt 规则的行为设计（new_templates.py:422-436）

```
如果 premise_safe=False：以 correction 开头，然后基于已知事实给出有用回答
如果 premise_safe=True：直接用 active_facts 回答
优先级：active > uncertain > stale
模糊消解：新 session 的 active 记忆 > 旧 session；profile_summary 作 tiebreaker
```

---

## 二、answer_generation 在哪里出错——126 例分析中的 22 例

失败分析第五章专门描述了「correction 正确但 answer 仍回退旧状态」的 22 例。它们在 answer_generation 内部有四个具体的失败点：

---

### 失败点 A：`active_facts_text` fallback 把矛盾旧记忆暴露给模型（案例 5.1、5.3、5.4）

这是代码级 bug，在 new_engine.py:84-88。

当 `premise_safe=False` 时，premise_check 判定问题前提已失效。但如果它没返回 `usable_active_facts`（这在 premise_check 的 LLM 输出中是可能的），代码会 fallback 到 **把所有 active_items 全部传入**。这些 active_items 中包含大量支持旧状态的记忆——它们之所以还在 active 里，正是因为上游 abductive_judgment 没有把它们标 stale。

结果：模型在 `active_facts`（标注为"已确认当前事实"）和 `stale_facts` 里同时看到了旧状态的支持，correction 是一句话，而旧状态的语义质量远高于 correction 的权重。

**案例 5.3**（d806d94c）：correction 说"助学贷款已减免"，但 active_items 里仍有 `monthly student loan payment $410`（从未被标 stale），模型直接用这条 active 记忆建议"增加每月还款额"——correction 只阻止了前提确认，没有阻止 answer_gen 主动引用矛盾 active 记忆。

---

### 失败点 B：correction 在 prompt 结构中地位过低（案例 5.2）

查看 new_templates.py:398-436，prompt 的内容顺序是：

```
1. query_text                   ← 大段
2. profile_summary              ← 全局印象（可能几百字，仍含旧状态）
3. active_facts                 ← 最多 8 条，语义量最大
4. uncertain_facts              ← 最多 8 条
5. stale_facts                  ← 最多 8 条
6. Premise assessment（两行）
   - premise_safe=False
   - correction: [一句话]
```

correction 被压在最后，与三段共 24 条记忆竞争注意力。在 Transformer attention 机制下，靠后的短字段对最终生成的影响力天然弱于靠前的大段内容。

**案例 5.2**（ebae7623）：correction 说"PT 已放行，可以全速跑 5K"，但模型在生成具体建议时激活了"膝盖康复期"的健康保守先验，给出"先测试 1-2 英里"的建议——correction 没有渗透到具体参数生成层。

---

### 失败点 C：correction 影响叙述但不影响具体参数（案例 5.4）

**案例 5.4**（55106a99）：correction 明确说"朋友圈扩展到约 12 人"，answer_gen 在开头的叙述里接受了这个修正，但在生成具体时间表时从记忆池拉取了 `inner circle: 3 people` 这条旧记忆，最终时间表写的是"每周联系 2-3 人"。

这是一个**描述层 vs 参数层分离**的问题：prompt 规则的 "START the answer with the correction" 被模型理解为"在开头提及"，而非"以 correction 提供的事实作为后续所有具体参数的依据"。active 记忆里的旧数字在建议生成阶段被当作具体数值使用。

---

### 失败点 D：hedging + 实质违背模式（跨案例普遍）

**案例 5.1**（ad8b0a1f）：correction 说"用户现在独居，独立安排行程"，answer_gen 开头一句"可能过时了"，但整套建议（Sunday sync、shared calendar、与配偶共享 timeline）完整照搬了 M_old 的配偶协调逻辑。

这是 prompt 规则 "START the answer with the correction. Then give a helpful response based on what we do know" 的设计缺陷——"what we do know" 被模型解读为"所有 active_facts"，而 active 记忆里有大量多 session 积累的旧状态记忆，语义权重远超一句 correction。

---

## 三、切实解决方案

### 方案 1（代码，最高优先）：`premise_safe=False` 时禁止 fallback 到全量 active_items

**位置**：new_engine.py:84-88

当 `premise_safe=False` 且 `usable_active_facts` 为空时，不暴露未过滤的 active_items，而是输出一个明确的占位符，让模型知道"前提不安全、当前可用事实已在 correction 里"。

```python
premise_safe = bool(premise_result.get("premise_safe", True))
usable_active = premise_result.get("usable_active_facts", [])

if usable_active:
    active_facts_text = "\n".join(...)
elif premise_safe:
    active_facts_text = "\n".join(f"- {item.content}" for item in active_items) or "(none)"
else:
    active_facts_text = "(premise unsafe — see correction for current state; no unambiguously usable active facts identified)"
```

### 方案 2（prompt，最高优先）：`premise_safe=False` 时把 correction 提升为首要上下文

当 `premise_safe=False` 时，构建一个 `correction_header` 块，在传入 prompt 之前置顶：

```python
if not premise_safe and correction:
    correction_header = (
        "⚠️ CURRENT REALITY — THIS OVERRIDES CONFLICTING MEMORIES BELOW:\n"
        f"{correction}\n\n"
        "Any active memory that contradicts the above is outdated for the purpose of this response. "
        "Build ALL recommendations on this corrected state, not on conflicting active memories."
    )
else:
    correction_header = ""
```

### 方案 3（prompt 规则，中优先）：防止"承认 + 实质违背"模式

```
- If premise_safe=False: START the answer with the correction. Then give a helpful response
  where EVERY specific recommendation is consistent with the corrected state.
  Do NOT suggest actions, plans, numbers, or behaviors that only make sense under the outdated
  state. If active memories contain values (counts, amounts, names) that conflict with the
  correction, use the correction's values — not the memory values.
  "Acknowledging the correction in the first sentence" is not sufficient — the correction must
  govern every concrete recommendation in the response.
```

### 方案 4（prompt 规则，中优先）：resolved/cleared/discharged 的语义约束

```
- When correction states that a constraint, impediment, or condition has been resolved,
  cleared, or discharged, accept this as complete resolution. Do not recommend actions
  that presuppose the constraint still partially exists unless the correction explicitly
  says "partial."
```

### 方案汇总与实施顺序

| 优先级 | 方案 | 类型 | 解决的失败点 | 预计影响案例数 |
|---|---|---|---|---|
| **P0** | 方案 1：`premise_safe=False` 禁 fallback | 代码 | A（矛盾 active 记忆暴露） | ~8–12 |
| **P0** | 方案 2：correction 置顶 | 代码+prompt | B（correction 地位过低） | ~15–18 |
| **P1** | 方案 3：规则强化 all-or-nothing | prompt | C+D（参数层/实质违背） | ~10–14 |
| **P2** | 方案 4：clearance 语义约束 | prompt | B（保守健康偏向） | ~3–5 |

---

## User

你把失败的例子，从trace里面提取出来，answer_generation这个环节的输入和输出。然后你改这个环节，改完提交，并把这些输入送入这个模块（不跑整个流程），看它们的输出是什么，手动判断是否改对了

## Assistant

先找 trace 文件在哪里，看一下结构。

[找到 trace 文件在 /mnt/laq/RECAST/runs/fb52cd3/baseline_s137/]

[提取 Chapter 5 四个案例的 answer_generation 完整输入输出]

[提取四个案例的 premise_check 输出，确认 usable_active_facts 和 outdated_facts]

**提取结果：**

**Case ad8b0a1f / dim2_query（配偶协调）**
- premise_safe: False
- correction: "The belief that you always need to coordinate with your spouse before finalizing your schedule is likely outdated..."
- usable_active_facts: 8 条（通勤、周四午餐、园艺等，均与配偶无关）
- 原 answer: 开头一句 "might be a bit outdated"，随即给出完整 Sunday spouse sync / shared calendar with spouse 方案

**Case 55106a99 / dim3_query（朋友圈）**
- premise_safe: False
- correction: "social schedule may be temporarily disrupted by Tokyo trip, prior weekly routine no longer stable"
- usable_active_facts: 3 条（即将出行、Nikko 日游、mindfulness）
- outdated_facts 包含了 "three people every week (stale)" 和 "about a dozen close friends (stale)"——M_new 被错误标 stale
- 原 answer: 推荐 "2-3 close friends"

**Case ebae7623 / dim3_query（PT 放行）**
- premise_safe: False
- correction: "We're no longer certain that your regular running routine is active — check if you're cleared to run now"
- usable_active_facts: [] （空）
- profile_summary 里明确写着 "Knee cleared by PT"
- 原 answer: "just ease back into it. Try a shorter, easy-paced run first (maybe a mile or two)"

**Case d806d94c / dim3_query（助学贷款减免）**
- premise_safe: False
- correction: "Your $4,200 take-home pay memory is outdated because you now contribute 4% to a 401(k)"
- active_facts 里仍有 "$38,000 student loans, minimum $410/month"（从未被标 stale）
- 原 answer: "bumping to $500 is a solid plan... $38K balance with rates up to 6.2%..."

---

**实施改动：**

**new_templates.py**：更新 `ANSWER_GENERATION_PROMPT`
1. 开头增加 `{correction_header}` 占位符
2. 强化 `premise_safe=False` 的规则，从一行变为五条子规则：
   - 前提视为确定为 FALSE，非"可能过时"
   - 每条具体建议必须符合修正后的状态
   - 若查询嵌入旧前提，重定向到修正后现实
   - correction 中的数字/数量覆盖 active 记忆中冲突值
   - clearance/discharge/resolution = 完全解决，不加残余保守

**new_engine.py**：更新 `_generate_answer()`
1. `premise_safe` 和 `correction` 提前到 `active_facts_text` 之前计算
2. 当 `premise_safe=False` 且 `usable_active_facts` 为空时，不 fallback 到全量 active_items，改为占位符
3. 当 `premise_safe=False` 且 correction 非空时，构建 `correction_header` 块
4. 将 `{correction_header}` 纳入 prompt 填充

**Git commit：** d841655
```
fix: answer_generation — correction header + active_facts guard + stronger rules
```

---

**测试脚本 analysis_output/test_answer_gen_fix.py** — 从 trace 中重建四个 case 的输入，用新 prompt 直接调 LLM，对比输出。

---

**测试结果：**

**Case 1：ad8b0a1f / dim2_query（配偶协调）**

原 answer：
> "might be a bit outdated — though it's still good practice. Given that you commute differently... here's a detailed weekly planning system... Sunday sync sessions together, sharing timelines with your spouse..."

新 answer：
> "the idea that you always need to coordinate with your spouse before locking in your schedule is likely outdated now. Your weeks are busier — gardening every weekend, fitness classes, commute (bus M/W/F, walk T/Th), marketing work... So instead of a system built around mandatory spousal sync, here's a practical weekly planning approach that fits your current life: [lock in fixed anchors: gardening, Thursday lunch, commute, sister's birthday...]"

**判定：PASS ✓** — 彻底去掉了配偶协调逻辑，用 active 记忆里的真实锚点重建计划。

---

**Case 2：55106a99 / dim3_query（朋友圈规模）**

原 answer：
> "Weeknights after 6:30 could be good for quick check-ins with 2–3 close friends..."

新 answer：
> "your social schedule is temporarily disrupted by a 10-day trip to Tokyo... Instead of building a fixed weekly schedule, let's focus on a flexible plan... Identify 3–4 key people you want to stay in close touch with..."

**判定：MARGINAL ～** — "3-4 人"比原来的"2-3 人"略好，但离 M_new 的"12 人朋友圈"仍远。根因是上游 abductive_judgment 把 M_new "dozen close friends" 也错误标为 stale，answer_gen 拿不到这条信息。

---

**Case 3：ebae7623 / dim3_query（PT 放行）**

原 answer：
> "your running routine was interrupted by knee issues, and you only recently got cleared... just ease back into it. Try a shorter, easy-paced run first (maybe a mile or two) to see how the knee feels..."

新 answer：
> "your regular running routine was interrupted... But good news — your profile says your knee has been cleared by your PT, so you're good to run again. So yes, including a run in your morning routine tomorrow makes sense... Just ease back in since it's been a while..."

**判定：PARTIAL ✓/～** — 明确说出"cleared by PT, you're good to run again"；原来的"1-2 英里测膝盖"没了；但仍有"ease back in since it's been a while"（指出行打断了跑步习惯，而非膝盖）。correction 本身措辞弱（"check if you're cleared"），限制了 answer_gen 能做到的程度。

---

**Case 4：d806d94c / dim3_query（助学贷款减免）**

原 answer：
> "bumping to $500 is a solid plan... $38K balance with rates up to 6.2%, paying more aggressively will save you interest..."

新 answer：
> "your take-home is lower than $4,200... Also, your $350 is below the minimum $410... that could cause issues..."

**判定：UNCHANGED（上游 bug）** — 贷款减免从未被标 stale，active_facts 里贷款信息还在。premise_check 的 correction 只提 take-home pay，不知道贷款已消失。answer_gen 拿到的信息就是错的，本层无法修复。

---

## User

你认为这个环节完全解决了吗，还是有哪些未解决的？

## Assistant

没有完全解决。逐层分析：

### 已解决的

**核心模式**：correction 正确 → answer 仍回退旧状态（Case 1 完整修复，Case 3 大部分）。

机制上：correction 置顶 + redirect 规则共同阻断了"开头口头免责、后续建议照旧"的路径。

---

### 未解决的三类问题

**1. correction 本身措辞弱 → 修复不完整（Case 3 残留）**

Case 3 的 correction 是 `"check if you're cleared to run now"`，而 profile_summary 明确写着 `"Knee cleared by PT"`。新规则说"correction 或 profile_summary 描述了 clearance 就视为完全放行"，但模型仍加了 `"ease back in since it's been a while"`。

原因：correction_header 把这句弱措辞 `"check if you're cleared"` 置顶放大了，反而让模型更加保守。**correction 质量的瓶颈在 premise_check，不在 answer_gen。** 只要 premise_check 产出弱 correction，answer_gen 再强的规则也只能部分弥补。

---

**2. correction 方向本身错误时，correction_header 会放大错误（Chapter 4 共 6 例）**

这是我的修复引入的**新风险**。

失败分析 Chapter 4 里：
- `8aeb8778`：premise_check 把 M_new 的状态（"需他人核实"）标注为 outdated，correction 写"verification condition no longer applies"
- `0af76ce2`：M_new 的"外出充电"被标为 stale，correction 说"外出充电是过去习惯"
- `4bb62dbf`：M_new 的批量备餐习惯被标为 stale，correction 说"不再受严格周日惯例约束"

这三个 case 的 premise_check 把新旧方向完全搞反了。旧代码里 correction 只是 prompt 末尾一个小字段，模型有时会忽略它从而产出正确建议；**新代码把这个错误的 correction 以 `⚠️ CURRENT REALITY` 的形式置顶，并加了 "redirect your answer to fit the corrected state" 的强制指令**，这会让这 6 个 case 的答案比原来更错。

---

**3. active_facts guard 过于粗暴（潜在误伤）**

当 `premise_safe=False` 且 `usable_active_facts` 为空时，我把 active_facts 整段替换成占位符。

问题：premise_check 可能因 A 话题触发了 `premise_safe=False`（比如地理位置相关的前提），但 active_items 里存的是 B 话题的记忆（饮食偏好、日常习惯），这些 B 话题记忆和失效前提毫不相关，完全可以用来回答问题。Guard 把它们一并清空，answer_gen 就只剩 correction + stale_facts + profile_summary，回答质量会退化。

---

**4. 上游失败导致的 answer_gen 失败，本层无法修复（Case 2、4）**

- **Case 2**（朋友圈）：M_new 的 `dozen close friends` 被 abductive_judgment 错误标 stale → answer_gen 永远拿不到这条信息
- **Case 4**（贷款减免）：减免事件从未被检测 → $38K 贷款留在 active → premise_check 的 correction 只提 take-home pay → answer_gen 不知道贷款已消失

这两类是结构性上游失败。answer_gen 的输入已经是错的，prompt 改多好也无济于事。

---

### 一句话结论

**这次修复对"correction 正确、answer 回退旧状态"这个特定模式有效（22 例里的约 8-12 例）；但对 Chapter 4 那 6 例"correction 方向错误"的案例引入了回退风险，对上游完全漏检的案例无效。**

最高优先级的下一步应该是 premise_check 的方向判断问题（Chapter 4），而不是继续在 answer_gen 层加补丁——因为 answer_gen 的输出质量上限被 premise_check 的输出质量卡死了。

---

## User

3. 是你改错的吗，还是什么意思？是本环节还是上游环节？

## Assistant

是我改出来的权衡风险，不是上游问题。

### 具体说

改之前：
- `premise_safe=False` + `usable_active_facts` 为空 → fallback 用全部 `active_items`
- **风险 A**：active_items 里含有支持旧状态的记忆，它们会和 correction 竞争注意力

改之后：
- 同样条件 → 替换成占位符 `"(no unambiguously current facts — see correction above)"`
- **风险 B**：如果 active_items 里存在和 premise 问题无关、但对回答当前查询有用的记忆，它们也被一并清空

两者都是 `_generate_answer()` 内部的行为，都是 **answer_generation 本层**的逻辑，不涉及上游。

### 但根子取决于上游

风险 B 能不能实际发生，取决于 premise_check 有没有把那些"无关但有用"的 active 记忆放进 `usable_active_facts`。

premise_check 的 prompt 说：`"active memory contents directly relevant to answering"`——理论上应该把所有跟回答有关的 active 记忆都收进来，而不只是跟 premise 冲突相关的。如果 premise_check 在 `premise_safe=False` 时确实做到了这一点，那 `usable_active_facts` 就不会为空，我的 guard 就不会触发，风险 B 就不存在。

也就是说：**风险 B 是我改出来的，但它能否成真是 premise_check 说了算**。

### 实际情况看

4 个测试 case 里 guard 只对 Case 3（ebae7623）触发，那次 active_items 是清洁提醒、烹饪课之类的内容，和"能不能跑步"查询完全无关，清空它们不影响回答质量。

所以目前不是一个已观测到的问题，只是一个值得注意的潜在风险——如果 premise_check 在某个 case 里少填了 `usable_active_facts`，可能会损失有用的 active 记忆。
