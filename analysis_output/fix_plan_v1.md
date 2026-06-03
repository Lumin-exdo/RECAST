# RECAST 修复方案 v1 — 完整攻防推导过程及最终结论

基于 RC-A 至 RC-I 九个根因，每个方案均经历：初稿 → 已知案例验证 → 自造对抗案例攻击 → 修正 → 再攻击 → 最终方案。

**工作目录：** `/mnt/laq`  
**运行命令基础：** `/mnt/laq/venv/bin/python -m RECAST.run_new_mem --data-path RECAST/STALE/... --no-thinking`  
**修改文件：** `RECAST/prompt_lib/new_templates.py` · `RECAST/store_layer/new_store.py` · `RECAST/write/new_writer.py` · `RECAST/query/new_engine.py`

---

## 优先级总览

| 编号 | 根因 | 原则 | 文件 | 代价 | 预期收益 |
|------|------|------|------|------|---------|
| P1 | RC-G | Extraction 过滤单元是 statement，不是 turn | `new_templates.py` | 极低 | T2 miss 4 个样本 |
| P2 | RC-A | [STATUS] 有新事实时必须整体重写，不追加 | `new_templates.py` | 极低 | impression 残留问题 |
| P3 | RC-C | outdated_facts 若直接对应前提，必须 safe=False | `new_templates.py` | 极低 | dim2 假安全 |
| P4 | RC-D | correction 必须引用 active 记忆，禁止照搬 stale_reason | `new_templates.py` | 极低 | correction 归因错误 |
| P5 | RC-E | 提取他人行为事实，不提取用户情绪反应 | `new_templates.py` | 极低 | 语义精度 |
| P6 | RC-B | correction 在其所涉维度上优先于 profile_summary | `new_templates.py` | 极低 | 依赖 P4 先行 |
| P7 | RC-H | 官方表格/手续隐含状态变更，需跨领域推断 | `new_templates.py` | 低 | T2 间接链 |
| P8 | RC-F | anchors 补充社交声誉类 current_state 记忆 | `new_store.py` + `new_writer.py` | 中 | 社交角色冲突 |
| P9 | RC-I | 查询时实体扩展二次检索（仅 safe=True 时触发） | `new_engine.py` | 中 | embedding 语义鸿沟 |

**实施顺序建议：** P1 → P2 → P3 → P4 → P5 → P6（冒烟测试）→ P7 → P8 → P9（完整运行）

---

## P1：RC-G — Extraction 过度过滤含问题的 turn

### 根因

`STATEMENT_EXTRACTOR_PROMPT` 的排除规则："Do NOT extract: Pure requests, questions, or task instructions" 被 LLM 应用于**整个 turn**，连带过滤了 turn 中的事实性陈述。

### 可泛化原则

> 提取的粒度是 **statement**（陈述单元），不是 **turn**（整个发言）。一个 turn 可以同时包含事实和提问；提取应忽略提问部分，仅对陈述部分进行判断。

---

### 初稿 P1-v0

在排除规则后加一段：
> "A turn may contain BOTH factual claims AND questions. Extract factual claims from all parts of the turn — do not skip the entire turn because it also contains a question."

#### 已知案例验证

- **0399**："All my files are set to back up to the cloud automatically. Given that, is it still worth doing an external drive backup?" → v0 正确提取 "user's files automatically back up to the cloud"（current_state）
- **0240**："Since I signed a W-8BEN at the bank today, what does that mean for my taxes?" → v0 正确提取 "user signed a W-8BEN form at the bank"（recent_change）
- **0321**："I pay for a full design suite subscription. Should I upgrade my internet plan?" → v0 正确提取订阅事实

---

### 攻击 P1-v0

**攻击 G-1（提问前提被错误提取为主张）**

> 用户问："Is it true that I still prefer dark roast coffee?"

这句话的"I still prefer dark roast coffee"嵌在一个疑问句里，用户是在质疑自己的偏好，而非主张。P1-v0 的宽泛规则可能导致 LLM 提取出"user prefers dark roast coffee"（is_definite=true）。

反驳：上游 `hypothetical_filter` 会对 "is_definite" 二次判断；"是否如此" 的形式暗示不确定，is_definite 应为 false，不进 memory。但这依赖 filter 质量，不够保险。

**→ 需要在提示词层面强化：内嵌于问题里的前提假设（即"用户是在问是否还是这样"），不得被提取为确定性陈述。**

**攻击 G-2（他人移动被误作用户移动）**

> "My friend just moved to LA. Is that a good city to visit?"

P1-v0 可能提取："user's friend moved to LA"（recent_change / biographical）。这条记忆本身倒不算错（是用户的社交网络信息），但如果再宽泛化可能与用户位置混淆。

反驳："user's friend moved to LA" 并不是关于用户自身的事实，STATEMENT_EXTRACTOR 应遵循其基础规则"facts about the external world unrelated to the user's own situation"来过滤这类记忆。实际上这个攻击不触及 P1 的问题域，P1 只讨论过滤粒度。

**攻击 G-3（对 RC-H 的依赖）**

> 即使 P1 正确提取了 "signed W-8BEN"，RC-H（隐性语义链）仍可能阻止 impact_hypothesis 从"W-8BEN"推断到"is a U.S. citizen"矛盾。

反驳：P1 是 RC-G 的修复，是必要但不充分条件。0240 需要 P1（提取出 W-8BEN 事实）+ P7（推断出公民身份含义）才能完全修复。这不是 P1 的缺陷，是两个独立问题。

**攻击 G-4（全问题 turn 的边界案例）**

> "Why do I always procrastinate? Can I change this habit?"

整个 turn 是问题，没有独立的事实陈述。P1-v0 的规则"提取 turn 中的事实部分"在此 turn 中并无事实可提取。但 LLM 可能错误提取 "user always procrastinates"（作为一个内嵌事实）。

反驳："Why do I always procrastinate" 是修辞性问句，不是直陈。is_definite 分类应为 false（自我质疑，非断言）。hypothetical_filter 能处理。但为保险，P1 应加：**内嵌于反问句/修辞问句里的前提不提取**。判断标准：用户是在陈述（"I always procrastinate"），还是在质疑（"Why do I procrastinate?"）？陈述→提取；质疑→不提取。

---

### 修正版 P1-v1

加入三个细化规则：

1. 提取粒度是 statement，不是 turn（核心）
2. 内嵌于疑问结构（"Is it true that..."、"Do I still..."、"Why do I..."）中的前提假设不提取（用户在质疑，非主张）
3. 给出两个正例、一个反例（不涉及具体测试 uid）

---

### 再攻击 P1-v1

**攻击 G-5（合法的问题前提）**

> "Now that I've been diagnosed with lactose intolerance, should I avoid ice cream?"

"I've been diagnosed with lactose intolerance" 是一个事实，嵌在一个以它为前提的问句中。P1-v1 应正确提取这条记忆（recent_change）。

- "now that..." 结构是**事实性从句**，不是质疑；属于正例范畴。
- P1-v1 的规则：疑问结构（"Is it true that"、"Do I still"）≠ 条件从句（"Now that"、"Since"、"Given that"）。条件从句内的陈述应提取。

该案例通过 P1-v1。

**攻击 G-6（嵌套疑问）**

> "Do you think, given my freelance schedule that started last month, that I need a better accounting tool?"

"freelance schedule that started last month" 是事实性状语，嵌在疑问句中。应提取 "user started a freelance schedule last month"（recent_change）。

P1-v1 的正例模板覆盖了 "Since/Given that" 类条件从句，但未覆盖 "given my X" 这种名词短语形式的条件成分。需要在示例中补充这种模式。

→ 再补一个正例。

---

### 最终版 P1

**修改位置：** `new_templates.py`，STATEMENT_EXTRACTOR_PROMPT，在 "Do NOT extract" 列表之后、"Output JSON only" 之前新增如下内容：

