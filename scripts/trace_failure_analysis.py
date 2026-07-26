"""
Trace-level failure analysis for strict-rubric regressions.

For each run, finds samples that pass the original rubric but fail the strict
rubric (new failures under stricter reasoning requirement).

For runs that have per-sample trace.json, classifies the root cause:

  Write-phase failures (memory encoding):
    W1-MISS    M_new session extracted no statements / created no memory items
    W2-UNHYP   Statements extracted but hypotheses generated are empty
    W3-NOJUDGE Hypotheses exist but abductive judgment produced no
               stale/uncertain invalidation across any earlier session
    W4-LATE    Invalidation fired only after the relevant conflict session
               (timing error — evidence arrived too late)

  Read-phase failures (retrieval / answer generation):
    R2-MISS    M_new-related memory items exist but none appear in retrieved_ids
    R1-POLLUTE retrieved_ids contain only pre-M_new memories; M_new items absent
               (retrieved old context crowds out M_new)
    R3-IGNORE  M_new items retrieved but dim_response text ignores them

  Other:
    NO-TRACE   Run has no per-sample trace.json (shallow analysis only)
    UNKNOWN    Trace exists but evidence is ambiguous

Usage:
  cd /home/lumin_exdo
  python -m RECAST.scripts.trace_failure_analysis
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

# ── Paths ────────────────────────────────────────────────────────────────────
RECAST_ROOT = Path(__file__).resolve().parents[1]
STALE_PATH  = RECAST_ROOT / "STALE/STALE/outputs/STALE_MAIN.json"
RUNS        = RECAST_ROOT / "runs"
STRICT_DIR  = RUNS / "rescore_strict"
CUP_PATH    = Path("/mnt/laq/cup_mem/eval_answers3")

# ── Run registry ─────────────────────────────────────────────────────────────
# (name, orig_T1, orig_T2, strict_T1, strict_T2, trace_dir_or_None)
# trace_dir: directory where per-sample <idx>/trace.json live
# For runs where T1/T2 answers were split across subdirs, pass None and handle
# separately in RUN_REGISTRY.
RUN_REGISTRY = [
    dict(
        name        = "CupMem",
        orig_t1     = CUP_PATH / "scores_T1_rescore.json",
        orig_t2     = CUP_PATH / "scores_T2_rescore.json",
        strict_t1   = STRICT_DIR / "cupmem_scores_T1_strict.json",
        strict_t2   = STRICT_DIR / "cupmem_scores_T2_strict.json",
        answers_t1  = CUP_PATH / "cupmem_full_T1.json",
        answers_t2  = CUP_PATH / "cupmem_full_T2.json",
        trace_dir   = None,
    ),
    dict(
        name        = "d215489",
        orig_t1     = RUNS / "d215489/e2ev3_400/scores_T1_newjudge.json",
        orig_t2     = RUNS / "d215489/e2ev3_400/scores_T2_newjudge.json",
        strict_t1   = STRICT_DIR / "d215489_T1_strict.json",
        strict_t2   = STRICT_DIR / "d215489_T2_strict.json",
        answers_t1  = RUNS / "d215489/e2ev3_400/answers_T1.json",
        answers_t2  = RUNS / "d215489/e2ev3_400/answers_T2.json",
        trace_dir   = RUNS / "d215489/e2ev3_400",
    ),
    dict(
        name        = "dispatch_fix",
        orig_t1     = RUNS / "bugfix/t1_full/scores_T1.json",
        orig_t2     = RUNS / "bugfix/t2_full/scores_T2.json",
        strict_t1   = STRICT_DIR / "dispatch_fix_T1_strict.json",
        strict_t2   = STRICT_DIR / "dispatch_fix_T2_strict.json",
        answers_t1  = RUNS / "bugfix/t1_full/answers_T1.json",
        answers_t2  = RUNS / "bugfix/t2_full/answers_T2.json",
        trace_dir   = None,
    ),
    dict(
        name        = "G2_per_session",
        orig_t1     = RUNS / "9d4f025/replay400/scores_T1.json",
        orig_t2     = RUNS / "9d4f025/replay400/scores_T2.json",
        strict_t1   = STRICT_DIR / "scores_T1_strict.json",
        strict_t2   = STRICT_DIR / "scores_T2_strict.json",
        answers_t1  = RUNS / "9d4f025/replay400/answers_T1.json",
        answers_t2  = RUNS / "9d4f025/replay400/answers_T2.json",
        # Write-phase trace is in d215489; read-phase has no per-sample trace
        trace_dir   = RUNS / "d215489/e2ev3_400",
        write_only_trace = True,  # retrieved_ids not available for this run
    ),
    dict(
        name        = "ablation_e",
        orig_t1     = RUNS / "bugfix/e_full/scores_T1.json",
        orig_t2     = RUNS / "bugfix/e_full/scores_T2.json",
        strict_t1   = STRICT_DIR / "ablation_e_T1_strict.json",
        strict_t2   = STRICT_DIR / "ablation_e_T2_strict.json",
        answers_t1  = RUNS / "bugfix/e_full/answers_T1.json",
        answers_t2  = RUNS / "bugfix/e_full/answers_T2.json",
        trace_dir   = RUNS / "bugfix/e_full",
    ),
    dict(
        name        = "ablation_d",
        orig_t1     = RUNS / "bugfix/d_full/scores_T1.json",
        orig_t2     = RUNS / "bugfix/d_full/scores_T2.json",
        strict_t1   = STRICT_DIR / "ablation_d_T1_strict.json",
        strict_t2   = STRICT_DIR / "ablation_d_T2_strict.json",
        answers_t1  = RUNS / "bugfix/d_full/answers_T1.json",
        answers_t2  = RUNS / "bugfix/d_full/answers_T2.json",
        trace_dir   = RUNS / "bugfix/d_full",
    ),
    dict(
        name        = "a_poolreset",
        orig_t1     = RUNS / "a_poolreset/scores_T1.json",
        orig_t2     = RUNS / "a_poolreset/scores_T2.json",
        strict_t1   = STRICT_DIR / "a_poolreset_T1_strict.json",
        strict_t2   = STRICT_DIR / "a_poolreset_T2_strict.json",
        answers_t1  = RUNS / "a_poolreset/answers_T1.json",
        answers_t2  = RUNS / "a_poolreset/answers_T2.json",
        trace_dir   = None,
    ),
    dict(
        name        = "a_nopool",
        orig_t1     = RUNS / "a_nopool/scores_T1.json",
        orig_t2     = RUNS / "a_nopool/scores_T2.json",
        strict_t1   = STRICT_DIR / "a_nopool_T1_strict.json",
        strict_t2   = STRICT_DIR / "a_nopool_T2_strict.json",
        answers_t1  = RUNS / "a_nopool/answers_T1.json",
        answers_t2  = RUNS / "a_nopool/answers_T2.json",
        trace_dir   = None,
    ),
    dict(
        name        = "a_noimp",
        orig_t1     = RUNS / "a_noimp/scores_T1.json",
        orig_t2     = RUNS / "a_noimp/scores_T2.json",
        strict_t1   = STRICT_DIR / "a_noimp_T1_strict.json",
        strict_t2   = STRICT_DIR / "a_noimp_T2_strict.json",
        answers_t1  = RUNS / "a_noimp/answers_T1.json",
        answers_t2  = RUNS / "a_noimp/answers_T2.json",
        trace_dir   = None,
    ),
    dict(
        name        = "ablation_c",
        orig_t1     = RUNS / "bugfix/c_full/scores_T1.json",
        orig_t2     = RUNS / "bugfix/c_full/scores_T2.json",
        strict_t1   = STRICT_DIR / "ablation_c_T1_strict.json",
        strict_t2   = STRICT_DIR / "ablation_c_T2_strict.json",
        answers_t1  = RUNS / "bugfix/c_full/answers_T1.json",
        answers_t2  = RUNS / "bugfix/c_full/answers_T2.json",
        trace_dir   = RUNS / "bugfix/c_full",
    ),
    dict(
        name        = "a_naive",
        orig_t1     = RUNS / "bugfix/a_naive_answer/scores_T1.json",
        orig_t2     = RUNS / "bugfix/a_naive_answer/scores_T2.json",
        strict_t1   = STRICT_DIR / "a_naive_T1_strict.json",
        strict_t2   = STRICT_DIR / "a_naive_T2_strict.json",
        answers_t1  = RUNS / "bugfix/a_naive_answer/answers_T1.json",
        answers_t2  = RUNS / "bugfix/a_naive_answer/answers_T2.json",
        trace_dir   = None,
    ),
    dict(
        name        = "ablation_f",
        orig_t1     = RUNS / "bugfix/ablation_f/eval_T1.json",
        orig_t2     = RUNS / "bugfix/ablation_f/eval_T2.json",
        strict_t1   = STRICT_DIR / "ablation_f_T1_strict.json",
        strict_t2   = STRICT_DIR / "ablation_f_T2_strict.json",
        answers_t1  = RUNS / "bugfix/ablation_f/answers_T1.json",
        answers_t2  = RUNS / "bugfix/ablation_f/answers_T2.json",
        trace_dir   = None,
    ),
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def load_scores(path: Path, ct: str) -> dict[str, dict]:
    """Return {uid: {dim1: bool, dim2: bool, dim3: bool}}"""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())

    # Format A: {"details": [{"uid":..., "evaluation": {"dim1_eval": {"pass":...}}}]}
    if "details" in raw:
        out: dict[str, dict] = {}
        for entry in raw["details"]:
            uid  = entry.get("uid", "")
            evl  = entry.get("evaluation", {})
            out[uid] = {
                "dim1": bool(evl.get("dim1_eval", {}).get("pass", False)),
                "dim2": bool(evl.get("dim2_eval", {}).get("pass", False)),
                "dim3": bool(evl.get("dim3_eval", {}).get("pass", False)),
            }
        return out

    # Format B (old scorer): {"summary": {...}, "accuracy": {ct: {dim1: {...}}}}
    acc = raw.get("summary", raw).get("accuracy", {}).get(ct, {})
    # Format B gives aggregates only, not per-uid — cannot use for per-sample
    # Fall back to empty
    return {}


def load_answers(path: Path) -> dict[str, dict]:
    """Return {uid: answer_entry}"""
    if not path.exists():
        return {}
    entries = json.loads(path.read_text())
    return {e["uid"]: e for e in entries}


def load_trace(trace_dir: Path, uid_to_idx: dict[str, int], uid: str) -> Optional[dict]:
    idx = uid_to_idx.get(uid)
    if idx is None:
        return None
    tf = trace_dir / f"{idx:04d}" / "trace.json"
    if not tf.exists():
        return None
    return json.loads(tf.read_text())


def classify(
    uid: str,
    dim: int,
    stale_sample: dict,
    trace: Optional[dict],
    write_only: bool = False,
    answer_text: str = "",
) -> tuple[str, str]:
    """
    Returns (failure_type, evidence_summary).
    dim: 1, 2, or 3.
    """
    if trace is None:
        return "NO-TRACE", "no trace.json available"

    result = trace.get("result", {})
    session_logs = result.get("session_logs", [])
    relevant_idxs: list[int] = stale_sample.get("relevant_session_index", [])

    if not relevant_idxs:
        return "UNKNOWN", "no relevant_session_index in STALE sample"

    # M_new is always the last relevant session
    m_new_idx = relevant_idxs[-1]
    if m_new_idx >= len(session_logs):
        return "UNKNOWN", f"M_new session index {m_new_idx} out of range (len={len(session_logs)})"

    m_new_log = session_logs[m_new_idx]
    stmt_log   = m_new_log.get("statement_log", [])

    # ── W1: no statements extracted / no memory items created ────────────────
    created_ids = [s["new_item_id"] for s in stmt_log if s.get("new_item_id")]
    if not stmt_log:
        return "W1-MISS", f"M_new session (idx={m_new_idx}) produced zero statements"
    if not created_ids:
        return "W1-MISS", (
            f"M_new session extracted {len(stmt_log)} statements but all "
            f"new_item_id=None (no memory written)"
        )

    # ── W2: hypotheses empty ─────────────────────────────────────────────────
    all_hyps = [h for s in stmt_log for h in s.get("hypotheses", [])]
    if not all_hyps:
        return "W2-UNHYP", (
            f"M_new session created {len(created_ids)} items but generated "
            f"0 hypotheses — conflict signature never encoded"
        )

    # ── W3: no invalidation fired in any session up to (and including) M_new ─
    invalidation_actions = {"mark_stale", "mark_uncertain"}
    inval_found = False
    for sess in session_logs[: m_new_idx + 1]:
        for jl in sess.get("judgment_logs", []):
            if jl.get("action") in invalidation_actions:
                inval_found = True
                break
        if inval_found:
            break

    if not inval_found:
        # Check if judgments show high-confidence conflicts that were dropped
        raw_types = [
            jr.get("type", "")
            for s in stmt_log
            for jr in s.get("judgments_raw", [])
            if jr.get("type")
        ]
        conflict_raw = [t for t in raw_types if t in ("weakens_support", "contradicts")]
        evidence = (
            f"hypotheses generated ({len(all_hyps)}) but no stale/uncertain action "
            f"fired. judgments_raw conflict types in M_new session: {conflict_raw or 'none'}"
        )
        return "W3-NOJUDGE", evidence

    # ── Read-phase analysis ──────────────────────────────────────────────────
    if write_only:
        # No per-sample read trace for this run; report write-phase as OK
        return "R3-IGNORE", (
            f"write-phase OK (items={created_ids[:3]}…); "
            f"no read-phase trace available — answer may ignore M_new"
        )

    dim_key = f"dim{dim}_query"
    q_log = result.get("query_logs", {}).get(dim_key, {})
    retrieved = set(q_log.get("retrieved_ids", []))

    # Collect all M_new-session item ids (items created in relevant sessions)
    m_new_ids: set[str] = set()
    for idx in relevant_idxs:
        if idx < len(session_logs):
            for s in session_logs[idx].get("statement_log", []):
                if s.get("new_item_id"):
                    m_new_ids.add(s["new_item_id"])

    relevant_retrieved = retrieved & m_new_ids

    # ── R2 / R1 ──────────────────────────────────────────────────────────────
    if not relevant_retrieved and m_new_ids:
        # Check whether the retrieved set contains only pre-M_new items
        pre_ids = set()
        for sess in session_logs[: m_new_idx]:
            for s in sess.get("statement_log", []):
                if s.get("new_item_id"):
                    pre_ids.add(s["new_item_id"])
        polluted = bool(retrieved & pre_ids)
        tag = "R1-POLLUTE" if polluted else "R2-MISS"
        return tag, (
            f"M_new items {list(m_new_ids)[:3]} not in retrieved_ids "
            f"(retrieved {len(retrieved)} items; pre-M_new overlap={polluted})"
        )

    # ── R3: retrieved OK but answer ignores ──────────────────────────────────
    return "R3-IGNORE", (
        f"M_new items retrieved ({list(relevant_retrieved)[:3]}) "
        f"but dim{dim} response does not ground reasoning in M_new. "
        f"answer[:200]: {answer_text[:200]}"
    )


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    stale_list = json.loads(STALE_PATH.read_text())
    stale_by_uid: dict[str, dict] = {s["uid"]: s for s in stale_list}

    # Build uid→abs_idx mapping (order in STALE_MAIN matches run output order)
    uid_to_abs: dict[str, int] = {s["uid"]: i for i, s in enumerate(stale_list)}

    # Build uid→per-sample-dir-index mapping per run
    # (abs_idx filtered to T1 or T2 gives the 0..199 index used in 0000/ dirs)
    t1_uids = [s["uid"] for s in stale_list if s["type"] == "T1"]
    t2_uids = [s["uid"] for s in stale_list if s["type"] == "T2"]
    uid_to_t1_idx = {uid: i for i, uid in enumerate(t1_uids)}
    uid_to_t2_idx = {uid: i for i, uid in enumerate(t2_uids)}

    # Global summary
    global_type_counts: dict[str, int] = defaultdict(int)
    report_lines: list[str] = []

    for run in RUN_REGISTRY:
        name       = run["name"]
        trace_dir  = run.get("trace_dir")
        write_only = run.get("write_only_trace", False)

        # Load scores for both conflict types
        for ct, orig_path, strict_path, ans_path in [
            ("T1", run["orig_t1"],  run["strict_t1"],  run["answers_t1"]),
            ("T2", run["orig_t2"],  run["strict_t2"],  run["answers_t2"]),
        ]:
            orig   = load_scores(Path(str(orig_path)),   ct)
            strict = load_scores(Path(str(strict_path)), ct)
            if not orig or not strict:
                report_lines.append(f"\n[{name} {ct}] scores not available — skip")
                continue

            answers = load_answers(Path(str(ans_path)))

            # uid→sample-dir index depends on conflict type
            uid_to_dir_idx = uid_to_t1_idx if ct == "T1" else uid_to_t2_idx

            # Find new strict failures (orig pass → strict fail)
            new_failures: list[dict] = []
            for uid, o in orig.items():
                s = strict.get(uid)
                if not s:
                    continue
                for dim in [1, 2, 3]:
                    dk = f"dim{dim}"
                    if o[dk] and not s[dk]:
                        new_failures.append({"uid": uid, "dim": dim})

            total_orig_pass  = sum(1 for o in orig.values()   for dk in ["dim1","dim2","dim3"] if o[dk])
            total_strict_pass= sum(1 for s in strict.values() for dk in ["dim1","dim2","dim3"] if s[dk])
            report_lines.append(
                f"\n{'='*60}\n[{name} {ct}]  "
                f"orig-pass={total_orig_pass}  strict-pass={total_strict_pass}  "
                f"new-failures={len(new_failures)}"
            )

            type_counts: dict[str, int] = defaultdict(int)
            per_sample: list[str] = []

            for fail in new_failures:
                uid  = fail["uid"]
                dim  = fail["dim"]
                abs_idx = uid_to_abs.get(uid, -1)
                stale_s = stale_by_uid.get(uid, {})

                # Load trace
                trace = None
                if trace_dir:
                    trace = load_trace(Path(str(trace_dir)), uid_to_dir_idx, uid)

                # Get dim response text
                ans_entry   = answers.get(uid, {})
                responses   = ans_entry.get("target_model_responses", {})
                dim_resp    = responses.get(f"dim{dim}_response", "")

                ftype, evidence = classify(
                    uid, dim, stale_s, trace,
                    write_only=write_only,
                    answer_text=dim_resp,
                )

                type_counts[ftype] += 1
                global_type_counts[ftype] += 1

                per_sample.append(
                    f"  abs_idx={abs_idx:3d}  dim{dim}  {ftype:12s}  "
                    f"uid={uid[:8]}  {evidence[:100]}"
                )

            # Type distribution
            report_lines.append(f"  Type distribution: {dict(sorted(type_counts.items()))}")
            report_lines.extend(per_sample)

    # Overall summary
    report_lines.insert(0, "=== STRICT FAILURE TRACE ANALYSIS ===\n")
    report_lines.append(f"\n{'='*60}")
    report_lines.append("GLOBAL type distribution across all runs:")
    for ftype, cnt in sorted(global_type_counts.items(), key=lambda x: -x[1]):
        report_lines.append(f"  {ftype:12s}: {cnt}")

    out_path = STRICT_DIR / "trace_failure_analysis.txt"
    report = "\n".join(report_lines)
    out_path.write_text(report)
    print(report)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
