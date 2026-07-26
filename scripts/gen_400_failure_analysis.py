"""
DeepSeek v4-flash 直连 400 样本失败分析
数据来源：
  - d215489 e2ev3_full340 (340 样本, e2ev3 pipeline)
  - afc4c62 e2ev3_remaining45 (45 样本, e2ev3 pipeline)
  - 807dee0 crash_fix_5 (15 样本, old premise_check pipeline)
评分：d215489/merged_full_v3/scores_T1.json + scores_T2.json (qwen3.6-plus judge)
输出：analysis_output/dpsk_400_failure_analysis.md
"""
import json, glob, os
from collections import defaultdict

SCORE_T1   = "/mnt/laq/RECAST/runs/d215489/merged_full_v3/scores_T1.json"
SCORE_T2   = "/mnt/laq/RECAST/runs/d215489/merged_full_v3/scores_T2.json"
DATASET    = "/mnt/laq/RECAST/STALE/STALE/outputs/STALE_MAIN.json"
OUT_PATH   = "/mnt/laq/RECAST/analysis_output/dpsk_400_failure_analysis.md"

TRACE_SEARCH_DIRS = [
    # e2ev3 pipeline
    "/mnt/laq/RECAST/runs/d215489/e2ev3_full340",
    "/mnt/laq/RECAST/runs/d215489/e2ev3_t2_remaining",
    "/mnt/laq/RECAST/runs/afc4c62/e2ev3_remaining45",
    "/mnt/laq/RECAST/runs/474e206/remaining45",
    # old pipeline (premise_check)
    "/mnt/laq/RECAST/runs/807dee0/crash_fix_5",
    "/mnt/laq/RECAST/runs/807dee0/full_60",
    "/mnt/laq/RECAST/runs/908399d/improved_60_rest",
]

# ─── 加载数据集 ───────────────────────────────────────────────────────────────
print("Loading dataset...")
dataset = json.load(open(DATASET))
uid_to_rec = {str(r.get("uid",""))[:8]: r for r in dataset}

# ─── 加载评分 ─────────────────────────────────────────────────────────────────
print("Loading scores...")
scores = {}
for path, ctype in [(SCORE_T1,"T1"), (SCORE_T2,"T2")]:
    for item in json.load(open(path))["details"]:
        uid8 = str(item["uid"])[:8]
        ev   = item.get("evaluation", {})
        scores[uid8] = {
            "uid":  item["uid"],
            "type": ctype,
            "dim1": ev.get("dim1_eval", {}).get("pass", None),
            "dim2": ev.get("dim2_eval", {}).get("pass", None),
            "dim3": ev.get("dim3_eval", {}).get("pass", None),
        }
print(f"  {len(scores)} scored samples")

# ─── 构建 trace 索引 ──────────────────────────────────────────────────────────
print("Building trace index...")
trace_index = {}   # uid8 -> (path, pipeline_version)

for d in TRACE_SEARCH_DIRS:
    for f in glob.glob(f"{d}/*/trace.json"):
        try:
            with open(f) as fp:
                uid_raw = json.load(fp).get("uid", "")
            uid8 = str(uid_raw)[:8]
            if uid8 not in trace_index:
                trace_index[uid8] = f
        except:
            pass

print(f"  {len(trace_index)} traces found")

def get_pipeline(trace):
    phases = {c.get("phase") for c in trace.get("call_records", [])}
    return "e2ev3" if "answer_generation_v2" in phases else "old_premise_check"

# ─── 分类函数 ─────────────────────────────────────────────────────────────────