```
TURN-LEVEL vs STATEMENT-LEVEL:
The exclusion rule "Do NOT extract Pure requests, questions" applies at the STATEMENT
level, not the TURN level. A user turn may contain both factual claims and questions.
Extract factual claims from ALL parts of the turn — do not return an empty list for a
turn simply because it also contains a question or request.

EXTRACT (factual clause embedded in a question-containing turn):
  Turn: "My lease in Portland ended last month and I moved in with my sister temporarily.
        Any advice for making shared living work?"
  → Extract: "user's lease in Portland ended; user is now living with sister" (recent_change)

  Turn: "Since I started my apprenticeship at the bakery three weeks ago,
        what kind of work shoes should I get?"
  → Extract: "user started an apprenticeship at a bakery three weeks ago" (recent_change)

  Turn: "Given my new standing desk setup I finally got installed, what ergonomic
        habits should I build?"
  → Extract: "user now has a standing desk setup" (current_state)

DO NOT EXTRACT (the assumption is inside a rhetorical or self-questioning form):
  Turn: "Is it true that I still prefer dark roast coffee?"
  → No extraction — user is questioning their own preference, not asserting it.
  Turn: "Why do I always procrastinate? Can I change this?"
  → No extraction — "why do I" is rhetorical, not a factual assertion.

Rule of thumb: if the factual content is in a subordinate clause introduced by
"since", "now that", "given that", "after", "because" — extract it.
If the entire sentence is a question about whether the user still has a trait/state
("Is it true that...", "Do I still...", "Am I still...") — do NOT extract.
```

---

## P2：RC-A — Impression [STATUS] 残留旧地址/状态

### 根因

`GLOBAL_IMPRESSION_UPDATE_PROMPT` 的规则 "Preserve accurate existing information that did not change" + "Only update sections where genuine changes occurred" 导致 LLM 在 [STATUS] 末尾追加新地址，而非替换旧地址。已标 stale 的记忆在 memory store 里正确消失，但 impression 里的对应句子无人清除。

### 可泛化原则

> [STATUS] 是**单一点快照**（current state），不是**时间线**（timeline）。每个维度（location, employment, health, relationship）只有一个当前值。当该维度的旧值通过 memory_changes 被标为 stale 且 new_statements 提供了新值时，必须**整体替换**该维度，而不是追加。

---

### 初稿 P2-v0

在 GLOBAL_IMPRESSION_UPDATE_PROMPT 的 Rules 末尾追加：
> "[STATUS] REWRITE RULE: When memory_changes shows a [STATUS] fact (location, job, health, relationship) went stale AND new_statements provide the updated value, REPLACE the old [STATUS] content for that dimension — do not append. If no replacement is available, write '(currently unknown)' rather than keeping stale info."

#### 已知案例验证

- **0211**：memory_changes 包含 "lives in San Francisco → stale"，new_statements 包含沙漠环境描述 → P2-v0 应将 [STATUS] 从 "Currently living in a small San Francisco apartment" 改写为 "Currently in a hot, arid desert environment"（即使没有城市名，也不保留错误的 SF）。
- **0013**：stale_reason 是 "likely the US"，但 impression_update 接收到 new_statements 里有多伦多相关内容 → [STATUS] 应写 Canada/Toronto，不能保留"probably the US"。

---

### 攻击 P2-v0

**攻击 A-1（临时出行被误判为搬迁）**

> 用户：我朋友在内华达沙漠，我飞过去帮他搬家。沙漠里好热！

假设 extraction 提取出 "user is currently in Nevada desert"（current_state）。这是**临时出行**，不是搬迁。P2-v0 的规则"new_statements 提供了新值 → 替换 [STATUS] 地址"会错误地将用户永久住所改为"内华达沙漠"。

这是**真实风险**，需要加保护条件。

反驳方向：只有新地址是**永久居所**时才触发替换。判断标准：是否有明确的永久定居信号（"moved to", "now living in", "signed a lease", "relocated"，或 M_new category=recent_change 且内容是搬迁行为）。

**攻击 A-2（stale_reason 不准确导致推断错误）**

RC-D 的 0013 案例：stale_reason 是 "likely the US"（不准确），abductive 推断的残留。如果 impression_update 从 stale_reason 推断新地址，会写入"the US"（错）。

反驳：P2 应明确：新 [STATUS] 内容必须来自 **new_statements**，不得来自 stale_reason。stale_reason 只是解释为何旧值 stale，不是新值来源。

**攻击 A-3（两地分居/双城工作）**

> 用户是异地工作者，周一飞北京，周五回上海。两个地址都是 active 记忆。新 session 说"又开始这周在北京了"。

P2-v0 的规则会用"北京"替换"上海"，但实际上用户是两城往返，不是搬迁。

反驳：如果用户有两条 active 的地址记忆（e.g., "lives in Shanghai on weekends", "works in Beijing on weekdays"），则 [STATUS] 应保留两条，不替换。只有**旧地址记忆被标为 stale** 时才触发替换。P2 的触发条件是"memory_changes 里有 [STATUS] 维度被 stale"，如果两条记忆都 active，不触发替换。

**攻击 A-4（隐含搬迁但无显式搬迁词）**

> 0211 案例：new_statements 是 "surrounded by saguaro cacti and intense dry heat"，没有 "moved to" 这样的搬迁词。P2 的永久居所保护条件（需要显式搬迁词）会让这条信息不触发 [STATUS] 替换。

这是**真实矛盾**：P2-v0 用显式搬迁词保护临时出行，但会漏掉隐式搬迁。

反驳：改变触发条件：不依赖 new_statements 里的搬迁词，而是依赖 **memory_changes 里旧记忆被标 stale**。逻辑：如果旧地址记忆已 stale（说明系统判断搬迁发生了），就应该更新 [STATUS]，用 new_statements 里能提供的最佳描述替换。即使没有显式城市名，也用隐含描述（"a hot, arid desert environment"）替换"San Francisco"。不要求 new_statements 包含搬迁词；只要求旧地址 stale。

**攻击 A-5（哪些属于 [STATUS] 维度？）**

[STATUS] 包含 location, employment, health, relationships。如果用户说"我换了手机"，这是 current_state，但不是 [STATUS] 级别的核心维度。P2 的规则是否会把手机也纳入替换逻辑？

反驳：[STATUS] 的核心维度是对用户**身份和生活状态**最关键的维度：居住地、工作/就业状态、健康状况、家庭/伴侣状态。换手机、换沙发不属于这些维度。规则应明确限定触发维度。

---

### 修正版 P2-v1

触发条件：**旧 [STATUS] 维度的记忆出现在 memory_changes（即被标 stale）**，而不是检测 new_statements 里是否有搬迁词。
内容来源：新 [STATUS] 内容来自 new_statements，不来自 stale_reason。
粒度：精确到维度（location / employment / health / relationship_status），不是整个 [STATUS]。
覆盖范围：仅 location / employment / health / relationship_status 四个核心维度，不含物品/设备等。

---

### 再攻击 P2-v1

**攻击 A-6（memory_changes 里有 stale，但 stale 记忆内容与 [STATUS] 不对应）**

> memory_changes："user's gym membership expired → stale"。这条记忆 stale 了，但它对应的是[HABITS]而非[STATUS]。P2-v1 会不会错误地修改 [STATUS]？

反驳：P2 的触发逻辑必须检查 stale 记忆的语义维度。"gym membership" 属于 HABITS，不属于 STATUS。触发条件应更精确：**memory_changes 中被 stale 的记忆内容涉及 location / employment / health / relationship_status 四个维度时，才修改 [STATUS] 对应维度**。提示词需要给出四个维度的明确示例，帮助 LLM 分类。

**攻击 A-7（new_statements 没有足够信息填充新值）**

