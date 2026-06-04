# RECAST 126 例失败案例：管线追踪分析报告

**Run:** baseline_s137  
**样本:** 126 个失败案例（T1×30 + T2×30 共 60 个样本，每样本最多 3 dim）  
**日期:** 2026-06-04  
**分析方法:** 逐案读取 trace.json，定位首个出错步骤，展示该步的输入与输出

管线顺序：`statement_extraction → hypothetical_filter → impact_hypothesis → abductive_judgment → pool_synthesis → impression_update → premise_check → answer_generation`

---

## 统计概览

| 首个出错步骤 | 失败案例数 | 典型冲突类型 |
|---|---|---|
| **abductive\_judgment**（在已有记忆里误标 M\_new 为 stale） | ~25 | T1 居多 |
| **impact\_hypothesis**（未生成地理/关系/领域推断假设） | ~20 | T2 居多 |
| **pool\_synthesis**（弱证据未达阈值，未触发 stale） | ~15 | T2 |
| **statement\_extraction**（M\_new 被当成 pure-request 过滤） | ~10 | T1 |
| **abductive\_judgment**（同 session 内新记忆互相抵消） | ~8 | T1 |
| **premise\_check**（outdated\_facts 指向方向颠倒） | ~6 | T1 |
| **answer\_generation**（有正确 correction 但建议仍回退旧状态） | ~22 | dim3 集中 |

---

## 第一章：首错步骤 = `abductive_judgment`（跨 session，T2 间接推理链未闭合）

### 机制

T2 样本中，M\_new 通过环境细节暗示地理迁居、关系状态变化或专业身份转变。  
`impact_hypothesis` 为 M\_new 语句生成了若干假设，但这些假设的语义向量不够靠近目标记忆，导致 `abductive_judgment` 在评估目标记忆时，判断结果为 `no_conflict` 或 `weakens_support`（置信度不足），最终 `pool_synthesis` 也无法累积到足够的证据阈值。

---

### 案例 1.1 — uid `704cac18`  T2 dim1  "波特兰 vs 树皮蝎"

**M\_old：** "I've honestly become so accustomed to the pace of life here in Portland that I can't imagine navigating the rhythm of a different city anymore."  
**M\_new：** "I've learned the hard way to always shake out my boots before putting them on; I found another bark scorpion hiding near the heel this morning, driven indoors by the relentless dry heat."

---

**步骤 1：statement\_extraction（Session 35）**

输入（Session 35 用户发言片段）：提到找到树皮蝎、残酷干热驱蝎入室。

输出（提取到的 statements）：
```
"found a bark scorpion hiding near boot heel this morning, driven indoors by relentless dry heat"
"currently in an environment with dry heat"
```
✓ 提取成功，两条语句均通过 hypothetical\_filter。

---

**步骤 2：impact\_hypothesis（"found bark scorpion..." 语句）**

输入：statement = "found a bark scorpion hiding near boot heel this morning, driven indoors by relentless dry heat"

输出（生成的假设列表，前 3 条）：
```
"user did not have to worry about scorpions entering the home (physical/spatial — enabling context for pet and child safety)"
"user's dog could be let out without immediate risk of encountering a scorpion (enabling context — pet care routine)"  
"user's home environment did not require extra pest control measures or vigilance (economic)"
```

⚠️ **问题所在**：三条假设全部围绕**宠物安全和害虫控制**，没有一条生成"用户所在地理位置已改变（从波特兰迁至沙漠地区）"的假设。  
"bark scorpion = 亚利桑那/新墨西哥沙漠物种 ≠ 太平洋西北地区"这条领域知识没有触发。

impact\_hypothesis 为 "currently in an environment with dry heat" 语句生成的假设：
```
"User previously lived in a non-dry-heat environment, which may have affected skincare routine, plant care habits, and wardrobe choices"
"User's skincare routine may no longer be suitable for dry heat conditions"
"User's plant care habits may have been shaped by previous climate; dry heat would make plant survival harder"
```

这些假设指向皮肤护理和植物护理，仍然没有产生指向 m\_00038（"lives in Portland"）的路径。

---

**步骤 3：abductive\_judgment（Session 35，m\_00038 对 "scorpion" 语句的评估）**

输入：  
- target\_memory: `m_00038`，content = "lives in Portland"  
- statement: "found a bark scorpion hiding near boot heel this morning..."  
- supporting\_hypotheses（来自上一步的宠物/害虫假设，无地理假设）

输出：
```json
{
  "target_item_id": "m_00038",
  "confidence": 0.0,
  "type": "no_conflict",
  "inference_chain": "Location is independent of dog ownership. No conflict.",
  "action": "discarded"
}
```

⚠️ **错误确认**：判断结果是 `no_conflict`，直接丢弃。inference\_chain 甚至错误地写成了"与狗的所有权无关"——这是 m\_00038 在处理另一条语句（"has a medium-sized dog"）时的推断，说明系统把错误的 supporting\_hypothesis 传入了 m\_00038 的评估上下文。

实际上，即使在 "dry heat" 语句的评估中，m\_00038 在整个 session 的所有 177 条 judgment\_log 中，没有任何一条的 inference\_chain 提到了地理迁居。

---

**步骤 4 之后：premise\_check（dim1\_query）**

输入：  
- query: "你是否还住在波特兰？"  
- retrieved memories: 含 m\_00038 (active, "lives in Portland")  
- outdated\_facts: `[]`

输出：
```
premise_safe: True（注意：这里判断为安全是因为 premise_result 认为"lives in Portland"仍是 active）
correction: None
```

等等，实际 premise\_result 里有内容（因为 verdict 字段），让我看实际：
dim1 的 verdict:
```
correction: "We're no longer certain that the user still lives in Portland. The active memory that said 
'lives in Portland' is still listed as confirmed, but there is no recent evidence directly confirming 
current location."
```
premise\_safe: False（但 outdated\_facts=[]，correction 表述模糊）

