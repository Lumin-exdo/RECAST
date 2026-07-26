#!/usr/bin/env python3
"""
Precise per-dim failure-stage attribution for the 3af9122/replay400 run.

For every (sample, dim) that the judge scored as failing, walk the pipeline
stage by stage using artifacts already on disk — no new LLM calls:
  1. extraction      — was M_old ever stored as a memory item?
  2. extraction       — was the M_new statement (around relevant_session_index) extracted?
  3. candidate-gen    — did the M_old item ever appear as a judged candidate
                         when the M_new statement was processed?
  4. abductive_judgment — what type/confidence did it get?
  5. dispatch/pool    — did the item actually end up stale/uncertain by query time?
  6. query-retrieval  — was the item actually shown to the LLM at query time?
  7. answer-generation — item was shown correctly tagged, judge still failed —
                         this is a genuine model-reasoning failure, not a pipeline bug.

Outputs:
  analysis_output/precise_failure_attribution.json  (machine-readable, one record per failing dim)
  analysis_output/precise_failure_attribution.md     (grouped by stage, with examples)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from RECAST.scripts.replay_from_trace import find_trace  # noqa: E402
from RECAST.retrieval.embedding import build_retriever  # noqa: E402

RUNS_ROOT = _REPO / "RECAST" / "runs"
RUN_DIR = RUNS_ROOT / "3af9122" / "replay400"
DATASET_PATH = _REPO / "RECAST" / "STALE" / "STALE" / "outputs" / "STALE_MAIN.json"
EMBED_PATH = _REPO / "RECAST" / "models" / "all-MiniLM-L6-v2"
OUT_JSON = _REPO / "RECAST" / "analysis_output" / "precise_failure_attribution.json"
OUT_MD = _REPO / "RECAST" / "analysis_output" / "precise_failure_attribution.md"
MATCH_THRESHOLD = 0.45  # cosine similarity; calibrated by spot-checking known-good extractions

_RETRIEVER = None


def get_retriever():
    global _RETRIEVER
    if _RETRIEVER is None:
        _RETRIEVER = build_retriever(str(EMBED_PATH), device="cpu")
    return _RETRIEVER

DIM_LABELS = {"dim1": "dim1_query", "dim2": "dim2_query", "dim3": "dim3_query"}


def load_dataset() -> Dict[str, Dict[str, Any]]:
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    return {str(r["uid"]): (i, r) for i, r in enumerate(data)}


def load_scores(path: Path) -> Dict[str, Dict[str, Any]]:
    d = json.loads(path.read_text(encoding="utf-8"))
    return {rec["uid"]: rec for rec in d["details"]}


def flatten_statements(trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    """All statement_log entries across all sessions, in session order."""
    out = []
    for slog in trace["result"]["session_logs"]:
        s_idx = slog.get("session_index")
        for stmt in slog.get("statement_log", []):
            entry = dict(stmt)
            entry["_session_index"] = s_idx
            out.append(entry)
    return out


def best_fuzzy_match(target_text: str, statements: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], float]:
    """Semantic match via the same embedding model the pipeline itself uses for
    retrieval — character-level fuzzy matching was tried first and rejected:
    extracted statements are short paraphrases of the source text, not
    substrings, so length-sensitive string similarity badly undercounts
    correct extractions (e.g. M_new is one full sentence; the stored item is
    a 3-word clause expressing the same fact)."""
    if not statements:
        return None, 0.0
    retriever = get_retriever()
    ranked = retriever.rank(
        query_text=target_text,
        candidates=statements,
        text_getter=lambda s: s.get("statement", ""),
        top_k=1,
    )
    if not ranked:
        return None, 0.0
    return ranked[0]["item"], ranked[0]["score"]


def find_judgments_for_item(statements: List[Dict[str, Any]], item_id: str) -> List[Dict[str, Any]]:
    """All judgments across all statements where target_item_id == item_id, with provenance."""
    out = []
    for stmt in statements:
        for j in stmt.get("judgments_raw", []):
            if j.get("target_item_id") == item_id:
                rec = dict(j)
                rec["_trigger_statement"] = stmt.get("statement")
                rec["_trigger_session_index"] = stmt.get("_session_index")
                out.append(rec)
    return out


def load_cache_texts(sample_dir: Path) -> List[Tuple[str, str]]:
    """Return list of (system_prompt, response_text) for every cached call in this sample."""
    out = []
    cache_dir = sample_dir / ".cache"
    if not cache_dir.exists():
        return out
    for f in cache_dir.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        msgs = d.get("messages", [])
        sys_prompt = next((m["content"] for m in msgs if m.get("role") == "system"), "")
        out.append((sys_prompt, d.get("text", "")))
    return out


def find_query_prompt(cache_texts: List[Tuple[str, str]], query_text: str) -> Optional[str]:
    """Find the answer_query_v2 system prompt for this specific probing query."""
    needle = query_text.strip()[:80]  # first 80 chars is enough to disambiguate
    for sys_prompt, _ in cache_texts:
        if "Stored memories (tagged with reliability)" in sys_prompt and needle in sys_prompt:
            return sys_prompt
    return None


def item_status_in_prompt(prompt: str, item_id: str) -> Optional[str]:
    """Look for '[ACTIVE] m_XXXXX:' / '[STALE] m_XXXXX:' / '[UNCERTAIN] m_XXXXX:' in the memories list."""
    m = re.search(rf"\[(ACTIVE|STALE|UNCERTAIN)\]\s*{re.escape(item_id)}\b", prompt)
    return m.group(1) if m else None


def classify(
    *,
    m_old_match: Optional[Dict[str, Any]],
    m_old_score: float,
    m_new_match: Optional[Dict[str, Any]],
    m_new_score: float,
    judgments_for_item: List[Dict[str, Any]],
    relevant_session_indices: List[int],
    query_prompt: Optional[str],
    item_id: Optional[str],
) -> Tuple[str, str]:
    """Returns (stage, detail)."""
    if m_old_score < MATCH_THRESHOLD or m_old_match is None:
        return "RC-NOSTALE-extraction", f"M_old has no good extraction match (best embedding sim={m_old_score:.2f})"
    if not m_old_match.get("new_item_id"):
        return "RC-NOSTALE-not-stored", "M_old text was extracted but never stored as a memory item (is_definite=False or filtered)"
    if m_new_score < MATCH_THRESHOLD or m_new_match is None:
        return "RC-NOSTALE-Mnew-missing", f"M_new statement has no good extraction match near relevant sessions (best embedding sim={m_new_score:.2f})"

    # Did the M_new statement (or anything near its session) ever judge this item?
    relevant_judgments = [
        j for j in judgments_for_item
        if j.get("_trigger_session_index") in relevant_session_indices
    ]
    if not judgments_for_item:
        return "RC-RETRIEVAL-never-candidate", "M_old item never appeared as a judged candidate for ANY statement in the whole session — never retrieved into the candidate pool"
    if not relevant_judgments:
        sessions_seen = sorted({j.get("_trigger_session_index") for j in judgments_for_item})
        return "RC-RETRIEVAL-missed-trigger", f"item was judged against OTHER statements (sessions {sessions_seen}) but never against the M_new statement itself"

    best_judgment = max(relevant_judgments, key=lambda j: j.get("confidence", 0.0))
    jtype = best_judgment.get("type")
    conf = best_judgment.get("confidence", 0.0)
    if jtype == "no_conflict" or conf < 0.35:
        return "RC-JUDGMENT-no-conflict", f"abductive_judgment classified the trigger statement as type={jtype} confidence={conf:.2f} — failed to detect the conflict"

    # Judgment correctly detected something — check final status at query time
    if query_prompt is None:
        return "RC-UNKNOWN-no-query-prompt", "could not locate the query-phase prompt for this dim to verify final status"
    status = item_status_in_prompt(query_prompt, item_id) if item_id else None
    if status is None:
        return "RC-QUERY-RETRIEVAL-miss", "item correctly judged weakened/invalidated but was NOT among the memories shown to the LLM at query time (retrieval miss)"
    if status == "ACTIVE":
        return "RC-POOL-not-marked", f"abductive_judgment detected conflict (type={jtype} conf={conf:.2f}) but item still shows [ACTIVE] at query time — pool/dispatch never marked it stale/uncertain"

    # status is STALE or UNCERTAIN, item was shown, judge still failed -> real reasoning gap
    return "RC-REASONING-final", f"item correctly tagged [{status}] and shown to the LLM at query time (judgment type={jtype} conf={conf:.2f}) — judge still failed; this is a genuine answer-generation reasoning failure"


def main():
    uid_to_record = load_dataset()
    scores_t1 = load_scores(RUN_DIR / "scores_T1.json")
    scores_t2 = load_scores(RUN_DIR / "scores_T2.json")
    all_scores = {**scores_t1, **scores_t2}

    results = []
    total_checked = 0
    for uid, score_rec in all_scores.items():
        if uid not in uid_to_record:
            continue
        abs_idx, dataset_rec = uid_to_record[uid]
        failing_dims = [
            d for d in ("dim1", "dim2", "dim3")
            if not score_rec["evaluation"].get(f"{d}_eval", {}).get("pass", True)
        ]
        if not failing_dims:
            continue

        trace_path = find_trace(abs_idx, RUNS_ROOT)
        if trace_path is None:
            for d in failing_dims:
                results.append({"uid": uid, "abs_idx": abs_idx, "dim": d, "stage": "RC-NO-TRACE", "detail": "no trace file found at all"})
            continue
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        statements = flatten_statements(trace)

        m_old = dataset_rec.get("M_old", "")
        m_new = dataset_rec.get("M_new", "")
        relevant_idx = dataset_rec.get("relevant_session_index", [])
        if isinstance(relevant_idx, int):
            relevant_idx = [relevant_idx]

        m_old_match, m_old_score = best_fuzzy_match(m_old, statements)
        # restrict M_new search to statements at/after the earliest relevant session if possible
        m_new_match, m_new_score = best_fuzzy_match(m_new, statements)

        item_id = m_old_match.get("new_item_id") if m_old_match else None
        judgments_for_item = find_judgments_for_item(statements, item_id) if item_id else []

        sample_dir = RUN_DIR / f"{abs_idx:04d}"
        cache_texts = load_cache_texts(sample_dir)

        probing_queries = dataset_rec.get("probing_queries", {})

        total_checked += 1
        for d in failing_dims:
            qlabel = DIM_LABELS[d]
            qtext = str(probing_queries.get(qlabel, ""))
            query_prompt = find_query_prompt(cache_texts, qtext) if qtext else None
            stage, detail = classify(
                m_old_match=m_old_match,
                m_old_score=m_old_score,
                m_new_match=m_new_match,
                m_new_score=m_new_score,
                judgments_for_item=judgments_for_item,
                relevant_session_indices=relevant_idx,
                query_prompt=query_prompt,
                item_id=item_id,
            )
            results.append({
                "uid": uid,
                "abs_idx": abs_idx,
                "type": dataset_rec.get("type"),
                "dim": d,
                "stage": stage,
                "detail": detail,
                "M_old": m_old,
                "M_new": m_new,
                "item_id": item_id,
                "m_old_match_score": round(m_old_score, 3),
                "m_new_match_score": round(m_new_score, 3),
                "judge_reasoning": score_rec["evaluation"][f"{d}_eval"]["reasoning"],
            })

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Build markdown report grouped by stage ──────────────────────────────
    from collections import defaultdict
    by_stage: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in results:
        by_stage[r["stage"]].append(r)

    lines = []
    lines.append("# Precise Failure-Stage Attribution — 3af9122/replay400\n")
    lines.append(f"Total failing (sample, dim) pairs: **{len(results)}** across {total_checked} samples with >=1 failing dim.\n")
    lines.append("Methodology: for each failing dim, fuzzy-match M_old/M_new against the trace's "
                  "extracted statements, trace every abductive_judgment call targeting that item, "
                  "check final [STALE]/[UNCERTAIN]/[ACTIVE] status shown to the LLM at query time, "
                  "and only attribute to 'reasoning failure' if the item was correctly tagged and "
                  "retrieved but the judge still failed.\n")
    lines.append("## Stage distribution\n")
    lines.append("| Stage | Count | % |")
    lines.append("|---|---|---|")
    for stage in sorted(by_stage, key=lambda s: -len(by_stage[s])):
        n = len(by_stage[stage])
        lines.append(f"| {stage} | {n} | {n/len(results)*100:.1f}% |")
    lines.append("")

    for stage in sorted(by_stage, key=lambda s: -len(by_stage[s])):
        items = by_stage[stage]
        lines.append(f"## {stage} ({len(items)})\n")
        t1n = sum(1 for i in items if i["type"] == "T1")
        t2n = sum(1 for i in items if i["type"] == "T2")
        lines.append(f"T1={t1n} T2={t2n}\n")
        for ex in items[:8]:
            lines.append(f"#### {ex['uid'][:8]} · {ex['type']} · abs_idx={ex['abs_idx']} · {ex['dim']}\n")
            lines.append(f"- M_old: {ex['M_old']}")
            lines.append(f"- M_new: {ex['M_new']}")
            lines.append(f"- item_id: {ex['item_id']} (match scores: old={ex['m_old_match_score']}, new={ex['m_new_match_score']})")
            lines.append(f"- detail: {ex['detail']}")
            lines.append(f"- judge said: {ex['judge_reasoning'][:200]}")
            lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"Checked {total_checked} samples, {len(results)} failing dims")
    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_MD}")


if __name__ == "__main__":
    main()
