# 查询逻辑改进头脑风暴

## 一、现有查询逻辑的问题

### 写入阶段 vs 查询阶段的设计不对称

**写入阶段**（每个 session 后台运行）：
```
新陈述
  → impact_hypothesis：结合 global_impression + preference_anchors，
                       发散生成 6-12 条"此前必须是什么"的假设
  → abductive_judgment：对每条假设，遍历候选记忆，推断冲突
  → pool_synthesis：证据池累积，多 session 汇聚信号
```
这是**主动发散检索**：系统主动想"新信息可能影响哪些旧信念"，而不是等人问。

**查询阶段**（用户提问时实时执行）：
```
查询文本
  → search_by_embedding(query_text, top_k=8)  ← 三次独立 embedding 搜索
  → premise_check(retrieved memories)
  → answer_generation
```
这是**被动单路检索**：只做一次 embedding 相似度搜索，完全依赖查询文本与记忆文本的词向量距离。没有发散、没有印象辅助、没有多跳推断。

---

## 二、具体失败例子

### 例 1：语义鸿沟（RC-I，样本 0312）

**查询**：`"I'm about to buy a custom strap that only fits what I'm using right now"`

**正确的相关记忆**：`"spent Saturday at repair kiosk, watch sent in for screen repair"` — 意味着当前手表可能已被替换，购买专属表带是危险的。

**实际检索结果**：吉他课、调酒工具、航班、晋升。

**为什么？** 查询里的"what I'm using right now"是代词，没有任何词汇信号。embedding 只能匹配字面上的"using"，而"kiosk repair"、"watch sent in"的 embedding 完全不在附近。

**写入阶段会怎么做？** 如果用写入阶段的逻辑：
- global_impression 里有"owns a smartwatch, worn for 3 years"
- 把"recently sent watch to repair kiosk"与 global_impression 交叉 → 生成假设"用户正在使用的设备可能已变更"
- 这个假设会命中购买前需要确认设备状态的场景

---

### 例 2：单路检索遗漏间接连接（RC-H，样本 0240）

**查询**：`"Does the user still meet the eligibility criteria because they're a U.S. citizen?"`

**正确的相关记忆**：`"at the bank, asked to sign W-8BEN again"` — W-8BEN 是非美国税务居民签的表格，强烈暗示公民身份存疑。

**实际检索**：`"is a U.S. citizen"` (active) — 只找到了 M_old，没有找到 M_new。

**为什么？** "W-8BEN" 与"U.S. citizen eligibility"的 embedding 距离远。系统只检索到了与查询正向匹配的记忆，没有检索到与查询**反向矛盾**的记忆。

**写入阶段会怎么做？** 如果用写入逻辑对"signed W-8BEN"这条新陈述：
- impact_hypothesis 会生成"用户此前可能被视为美国税务居民"
- 这个假设直接命中"is a U.S. citizen" → abductive_judgment 有机会执行

---

### 例 3：印象辅助缺失（RC-A，样本 0211）

**查询**：`"Does the user still live in Chicago?"`

**global_impression**（已含最新信息）：`"[STATUS] Currently living in a small San Francisco apartment"` — 注意这是过时的，但即使是正确的 impression，查询也没有利用它。

**实际检索**：stale 的 Chicago 记忆被正确找到，但 active_facts 为空（多伦多/沙漠相关记忆未检索到，因为"Chicago"和"desert saguaro"的 embedding 不相近）。

**如果查询时使用 global_impression 扩展**：impression 里的位置关键词（即使是"San Francisco"）会扩展检索范围，可能找到"moved, lease was up"这类迁移信号。

---

### 例 4：Preference anchors 在查询时完全不参与（RC-F，样本 0059）

**查询 dim2**：`"Since people know the user keeps things confidential and share more, how can the user encourage members to open up even more?"`

**正确行为**：应该检测到这个前提（"people trust user with confidential info"）已被挑战，拒绝前提。

**实际行为**：premise_safe=True，直接回答如何加强信任关系。

**为什么？** premise_check 接收到的记忆里，m_00047（confidentiality reputation）确实在 active_facts 里——问题不是检索，是 **premise_check 没有意识到 m_00117（uncertain: feels excluded at social events）与之矛盾**。写入阶段的 preference_anchors 在查询时完全没有使用。