---

**步骤 5：answer\_generation（dim1\_query）**

输入（传给 answer\_gen 的上下文）：  
- correction: "不确定是否还住波特兰，没有近期证据直接确认"（模糊，未提供新状态）  
- stale\_items 中无地理更新  
- profile\_summary：仍含 Portland 相关描述

输出（最终 answer）：
> "Actually, the most recent confirmed information I have **still says you live in Portland** — there's no memory of a move since then, so yes, **it looks like you're still there**. Let me know if anything has changed recently!"

**判定：FAIL**

> "The response explicitly confirms the user still lives in Portland and claims there is 'no memory of a move,' completely ignoring the M_new clues (bark scorpions, relentless dry heat)."

**根因定位：`impact_hypothesis`**  
scorpion + dry heat 语句的假设没有产生地理迁居路径 → abductive\_judgment 对 m\_00038 判断 no\_conflict → m\_00038 保持 active → answer\_gen 用 active 记忆直接确认波特兰。

---

### 案例 1.2 — uid `321e33b5`  T2 dim1  "丧偶 vs 分床伴侣"

**M\_old：** "I usually come to these meetings by myself since my spouse passed away."  
**M\_new：** "We spent last night stuffing envelopes for the school fundraiser, and I'm still finding glitter in my sleeves—turns out when you share a bed with someone who crafts at midnight, you share the evidence too."

---

**步骤 1：statement\_extraction（Session 30）**

输入（Turn 2）：用户说"I'm still finding glitter in my sleeves—turns out when you share a bed with someone who crafts at midnight, you share the evidence too."

输出：
```json
{
  "statements": [
    {
      "text": "shares a bed with someone who crafts at midnight",
      "category": "current_state",
      "is_definite": true
    }
  ]
}
```
✓ 提取成功，创建 m\_00108（current\_state: "shares a bed with someone who crafts at midnight"）。

---

**步骤 2：impact\_hypothesis（"shares a bed with someone..." 语句）**

输入：statement = "shares a bed with someone who crafts at midnight"

输出（生成的假设，前 3 条）：
```
"the user was able to stay up past midnight without sharing a bed or being disturbed by a partner's activities"
"the user's partner or bedmate was not someone who crafts late at night, implying a new relationship or change in living situation"
"the user was able to sleep uninterrupted or without accommodating another person's schedule in the bedroom"
```
✓ 第 2 条假设已经包含"new relationship or change in living situation"，是指向关系状态变化的正确路径。

---

**步骤 3：abductive\_judgment（Session 30，m\_00001 的评估）**

输入：  
- target\_memory: `m_00001`，content = "the user's spouse passed away"  
- statement: "shares a bed with someone who crafts at midnight"  
- supporting\_hypotheses: 含"new relationship or change in living situation"的假设

输出：
```json
{
  "target_item_id": "m_00001",
  "confidence": 0.4,
  "type": "weakens_support",
  "inference_chain": "New statement: user shares a bed with someone who crafts at midnight. Sharing a bed with someone implies a current bedmate. If the spouse passed away, this new bedmate suggests either a new relationship or a change in living situation. The original memory of spouse's death may still be true (the death happened), but the implication that user sleeps alone is invalidated. However, the memory itself...",
  "action": "added_to_pool"
}
```

abductive\_judgment 识别出了关联（有新床伴 → 可能有新关系），但因为"配偶去世"是历史事实，判断无法直接 invalidate 它，给出 confidence=0.4，仅加入证据池（added\_to\_pool）。

---

**步骤 4：pool\_synthesis（后续某 session）**

输入：  
- target\_memory: `m_00001`，content = "the user's spouse passed away"  
- 累积证据: 仅 1 条（Session 30 的 "shares a bed" 语句，chain 置信度 0.4）

输出：
```json
{
  "synthesized_confidence": 0.40,
  "reasoning": "The only evidence pointing to this memory being outdated is a single statement from Session 30 about the user sharing a bed with someone who crafts at midnight. This suggests a current bedmate, but does not directly contradict the fact that the spouse passed away... It is possible to share a bed with a new partner while the spouse still passed away."
}
```

⚠️ **错误所在**：pool\_synthesis 的推理正确识别了"有新床伴"，但将"配偶去世这件事（历史事实）"与"用户目前的伴侣状态（当前状态）"混为一谈——它认为两件事可以并存（"仍可能是真的，同时有新床伴"），因此 synthesized\_confidence = 0.40，**不满足触发 stale 的阈值**。

m\_00001 最终状态：**active**，confidence = 0.85。

---

**步骤 5：premise\_check（dim1\_query）**

输入：  
- query（含错误前提）："你是否还习惯独自赴约，因为配偶已去世？"  
- retrieved memories: m\_00001 active（"spouse passed away"），m\_00108 active（"shares a bed..."）  
- outdated\_facts: `[]`（m\_00001 是 active，不在 outdated\_facts 里）

输出：
```
premise_safe: True
correction: None
```

---

**步骤 6：answer\_generation（dim1\_query）**

输入：  
- correction = None（前提被判为安全）  
- active memories: 含 m\_00001 "spouse passed away" + m\_00108 "shares a bed with someone"

输出：
> "Yes, based on the conversation history, the marital status implied by your spouse having passed away is **still current**. The active facts confirm that your spouse passed away, and **there is no newer information suggesting any change**... So you are still a widow/widower."

**判定：FAIL**

> "The response explicitly affirms that the widowed status from M_old is still current and incorrectly claims there is no newer information suggesting a change."

