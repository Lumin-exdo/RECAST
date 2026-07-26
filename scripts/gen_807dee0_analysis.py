#!/usr/bin/env python3
"""
Generate 807dee0_failure_analysis.md from trace files.
"""
import json
import os
import re
import difflib
from typing import Optional

BASE = "/mnt/laq/RECAST/runs/807dee0/full_60"
OUTPUT = "/mnt/laq/RECAST/analysis_output/807dee0_failure_analysis.md"

# ── UID → directory mapping ──────────────────────────────────────────────────
UID8_TO_DIR = {}
for _d in sorted(os.listdir(BASE)):
    _tp = os.path.join(BASE, _d, "trace.json")
    if os.path.exists(_tp):
        with open(_tp) as _f:
            _tr = json.load(_f)
        _uid = _tr["result"]["sample_meta"].get("uid", "")
        UID8_TO_DIR[_uid[:8]] = _d


def load_trace(uid8: str) -> dict:
    d = UID8_TO_DIR.get(uid8)
    if not d:
        return {}
    with open(os.path.join(BASE, d, "trace.json")) as f:
        return json.load(f)


# ── Scores ────────────────────────────────────────────────────────────────────
with open(os.path.join(BASE, "scores.json")) as f:
    scores_807 = json.load(f)["details"]

with open(os.path.join(BASE, "qonly_fix5_scores.json")) as f:
    scores_qf5 = json.load(f)["details"]

with open("/mnt/laq/RECAST/runs/63d3571/improved_60_v2/scores_merged_60.json") as f:
    scores_qonly = json.load(f)["details"]


def build_score_index(details):
    idx = {}
    for d in details:
        uid8 = d["uid"][:8]
        ev = d.get("evaluation", {})
        idx[uid8] = {}
        for dim in ("dim1", "dim2", "dim3"):
            key = f"{dim}_eval"
            if key in ev:
                idx[uid8][dim] = ev[key]
    return idx


score807_idx = build_score_index(scores_807)
scoreqf5_idx = build_score_index(scores_qf5)
scoreqonly_idx = build_score_index(scores_qonly)


def get_807_score(uid8, dim):
    return score807_idx.get(uid8, {}).get(dim, {})


def get_qonly_score(uid8, dim):
    s = scoreqonly_idx.get(uid8, {}).get(dim, {})
    if not s:
        s = scoreqf5_idx.get(uid8, {}).get(dim, {})
    return s


# ── qonly answers ─────────────────────────────────────────────────────────────
with open("/mnt/laq/RECAST/runs/63d3571/improved_60_v2/answers_merged_60.json") as f:
    qonly_answers_list = json.load(f)
with open(os.path.join(BASE, "qonly_fix5_answers.json")) as f:
    qf5_answers_list = json.load(f)

qonly_ans_idx = {}
for item in qonly_answers_list:
    uid8 = item["uid"][:8]
    qonly_ans_idx[uid8] = item.get("target_model_responses", {})
for item in qf5_answers_list:
    uid8 = item["uid"][:8]
    if uid8 not in qonly_ans_idx:
        qonly_ans_idx[uid8] = item.get("target_model_responses", {})


def get_qonly_answer(uid8, dim):
    resp = qonly_ans_idx.get(uid8, {})
    return resp.get(f"{dim}_response", "")


# ── Helper: find best-matching memory item ────────────────────────────────────
def extract_keywords(text: str, min_len=4) -> list:
    return [w.lower() for w in re.findall(r'\b\w{%d,}\b' % min_len, text)]


def find_matching_item(snapshot, target_text: str):
    """
    Multi-strategy search: first try keyword overlap (favors semantic match),
    then fall back to sequence similarity.
    Returns (item, score, method) or (None, 0, 'none').
    """
    all_items = (
        snapshot.get("active_items", []) +
        snapshot.get("uncertain_items", []) +
        snapshot.get("stale_items", [])
    )
    if not all_items:
        return None, 0.0, "none"

    target_kws = set(extract_keywords(target_text))
    target_lower = target_text.lower()

    # Strategy 1: keyword overlap ratio
    kw_scored = []
    for item in all_items:
        content = item.get("content", "")
        item_kws = set(extract_keywords(content))
        if target_kws and item_kws:
            overlap = len(target_kws & item_kws) / len(target_kws | item_kws)
        else:
            overlap = 0.0
        kw_scored.append((overlap, item))
    kw_scored.sort(reverse=True, key=lambda x: x[0])
    best_kw_score, best_kw_item = kw_scored[0]

    # Strategy 2: sequence similarity
    seq_scored = []
    for item in all_items:
        content = item.get("content", "")
        ratio = difflib.SequenceMatcher(None, target_lower, content.lower()).ratio()
        seq_scored.append((ratio, item))
    seq_scored.sort(reverse=True, key=lambda x: x[0])
    best_seq_score, best_seq_item = seq_scored[0]

    # Use keyword if score is decent, otherwise sequence
    if best_kw_score >= 0.20:
        return best_kw_item, best_kw_score, "keyword"
    elif best_seq_score >= 0.5:
        return best_seq_item, best_seq_score, "sequence"
    else:
        # Low confidence — return best keyword anyway but flag
        return best_kw_item, best_kw_score, "low_confidence"


