# RECAST 失败分析与修复方案

基于 T1/T2 抽样评测结果（T1×30，T2×8 已完成），系统性梳理失败模式，逐一提出修复方案、对其进行攻击，并给出最终结论。

---

## 失败归类

### RC-A：Impression 残留旧地址（0211、0239）

**现象**：`impression_update` 写入 `[STATUS] Currently living in X` 后，后续搬迁只追加不覆盖。answer_gen 通过 `profile_summary` 读到旧地址并写进回答。

**示例**：0211 的 M_new 描述沙漠环境（仙人掌），全局 impression 仍写着"Currently living in a small San Francisco apartment"，answer_gen 输出"你搬到了旧金山"。

---

### RC-B：answer_gen 用 profile_summary 覆盖了 premise_check 的 correction（0211）

**现象**：premise_check correction 明确说"移到干旱气候"，但 answer_gen 当前的 disambiguation rule 把 profile_summary 当 tiebreaker，于是旧地址胜出。

---

### RC-C：premise_check 不拒绝定性状态对抗前提（0226 dim2）

**现象**：用户已从"昏暗工位"移到"强烈日晒窗边"，dim2 对抗前提假设仍在昏暗区域。premise_check 返回 safe，answer_gen 给出采光改造建议，未拒绝前提。

---

### RC-D：premise_check 的 correction 归因错误（0223）

**现象**：实际原因是癫痫药导致驾照暂停，但 correction 说"你搬家了"。answer_gen 用错误的 correction 生成错误建议。

---

### RC-E：extraction 丢弃行为细节，只保留情绪结果（0059）

**现象**："人们在谈话涉及隐私时保持距离、不再拉我进私聊" → 提取成 "feels excluded at neighborhood events"，丢失"为何被排除"的关键语义，导致 impact_hypothesis 无法指向 confidentiality 记忆。

---

### RC-F：preference_anchors 漏掉 current_state 社交声誉类记忆（0059）

**现象**：m_00047（"People trust me with confidential info"）是 `current_state` 类，不进 `get_preference_anchors()`（只取 `lasting_preference` + `biographical`）。Impact_hypothesis 没有看到它，abductive_judgment 从未对它生成判断。

---

## 逐一修复、攻击与结论

---

### RC-A 修复

**方案 A1**：在 `GLOBAL_IMPRESSION_UPDATE` 提示词中加入：更新 `[STATUS]` 中的地理位置时必须**整体重写** STATUS 段，不可追加。凡有新居住地 active 确认，旧的必须删掉。

**攻击 A1**：
- **A1-a**：新位置是 uncertain 的（"我可能要搬去…"），错误覆盖后 impression 含 uncertain 信息。
  → 反驳：加条件"仅当新位置已确定（active 状态）才替换"。
- **A1-b**：用户在两城市之间往来（异地工作），正常双地址会被错误认为是搬迁而删掉一个。
  → 反驳：若两地址均 active 则保留；仅旧地址变 stale 时触发替换。
- **A1-c**：impression 是自由文本，LLM 识别不出哪句话对应哪条 stale 记忆。
  → **真实风险**，需要在提示词里明确：每次更新时重写整个 `[STATUS]` 段，而非在末尾追加。

**结论**：A1 可行，需配合"整体重写 [STATUS]"而非局部追加的指令，并限定只有 active 确认地址才触发替换。

---

### RC-B 修复

**方案 B1**：在 `ANSWER_GENERATION` 提示词中改规则：当 `correction` 非空时，correction 所涉及的维度（位置/角色/状态）**优先于** profile_summary，而非 tiebreaker。

**攻击 B1**：
- **B1-a**：correction 描述新状态（"已移到多伦多"），profile_summary 有更完整的职业信息（"在多伦多做软件工程师"）。若 correction 全面压制 profile_summary，可能丢失有用补充。
  → 反驳：只压制同一维度（位置由 correction 决定），profile_summary 的职业等其他维度不受影响。
- **B1-b**：correction 本身可能是错的（RC-D 的情况），此时优先 correction 固化错误。
  → **真实风险**：B1 与 RC-D 有依赖关系，需先修好 RC-D，correction 质量提升后再开启 B1。
- **B1-c**：提示词里难以清晰界定"correction 所涉及的维度"。
  → 可用 correction 文本语义引导：correction 提到地点，则 profile_summary 的地点维度不被使用。

**结论**：B1 依赖 RC-D 先修好，独立实施有放大 correction 错误的风险。暂缓，先修 A1 和 D1。

---

### RC-C 修复

**方案 C1**：在 `PREMISE_CHECK` 中增加一条规则：检查前提中的定性描述（昏暗/明亮、冷/热、安静/嘈杂）是否与 uncertain/active 记忆中相反的定性描述冲突。

**攻击 C1**：
- **C1-a**：用户"今天开了暖气"→ 与"住在寒冷地区"冲突吗？不一定，可能是正常季节性行为。C1 会产生大量假阳性。
  → 反驳：只在前提的定性状态与存储的定性状态**正好相反且跨越多个 session**时才标 unsafe。
- **C1-b**："昏暗"和"强烈日晒"的 embedding 距离未必近，检索可能根本拿不到相反记忆。
  → **真实风险**：premise_check 依赖 embedding 检索，语义相反的词未必是近邻。

