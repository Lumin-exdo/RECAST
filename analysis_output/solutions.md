# 解决方案：NewMem Pipeline 失败修复

> 基于 full-mode eval 失败分析，对应 `failure_analysis.md` 中的 RC-1 至 RC-5。
> 所有修改集中在两个文件：`prompt_lib/new_templates.py` 和 `write/new_writer.py`，
> 查询侧在 `query/new_engine.py`。

---

## 快速对照表

| 修复编号 | 解决的 RC | 修改位置 | 难度 | 预期覆盖失败样本 |
|---------|-----------|---------|------|----------------|
| Fix 1 | RC-1 | `new_templates.py` TRIGGER_GATE_PROMPT | 低（改 prompt） | T1-2, T1-3, T2-1, T2-2 (M_old 未存) |
| Fix 2 | RC-2 | `new_writer.py` + `new_templates.py` | 中（改代码+prompt） | T1-1, T2-5 (M_new 被丢弃) |
| Fix 3 | RC-3 | `new_templates.py` IMPACT_HYPOTHESIS_PROMPT | 低（改 prompt） | T2-1, T2-2, T2-3, T2-4 (间接推理失败) |
| Fix 4 | RC-4 | `new_templates.py` PREMISE_CHECK_PROMPT + `query/new_engine.py` | 中 | 所有 SAFE 误判（8 个样本受影响） |
| Fix 5 | RC-5 | `new_templates.py` ANSWER_GENERATION_PROMPT | 低（改 prompt） | T1-4, T2-3, T2-4, T2-2 dim3 |
| Fix 6 | RC-1/RC-2 | `write/new_writer.py` `_should_update_impression` | 低（改代码） | 长期收益：impression 与 active store 保持同步 |

---

## Fix 1：Trigger_gate prompt 改造（RC-1）

### 问题所在

**文件**：`prompt_lib/new_templates.py` 第 76–96 行  
**代码**：`write/new_writer.py` 第 76–81 行的 `_check_trigger_gate`

trigger_gate 只收到一个 `{global_impression}`（500 字符的全局摘要），决策标准是：

```
should_trigger=true if the statement might change, contradict,
or make obsolete any existing memory about the user
```

这是 **冲突检测器**，不是 **新信息累积器**。当 profile 里已有其他话题的记忆（如"用户有一只狗"），而新进来一条"我的投资主要是指数基金"时，gate 会问："这条话有没有让现有记忆失效？" — 没有，所以丢弃。这就是 M_old 在第一次出现时被丢弃的根本原因。

失败样本：
- `5f77adc7` session 10："I usually wait until my current device completely stops meeting my needs" → 丢弃（reason: "does not contradict or change any existing memory about the user"）
- `5308c7fd` session 14："The majority of my holdings are situated in broad market index funds" → 丢弃
- `3e3af301` session 7："I've kept a backup of all the license keys" → 丢弃

### 修复方案

**在 `new_templates.py` 中修改 `TRIGGER_GATE_PROMPT`：**

```python
# ──────────────────────────────────────────────────────────────
# 修改前（第 76–96 行）
# ──────────────────────────────────────────────────────────────
TRIGGER_GATE_PROMPT = """Decide whether this user statement might require updating or invalidating existing memory about the user.

Statement: {statement}

Current user profile summary:
{global_impression}

Output JSON only:
{
  "should_trigger": true,
  "reason": "one sentence explanation"
}

Rules:
- should_trigger=true if the statement might change, contradict, or make obsolete any existing memory about the user
- should_trigger=true if the profile is empty (any factual personal statement is worth storing)
- should_trigger=false only for clearly irrelevant statements: weather comments, external world facts, task-only content with no personal state implication
- Common triggers: change of location, change of job/employer, change of relationship status, change of health, change of habits, change of living situation
- Even indirect statements can trigger: "adapting to life in a new city" implies a location change without naming the city
- Be generous with triggering — a false negative (missing an important update) is worse than a false positive
"""
```