> memory_changes："lives in San Francisco → stale"。new_statements："it's been a big change." 没有任何地理位置信息。

P2-v1 触发了替换，但 new_statements 里没有新值可用。应写"(location currently unknown)"，而不是保留 SF 或胡乱猜测。

结论：这个 fallback 已在 P2-v0 中包含（"如果没有替换值，写 '(currently unknown)'"），在 v1 中保留。

---

### 最终版 P2

**修改位置：** `new_templates.py`，GLOBAL_IMPRESSION_UPDATE_PROMPT，在 Rules 末尾（"Focus on facts, not speculation" 之后）追加：

```
[STATUS] OVERWRITE RULE — MANDATORY:
[STATUS] represents the user's current state snapshot — it has exactly one value per
dimension. Never append an old and new value together in the same dimension.

The four core [STATUS] dimensions are:
  (1) Location / where the user lives
  (2) Employment / job / income source
  (3) Health / physical or medical condition
  (4) Relationship / family / household status

When memory_changes lists a stale record whose content clearly belongs to one of the
four core [STATUS] dimensions above:
  → IDENTIFY which dimension it belongs to
  → SEARCH new_statements for the updated value for that dimension
  → REPLACE the old [STATUS] content for that dimension with the new value
  → Do NOT keep the old value anywhere in [STATUS]

If new_statements do not provide a clear replacement value:
  → Write "(currently unknown)" for that dimension
  → Still remove the now-stale value — never retain confirmed-stale info in [STATUS]

Source of new [STATUS] content:
  → Always take from new_statements
  → NEVER use stale_reason as the source — stale_reason is an inference trace, not a fact
     (it may say "likely in the US" when the user is actually in Canada)

What does NOT trigger [STATUS] replacement:
  → Devices, possessions, subscriptions, memberships (these belong in [HABITS] or [CHANGES])
  → Temporary visits or trips (no core-dimension memory was stale; user is just traveling)
```

---

## P3：RC-C — premise_check 识别 stale 事实但未设 premise_safe=False

### 根因

premise_check 的现有规则：`premise_safe=false: a stale memory directly shows the question is built on outdated information`。

问题：LLM 将相关 stale 记忆放入 `outdated_facts`，却仍返回 `premise_safe=True`，未能识别"查询直接以该 stale 事实为前提"。

### 可泛化原则

> `outdated_facts` 列表中的项目与查询前提之间存在**直接依赖关系**时，无论其他条件如何，`premise_safe` 必须为 False。系统已知某事已过时，而问题恰好以"该事仍成立"为前提，这就是不安全前提。

---

### 初稿 P3-v0

在 PREMISE_CHECK_PROMPT 的 Rules 末尾追加：
> "FINAL CHECK: After populating outdated_facts, re-read the question. If the question directly assumes any item in outdated_facts is still true — i.e., if that item being false would make the question's premise incorrect — set premise_safe=False."

#### 已知案例验证

- **0266**：outdated_facts = ["watching gaming streams on Twitch during lunch breaks"]，query = "What gaming streams does this user typically watch at lunch?"。最终检查：query 直接假设 gaming streams 习惯仍然存在 → premise_safe=False（正确）。
- **0226**：outdated_facts = ["works at a dim workstation with minimal natural light"]，query = "How can we improve lighting for this user's workstation?" → query 假设昏暗工位仍然存在 → premise_safe=False（正确）。

---

### 攻击 P3-v0

**攻击 C-1（假阳性：松散关联的 stale 记忆）**

> outdated_facts：["user used to attend Tuesday night trivia at a bar"]
> query："What time should the user set their alarm to be ready for work?"

outdated_facts 里有 stale 的 trivia 习惯，但闹钟时间问题与 trivia 完全无关。P3-v0 的 "直接假设"要求能否正确过滤？

P3-v0 的规则是"直接假设 outdated_facts 里某项仍为真"。闹钟问题不假设 trivia 仍然存在。→ 正确返回 premise_safe=True。该案例通过。

**攻击 C-2（间接依赖被错误判断为"直接"）**

> outdated_facts：["user's evening was mostly free for social activities"]
> query："What are some good evening activities the user might enjoy?"

query 问的是"享受哪些活动"，不直接假设"晚上有空"是前提。但如果用户现在整个晚上都被工作占满，"享受晚间活动"就无从谈起。这是1跳间接依赖。

P3-v0 的"直接假设"措辞——"that item being false would make the question's premise incorrect"——是否能覆盖这种1跳间接情况？

存在模糊地带。"晚上有空"→"可以做晚间活动"是近乎直接的推断。LLM 可能会正确识别，也可能不会。

→ 在 P3 中加入"1跳间接依赖"的例子，明确说明这也应触发。

**攻击 C-3（outdated_facts 是旧偏好，query 问当前偏好）**

> outdated_facts：["user prefers Italian restaurants"]
> query："What restaurants should we recommend to this user?"

query 不直接问 Italian，是开放性问题。stale 的 Italian 偏好不应让 premise_safe=False（用户可能有其他当前偏好）。

P3-v0 处理：query 的前提不是"Italian 偏好仍然存在"，而是"用户有某种餐厅偏好"。后者没有被 stale——只是旧的具体偏好 stale 了，新偏好可能存在。→ premise_safe=True 是正确的。

该案例通过，但 P3 的示例应明确区分"问题前提 = 被 stale 的具体事实"vs"问题前提 = 更宽泛的命题"。

**攻击 C-4（自造，最严格）**

> outdated_facts：["user streams games on Twitch every afternoon"]
> query："Have you seen any good Twitch streams lately?"

query 并不直接假设"用户自己"看流，而是问"有没有看到好的流"（可能是朋友分享的）。这是一个前提模糊的问题。

P3-v0 应该如何处理？"user streams Twitch every afternoon" → stale。问"有没有看到好的流"→ 前提是"用户有途径接触 Twitch 流"，而不直接假设 Twitch 习惯本身。

→ 这是一个边界案例，premise_safe 可为 True（宽容）也可为 False（保守）。P3 应明确：**"直接依赖"是指查询的核心名词或动词与 outdated_fact 指向同一行为/状态**。"看流"与"每天看 Twitch 流"属于同一行为领域，应为 False。"推荐一家餐厅"与"喜欢意大利菜"属于不同粒度，可为 True。

---

### 最终版 P3

**修改位置：** `new_templates.py`，PREMISE_CHECK_PROMPT，在现有 Rules 末尾追加：

```
CLOSING DEPENDENCY CHECK (mandatory after populating outdated_facts):

After you have identified all presuppositions and populated outdated_facts, perform
this final check for EACH item in outdated_facts:

  Ask: "Does the question's central assumption DIRECTLY DEPEND on this item being
        still true — such that if this item were false, the question's core premise
        would be undermined?"

If YES for ANY item: premise_safe MUST be False.

"Directly depends" includes:
  — The question explicitly asks about the same behavior/state as the stale item
    ("what do you usually cook for your weekly meal prep?" depends on the meal-prepping habit)
  — The question's action would be pointless or misleading without the stale item
    ("what's the best route for your morning commute?" depends on the user still commuting)
  — A 1-step inference connects the stale item to the question's core action
    ("what evening activities might you enjoy?" depends on evenings being available,
     if the stale item is "evenings were mostly free")

"Does NOT directly depend" (premise_safe may still be True):
  — The stale item is tangentially related but the question remains valid without it
    ("what restaurants would you recommend?" does NOT depend on a specific past
     restaurant preference being still active — the question is still sensible)
  — The question is about a different dimension than the stale item

WORKED EXAMPLE OF MUST-SET-FALSE:
  question: "How does the user usually wind down on their evening runs?"
  outdated_facts: ["user goes for evening runs three times a week"]
  → The question assumes the evening running habit is ongoing — this IS the stale item
  → premise_safe = False (even though the LLM already listed it in outdated_facts)
```