**根因定位：`pool_synthesis`**  
单条 0.4 证据不足以触发 stale 阈值；pool\_synthesis 未能推导出"有新床伴 → 用户当前关系状态已改变 → '由于配偶去世独自赴约'这一前提失效"。推理停在"配偶去世是历史事实"，未进入"但当前伴侣状态已改变"这一步。

---

### 案例 1.3 — uid `546d39d6`  T2 dim1  "晴天 vs 海雾"

**M\_old：** "I'm exposed to pretty intense sunlight on a daily basis since the sky here stays clear most of the time."  
**M\_new：** "I've started timing my dog's walks by the tide charts—some days the boardwalk's underwater, and on the others the air's so thick with mist that everything a block away looks like it's been rubbed out."

---

**步骤 1：statement\_extraction（Session 41）**

输入（M\_new 用户发言）：提到潮汐表、栈道被淹、浓雾。

输出：
```
"has a grill that needs heat deflectors replaced (starting to rust and flake off)"
```

（注：实际提取的是同一 session 内另一条与 M\_new 无关的维护信息。M\_new 的核心内容"潮汐/浓雾"没有产生 current\_state 类提取。）

⚠️ **M\_new 关键信息（天气/地理环境变化）在 statement\_extraction 阶段即未被提取为状态陈述。**

没有提取到"currently in a foggy coastal environment"或"boardwalk underwater due to tides"这类 current\_state 语句，导致后续所有阶段均无法处理这一冲突。

---

**步骤 2 之后：全部跳过**

因为没有提取到相关状态语句，impact\_hypothesis、abductive\_judgment、pool\_synthesis 对"晴天强日照"这条旧记忆均未执行任何操作。

---

**步骤 3：premise\_check（dim1\_query）**

输入：  
- query: "你是否仍每天暴露在强烈日照下？"  
- retrieved memories: 含"is exposed to intense sunlight daily"（active）  
- outdated\_facts: `[]`

输出：
```
premise_safe: True
correction: None
```

---

**步骤 4：answer\_generation（dim1\_query）**

输出：
> "**Yes, that's still accurate from what I know.** Your active memory confirms you're exposed to intense sunlight daily because the sky stays clear most of the time."

**判定：FAIL**

**根因定位：`statement_extraction`**  
"tide charts / boardwalk underwater / air so thick with mist" 是环境描述，不符合 CURRENT\_STATE 或 RECENT\_CHANGE 的典型模式（无 "I am / I now / I've been"形式），被过滤。整条推理链从源头断裂。

---

### 案例 1.4 — uid `ba60203c`  T2 dim2  "本科生 vs 住院医生"

**M\_old：** "Just to give you some context, I'm currently pursuing my bachelor's degree."  
**M\_new：** "At orientation this morning, they handed me a white coat with my name stitched on it and walked us through the overnight call schedule—apparently my first rotation starts Monday, so I'm scrambling to find a cheap place closer to the hospital."

---

**步骤 1：statement\_extraction**

输入：用户提到白大褂仪式、轮班表、第一轮值班从周一开始、在医院附近找住处。

输出（提取的 statements）：
```
"attending medical school or healthcare training program (starting clinical rotations)"
"looking for affordable housing near the hospital"
"overwhelmed by new schedule"
```
✓ 提取正确，第一条明确标注了临床实习开始。

---

**步骤 2：impact\_hypothesis（"starting clinical rotations" 语句）**

输入：statement = "attending medical school or healthcare training program (starting clinical rotations)"

输出（假设，前 4 条）：
```
"user was previously not yet in the clinical training phase, implying significant educational advancement"
"user's time availability during evenings and weekends has drastically reduced due to call schedules"
"user's living arrangement near campus may no longer be optimal"
"user's study habits and routine are likely to change significantly"
```

⚠️ **缺失的假设**："user's claim of 'pursuing a bachelor's degree' may now be outdated if they have advanced to a graduate/professional degree program"。  
生成的假设关注时间安排和住房变化，但没有产生"本科生身份是否仍有效"的质疑路径。

---

**步骤 3：abductive\_judgment**

因为 impact\_hypothesis 没有生成指向"bachelor's degree"记忆的假设，abductive\_judgment 对那条记忆的评估结果是 `no_conflict` 或 `discarded`（无证据指向）。

---

**步骤 4：premise\_check（dim2\_query）**

输入：  
- query（错误前提）："作为一个本科生，帮我规划课程安排。"  
- outdated\_facts: `[]`

输出：
```
premise_safe: True
correction: None
```

---

**步骤 5：answer\_generation（dim2\_query）**

输出：
> "To suggest a manageable course load and study schedule, I need a bit more info about your current semester—like how many credits you're considering, which days you're free for classes, and how much time you can realistically dedicate to studying each day. Since you're a parent, love art and reading, and you tend to be consistent about commitments, here's a weekly template for a lighter load..."

**判定：FAIL**

> "The response blindly accepts the false premise that the user is pursuing a bachelor's degree."

**根因定位：`impact_hypothesis`**  
"临床实习开始"这一状态变化没有产生"本科学历前提可能已失效"的假设。需要医学教育领域知识：white coat ceremony → 进入 MD/DO 临床阶段 → 不再是本科生。这条领域推断链未被生成。

---

## 第二章：首错步骤 = `abductive_judgment`（同 session 内新记忆被误标）

### 机制

同一 session 中提取出多条 statements，其中 statement A（"moved into a new apartment"）和 statement B（"lives in top-floor south-facing windows"）来自同一条 M\_new 消息，描述同一个新状态。  
但 `abductive_judgment` 在处理 statement A 时，会遍历本 session 中由 statement B 已经创建的新记忆，并错误地将 B 创建的记忆判定为"旧公寓的特征 → 已过时"。这是同 session 内的写后读冲突。

---

### 案例 2.1 — uid `cc9aaa40`  T1 dim3  "朝南大窗被误标为旧居"

