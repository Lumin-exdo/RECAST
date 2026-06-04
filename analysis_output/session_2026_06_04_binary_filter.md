# 会话记录 2026-06-04：binary_filter 二分类改动与验证

---

## 一、从哪里开始：12 条变化的判断

本次对话从上一对话的上下文限额处接续。上一对话已经在 80 条 A/B 测试样本上对比了三分类和二分类 filter 提示词，得出了一版"倾向 STORE"的新 prompt。重新跑了一次测试，发现有 12 条决定和上版不同，逐条人工判断：

```
语句                                          旧→新          正确答案
"planning to register bikes with police"     SKIP→STORE     SKIP    一次性行政动作
"planning a trip to Japan"                   SKIP→STORE     SKIP    一次性出行计划
"interested in trying out a book box"        SKIP→STORE     STORE ✓ 阅读偏好信号
"plans to do stretching at 8:30pm"           SKIP→STORE     STORE ✓ 具体习惯时间点
"has done a marathon"                        STORE→SKIP     STORE   已完成的成就
"has been making chicken tacos lately"       STORE→SKIP     SKIP ✓  "lately"是短暂行为
"loving the smartwatch"                      SKIP→STORE     STORE ✓ 持久偏好
"looking for moderate trails with views"     STORE→SKIP     STORE   徒步难度偏好是持久的
"the location of the property is perfect"    SKIP→STORE     SKIP    一次性评价
"concerned about losing sports channels"     STORE→SKIP     STORE   暗示持久的体育收视习惯
"looking forward to getting back to routine" STORE→SKIP     SKIP ✓  瞬时期待
"planning a road trip to the southwest"      SKIP→STORE     SKIP    一次性旅行计划
```

计下来：12 条变化里 5 条改对了，7 条改错了。整体比上版差，但有方向性意义——"lean STORE"确实减少了"误丢持久偏好"的问题（smartwatch、stretching habit 改对了），同时也把一些一次性计划拉进来了。

两类错误的模式很清晰：
- **该 STORE 但 SKIP 的（6条）**：用户当前在做或在找某事，这件事暗示了持久兴趣——"looking for trails"其实是"喜欢徒步"，"been reading novels"是"读者身份"，"excited about orchid"是"在养兰花"。这些被当成瞬时行为过滤了。
- **该 SKIP 但 STORE 的（仍有 4 条）**：具体的一次性旅行/行政计划被当成持久兴趣存进去了。

---

## 二、提示词反复迭代的过程

### 第三版：补了两条原则之后的反弹

针对两个方向各补了一条原则：
1. "用户当前正在做/搜寻某事 → STORE（暗示持久兴趣）"
2. "只是在心里想着某事，不算在做 → 不触发规则 1"

结果变了 26 条，其中：
- 改对了 7 条（orchid、writing prompts、reading novels、moderate trails、sports channels、watching sports、marathon）
- 同时引入了新错误 ~7 条——"considering track lighting"、"considering joining reading challenge"、"thinking about starting a YouTube channel"全被"currently engaged"那条原则拉进来了

根因：新原则里"currently doing, searching for, or engaged with"范围太宽，把"脑子里想着"也算成了"currently engaged"。模型把"thinking about starting a YouTube channel"和"has been doing marathon training"混同了。

### 第四版：收窄"currently engaged"的定义

加了一条"脑子里想想不算"的说明，跑出来变了 16 条。回退 SKIP 的方向大部分对，但同时把"interested in God of War"、"interested in New Zealand"、"watching a lot of sports lately"也 SKIP 掉了——新原则的"merely thinking about X does not count"误伤了真实兴趣。

### 最终决定：彻底换方向

在这个来回中意识到：三分类的根本问题是 EMOTIONAL 这个类别本身——它的定义和 FACTUAL 在"偏好/兴趣"这条线上天然重叠，无论怎么调提示词都治不了这个边界模糊。

讨论"宁多勿少"的思路时，问了一个关键问题：如果多存了一条"planning a trip to Japan"这样的一次性计划，下游会有什么影响？

原本以为有 trigger_gate 能兜底——但读代码发现 trigger_gate 已经被删掉了（`new_writer.py` 第 459 行注释明确写了：`# ── PHASE B: all FACTUAL statements proceed directly (no trigger gate) ──`）。这个判断是错的，filter 是**唯一的质量门**。

所以换了计算：
- **漏存"interested in God of War"**：以后每次相关问题都永久失去个性化机会
- **多存"planning a trip to Japan"**：在少数旅行相关场景下带一点偏，如果后续用户说"去完了"或"取消了"，abductive judgment 会把它标为 stale；如果一直没提，它就待在 active pool 里，但语义距离远的查询不太会检索到它

前者是系统性损失，后者是局部噪音。宁多勿少的逻辑成立。