# ── Helper: find judgment for a specific target item ─────────────────────────
def find_judgment_for_item(stmt_entry, target_id):
    for jr in stmt_entry.get("judgments_raw", []):
        if jr.get("target_item_id") == target_id:
            return jr
    return None


# ── Helper: find ALL appearances of M_old item in candidate_ids ──────────────
def find_mold_in_candidates(session_logs, mold_item_id: str):
    results = []
    for slog in session_logs:
        sidx = slog.get("session_index", -1)
        for stmt_entry in slog.get("statement_log", []):
            if mold_item_id in stmt_entry.get("candidate_ids", []):
                results.append((sidx, stmt_entry))
    return results


# ── Helper: get sessions at relevant_session_index ────────────────────────────
def get_relevant_session_stmts(session_logs, rsi):
    """rsi may be int or list of ints."""
    if isinstance(rsi, int):
        rsi_set = {rsi}
    elif isinstance(rsi, list):
        rsi_set = set(rsi)
    else:
        return []

    stmts = []
    for slog in session_logs:
        if slog.get("session_index") in rsi_set:
            for stmt_entry in slog.get("statement_log", []):
                stmts.append((slog["session_index"], stmt_entry))
    return stmts


# ── Main analysis function ────────────────────────────────────────────────────
def analyze_sample(uid8: str, ttag: str, failing_dims: list, failure_category: str) -> str:
    tr = load_trace(uid8)
    if not tr:
        return f"## Sample ??? — {uid8} ({ttag})\n\nTRACE NOT FOUND\n\n"

    result = tr["result"]
    meta = result["sample_meta"]
    sample_index = meta.get("sample_index", "???")
    m_old = meta.get("M_old", "")
    m_new = meta.get("M_new", "")
    explanation = meta.get("explanation", "")
    conflict_type = meta.get("conflict_type", "unknown")
    rsi = meta.get("relevant_session_index", -1)

    session_logs = result.get("session_logs", [])
    query_logs = result.get("query_logs", {})
    snapshot = result.get("final_profile_snapshot", {})

    # Normalize failing dims to set of "1","2","3"
    fail_set = set()
    for fd in failing_dims:
        fd_clean = fd.replace("dim", "").strip()
        fail_set.add(fd_clean)

    lines = []
    lines.append(f"## Sample {str(sample_index).zfill(4)} — {uid8} ({ttag})")
    lines.append("")
    lines.append(f"**M_old:** {m_old}")
    lines.append(f"**M_new:** {m_new}")
    lines.append(f"**Conflict type:** {conflict_type}")
    lines.append(f"**Explanation:** {explanation[:250]}{'...' if len(explanation) > 250 else ''}")
    lines.append("")

    # ── Find M_old item in snapshot ──────────────────────────────────────────
    mold_item, match_score, match_method = find_matching_item(snapshot, m_old)

    # Sanity check: if match is low confidence, warn
    if mold_item and match_score >= 0.15:
        lines.append("### M_old Memory Item in Final Snapshot")
        lines.append(f"- **item_id:** `{mold_item['item_id']}`")
        lines.append(f"- **content:** {mold_item['content']}")
        status = mold_item.get("status", "unknown")
        status_note = " ← SHOULD BE STALE (miss)" if status.lower() in ("active",) and failure_category == "premise_check_miss" else ""
        lines.append(f"- **status:** {status}{status_note}")
        lines.append(f"- **confidence:** {mold_item.get('confidence', 'N/A')}")
        lines.append(f"- **category:** {mold_item.get('category', 'N/A')}")
        lines.append(f"- **match quality:** {match_method} ({match_score:.2f})")
        if match_score < 0.25:
            lines.append(f"- **WARNING:** Low match confidence — M_old content may not be stored in memory at all")
        mold_item_id = mold_item["item_id"]
        lines.append("")
    else:
        lines.append("### M_old Memory Item in Final Snapshot")
        lines.append("_M_old content not found in memory snapshot — likely was never extracted/stored_")
        lines.append("")
        mold_item_id = None

    # ── Write phase analysis ─────────────────────────────────────────────────
    lines.append("### Write Phase Analysis")
    lines.append("")

    if ttag == "T2":
        # T2: M_new is presented at query time in the question, NOT as a session statement
        # The write phase sessions only contain the user's background life events
        # The conflict detection must happen at query time (premise_check) using M_new from the question
        lines.append("**T2 note:** M_new is presented as context in the query question, NOT as a session statement.")
        lines.append("The write phase has no opportunity to detect the M_new→M_old conflict.")
        lines.append("Conflict detection depends entirely on premise_check processing the question context.")
        lines.append("")

        # For T2, show if M_old is in any session
        m_old_kws = extract_keywords(m_old, min_len=4)
        stored_session = None
        for slog in session_logs:
            for s in slog.get("statement_log", []):
                stmt = s["statement"].lower()
                if sum(1 for kw in m_old_kws if kw in stmt) >= 2:
                    stored_session = (slog["session_index"], s["statement"])
                    break
            if stored_session:
                break

        if stored_session:
            lines.append(f"**M_old found in session {stored_session[0]}:** _{stored_session[1][:100]}_")
            lines.append("M_old was stored in memory — premise_check had M_old available but still gave false-safe verdict.")
        else:
            lines.append("**M_old was NOT directly stored as a session statement.**")
            lines.append("It appears in the memory store only via related context (or not at all).")
        lines.append("")

        # For T2, check if M_old item appeared in candidates during M_new-adjacent sessions
        # (unlikely but check anyway)
        if mold_item_id:
            all_hits = find_mold_in_candidates(session_logs, mold_item_id)
            if all_hits:
                # Show if any M_new related statement triggered M_old as candidate
                m_new_kws = set(extract_keywords(m_new, min_len=4))
                mnew_hits = [(si, s) for si, s in all_hits
                             if sum(1 for kw in m_new_kws if kw in s.get("statement","").lower()) >= 2]
                if mnew_hits:
                    lines.append(f"**M_new-related statements that included M_old item in candidates:**")
                    for si, s in mnew_hits[:2]:
                        jr = find_judgment_for_item(s, mold_item_id)
                        if jr:
                            lines.append(f"  - Session {si}: _{s.get('statement','')[:80]}_")
                            lines.append(f"    verdict={jr.get('type', jr.get('verdict','?'))}, chain: {jr.get('inference_chain','')[:100]}")
                else:
                    lines.append(f"M_old item appeared in candidate_ids for {len(all_hits)} unrelated statements only — none M_new-related.")
            lines.append("")
    else:
        # T1: M_new IS a session statement; check what happened in the relevant session
        rel_stmts = get_relevant_session_stmts(session_logs, rsi)
        lines.append(f"**Relevant session index:** {rsi}")
        lines.append("")

        if not rel_stmts:
            lines.append(f"_No statements found in session(s) {rsi}_")
            lines.append("")
        else:
            for sidx, stmt_entry in rel_stmts[:8]:
                stmt_text = stmt_entry.get("statement", "")
                hypotheses = stmt_entry.get("hypotheses", [])
                candidate_ids = stmt_entry.get("candidate_ids", [])
                hf = stmt_entry.get("hypothetical_filter", {})
                hf_verdict = hf.get("verdict", "N/A") if hf else "N/A"
                hf_reason = str(hf.get("reason", "")) if hf else ""

                # Does this statement contain M_new keywords?
                m_new_kws = extract_keywords(m_new, min_len=4)
                is_mnew_related = sum(1 for kw in m_new_kws if kw in stmt_text.lower()) >= 2

                lines.append(f"**Session {sidx} Statement:**  _{stmt_text}_")
                if is_mnew_related:
                    lines.append("  ← *M_new-related statement*")
                lines.append("")
                lines.append(f"- Hypothetical filter: {hf_verdict} — {hf_reason[:120]}")

                if hypotheses:
                    lines.append("- Impact hypotheses:")
                    for h in hypotheses[:5]:
                        lines.append(f"  - {h}")

                lines.append(f"- Candidate IDs: {candidate_ids[:8] if candidate_ids else '(none)'}")

                if mold_item_id:
                    if mold_item_id in candidate_ids:
                        jr = find_judgment_for_item(stmt_entry, mold_item_id)
                        if jr:
                            verdict_val = jr.get("type", jr.get("verdict", "?"))
                            is_miss = verdict_val in ("no_conflict", "none", "") or not verdict_val
                            lines.append(f"- **M_old item WAS in candidates** → judgment: **{verdict_val}**{'  ← ABDUCTIVE MISS' if is_miss else '  ← correctly flagged'}")
                            lines.append(f"  - inference_chain: {jr.get('inference_chain','')[:250]}")
                            lines.append(f"  - confidence: {jr.get('confidence','N/A')}")
                        else:
                            lines.append(f"- **M_old item WAS in candidates** but no judgment entry found in judgments_raw")
                    else:
                        lines.append(f"- **M_old item NOT in candidate_ids** → retrieval miss for this statement")
                lines.append("")

        # Check if M_old item appeared in candidates in ANY session
        if mold_item_id:
            all_hits = find_mold_in_candidates(session_logs, mold_item_id)
            if all_hits:
                # Show the M_new-session hits vs other hits
                rsi_set = set(rsi) if isinstance(rsi, list) else {rsi}
                mnew_sess_hits = [(si, s) for si, s in all_hits if si in rsi_set]
                other_hits = [(si, s) for si, s in all_hits if si not in rsi_set]

                if mnew_sess_hits:
                    lines.append(f"**In the M_new-relevant session(s), M_old item appeared in candidates {len(mnew_sess_hits)} time(s).**")
                    for si, s in mnew_sess_hits[:3]:
                        jr = find_judgment_for_item(s, mold_item_id)
                        if jr:
                            verdict_val = jr.get("type", jr.get("verdict", "?"))
                            lines.append(f"  - Session {si}: _{s.get('statement','')[:60]}_ → verdict={verdict_val}, chain: {jr.get('inference_chain','')[:150]}")
                else:
                    lines.append(f"**M_old item did NOT appear in candidates in the M_new-relevant session(s) — only in {len(other_hits)} unrelated session(s).**")
                    for si, s in other_hits[:2]:
                        jr = find_judgment_for_item(s, mold_item_id)
                        v = jr.get("type", jr.get("verdict","?")) if jr else "?"
                        lines.append(f"  - Session {si}: _{s.get('statement','')[:60]}_ → verdict={v}")
                lines.append("")
            else:
                lines.append(f"**M_old item NEVER appeared in candidate_ids across all sessions** → embedding/retrieval gap")
                lines.append("")

    # ── Query phase analysis ─────────────────────────────────────────────────
    lines.append("### Query Phase Analysis")
    lines.append("")

    for dim in ("dim1", "dim2", "dim3"):
        dlog = query_logs.get(f"{dim}_query") or {}
        answer = dlog.get("answer", "")
        verdict = dlog.get("verdict") or {}
        premise_result = dlog.get("premise_result") or {}

        score = get_807_score(uid8, dim)
        passed = score.get("pass", None)
        reasoning = score.get("reasoning", "")[:150]

        qonly_score = get_qonly_score(uid8, dim)
        qonly_pass = qonly_score.get("pass", None)
        qonly_answer = get_qonly_answer(uid8, dim)

        status_mark = "FAIL" if dim.replace("dim", "") in fail_set else "pass"

        lines.append(f"**{dim.upper()} [{status_mark}]**")

        premise_safe = premise_result.get("premise_safe")
        if premise_safe is None:
            premise_safe = verdict.get("premise_safe")
        lines.append(f"- premise_safe: `{premise_safe}`")

        presups = premise_result.get("presuppositions", [])
        if presups:
            for p in presups[:3]:
                lines.append(f"  - presup: {str(p)[:120]}")

        correction = premise_result.get("correction") or verdict.get("correction") or ""
        correction_str = str(correction)
        if correction_str and correction_str not in ("None", ""):
            lines.append(f"- correction: {correction_str[:250]}{'...' if len(correction_str) > 250 else ''}")
        else:
            lines.append("- correction: (none)")

        outdated = premise_result.get("outdated_facts", [])
        if outdated:
            lines.append(f"- outdated_facts: {outdated[:3]}")

        lines.append(f"- **answer:** _{str(answer)[:220]}_")
        lines.append(f"- scorer 807: pass=**{passed}**, reasoning: {reasoning}")
        lines.append(f"- qonly_v2: pass={qonly_pass}, answer: _{str(qonly_answer)[:150]}_")
        lines.append("")

    return "\n".join(lines) + "\n"