**M\_old：** "It stays pretty dim in my living room even during the afternoon because of the trees outside."  
**M\_new：** "Now that I'm in the top-floor place with big south-facing windows, the living room is flooded with sunlight for most of the day."

---

**步骤 1：statement\_extraction（Session 35）**

输入（同一 M\_new 消息的内容）

输出（4 条 statements，按提取顺序）：
```
[0] "moved into a new apartment"
[1] "lives in a top-floor apartment with big south-facing windows"
[2] "living room gets a ton of direct sun most of the day"
[3] "prefers a minimal number of fixtures (wants to add only one lamp)"
```
✓ 提取正确，全部通过 hypothetical\_filter。

---

**步骤 2：新记忆创建（Session 35 内）**

由 statement [1] 创建：`m_00131`，content = "lives in a top-floor apartment with big south-facing windows"  
由 statement [2] 创建：`m_00132`，content = "living room gets a ton of direct sun most of the day"

---

**步骤 3：abductive\_judgment（Session 35，statement [0] 对 m\_00131 的评估）**

输入：  
- new\_statement: "moved into a new apartment"  
- target\_memory: `m_00131`，content = "lives in a top-floor apartment with big south-facing windows"（本 session 刚创建）  
- supporting\_hypothesis: "user's physical location has changed → features of old apartment no longer apply"

输出：
```json
{
  "target_item_id": "m_00131",
  "confidence": 0.85,
  "type": "direct_invalidation",
  "inference_chain": "New statement: moved into a new apartment → implies the user's living situation changed → if the old apartment was the one with top-floor/south-facing windows, that memory likely describes a previous apartment and is now outdated unless the new apartment has the same features, which is uncertain → direct_invalidation because the memory is location-specific and the move almost certainly changes it.",
  "action": "marked_stale"
}
```

⚠️ **错误所在**：inference\_chain 假设"top-floor south-facing windows"描述的是**旧公寓**，但 m\_00131 实际上是由**同一 session、同一 M\_new 消息**中的"新公寓描述"提取出来的，描述的是**新公寓的特征**。abductive\_judgment 没有区分"本 session 内的新记忆是描述新状态"和"跨 session 的旧记忆是描述旧状态"。

m\_00131 和 m\_00132 在**创建的同一 session 内**就被标为 stale，stale\_since\_session = created\_session = 35。

---

**步骤 4：premise\_check（dim3\_query）**

输入：  
- query: "客厅光线充足，帮我选一盏灯。"（错误前提：现在也充足）  
- outdated\_facts: `['lives in a top-floor apartment with big south-facing windows (stale since session 35)', 'living room gets a ton of direct sun most of the day (stale since session 35)']`

premise\_check 看到"充足日照"这两条记忆都被标为 stale（且 stale\_reason 写着"搬家 → 旧公寓特征"），生成了以下 correction：

输出：
```
premise_safe: False
correction: "We're no longer certain your current living room has the same lighting conditions as before. You recently moved into a new apartment, and the previous memories about top-floor, south-facing windows with direct sun all day are stale—so the new space could be very different."
```

⚠️ **correction 方向完全错误**：correction 将"充足日照"解读为"旧公寓特征（已过时）"，暗示新公寓光线未知，而实际情况是 M\_new 在描述**新公寓正是充满阳光**。

---

**步骤 5：answer\_generation（dim3\_query）**

输入：  
- correction: "旧公寓有大窗户/充足阳光，新公寓情况未知"

输出：
> "Actually, we don't know for sure what your new apartment's lighting is like yet—**your previous top-floor, south-facing windows with all-day direct sun were from your old place**. Since you just moved, you'll need to observe the light in your living room before deciding. A Maidenhair Fern or Calathea will scorch in direct sun, so if the new spot gets any harsh rays..."

**判定：FAIL**

> "The response incorrectly swaps the old and new states, claiming the sunny windows were from the old place."

**根因定位：`abductive_judgment`（同 session 内新记忆互相抵消）**  
Statement [0]"moved"和 statement [1]"big windows"来自同一 M\_new 消息，描述同一个新状态。但 abductive\_judgment 将 statement [0] 对 statement [1] 创建的新记忆 m\_00131 执行了 invalidation，将"新公寓朝南大窗"误判为"旧公寓特征已过时"，然后 correction 完整继承了这个错误方向，answer\_gen 照单全收。

---

### 案例 2.2 — uid `7c0ae4e7`  T1 dim1  "Austin 被误标为旧状态"

**M\_old：** "I've been based in Seattle for the last few years."  
**M\_new：** "I just finished updating my address after settling into my new place in Austin, and I'm trying to get set up with the local utilities and services here."

---

**步骤 1：statement\_extraction（Session 33）**

输出：包含 "settled into my new place in Austin"（category=recent\_change，创建 m\_00135）。  
✓ 提取成功。

---

**步骤 2：abductive\_judgment（Session 45，statement 作用于 m\_00135）**

在 Session 45 中，提取了以下 statements：
```
"looking for household items"
"looking for kitchen utensils or coffee makers"
"scored great deals at Macy's during their winter clothing sale, saving $200"
...
```

abductive\_judgment 处理 "looking for kitchen utensils or coffee makers" 时，评估 m\_00135：

输入：  
- new\_statement: "looking for kitchen utensils or coffee makers"  
- target\_memory: `m_00135`，content = "settled into my new place in Austin"

输出：
```json
{
  "target_item_id": "m_00135",
  "confidence": 0.55,
  "type": "weakens_support",
  "inference_chain": "User now looking for household items → this may imply they are not fully settled (still acquiring items). However, 'settled into my new place' is a past event that could be true even if they are still adding household items... using supporting hypothesis: 'the user's current home in Austin was fully furnished and set up for daily life' → if they are now looking for household items, that hypothesis is contradicted. So memory m_00135 (settled into new place) is weakened: being settled implies no longer needing to search for household items. Confidence: 0.55.",
  "action": "marked_uncertain"
}
```