```python
# ──────────────────────────────────────────────────────────────
# 修改后
# ──────────────────────────────────────────────────────────────
TRIGGER_GATE_PROMPT = """Decide whether this user statement should be stored in the user's memory profile.

Statement: {statement}
Statement category: {category}

Current user profile summary (INCOMPLETE — captures recent highlights only, not every stored fact):
{global_impression}

Output JSON only:
{
  "should_trigger": true,
  "reason": "one sentence explanation"
}

Rules (apply the FIRST rule that matches):
1. should_trigger=true if the statement introduces a specific personal attribute
   (preference, habit, belief, portfolio, routine, possession, identity) —
   even if it does not conflict with anything in the summary.
   The summary is incomplete; absence of a topic does NOT mean the fact is already stored.
2. should_trigger=true if the statement might change, contradict, or make obsolete any
   existing memory about the user (location, job, relationship, health, habits, living situation).
3. should_trigger=true if the profile is empty.
4. should_trigger=false ONLY for: weather/nature observations, external world news,
   one-shot tasks with no personal state implication, generic filler.

Key: a false negative (failing to store a real personal fact) is far worse than
a false positive (storing a mildly redundant fact). When in doubt, trigger=true.
"""
```

**在 `new_writer.py` 中修改 `_check_trigger_gate`，传入 category：**

```python
# ──────────────────────────────────────────────────────────────
# 修改前（第 76–81 行）
# ──────────────────────────────────────────────────────────────
def _check_trigger_gate(self, statement: str, global_impression: GlobalImpression) -> Dict[str, Any]:
    prompt = TRIGGER_GATE_PROMPT.replace("{statement}", statement).replace(
        "{global_impression}", global_impression.content or "(no profile yet — user's first sessions)"
    )
    result = self._safe_call_json(prompt, "Assess trigger.", phase="trigger_gate")
    return result
```

```python
# ──────────────────────────────────────────────────────────────
# 修改后
# ──────────────────────────────────────────────────────────────
def _check_trigger_gate(
    self,
    statement: str,
    global_impression: GlobalImpression,
    *,
    category: str = "",
) -> Dict[str, Any]:
    prompt = (
        TRIGGER_GATE_PROMPT
        .replace("{statement}", statement)
        .replace("{category}", category or "unspecified")
        .replace("{global_impression}", global_impression.content or "(no profile yet — user's first sessions)")
    )
    result = self._safe_call_json(prompt, "Assess trigger.", phase="trigger_gate")
    return result
```

**在 `process_session` 中（第 452–462 行），把 `category` 传给 gate：**

```python
# ──────────────────────────────────────────────────────────────
# 修改前（第 455–463 行）
# ──────────────────────────────────────────────────────────────
if len(factual_indices) > 1:
    with ThreadPoolExecutor(max_workers=min(len(factual_indices), max_workers)) as ex:
        gate_list = list(ex.map(
            lambda i: self._check_trigger_gate(statements[i]["text"], impression),
            factual_indices,
        ))
else:
    gate_list = [self._check_trigger_gate(statements[i]["text"], impression) for i in factual_indices]
```

```python
# ──────────────────────────────────────────────────────────────
# 修改后
# ──────────────────────────────────────────────────────────────
def _gate_one(i: int) -> Dict[str, Any]:
    return self._check_trigger_gate(
        statements[i]["text"],
        impression,
        category=statements[i].get("category", ""),
    )

if len(factual_indices) > 1:
    with ThreadPoolExecutor(max_workers=min(len(factual_indices), max_workers)) as ex:
        gate_list = list(ex.map(_gate_one, factual_indices))
else:
    gate_list = [_gate_one(i) for i in factual_indices]
```

---

## Fix 2：Trigger_gate 加入向量检索上下文（RC-2）

### 问题所在

**文件**：`write/new_writer.py` 第 76–81 行