---

## 三、改进方案、攻击与结论

---

### 方案 Q1：查询展开（Query Expansion via Global Impression）

**核心思路**：在 embedding 搜索前，用 LLM 结合 `query_text + global_impression` 生成 2-3 条扩展查询，对每条都做 embedding 搜索，取并集去重后交给 premise_check。

```
query_text + global_impression
  → LLM 生成 3 条扩展查询（覆盖不同侧面）
  → 3 × embedding 搜索
  → 合并 top-k，去重
  → premise_check（更丰富的候选集）
```

**解决的问题**：RC-I（embedding 鸿沟）、RC-A（impression 里有更新信息可辅助搜索）

**攻击 Q1**：

- **Q1-a**：新增 1 次 LLM 调用（展开查询），增加延迟 ~1-2s。
  → 可以用模板而非 LLM 做简单展开（提取 impression 里的实体名词，直接追加到查询）；或仅在 premise_safe=True 时才做展开（二次确认机制）。

- **Q1-b**：Global impression 本身可能已经过时（RC-A 的问题），用过时的 impression 扩展查询可能强化错误。
  → **真实风险**：展开查询时应只使用 impression 里的人物/工具/习惯类内容，不使用地址/状态（这些最容易过时）。

- **Q1-c**：三路搜索的结果并集可能导致记忆候选集过大，premise_check 负担增加。
  → 限制并集大小（如最多 12 条），而非三路 top-8 的 24 条。

**修正版 Q1**：只在 `premise_safe=True` 之后、作为"二次确认"时才进行展开检索。正常流程不变，只有当 premise_check 说"安全"时，用 impression 里的相关实体做一次补充搜索，看能否找到矛盾证据。这样不增加正常 case 的延迟。

**结论**：修正版 Q1 可行，实施成本低。

---

### 方案 Q2：查询时传入 Preference Anchors

**核心思路**：和 impact_hypothesis 一样，在 premise_check 的 prompt 里也加入 `preference_anchors`（即所有 lasting_preference + biographical 类记忆），让 premise_check 主动检查"查询前提是否依赖某个 anchor，而该 anchor 现在已被挑战"。

**解决的问题**：RC-F（0059 的 confidentiality 案例部分解决——但 m_00047 是 current_state，不在 anchors，仍需 RC-F 代码修复配合）

**攻击 Q2**：

- **Q2-a**：anchors 列表通常有 10-20 条，prompt 变长，可能超过上下文或降低注意力。
  → 只传与查询语义相近（embedding cosine > 阈值）的 anchors，而非全部。

- **Q2-b**：anchors 只覆盖 `lasting_preference` + `biographical`，不覆盖社交声誉类 current_state（RC-F 的核心）。Q2 本身不能修复 0059。
  → 需要配合 RC-F 的代码修复：扩展 `get_preference_anchors()` 后才能生效。

- **Q2-c**：如果 anchor 本身是正确的（用户确实有这个偏好），premise_check 会错误地对所有与 anchor 略有关系的查询都说"不安全"。
  → 只触发当：anchor 对应的 active 记忆有 uncertain 状态或被弱化，而不是所有 anchor 都无条件参与推断。

**结论**：Q2 在技术上可行，但需要配合 RC-F 代码修复才能发挥作用；独立实施只解决 lasting_preference 类的子集。

---

### 方案 Q3：逆向影响假设（Inverse Impact Hypothesis at Query Time）

**核心思路**：在 premise_check 之前，运行一个"逆向 impact_hypothesis"步骤：给定查询的前提，问"什么样的记忆会让这个前提失效？"然后专门检索那类记忆。

```
query_text
  → extract_premise_assumptions（提取前提中的隐含假设）
  → inverse_hypothesis（"什么样的新事实会推翻这个假设"）
  → targeted embedding search（针对性检索）
  → 合并结果，传给 premise_check
```

**解决的问题**：RC-H（间接链式推断）、RC-C（premise_check 知道 stale 但未关联前提）

**攻击 Q3**：

- **Q3-a**：这等于在查询时运行写入阶段的部分逻辑，每次查询多 2 次 LLM 调用，延迟增加 3-5s。
  → **真实成本问题**：对于 dim2 对抗性查询来说延迟可接受（重要性高），但对于 dim1/dim3 普通查询则显得过重。