# ── Root cause classification ─────────────────────────────────────────────────
def assign_rc(uid8, category, ttag, tr):
    result = tr.get("result", {})
    meta = result.get("sample_meta", {})
    m_old = meta.get("M_old", "")
    m_new = meta.get("M_new", "")
    snapshot = result.get("final_profile_snapshot", {})
    session_logs = result.get("session_logs", [])
    query_logs = result.get("query_logs", {})
    rsi = meta.get("relevant_session_index", -1)

    if category == "answer_gen_miss":
        # Check if correction was very vague/absent
        for dim in ("dim1", "dim2", "dim3"):
            dq = query_logs.get(f"{dim}_query") or {}
            pr = dq.get("premise_result") or {}
            correction = str(pr.get("correction") or "")
            if correction and correction not in ("None", "") and len(correction.strip()) > 20:
                # correction present but answer_gen ignored it
                return "RC-J", "answer_gen ignored correction — premise_check issued UNSAFE with correction but answer_gen used stale active memories"
        return "RC-K", "correction too vague/absent — premise_check flagged UNSAFE but correction text was too sparse to guide answer_gen"

    # premise_check_miss path
    if ttag == "T2":
        # Check if M_old was never stored
        mold_item, score, method = find_matching_item(snapshot, m_old)
        if score < 0.20:
            return "RC-F", "T2 M_old not stored in memory — only appeared in query question context; premise_check had no matching memory to compare against"
        # M_old was stored but premise_check missed it
        return "RC-H", "T2 semantic chain — premise_check did not connect M_new (in question) to M_old (in memory) via 2-hop implication"

    # T1 premise_check_miss
    mold_item, score, method = find_matching_item(snapshot, m_old)
    if not mold_item or score < 0.15:
        return "RC-E", "M_old never extracted/stored — statement_extraction failed to store M_old as a distinct memory item"

    mold_item_id = mold_item["item_id"]
    rsi_set = set(rsi) if isinstance(rsi, list) else {rsi}

    # Check if M_old appeared in candidates in the relevant session
    all_hits = find_mold_in_candidates(session_logs, mold_item_id)
    mnew_hits = [(si, s) for si, s in all_hits if si in rsi_set]

    if mnew_hits:
        # M_old was retrieved — check if judgment was no_conflict
        for si, s in mnew_hits:
            jr = find_judgment_for_item(s, mold_item_id)
            if jr:
                verdict_val = jr.get("type", jr.get("verdict", ""))
                if verdict_val in ("no_conflict", "", None):
                    return "RC-B", f"abductive miss with candidate — M_old was retrieved in session {si} but LLM judged no_conflict (inference_chain: {jr.get('inference_chain','')[:80]})"
        return "RC-B", "abductive miss — M_old was in candidates but judgment failed"
    else:
        return "RC-I", "embedding gap — M_old never surfaced in candidate_ids for M_new-related statements; impact hypotheses did not semantically overlap with M_old content"