RC-2 的失败更微妙：M_old 已经存在于 active store（如 m_00005："I tend to evaluate each issue on its own merits"），但 M_new 来临时，trigger_gate 把 M_new 与全局摘要（500 字，只含摘要性描述）对比，没有看到具体的 m_00005 条目，于是漏判。

失败样本：
- `7eb14667` session 36："I'll be backing the same ticket all the way down the ballot" → 被丢弃，reason: "does not contradict or update any existing memories about job, location, or reading habits"（gate 只看到了摘要里的"job/location/reading"，没有看到 m_00005 的具体内容）
- `0c0086f5` session 13：M_old 被丢，因为 gate 比对的摘要里没有"morning focus block"的具体信息

### 修复方案

在 `_check_trigger_gate` 里先做向量检索，把最相关的 5 条 active 记忆传给 gate。

**在 `new_templates.py` 中增加新版 prompt 变量：**

```python
# ──────────────────────────────────────────────────────────────
# 修改后的 TRIGGER_GATE_PROMPT（在 Fix 1 基础上继续扩展）
# ──────────────────────────────────────────────────────────────
TRIGGER_GATE_PROMPT = """Decide whether this user statement should be stored in the user's memory profile.

Statement: {statement}
Statement category: {category}

Current user profile summary (INCOMPLETE — captures recent highlights only):
{global_impression}

Most semantically related existing memories (top matches from full profile):
{retrieved_context}

Output JSON only:
{{
  "should_trigger": true,
  "reason": "one sentence explanation"
}}

Rules (apply the FIRST rule that matches):
1. should_trigger=true if the statement introduces a specific personal attribute
   (preference, habit, belief, portfolio, routine, possession, identity) not yet captured.
   The summary is incomplete; also check the retrieved memories above.
2. should_trigger=true if the statement contradicts or updates any retrieved memory above,
   even if the connection is indirect (e.g. a new voting behavior contradicts
   'evaluates issues independently'; an early bedtime contradicts 'frequent night socializing').
3. should_trigger=true if the profile is empty.
4. should_trigger=false ONLY for: weather/nature observations, external world news,
   one-shot tasks, generic filler.

When in doubt: trigger=true.
"""
```

**在 `new_writer.py` 中修改 `_check_trigger_gate`：**

```python
# ──────────────────────────────────────────────────────────────
# 修改后（在 Fix 1 基础上再改）
# ──────────────────────────────────────────────────────────────
def _check_trigger_gate(
    self,
    statement: str,
    global_impression: GlobalImpression,
    *,
    category: str = "",
) -> Dict[str, Any]:
    # 向量检索最相关的 5 条 active 记忆，作为额外上下文
    nearby: list = []
    if self.store.get_active_items():
        nearby = self.store.search_by_embedding(
            query_text=statement,
            embedding=self.embedding,
            top_k=5,
            status_filter=["active", "uncertain"],
        )
    retrieved_context = (
        "\n".join(f"- [{item.item_id}] {item.content}" for item in nearby)
        or "(no related memories found yet)"
    )

    prompt = (
        TRIGGER_GATE_PROMPT
        .replace("{statement}", statement)
        .replace("{category}", category or "unspecified")
        .replace("{global_impression}", global_impression.content or "(no profile yet)")
        .replace("{retrieved_context}", retrieved_context)
    )
    result = self._safe_call_json(prompt, "Assess trigger.", phase="trigger_gate")
    return result
```

> **性能说明**：gate 本来就对每个 factual statement 做一次 LLM 调用，这里只额外增加一次内存内的 embedding 检索（无 LLM 调用），开销极小。parallel gate 已经在 ThreadPoolExecutor 里运行，embedding 检索是只读的，并发安全。

---

## Fix 3：Impact_hypothesis 加入 T2 推理链示例（RC-3）

### 问题所在

**文件**：`prompt_lib/new_templates.py` 第 99–146 行