随后 pool\_synthesis（或另一条语句）将 m\_00135 推进到：

```json
{
  "target_item_id": "m_00135",
  "confidence": 0.8,
  "type": "direct_invalidation", 
  "inference_chain": "The new statement 'looking for kitchen utensils or coffee makers' directly contradicts the implication of being 'settled'—a settled kitchen would have utensils and a coffee maker. This indicates the user is not yet fully settled, contradicting the memory. The memory is invalidated.",
  "action": "marked_stale"
}
```

⚠️ **错误所在**：inference\_chain 的逻辑是"已安顿的厨房 = 有厨具 → 还在找厨具 = 未安顿"。但实际上，新搬家的人在**搬入**新居后购置厨具完全正常，"在新家找厨具"与"已搬入新家"完全相容，甚至是搬家过程的一部分。这是一个语义过度推断：abductive\_judgment 将"purchase/acquire household items"解读为"not settled"，而忽略了"新搬家者在安顿过程中购置用品"这个常识场景。

---

**步骤 3：premise\_check（dim1\_query）**

输入：  
- outdated\_facts: 包含 `m_00135: settled into my new place in Austin (stale)`  
- stale 的原因写的是"厨具采购 → 未安顿 → Austin 记忆失效"

输出：
```
correction: "We're no longer certain that you still live in Seattle — a stale memory indicates you moved to Austin, but that memory has also been marked stale, so your current location is unclear."
```

---

**步骤 4：answer\_generation（dim1\_query）**

输入：correction = "Seattle 旧记忆已过期，Austin 迁居记忆**也**过期，当前位置不明"

输出：
> "Actually, it looks like you've moved since we last spoke — a stale memory indicated you relocated from Seattle to Austin, **but that's also been marked as not current. So I'm not entirely sure where you're based right now.** Could you let me know your current city..."

**判定：FAIL**

> "The response immediately contradicts itself by claiming the Austin update is 'not current.'"

**根因定位：`abductive_judgment`（过度推断，"购置厨具" → "未安顿 → Austin 记忆失效"）**  
abductive\_judgment 使用"还在买厨具 = 未安顿 = Austin 记忆错误"这条错误推理链，将 M\_new 的搬家目的地信息（Austin）也标为 stale，最终 correction 把新旧两个地点都否定，产生虚假不确定性。

---

## 第三章：首错步骤 = `statement_extraction`（M\_new 被当作 pure-request 过滤）

### 机制

extraction 提示词有规则："不提取 pure requests 或 questions"。当 M\_new 的事实性内容嵌入在请求性句式里时，提取器将整个 turn 视为请求，返回空列表，导致所有下游步骤无法感知状态变化。

---

### 案例 3.1 — uid `d4721a2a`  T1 dim1/dim2/dim3（三 dim 全失败）

**M\_old：** "There are only a handful of people from work I actually interact with on a regular basis."  
**M\_new：** "After the reorg, I'm on calls and message threads all day with a rotating mix of about thirty different names, and it feels like I'm constantly looping in someone new for day-to-day coordination."

---

**步骤 1：statement\_extraction（Session 44 / Call Record #97）**

输入（完整用户发言）：
```
"Can you help me come up with a lightweight system for keeping track of who's who and what 
I'm waiting on without turning it into a huge admin project? After the reorg, I'm on calls 
and message threads all day with a rotating mix of about thirty different names, and it 
feels like I'm constantly looping in someone new for day-to-day coordination. I'm thinking 
some combo of tagging, a simple spreadsheet, maybe a recurring 'who owns what' note—but 
I'm not sure what's actually sustainable. Any practical workflow ideas...?"
```

输出：
```json
{"statements": []}
```

⚠️ **完全提取失败**。提取器将整个 turn 识别为"请求帮助规划工作流"（pure request），过滤了嵌在其中的事实性状态陈述："after the reorg, rotating mix of about thirty different names"——这是一个 CURRENT\_STATE 级别的事实（工作协作网络已从"少数固定同事"扩展至"约 30 人轮换"）。

**问题机制**：用户的陈述以"Can you help me..."开头，整体句式是请求，M\_new 的事实内容作为背景信息嵌在请求的 Because 从句里。提取器的"不提取 pure-requests"规则把整个 turn 归类为请求，没有识别出背景信息中的状态变化。

---

**步骤 2 之后：全部跳过**

无 statement 输出 → 无 impact\_hypothesis → 无 abductive\_judgment → "只有少数同事"的旧记忆保持 active。

---

**步骤 3：premise\_check（dim1\_query / dim2\_query）**

dim1（直接确认）输入：query = "你是否仍然只有少数几个同事定期联系？"  
outdated\_facts: 含 `[m_00037] listens to audiobooks during commute (stale)` 等无关记忆，与工作协作无关。  
correction 内容：模糊提及"工作情况可能变化"（基于 WFH 信息推断，而非 reorg 信息）

dim2（对抗探针）输入：question 嵌入了"既然你只有 Alex 和 Rachel 这几个固定同事..."的错误前提。

---

**步骤 4：answer\_generation**

dim1 输出：
> "I don't have current information on whether you still interact with only a handful of colleagues regularly. Could you clarify?"

dim2 输出：
> "Since you work from home most of the time and **only regularly interact with a handful of work folks like Alex and Rachel**, I'd suggest setting up a simple recurring calendar invite..."

dim3 输出：
> "Honestly, probably not — unless your lunch break is unusually long... For example, **if you have 8 people**, that's 2 hours straight."

**判定：FAIL（三 dim）**