def classify_sample(trace, failing_dims):
    pipeline = get_pipeline(trace)
    r        = trace["result"]
    fps      = r.get("final_profile_snapshot", {})
    stale_items = fps.get("stale_items", [])
    stale_ids   = {it["item_id"] for it in stale_items}
    stale_content = {it["item_id"]: it.get("content","") for it in stale_items}
    ql       = r.get("query_logs", {})

    dim_results = {}
    for dim in failing_dims:
        q    = ql.get(f"dim{dim}_query", {})
        rids = q.get("retrieved_ids") or []
        if isinstance(rids, dict):
            retrieved_stale = set(rids.get("stale_ids", []))
            all_rids        = (set(rids.get("active_ids",[])) |
                               set(rids.get("uncertain_ids",[])) | retrieved_stale)
        else:
            all_rids        = set(rids)
            retrieved_stale = all_rids & stale_ids

        verdict      = q.get("verdict", {})
        premise_safe = verdict.get("premise_safe", True)
        answer       = str(q.get("answer", "")).strip()

        # ── classify ──
        if len(stale_ids) == 0:
            stage  = "RC-NOSTALE"
            detail = "Write phase produced no stale items — conflict exists in dataset but pipeline never detected M_old→M_new transition."
        elif len(retrieved_stale) == 0 and len(all_rids) > 0:
            stage  = "RC-RETRIEVAL"
            detail = (f"Stale items exist ({len(stale_ids)}) but NONE retrieved for dim{dim} query "
                      f"(retrieved {len(all_rids)} items, all active/uncertain). "
                      f"Stale IDs not retrieved: {sorted(stale_ids)[:5]}")
        else:
            if pipeline == "old_premise_check":
                if premise_safe:
                    stage  = "RC-REASONING"
                    detail = ("premise_check returned premise_safe=True despite stale items being retrieved. "
                              "Model failed to match stale content against query assumption.")
                else:
                    stage  = "RC-ANSWER-GEN"
                    detail = ("premise_check correctly flagged premise_safe=False, but answer_generation "
                              "still produced an answer that accepted the outdated premise.")
            else:  # e2ev3 — single E2E call, no premise_check
                stage  = "RC-REASONING"
                detail = (f"Stale items retrieved ({len(retrieved_stale)}/{len(stale_ids)} stale IDs in top-k), "
                          f"but E2E model failed to surface/flag the conflict in its answer.")

        dim_results[dim] = {
            "stage":           stage,
            "pipeline":        pipeline,
            "detail":          detail,
            "stale_ids_total": sorted(stale_ids),
            "stale_sample_content": [f"{k}: {stale_content[k][:80]}" for k in sorted(stale_ids)[:3]],
            "retrieved_stale": sorted(retrieved_stale) if isinstance(retrieved_stale, set) else "N/A",
            "all_retrieved":   len(all_rids),
            "premise_safe":    premise_safe if pipeline == "old_premise_check" else "N/A (e2ev3)",
            "answer_snippet":  answer[:300],
        }

    return dim_results


# ─── 分析循环 ─────────────────────────────────────────────────────────────────
print("Analysing failures...")

buckets    = defaultdict(list)  # stage -> [records]
no_trace   = []
all_samples = 0

stage_priority = {"RC-NOSTALE":0,"RC-RETRIEVAL":1,"RC-REASONING":2,"RC-ANSWER-GEN":3}

for uid8, sc in scores.items():
    fail_dims = [d for d in [1,2,3] if sc.get(f"dim{d}") is False]
    if not fail_dims:
        continue
    all_samples += 1

    if uid8 not in trace_index:
        no_trace.append(uid8)
        continue

    try:
        trace = json.load(open(trace_index[uid8]))
    except:
        no_trace.append(uid8)
        continue

    rec   = uid_to_rec.get(uid8, {})
    m_old = str(rec.get("M_old", rec.get("m_old","(not found)")))[:250]
    m_new = str(rec.get("M_new", rec.get("m_new","(not found)")))[:250]

    dim_results  = classify_sample(trace, fail_dims)
    dom_stage    = max(dim_results.values(), key=lambda x: stage_priority.get(x["stage"],0))["stage"]
    pipeline_ver = get_pipeline(trace)

    buckets[dom_stage].append({
        "uid8":         uid8,
        "uid":          sc["uid"],
        "type":         sc["type"],
        "pipeline":     pipeline_ver,
        "dims_failed":  fail_dims,
        "dims_pass":    [d for d in [1,2,3] if sc.get(f"dim{d}") is True],
        "dim_results":  dim_results,
        "m_old":        m_old,
        "m_new":        m_new,
    })

print(f"\nTotal failing samples: {all_samples}")
for stage in ["RC-NOSTALE","RC-RETRIEVAL","RC-REASONING","RC-ANSWER-GEN"]:
    print(f"  {stage}: {len(buckets.get(stage,[]))}")
print(f"  No trace: {len(no_trace)}")

# ─── 报告 ─────────────────────────────────────────────────────────────────────
print("\nWriting report...")