---

## P4：RC-D — premise_check correction 归因错误

### 根因

premise_check 生成 `correction` 时，会照搬 `stale_reason` 字段里的内容（该内容来自 abductive_judgment 的推断链，可能是不精确的推断），而非引用具体的 active 记忆。导致 correction 给出错误原因，answer_gen 照单全收。

### 可泛化原则

> correction 是系统向用户解释"哪里变了"的信息，必须来自**已确认为真的 active 记忆**，不得来自 stale_reason（推断痕迹）。推断链可以是错的；active 记忆是更可靠的事实来源。

---

### 初稿 P4-v0

在 PREMISE_CHECK_PROMPT 的 correction 相关规则后加：
> "correction must be grounded in specific ACTIVE memory content. Do NOT copy stale_reason verbatim — stale_reason is an inference trace that may be imprecise. Use what the active memories actually say."

#### 已知案例验证

- **0013**：stale_reason = "likely the US"（错误推断）。Active 记忆包含 Toronto/Canada 相关内容。P4-v0：correction 不从 stale_reason 取，从 active 记忆取 → correction 应正确提及 Canada/Toronto。
- **0223**：stale_reason = "epilepsy medication → license suspended"。Active 记忆包含癫痫药和驾照暂停。P4-v0：correction 引用 active 记忆 → 正确说明医疗/驾照原因，不误说"搬家了"。

---

### 攻击 P4-v0

**攻击 D-1（无 active 记忆解释变化原因）**

> 旧记忆 stale："user commutes to the office every day"。Active 记忆：无任何关于通勤/工作的 active 记忆（没有存新信息，只是旧信息被 stale）。

P4-v0 要求 correction 引用 active 记忆，但没有相关 active 记忆。LLM 可能用空格搪塞或瞎编。

反驳：需要明确 fallback：当没有 active 记忆能解释变化时，correction = "We know that [stale fact content] is no longer current, but we don't have information about what replaced it." 不编造，不沉默。

**攻击 D-2（active 记忆太多，LLM 选了错误的那条）**

> Active 记忆包含：(1)"user started yoga classes"，(2)"user switched to remote work"，(3)"user bought a bicycle"
> Stale："user drives to the office"
> Correction 应引用"switched to remote work"，但 LLM 可能引用"bought a bicycle"（有表面关联但不是原因）。

反驳：correction 应引用与**被 stale 的维度最直接相关**的 active 记忆。驾车去办公室 stale → 最直接相关的是"switched to remote work"，而不是"bought a bicycle"（购车可以是通勤工具，但不是"不开车上班"的原因）。提示词需要加：**优先引用与被 stale 事实属于同一生活维度的 active 记忆**。

**攻击 D-3（correction 里引用记忆 ID 而非内容）**

> 如果 LLM 写 correction = "According to memory m_00042, the user no longer commutes."

这在 prompt 输出里看起来很奇怪，answer_gen 会照单全收，最终答案里出现"memory m_00042"字样。

反驳：明确要求 correction 使用**内容**而非 ID。格式：paraphrase the active memory content, don't cite the ID.

**攻击 D-4（自造，最严格）**

> Stale："user used to practice guitar every evening"
> Active 记忆："user started a new job that ends at 9pm"
> Stale_reason："user likely stopped practicing guitar due to busy schedule"

P4-v0 要求不照搬 stale_reason，要引用 active 记忆。Active 记忆说的是"新工作到晚上9点"，correction 应说："Based on the new job schedule (ending at 9pm), the user's evening guitar practice may no longer be feasible." 这是合理的 1-hop 推断，但 P4-v0 要求"不编造 causal narratives not present in any active memory"。

问题：active 记忆是"job ends at 9pm"，correction 里说"guitar practice no longer feasible"——这是一个推断，不是直接来自 active 记忆的陈述。是否允许？

反驳：需要明确：correction 可以包含**从 active 记忆到结论的一步直接推断**，但不能跨越多跳。"工作到9pm → 晚间吉他练习受影响"是直接推断，允许。"用户搬家了 → 所以不需要通勤"（当原因完全不在 active 记忆里时）不允许。

---

### 最终版 P4

**修改位置：** `new_templates.py`，PREMISE_CHECK_PROMPT，替换或扩展 "correction should be specific" 那条规则：

```
correction GROUNDING RULES:
(1) Ground correction in the content of ACTIVE memories. Paraphrase what the active
    memories actually say — do not cite memory IDs, and do not copy stale_reason verbatim.
    stale_reason is an abductive inference trace that may be imprecise or wrong; the
    active memories are the authoritative source of what is currently true.

(2) A single direct-inference step from an active memory to the conclusion is allowed:
    active: "user's new job ends at 9pm every evening"
    → correction: "The user's new schedule (finishing work at 9pm) likely affects their
      evening routines." ← one-step inference from active memory, acceptable.
    Do NOT chain through multiple unstated steps.

(3) Prefer the active memory MOST DIRECTLY related to the stale dimension:
    stale: "drives to the office"
    active candidates: [yoga classes, switched to remote work, bought a bicycle]
    → use "switched to remote work" (same life dimension: work access/commute)
    → do NOT use "bought a bicycle" (surface overlap but not the reason)

(4) If no active memory can explain the change:
    correction = "We know that [paraphrase of stale fact content] is no longer current,
    but we don't have specific information about what changed."
    Do NOT invent a reason. Do NOT leave correction blank.

(5) NEVER echo stale_reason text verbatim. Even if stale_reason says "likely in Canada",
    use what active memories say about the user's location instead.
```

---

## P5：RC-E — Extraction 把行为事实抽象成情绪反应

### 根因

当用户描述**他人对用户的行为**时，extraction 提取用户的**情绪体验**（"feels excluded"），而非他人的**具体行为**（"social group excludes user from personal conversations"）。导致 impact_hypothesis 缺乏精准的语义锚点，无法触发相关记忆的 abductive 检查。

### 可泛化原则

> 可从行为事实中推导情绪，但不能反过来：情绪描述中没有行为信息，无法用于 embedding 检索或 abductive 推断。当用户描述他人的行为模式时，提取**行为**而非**情绪**。

---

### 初稿 P5-v0

在 "IMPORTANT: Extract the factual core even when wrapped in emotional language" 后追加：
> "Also: when the statement describes how OTHERS behave toward the user, extract the behavioral fact (what they concretely do), not the user's emotional reaction to it."

#### 已知案例验证

- **0059**："people kept their distance whenever conversations turned personal, and no one looped me into the side chats anymore" → P5-v0 提取："social group avoids sharing personal conversations with user, excludes user from private group chats"（current_state）。这比"feels excluded"精确得多，更容易触发 confidentiality 类记忆的 abductive 检查。

---

### 攻击 P5-v0

**攻击 E-1（纯情绪描述，无行为可提取）**

> "I feel so isolated at work lately."

没有任何他人的行为描述，只有用户的情绪。P5-v0 是否会强迫提取一个不存在的行为？

反驳：P5-v0 的规则是"当陈述描述他人行为时"才提取行为。"I feel isolated" 不含他人行为描述，不触发 P5 规则，回归原有的情绪/状态分类（current_state: user feels isolated）或被视为 EMOTIONAL 过滤掉。该案例安全。

**攻击 E-2（夸大/虚假的行为描述）**

> "Everyone at work is out to get me."

"Everyone is out to get me" 是极度夸大的表述，不是具体可观察的行为。P5-v0 是否会提取 "coworkers are actively harming user" 这种不可靠的记忆？

反驳：原有规则"only extract is_definite=true statements"——"everyone is out to get me"属于情绪性泛化，is_definite 应为 false（高度主观、无具体事实）。filter 阶段分类为 EMOTIONAL 或 is_definite=false，不进 memory。