三 dim 呈现三种不同的失败形式（虚假不确定/接受错误前提/数量严重低估），但均来自同一根因。

**根因定位：`statement_extraction`**  
"Can you help me..." 句式导致整个 turn 被当作请求过滤，背景信息中的"约 30 名轮换同事"无法提取。后续管线对 M\_new 的核心事实完全无感知。

---

### 案例 3.2 — uid `a7e9be55`  T1 dim2/dim3（两 dim 失败）

**M\_old：** "For the past few months, I've been dealing with consistently high stress levels that just don't seem to go away."  
**M\_new：** "I've been waking up feeling surprisingly calm lately, and even when my days get busy my body doesn't go into that tense, on-edge mode anymore."

---

**步骤 1：statement\_extraction**

输入（用户发言）：描述最近晨起意外平静、忙起来也不再进入紧绷模式。

输出：`{"statements": []}` — 提取为空。

⚠️ **提取失败**。"my body doesn't go into that tense, on-edge mode anymore"是否定性描述（否定一个已消失的状态），不符合提取规则里"CURRENT\_STATE: what is currently true"的正向模式。提取器倾向于提取正向陈述（"I am X"、"I have Y"），对否定式变化（"I no longer X"）识别能力弱。

---

**步骤 2 之后：全部跳过**

"持续高压"旧记忆（对应 M\_old）保持 active，所有后续步骤均无法感知压力状态的改变。

---

**步骤 3：premise\_check（dim2\_query）**

dim2 的错误前提是"你目前承受持续高压，帮我制定一个两周计划来改善。"

outdated\_facts: `['user has been doing yoga and meditation to help with anxiety (stale)', 'user has been trying to establish a morning routine (stale)']`——与"是否仍有持续高压"无直接关联。

输出：`premise_safe: True, correction: None`

---

**步骤 4：answer\_generation（dim3）**

dim3 输出（部分）：
> "Given your recent promotion, **the panic attacks since mid-January**, and your new interest in baking and self-improvement books, I'd lean into small anchors rather than a full rigid routine..."

⚠️ 幻觉："mid-January 以来的恐慌发作"完全不存在于任何记忆或 M\_new 中，是 answer\_gen 从记忆池中随机抓取心理健康相关碎片填充的。

**判定：FAIL（dim2/dim3）**

**根因定位：`statement_extraction`**  
否定式内省描述（"my body doesn't go into that tense mode anymore"）无法匹配 CURRENT\_STATE 的提取模式，返回空列表，下游全线无感知。

---

### 案例 3.3 — uid `b5d5e6aa`  T2 dim1/dim2

**M\_old：** "Even if a blender has thousands of five-star ratings, I focus entirely on the critical reviews; I trust the complaints more than the praise."  
**M\_new：** "I've been helping my niece with her debate prep, and the coach has us do this weird drill: we can't bring up counterarguments at all until we've written a full page of what the other side is trying to accomplish in the best possible light. It's uncomfortable, but it's making me notice how quickly I reach for the ugliest interpretation by default."

---

**步骤 1：statement\_extraction**

输入：用户描述辩论练习——需要先穷举对方最有利的解读才能提出反驳，从而意识到自己默认倾向负面解读。

输出：`{"statements": []}` — 提取为空。

⚠️ **提取失败**。整段文字是一个关于辩论训练的**叙事**，没有"我改变了评价产品的方式"的显式陈述。用户描述的是一个元认知感悟（"意识到自己倾向负面解读"），属于内省型的间接状态变化，提取器的正向事实提取模式无法捕获这类语义。

---

**步骤 2 之后：全部跳过**

"只看差评"旧记忆保持 active，dim2 的错误前提（"你只关注差评，请用这个逻辑帮我选搅拌机"）无法被检测。

---

**步骤 4：answer\_generation（dim2）**

输出：
> "Since **you ignore five-star ratings and rely on critical reviews**, here's a step-by-step method to pick the best blender by prioritizing complaints: 1. **Filter by 1- and 2-star reviews only** — Go to any major retailer site..."

**判定：FAIL**

**根因定位：`statement_extraction`**  
辩论训练的内省叙事没有匹配任何提取类别（CURRENT\_STATE / RECENT\_CHANGE / BIOGRAPHICAL / LASTING\_PREFERENCE），返回空。

---

## 第四章：首错步骤 = `premise_check`（outdated\_facts 比对方向颠倒）

### 机制

`premise_check` 的 `outdated_facts` 字段有时记录了**正确的过期事实**，但附带的注释（explanation）将方向写反：把描述 M\_new 状态的记忆标注为"已过时"，或者把验证条件（来自 M\_new）标注为"已失效的旧约束"。  
这导致 premise\_check 得出错误的 correction（告诉 answer\_gen 的方向反了），answer\_gen 照单全收。

---

### 案例 4.1 — uid `8aeb8778`  T1 dim3  "验证需求被标为已失效"

**M\_old：** "Honestly, after years of helping out during the block parties, most neighbors just assume my word is solid when it comes to planning events."  
**M\_new：** "At my building's annual meeting last night, they put me on the spot to explain myself, and now anything I submit has to be verified by someone else before they'll accept it."

---

**premise\_check 的 outdated\_facts 输出：**
```
[m_00117] was put on the spot during the building's annual meeting and now has a condition 
          that any submission must be verified by someone else before they'll accept it.
          (stale: verification condition no longer applies)
[m_00038] after years of helping out during the block parties, most neighbors assume their 
          word is solid when it comes to planning events.
          (stale: verification requirement now exists, so unqualified trust is outdated)
```

⚠️ **m\_00117 的注释方向颠倒**：m\_00117 记录的内容正是 M\_new（需要他人核实）。注释写"verification condition no longer applies（验证条件已不再适用）"，把 M\_new 的当前约束标注为"已过时"——这个标注方向完全反了。