**更深根因**：0226 的问题在于 premise_check 缺少多跳推理：
- 新事实（坐到窗边）→ 隐含（光线充足）→ 矛盾旧前提（通常昏暗）
- 这是 1-2 跳推断，现有 premise_check 提示词有"multi-hop assumption tracing"指令但未覆盖**定性状态反转**。

**方案 C2**（更根本）：在 PREMISE_CHECK 的 multi-hop 指令中增加"定性状态反转"例子：
- 前提描述某种环境质量（昏暗、嘈杂、寒冷）
- 若 active/uncertain 记忆中存在与此相反的环境事实，则标记 premise unsafe

**攻击 C2**：LLM 要判断"昏暗"和"阳光直射"是矛盾的，这需要常识推理——实际上 LLM 完全可以做到，不是问题。

**结论**：C2 在现有 premise_check 框架内添加定性状态反转的例子即可，改动量小，泛化好。

---

### RC-D 修复

**方案 D1**：`PREMISE_CHECK` 的 correction 字段改写规则：correction 必须引用**具体的 active 记忆内容**作为依据，而非自行推断叙事。格式：
> "根据记忆 [X]，实际情况是 Y，因此原前提已失效。"

**攻击 D1**：
- **D1-a**：若没有 active 记忆能解释为何旧前提失效（只有 stale 标记但无替代事实），correction 写什么？
  → 此时 correction 应说"这条信息已过时，但目前没有明确的替代信息"，而非编造原因。
- **D1-b**：active 记忆可能很多，premise_check 可能引用了错误的那条。
  → 只引用与 outdated_facts 直接相关的 active 记忆，其他不引用。

**结论**：D1 可行，风险低，且能降低 RC-B 的依赖风险（correction 更准确，B1 就更安全）。

---

### RC-E 修复

**方案 E1**：在 `STATEMENT_EXTRACTION` 中增加规则：当用户描述**他人对用户的行为**时，提取他人的具体行为，而非用户的情绪反应。

新增示例（加入提示词）：
- "大家在谈话变得私人时就疏远我" → 提取 "social group avoids sharing personal conversations with user"（而非 "feels excluded"）
- "老板今天对我很冷淡" → 提取 "supervisor treated user coldly"（而非 "user felt hurt"）

**攻击 E1**：
- **E1-a**："我感觉被孤立了"——根本没有行为描述，只有情绪。
  → E1 不强制行为描述必须存在；无行为描述时按原逻辑提取情绪/状态。
- **E1-b**：行为描述比情绪描述更长，可能降低 embedding 检索精度。
  → 实际上"people avoid personal conversations"与"confidentiality reputation"在语义空间更近，比"feels excluded"更容易命中相关记忆。
- **E1-c**：用户描述的行为可能是一次性事件，提取进 memory 会误导。
  → 提取时 `is_definite` 标 false（不确定），filter 阶段会处理。

**结论**：E1 是对的，风险低，泛化好。

---

### RC-F 修复

**方案 F1（提示词层）**：在 `IMPACT_HYPOTHESIS` 的 Step 2 中增加 Part C：
> Part C — 社交角色/信任关系检查（MANDATORY）：profile 里有没有描述用户在某个群体中的角色、声誉、信任度的内容？新陈述是否通过 1-2 跳推断威胁到这个角色？

**攻击 F1**：
- **F1-a**：若 global_impression 和 preference_anchors 都没有这条社交角色信息（0059 的情况），Part C 拿不到任何输入。
  → F1 治标不治本，需要配合 F2。

**方案 F2（代码层）**：扩展 `get_preference_anchors()` 的语义：除 `lasting_preference` + `biographical` 外，用 **embedding 相似度**筛选与"社交角色/信任/声誉"主题最近的 top-3 `current_state` 记忆加入 anchors。

**攻击 F2**：
- **F2-a**：关键词过滤容易出错（"People trust me" vs "I trust people"）。
  → 已改为 embedding 相似度筛选，不依赖关键词。
- **F2-b**：anchors 列表变长，LLM 可能不认真处理每条。
  → 在 IMPACT_HYPOTHESIS 中专门标注这些是"高风险交叉检查项"。

**结论**：F1 + F2 配合，F2 用 embedding 相似度而非关键词匹配，代码改动适中。

---

## 优先级总结

| 优先级 | RC | 修复方案 | 影响面 | 实施成本 |
|--------|-----|----------|--------|----------|
| 🔴 最高 | RC-A | impression_update 整体重写 [STATUS]，不追加 | 所有多次搬迁样本 | 低（提示词 1-2 句） |
| 🔴 最高 | RC-D | premise_check correction 引用 active 记忆，不编造叙事 | 所有 correction 归因错误 | 低（提示词规则） |
| 🟠 高 | RC-E | extraction 提取行为而非情绪 | 所有第三方行为场景 | 低（加 2 条例子） |
| 🟠 高 | RC-C | premise_check 增加定性状态反转例子 | dim2 定性对抗前提 | 低（加 1 条例子） |
| 🟡 中 | RC-F | anchors 用 embedding 补充社交声誉 current_state | 社交角色冲突类 | 中（改代码） |
| 🟡 中 | RC-B | answer_gen correction 优先于 profile_summary | 等 RC-D 修好后评估 | 低 |