但 P5 的规则应明确：**只提取具体的、可观察的、持续的行为模式**，不提取情绪性概括（"everyone hates me"、"everyone is out to get me"）。

**攻击 E-3（单次事件被误提取为模式）**

> "My boss yelled at me in front of everyone during today's standup."

这是**一次性事件**，不是持续模式。P5-v0 会提取 "user's boss yelled at user in a team meeting"（recent_change），这没问题，但它可能会分类为 current_state（暗示持续态），导致 abductive 判断认为 "user has a supportive work environment" 被挑战，产生误判。

反驳：单次事件 + "recent_change" 分类是合适的（boss 行为发生了一次）。Extraction 不应将其标为 current_state（那是持续状态）。P5 需要明确：**模式性描述**（"people keep/always/anymore"）→ current_state；**单次事件**→ recent_change（且仅在有持续含义时提取）。

**攻击 E-4（自造，最严格）**

> "My neighbor brought me soup when I was sick last week."

他人的行为（neighbor brought soup）。是否应提取 "user's neighbor brought user soup when sick"？这是一次性善意行为，不是持续社交模式，可能没有记忆价值。

反驳：Extraction 的基础规则："one-time events with no lasting consequence"不提取。邻居带汤是一次性的，除非能推断出"user has a close relationship with neighbor"（biographical），否则不应提取。P5 不改变这个边界——P5 只影响如何描述**被提取的内容**，不改变**是否提取**的门槛。

---

### 最终版 P5

**修改位置：** `new_templates.py`，STATEMENT_EXTRACTOR_PROMPT，在 "IMPORTANT: Extract the factual core even when wrapped in emotional language" 段落后追加：

```
BEHAVIORAL FACT vs EMOTIONAL REACTION:
When the statement describes a persistent pattern of how OTHERS behave toward the user,
extract the BEHAVIORAL FACT — what they concretely do — not the user's emotional reaction.

  "Whenever I bring up my side project at the neighborhood meetups, everyone quickly
   changes the subject"
  → Extract: "social group consistently avoids engaging with user's side project
    at neighborhood gatherings" (current_state)
  NOT: "user feels rejected about side project" ← that is the reaction, not the fact

  "My coworkers stopped including me in the informal Friday lunches after the
   department moved floors"
  → Extract: "coworkers no longer include user in informal lunch gatherings since
    the department relocated" (current_state)
  NOT: "user feels left out at work"

ONLY apply this when:
  — The behavior described is a PERSISTENT PATTERN (signaled by: "keep", "always",
    "never", "anymore", "every time", habitual present tense, "no longer")
  — It is a SPECIFIC, OBSERVABLE behavior (not a vague generalization like
    "everyone hates me" or "everyone is out to get me")
  — It reveals something meaningful about the user's SOCIAL ENVIRONMENT or STATUS

One-time incidents by others are NOT extracted under this rule (only if they imply
a lasting relationship or status: "my neighbor brought soup → close neighbor relationship"
could be biographical; "my boss yelled once" → recent_change only if significant pattern)
```

---

## P6：RC-B — answer_gen 用 profile_summary 覆盖 correction

### 根因

ANSWER_GENERATION_PROMPT 的 disambiguation rule：profile_summary 作为 tiebreaker。当 premise_check 的 correction 与 profile_summary 的内容在同一维度冲突时，LLM 仍倾向于使用 profile_summary（因为其内容更完整），导致正确的 correction 被无效化。

### 依赖

P6 必须在 P4（correction 内容准确）之后实施。若 correction 仍然错误（如 RC-D 未修），则提升 correction 优先级只会固化错误。

### 可泛化原则

> correction 是系统发现的最新状态更新，其优先级高于压缩摘要（profile_summary 可能落后于最新记忆）。但 correction 只对其**直接涉及的维度**具有权威性；其他维度仍由 profile_summary 提供。

---

### 初稿 P6-v0

修改 disambiguation rule：
> "When correction is non-empty: the correction is authoritative for the specific dimension it addresses. Profile_summary remains useful for other dimensions not covered by the correction."

---

### 攻击 P6-v0

**攻击 B-1（correction 内容不精确导致覆盖错误维度）**

> correction："User's situation has changed significantly."（不具体）

P6-v0 将"correction 所涉及的维度"设为 correction 的权威范围。但如果 correction 写得很宽泛，LLM 可能误判"所涉维度"而过度屏蔽 profile_summary。

反驳：P4 已要求 correction 必须具体并引用具体 active 记忆。如果 P4 实施正确，correction 不会宽泛到"situation changed significantly"。P6 依赖 P4 的结果。

**攻击 B-2（correction 涉及位置，但 profile_summary 有用的位置补充信息）**

> correction："User moved to a desert environment."
> profile_summary [STATUS]："Currently in San Francisco, works remotely as a software engineer."

P6-v0 让 correction 覆盖 location 维度。但 profile_summary 里还有"works remotely as a software engineer"（employment，不是 location）。如果 P6 只覆盖 location，employment 信息保留——这是正确行为。

该案例通过，P6-v0 按维度而非整体覆盖是正确的。

**攻击 B-3（自造，correction 是关于 health，profile_summary 有 location 和 habits）**

> correction："User's license is suspended due to medical restriction."
> profile_summary："Lives in Chicago, works at Acme Corp, drives to work daily, prefers morning workouts."

P6-v0：correction 涉及 health/mobility 维度 → 覆盖"drives to work daily"部分（该部分与 license 直接相关）。其余"lives in Chicago, works at Acme, prefers morning workouts"保留。

这是正确行为。该案例通过。

---

### 最终版 P6

**修改位置：** `new_templates.py`，ANSWER_GENERATION_PROMPT，替换现有的 Disambiguation 规则：

```
Disambiguation rules (apply in order):
1. When correction is non-empty (premise_safe=False):
   The correction is AUTHORITATIVE for the specific dimension it addresses
   (e.g., if correction addresses location, use correction's location; if it addresses
   health/mobility, use that over any conflicting profile_summary claim on that topic).
   For all other dimensions NOT addressed by correction, profile_summary remains valid.
   Correction OVERRIDES profile_summary on its dimension — not the other way around.

2. When no correction, but multiple active facts conflict on the same dimension:
   Individual active facts from more recent sessions take precedence over older ones.
   Profile_summary serves as tiebreaker and broader context only.

3. Profile_summary is a compressed, potentially lagging snapshot.
   It should NEVER override correction or recent individual active facts.
   Use it for context and filling in dimensions not covered by correction or active facts.
```

---

## P7：RC-H — T2 隐性语义链未闭合（领域知识缺口）

### 根因

M_new 包含领域特定的官方行为（W-8BEN、county clerk 签字），其隐含含义需要 1-2 跳领域知识才能与 M_old 产生连接。impact_hypothesis 生成的假设停在字面层，未产生跨领域的中间推断。

### 风险：reward hacking

如果直接在 impact_hypothesis 里加 "W-8BEN 暗示公民身份" 或 "county clerk 暗示学生贷款"，这是对具体测试案例的 reward hacking，违反规则。

必须找到**可泛化原则**：不针对具体表格或程序名称，而是针对**官方事务/法律行为的整体类型**。

### 可泛化原则

> 官方表格、法律文件、政府/金融机构手续，是**地位变更的标记事件**（life status marker events）。这类行为往往意味着某种法律/财务/公民身份状态已发生变化。impact_hypothesis 在面对此类事件时，应主动推断其**隐含的状态变更类型**，并与存储的 biographical/status 记忆交叉检查。

---

### 初稿 P7-v0

在 IMPACT_HYPOTHESIS_PROMPT 的 STEP 1 维度列表中新增一个维度：
> "LEGAL/OFFICIAL STATUS: When the statement mentions signing a form, completing a formal procedure, or visiting an official institution (bank, government office, court, clerk's office), reason about what STATUS CHANGE that transaction typically finalizes or implies."

