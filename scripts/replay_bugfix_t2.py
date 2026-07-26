#!/usr/bin/env python3
"""
Selective bug-fix verification for T2 samples only.

Investigates whether commit 16e6a32's dispatch-boundary fix (weakens_support
judgments with confidence>=0.75 were silently discarded by the old dispatch
code instead of entering the evidence pool) changes T2 results — especially
dim3 (IPA) — versus the original `d215489` true-baseline ("group2") run.

Cost-saving design: for each T2 sample we detect the EARLIEST session where
the bug condition fires. Everything before that session is provably identical
between old and new dispatch code, so instead of re-running it (which would
re-pay for pool_synthesis/impression_update LLM calls that produce the exact
same outcome as the original run), we load that session's recorded
`profile_snapshot_after_session` directly into the store — zero LLM calls —
and only resume fresh LLM-driven replay from the bug session onward.

- Samples where the bug never fires: original group2 answer.json is reused
  verbatim (zero cost).
- Samples where it fires at session 0: no prefix to skip, falls back to a
  full fresh replay (same as standard replay_from_trace.py).
- Otherwise: fast-forwarded as described above.

Run from /mnt/laq:
  python -m RECAST.scripts.replay_bugfix_t2 --run-name bugfix_t2_full --workers 8
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO = Path(__file__).resolve().parent.parent.parent  # /mnt/laq
sys.path.insert(0, str(_REPO))

from RECAST.scripts.replay_from_trace import (  # noqa: E402
    DEFAULT_DATA_PATH,
    DEFAULT_EMBED_PATH,
    DEFAULT_RUNS_ROOT,
    build_precomputed,
    find_trace,
)
from RECAST.memory.new_models import (  # noqa: E402
    Evidence,
    GlobalImpression,
    MemoryItem,
    StaleMetadata,
    VersionEntry,
)

GROUP2_RUN_DIR = DEFAULT_RUNS_ROOT / "d215489" / "e2ev3_400"


# ── Bug detection ──────────────────────────────────────────────────────────────

def find_first_bug_session(trace_path: Path) -> Optional[int]:
    """Earliest session_index containing a judgment with type=='weakens_support'
    and confidence>=0.75 — the exact boundary condition the pre-16e6a32 dispatch
    code silently discarded instead of pooling. judgments_raw is Phase C output,
    independent of dispatch logic, so this detection is valid regardless of which
    dispatch version originally produced this trace."""
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    best: Optional[int] = None
    for slog in trace.get("result", {}).get("session_logs", []):
        s_idx = int(slog.get("session_index"))
        for stmt in slog.get("statement_log", []):
            for j in stmt.get("judgments_raw", []):
                jtype = str(j.get("type", "")).strip()
                try:
                    conf = float(j.get("confidence") or 0.0)
                except (TypeError, ValueError):
                    conf = 0.0
                if jtype == "weakens_support" and conf >= 0.75:
                    if best is None or s_idx < best:
                        best = s_idx
    return best


# ── Snapshot deserialization (inverse of MemoryItem.to_dict() etc.) ───────────

def _evidence_from_dict(d: Dict[str, Any]) -> Evidence:
    return Evidence(
        evidence_id=d["evidence_id"],
        statement_text=d["statement_text"],
        inference_chain=d["inference_chain"],
        confidence=float(d["confidence"]),
        session_index=int(d["session_index"]),
        session_time=d["session_time"],
    )


def _version_entry_from_dict(d: Dict[str, Any]) -> VersionEntry:
    return VersionEntry(
        session=int(d["session"]),
        time=d["time"],
        from_status=d["from_status"],
        to_status=d["to_status"],
        reason=d["reason"],
    )


def _stale_metadata_from_dict(d: Optional[Dict[str, Any]]) -> Optional[StaleMetadata]:
    if d is None:
        return None
    return StaleMetadata(
        stale_since_session=int(d["stale_since_session"]),
        stale_since_time=d["stale_since_time"],
        stale_reason=d["stale_reason"],
        superseded_by=d.get("superseded_by", ""),
    )


def _item_from_dict(d: Dict[str, Any]) -> MemoryItem:
    return MemoryItem(
        item_id=d["item_id"],
        content=d["content"],
        status=d["status"],
        confidence=float(d["confidence"]),
        created_session=int(d["created_session"]),
        created_time=d["created_time"],
        last_updated_session=int(d["last_updated_session"]),
        last_updated_time=d["last_updated_time"],
        category=d.get("category", ""),
        stale_metadata=_stale_metadata_from_dict(d.get("stale_metadata")),
        evidence_pool=[_evidence_from_dict(e) for e in d.get("evidence_pool", [])],
        pool_confidence=float(d.get("pool_confidence", 0.0)),
        version_log=[_version_entry_from_dict(v) for v in d.get("version_log", [])],
    )


def load_snapshot_into_store(store, snapshot: Dict[str, Any]) -> None:
    """Faithfully reconstruct full store state (items + global_impression + ID
    counters) from a trace's profile_snapshot_after_session dict. Zero LLM calls —
    this is a direct, lossless deserialization of state the original run already
    computed and verified."""
    max_item_n = 0
    max_evidence_n = 0
    for bucket in ("active_items", "uncertain_items", "stale_items"):
        for d in snapshot.get(bucket, []):
            item = _item_from_dict(d)
            store.add_item(item)
            m = re.match(r"m_(\d+)$", item.item_id)
            if m:
                max_item_n = max(max_item_n, int(m.group(1)))
            for ev in item.evidence_pool:
                m2 = re.match(r"e_(\d+)$", ev.evidence_id)
                if m2:
                    max_evidence_n = max(max_evidence_n, int(m2.group(1)))
    store._item_counter = max_item_n
    store._evidence_counter = max_evidence_n

    gi = snapshot.get("global_impression") or {}
    store.global_impression = GlobalImpression(
        content=gi.get("content", ""),
        last_updated_session=int(gi.get("last_updated_session", -1)),
        last_updated_time=gi.get("last_updated_time", ""),
        update_log=list(gi.get("update_log", [])),
    )


# ── Core selective replay ──────────────────────────────────────────────────────

def replay_sample_selective(
    *,
    dataset_record: Dict[str, Any],
    abs_idx: int,
    trace_path: Path,
    engine,
    sample_dir: Path,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    uid = str(dataset_record.get("uid", f"idx_{abs_idx}"))

    engine.reset()
    if hasattr(engine.llm, "reset_usage_tracking"):
        engine.llm.reset_usage_tracking()

    sessions = dataset_record.get("haystack_session", [])
    timestamps = dataset_record.get("timestamps", [])
    indices = list(range(len(sessions)))

    sessions_by_idx = build_precomputed(trace_path)
    trace_raw = json.loads(trace_path.read_text(encoding="utf-8"))
    trace_session_logs = {int(s["session_index"]): s for s in trace_raw["result"]["session_logs"]}

    first_bug_session = find_first_bug_session(trace_path)
    fastforwarded = False
    start_idx = 0

    if first_bug_session is not None and first_bug_session > 0:
        candidate_idxs = [s for s in trace_session_logs if s < first_bug_session]
        if candidate_idxs:
            seed_idx = max(candidate_idxs)
            snapshot = trace_session_logs[seed_idx].get("profile_snapshot_after_session")
            if snapshot:
                load_snapshot_into_store(engine.store, snapshot)
                start_idx = seed_idx + 1
                fastforwarded = True

    session_logs: List[Dict[str, Any]] = []
    for idx in indices:
        if idx < start_idx:
            continue
        session = sessions[idx]
        session_time = timestamps[idx] if idx < len(timestamps) else ""

        precomputed = sessions_by_idx.get(idx)
        if precomputed is not None:
            factual = precomputed["factual"]
            judgments = precomputed["judgments"]
        else:
            factual = []
            judgments = {}

        slog = engine.process_session(
            session=session,
            session_index=idx,
            session_time=session_time,
            precomputed_factual=factual,
            precomputed_judgments=judgments,
        )
        slog["elapsed_seconds"] = 0.0
        session_logs.append(slog)

        replay_stmt_count = len(slog.get("statement_log", []))
        if replay_stmt_count != len(factual):
            raise RuntimeError(
                f"[{uid}] session {idx}: statement count mismatch "
                f"(trace={len(factual)}, replay={replay_stmt_count})"
            )

    # Verify item IDs only for sessions actually replayed (idx >= start_idx) —
    # the fast-forwarded prefix is loaded verbatim from a verified snapshot, no
    # need to re-verify IDs that we didn't regenerate.
    for s_idx, slog_t in trace_session_logs.items():
        if s_idx < start_idx:
            continue
        replay_slog = next((s for s in session_logs if s.get("session_index") == s_idx), None)
        if replay_slog is None:
            continue
        replay_stmts = replay_slog.get("statement_log", [])
        out_idx = 0
        for stmt_t in slog_t.get("statement_log", []):
            expected_id = stmt_t.get("new_item_id", "")
            if not expected_id:
                continue
            actual_id = replay_stmts[out_idx].get("new_item_id", "") if out_idx < len(replay_stmts) else "MISSING"
            if actual_id != expected_id:
                raise RuntimeError(
                    f"[{uid}] session {s_idx} stmt {out_idx}: item ID mismatch — "
                    f"expected {expected_id}, got {actual_id}. Statement order "
                    f"diverged from original run."
                )
            out_idx += 1

    probing_queries = dataset_record.get("probing_queries", {})
    responses: Dict[str, str] = {}
    for label, query_text in probing_queries.items():
        qlog = engine.answer_query_v2(query_label=label, query_text=str(query_text))
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
        "first_bug_session": first_bug_session,
        "fastforwarded": fastforwarded,
        "fastforward_resume_session": start_idx if fastforwarded else None,
    }

    sample_dir.mkdir(parents=True, exist_ok=True)
    _tmp = sample_dir / "answer.json.tmp"
    _tmp.write_text(json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8")
    _tmp.replace(sample_dir / "answer.json")
    return answer


# ── Entrypoint ────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--run-name", default="bugfix_t2")
    p.add_argument("--type", default="T2", choices=["T1", "T2"], help="Which conflict type to replay (default: T2)")
    p.add_argument("--data-path", default=str(DEFAULT_DATA_PATH))
    p.add_argument("--embedding-model-path", default=str(DEFAULT_EMBED_PATH))
    p.add_argument("--embedding-device", default="cpu")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--uids", default="", help="Comma-separated UIDs (overrides --type filter)")
    p.add_argument("--startup-stagger", type=float, default=1.0)
    p.add_argument("--no-thinking", action="store_true")
    p.add_argument("--output-dir", default="")
    return p.parse_args()


def main():
    args = parse_args()

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

    shared_retriever = build_retriever(args.embedding_model_path, device=args.embedding_device)

    all_records = json.loads(Path(args.data_path).read_text(encoding="utf-8"))
    uid_to_idx = {str(r.get("uid", "")): i for i, r in enumerate(all_records)}

    if args.uids:
        target = {u.strip() for u in args.uids.split(",") if u.strip()}
        records = [(r, uid_to_idx[str(r["uid"])]) for r in all_records
                   if any(str(r.get("uid", "")).startswith(u) for u in target)]
    else:
        records = [(r, i) for i, r in enumerate(all_records) if r.get("type") == args.type]

    run_dir = Path(args.output_dir).resolve() if args.output_dir else DEFAULT_RUNS_ROOT / "bugfix" / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Selective {args.type} bug-fix replay: {len(records)} samples")
    print(f"Run dir: {run_dir}")
    print("Reuse rule: bug-unaffected samples copy group2 answer.json verbatim;")
    print("affected samples fast-forward via profile_snapshot_after_session to the")
    print("session before the first bug occurrence, then replay fresh from there.")

    def _run_one(rec_idx):
        record, abs_idx = rec_idx
        uid = str(record.get("uid", abs_idx))
        sample_dir = run_dir / f"{abs_idx:04d}"

        if (sample_dir / "answer.json").exists():
            print(f"  [skip] abs_idx={abs_idx}: already done", flush=True)
            return json.loads((sample_dir / "answer.json").read_text())

        trace_path = find_trace(abs_idx, DEFAULT_RUNS_ROOT)
        if trace_path is None:
            print(f"  [SKIP] abs_idx={abs_idx}: no trace found", flush=True)
            return None

        first_bug_session = find_first_bug_session(trace_path)
        if first_bug_session is None:
            # Unaffected — copy group2's original answer.json verbatim, zero cost.
            orig_answer = GROUP2_RUN_DIR / f"{abs_idx:04d}" / "answer.json"
            if not orig_answer.exists():
                print(f"  [ERROR] abs_idx={abs_idx}: unaffected but no group2 answer found at {orig_answer}", flush=True)
                return None
            data = json.loads(orig_answer.read_text())
            data["bugfix_reused_verbatim"] = True
            sample_dir.mkdir(parents=True, exist_ok=True)
            _tmp = sample_dir / "answer.json.tmp"
            _tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            _tmp.replace(sample_dir / "answer.json")
            print(f"  [reuse] uid={uid} abs_idx={abs_idx}: bug never fires, copied group2 answer verbatim", flush=True)
            return data

        llm = LLMClient(
            model=model,
            api_key=api_key,
            base_url=base_url,
            log_dir=sample_dir / ".cache",
            default_extra_request_kwargs=default_extra or None,
        )
        engine = NewMemEngine(
            llm=llm,
            retriever=shared_retriever,
            per_evidence_pool=True,
        )

        try:
            print(f"  [start] uid={uid} abs_idx={abs_idx} first_bug_session={first_bug_session}", flush=True)
            t0 = time.perf_counter()
            answer = replay_sample_selective(
                dataset_record=record,
                abs_idx=abs_idx,
                trace_path=trace_path,
                engine=engine,
                sample_dir=sample_dir,
            )
            elapsed = time.perf_counter() - t0
            ff_note = f" (fast-forwarded to session {answer.get('fastforward_resume_session')})" if answer.get("fastforwarded") else ""
            print(f"  [done]  uid={uid} ({elapsed:.1f}s){ff_note}", flush=True)
            return answer
        except Exception as exc:
            print(f"  [ERROR] abs_idx={abs_idx}: {exc}", flush=True)
            return None

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

    (run_dir / f"answers_{args.type}.json").write_text(
        json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"answers saved: {args.type}={len(answers)}")
    print(f"Run dir: {run_dir}")


if __name__ == "__main__":
    main()
