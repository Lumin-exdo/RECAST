# RECAST 60 样本完整 Trace 分析

基于 T1×30 + T2×30 共 60 个 STALE 样本的 trace 级别根因分析。
评测维度：dim1（召回）、dim2（对抗探测）、dim3（行动合规）。

---

## 总体结果估算（语义分析，待 qwen3.6-plus 评分验证）

| 类型 | dim1 | dim2 | dim3 | 综合 |
|------|------|------|------|------|
| T1 (30) | ~97% | ~87% | ~97% | **~93%** |
| T2 (30) | ~73% | ~80% | ~87% | **~80%** |
| 全体 | ~85% | ~83% | ~92% | **~87%** |

T2 显著难于 T1，主要原因是 T2 冲突需要 1-2 跳推断，而 extraction、impact_hypothesis、retrieval 的任一失败都会断链。

---

## 根因目录

### RC-A：Impression 残留旧地址/状态

**机制**：`impression_update` 将用户位置写入 `[STATUS]` 后，后续搬迁只追加不覆盖。Answer_gen 通过 `profile_summary` 读到旧地址并写进回答。

**Trace 证据**：
- **0211 (T2) dim1**：
  - `m_00031: "lives in San Francisco"` → status=**stale**，被正确标记
  - `global_impression [STATUS]` 仍写着 "Currently living in a small San Francisco apartment"
  - Answer_gen 读 profile_summary → 输出"你搬到了旧金山"（实为沙漠）
- **0239 (T2) dim1/dim2**：同样模式，impression 残留湾区地址，M_new 为其他位置变化
- **0013 (T1) dim2**：stale_reason 写入"likely the US"（abductive inference），premise_check 照搬进 correction，answer_gen 回答说"the US"（实为加拿大多伦多）

**根本原因**：impression_update 追加地址而非重写 `[STATUS]`；即使相关记忆已标 stale，impression 里的对应句子不会被同步清除。

---

### RC-B：Answer_gen 用 profile_summary 覆盖 correction（依赖 RC-A 先修）

**机制**：当前 disambiguation rule 把 profile_summary 作为 tiebreaker，但 profile_summary 含有旧地址时会压过 premise_check 的正确 correction。

**Trace 证据**：
- **0211 dim1**：premise_check correction 明确说"已移到干旱气候"，但 profile_summary 说"SF apartment"，answer_gen 输出了 SF。

---

### RC-C：Premise_check 识别 stale 事实但未标记 premise_safe=False

**机制**：premise_check 正确将相关记忆放入 `outdated_facts`，但 LLM 判断 `premise_safe=True`，未将"查询依赖此 stale 事实"连接起来。

**Trace 证据**：
- **0266 (T1) dim2**：
  - `outdated_facts`: `["m_00027: watching gaming streams on Twitch during lunch breaks (stale — user now plays games instead)"]`
  - `premise_safe: True`（错误）
  - dim2 查询前提正是"用户在午休时看 gaming streams"，与 stale 事实直接矛盾，但 LLM 未连接
- **0226 (T2) dim2**：
  - 用户已从"昏暗工位"移到"窗边强光"
  - dim2 前提假设仍在昏暗区，premise_check 返回 safe，answer_gen 给采光建议

**根本原因**：premise_check 提示词的多跳推断指令未覆盖"定性状态反转"场景；LLM 知道旧事实 stale，但不会自动检查查询是否依赖它。

---

### RC-D：Premise_check correction 归因错误（叙事替代事实）

**机制**：premise_check 生成 correction 时未引用具体 active 记忆，而是自行推断因果叙事，导致 correction 给出错误原因，answer_gen 照单全收。

**Trace 证据**：
- **0223 (T2)**：
  - M_new：癫痫药导致驾照暂停（医疗/法律原因）
  - premise_check correction："你搬家了，40 分钟通勤限制已过时"（地址搬迁说法）
  - Answer_gen 用错误的 correction 给出错误建议
- **0013 (T1) dim2**：
  - stale_reason 由 abductive_judgment 写入"likely the US"（从电影院推断）
  - premise_check 照搬 stale_reason 进 correction → 最终回答说"the US"

---

### RC-E：Extraction 把行为事实抽象成情绪反应

**机制**：当陈述描述他人对用户的行为时，extraction 提取用户的情绪体验而非具体行为事实，导致 impact_hypothesis 缺乏精准的语义锚点。

**Trace 证据**：
- **0059 (T1)**：
  - 原文：`"people kept their distance whenever conversations turned personal, and no one looped me into the side chats anymore"`
  - 提取结果：`"user is uncertain about social dynamics and feels excluded at neighborhood events"`
  - 关键细节丢失："谈话涉及私人时才躲"→ 应提取"social group avoids sharing personal info with user"
  - 影响：impact_hypothesis 生成的假设全是宠物/工作/爱好，无一指向 confidentiality 记忆

- **0274 (T2)**：
  - 原文：`"I spent my lunch break at the county clerk's office signing the last of the paperwork—turning..."`（county clerk 签字，含义为某种法律程序完结）
  - 提取结果：`"I've been dealing with official paperwork"`（极度抽象）
  - impact_hypothesis 假设指向遗产整理、文档类，无一指向学生贷款

---