T2 失败的核心是：M_new 改变了属性 A，A 导致 B 失效，但系统没做 A→B 的前向推理。impact_hypothesis prompt 虽然要求"至少 2 个 non-obvious, multi-hop hypotheses"，但示例全是政治/公民身份类的，缺少**时间安排/作息**类推理链。

失败样本：
- `3e3af301`："做了全盘重装" → 应推断出"本地备份不复存在"
- `e48c9b37`："凌晨班" → 应推断出"早睡早起" → "晚间社交不再可行"
- `7eb14667`（已有触发问题，但即使触发也需要正确推理）

### 修复方案

**在 `new_templates.py` 中修改 `IMPACT_HYPOTHESIS_PROMPT`，补充 T2 相关示例：**

```python
# ──────────────────────────────────────────────────────────────
# 在现有 IMPACT_HYPOTHESIS_PROMPT 的 Rules 区块末尾追加（约 146 行后）
# ──────────────────────────────────────────────────────────────

# 原 Rules 末尾：
# - Think about SYSTEMIC CONSEQUENCES, not just the immediate event:
#   ...
#   parka from back of closet → cold climate → contradicts any stored belief about warm/humid climate

# 追加以下示例（替换最后几行或扩展 Rules 区块）：
```

将第 142–146 行替换为：

```
- Think about SYSTEMIC CONSEQUENCES, not just the immediate event:
  inheriting property from a family member → now a homeowner → "user rents their home" is now wrong
  federal job offer → requires citizenship → "user is not pursuing citizenship" is now wrong
  parka from back of closet → cold climate → contradicts any stored belief about warm/humid climate
  full system wipe and reinstall → all local files gone → "user has locally archived X" is now wrong
  early-morning work shifts / pre-dawn schedule → early bedtime → "user does late-night activities" is now wrong
  early bedtime / phone off after dinner → unavailable in evenings → "user has frequent evening social plans" is now wrong
  moved to a new city / signed new lease → new time zone, new daily rhythm → any stored timezone or schedule is now wrong
  bedroom furniture change requiring full clearance → nightstand/side area cleared → "user keeps items on nightstand" is now wrong
  signed up for developer beta → actively values new features now → "user waits until device fully breaks before upgrading" is now wrong
  switched to variable/gig income → "user has stable monthly salary" is now wrong
"""
```

> **说明**：这些示例覆盖了全部 T2 失败样本的推理模式，让 LLM 在生成 hypotheses 时有更多相似结构可以参照。

---

## Fix 4：Premise_check 双向推理 + 扩大 stale 检索范围（RC-4）

### 问题所在

**文件**：`prompt_lib/new_templates.py` 第 222–258 行  
**代码**：`query/new_engine.py` 第 24–46 行、第 111–153 行

**问题 A（最严重）**：premise_check prompt 的规则：

```
If no relevant memories exist at all, premise_safe=true
```

当 M_old 从未存储（trigger_gate 丢弃），stale 列表为空，premise_check 只能说 SAFE，答题器接受错误前提。

**问题 B（T2）**：即使 M_new 已存储为 active memory（如"凌晨班"），premise_check 只比较 stale 列表与 query，不做主动推理：active memory 里的"凌晨班"并不自动产生"晚间不可用"的推断。

**问题 C（stale 检索范围）**：`_retrieve_for_query` 对 stale items 也只搜 top_k=8。如果 M_old 被标记为 stale 但 query 和它语义距离较远，它可能进不了 top_k，premise_check 看不到它。

### 修复方案

#### A. 修改 `new_templates.py` PREMISE_CHECK_PROMPT

```python
# ──────────────────────────────────────────────────────────────
# 修改前（第 222–258 行）
# ──────────────────────────────────────────────────────────────
PREMISE_CHECK_PROMPT = """Check whether a user's question contains implicit assumptions that may be outdated given what we know.

User question: {query_text}

Current active memories (what we know to be currently true):
{active_memories}

Recently stale memories (what USED TO BE true but has changed):
{stale_memories}

...

Rules:
- premise_safe=true: active memories confirm the question's assumptions OR no assumptions are contradicted
- premise_safe=false: a stale memory shows the question is built on outdated information
- correction should be specific: name what changed, not just "things have changed"
- If no relevant memories exist at all, premise_safe=true (we don't know enough to say it's unsafe)
...
"""
```

