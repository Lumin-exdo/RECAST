from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_PATH = BASE_DIR.parent / "STALE" / "STALE" / "outputs" / "STALE_MAIN.json"
DEFAULT_EMBEDDING_MODEL_PATH = BASE_DIR.parent / "STALE" / "cup_mem" / "models" / "all-MiniLM-L6-v2"
DEFAULT_RUNS_ROOT = BASE_DIR / "runs"
# Look for .env in project root first, then fall back to legacy STALE path
DEFAULT_ENV_FILE = (BASE_DIR / ".env") if (BASE_DIR / ".env").exists() else BASE_DIR.parent / "STALE" / "STALE" / ".env"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


def get_git_commit() -> str:
    import subprocess
    try:
        return subprocess.check_output(
            ["git", "-C", str(BASE_DIR), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def get_env(*names: str) -> str:
    for name in names:
        val = os.environ.get(name, "").strip()
        if val:
            return val
    return ""


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_records(path: Path) -> List[Dict[str, Any]]:
    payload = load_json(path)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    raise ValueError(f"Unsupported dataset format in {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run NewMemEngine on STALE samples.")
    parser.add_argument("--data-path", type=str, default="")
    parser.add_argument("--n-samples", type=int, default=5, help="Number of samples to run (0 = all)")
    parser.add_argument("--sample-index", type=int, default=-1, help="Run single sample at this index (-1 = run n-samples)")
    parser.add_argument("--type", type=str, default="all", choices=["T1", "T2", "all"], help="Filter by sample type")
    parser.add_argument("--session-mode", type=str, default="full", choices=["full", "relevant_only"])
    parser.add_argument("--run-name", type=str, default="default", help="Experiment label (e.g. reeval_failing, full_T1)")
    parser.add_argument("--start-index", type=int, default=0, help="Slice start (inclusive)")
    parser.add_argument("--end-index", type=int, default=-1, help="Slice end (exclusive, -1=all)")
    parser.add_argument("--use-cache", action="store_true", help="Enable per-sample LLM cache at {idx:04d}/.cache/ (default: no cache)")
    parser.add_argument("--embedding-model-path", type=str, default="")
    parser.add_argument("--embedding-device", type=str, default="cpu")
    parser.add_argument("--seed", type=int, default=-1, help="Random seed for --n-samples shuffle (-1 = take first N, no shuffle)")
    parser.add_argument("--no-thinking", action="store_true", help="Disable chain-of-thought thinking for DeepSeek models")
    parser.add_argument("--uids", type=str, default="", help="Comma-separated list of UIDs to run (overrides --n-samples / --start-index)")
    parser.add_argument("--workers", type=int, default=0, help="Parallel worker threads (0 = one per sample, 1 = serial)")
    parser.add_argument("--startup-stagger", type=float, default=0.0, help="Seconds to wait between submitting each worker (avoids burst API load at startup)")
    parser.add_argument("--commit-override", type=str, default="", help="Override git commit hash used in run directory path (e.g. 7094eb6)")
    parser.add_argument("--run-dir", type=str, default="", help="Directly specify run directory (overrides commit+run-name path)")
    parser.add_argument("--query-only", action="store_true", help="Skip session processing; restore store from existing trace.json and rerun queries only")
    parser.add_argument("--env-file", type=str, default="", help="Path to .env file with API keys (default: .env in project root)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    env_path = Path(args.env_file).resolve() if args.env_file else DEFAULT_ENV_FILE
    load_env_file(env_path)

    model = get_env("TARGET_MODEL")
    api_key = get_env("DEEPSEEK_API_KEY", "OPENAI_API_KEY")
    base_url = get_env("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL

    if not model:
        raise ValueError("No model configured. Set TARGET_MODEL in STALE/.env.")
    if not api_key:
        raise ValueError("No API key configured. Set OPENAI_API_KEY in STALE/.env.")

    data_path = Path(args.data_path).resolve() if args.data_path else DEFAULT_DATA_PATH.resolve()
    if not data_path.exists():
        raise FileNotFoundError(f"Dataset not found: {data_path}")

    embedding_path_str = args.embedding_model_path or str(DEFAULT_EMBEDDING_MODEL_PATH.resolve())
    embedding_path = Path(embedding_path_str)
    if not embedding_path.exists():
        raise FileNotFoundError(f"Embedding model not found: {embedding_path}")

    if args.run_dir:
        run_dir = Path(args.run_dir).resolve()
    else:
        commit = args.commit_override.strip() if args.commit_override.strip() else get_git_commit()
        run_dir = DEFAULT_RUNS_ROOT / commit / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    from RECAST.llm_layer.client import LLMClient
    from RECAST.new_pipeline import NewMemEngine
    from RECAST.core.new_config import NewConfig
    from RECAST.retrieval.embedding import build_retriever

    default_extra: dict = {}
    if args.no_thinking:
        default_extra["extra_body"] = {"thinking": {"type": "disabled"}}

    # Build embedding model once; it's read-only and safe to share across threads.
    shared_retriever = build_retriever(str(embedding_path), device=args.embedding_device)

    all_records = load_records(data_path)
    # Build uid→original_index map before any filtering so abs_idx is always the true dataset index
    uid_to_original_idx: dict = {str(r.get("uid", "")): i for i, r in enumerate(all_records)}

    if args.type != "all":
        all_records = [r for r in all_records if r.get("type") == args.type]

    uid_index_map: dict = {}  # uid → original dataset index (for abs_idx / sample_dir naming)
    if args.uids:
        target_uids = {u.strip() for u in args.uids.split(",") if u.strip()}
        records_to_run = []
        for r in all_records:
            uid_str = str(r.get("uid", ""))
            if any(uid_str.startswith(u) for u in target_uids):
                records_to_run.append(r)
                uid_index_map[uid_str] = uid_to_original_idx[uid_str]
        if not records_to_run:
            raise ValueError(f"No records matched UIDs: {target_uids}")
    elif args.sample_index >= 0:
        if args.sample_index >= len(all_records):
            raise IndexError(f"sample_index {args.sample_index} out of range (total {len(all_records)})")
        records_to_run = [all_records[args.sample_index]]
    else:
        end = args.end_index if args.end_index >= 0 else len(all_records)
        records_to_run = all_records[args.start_index:end]
        if args.n_samples > 0:
            if args.seed >= 0:
                import random as _random
                _random.Random(args.seed).shuffle(records_to_run)
            records_to_run = records_to_run[:args.n_samples]

    n_workers = args.workers if args.workers > 0 else len(records_to_run)
    print(f"Running {len(records_to_run)} samples with model={model}, session_mode={args.session_mode}, workers={n_workers}")
    print(f"Run dir: {run_dir}  [commit={commit}, run_name={args.run_name}]")

    def _run_one(item: Dict[str, Any], abs_idx: int) -> Dict[str, Any]:
        uid = str(item.get("uid", f"item_{abs_idx}"))
        print(f"  [start] uid={uid} abs_idx={abs_idx}", flush=True)

        sample_dir = run_dir / f"{abs_idx:04d}"
        sample_dir.mkdir(exist_ok=True)
        log_dir = sample_dir / ".cache"
        log_dir.mkdir(exist_ok=True)

        # Each worker gets its own LLMClient and engine to avoid shared mutable state.
        worker_llm = LLMClient(
            model=model,
            api_key=api_key,
            base_url=base_url,
            cache_dir=sample_dir / ".cache" if args.use_cache else None,
            log_dir=log_dir,
            default_extra_request_kwargs=default_extra or None,
        )
        worker_engine = NewMemEngine(
            llm=worker_llm,
            thresholds=NewConfig(),
            retriever=shared_retriever,
        )

        t0 = time.perf_counter()
        try:
            # --query-only: restore store from existing trace snapshot, skip session phase
            existing_trace_path = sample_dir / "trace.json"
            if args.query_only and existing_trace_path.exists():
                with existing_trace_path.open(encoding="utf-8") as _f:
                    existing_trace = json.load(_f)
                existing_result = existing_trace.get("result", {})
                snapshot = existing_result.get("final_profile_snapshot", {})
                if snapshot:
                    worker_engine.store.from_snapshot(snapshot)
                    # Run only the query phase
                    probing_queries = item.get("probing_queries", {})
                    query_logs: Dict[str, Any] = {}
                    for label, query_text in probing_queries.items():
                        qlog = worker_engine.answer_query(query_label=label, query_text=str(query_text))
                        qlog["elapsed_seconds"] = 0.0
                        query_logs[label] = qlog
                    # Merge into existing result, preserving session_logs etc.
                    existing_result["query_logs"] = query_logs
                    existing_result["completed_query_count"] = len(query_logs)
                    result = existing_result
                    print(f"  [query-only] uid={uid} restored from snapshot, ran {len(query_logs)} queries", flush=True)
                else:
                    raise ValueError("No final_profile_snapshot in existing trace — cannot use --query-only")
            else:
                result = worker_engine.run_sample(
                    item,
                    sample_index=abs_idx,
                    session_mode=args.session_mode,
                )
            elapsed = time.perf_counter() - t0
            print(f"  [done]  uid={uid} ({elapsed:.1f}s)", flush=True)
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            print(f"  [ERROR] uid={uid} {exc} ({elapsed:.1f}s)", flush=True)
            result = {"error": str(exc), "query_logs": {}}

        query_logs = result.get("query_logs", {})
        responses: Dict[str, str] = {}
        dim_meta: Dict[str, Any] = {}
        for dim_key in ("dim1", "dim2", "dim3"):
            qlog = query_logs.get(f"{dim_key}_query", {})
            answer_text = ""
            if isinstance(qlog, dict):
                answer_text = str(qlog.get("answer", "")).strip()
                if not answer_text:
                    inner = qlog.get("answer", {})
                    if isinstance(inner, dict):
                        answer_text = str(inner.get("answer", "")).strip()
            responses[f"{dim_key}_response"] = answer_text
            dim_meta[f"{dim_key}_meta"] = {
                "elapsed_seconds": float(qlog.get("elapsed_seconds", 0.0)) if isinstance(qlog, dict) else 0.0,
                "usage": {},
            }

        call_records = worker_llm.get_call_records() if hasattr(worker_llm, "get_call_records") else []
        usage_summary = worker_llm.get_usage_summary() if hasattr(worker_llm, "get_usage_summary") else {}

        answer_record = {
            "uid": uid,
            "target_model_responses": responses,
            "target_model_meta": dim_meta,
            "usage_summary": usage_summary,
            "sample_index": abs_idx,
            "type": item.get("type", ""),
            "elapsed_seconds": elapsed,
        }
        trace_record = {"uid": uid, "result": result, "call_records": call_records}

        (sample_dir / "answer.json").write_text(
            json.dumps(answer_record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (sample_dir / "trace.json").write_text(
            json.dumps(trace_record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return answer_record

    # Build (item, abs_idx) list
    indexed: List[tuple] = []
    for seq_idx, item in enumerate(records_to_run):
        uid = str(item.get("uid", f"item_{seq_idx}"))
        if uid in uid_index_map:
            abs_idx = uid_index_map[uid]
        elif args.sample_index >= 0:
            abs_idx = args.sample_index
        else:
            abs_idx = args.start_index + seq_idx
        indexed.append((item, abs_idx))

    answers: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        futures = {}
        for i, (item, abs_idx) in enumerate(indexed):
            if i > 0 and args.startup_stagger > 0:
                time.sleep(args.startup_stagger)
            futures[executor.submit(_run_one, item, abs_idx)] = abs_idx
        for future in as_completed(futures):
            answers.append(future.result())

    # Merge all answers into a single file for the scorer
    answers_path = run_dir / "answers.json"
    answers_path.write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nDone. {run_dir}/")
    print(f"  Per-sample: {run_dir}/{{idx:04d}}/answer.json + trace.json")
    print(f"  Merged for scorer: {answers_path}")
    print(f"\nTo evaluate:")
    print(f"  cd /home/lumin_exdo/STALE/STALE/Evaluation")
    print(f"  python full_eval_performance.py \\")
    print(f"    --answers-path {answers_path} \\")
    print(f"    --dataset-path {data_path} \\")
    print(f"    --output-path {run_dir}/scores.json \\")
    print(f"    --model-method new_mem \\")
    print(f"    --conflict-type T1")


if __name__ == "__main__":
    main()