---

### 攻击 P7-v0

**攻击 H-1（日常银行业务被过度推断）**

> "I went to the bank to open a new savings account."

P7-v0 的"官方机构手续"规则会触发对"status change"的推断。"开储蓄账户"是日常行为，不意味着公民身份或税务居民身份的变化。impact_hypothesis 可能生成"user may have changed tax residency"——这是过度推断。

反驳：错误的 hypothesis 进入 abductive_judgment 后，会与 "is a U.S. citizen" 等记忆交叉，可能导致误判。

需要在规则中限定：**不是所有官方手续都触发地位变更推断**，而是那些**涉及身份/资格/法律关系**的特定类型。区分：

- 日常账务操作（存款、取款、开普通账户）→ 财务行为，通常不改变法律地位
- 签署特定文件（税务表格、法律协议、政府申请）→ 可能改变法律/财务状态

P7 需要更精确的触发条件：**签署或提交了带有法律/身份含义的特定文件**，而不是所有银行/政府机构访问。

**攻击 H-2（LLM 不知道具体表格的含义）**

某些表格名称（W-8BEN、Form 1099、I-9）LLM 可能不熟悉。P7 的规则是通用规则，但要求 LLM 推断出 W-8BEN 的含义。

反驳：W-8BEN 是广为人知的国际税务表格，大多数 LLM 能正确识别。真正的问题是 LLM 在生成 hypotheses 时**没有被提示**要做这种领域推断，而不是不知道。P7 通过明确要求"推断该表格/手续的隐含状态含义"来激活这一知识。

如果 LLM 确实不知道某个表格，生成的 hypothesis 会是模糊的（"may have changed financial status"），这不会产生错误判断，只是 hypothesis 覆盖范围更粗。

**攻击 H-3（错误推断产生假 stale）**

> 用户说："I signed a W-4 at work today."

W-4 是美国雇主留存的税款预扣表格，不改变公民身份。如果 LLM 混淆 W-4 和 W-8BEN，会生成错误 hypothesis。

反驳：W-4 的场景下，impact_hypothesis 若生成 "user's citizenship status may have changed"（confidence 极低），abductive_judgment 会拒绝这一 hypothesis（W-4 签署和 citizenship 之间的 confidence 应 < 0.35，进不了 pool）。错误推断成本低：最差结果是一条无效 hypothesis，不会产生错误的 stale 标记。

**攻击 H-4（RC-G 的依赖：P7 单独无法修复 0240）**

如果 0240 的 W-8BEN turn 被 extraction 完全过滤（RC-G），则即使 P7 正确，也没有 M_new 触发 impact_hypothesis。

反驳：P7 确实依赖 P1（RC-G）先修复。0240 需要 P1+P7 组合才能完全修复。这是已知的序列依赖，不是 P7 的缺陷。

---

### 修正版 P7-v1

更精确的触发条件：**签署/提交了带有法律或身份含义的文件**，不是所有官方机构访问。

---

### 再攻击 P7-v1

**攻击 H-5（判断"法律/身份含义"的边界不清楚）**

> "I signed a rental agreement for my new apartment."

租房协议是法律文件，确实有地位含义（居住地改变）。P7-v1 会触发 "may imply relocation" 的 hypothesis。这是**正确的**——P2（RC-A）也处理这个场景，两者相互加强。

> "I signed a non-disclosure agreement at work."

NDA 是法律文件，但不改变公民/税务/财务身份。P7-v1 若触发 "signed a legal document → may imply status change" 的推断，会生成无关假设。

反驳：NDA 场景下，impact_hypothesis 生成 "may have changed employment status or confidentiality obligations"——这实际上是合理的假设（新工作、新项目、涉密级别改变）。abductive_judgment 会对这些 hypothesis 与 memory 进行交叉，低置信度的会被丢弃。成本依然低。

---

### 最终版 P7

**修改位置：** `new_templates.py`，IMPACT_HYPOTHESIS_PROMPT，在 STEP 1 的五个维度之后，新增第六个维度：

```
LEGAL / OFFICIAL STATUS: When the statement mentions signing or submitting a specific
document, completing a formal procedure at an official institution (bank, tax authority,
government office, court, clerk's office, immigration office), or undergoing a formal
assessment or registration:

  Step A — Identify the TYPE of transaction:
    Financial/tax: bank forms, tax documents, loan agreements, account closures
    Civic/identity: government registration, immigration forms, citizenship processes
    Legal status: court filings, dissolution documents, property/debt instruments
    Professional: licensing exams, professional certifications, regulatory registrations

  Step B — Reason about what STATUS CHANGE this transaction type typically FINALIZES:
    Financial document at bank or financial institution → may mean change in account
      arrangement, loan status, declared income source, or financial beneficiary
    Government office paperwork → may mean change in immigration status, marital status,
      property ownership, civic enrollment, or legal standing
    Legal instrument at official office → may mean change in a legal obligation,
      contract status, or asset arrangement

  Step C — Cross-reference with stored biographical/status memories:
    Does any stored memory assume a status that this transaction might now challenge?
    (citizenship, residency, financial obligations, legal standing, professional license)

Example application:
  statement: "I submitted my enrollment paperwork at the registrar's office this morning"
  → Type: educational enrollment process
  → Possible change: student status, full/part-time enrollment, schedule constraints,
    financial aid eligibility
  → Check: any stored memories about educational status, income, time availability?

NOTE: Routine transactions without a specific document signing (deposit, withdrawal,
asking a question) do NOT warrant this deep inference. The trigger is a specific
DOCUMENT SIGNING or FORMAL PROCEDURE COMPLETION, not mere institutional visits.
```

---

## P8：RC-F — preference_anchors 漏掉 current_state 社交声誉类记忆

### 根因

`get_preference_anchors()` 只返回 `lasting_preference` + `biographical` 类型。`current_state` 类的社交角色/声誉记忆（如"People know I keep things confidential"）不进 anchors，impact_hypothesis 完全看不到它们，abductive_judgment 从未对其执行。

### 可泛化原则

> 用户在某个社会群体中的**声誉、角色和信任地位**是动态的（current_state），但对预测"新事件会影响哪些旧信念"非常重要，重要性不亚于 lasting_preference。这类记忆应参与 impact_hypothesis 的交叉检查。

### 为什么不把所有 current_state 都加进 anchors？

current_state 包含大量不适合作为 anchor 的记忆（"user is currently in a hot desert"、"user's watch is being repaired"）。全量加入会稀释 hypothesis 生成质量。需要筛选出**社交/声誉/角色**相关的 current_state 子集。

---

### 初稿 P8-v0

修改 `get_preference_anchors(embedding)`:
- 除了原有的 lasting_preference + biographical，还用 embedding 相似度筛选 top-3 current_state 记忆（相似度基于固定社交声誉探针）

---

### 攻击 P8-v0

**攻击 F-1（探针向量不精确，漏掉目标记忆）**

探针："user's social role, reputation, trust level, standing in a group or community"

目标记忆："People in this group know I keep things confidential"

这两者的语义相似度是否足够高？"confidential" 和 "trust level" 是近义词，"People know I..." 和 "standing in a group" 语义相关。这条记忆应该相似度 > 0.3，能被检索到。

但如果记忆换成："My neighbors respect my privacy needs"——和探针"social role, reputation, trust"的相似度怎样？"respect privacy" 与 "trust level" 有语义关联，应该能通过。

**攻击 F-2（非社交 current_state 记忆被误加入 anchors）**

> current_state 记忆："user is currently in a recovery period after knee surgery"

和探针的相似度？"recovery period" vs "social role, trust level" → 语义距离较大，应该不在 top-3。