```python
# ──────────────────────────────────────────────────────────────
# 修改后
# ──────────────────────────────────────────────────────────────
PREMISE_CHECK_PROMPT = """Check whether a user's question contains implicit assumptions that conflict with what we currently know.

User question: {query_text}

Current active memories (confirmed as currently true):
{active_memories}

Stale memories (USED TO BE true but has changed):
{stale_memories}

Identify what the question implicitly assumes about the user's current state.
Then check BOTH of the following:
  (1) Whether any STALE memory directly contradicts the assumption.
  (2) Whether any ACTIVE memory logically IMPLIES that the assumption is currently impossible
      (e.g. active: "pre-dawn work shifts, in bed by 9pm" → implies "late-night activities not feasible";
       active: "did a full system wipe with only cloud restore" → implies "local-only backups are gone";
       active: "will vote straight party ticket" → implies "no longer evaluates issues independently").

Output JSON only:
{
  "presuppositions": [
    "what the question implicitly assumes"
  ],
  "premise_safe": true,
  "correction": "if premise_safe=false: what the user should know before we answer (one sentence)",
  "usable_active_facts": [
    "active memory contents directly relevant to answering"
  ],
  "outdated_facts": [
    "stale memory contents that the question may be assuming are still true"
  ]
}

Rules:
- premise_safe=false if: (a) a stale memory directly contradicts an assumption, OR
                          (b) active memories together logically IMPLY the assumption is currently false
- premise_safe=true only if: active memories support the assumption, OR genuinely no information either way
- correction must name WHAT changed and WHY it matters — never just "things have changed"
- If no relevant memories exist and nothing can be logically inferred, premise_safe=true
"""
```

#### B. 修改 `query/new_engine.py`：stale 检索改为全量

当前代码（第 40–45 行）对 stale 也只取 top_k=8。若 M_old 被标记为 stale 但与 query 语义不近，可能漏检。

```python
# ──────────────────────────────────────────────────────────────
# 修改前（第 24–46 行）
# ──────────────────────────────────────────────────────────────
def _retrieve_for_query(self, query_text: str) -> Dict[str, List[MemoryItem]]:
    cfg = getattr(self, "thresholds", None)
    top_k = getattr(cfg, "retrieval_top_k", 8) if cfg else 8

    active = self.store.search_by_embedding(
        query_text=query_text, embedding=self.embedding,
        top_k=top_k, status_filter=["active"],
    )
    uncertain = self.store.search_by_embedding(
        query_text=query_text, embedding=self.embedding,
        top_k=top_k, status_filter=["uncertain"],
    )
    stale = self.store.search_by_embedding(
        query_text=query_text, embedding=self.embedding,
        top_k=top_k, status_filter=["stale"],
    )
    return {"active": active, "uncertain": uncertain, "stale": stale}
```

```python
# ──────────────────────────────────────────────────────────────
# 修改后
# ──────────────────────────────────────────────────────────────
def _retrieve_for_query(self, query_text: str) -> Dict[str, List[MemoryItem]]:
    cfg = getattr(self, "thresholds", None)
    top_k = getattr(cfg, "retrieval_top_k", 8) if cfg else 8

    active = self.store.search_by_embedding(
        query_text=query_text, embedding=self.embedding,
        top_k=top_k, status_filter=["active"],
    )
    uncertain = self.store.search_by_embedding(
        query_text=query_text, embedding=self.embedding,
        top_k=top_k, status_filter=["uncertain"],
    )
    # stale 条目数量通常远少于 active，全量传入 premise_check
    # 避免因语义距离远而漏检关键 stale 记忆（如 M_old 被丢弃后无法被检索）
    all_stale = self.store.get_stale_items()
    if len(all_stale) <= 30:
        stale = all_stale  # 数量少时直接全量
    else:
        stale = self.store.search_by_embedding(
            query_text=query_text, embedding=self.embedding,
            top_k=top_k * 2, status_filter=["stale"],  # 至少扩大检索范围
        )
    return {"active": active, "uncertain": uncertain, "stale": stale}
```