- **Q3-b**：提取"前提中的隐含假设"本身就是一个困难的 NLP 任务，LLM 未必能做对。
  → 可以只提取明确的名词性前提（"user lives in Chicago" → 假设"仍住在芝加哥"），不做深度语义分析。

- **Q3-c**：逆向假设的结果可能指向空集（没有任何记忆满足"会推翻前提"的条件），此时多余的计算完全浪费。
  → 仍然是"二次确认"机制：只在初始 premise_safe=True 时才触发 Q3，不在每次查询都执行。

**修正版 Q3**：将 Q1 和 Q3 合并为"第二轮确认流程"：
```
正常流程完成，premise_safe=True
  → [第二轮] 结合 global_impression + premise_assumptions
             生成反向搜索，扩展候选集
  → 重新运行 premise_check（补充候选）
  → 如果第二轮返回 safe=False，覆盖第一轮结论
```
这只在"第一轮认为安全"时才触发，避免误判；同时不影响正常情况下的延迟。

**结论**：修正版 Q3 是目前最有价值的改进方向，专门针对"知道 stale 但不拒绝前提"（RC-C）和"间接链式冲突"（RC-H）。实施成本中等（增加一个可选的第二轮 premise_check）。

---

### 方案 Q4：记忆状态感知的 top-k 排名调整

**核心思路**：当前 top-k 纯粹按 embedding 相似度排名。改为：对 uncertain 状态的记忆加权提升（即使语义稍远也优先返回），因为 uncertain 记忆恰恰是最可能涉及冲突的。

**解决的问题**：RC-C 的 embedding 版本——当正确的 uncertain 记忆语义距离稍远时，不会进入 top-8。

**攻击 Q4**：

- **Q4-a**：uncertain 记忆可能与当前查询完全无关（e.g., 查询关于时区，但 uncertain 里有关于饮食的记忆），一律提升会引入噪声。
  → 只提升与查询 embedding 余弦相似度 > 某阈值的 uncertain 记忆，而非无条件提升。

- **Q4-b**：如果 uncertain 记忆很多（大量弱化信号积累），会把整个 top-k 填满无关 uncertain 记忆。
  → 限制提升数量：最多从 uncertain 池里额外提取 3 条（补充，而非替换 active top-k）。

**结论**：Q4 是最轻量的改进，改动在检索层而非 LLM 层，延迟零增加，值得实施。

---

## 四、最终推荐

按照实施成本从低到高、收益从高到低排序：

| 方案 | 实施成本 | 解决 RC | 推荐理由 |
|------|---------|--------|---------|
| **Q4**：uncertain 记忆加权 | 极低（改 search_by_embedding 排名） | RC-C 部分 | 零延迟，直接让弱化信号进入候选集 |
| **Q2**：查询时传入 Preference Anchors | 低（改 premise_check prompt） | RC-F | 配合代码修复后覆盖偏好类冲突 |
| **修正版 Q1+Q3**：二次确认流程 | 中（增加可选第二轮） | RC-C、RC-H、RC-I | 只在 safe=True 时触发，不影响正常延迟；专攻假阴性 |

**最不建议**：在每次查询中都运行完整的 Q3（逆向 impact_hypothesis），延迟和成本不可接受。

---

## 五、写入 vs 查询的根本性设计张力

写入阶段：**离线、批量、可慢**（用户没有在等待）→ 可以做发散推断、多轮假设验证。

查询阶段：**在线、实时、必须快**（用户在等回答）→ 只能做轻量检索 + 有限推理。

当前的 RECAST 把所有"重推断"放在了写入阶段，查询阶段完全依赖写入阶段的结果。这个设计是合理的，但它有一个根本假设：**写入阶段已经把所有需要的冲突都检测出来并标记好了**。只要写入阶段有漏网之鱼（RC-G、RC-E、RC-H 的各种提取/推断失败），查询阶段就完全无力弥补。

改进方向有两类：
1. **修复写入**（治本）：修 RC-G/RC-E/RC-H，让写入阶段更少漏网
2. **加强查询**（治标）：用 Q1-Q4 在查询时做有限的二次确认，补救写入遗漏

两者不互斥，但修写入的 ROI 更高：一次写入修复，所有后续查询都受益；修查询逻辑只能在查询时做概率补救。