def icon(sc, dim):
    v = sc.get(f"dim{dim}")
    return "✅" if v is True else ("❌" if v is False else "?")

def pct(a,b): return f"{a/b*100:.1f}%" if b else "?%"

s1sum = json.load(open(SCORE_T1))["summary"]["accuracy"]["T1"]
s2sum = json.load(open(SCORE_T2))["summary"]["accuracy"]["T2"]

STAGE_TITLE = {
    "RC-NOSTALE":    "第一章 RC-NOSTALE — Write Phase 未生成 Stale Item",
    "RC-RETRIEVAL":  "第二章 RC-RETRIEVAL — Stale Item 存在但检索未命中",
    "RC-REASONING":  "第三章 RC-REASONING — Stale Item 被检索到但模型推理失败",
    "RC-ANSWER-GEN": "第四章 RC-ANSWER-GEN — Premise Check 正确但 Answer Generation 失败",
}
STAGE_INTRO = {
    "RC-NOSTALE": (
        "Write phase（statement_extraction → abductive_judgment → pool_synthesis）未能识别 M_old→M_new 的状态转换，"
        "导致没有 stale item 写入 memory store。查询时即使检索正确，也没有 stale 信号可用。\n\n"
        "**可能根因**：abductive judgment 阈值过高、haystack sessions 中冲突信号太弱、"
        "statement extraction 提取的陈述未覆盖 M_old 的关键属性。"
    ),
    "RC-RETRIEVAL": (
        "Memory store 中存在 stale items（write phase 成功），但在 retrieval 阶段，"
        "相关 stale items 的嵌入相似度低于 active items，未进入 top-k 被检索到。\n\n"
        "**可能根因**：stale item 内容表述与查询语义距离远、"
        "embedding model 对 stale 状态的语义区分能力不足、retrieve_k 太小。"
    ),
    "RC-REASONING": (
        "Stale items 已被检索到，但模型（E2E v3 单次调用或旧 pipeline 的 premise_check）"
        "未能识别 stale item 与查询 assumption 之间的矛盾，给出了接受过时前提的回答。\n\n"
        "**可能根因**：模型 4-step CoT 推理能力不足（premise_check 87% 错误率）、"
        "E2E prompt 未强制 per-item 对比、stale_reason 表述不精确导致矛盾不明显。"
    ),
    "RC-ANSWER-GEN": (
        "Premise check 正确返回 premise_safe=False，但 answer_generation 仍生成了接受错误前提的答案。\n\n"
        "**可能根因**：correction 字段作为参考信息而非硬性指令注入 answer_generation prompt；"
        "模型在生成阶段推理不稳定，未充分遵循 correction。"
    ),
}

L = []
L += [
    "# DeepSeek v4-flash 直连 400 样本失败分析",
    "",
    f"**Pipeline 版本**：e2ev3（340+45=385 样本）+ old premise_check（15 样本）  ",
    f"**评分模型**：qwen3.6-plus（直连 Dashscope）  ",
    f"**生成时间**：2026-06-16",
    "",
    "---",
    "",
    "## 总体指标",
    "",
    "| | T1 (n=200) | T2 (n=200) | 合计 |",
    "|---|---|---|---|",
    f"| SR (dim1) | {pct(s1sum['dim1']['correct'],200)} ({s1sum['dim1']['correct']}/200)"
    f" | {pct(s2sum['dim1']['correct'],200)} ({s2sum['dim1']['correct']}/200)"
    f" | {pct(s1sum['dim1']['correct']+s2sum['dim1']['correct'],400)} |",
    f"| PR (dim2) | {pct(s1sum['dim2']['correct'],200)} ({s1sum['dim2']['correct']}/200)"
    f" | {pct(s2sum['dim2']['correct'],200)} ({s2sum['dim2']['correct']}/200)"
    f" | {pct(s1sum['dim2']['correct']+s2sum['dim2']['correct'],400)} |",
    f"| IPA (dim3)| {pct(s1sum['dim3']['correct'],200)} ({s1sum['dim3']['correct']}/200)"
    f" | {pct(s2sum['dim3']['correct'],200)} ({s2sum['dim3']['correct']}/200)"
    f" | {pct(s1sum['dim3']['correct']+s2sum['dim3']['correct'],400)} |",
    f"| **Overall**| **{pct(s1sum['overall']['correct'],600)}**"
    f" | **{pct(s2sum['overall']['correct'],600)}**"
    f" | **{pct(s1sum['overall']['correct']+s2sum['overall']['correct'],1200)}** |",
    "",
    "---",
    "",
    "## 失败样本根因分布",
    "",
    f"失败样本总数（至少一个 dim 失败）：**{all_samples}**（另有 {len(no_trace)} 个无 trace）",
    "",
]

