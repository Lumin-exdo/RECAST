"""
Query-only runner: load write-phase memory state from 63d3571 traces,
run answer_query_v2() (E2E, no premise_check), save results for scoring.

Run from /mnt/laq:
  python -m RECAST.scripts.query_only_runner --run-name qonly_v2 --workers 4

Output: RECAST/runs/qonly_v2/merged.json
"""

from __future__ import annotations

import argparse
import concurrent.futures
import glob
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# RECAST package lives at /mnt/laq/RECAST
RECAST_ROOT = Path(__file__).resolve().parents[1]   # .../RECAST
WORK_DIR    = RECAST_ROOT.parent                     # /mnt/laq (package parent)

TRACES_V2   = RECAST_ROOT / "runs/63d3571/improved_60_v2"
TRACES_FILL = RECAST_ROOT / "runs/63d3571/improved_60_fill"
STALE_JSON  = RECAST_ROOT / "STALE/STALE/outputs/STALE_MAIN.json"
EMB_PATH    = RECAST_ROOT / "models/all-MiniLM-L6-v2"
RUNS_DIR    = RECAST_ROOT / "runs"


# ── store reconstruction ───────────────────────────────────────────────────── #
def _build_store_from_snapshot(snapshot: Dict[str, Any]):
    from RECAST.store_layer.new_store import NewProfileStore
    from RECAST.memory.new_models import Evidence, MemoryItem, StaleMetadata, VersionEntry

    store = NewProfileStore()

    def _make_item(d: Dict) -> MemoryItem:
        sm = None
        if d.get("stale_metadata"):
            sd = d["stale_metadata"]
            sm = StaleMetadata(
                stale_since_session=sd.get("stale_since_session", 0),
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
                session=v.get("session", 0),
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

    for bucket in ("active_items", "uncertain_items", "stale_items"):
        for item_dict in (snapshot.get(bucket) or []):
            store.add_item(_make_item(item_dict))

    gi = snapshot.get("global_impression", {})
    if isinstance(gi, dict):
        store.global_impression.content = gi.get("content", "")
    elif isinstance(gi, str):
        store.global_impression.content = gi

    return store


# ── uid → trace path ───────────────────────────────────────────────────────── #
def _build_uid_map(traces_dir: Optional[Path] = None) -> Dict[str, Path]:
    uid_map: Dict[str, Path] = {}
    if traces_dir is not None:
        patterns = [str(traces_dir / "*/trace.json")]
    else:
        patterns = [
            str(TRACES_V2 / "*/trace.json"),
            str(TRACES_FILL / "*/trace.json"),
        ]
    for pattern in patterns:
        for p in glob.glob(pattern):
            try:
                with open(p) as f:
                    t = json.load(f)
                uid = (t.get("uid") or
                       t.get("result", {}).get("sample_meta", {}).get("uid", ""))
                if uid:
                    uid_map[uid[:8]] = Path(p)
            except Exception:
                pass
    return uid_map


# ── STALE queries ─────────────────────────────────────────────────────────── #
def _load_stale_queries() -> Dict[str, Dict[str, str]]:
    with open(STALE_JSON) as f:
        samples = json.load(f)
    result = {}
    for s in samples:
        uid8 = s.get("uid", "")[:8]
        pq = s.get("probing_queries", {})
        result[uid8] = {
            "dim1_query": pq.get("dim1_query", ""),
            "dim2_query": pq.get("dim2_query", ""),
            "dim3_query": pq.get("dim3_query", ""),
        }
    return result


# ── per-sample processing ─────────────────────────────────────────────────── #
def process_sample(
    uid8: str,
    trace_path: Path,
    queries: Dict[str, str],
    llm,
    retriever,
    thresholds,
) -> Dict[str, Any]:
    from RECAST.query.new_engine import NewQueryEngineMixin
    from RECAST.store_layer.new_store import NewProfileStore

    with open(trace_path) as f:
        trace = json.load(f)

    snapshot = trace.get("result", {}).get("final_profile_snapshot")
    if not snapshot:
        print(f"  [{uid8}] ERROR: no final_profile_snapshot", flush=True)
        return {"uid": uid8, "error": "no snapshot"}

    store = _build_store_from_snapshot(snapshot)

    class _Engine(NewQueryEngineMixin):
        pass

    engine = _Engine()
    engine.store = store
    engine.llm = llm
    engine.embedding = retriever
    engine.thresholds = thresholds

    dim_responses: Dict[str, str] = {}
    dim_meta: Dict[str, Any] = {}

    for dim in ("dim1_query", "dim2_query", "dim3_query"):
        q = queries.get(dim, "")
        if not q:
            dim_responses[dim.replace("_query", "_response")] = ""
            dim_meta[dim.replace("_query", "_meta")] = {}
            continue
        label = dim.replace("_query", "")
        t0 = time.time()
        result = engine.answer_query_v2(query_label=label, query_text=q)
        elapsed = round(time.time() - t0, 1)
        answer = result.get("answer", "")
        verdict = result.get("verdict", {})
        dim_responses[dim.replace("_query", "_response")] = answer
        dim_meta[dim.replace("_query", "_meta")] = {
            **verdict,
            "elapsed_seconds": elapsed,
            "retrieved_n": len(result.get("retrieved_ids", [])),
        }
        print(
            f"  [{uid8}] {label} done ({elapsed}s)  "
            f"conflicts={verdict.get('conflicts', [])}",
            flush=True,
        )

    sample_type = "T1" if str(TRACES_V2) in str(trace_path) else "T2"
    return {
        "uid": uid8,
        "type": sample_type,
        "target_model_responses": dim_responses,
        "target_model_meta": dim_meta,
        "usage_summary": {},
    }


# ── target UIDs ───────────────────────────────────────────────────────────── #
T1_UIDS = (
    "89b77229,7ee76c41,1a85388f,f6d12075,d9545076,e229c5cd,eacb64ff,fdada4cc,"
    "a4b2e2fd,2006d545,d74f7f3e,b17c5c02,b35794f3,7a7621e2,34d402c0,6ff5a576,"
    "e72a2ba5,93a1c511,f7fb891b,79e4cc40,2ba8e3f4,26e99c95,dae22057,eee1a643,"
    "e51c1d33,e1703b4d,9867971c,8aeb8778,a6170008,3305ce57"
).split(",")

T2_UIDS = (
    "d806d94c,feef3933,14897e47,c9cc370e,2c711459,993152aa,c03f7b53,60604200,"
    "06071a3e,2d92d1c2,fbe6fd55,28daa975,27a52329,830a2e06,a2a3e641,da38532d,"
    "48707e03,f50107f1,ea1bd523,855155ad,1469bde3,5a4781fe,5ae24023,87ea8043,"
    "14ed299f,4ad50bc6,5372c535,d13024ef,c2cc2d39,53d876a2"
).split(",")


# ── main ──────────────────────────────────────────────────────────────────── #
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-name", default="qonly_v3")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--embedding-model-path", default=str(EMB_PATH))
    parser.add_argument("--embedding-device", default="cpu")
    parser.add_argument(
        "--traces-dir",
        default=str(RECAST_ROOT / "runs/807dee0/full_60"),
        help="Directory containing */trace.json subdirs (default: 807dee0/full_60)",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(WORK_DIR))
    from RECAST.llm_layer.client import LLMClient
    from RECAST.retrieval.embedding import build_retriever
    from RECAST.core.new_config import NewConfig

    # Load API credentials from .env
    env_file = RECAST_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

    out_dir = RUNS_DIR / args.run_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "merged.json"

    print(f"Query-only runner  run={args.run_name}  model={args.model}  workers={args.workers}",
          flush=True)

    traces_dir = Path(args.traces_dir) if args.traces_dir else None
    uid_map = _build_uid_map(traces_dir)
    stale_queries = _load_stale_queries()
    target_uids = T1_UIDS + T2_UIDS

    missing = [u for u in target_uids if u not in uid_map]
    if missing:
        print(f"WARNING: {len(missing)} UIDs missing traces: {missing[:5]}", flush=True)

    work_items = [
        (u, uid_map[u], stale_queries.get(u, {}))
        for u in target_uids if u in uid_map
    ]
    print(f"Processing {len(work_items)} samples", flush=True)

    # DeepSeek: disable thinking via extra_body
    default_extra = {"extra_body": {"thinking": {"type": "disabled"}}}
    llm = LLMClient(
        model=args.model,
        api_key=api_key,
        base_url=base_url,
        default_extra_request_kwargs=default_extra,
    )
    retriever = build_retriever(args.embedding_model_path, device=args.embedding_device)
    thresholds = NewConfig()

    results: List[Dict] = []

    def _run(item):
        uid8, trace_path, queries = item
        return process_sample(uid8, trace_path, queries, llm, retriever, thresholds)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_run, item): item[0] for item in work_items}
        for fut in concurrent.futures.as_completed(futures):
            uid8 = futures[fut]
            try:
                res = fut.result()
                results.append(res)
                print(f"[{len(results)}/{len(work_items)}] {uid8} complete", flush=True)
            except Exception as exc:
                import traceback
                print(f"[FAIL] {uid8}: {exc}", flush=True)
                traceback.print_exc()
                results.append({"uid": uid8, "error": str(exc)})

    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    errors = [r for r in results if "error" in r]
    print(f"\nDone. {len(results)} samples → {out_path}", flush=True)
    if errors:
        print(f"Errors: {[e['uid'] for e in errors]}", flush=True)


if __name__ == "__main__":
    main()