# ── Failing cases ─────────────────────────────────────────────────────────────
PREMISE_CHECK_MISS = [
    # T1
    ("6ff5a576",  "T1", ["dim2"], "premise_check_miss"),
    ("34d402c0",  "T1", ["dim1","dim2"], "premise_check_miss"),
    ("7ee76c41",  "T1", ["dim3"], "premise_check_miss"),
    ("eacb64ff",  "T1", ["dim3"], "premise_check_miss"),
    ("e229c5cd",  "T1", ["dim1","dim2"], "premise_check_miss"),
    ("a6170008",  "T1", ["dim1","dim2"], "premise_check_miss"),
    ("dae22057",  "T1", ["dim3"], "premise_check_miss"),
    ("3305ce57",  "T1", ["dim3"], "premise_check_miss"),
    ("f6d12075",  "T1", ["dim3"], "premise_check_miss"),
    # T2
    ("14897e47",  "T2", ["dim1","dim2","dim3"], "premise_check_miss"),
    ("f50107f1",  "T2", ["dim1","dim2","dim3"], "premise_check_miss"),
    ("d806d94c",  "T2", ["dim1","dim2","dim3"], "premise_check_miss"),
    ("4ad50bc6",  "T2", ["dim1"], "premise_check_miss"),
    ("2c711459",  "T2", ["dim2"], "premise_check_miss"),
    ("855155ad",  "T2", ["dim2"], "premise_check_miss"),
    ("1469bde3",  "T2", ["dim3"], "premise_check_miss"),
    ("c03f7b53",  "T2", ["dim1","dim2"], "premise_check_miss"),
]

