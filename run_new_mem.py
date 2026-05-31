from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_PATH = BASE_DIR.parent / "STALE" / "STALE" / "outputs" / "STALE_MAIN.json"
DEFAULT_EMBEDDING_MODEL_PATH = BASE_DIR.parent / "STALE" / "cup_mem" / "models" / "all-MiniLM-L6-v2"
DEFAULT_OUTPUT_ROOT = BASE_DIR / "runs_new_mem"
DEFAULT_ENV_FILE = BASE_DIR.parent / "STALE" / "STALE" / ".env"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


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
    parser.add_argument("--output-root", type=str, default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--start-index", type=int, default=0, help="Slice start (inclusive)")
    parser.add_argument("--end-index", type=int, default=-1, help="Slice end (exclusive, -1=all)")
    parser.add_argument("--cache-dir", type=str, default="", help="Shared LLM cache dir (optional, overrides per-run cache)")
    parser.add_argument("--embedding-model-path", type=str, default="")
    parser.add_argument("--embedding-device", type=str, default="cpu")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_file(DEFAULT_ENV_FILE)

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

    output_root = Path(args.output_root).resolve()
    run_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    from MyMem.llm_layer.client import LLMClient
    from MyMem.new_pipeline import NewMemEngine
    from MyMem.core.new_config import NewConfig

    cache_dir = Path(args.cache_dir).resolve() if args.cache_dir else run_dir / ".cache"
    llm = LLMClient(
        model=model,
        api_key=api_key,
        base_url=base_url,
        cache_dir=cache_dir,
    )
    engine = NewMemEngine(
        llm=llm,
        embedding_model_path=str(embedding_path),
        embedding_device=args.embedding_device,
        thresholds=NewConfig(),
    )

    all_records = load_records(data_path)

    if args.type != "all":
        all_records = [r for r in all_records if r.get("type") == args.type]

    if args.sample_index >= 0:
        if args.sample_index >= len(all_records):
            raise IndexError(f"sample_index {args.sample_index} out of range (total {len(all_records)})")
        records_to_run = [all_records[args.sample_index]]
    else:
        end = args.end_index if args.end_index >= 0 else len(all_records)
        records_to_run = all_records[args.start_index:end]
        if args.n_samples > 0:
            records_to_run = records_to_run[: args.n_samples]

    print(f"Running {len(records_to_run)} samples with model={model}, session_mode={args.session_mode}")
    print(f"Output dir: {run_dir}")

    answers: List[Dict[str, Any]] = []
    traces: List[Dict[str, Any]] = []
    answers_path = run_dir / "answers.json"
    traces_path = run_dir / "traces.json"

    for idx, item in enumerate(records_to_run):
        uid = str(item.get("uid", f"item_{idx}"))
        abs_idx = args.start_index + idx if args.sample_index < 0 else args.sample_index
        print(f"  [{idx+1}/{len(records_to_run)}] uid={uid} type={item.get('type', '?')} ...", end="", flush=True)

        if hasattr(llm, "reset_usage_tracking"):
            llm.reset_usage_tracking()

        t0 = time.perf_counter()
        try:
            result = engine.run_sample(
                item,
                sample_index=abs_idx,
                session_mode=args.session_mode,
            )
            elapsed = time.perf_counter() - t0
            print(f" done ({elapsed:.1f}s)")
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            print(f" ERROR: {exc} ({elapsed:.1f}s)")
            result = {"error": str(exc), "query_logs": {}}

        query_logs = result.get("query_logs", {})
        dim_meta: Dict[str, Any] = {}
        responses: Dict[str, str] = {}

        for dim_key in ("dim1", "dim2", "dim3"):
            label = f"{dim_key}_query"
            qlog = query_logs.get(label, {})
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

        call_records = llm.get_call_records() if hasattr(llm, "get_call_records") else []
        usage_summary = {}
        if hasattr(llm, "get_usage_summary"):
            usage_summary = llm.get_usage_summary()

        answer_record = {
            "uid": uid,
            "target_model_responses": responses,
            "target_model_meta": dim_meta,
            "usage_summary": usage_summary,
            "sample_index": abs_idx,
            "type": item.get("type", ""),
            "elapsed_seconds": elapsed,
        }
        answers.append(answer_record)

        trace_record = {
            "uid": uid,
            "result": result,
            "call_records": call_records,
        }
        traces.append(trace_record)

        # Per-sample incremental write — answers only (traces written at end)
        answers_path.write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")

    traces_path.write_text(json.dumps(traces, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nDone. Answers: {answers_path}")
    print(f"Traces: {traces_path}")
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