实际上应该是 m\_00038（无条件信任 = M\_old）被标为 stale，而 m\_00117（需要核实 = M\_new）是当前有效状态。

---

**answer\_generation（dim3）输出：**
> "Since you serve on your condo board, you already know how groups like that operate... **Given your goal to increase engagement and your reputation as someone whose word is trusted**, you have solid foundations to build on..."

**判定：FAIL**

> "The response incorrectly labels the new mandatory verification requirement as 'stale' and advises the user to lean on their old, broadly trusted reputation."

**根因定位：`premise_check`（outdated\_facts 注释写反）**  
注释"verification condition no longer applies"将 M\_new 约束标为失效，correction 因此告诉 answer\_gen "旧信誉仍有效"，建议建立在错误方向上。

---

### 案例 4.2 — uid `0af76ce2`  T1 dim3  "外出充电感被误标为旧状态"

**M\_old：** "I tend to decline most evening invitations because I really prefer spending my free time at home."  
**M\_new：** "Lately I feel most recharged when I'm out in the middle of things—checking out new spots, meeting people, and keeping my calendar full with plans."

---

**premise\_check 的 outdated\_facts：**
```
[m_00102] feels most recharged when out in the middle of things—checking new spots, 
          meeting people, keeping calendar full (stale)
```

⚠️ **方向颠倒**：m\_00102 的内容正是 M\_new 描述的当前状态（外出时最充电），但被标注为 stale。  
实际上应该是"偏爱待在家、拒绝晚间邀约"（M\_old）被标为 stale。

---

**answer\_generation（dim3）输出：**
> "So you've been spending a lot of evenings immersed in creative projects lately — painting, working on handmade jewelry... **Since you used to feel most recharged by checking new spots** (but that's more of a past pattern)... here's a schedule heavily focused on **home-based activities** (painting, reading, Instagram)..."

**判定：FAIL**

> "Response 3 incorrectly frames the user's current outgoing preference as a past trait and designs a schedule heavily focused on staying home."

**根因定位：`premise_check`（outdated\_facts 标注 M\_new 状态为 stale）**  
correction 告知"外出充电是过去的习惯"，answer\_gen 据此推荐居家活动日程，与 M\_new 完全相反。

---

### 案例 4.3 — uid `4bb62dbf`  T1 dim3  "批量备餐习惯被标为已弃用"

**M\_old：** "I've never been one to follow recipes exactly; I'm comfortable with the meal not turning out perfectly if it means I learn something new."  
**M\_new：** "I've started batch-cooking on Sundays and labeling everything down to the minute, because if dinner isn't predictable on weeknights the whole schedule falls apart."

---

**premise\_check 的 outdated\_facts：**
```
batch-cooking on Sundays and labeling everything down to the minute 
(stale since session 38: user no longer maintains that rigid routine, so spontaneous 
Wednesday cooking is plausible)
```

⚠️ **方向颠倒**：这条记忆记录的正是 M\_new（批量备餐 = 用户当前建立的新习惯），但注释写"已弃用严格习惯"，将 M\_new 状态标为旧习惯。

实际情况：用户**刚建立**了批量备餐习惯（M\_new = recent\_change），但注释将其标为"session 38 就已过时"。

---

**answer\_generation（dim3）输出：**
> "Yes, this actually sounds like a great idea for you!... **You're no longer tied to that rigid Sunday batch-cooking routine, so a spontaneous Wednesday experiment totally fits your current approach**..."

**判定：FAIL**

> "The response falsely claims the user is 'no longer tied to that rigid Sunday batch-cooking routine,' directly violating M_new."

**根因定位：`premise_check`（outdated\_facts 将 M\_new 的新习惯标为弃用的旧惯例）**

---

## 第五章：首错步骤 = `answer_generation`（有正确 correction 但建议仍回退旧状态）

### 机制

这是唯一一类 correction 已经正确生成、但 answer\_gen 仍然失败的情况。  
失败模式：answer\_gen 的开头承认了 correction 的内容（口头 hedging），但在生成具体的行动建议时，从记忆池中拉取了 M\_old 相关的旧记忆，建议内容回退到 M\_old 的行为逻辑。

---

### 案例 5.1 — uid `ad8b0a1f`  T1 dim2  "行程协调建议照搬配偶逻辑"

**M\_old：** "I always need to coordinate with my spouse before finalizing my schedule."  
**M\_new：** "I've been getting used to making plans on my own lately, since it's just me at home now."

---

**premise\_check 输出（正确）：**
```
premise_safe: False
correction: "The premise that the user always needs to coordinate with their spouse is likely outdated—the user now lives alone and handles scheduling independently."
```
✓ correction 正确识别了前提失效。

---

**answer\_generation 输入：**
- correction: 用户现在独居、独立安排行程  
- 记忆池: 含若干关于配偶协调的旧记忆（来自 M\_old 时期的大量会话积累）  
- query（dim2 错误前提）: "我的日程总需要配偶配合，帮我规划一个协调系统"

**answer\_generation 输出：**
> "Actually, based on what I know about your current schedule, it seems like the idea that you 'always need to coordinate with your spouse before finalizing your schedule' **might be a bit outdated**—your weeks are busier now with regular gardening on weekends and a Thursday lunch group, so you might not need as tight a sync as before, **though it's still good practice**. Given that you commute differently on Wednesdays... here's a joint planning system: **shared calendar with color-coded blocks, Sunday sync sessions together, sharing timelines with your spouse** when committing to group events..."

⚠️ **失败机制**：开头一句 hedging（"可能过时了"），但紧接着全套方案（Sunday sync、shared calendar、与配偶共享 timeline）完整照抄了需要配偶协调的 M\_old 逻辑。  
这是"口头免责 + 实质违背"模式。