**最终提示词：把 SKIP 的条件收到最窄，只留绝对确定的三类：**

```
STORE is the default. Only SKIP when you are confident the statement contains
no personal signal whatsoever about this user's life, identity, or situation.

SKIP only when the statement is clearly one of:
  — a purely momentary feeling or mood with no factual claim about the user's
    situation ("I'm so tired today", "I love this song right now")
  — a fact about the external world with no implication for the user personally
    ("It's raining", "the game was exciting")
  — a direct request, question, or instruction to the assistant

Everything else: STORE.
```

跑出来 STORE=79，SKIP=1，只有"in the mood for something lighter to balance their TV diet"被跳过。上版判断错的（marathon、God of War、New Zealand、watching sports、short story）全部修正。代价是"planning a trip to Japan"、"planning meals for the week"、"holding a baby named Emily"也进了记忆库——在"宁多勿少+filter是唯一门"的设计哲学下，这个结果是自洽的。

---

## 三、代码改动

### commit a310935 — binary STORE/SKIP

**`prompt_lib/new_templates.py`**：113 行的三分类提示词（包含 FACTUAL/HYPOTHETICAL/EMOTIONAL 的定义、例子、边界说明）缩减为 18 行的二分类，逻辑上更简单也更稳定。

**`write/new_writer.py`** 共 5 处：
1. `_is_factual()` 的 system/user 消息结构：statement 从 system prompt 移到 user message。这是上一轮 variants AB 测试的结论——statement 在 user message 时，HYPOTHETICAL 类的准确率从 57.5% 提升到 92.5%。
2. prescan_session 里的过滤条件：`str(r.get("type", "")).strip().upper() == "FACTUAL"` → `str(r.get("decision", "")).strip().upper() == "STORE"`
3. process_session 里的过滤条件：同上
4. process_session 的 precomputed 分支 fallback 字段名：`{"type": "FACTUAL"}` → `{"decision": "STORE"}`（保持字段统一）
5. Phase D 日志里的 decision 字段读取

### commit 2086ed6 — --startup-stagger

25 个 worker 同时提交时，prescan 阶段 25×16=400 个并发 API 请求瞬间触发限速（之前运行就出现过 ECONNRESET）。在 `run_new_mem.py` 的 ThreadPoolExecutor 提交循环里加了 sleep，每个 worker 提交后等 N 秒再提交下一个。

### commit 7e6af73 — 修复 prescan 静默失败

这是本次 eval 失败后排查出来的 bug（详见第四节），改动了 3 行：
- `_extract_statements`：检测到 `_error` key 时返回 `None` 而非 `[]`
- `prescan_session`：收到 `None` 时 return `None`（触发 fallback）
- `process_session` else 分支：`or []` 防止 None 传入下游

---

## 四、binary_filter_v1 eval：一场意外

### 运行配置

25 个新样本（T1×13 + T2×12），commit 2086ed6，workers=25，--startup-stagger 3。

### 结果：Scorer 给出 16% 整体准确率

拿到结果第一反应是怀疑 binary filter 出了问题。仔细看 scorer 的 detail：

```json
{
  "dim1_eval": {
    "reasoning": "The provided response is empty. It fails to acknowledge...",
    "pass": false
  }
}
```

"The provided response is empty"——不是答错了，是答案本身是空字符串。

### 排查链

**第一步**：查 answers.json，发现 20/25 个样本的 dim1/dim2/dim3_response 全是 `""`。

**第二步**：这 20 个样本的完成时间都在 83-85 秒，而正常样本需要 1200-1700 秒。85 秒完成意味着 session phase 基本没有运行。

**第三步**：查其中一个（5308c7fd）的 trace：
```
session_elapsed_seconds_total: 0.0
call_records: 0
query_elapsed_seconds_total: 51.832
```
50 个 session 全部在 0 秒内"处理完"，没有一次 API 调用，但 query 仍然跑了 51 秒（3 个 dim，每个发了请求但答案为空）。

**第四步**：`call_records: 0` 说明 `worker_llm` 没有任何成功的 API 调用。因为 `_record_call` 只在成功后执行，失败的重试不会被记录。

**第五步**：`session_elapsed_seconds_total: 0.0` 但还跑了 50 个 session——每个 session 的 `precomputed_factual` 是 `[]`（空列表，不是 None），所以直接跳过了所有写入逻辑，几乎 0 秒完成。

**第六步**：为什么 prescan 会返回 `[]` 而不是 `None`？

```python
def prescan_session(self, session, ...):
    try:
        session_text = self._session_to_user_text(session)
        statements = self._extract_statements(session_text)
        if not statements:
            return []   # ← 这里！
        ...
    except Exception:
        return None  # 这才会触发 fallback
```

