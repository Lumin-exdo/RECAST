from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from openai import OpenAI


ROOT = Path("/mnt/laq/RECAST")
LIGHTMEM_SRC = Path("/mnt/laq/lightmem_repo/src")
if str(LIGHTMEM_SRC) not in sys.path:
    sys.path.insert(0, str(LIGHTMEM_SRC))


class SimpleTokenizer:
    def encode(self, text: str) -> List[int]:
        return list(range(max(1, len(str(text).split()))))


class SingleSegmenter:
    buffer_len = 100_000
    tokenizer = SimpleTokenizer()

    def propose_cut(self, buffer_texts: List[str]) -> List[int]:
        return []


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key in {"OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_API_KEY", "TARGET_MODEL"}:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


def normalize_usage(usage: Any) -> Dict[str, int]:
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def add_usage(total: Dict[str, int], usage: Dict[str, int]) -> None:
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        total[key] = total.get(key, 0) + int(usage.get(key, 0) or 0)


def format_session(session: List[Dict[str, Any]], timestamp: str) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    for msg in session:
        role = str(msg.get("role", "")).strip()
        content = str(msg.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content, "time_stamp": timestamp})
    return messages


def make_config(collection_name: str, api_key: str, base_url: str, memory_model: str, qdrant_path: Path) -> Dict[str, Any]:
    return {
        "pre_compress": False,
        "topic_segment": False,
        "messages_use": "user_only",
        "metadata_generate": True,
        "text_summary": True,
        "memory_manager": {
            "model_name": "openai",
            "configs": {
                "model": memory_model,
                "api_key": api_key,
                "max_tokens": 4000,
                "openai_base_url": base_url,
            },
        },
        "extract_threshold": 0.1,
        "index_strategy": "embedding",
        "text_embedder": {
            "model_name": "huggingface",
            "configs": {
                "model": str(ROOT / "models/all-MiniLM-L6-v2"),
                "embedding_dims": 384,
                "model_kwargs": {"device": "cpu"},
            },
        },
        "retrieve_strategy": "embedding",
        "embedding_retriever": {
            "model_name": "qdrant",
            "configs": {
                "collection_name": collection_name,
                "embedding_model_dims": 384,
                "path": str(qdrant_path),
            },
        },
        "update": "offline",
    }


def call_answer(client: OpenAI, model: str, question: str, memories: List[str]) -> tuple[str, Dict[str, Any]]:
    prompt = (
        "Answer the user's question using the retrieved memories. "
        "If the question assumes an outdated state, point out the discrepancy and use the newer evidence.\n\n"
        f"Question:\n{question}\n\nRetrieved memories:\n" + "\n".join(f"- {m}" for m in memories[:20])
    )
    t0 = time.perf_counter()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=700,
    )
    elapsed = time.perf_counter() - t0
    if isinstance(response, str):
        return response.strip(), {
            "elapsed_seconds": elapsed,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "raw_response_type": "str",
        }
    text = response.choices[0].message.content or ""
    return text.strip(), {"elapsed_seconds": elapsed, "usage": normalize_usage(getattr(response, "usage", None))}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uid", default="1a85388f")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--memory-model", default="qwen/qwen3.6-flash")
    args = parser.parse_args()

    load_env(ROOT / ".env")
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    model = os.environ.get("TARGET_MODEL", "deepseek-v4-flash")
    if not api_key:
        raise RuntimeError("Missing API key")

    from lightmem.memory.lightmem import LightMemory

    data = json.loads((ROOT / "STALE/STALE/outputs/STALE_MAIN.json").read_text(encoding="utf-8"))
    record = next(item for item in data if str(item.get("uid", "")).startswith(args.uid))

    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    qdrant_path = out_dir / "qdrant"
    if qdrant_path.exists():
        shutil.rmtree(qdrant_path)

    lightmem = LightMemory.from_config(make_config(f"stale_{record['uid'][:8]}", api_key, base_url, args.memory_model, qdrant_path))
    from lightmem.factory.memory_buffer.sensory_memory import SenMemBufferManager

    lightmem.config.topic_segment = True
    lightmem.segmenter = SingleSegmenter()
    lightmem.senmem_buffer_manager = SenMemBufferManager(
        max_tokens=lightmem.segmenter.buffer_len,
        tokenizer=lightmem.segmenter.tokenizer,
    )
    total_usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    started = time.perf_counter()

    add_results: List[Any] = []
    for session, timestamp in zip(record.get("haystack_session", []), record.get("timestamps", [])):
        messages = format_session(session, timestamp)
        if not messages:
            continue
        result = lightmem.add_memory(messages=messages, force_segment=True, force_extract=True)
        add_results.append(result)

    client = OpenAI(api_key=api_key, base_url=base_url)
    responses: Dict[str, str] = {}
    meta: Dict[str, Any] = {}
    retrieval_log: Dict[str, Any] = {}
    for query_key, question in record.get("probing_queries", {}).items():
        dim = query_key.replace("_query", "")
        memories = lightmem.retrieve(str(question), limit=20)
        retrieval_log[query_key] = memories
        answer, call_meta = call_answer(client, model, str(question), memories)
        responses[f"{dim}_response"] = answer
        meta[f"{dim}_meta"] = call_meta
        add_usage(total_usage, call_meta["usage"])

    elapsed = time.perf_counter() - started
    token_stats = getattr(lightmem, "token_stats", {})
    output = {
        "uid": record["uid"],
        "sample_index": data.index(record),
        "type": record.get("type", ""),
        "method": "lightmem_stale_probe",
        "elapsed_seconds": elapsed,
        "target_model_responses": responses,
        "target_model_meta": meta,
        "retrieval_log": retrieval_log,
        "lightmem_token_stats": token_stats,
        "answer_generation_usage": total_usage,
        "add_result_count": len(add_results),
    }
    (out_dir / "answer.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