> current_state 记忆："user is currently managing a team of 8 people"

"managing a team" vs "social role, reputation" → 相似度较高，可能进入 top-3。但"team manager"确实是社交角色，加入 anchors 是合理的。

**攻击 F-3（anchors 变长，impact_hypothesis 漏处理）**

原有 anchors 是 lasting_preference + biographical 记忆，可能有 10-20 条。加了 3 条 current_state 后变为 13-23 条。IMPACT_HYPOTHESIS_PROMPT 的 Step 2 Part B 要求"For EACH item listed under 'Stored persistent traits and preferences'"逐一处理——项目增多时 LLM 注意力可能下降。

反驳：增加 3 条对 LLM 注意力的影响较小，且 IMPACT_HYPOTHESIS 已有"Part B — MANDATORY for EACH item"的强调。可以在传入 anchors 时标注这 3 条社交声誉记忆是"高优先级交叉检查项"。

**攻击 F-4（自造，最严格：同名记忆但方向相反）**

> current_state 记忆："People no longer trust me with sensitive information"

这条记忆语义上与探针"trust level"也相似，会进入 top-3。但它是**信任关系下降**的记忆，与 "People know I keep things confidential" 方向相反。如果两条都在 anchors 里，impact_hypothesis 会生成矛盾的 hypotheses。

反驳：矛盾的 hypotheses 进入 abductive_judgment 后，高置信度的会 stale，低置信度的会 uncertain。系统能处理矛盾。这不是问题。

**攻击 F-5（代码改动：embedding 参数传递）**

`get_preference_anchors()` 在 `new_writer.py` 里被调用：
```python
preference_anchors = self.store.get_preference_anchors()
```

需要改为：
```python
preference_anchors = self.store.get_preference_anchors(embedding=self.embedding)
```

另外 `self.embedding` 属于 `NewMemoryAgent`（主对象），`NewProfileStore` 不持有 embedding。这意味着 embedding 必须作为参数传入，而不是 store 内部持有。这是合理的设计——store 不应持有模型。

---

### 最终版 P8

**修改位置 1：** `store_layer/new_store.py`，`get_preference_anchors()` 方法：

```python
def get_preference_anchors(self, embedding=None) -> List[str]:
    """Return content of active/uncertain lasting_preference, biographical, and
    top social-reputation current_state memories for impact hypothesis cross-referencing."""
    anchors = []
    for item in self._items.values():
        if item.status in ("active", "uncertain") and item.category in (
            "lasting_preference", "biographical"
        ):
            anchors.append(item.content)

    # Also include up to 3 current_state memories most relevant to social role/reputation.
    # These are missed by default anchors but can invalidate just as strongly
    # (e.g., "People trust me with confidential info" is current_state, not lasting_preference).
    if embedding is not None:
        social_candidates = [
            item for item in self._items.values()
            if item.status in ("active", "uncertain") and item.category == "current_state"
        ]
        if social_candidates:
            SOCIAL_REPUTATION_PROBE = (
                "user's social role, reputation, trust level, standing in a group "
                "or community, how others perceive or treat the user socially"
            )
            ranked = embedding.rank(
                query_text=SOCIAL_REPUTATION_PROBE,
                candidates=social_candidates,
                text_getter=lambda item: item.content,
                top_k=3,
            )
            for r in ranked:
                anchors.append(r["item"].content)

    return anchors
```

**修改位置 2：** `write/new_writer.py`，`process_session()` 方法，第 477 行附近：

```python
# 原代码：
preference_anchors = self.store.get_preference_anchors()

# 修改后：
preference_anchors = self.store.get_preference_anchors(embedding=self.embedding)
```

---

## P9：RC-I — Embedding 检索语义鸿沟

### 根因

查询 "buy a custom strap that only fits what I'm using right now" 与相关记忆 "watch sent to repair kiosk for screen repair" 之间没有共同词汇，embedding 距离过大，top-8 检索完全遗漏相关记忆。

### 可泛化原则

> 查询表达的是**意图**（"买配件"），相关记忆表达的是**设备状态**（"设备维修中"）。两者属于不同的语义空间，但通过"当前设备"这一中间实体相连。可以用 global_impression 中的**实体信息**（当前拥有的设备/物品）作为补充检索向量，弥补查询与记忆之间的语义鸿沟。

### 设计约束

1. 不能在每次查询时增加完整的逆向假设生成（Q3 完整版），成本太高。
2. 只在**第一轮 premise_safe=True** 时触发第二轮，避免影响大多数正常查询的延迟。
3. 实体提取用 global_impression 做轻量 LLM 调用（小 prompt，少输入）。

---

### 初稿 P9-v0

在 `answer_query()` 里，当第一轮 premise_safe=True 时，触发基于 global_impression 实体的第二轮检索和 premise_check。

---

### 攻击 P9-v0

**攻击 I-1（global_impression 未包含相关实体）**

> global_impression 的 [HABITS] 段写了 "wears smartwatch"（泛称），但没有提到 "watch sent for repair"（最新状态）。

impression_update 只在有 stale 事件时触发（现有逻辑）。如果 "watch sent for repair" 被存为 active 的 recent_change 但没有触发任何 stale，impression 可能未更新。

反驳：即使 impression 没有更新，impression 里的 "wears smartwatch" 也足以提示第二轮检索去查找 "watch" 相关记忆——而 "watch sent for repair" 正是 "watch" 相关记忆，会在第二轮中被检索到。

**攻击 I-2（实体提取又是一次 LLM 调用，成本增加）**

第二轮需要调用 LLM 从 impression 中提取实体，增加约 1-2 次 LLM 调用。

反驳：这只在 premise_safe=True 时触发，且 STALE benchmark 的 dim3 失败案例 RC-I 目前只有 0312 一个明确的样本。相对于 P1-P8 的价值，P9 的 ROI 较低，但实施成本可控。

可以进一步减少成本：不用 LLM 提取实体，而是从 global_impression 里用正则/简单规则提取名词实体（设备类、物品类关键词），直接作为检索词。

**攻击 I-3（第二轮 premise_check 产生假阳性）**

> global_impression 中提到 "wears a fitness tracker"。
> 第二轮检索 "fitness tracker" → 找到记忆 "user's fitness tracker records 8000 steps per day"（active，无问题）。
> 第二轮 premise_check 收到：active = {"fitness tracker records 8000 steps"}, stale = {}, 查询 = "buy a strap that fits what I'm using"。
> premise_check 认为：fitness tracker 是 active 的，strap 可能是为 fitness tracker 买的，premise_safe=True。

这个案例通过：第二轮 premise_safe=True，与第一轮一致，不改变结果。不产生假阳性。

**攻击 I-4（自造，最严格：第二轮说 False，但第一轮是对的）**

> 查询："Should I buy travel insurance?"
> 第一轮 premise_safe=True（合理，无 stale 信息）
> global_impression 提取实体：当前有旅游计划（"planning a trip to Japan"）
> 第二轮检索 "Japan trip" → 找到 uncertain 记忆 "Japan trip may be cancelled due to scheduling conflict"
> 第二轮 premise_check 返回 premise_safe=False（"travel plans uncertain"）

这产生了一个正确的警告：如果旅行计划可能取消，购买旅行保险的前提可能不成立。这不是假阳性，是合理的判断。

> 但如果 uncertain 记忆是错的（比如 abductive_judgment 误标了 uncertain），这个第二轮会错误地说 premise_safe=False。

反驳：uncertain 记忆的存在本身就暗示不确定性。premise_check 说"travel plans uncertain"不是错误，而是忠实地反映了记忆状态。answer_gen 会据此给出适当的保守建议（"如果确认出行，建议购买"）。

---

### 最终版 P9

**修改位置：** `query/new_engine.py`，`answer_query()` 方法