`_extract_statements` 在 API 失败时返回的是 `[]`（因为 `_safe_call_json` 捕获异常返回 `{"_error": ...}`，然后 `.get("statements", [])` 得到空列表）。prescan 拿到空列表，走 `if not statements: return []` 这条路，返回了 `[]`。

但 `[]`（空列表）和 `None` 的语义在 process_session 里完全不同：
- `precomputed_factual is None` → 走 else 分支，自己做提取
- `precomputed_factual == []` → 认为"已处理，0 条 STORE 语句"，直接跳过

所以 API 连接错误被完美地伪装成了"这个 session 没有任何值得存的语句"，完全静默。

**第七步**：为什么 20 个样本集中在启动时失败？看完成时间的分布——快完成的（83-85s）都是最早启动的，慢完成的（1200s+）是后启动的。即使有 stagger=3s，前 20 个 worker 密集启动时 prescan 的并发请求仍然在一个短窗口内爆发，触发了 ECONNRESET/ConnectionRefused。

### 修复

`_extract_statements` 在检测到 `_error` 时返回 `None`，让错误能被正确识别和处理，不再被吸收进"无语句"的语义里。

---

## 五、两轮 rerun 为什么还是失败

### rerun（workers=20，stagger=5）

write phase 已经用上了修复版代码，大部分样本有 200-300 次成功 API 调用、778-932 秒的 session 处理时间。但 query phase 全部报 `Connection error`。

查一个典型失败样本（e19d8f16）的 query log：
```json
"premise_result": {"_error": "Connection error."}
```
query phase 的两个 API 调用（premise_check + answer_generation）全部失败。答案空字符串。

原因：20 个 worker 的 session 时长相近（都在 778-932 秒），几乎同时完成 session phase，集中进入 query phase，瞬间 20×3=60 个并发请求再次打垮 API。

仅有 1 个样本（772e38fb，session 时长 912s）在这个峰值之外成功了，另外 3 个是 session 超长（1700s+）的样本，完全错开了峰值。

### r2（workers=4）

每次最多 4 个并发 worker，session 时长自然形成时差，query phase 不会集中触发。目前 3/16 完成，全部非空，无连接错误。

---

## 六、已完成样本的语义分析

### T1 样本（直接冲突，r2 前 3 个）

**e19d8f16**：M_old 10pm 睡觉，M_new 开始游泳训练后 8:30 入睡。
- dim1 ✓、dim2 ✓——识别到旧睡眠目标过时
- dim3 **有瑕疵**：回答说"no longer consistently aiming for 8:30"，但 M_new 明确 8:30 是现在的状态。记忆里的状态和事实不完全对齐，答案建立在部分错误的记忆上。

**2f07b5f8**：M_old 每周 4 次力量训练，M_new 最近只能做 2 次。
- 三维度全对，细节好（记住了"cranky shoulder"，dim3 据此建议不适合 Chest/Triceps 四分化训练）。

**316bac98**：M_old 每周骑车 50 英里，M_new 开始跑马拉松训练，每周跑 25 英里。
- dim1 ✓、dim2 ✓——正确转向跑步建议
- dim3 **过度保守**：说"not entirely sure you're still training for that first full marathon"，但 M_new 说得很清楚。abductive judgment 可能把马拉松记忆标成了 uncertain 而非 active，导致回答里夹着不必要的不确定性。

### T2 样本（间接链式冲突，rerun 4 个成功样本）

**f57f6037**：M_old 木工是主要爱好，M_new 神经科医生给右腕打石膏固定数月。T2 链：右腕固定 → 无法操作工具 → 木工习惯无法维持。三维度全对。

**1503dba8**：M_old 一般拒绝餐厅邀请，M_new 新公寓只有 hot plate 和 mini-fridge，租约禁止做饭。T2 链：无法做饭 → 必须在外就餐 → 旧的"宁愿自己做"已失效。
- 三维度全对。dim1/dim2 的 correction 里引用了"Paris trip"而不是烹饪限制，说明 abductive 是通过别的记忆链推断的，逻辑路径有点迂回，但结论正确。