total_fail = sum(len(v) for v in buckets.values())
for stage in ["RC-NOSTALE","RC-RETRIEVAL","RC-REASONING","RC-ANSWER-GEN"]:
    cnt = len(buckets.get(stage,[]))
    t1_cnt = sum(1 for s in buckets.get(stage,[]) if s["type"]=="T1")
    t2_cnt = sum(1 for s in buckets.get(stage,[]) if s["type"]=="T2")
    L.append(f"| {stage} | {cnt} ({pct(cnt,total_fail)}) | T1={t1_cnt} / T2={t2_cnt} |")

L += [
    "",
    "（以下各章按失败阶段分类，每章内按 T1/T2 排序）",
    "",
]

# ─── 各章节 ───────────────────────────────────────────────────────────────────
for stage in ["RC-NOSTALE","RC-RETRIEVAL","RC-REASONING","RC-ANSWER-GEN"]:
    items = buckets.get(stage, [])
    title = STAGE_TITLE[stage]
    intro = STAGE_INTRO[stage]

    L += [
        "---",
        "",
        f"## {title}",
        "",
        f"> {intro}",
        "",
        f"**本章样本数：{len(items)}**",
        "",
    ]
    if not items:
        L += ["*（无）*", ""]
        continue

    # For RC-REASONING: sub-classify by which dims failed
    if stage == "RC-REASONING":
        subgroups = {
            "d1+d2+d3": [s for s in items if set(s["dims_failed"])=={1,2,3}],
            "d2+d3":    [s for s in items if set(s["dims_failed"])=={2,3}],
            "d1+d2":    [s for s in items if set(s["dims_failed"])=={1,2}],
            "d1+d3":    [s for s in items if set(s["dims_failed"])=={1,3}],
            "d1 only":  [s for s in items if s["dims_failed"]==[1]],
            "d2 only":  [s for s in items if s["dims_failed"]==[2]],
            "d3 only":  [s for s in items if s["dims_failed"]==[3]],
        }
        SUBGROUP_DESC = {
            "d1+d2+d3": "无法识别 stale + 无法拒绝错误前提 + 行动合规性全错",
            "d2+d3":    "能识别哪条 stale（d1 pass），但被迫接受错误前提 + IPA 错",
            "d1+d2":    "既不识别 stale 也不拒绝错误前提，但 IPA 偶然正确",
            "d1+d3":    "不识别 stale，IPA 也错，但 PR 偶然正确",
            "d1 only":  "只有 SR 失败：能拒绝前提但无法指认 stale 记忆",
            "d2 only":  "只有 PR 失败：接受了错误前提，但 SR+IPA 正确",
            "d3 only":  "只有 IPA 失败：行动建议接受了过时状态，但 SR+PR 正确",
        }
        L += [
            "### 子分类总览（按失败维度组合）",
            "",
            "| 失败组合 | 含义 | 数量 |",
            "|---|---|---|",
        ]
        for key in ["d1+d2+d3","d2+d3","d1+d2","d1+d3","d1 only","d2 only","d3 only"]:
            cnt=len(subgroups[key])
            if cnt:
                L.append(f"| **{key}** | {SUBGROUP_DESC[key]} | **{cnt}** |")
        L.append("")

        # Output each subgroup with header immediately followed by its samples
        for key in ["d1+d2+d3","d2+d3","d1+d2","d1+d3","d1 only","d2 only","d3 only"]:
            group = sorted(subgroups[key], key=lambda x:(x["type"],x["uid8"]))
            if not group:
                continue
            L += [f"### 子类 {key} — {SUBGROUP_DESC[key]}（{len(group)} 个）", ""]
            for s in group:
                uid8   = s["uid8"]
                sc     = scores[uid8]
                ptag   = f"`{s['pipeline']}`"
                L += [
                    f"#### {uid8} · {s['type']} · {ptag}",
                    "",
                    f"| dim1 SR | dim2 PR | dim3 IPA |",
                    f"|---|---|---|",
                    f"| {icon(sc,1)} | {icon(sc,2)} | {icon(sc,3)} |",
                    "",
                    f"- **M_old**：{s['m_old']}",
                    f"- **M_new**：{s['m_new']}",
                    "",
                ]
                for dim in s["dims_failed"]:
                    dr = s["dim_results"][dim]
                    rs = dr.get("retrieved_stale","")
                    rs_str = (f"{sorted(rs)[:5]}{'...' if len(rs)>5 else ''}"
                              if isinstance(rs, (list,set)) and rs else str(rs))
                    L += [
                        f"**dim{dim} 失败**",
                        f"- 阶段：`{dr['stage']}`",
                        f"- stale 总数 {len(dr['stale_ids_total'])} 个，检索到 {len(rs) if isinstance(rs,(list,set)) else 'N/A'} 个 stale",
                    ]
                    if dr["stale_sample_content"]:
                        L.append(f"- stale 内容样本：{' | '.join(dr['stale_sample_content'])}")
                    L += [
                        f"- 答案：*{dr['answer_snippet'].replace(chr(10),' ')[:250]}*",
                        "",
                    ]
                L.append("")
        # Skip the generic loop for RC-REASONING
        continue
    else:
        items_ordered = sorted(items, key=lambda x: (x["type"], x["uid8"]))

    for s in items_ordered:
        uid8   = s["uid8"]
        sc     = scores[uid8]
        dfail  = "/".join(f"d{d}" for d in s["dims_failed"])
        dpass  = "/".join(f"d{d}" for d in s["dims_pass"])
        ptag   = f"`{s['pipeline']}`"

        L += [
            f"### {uid8} · {s['type']} · {ptag}",
            "",
            f"| 维度 | 结果 |",
            f"|---|---|",
            f"| dim1 (SR) | {icon(sc,1)} |",
            f"| dim2 (PR) | {icon(sc,2)} |",
            f"| dim3 (IPA)| {icon(sc,3)} |",
            "",
            f"- **M_old**（旧状态）：{s['m_old']}",
            f"- **M_new**（新状态）：{s['m_new']}",
            "",
        ]

        for dim in s["dims_failed"]:
            dr = s["dim_results"][dim]
            L += [
                f"#### dim{dim} 失败分析",
                "",
                f"- **失败阶段**：`{dr['stage']}`",
                f"- **根因**：{dr['detail']}",
                f"- **Stale items 总数**：{len(dr['stale_ids_total'])}",
            ]
            if dr["stale_sample_content"]:
                L.append(f"- **Stale item 内容样本**：")
                for item_str in dr["stale_sample_content"]:
                    L.append(f"  - {item_str}")
            if dr.get("retrieved_stale") and dr["retrieved_stale"] != "N/A":
                rs = dr["retrieved_stale"]
                L.append(f"- **检索到的 stale IDs**（{len(rs)} 个）：{rs[:5]}{'...' if len(rs)>5 else ''}")
            elif dr["retrieved_stale"] == "N/A":
                L.append("- **检索到的 stale IDs**：N/A（old pipeline 未存储 retrieved_ids）")
            L.append(f"- **premise_safe**：{dr['premise_safe']}")
            L += [
                f"- **模型答案（前300字）**：",
                f"  > {dr['answer_snippet'].replace(chr(10), ' ').replace('>', '')}",
                "",
            ]

        L.append("")

# ─── 附录 ─────────────────────────────────────────────────────────────────────
if no_trace:
    L += [
        "---", "",
        "## 附录：无 trace 文件的样本",
        "",
        f"以下 {len(no_trace)} 个样本未找到 trace.json：",
        "",
    ]
    for uid8 in no_trace:
        sc = scores.get(uid8,{})
        L.append(f"- `{uid8}` ({sc.get('type','?')}) "
                 f"d1={icon(sc,1)} d2={icon(sc,2)} d3={icon(sc,3)}")

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with open(OUT_PATH, "w", encoding="utf-8") as f:
    f.write("\n".join(L))

wc = os.path.getsize(OUT_PATH)
print(f"Done → {OUT_PATH}  ({len(L)} lines, {wc/1024:.0f} KB)")
