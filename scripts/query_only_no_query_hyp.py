#!/usr/bin/env python3
"""
Ablation F: w/o read-phase query hypothesis expansion.

Loads G2's final write-phase state (profile_snapshot from
runs/d215489/e2ev3_400/{idx:04d}/trace.json) for all 400 samples,
then runs the query phase with `no_query_hypothesis=True` — retrieval
uses only the original query text instead of query + expanded hypotheses.
Write phase and answer-generation CoT are unchanged from G2.

Run from /mnt/laq:
  python -m RECAST.scripts.query_only_no_query_hyp --run-name ablation_f --workers 8
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Optional

_REPO = Path(__file__).resolve().parent.parent.parent  # /mnt/laq
sys.path.insert(0, str(_REPO))

GROUP2_RUN_DIR = _REPO / "RECAST" / "runs" / "d215489" / "e2ev3_400"
DEFAULT_DATA_PATH = _REPO / "RECAST" / "STALE" / "STALE" / "outputs" / "STALE_MAIN.json"
DEFAULT_EMBED_PATH = _REPO / "RECAST" / "models" / "all-MiniLM-L6-v2"
DEFAULT_RUNS_ROOT = _REPO / "RECAST" / "runs"


def _build_store_from_snapshot(snapshot: Dict[str, Any]):
    from RECAST.store_layer.new_store import NewProfileStore
    from RECAST.memory.new_models import Evidence, GlobalImpression, MemoryItem, StaleMetadata, VersionEntry
    import re

    store = NewProfileStore()

    def _make_item(d: Dict) -> MemoryItem:
        sm = None
        if d.get("stale_metadata"):
            sd = d["stale_metadata"]
            sm = StaleMetadata(
                stale_since_session=int(sd.get("stale_since_session", 0)),
                stale_since_time=sd.get("stale_since_time", ""),
                stale_reason=sd.get("stale_reason", ""),
                superseded_by=sd.get("superseded_by", ""),
            )
        evidence = [
            Evidence(
                evidence_id=e.get("evidence_id", ""),
                statement_text=e.get("statement_text", ""),
                inference_chain=e.get("inference_chain", ""),
                confidence=float(e.get("confidence", 0.0)),
                session_index=int(e.get("session_index", 0)),
                session_time=e.get("session_time", ""),
            )
            for e in (d.get("evidence_pool") or [])
        ]
        version_log = [
            VersionEntry(
                session=int(v.get("session", 0)),
                time=v.get("time", ""),
                from_status=v.get("from_status", ""),
                to_status=v.get("to_status", ""),
                reason=v.get("reason", ""),
            )
            for v in (d.get("version_log") or [])
        ]
        return MemoryItem(
            item_id=d["item_id"],
            content=d["content"],
            status=d["status"],
            confidence=float(d.get("confidence", 0.85)),
            created_session=int(d.get("created_session", 0)),
            created_time=d.get("created_time", ""),
            last_updated_session=int(d.get("last_updated_session", 0)),
            last_updated_time=d.get("last_updated_time", ""),
            category=d.get("category", ""),
            stale_metadata=sm,
            evidence_pool=evidence,
            pool_confidence=float(d.get("pool_confidence", 0.0)),
            version_log=version_log,
        )

    max_item_n = 0
    max_evidence_n = 0
    for bucket in ("active_items", "uncertain_items", "stale_items"):
        for d in (snapshot.get(bucket) or []):
            item = _make_item(d)
            store.add_item(item)
            import re as _re
            m = _re.match(r"m_(\d+)$", item.item_id)
            if m:
                max_item_n = max(max_item_n, int(m.group(1)))
            for ev in item.evidence_pool:
                m2 = _re.match(r"e_(\d+)$", ev.evidence_id)
                if m2:
                    max_evidence_n = max(max_evidence_n, int(m2.group(1)))
    store._item_counter = max_item_n
    store._evidence_counter = max_evidence_n

    gi = snapshot.get("global_impression") or {}
    if isinstance(gi, dict):
        store.global_impression = GlobalImpression(
            content=gi.get("content", ""),
            last_updated_session=int(gi.get("last_updated_session", -1)),
            last_updated_time=gi.get("last_updated_time", ""),
            update_log=list(gi.get("update_log", [])),
        )
    elif isinstance(gi, str):
        store.global_impression.content = gi

    return store


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--run-name", default="ablation_f")
    p.add_argument("--data-path", default=str(DEFAULT_DATA_PATH))
    p.add_argument("--embedding-model-path", default=str(DEFAULT_EMBED_PATH))
    p.add_argument("--embedding-device", default="cpu")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--uids", default="", help="Comma-separated UID prefixes (default: all 400)")
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
    from RECAST.retrieval.embedding import build_retriever
    from RECAST.core.new_config import NewConfig
    from RECAST.query.new_engine import NewQueryEngineMixin

    default_extra: dict = {}
    if args.no_thinking:
        if model.lower().startswith("qwen"):
            default_extra["extra_body"] = {"enable_thinking": False}
        else:
            default_extra["extra_body"] = {"thinking": {"type": "disabled"}}

    shared_retriever = build_retriever(args.embedding_model_path, device=args.embedding_device)
    thresholds = NewConfig()

    all_records = json.loads(Path(args.data_path).read_text(encoding="utf-8"))

    if args.uids:
        target = {u.strip() for u in args.uids.split(",") if u.strip()}
        records = [(r, i) for i, r in enumerate(all_records)
                   if any(str(r.get("uid", "")).startswith(u) for u in target)]
    else:
        records = [(r, i) for i, r in enumerate(all_records)]

    run_dir = Path(args.output_dir).resolve() if args.output_dir else DEFAULT_RUNS_ROOT / "bugfix" / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Ablation F (no query hypothesis): {len(records)} samples")
    print(f"Run dir: {run_dir}")
    print("Reuses G2 final write-phase snapshot; retrieval uses query text only (no hypothesis expansion).")

    class _Engine(NewQueryEngineMixin):
        pass

    def _run_one(rec_idx):
        record, abs_idx = rec_idx
        uid = str(record.get("uid", abs_idx))
        sample_dir = run_dir / f"{abs_idx:04d}"

        if (sample_dir / "answer.json").exists():
            print(f"  [skip] abs_idx={abs_idx}: already done", flush=True)
            return json.loads((sample_dir / "answer.json").read_text())

        trace_path = GROUP2_RUN_DIR / f"{abs_idx:04d}" / "trace.json"
        if not trace_path.exists():
            print(f"  [SKIP] abs_idx={abs_idx}: no group2 trace at {trace_path}", flush=True)
            return None

        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        snapshot = trace.get("result", {}).get("final_profile_snapshot")
        if not snapshot:
            print(f"  [ERROR] abs_idx={abs_idx}: no final_profile_snapshot in trace", flush=True)
            return None

        llm = LLMClient(
            model=model,
            api_key=api_key,
            base_url=base_url,
            log_dir=sample_dir / ".cache",
            default_extra_request_kwargs=default_extra or None,
        )

        engine = _Engine()
        engine.store = _build_store_from_snapshot(snapshot)
        engine.llm = llm
        engine.embedding = shared_retriever
        engine.thresholds = thresholds
        engine.no_query_hypothesis = True

        try:
            print(f"  [start] uid={uid} abs_idx={abs_idx}", flush=True)
            t0 = time.perf_counter()
            probing_queries = record.get("probing_queries", {})
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
                "type": record.get("type", ""),
                "elapsed_seconds": round(elapsed, 2),
                "trace_source": str(trace_path),
            }
            sample_dir.mkdir(parents=True, exist_ok=True)
            _tmp = sample_dir / "answer.json.tmp"
            _tmp.write_text(json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8")
            _tmp.replace(sample_dir / "answer.json")
            print(f"  [done]  uid={uid} ({elapsed:.1f}s)", flush=True)
            return answer
        except Exception as exc:
            print(f"  [ERROR] abs_idx={abs_idx}: {exc}", flush=True)
            return None

    answers = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(_run_one, ri): ri for ri in records}
        for fut in as_completed(futures):
            ans = fut.result()
            if ans:
                answers.append(ans)

    print(f"\nCompleted: {len(answers)}/{len(records)} samples")
    t1_answers = [a for a in answers if a.get("type") == "T1"]
    t2_answers = [a for a in answers if a.get("type") == "T2"]
    (run_dir / "answers_T1.json").write_text(json.dumps(t1_answers, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "answers_T2.json").write_text(json.dumps(t2_answers, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"answers saved: T1={len(t1_answers)}  T2={len(t2_answers)}")
    print(f"Run dir: {run_dir}")


if __name__ == "__main__":
    main()