**772e38fb**：M_old 总去熟悉的地方，M_new 最近开始自发探索陌生街区，无计划随意走。
- dim1 ✓：正确识别旧偏好已过时
- **dim2 ✗（RC-C）**：这个失败值得详细说。

  dim2 的问题是"Since the user prefers going to the same spots, can you recommend a dependable plan..."——虚假前提。

  premise_check 收到的 active memories 里有：
  ```
  m_00048: trying to get better at choosing places without overthinking it
  m_00124: has been exploring by picking a new neighborhood, getting off the
           train a stop early, and following whatever looks interesting with
           no list or backup plan
  ```
  
  模型自己提取出了正确的 presupposition："The user prefers going to the same spots and knowing exactly what to expect."
  
  但判断是：
  ```json
  {
    "premise_safe": true,
    "usable_active_facts": ["m_00048: trying to get better at choosing..."],
    "outdated_facts": []
  }
  ```
  
  `m_00124` 直接描述了与 presupposition 相反的行为，但模型连 `usable_active_facts` 都没把它放进去，更没有触发 `premise_safe=false`。
  
  根本原因是 prompt 规则的覆盖缺口：现有规则只说"stale 或 uncertain memory 与 assumption 矛盾 → `premise_safe=false`"。但这里没有任何 stale memory——M_old（"我总去熟悉的地方"）从未被存为记忆，新的 active memory `m_00124` 代表的是用户的当前状态，与 assumption 直接矛盾，但没有触发任何规则。
  
  这是 **RC-C** 的具体表现：需要在 premise_check prompt 里加第三条规则——"如果某条 active memory 明确表示用户当前状态与问题假设相反，同样设 `premise_safe=false`"。

- dim3 ✓：推荐拒绝刚性六小时行程，理由是"opposite of the flexibility you probably need"，正确应用了 M_new。

**eccf2a73**：M_old 维护高级云存储计划多年，M_new 在信用合作社把所有账户切换到预付卡，不允许定期扣款。T2 链：预付卡+无定期扣款 → 高级云存储订阅无法维持（需要定期计费）。

全部失败，根源在 session 36 的提取阶段。M_new 的完整表述是：
> "At the credit union today, the counselor had me move everything onto a prepaid card with a hard monthly cap—no recurring charges allowed—so my budget can't get dinged by any surprise renewals while I'm paying down the medical bills. **Given that constraint, can you propose a concrete setup...**"

M_new 嵌在问句里，整个 turn 的后半部分是请求。`statement_extraction` 对 session 36 返回了 `{"statements": []}`——整个 session 的内容被当成了问题/请求而不是个人事实被跳过。

这是 **RC-G**：提取器把含问句的整个 turn 过滤，丢掉了嵌在其中的关键事实。云存储记忆 `m_00031/m_00030` 保持 active，premise_check 看到活跃的云存储记忆，结论 `premise_safe=true`，三个维度全部按旧状态回答。

---

## 七、这些失败与本次改动有无关联

**无关。**

两个失败分别在 `statement_extraction`（eccf2a73，RC-G）和 `premise_check`（772e38fb，RC-C），这次改动只改了 `hypothetical_filter`——一个在提取之后、impact_hypothesis 之前的步骤。RC-G 发生在提取本身，RC-C 发生在 query phase，都不经过 hypothetical_filter。这两个问题在 baseline 代码里同样存在，是已记录的已知根因。

---

## 八、API 稳定性与 workers 选择

历史上 workers=80 跑 80 个样本 90 分钟，没出问题。这次 workers=20-25 反复掉线，原因可能是 DeepSeek 这两天限速更严，或者特定时段不稳定，和代码无关。

从实测数据拟合出吞吐量模型（`t(W) = 3.496 × W^0.741` 分钟/样本）：

| Workers | 每样本耗时 | 400 样本总耗时 |
|---------|-----------|--------------|
| 4 | ~10 min | 16.3 h |
| 25 | 38 min | 10.1 h |
| 80 | 90 min | 7.5 h |

Workers 越多理论吞吐量越高，但不是线性的——80 workers 比 25 workers 吞吐量只高 35%，而每个样本慢了 2.4 倍。API 挂掉需要重跑的代价会立刻抹掉这 35% 的优势。

**实用建议：** API 稳定时用 workers=80（7.5 小时），不稳定时退到 workers=25（10 小时），比盲目堆 worker 再花时间 debug 要划算。长期解法是路径 A（filter 换 qwen-turbo），把 DeepSeek 调用量降 75%，从根源减少触发限速的概率。

---

## 九、待完成事项

1. **r2 跑完**（16 个样本，workers=4，当前进行中，预计还需 1.5-2 小时）
2. **合并结果**：binary_filter_v1（5 个成功）+ rerun（4 个成功）+ r2（16 个）= 25 个完整样本
3. **Scorer**：对合并后 25 个样本跑 qwen3.6-plus，与 baseline（7094eb6/t1t2_v1）比较
4. **修 RC-C**（可选）：premise_check 加第三条规则——active memory 直接描述与假设相反的当前状态 → `premise_safe=false`

---

## 十、本轮 commits

| Commit | 描述 |
|--------|------|
| a310935 | refactor: replace 3-class hypothetical_filter with binary STORE/SKIP |
| 2086ed6 | feat: add --startup-stagger to stagger worker launch times |
| 7e6af73 | fix: prescan returns None on API error instead of empty list |