### RC-F：Preference_anchors 漏掉 current_state 社交声誉类记忆

**机制**：`get_preference_anchors()` 只返回 `lasting_preference` + `biographical` 类型的记忆，`current_state` 类的社交角色/声誉记忆不进 anchors，impact_hypothesis 没有指向它们的指针。

**Trace 证据**：
- **0059 (T1)**：
  - `m_00047: "People in this group know I keep things confidential"` → category=`current_state`，不在 anchors
  - Global impression 也未提及 confidentiality reputation
  - Impact_hypothesis 的 system prompt 里无此记忆，abductive_judgment 从未对 m_00047 执行
  - 结果：m_00047 全程 active，premise_check 认为前提安全，answer_gen 直接肯定"你还有保密名声"

---

### RC-G：事实嵌在问题句里，Extraction 整体过滤整个 turn

**机制**：Extraction 提示词规则"不提取 Pure requests/questions"被 LLM 应用到包含问题的整个 turn，连同 turn 里的事实性陈述一起过滤掉。

**Trace 证据**：
- **0399 (T2)**：
  - Session 10 原始用户发言：`"All my files are set to back up to the cloud automatically. Given that, is it still worth doing an external drive backup too..."`
  - Extraction 对 session 10 返回空列表
  - Session 36 "switched laptop to local-only account"（M_new）正常提取
  - 结果：dim1/dim2 无法检测云备份 → 本地账号的冲突
  
- **0321 (T2)**：
  - Session 0 为设计套件订阅的背景介绍（提问语境）
  - Extraction 对 session 0 返回空列表
  - "moved workstation, cut internet line"（M_new, session 39）正常提取
  - 结果：云端设计套件订阅记忆从未存在，无法与断网产生冲突判断

- **0240 (T2)**：
  - M_new 中 W-8BEN 签署未出现在任何 session 的提取结果里
  - 仅 `is a U.S. citizen`（M_old）被存为 m_00032
  - Impact_hypothesis 和 abductive_judgment 均无机会指向公民身份记忆

- **0382 (T2)**：
  - Premise_check 明确返回 `"no active memory confirms or contradicts the existence of a hard, physically demanding end-of-month deadline"`
  - 翻修截止日期记忆不在存储或不在检索范围内

---

### RC-H：T2 隐性语义链未闭合

**机制**：M_new 包含领域特定行为（特定税务表格、特定法律程序），其隐含含义需要 1-2 跳领域知识推断才能联系到 M_old。Impact_hypothesis 生成的假设停在字面层，没有产生指向正确记忆的假设。

**Trace 证据**：
- **0240 (T2) dim1**：
  - W-8BEN = 非美国税务身份人士签署的表格，强烈暗示公民身份存疑
  - Impact_hypothesis 需要推断：签 W-8BEN → 被视为非美国税务居民 → 与"is a U.S. citizen"冲突
  - 此链条未生成，m_00032 全程 active，premise_check 返回 safe

- **0274 (T2) dim1/dim2**：
  - county clerk 签字 → 可能为债务消除/法律文件 → 学生贷款可能已消除
  - 即使"official paperwork"被提取，impact_hypothesis 也未生成"学生贷款状态可能改变"的假设
  - m_00047（学生贷款）全程 active，系统继续给还贷建议

---

### RC-I：Embedding 检索语义鸿沟

**机制**：查询的语义向量与正确记忆的语义向量距离过大，top-k 检索拉回完全无关的记忆，premise_check 和 answer_gen 基于错误的记忆集生成回答。

**Trace 证据**：
- **0312 (T2) dim3**：
  - 查询：`"I'm about to buy a custom strap that only fits what I'm using right now"`
  - 检索到：吉他课、调酒工具、航班、晋升——完全无关
  - 正确记忆"spent Saturday at repair kiosk, watch sent in"与查询 embedding 距离过大
  - Answer_gen 输出了关于吉他课的建议

---

## 各 RC 样本索引

| RC | 影响样本 | 维度 |
|----|---------|------|
| RC-A | 0211, 0239, 0013 | dim1/dim2 |
| RC-B | 0211 | dim1 |
| RC-C | 0266, 0226 | dim2 |
| RC-D | 0223, 0013 | all dims |
| RC-E | 0059, 0274 | dim1/dim2 |
| RC-F | 0059 | dim1/dim2 |
| RC-G | 0399, 0321, 0240, 0382 | dim1/dim2 |
| RC-H | 0240, 0274 | dim1/dim2 |
| RC-I | 0312 | dim3 |

---

## T2 vs T1 准确率差距的根因

T2 比 T1 低约 13 个百分点，主要来自三个结构性因素：

1. **间接链长度**：T2 冲突需要 2-3 跳推断（M_new → 中间状态 → M_old 失效），链上任一环节失败都导致整体失败。RC-H 直接体现这一点。

2. **M_old 提取难度更高**：T2 的 M_old 往往在对话里以背景假设出现（"我的文件自动备份到云端"作为提问背景），比 T1 的直接陈述更难被 extraction 识别。RC-G 集中于 T2。

3. **Impression 无法覆盖多步位置链**：T2 样本常包含多次位置或状态变化，impression 残留更严重。RC-A 对 T2 影响尤为显著。