ANSWER_GEN_MISS = [
    # T1
    ("e72a2ba5",  "T1", ["dim2","dim3"], "answer_gen_miss"),
    ("eee1a643",  "T1", ["dim3"], "answer_gen_miss"),
    ("d74f7f3e",  "T1", ["dim3"], "answer_gen_miss"),
    ("dae22057",  "T1", ["dim2"], "answer_gen_miss"),
    ("26e99c95",  "T1", ["dim3"], "answer_gen_miss"),
    # T2
    ("06071a3e",  "T2", ["dim3"], "answer_gen_miss"),
    ("a2a3e641",  "T2", ["dim1","dim2","dim3"], "answer_gen_miss"),
    ("c2cc2d39",  "T2", ["dim1","dim2","dim3"], "answer_gen_miss"),
    ("5ae24023",  "T2", ["dim1","dim3"], "answer_gen_miss"),
    ("855155ad",  "T2", ["dim1"], "answer_gen_miss"),
    ("48707e03",  "T2", ["dim3"], "answer_gen_miss"),
    ("c9cc370e",  "T2", ["dim3"], "answer_gen_miss"),
    ("60604200",  "T2", ["dim1","dim2"], "answer_gen_miss"),
]


# ── Build document ────────────────────────────────────────────────────────────
sections = []

