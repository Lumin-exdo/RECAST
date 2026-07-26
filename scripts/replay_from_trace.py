#!/usr/bin/env python3
"""
Replay write phase from existing traces, then run fresh query phase.

What is REUSED (no LLM calls):
  - statement_extraction: uses trace's statement_log texts
  - impact_hypothesis:    uses trace's hypotheses
  - abductive_judgment:   uses trace's judgments_raw (exact LLM outputs from old run)

What is RUN FRESH (new LLM calls):
  - pool_synthesis:       new per-session timing; different inputs vs old per-evidence calls
  - impression_update:    depends on which items got stale (different under new dispatch)
  - query phase (3 calls per sample): premise_check + answer_gen via answer_query_v2

This isolates exactly what changed: dispatch routing + pool timing.

Run from /mnt/laq:
  python -m RECAST.scripts.replay_from_trace --run-name replay400 --n-samples 0 --workers 10
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Path setup ────────────────────────────────────────────────────────────────
_REPO = Path(__file__).resolve().parent.parent.parent  # /mnt/laq
sys.path.insert(0, str(_REPO))

DEFAULT_RUNS_ROOT = _REPO / "RECAST" / "runs"
DEFAULT_DATA_PATH = _REPO / "RECAST" / "STALE" / "STALE" / "outputs" / "STALE_MAIN.json"
DEFAULT_EMBED_PATH = _REPO / "RECAST" / "models" / "all-MiniLM-L6-v2"

# Priority order when looking up trace files (most complete runs first)
TRACE_PRIORITY = [
    "d215489/e2ev3_400",
]


def _trace_completeness(trace_path: Path) -> int:
    """Proxy for trace quality: total statements across all sessions. Used to
    pick deterministically among multiple candidate traces instead of relying
    on directory-iteration order (which is incidental, not a quality signal)."""
    try:
        t = json.loads(trace_path.read_text(encoding="utf-8"))
        return sum(
            len(slog.get("statement_log", []))
            for slog in t.get("result", {}).get("session_logs", [])
        )
    except Exception:
        return -1


# ── Trace discovery ───────────────────────────────────────────────────────────

def find_trace(abs_idx: int, runs_root: Path) -> Optional[Path]:
    """Find the best trace.json for abs_idx, checking priority dirs first.

    Priority dirs are checked in order — first match wins, deterministic.
    If none of the priority dirs have it, every other run dir is scanned and
    the trace with the most total statements (most complete extraction) wins,
    instead of whichever happens to sort first in directory-iteration order.
    """
    idx_str = f"{abs_idx:04d}"
    for sub in TRACE_PRIORITY:
        t = runs_root / sub / idx_str / "trace.json"
        if t.exists():
            return t
    # Fallback: scan all commit dirs, pick the most complete trace deterministically
    candidates: List[Path] = []
    for commit_dir in runs_root.iterdir():
        if not commit_dir.is_dir() or not commit_dir.name[0].isalnum():
            continue
        for run_dir in commit_dir.iterdir():
            t = run_dir / idx_str / "trace.json"
            if t.exists():
                candidates.append(t)
    if not candidates:
        return None
    candidates.sort(key=lambda p: (_trace_completeness(p), str(p)), reverse=True)
    return candidates[0]


# ── Precomputed data builder ──────────────────────────────────────────────────

def build_precomputed(trace_path: Path) -> List[Dict[str, Any]]:
    """
    Extract per-session precomputed data from a trace file.
    Returns list indexed by session_index, each entry:
      {
        "session_index": int,
        "session_time": str,
        "factual": [{"text": str, "category": "", "is_definite": True}, ...],
        "judgments": {stmt_idx: {"hypotheses": [...], "candidate_ids": [...], "judgments_raw": [...]}},
      }
    Indexed by session_index so gaps are handled correctly.
    """
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    session_logs = trace["result"]["session_logs"]

    sessions_by_idx: Dict[int, Dict[str, Any]] = {}
    for slog in session_logs:
        s_idx = int(slog["session_index"])
        factual = []
        judgments: Dict[int, Dict[str, Any]] = {}
        out_idx = 0
        for stmt in slog.get("statement_log", []):
            if not stmt.get("new_item_id"):
                # Extracted in the original run but never stored as a memory item
                # (e.g. is_definite=False, or filtered/deduped) — skip it so the
                # replay creates exactly as many items as the original run did.
                # Forcing is_definite=True for this entry would create an extra
                # item and drift every subsequent item ID by one.
                continue
            factual.append({
                "text": stmt["statement"],
                "category": "",
                "is_definite": True,
            })
            judgments[out_idx] = {
                "hypotheses": stmt.get("hypotheses", []),
                "candidate_ids": stmt.get("candidate_ids", []),
                "judgments_raw": stmt.get("judgments_raw", []),
            }
            out_idx += 1
        sessions_by_idx[s_idx] = {
            "session_index": s_idx,
            "session_time": slog.get("session_time", ""),
            "factual": factual,
            "judgments": judgments,
        }
    return sessions_by_idx


# ── Core replay logic ─────────────────────────────────────────────────────────

def replay_sample(
    *,
    dataset_record: Dict[str, Any],
    abs_idx: int,
    trace_path: Path,
    engine,
    sample_dir: Path,
    no_hypothesis: bool = False,
) -> Dict[str, Any]:
    """
    Replay one sample: write phase from trace + fresh query phase.
    Returns answer dict compatible with full_eval_performance.py.

    no_hypothesis=True: only reuse factual statements from trace;
      Phase C runs with embedding similarity (no hypothesis LLM, no abductive LLM).
    no_hypothesis=False (default): reuse statements + hypotheses + judgments_raw from trace,
      skipping all Phase C LLM calls (standard replay).
    """
    t0 = time.perf_counter()
    uid = str(dataset_record.get("uid", f"idx_{abs_idx}"))

    engine.reset()
    if hasattr(engine.llm, "reset_usage_tracking"):
        engine.llm.reset_usage_tracking()

    sessions = dataset_record.get("haystack_session", [])
    timestamps = dataset_record.get("timestamps", [])
    indices = list(range(len(sessions)))

    sessions_by_idx = build_precomputed(trace_path)

    # ── Write phase: replay each session from trace data ──────────────────
    session_logs: List[Dict[str, Any]] = []
    for idx in indices:
        session = sessions[idx]
        session_time = timestamps[idx] if idx < len(timestamps) else ""

        precomputed = sessions_by_idx.get(idx)
        if precomputed is not None:
            factual = precomputed["factual"]
            # no_hypothesis: only reuse statements; embedding similarity replaces Phase C
            # nli_judge: pass precomputed judgments (hypotheses reused, judgments_raw ignored by Phase C)
            judgments = None if no_hypothesis else precomputed["judgments"]
        else:
            factual = []
            judgments = None if no_hypothesis else {}

        slog = engine.process_session(
            session=session,
            session_index=idx,
            session_time=session_time,
            precomputed_factual=factual,
            precomputed_judgments=judgments,
        )
        slog["elapsed_seconds"] = 0.0  # replay; not meaningful
        session_logs.append(slog)

        # ── Statement count sanity check ──────────────────────────────────
        replay_stmt_count = len(slog.get("statement_log", []))
        if replay_stmt_count != len(factual):
            raise RuntimeError(
                f"[{uid}] session {idx}: statement count mismatch "
                f"(trace={len(factual)}, replay={replay_stmt_count})"
            )

    # ── Verify item IDs globally ───────────────────────────────────────────
    # Re-load trace and compare all new_item_ids
    trace_raw = json.loads(trace_path.read_text(encoding="utf-8"))
    for slog_t in trace_raw["result"]["session_logs"]:
        s_idx = int(slog_t["session_index"])
        replay_slog = next((s for s in session_logs if s.get("session_index") == s_idx), None)
        if replay_slog is None:
            continue
        # Compare against a compacted index, mirroring build_precomputed's skip
        # of statements that never got a new_item_id in the original run —
        # those aren't replayed at all, so positions shift.
        replay_stmts = replay_slog.get("statement_log", [])
        out_idx = 0
        for stmt_t in slog_t.get("statement_log", []):
            expected_id = stmt_t.get("new_item_id", "")
            if not expected_id:
                continue
            actual_id = replay_stmts[out_idx].get("new_item_id", "") if out_idx < len(replay_stmts) else "MISSING"
            if actual_id != expected_id:
                raise RuntimeError(
                    f"[{uid}] session {s_idx} stmt {out_idx}: "
                    f"item ID mismatch — expected {expected_id}, got {actual_id}. "
                    f"Statement order diverged from original run."
                )
            out_idx += 1

    # ── Query phase: fresh LLM calls ──────────────────────────────────────
    probing_queries = dataset_record.get("probing_queries", {})
    query_logs: Dict[str, Any] = {}
    responses: Dict[str, str] = {}
    for label, query_text in probing_queries.items():
        qlog = engine.answer_query_v2(query_label=label, query_text=str(query_text))
        query_logs[label] = qlog
        responses[label] = qlog.get("answer", "")

    elapsed = time.perf_counter() - t0
    usage = engine.llm.get_usage_summary() if hasattr(engine.llm, "get_usage_summary") else {}

    answer = {
        "uid": uid,
        "target_model_responses": {
            "dim1_response": responses.get("dim1_query", ""),
            "dim2_response": responses.get("dim2_query", ""),
            "dim3_response": responses.get("dim3_query", ""),
        },
        "target_model_meta": {
            "dim1_meta": {"elapsed_seconds": 0},
            "dim2_meta": {"elapsed_seconds": 0},
            "dim3_meta": {"elapsed_seconds": 0},
        },
        "usage_summary": usage,
        "sample_index": abs_idx,
        "type": dataset_record.get("type", ""),
        "elapsed_seconds": round(elapsed, 2),
        "trace_source": str(trace_path),
    }

    # Save per-sample outputs (atomic write: tmp + rename, so a kill mid-write
    # can't leave a partial file that a later resume mistakes for "done")
    sample_dir.mkdir(parents=True, exist_ok=True)
    _tmp = sample_dir / "answer.json.tmp"
    _tmp.write_text(json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8")
    _tmp.replace(sample_dir / "answer.json")

    return answer


# ── Entrypoint ────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--run-name", default="replay400")
    p.add_argument("--data-path", default=str(DEFAULT_DATA_PATH))
    p.add_argument("--embedding-model-path", default=str(DEFAULT_EMBED_PATH))
    p.add_argument("--embedding-device", default="cpu")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--n-samples", type=int, default=5,
                   help="Number of samples (0=all)")
    p.add_argument("--uids", default="",
                   help="Comma-separated UIDs (overrides n-samples)")
    p.add_argument("--start-index", type=int, default=0)
    p.add_argument("--startup-stagger", type=float, default=1.0)
    p.add_argument("--no-thinking", action="store_true")
    p.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    p.add_argument("--global-temperature", type=float, default=None,
                   help="Temperature for ALL LLM calls (default: API default). Use 0.3 for paper runs.")
    p.add_argument("--per-evidence", action="store_true",
                   help="Use per-evidence pool synthesis instead of per-session (default: per-session — "
                        "verified equivalent in quality, ~21%% fewer pool_synthesis LLM calls).")
    p.add_argument("--output-dir", default="",
                   help="Override full output directory (all 400 samples go here, no commit-hash subdir).")
    p.add_argument("--no-hypothesis", action="store_true",
                   help="Ablation A-NoHyp: skip hypothesis generation + abductive LLM; use embedding similarity instead.")
    p.add_argument("--no-impression", action="store_true",
                   help="Ablation A-NoImp: skip global impression update (Phase E).")
    p.add_argument("--nli-judge", action="store_true",
                   help="Ablation A-NLI: replace abductive judgment LLM with NLI model per candidate.")
    p.add_argument("--nli-model", default="cross-encoder/nli-deberta-v3-small",
                   help="NLI CrossEncoder model name or local path (used with --nli-judge).")
    p.add_argument("--no-pool", action="store_true",
                   help="Ablation A-NoPool: skip evidence accumulation + pool synthesis LLM; "
                        "decide immediately from each judgment's own confidence.")
    p.add_argument("--pool-reset-per-session", action="store_true",
                   help="Ablation A-PoolReset: per-session pool synthesis (implies --no-per-evidence); "
                        "after each session's synthesis decision, evidence_pool is cleared so it "
                        "cannot carry over into future sessions.")
    return p.parse_args()


def get_git_commit() -> str:
    import subprocess
    try:
        return subprocess.check_output(
            ["git", "-C", str(_REPO / "RECAST"), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


def main():
    args = parse_args()

    # ── API config (same as run_new_mem.py) ───────────────────────────────
    from RECAST.run_new_mem import load_env_file, get_env, DEFAULT_ENV_FILE
    load_env_file(DEFAULT_ENV_FILE)
    model = get_env("TARGET_MODEL")
    api_key = get_env("OPENAI_API_KEY", "DEEPSEEK_API_KEY")
    base_url = get_env("OPENAI_BASE_URL") or "https://openrouter.ai/api/v1"
    if not model or not api_key:
        sys.exit("Set TARGET_MODEL and OPENAI_API_KEY in .env")

    from RECAST.llm_layer.client import LLMClient
    from RECAST.new_pipeline import NewMemEngine
    from RECAST.retrieval.embedding import build_retriever

    default_extra: dict = {}
    if args.no_thinking:
        if model.lower().startswith("qwen"):
            default_extra["extra_body"] = {"enable_thinking": False}
        else:
            default_extra["extra_body"] = {"thinking": {"type": "disabled"}}
    if args.global_temperature is not None:
        default_extra["temperature"] = args.global_temperature

    shared_retriever = build_retriever(args.embedding_model_path, device=args.embedding_device)

    # ── Dataset ───────────────────────────────────────────────────────────
    all_records = json.loads(Path(args.data_path).read_text(encoding="utf-8"))
    uid_to_idx = {str(r.get("uid", "")): i for i, r in enumerate(all_records)}

    if args.uids:
        target = {u.strip() for u in args.uids.split(",") if u.strip()}
        records = [(r, uid_to_idx[str(r["uid"])]) for r in all_records
                   if any(str(r.get("uid","")).startswith(u) for u in target)]
    else:
        end = len(all_records) if args.n_samples == 0 else args.start_index + max(args.n_samples, 1)
        records = [(r, i) for i, r in enumerate(all_records)][args.start_index:end]

    runs_root = Path(args.runs_root)
    if args.output_dir:
        run_dir = Path(args.output_dir).resolve()
        commit = "fixed-dir"
    else:
        commit = get_git_commit()
        run_dir = runs_root / commit / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    no_hyp = getattr(args, "no_hypothesis", False)
    no_imp = getattr(args, "no_impression", False)
    nli_judge = getattr(args, "nli_judge", False)
    nli_model = getattr(args, "nli_model", "cross-encoder/nli-deberta-v3-small")
    print(f"Replay: {len(records)} samples | model={model} | commit={commit}")
    print(f"Run dir: {run_dir}")
    if no_hyp:
        print(f"Mode: A-NoHyp — from trace: statements only; Phase C: embedding similarity (no hypothesis/abductive LLM)")
    elif nli_judge:
        print(f"Mode: A-NLI — from trace: statements + hypotheses; Phase C: NLI per-candidate ({nli_model})")
    else:
        print(f"Mode: standard replay — from trace: statements + hypotheses + judgments_raw")
    if no_imp:
        print(f"  + A-NoImp: Phase E (impression update) skipped")
    no_pool = getattr(args, "no_pool", False)
    pool_reset = getattr(args, "pool_reset_per_session", False)
    if no_pool:
        print(f"  + A-NoPool: evidence pool accumulation + pool_synthesis LLM skipped; "
              f"gray-zone judgments decided immediately from single-judgment confidence")
    if pool_reset:
        print(f"  + A-PoolReset: per-session pool synthesis, evidence_pool cleared after each "
              f"session's decision (no cross-session carryover)")
    print(f"Phases fresh (LLM calls): "
          f"{'pool_synthesis(skipped: no_pool)' if no_pool else 'pool_synthesis'}"
          f"{'(skipped: impression)' if no_imp else ', impression_update'}, query (3×)")

    # ── Worker function ───────────────────────────────────────────────────
    def _run_one(rec_idx):
        record, abs_idx = rec_idx
        uid = str(record.get("uid", abs_idx))

        trace_path = find_trace(abs_idx, runs_root)
        if trace_path is None:
            print(f"  [SKIP] abs_idx={abs_idx}: no trace found", flush=True)
            return None
        in_priority = any(
            str(trace_path) == str(runs_root / sub / f"{abs_idx:04d}" / "trace.json")
            for sub in TRACE_PRIORITY
        )
        if not in_priority:
            print(f"  [TRACE-FALLBACK] abs_idx={abs_idx}: using {trace_path} (not in TRACE_PRIORITY)", flush=True)

        sample_dir = run_dir / f"{abs_idx:04d}"

        # Skip if already completed
        if (sample_dir / "answer.json").exists():
            print(f"  [skip] abs_idx={abs_idx}: already done", flush=True)
            return json.loads((sample_dir / "answer.json").read_text())

        llm = LLMClient(
            model=model,
            api_key=api_key,
            base_url=base_url,
            log_dir=sample_dir / ".cache",
            default_extra_request_kwargs=default_extra or None,
        )
        pool_reset = getattr(args, "pool_reset_per_session", False)
        engine = NewMemEngine(
            llm=llm,
            retriever=shared_retriever,
            # pool_reset_per_session only fires inside _flush_evidence_pools, which only
            # runs in per-session mode — force per_evidence_pool=False when reset is on.
            per_evidence_pool=False if pool_reset else getattr(args, "per_evidence", False),
            no_hypothesis=getattr(args, "no_hypothesis", False),
            no_impression=getattr(args, "no_impression", False),
            nli_judge=nli_judge,
            nli_model_name=nli_model,
            no_pool=getattr(args, "no_pool", False),
            pool_reset_per_session=pool_reset,
        )

        try:
            print(f"  [start] uid={uid} abs_idx={abs_idx}", flush=True)
            t0 = time.perf_counter()
            answer = replay_sample(
                dataset_record=record,
                abs_idx=abs_idx,
                trace_path=trace_path,
                engine=engine,
                sample_dir=sample_dir,
                no_hypothesis=getattr(args, "no_hypothesis", False),
            )
            elapsed = time.perf_counter() - t0
            print(f"  [done]  uid={uid} ({elapsed:.1f}s)", flush=True)
            return answer
        except Exception as exc:
            print(f"  [ERROR] abs_idx={abs_idx}: {exc}", flush=True)
            return None

    # ── Run with parallelism ──────────────────────────────────────────────
    answers = []
    stagger = args.startup_stagger

    if args.workers <= 1:
        for ri in records:
            ans = _run_one(ri)
            if ans:
                answers.append(ans)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {}
            for i, ri in enumerate(records):
                if stagger > 0 and i > 0:
                    time.sleep(stagger)
                fut = ex.submit(_run_one, ri)
                futures[fut] = ri
            for fut in as_completed(futures):
                ans = fut.result()
                if ans:
                    answers.append(ans)

    print(f"\nCompleted: {len(answers)}/{len(records)} samples")

    # ── Merge and save answers ────────────────────────────────────────────
    # Collect all answer.json files in run_dir (includes previously done ones)
    all_answers = []
    for f in sorted(run_dir.glob("*/answer.json")):
        try:
            all_answers.append(json.loads(f.read_text()))
        except Exception:
            pass

    uid_to_type = {str(r.get("uid","")): r.get("type","") for r in all_records}
    t1 = [a for a in all_answers if uid_to_type.get(str(a.get("uid",""))) == "T1"]
    t2 = [a for a in all_answers if uid_to_type.get(str(a.get("uid",""))) == "T2"]

    (run_dir / "answers_T1.json").write_text(json.dumps(t1, ensure_ascii=False, indent=2))
    (run_dir / "answers_T2.json").write_text(json.dumps(t2, ensure_ascii=False, indent=2))
    print(f"answers saved: T1={len(t1)}  T2={len(t2)}")
    print(f"Run dir: {run_dir}")


if __name__ == "__main__":
    main()
