#!/usr/bin/env python3
"""
gen_trace_doc.py — Generate a human-readable markdown document for all 32
failing cases from the RECAST 63d3571 run.

Output: /mnt/laq/RECAST/analysis_output/trace_readable_doc.md
"""

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Case manifest
# ---------------------------------------------------------------------------
CASES = [
    ("S01", "6ff5a576", "/mnt/laq/RECAST/runs/63d3571/improved_60_v2/0007/trace.json",  "T1"),
    ("S02", "34d402c0", "/mnt/laq/RECAST/runs/63d3571/improved_60_v2/0008/trace.json",  "T1"),
    ("S03", "e72a2ba5", "/mnt/laq/RECAST/runs/63d3571/improved_60_v2/0024/trace.json",  "T1"),
    ("S04", "7ee76c41", "/mnt/laq/RECAST/runs/63d3571/improved_60_v2/0029/trace.json",  "T1"),
    ("S05", "eee1a643", "/mnt/laq/RECAST/runs/63d3571/improved_60_v2/0052/trace.json",  "T1"),
    ("S06", "eacb64ff", "/mnt/laq/RECAST/runs/63d3571/improved_60_v2/0059/trace.json",  "T1"),
    ("S07", "e229c5cd", "/mnt/laq/RECAST/runs/63d3571/improved_60_v2/0064/trace.json",  "T1"),
    ("S08", "a6170008", "/mnt/laq/RECAST/runs/63d3571/improved_60_v2/0118/trace.json",  "T1"),
    ("S09", "d74f7f3e", "/mnt/laq/RECAST/runs/63d3571/improved_60_v2/0143/trace.json",  "T1"),
    ("S10", "dae22057", "/mnt/laq/RECAST/runs/63d3571/improved_60_v2/0147/trace.json",  "T1"),
    ("S11", "3305ce57", "/mnt/laq/RECAST/runs/63d3571/improved_60_v2/0154/trace.json",  "T1"),
    ("S12", "f6d12075", "/mnt/laq/RECAST/runs/63d3571/improved_60_v2/0194/trace.json",  "T1"),
    ("S13", "26e99c95", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0197/trace.json", "T2"),
    ("S14", "feef3933", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0201/trace.json", "T2"),
    ("S15", "ea1bd523", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0211/trace.json", "T2"),
    ("S16", "28daa975", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0223/trace.json", "T2"),
    ("S17", "5a4781fe", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0231/trace.json", "T2"),
    ("S18", "60604200", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0239/trace.json", "T2"),
    ("S19", "14897e47", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0240/trace.json", "T2"),
    ("S20", "06071a3e", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0256/trace.json", "T2"),
    ("S21", "f50107f1", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0270/trace.json", "T2"),
    ("S22", "d806d94c", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0274/trace.json", "T2"),
    ("S23", "4ad50bc6", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0278/trace.json", "T2"),
    ("S24", "a2a3e641", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0294/trace.json", "T2"),
    ("S25", "c2cc2d39", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0295/trace.json", "T2"),
    ("S26", "5ae24023", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0299/trace.json", "T2"),
    ("S27", "2c711459", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0312/trace.json", "T2"),
    ("S28", "855155ad", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0321/trace.json", "T2"),
    ("S29", "1469bde3", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0341/trace.json", "T2"),
    ("S30", "48707e03", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0358/trace.json", "T2"),
    ("S31", "c9cc370e", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0382/trace.json", "T2"),
    ("S32", "c03f7b53", "/mnt/laq/RECAST/runs/63d3571/improved_60_fill/0399/trace.json", "T2"),
]

# Merged scores file covers both T1 and T2
SCORES_PATH = Path("/mnt/laq/RECAST/runs/63d3571/improved_60_v2/scores_merged_60.json")
OUTPUT_PATH = Path("/mnt/laq/RECAST/analysis_output/trace_readable_doc.md")


# ---------------------------------------------------------------------------
# Load scorer index: uid8 -> {dim1: {pass, reasoning}, dim2: ..., dim3: ...}
# ---------------------------------------------------------------------------
def load_scorer_index(scores_path):
    index = {}
    if not scores_path.exists():
        return index
    with open(scores_path) as f:
        d = json.load(f)
    for item in d.get("details", []):
        uid8 = item.get("uid", "")[:8]
        ev = item.get("evaluation", {})
        index[uid8] = {
            "dim1": ev.get("dim1_eval", {}),
            "dim2": ev.get("dim2_eval", {}),
            "dim3": ev.get("dim3_eval", {}),
        }
    return index


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def trunc(s, n=150):
    """Truncate a string to n chars, appending '…' if truncated."""
    if s is None:
        return ""
    s = str(s).strip()
    return s[:n] + "…" if len(s) > n else s


def find_memory_statement(session_logs, session_index, keyword_text):
    """
    Find the statement in session `session_index` whose text best matches
    `keyword_text` (by word-overlap). Returns (stmt_text, item_id, filter,
    hypotheses, judgments_raw, judgment_logs).
    """
    for sess in session_logs:
        if sess.get("session_index") != session_index:
            continue
        stmts = sess.get("statement_log", [])
        ref_words = set(w.lower().strip(".,;:'\"") for w in keyword_text.split() if len(w) > 3)
        best, best_overlap = None, -1
        for stmt in stmts:
            txt_words = set(w.lower().strip(".,;:'\"") for w in stmt.get("statement", "").split() if len(w) > 3)
            overlap = len(ref_words & txt_words)
            if overlap > best_overlap:
                best_overlap = overlap
                best = stmt
        if best is None and stmts:
            best = stmts[0]
        if best:
            return (
                best.get("statement", ""),
                best.get("new_item_id"),
                best.get("hypothetical_filter", {}),
                best.get("hypotheses", []),
                best.get("judgments_raw", []),
                best.get("judgment_logs", []),
            )
        return None, None, {}, [], [], []
    return None, None, {}, [], [], []


def find_abductive_verdict_for(target_id, session_logs, new_session_index):
    """
    Search through ALL statements in new_session_index for a judgments_raw entry
    targeting target_id.  Returns (judgment_raw_dict, action_str) or (None, None).
    """
    if target_id is None:
        return None, None
    for sess in session_logs:
        if sess.get("session_index") != new_session_index:
            continue
        for stmt in sess.get("statement_log", []):
            raws = stmt.get("judgments_raw", [])
            logs = stmt.get("judgment_logs", [])
            for i, jr in enumerate(raws):
                if jr.get("target_item_id") == target_id:
                    action = logs[i].get("action") if i < len(logs) else None
                    return jr, action
    return None, None


def get_final_item_status(session_logs, item_id):
    """
    Scan ALL session judgment_logs to find the highest-priority action on item_id.
    Priority: stale/pool_triggered_stale > marked_uncertain > added_to_pool > discarded > (nothing=active).
    """
    if item_id is None:
        return "unknown"
    priority = {
        "stale": 5, "pool_triggered_stale": 5, "marked_stale": 5,
        "marked_uncertain": 3, "added_to_pool": 2,
        "discarded": 1,
    }
    best_p, best_action = 0, "active"
    for sess in session_logs:
        for stmt in sess.get("statement_log", []):
            for jl in stmt.get("judgment_logs", []):
                if jl.get("target_item_id") == item_id:
                    act = jl.get("action", "")
                    p = priority.get(act, 0)
                    if p > best_p:
                        best_p, best_action = p, act
    return best_action


def failed_dims_from_scorer(scorer_entry):
    """Return list of 'dim1'/'dim2'/'dim3' strings that FAILED per scorer."""
    failed = []
    for dim in ["dim1", "dim2", "dim3"]:
        ev = scorer_entry.get(dim, {})
        if ev.get("pass") is False:
            failed.append(dim)
    return failed


def fmt_dim_section(dim_key, q_data, scorer_entry, dim_short):
    """Format one query dimension with system verdict + scorer judgment."""
    if q_data is None:
        return f"**{dim_key}**: (no data)"
    vrd = q_data.get("verdict", {})
    pr = q_data.get("premise_result", {})

    ps = vrd.get("premise_safe")
    if ps is None:
        ps = pr.get("premise_safe")
    status = vrd.get("status", "—")

    outdated = pr.get("outdated_facts", "")
    if isinstance(outdated, list):
        outdated_str = "; ".join(str(x) for x in outdated)
    else:
        outdated_str = str(outdated or "")

    correction = pr.get("correction") or vrd.get("correction") or ""
    answer = q_data.get("answer", "") or ""

    # Scorer info
    sc = scorer_entry.get(dim_short, {}) if scorer_entry else {}
    sc_reasoning = trunc(sc.get("reasoning", ""), 200) if sc else ""

    lines = [
        f"**{dim_key}** (system: `{status}` premise_safe=`{ps}` | scorer: FAIL)",
        f"- outdated_facts: {trunc(outdated_str, 200) or '(none)'}",
        f"- correction: {trunc(str(correction), 200) or '(none)'}",
        f"- answer: \"{trunc(answer, 150)}\"",
    ]
    if sc_reasoning:
        lines.append(f"- scorer: \"{sc_reasoning}\"")
    return "\n".join(lines)


def classify_root_cause(m_old_id, m_new_id, old_filter, new_filter,
                        abductive_verdict, abductive_action,
                        old_item_status, failed_dims_list):
    """Heuristic root-cause classification. Returns a label string."""
    old_filt_dec = old_filter.get("decision", "") if old_filter else ""
    new_filt_dec = new_filter.get("decision", "") if new_filter else ""

    # Extraction miss
    if old_filt_dec == "SKIP":
        return "RC-extraction-miss: M_old filtered out (SKIP) — not stored in memory"
    if m_old_id is None:
        return "RC-extraction-miss: M_old not extracted or not stored"
    if new_filt_dec == "SKIP":
        return "RC-extraction-miss: M_new filtered out (SKIP) — no abductive run possible"
    if m_new_id is None:
        return "RC-extraction-miss: M_new not extracted or not stored"

    # Abductive miss
    if abductive_verdict is None:
        return "RC-abductive-miss: M_old not in candidate list for any M_new statement judgment"

    vtype = abductive_verdict.get("type", "")
    conf = float(abductive_verdict.get("confidence", 0.0))
    action = abductive_action or ""

    if action in ("stale", "pool_triggered_stale", "marked_stale"):
        # Staling happened but query stage still failed
        return "RC-answer_gen/premise_check-error: M_old staled but answer or correction was wrong"

    if vtype == "no_conflict":
        return f"RC-abductive-miss: typed no_conflict (conf={conf:.2f}) — M_old not staled"

    if conf < 0.5:
        return f"RC-abductive-miss: low confidence (conf={conf:.2f}) below stale threshold — M_old survived"

    if action == "discarded":
        return f"RC-abductive-miss: verdict discarded (conf={conf:.2f} < threshold)"

    if action in ("added_to_pool", "marked_uncertain"):
        return f"RC-abductive-miss: verdict action=`{action}` — M_old not staled, stays active"

    if old_item_status == "active":
        return "RC-abductive-miss: M_old stayed active after all session judgments"

    return "RC-unknown: inspect trace manually"


# ---------------------------------------------------------------------------
# Main formatter
# ---------------------------------------------------------------------------

def format_case(sid, uid_short, trace_path, typ, scorer_index):
    uid8 = uid_short[:8]
    path = Path(trace_path)
    if not path.exists():
        return f"## {sid} · {uid8} | {typ} | ERROR: trace not found\n\n"

    with open(path) as f:
        data = json.load(f)

    result = data.get("result", {})
    sm = result.get("sample_meta", {})
    session_logs = result.get("session_logs", [])
    query_logs = result.get("query_logs", {})

    m_old_text = sm.get("M_old", "")
    m_new_text = sm.get("M_new", "")
    rel_idx = sm.get("relevant_session_index", [None, None])
    old_sess_idx = rel_idx[0] if len(rel_idx) > 0 else None
    new_sess_idx = rel_idx[1] if len(rel_idx) > 1 else None

    # Scorer data
    scorer_entry = scorer_index.get(uid8, {})
    failed_dims = failed_dims_from_scorer(scorer_entry)
    failed_dim_str = ", ".join(failed_dims) if failed_dims else "unknown"

    # --- M_old statement (in old session) ---
    (old_stmt, m_old_id, old_filter, old_hyps,
     _, _) = find_memory_statement(session_logs, old_sess_idx, m_old_text)

    # --- M_new statement (in new session) ---
    (new_stmt, m_new_id, new_filter, new_hyps,
     _, _) = find_memory_statement(session_logs, new_sess_idx, m_new_text)

    # --- Abductive verdict: did new session judge M_old? ---
    abductive_verdict, abductive_action = find_abductive_verdict_for(
        m_old_id, session_logs, new_sess_idx)

    # --- Final status of M_old ---
    old_item_status = get_final_item_status(session_logs, m_old_id)

    # ---- Build markdown ----
    lines = []
    lines.append("---\n")
    lines.append(f"## {sid} · {uid8} | {typ} | FAIL: {failed_dim_str}\n")

    # Conflict
    lines.append("**冲突**")
    lines.append(f"- M_old (s{old_sess_idx}): \"{trunc(m_old_text, 200)}\"")
    lines.append(f"- M_new (s{new_sess_idx}): \"{trunc(m_new_text, 200)}\"")
    lines.append("")

    # Extraction
    lines.append("**提取**")
    old_filt_dec = old_filter.get("decision", "?") if old_filter else "?"
    old_filt_reason = old_filter.get("reason", "") if old_filter else ""
    if old_stmt is not None:
        lines.append(f"- s{old_sess_idx} → \"{trunc(old_stmt, 100)}\" → {m_old_id or 'None'} [{old_filt_dec}]")
        if old_filt_dec == "SKIP":
            lines.append(f"  - 过滤理由: {trunc(old_filt_reason, 130)}")
    else:
        lines.append(f"- s{old_sess_idx} → (未找到匹配语句) [{old_filt_dec}]")

    new_filt_dec = new_filter.get("decision", "?") if new_filter else "?"
    new_filt_reason = new_filter.get("reason", "") if new_filter else ""
    if new_stmt is not None:
        lines.append(f"- s{new_sess_idx} → \"{trunc(new_stmt, 100)}\" → {m_new_id or 'None'} [{new_filt_dec}]")
        if new_filt_dec == "SKIP":
            lines.append(f"  - 过滤理由: {trunc(new_filt_reason, 130)}")
        else:
            lines.append(f"  - 过滤器: {new_filt_dec}（{trunc(new_filt_reason, 90)}）")
            if new_hyps:
                lines.append("  - Impact 假设 (前3条):")
                for i, h in enumerate(new_hyps[:3], 1):
                    lines.append(f"    {i}. {trunc(h, 120)}")
    else:
        lines.append(f"- s{new_sess_idx} → (未找到匹配语句) [{new_filt_dec}]")
    lines.append("")

    # Abductive
    lines.append(f"**Abductive 判断** ({m_new_id or '?'} 对 {m_old_id or '?'} 的判断)")
    if m_old_id is None:
        lines.append("- M_old 未存储，无法进行 abductive 判断")
    elif abductive_verdict is None:
        lines.append(f"- 在候选列表中? 否（{m_old_id} 不在 s{new_sess_idx} 任何判断候选中）")
        lines.append("- 判断: (缺失)")
    else:
        vtype = abductive_verdict.get("type", "?")
        conf = abductive_verdict.get("confidence", "?")
        chain = trunc(str(abductive_verdict.get("inference_chain", "")), 180)
        action = abductive_action or "?"
        lines.append(f"- 在候选列表中? 是")
        lines.append(f"- 判断: type=`{vtype}` conf=`{conf}`")
        lines.append(f"- 推理链: \"{chain}\"")
        lines.append(f"- 动作: `{action}`")
    lines.append("")

    # M_old final status
    lines.append("**M_old 最终状态**")
    if m_old_id:
        is_problem = old_item_status not in ("stale", "pool_triggered_stale", "marked_stale")
        note = " ← 未被标记为stale" if is_problem else " ← 已stale（但查询阶段仍失败）"
        lines.append(f"- {m_old_id}: `{old_item_status}`{note}")
    else:
        lines.append("- M_old 未存储（extraction miss）")
    lines.append("")

    # Query phase — only failed dims
    lines.append(f"**查询阶段** (失败 dims: {failed_dim_str})")
    if failed_dims:
        for dim_short in failed_dims:
            dim_key = f"{dim_short}_query"
            q_data = query_logs.get(dim_key)
            lines.append(fmt_dim_section(dim_key, q_data, scorer_entry, dim_short))
    else:
        lines.append("(未能从评分数据确定失败 dim)")
    lines.append("")

    # Root cause
    rc = classify_root_cause(
        m_old_id, m_new_id, old_filter, new_filter,
        abductive_verdict, abductive_action,
        old_item_status, failed_dims
    )
    lines.append(f"**根因**: {rc}")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    scorer_index = load_scorer_index(SCORES_PATH)
    print(f"Loaded scorer data for {len(scorer_index)} UIDs")

    header = """\
# RECAST 63d3571 — 32 Failing Cases: Readable Trace Analysis

**Run**: 63d3571 (commit e77d3af)
**Types**: T1 = improved_60_v2 (sessions 1-30), T2 = improved_60_fill (sessions 31-60)
**Failure definition**: scorer (qwen3.6-plus) returned pass=False for at least one dim.
**Root cause labels**:
- RC-extraction-miss: M_old or M_new not stored in memory (filtered SKIP or zero extraction)
- RC-abductive-miss: M_old in memory but abductive judgment didn't stale it
- RC-answer_gen/premise_check-error: M_old correctly staled but final answer was wrong

---

"""
    sections = [header]
    for sid, uid_short, trace_path, typ in CASES:
        print(f"Processing {sid} ({uid_short[:8]}) … ", end="", flush=True)
        try:
            block = format_case(sid, uid_short, trace_path, typ, scorer_index)
            sections.append(block)
            print("OK")
        except Exception as e:
            import traceback
            sections.append(f"## {sid} · {uid_short[:8]} | {typ} | ERROR: {e}\n\n")
            print(f"ERROR: {e}")
            traceback.print_exc()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("".join(sections), encoding="utf-8")
    n_lines = OUTPUT_PATH.read_text().count("\n")
    print(f"\nWrote {len(CASES)} cases ({n_lines} lines) to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