---

## Fix 5：Answer generation 防止幻觉和过度保守（RC-5）

### 问题所在

**文件**：`prompt_lib/new_templates.py` 第 295–326 行

四种子类型（分析文档中有详述）：
- **Type A**（ac6b1ba4 dim3）：memory 正确但回答要求更多信息，不给出具体方案
- **Type B**（e48c9b37 dim3）：把 active memory 当成过去时态（"pre-dawn shifts are a thing of the past"）
- **Type C**（f42cf11f dim3）：先说 Murphy 床 → 再假设 nightstand 仍存在，自相矛盾
- **Type D**（ef90c67e dim3）：凭空捏造根本不存在的 stale signal

### 修复方案

**在 `new_templates.py` 中修改 `ANSWER_GENERATION_PROMPT` 的 Rules 部分：**

```python
# ──────────────────────────────────────────────────────────────
# 修改前（第 295–326 行）
# ──────────────────────────────────────────────────────────────
ANSWER_GENERATION_PROMPT = """Answer the user's question using available memory about them.
...
Rules:
- If premise_safe=false: START the answer with the correction. Then give a helpful response based on what we do know.
  Example: "Actually, [correction]. Given that, [helpful response]."
- If premise_safe=true: Answer directly using active facts
- When answering, prioritize active facts over uncertain facts
- Never present stale information as currently true
- If we lack sufficient information to answer well, say so honestly and ask a clarifying question
- Keep the answer conversational and natural, as if you know this user well
- Be specific and practical — use the actual facts we have, don't give generic answers
"""
```

```python
# ──────────────────────────────────────────────────────────────
# 修改后
# ──────────────────────────────────────────────────────────────
ANSWER_GENERATION_PROMPT = """Answer the user's question using available memory about them.

User question: {query_text}

Relevant current facts (confirmed active memories):
{active_facts}

Relevant uncertain facts (may be outdated):
{uncertain_facts}

Outdated information to be aware of (stale memories):
{stale_facts}

Premise assessment:
- Premise is safe: {premise_safe}
- Correction needed: {correction}

Output JSON only:
{{
  "answer": "your response to the user's question"
}}

Rules:
- If premise_safe=false: START the answer with the correction, then give a concrete helpful response.
  Example: "Actually, [correction]. Given that, [specific helpful answer based on corrected state]."
  Do NOT stop after the correction — always follow up with a specific, actionable response.
  Do NOT ask for more information unless a genuinely necessary specific fact is missing.

- If premise_safe=true: Answer directly using active facts.

- Active facts = CURRENTLY TRUE. Never describe an active fact as "in the past", "outdated",
  "no longer applicable", or "a thing of the past." Only stale memories are past.

- Never invent or imply that a habit or behavior has ended unless a stale memory says so.
  If something is in the active list, treat it as ongoing.

- Your answer must be internally consistent. Do not assert something in one sentence
  and contradict it in the next.

- Never present stale information as currently true.

- Be specific and practical — use actual facts, not generic advice.
"""
```

---

## Fix 6：Impression 更新条件放宽（RC-1/RC-2 长期收益）

### 问题所在

**文件**：`write/new_writer.py` 第 550–558 行

```python
if self._should_update_impression(all_judgment_logs) or (
    processed_statements and not self.store.get_global_impression().content
):
    self._update_global_impression(...)
```

`_should_update_impression` 只在有条目被标记为 stale 时返回 True（第 337–339 行）：