sections.append("""\
# 807dee0 Failure Analysis — 47 Cases

Generated from `807dee0/full_60` (60 samples, T1×30 + T2×30).
Total failures: 47 dim-cases across 28 unique samples.

---

## Classification Summary

| Category | Dim-cases | Unique samples |
|---|---|---|
| premise_check_miss (write-phase) | 27 | 17 |
| answer_gen_miss (query-phase) | 20 | 13 |
| **Total** | **47** | **28** (2 overlap) |

**Overlap:** `dae22057` fails dim3 as premise_check_miss and dim2 as answer_gen_miss.
`855155ad` fails dim2 as premise_check_miss and dim1 as answer_gen_miss.

---

## Root Cause Legend

| Code | Description |
|---|---|
| RC-B | Abductive miss with candidate — M_old item was retrieved into candidate_ids but LLM judged `no_conflict` |
| RC-E | Extraction miss — M_old was never extracted/stored as a memory item |
| RC-F | T2 M_old not stored — M_old only appeared in query context, never in session statements |
| RC-H | T2 semantic chain — premise_check failed to connect M_new (in question context) to M_old (in memory) via 2-hop implication |
| RC-I | Embedding gap — M_old item never surfaced in candidate_ids for M_new-related statements |
| RC-J | Answer_gen ignored correction — premise_check correctly flagged UNSAFE+correction but answer_gen used stale active memories |
| RC-K | Correction absent/vague — premise_check flagged UNSAFE but correction text too sparse to guide answer_gen |

---

## PART A: premise_check_miss (write-phase failures)

In these 17 samples, the write-phase abductive_judgment missed the M_new→M_old conflict. M_old memory item stayed ACTIVE. At query time, premise_check found no stale/uncertain signal and issued a "SAFE" verdict, causing the answer to be built on stale M_old state.

**For T1 samples:** M_new appears as a session statement. The write phase had an opportunity to detect the conflict but failed — either the impact hypotheses did not retrieve M_old into candidate_ids (embedding/retrieval gap), or M_old was retrieved but judged `no_conflict`.

**For T2 samples:** M_new is only presented as context in the query question — never as a session statement. The write phase has no opportunity to see M_new at all. Conflict detection must happen at query time via premise_check connecting the question's M_new scenario to stored M_old. When this premise_check connection fails, the result is a false-safe verdict.

""")