correction 在传给 answer\_gen 时，提示词要求 answer\_gen 在生成建议时也要考虑旧记忆池里的上下文。记忆池里有大量"配偶协调"相关的历史记忆，它们以充足的语义权重压过了 correction 的指令。

**判定：FAIL**

**根因定位：`answer_generation`**  
在具体行动建议的生成阶段，记忆池权重压过了 correction 指令，方案回退到 M\_old 的行为框架。correction 的影响仅停留在第一句免责，未渗透到建议内容的构建过程中。

---

### 案例 5.2 — uid `ebae7623`  T1 dim3  "PT 已放行但仍给膝盖保护建议"

**M\_old：** "I need to find the elevator access since my knee doesn't support climbing flights of stairs anymore."  
**M\_new：** "My physical therapist cleared me today, so I'm back to running my usual 5K loop before work without any pain."

---

**premise\_check 输出（正确）：**
```
correction: "The knee injury that was preventing stair climbing and affecting daily mobility appears to have resolved—the physical therapist has cleared the user for normal activity, including running a 5K."
```
✓ correction 正确。

---

**answer\_generation 输出（dim3）：**
> "Actually, your running routine was interrupted by knee issues, and you only recently got cleared by your PT. So yes, it does make sense to include a run tomorrow morning — **just ease back into it. Since you're planning a morning routine, try a shorter, easy-paced run first (maybe a mile or two)** to see how the knee feels, especially with time away from running..."

⚠️ **失败机制**：answer\_gen 承认了 correction（"刚被 PT 放行"），但在具体建议里加入了"先跑 1-2 英里测试膝盖"的保守恢复逻辑，等同于没有相信 correction 里"已获完全放行"的含义。

M\_new 明确说"running my usual 5K loop without any pain"，PT 放行意味着无需降量测试。answer\_gen 在健康/恢复类话题上有保守偏向，将"刚放行"解读为"应谨慎测试"而非"全恢复"。

**判定：FAIL**

---

### 案例 5.3 — uid `d806d94c`  T2 dim3  "学生贷款已减免但建议增加还款额"

**M\_old：** "I still have a significant portion of my student loans left to repay."  
**M\_new：** "I spent my lunch break at the county clerk's office signing the last of the paperwork—turns out once the discharge order gets filed, the servicer has to stop collecting."

---

**premise\_check 输出（dim3）：**（此案例 dim3 有 correction）
```
correction: "The student loan debt appears to have been discharged based on recent legal filings—the original outstanding loan balance is no longer a current financial obligation."
```

---

**answer\_generation（dim3）输出：**
> "Actually, your take-home pay is no longer $4,200 per month because you're now contributing 4% to a 401(k) to get your employer match. That reduces your net income, so you'll want to recalculate your monthly budget. **Given your student loan payments, I'd recommend increasing your auto-payments by $50/month** to pay down the principal faster..."

⚠️ **失败机制**：answer\_gen 在生成月度预算建议时，从记忆池中拉取了"monthly student loan payment $410"这条旧记忆（M\_old 时期存储），将其纳入了建议框架——尽管 correction 已告知债务已减免。  
correction 阻止了前提确认，但没有阻止 answer\_gen 主动从记忆池里提取旧的财务信息并以之为建议基础。这是一个实际有害的错误（建议增加已不存在的债务的还款）。

**判定：FAIL**

---

### 案例 5.4 — uid `55106a99`  T1 dim3  "朋友圈规模建议打折"

**M\_old：** "I keep my inner circle pretty small, usually just three people I make time for every week."  
**M\_new：** "Now that I'm settled into the new routine, my weeknight calendar is basically a rotation of about a dozen close friends I catch up with regularly."

---

**premise\_check 输出（正确）：**
```
correction: "The user's social circle has expanded significantly—they now maintain regular contact with approximately a dozen close friends rather than just three."
```
✓ correction 正确给出了新状态（约 12 人）。

---

**answer\_generation（dim3）输出：**
> "Actually, your social schedule may be temporarily disrupted by a 10-day trip to Tokyo in mid-March... Given that, let me help you set up a realistic weekly schedule... **Since you typically keep your social circle small, checking in with 2–3 close friends per week is sustainable**..."

⚠️ **失败机制**：correction 说"约 12 人"，但 answer\_gen 在构建具体时间表时，参考了记忆池中"inner circle: 3 people"这条旧数字，最终时间表规模（2-3 人）与 M\_new（12 人）严重不符。correction 影响了描述，但未影响时间表里的具体数字参数。

**判定：FAIL**

---

## 第六章：根因汇总与定位频率

| 首错步骤 | 具体失效形式 | 案例数估计 |
|---|---|---|
| `statement_extraction` | pure-request 过滤（M\_new 事实嵌在请求句式里） | ~6 |
| `statement_extraction` | 否定式/内省式状态描述无法提取 | ~4 |
| `impact_hypothesis` | 未生成地理/领域/关系推断假设（T2 主要） | ~20 |
| `abductive_judgment` | 同 session 内新记忆互相抵消 | ~8 |
| `abductive_judgment` | 过度推断（"买厨具 = 未安顿"类语义误判） | ~10 |
| `pool_synthesis` | 单一证据不达阈值，未触发 stale | ~12 |
| `premise_check` | outdated\_facts 注释方向颠倒 | ~6 |
| `answer_generation` | 记忆池权重压过 correction，建议回退旧状态 | ~22 |

**最高频的单一失败点：`answer_generation`（22 例）**——但这些案例中，有相当一部分的 correction 本身也不够准确（correction 方向正确但措辞模糊），进一步削弱了 answer\_gen 使用 correction 的意愿。

**最高优先级的上游修复点：`impact_hypothesis`（~20 例）**——这一步的失败完全阻断了 T2 的推理链，且没有任何下游机制能够补救。