```python
def _should_update_impression(self, judgment_logs: List[Dict[str, Any]]) -> bool:
    core_change_actions = {"marked_stale", "pool_triggered_stale"}
    return any(log.get("action") in core_change_actions for log in judgment_logs)
```

结果：新条目被添加到 active store，但 impression 不更新。trigger_gate 在后续 session 仍然基于旧的 impression 做决策，可能再次丢弃同话题的语句。

### 修复方案

```python
# ──────────────────────────────────────────────────────────────
# 修改前（第 550–558 行）
# ──────────────────────────────────────────────────────────────
if self._should_update_impression(all_judgment_logs) or (
    processed_statements and not self.store.get_global_impression().content
):
    self._update_global_impression(
        processed_statements,
        all_judgment_logs,
        session_index=session_index,
        session_time=session_time,
    )
```

```python
# ──────────────────────────────────────────────────────────────
# 修改后
# ──────────────────────────────────────────────────────────────
# 满足以下任一条件就更新 impression：
# 1. 有条目被标记为 stale（原有逻辑）
# 2. profile 尚无内容（原有逻辑）
# 3. 本 session 存入了新的 lasting_preference / current_state / biographical 条目
#    （这些类别代表用户基础状态，impression 应及时反映）
new_item_categories = {
    statements[i].get("category", "") for i in triggered_indices
}
has_significant_new_facts = bool(
    new_item_categories & {"lasting_preference", "current_state", "biographical"}
)

if (
    self._should_update_impression(all_judgment_logs)
    or (processed_statements and not self.store.get_global_impression().content)
    or has_significant_new_facts
):
    self._update_global_impression(
        processed_statements,
        all_judgment_logs,
        session_index=session_index,
        session_time=session_time,
    )
```

> **性能说明**：每次 impression 更新是 1 次 LLM 调用。在当前 eval（50 sessions/sample）下，最坏情况是每 session 都更新，增加 50 次调用。可以通过增加冷却窗口（如"上次更新后至少经过 N 个 session 才再更新"）来控制成本，但对 eval 准确性影响极小。

---

## 修改汇总与实施顺序

**建议实施顺序（按性价比）：**

1. **Fix 1** — trigger_gate prompt 增加"新属性存储"规则 + 传入 category。**最高收益、最低风险。** 覆盖所有 RC-1 样本。仅改 prompt 和两处函数签名。

2. **Fix 5** — answer_generation 增加三条防幻觉规则。**改 prompt 即可，零代码风险。** 覆盖所有 RC-5 样本。

3. **Fix 4A** — premise_check prompt 增加"active memory 可以推翻前提"规则。**改 prompt 即可。** 覆盖 T2 的 RC-4 失败。

4. **Fix 4B** — stale 检索改为全量（≤30 条时）。**一行代码改动。** 覆盖因检索范围不足导致的 RC-4 漏判。

5. **Fix 2** — trigger_gate 前置向量检索。**中等改动，显著覆盖 RC-2。** 需要在 gate 里加一次 embedding 检索，注意 prescan 阶段没有完整 memory state，只在 process_session 阶段有效。

6. **Fix 3** — impact_hypothesis 补充 T2 示例。**改 prompt 即可。** 覆盖 T2 间接推理，与 Fix 2、Fix 4 协同。

7. **Fix 6** — impression 更新条件放宽。**长期收益，本次 eval 影响有限**（eval 最后才 query，impression 精度影响的是中间 session 的 trigger_gate 决策，而不是最终答题质量）。

---

## 各根因与修复的对应关系

```
RC-1（M_old 未存）     ←── Fix 1（核心）、Fix 2（补充）、Fix 6（长期）
RC-2（M_new 被丢）     ←── Fix 1（部分）、Fix 2（核心）、Fix 3（部分）
RC-3（T2 推理链断）    ←── Fix 3（核心）、Fix 4A（query 侧补充）
RC-4（premise 假安全） ←── Fix 4A（prompt）、Fix 4B（stale 全量检索）
RC-5（答案生成失败）   ←── Fix 5（核心）
```