for uid8, ttag, failing_dims, category in PREMISE_CHECK_MISS:
    tr = load_trace(uid8)
    if tr:
        rc_code, rc_desc = assign_rc(uid8, category, ttag, tr)
    else:
        rc_code, rc_desc = "RC-?", "trace not found"
    section = analyze_sample(uid8, ttag, failing_dims, category)
    section += f"**Root cause: {rc_code} — {rc_desc}**\n\n"
    section += "---\n\n"
    sections.append(section)

sections.append("""\
---

## PART B: answer_gen_miss (query-phase failures)

In these 13 samples, the write phase DID correctly flag M_old as stale or uncertain (or premise_check at query time flagged UNSAFE with a correction). However, the final answer_generation step failed to build the response on the corrected state.

**Common failure modes:**
1. **RC-J**: answer_gen received the UNSAFE verdict and correction text, but produced an answer drawing from active memories rather than the correction — especially when active memories strongly assert M_old
2. **RC-K**: premise_check issued UNSAFE but the correction text was too vague (e.g., "something may have changed") — answer_gen could not derive the correct current state from it

""")

for uid8, ttag, failing_dims, category in ANSWER_GEN_MISS:
    tr = load_trace(uid8)
    if tr:
        rc_code, rc_desc = assign_rc(uid8, category, ttag, tr)
    else:
        rc_code, rc_desc = "RC-?", "trace not found"
    section = analyze_sample(uid8, ttag, failing_dims, category)
    section += f"**Root cause: {rc_code} — {rc_desc}**\n\n"
    section += "---\n\n"
    sections.append(section)

sections.append("""\
---

## Aggregate Statistics

### Dim breakdown
| Dim | premise_check_miss cases | answer_gen_miss cases | Total failing |
|---|---|---|---|
| dim1 | 8 | 5 | 13 |
| dim2 | 10 | 5 | 15 |
| dim3 | 9 | 10 | 19 |

dim3 has the highest failure count (19), particularly in answer_gen_miss, likely because dim3 questions require action-level reasoning (compliance, what to do) — a correct course of action depends critically on knowing the updated state, so even partial errors are penalized.

### Root cause distribution (estimated)

| Root cause | Count | Notes |
|---|---|---|
| RC-H (T2 semantic chain) | ~8 | All T2 premise_check_miss; premise_check didn't link M_new question context to M_old memory |
| RC-J (answer_gen ignored correction) | ~9 | Most T1+T2 answer_gen_miss; correction present but answer used stale facts |
| RC-I (embedding gap) | ~5 | T1 premise_check_miss; M_new hypotheses didn't semantically reach M_old |
| RC-B (abductive miss with candidate) | ~3 | T1 premise_check_miss; M_old retrieved but judged no_conflict |
| RC-K (correction too vague) | ~4 | answer_gen_miss; correction text present but sparse |

### T1 vs T2 failure patterns

**T1 (direct conflict):**
- premise_check_miss: impact hypotheses generated from M_new session statements are too concrete (e.g., "changed commute pattern") and don't match the abstract M_old content (e.g., "main base at home") → embedding gap
- answer_gen_miss: correction is specific but answer_gen template over-weights active memory pool

**T2 (indirect/chained conflict):**
- premise_check_miss: premise_check only has access to stored active memories when evaluating the question's presuppositions — it does not have M_new as an input. It cannot perform the 2-hop inference (M_new event → implicit change → M_old now stale) without explicitly reasoning about the question scenario against M_old
- answer_gen_miss: when correction is issued, it often contains the right fact but answer_gen answers with hedging language ("I'm not sure, but...") or reverts to pre-correction active facts

### Key fix priorities

1. **RC-H (8 cases):** premise_check needs to explicitly compare question presuppositions against stored memories using the full M_new scenario context. Adding M_new to the premise_check context at query time would allow it to flag T2 conflicts.

2. **RC-J (9 cases):** answer_gen should treat an UNSAFE verdict + correction as a hard override, not a suggestion. The current prompt apparently allows answer_gen to default to active memory even with a correction present.

3. **RC-I (5 cases):** impact hypothesis generation needs broader/more abstract hypotheses that can reach categorical memories (biographical, current_state with abstract phrasing). Could be improved by generating hypotheses at multiple levels of abstraction.
""")

doc = "\n".join(sections)

os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
with open(OUTPUT, "w") as f:
    f.write(doc)

print(f"Written: {OUTPUT}")
print(f"Total chars: {len(doc)}, lines: {doc.count(chr(10))}")