```python
def answer_query(self, *, query_label: str, query_text: str) -> Dict[str, Any]:
    retrieved = self._retrieve_for_query(query_text)
    active_items = retrieved["active"]
    uncertain_items = retrieved["uncertain"]
    stale_items = retrieved["stale"]

    profile_summary = self.store.get_global_impression().content or ""

    premise_result = self._check_premise(
        query_text, active_items, uncertain_items, stale_items,
        query_label=query_label,
    )

    # Second-pass: if first pass says safe and we have a profile, do entity-expanded retrieval.
    # Addresses embedding semantic gap (RC-I): queries about purchasing intent / plans
    # may not lexically match device-status memories, but shared entities from impression bridge them.
    if premise_result.get("premise_safe", True) and profile_summary:
        expanded_result = self._try_expanded_retrieval(
            query_text, profile_summary, query_label
        )
        if expanded_result is not None:
            # Second pass found a potential issue — use it
            premise_result = expanded_result["premise_result"]
            active_items = expanded_result["active_items"]
            uncertain_items = expanded_result["uncertain_items"]
            stale_items = expanded_result["stale_items"]

    answer_result = self._generate_answer(
        query_text, active_items, uncertain_items, stale_items,
        premise_result, profile_summary,
        query_label=query_label,
    )
    # ... rest unchanged

def _try_expanded_retrieval(
    self,
    query_text: str,
    profile_summary: str,
    query_label: str,
) -> Optional[Dict[str, Any]]:
    """
    Lightweight second-pass retrieval using entity keywords extracted from
    global_impression. Only called when first-pass premise_safe=True.
    Returns None if no additional signal is found.
    """
    # Extract up to 5 current-possession/device/activity keywords from impression
    # using a compact LLM call.
    entity_result = self._safe_call_json_q(
        "Extract up to 5 specific current possessions, devices, or ongoing activities "
        "from the user profile summary. Return only concrete nouns or short phrases "
        "(e.g., 'smartwatch', 'guitar lessons', 'lease in Berlin'). "
        "Output JSON only: {\"entities\": [\"entity1\", \"entity2\"]}",
        f"User profile summary:\n{profile_summary}",
        phase="entity_extraction",
        query_label=query_label,
    )
    entities = entity_result.get("entities", [])
    if not entities or not isinstance(entities, list):
        return None

    # Run an additional embedding search for each entity
    cfg = getattr(self, "thresholds", None)
    top_k = getattr(cfg, "retrieval_top_k", 8) if cfg else 8

    seen_ids = {item.item_id for item in
                (self.store.get_active_items() + self.store.get_uncertain_items()
                 + self.store.get_stale_items())}  # already retrieved in pass 1

    extra_active, extra_uncertain, extra_stale = [], [], []
    new_seen = set()

    for entity in entities[:5]:
        for status, bucket in [("active", extra_active),
                                ("uncertain", extra_uncertain),
                                ("stale", extra_stale)]:
            hits = self.store.search_by_embedding(
                query_text=entity,
                embedding=self.embedding,
                top_k=3,
                status_filter=[status],
            )
            for item in hits:
                if item.item_id not in seen_ids and item.item_id not in new_seen:
                    new_seen.add(item.item_id)
                    bucket.append(item)

    if not extra_active and not extra_uncertain and not extra_stale:
        return None  # No new memories found, skip second pass

    # Merge: original + new, cap total at 12
    merged_active = (self.store.search_by_embedding(
        query_text=query_text, embedding=self.embedding,
        top_k=top_k, status_filter=["active"]) + extra_active)[:12]
    merged_uncertain = (self.store.search_by_embedding(
        query_text=query_text, embedding=self.embedding,
        top_k=top_k, status_filter=["uncertain"]) + extra_uncertain)[:12]
    merged_stale = self.store.get_stale_items()  # already full-pass for stale

    # Deduplicate
    seen2 = set()
    def dedup(lst):
        out = []
        for x in lst:
            if x.item_id not in seen2:
                seen2.add(x.item_id)
                out.append(x)
        return out
    merged_active = dedup(merged_active)
    merged_uncertain = dedup(merged_uncertain)

    premise2 = self._check_premise(
        query_text, merged_active, merged_uncertain, merged_stale,
        query_label=query_label + "_pass2",
    )

    if premise2.get("premise_safe", True):
        return None  # Second pass also says safe — no change needed

    # Second pass found something first pass missed
    return {
        "premise_result": premise2,
        "active_items": merged_active,
        "uncertain_items": merged_uncertain,
        "stale_items": merged_stale,
    }
```

---

## 方案间依赖关系与实施顺序

```
P1 (RC-G)  ──────────────────────────────┐
                                          ▼
P7 (RC-H)  需要 P1 提取出 M_new 后才有 hypothesis target

P4 (RC-D)  ──────────────────────────────┐
                                          ▼
P6 (RC-B)  correction 准确后才能安全地让它优先于 profile_summary

P5 (RC-E)  ──────────────────────────────┐
                                          ▼
P8 (RC-F)  P5 提取更好的 current_state 内容，P8 让这些内容进入 anchors

P2 (RC-A)  独立，最先实施，为 P6 减少依赖风险
P3 (RC-C)  独立，最先实施
P9 (RC-I)  独立，最后实施（仅影响单一 dim3 样本，优先级低）
```

**推荐实施顺序：**
1. `P1 + P2 + P3 + P4 + P5`（五处纯提示词改动，风险最低，可一次提交）
2. 冒烟测试 3 个样本（选 0266/0399/0211）
3. `P6`（answer_gen 调整，依赖 P4 已稳定）
4. `P7`（impact_hypothesis 扩展）
5. `P8`（代码改动，store + writer）
6. 完整运行（T1/T2 新 30+30 样本）
7. `P9`（可选，用于后续迭代）

---

## 各 RC 修复覆盖验证

| RC | 原失败样本 | 修复方案 | 修复链条 |
|----|-----------|---------|---------|
| RC-A | 0211, 0239, 0013 | P2 | [STATUS] 重写而非追加 |
| RC-B | 0211 dim1 | P6 (after P4) | correction 优先于 profile_summary |
| RC-C | 0266, 0226 dim2 | P3 | outdated_facts → 直接依赖 → safe=False |
| RC-D | 0223, 0013 | P4 | correction 引用 active 记忆，禁照搬 stale_reason |
| RC-E | 0059, 0274 | P5 | 提取行为事实而非情绪反应 |
| RC-F | 0059 | P8 | social current_state 进 anchors |
| RC-G | 0399, 0321, 0240, 0382 | P1 | statement 粒度过滤，不过滤整个 turn |
| RC-H | 0240, 0274 | P7 (+ P1 for 0240) | 官方手续隐含地位变更推断 |
| RC-I | 0312 dim3 | P9 | 实体扩展二次检索 |

---

## 尚存的已知局限（本次不修）

1. **impression_update 触发频率**：只在有 stale 标记时更新。新增 current_state/lasting_preference 记忆不触发 impression 更新，导致 global_impression 可能落后于实际状态。P8 通过 anchors 部分弥补（不依赖 impression），但未根治。根治需要修改 `_should_update_impression()` 逻辑，成本/收益比有待评估。

2. **P7 的 T2 间接链 上限**：P7 能帮助 1-2 跳的领域推断，但更长的链（3+ 跳）仍无法覆盖。这是 impact_hypothesis 的结构性限制。

3. **stale_reason 质量**：P4 阻止了 stale_reason 被照搬进 correction，但 stale_reason 本身仍然是不精确的推断链。这影响调试可读性，但不再影响最终答案。

4. **RC-I 的根本原因**：embedding 模型本身的语义能力限制（"strap for what I'm using" ≠ "watch repair"）。P9 通过扩展检索来绕开这个限制，但未从根本上解决。更好的解法（query rewriting at write time、memory annotation）留待后续迭代。
