#!/usr/bin/env python3
"""Fair-attribution answer rerun for STALE baselines.

This script reruns answer generation with a shared attribution-aware prompt while
preserving each baseline's own retrieval/memory context. It does not reveal
ground-truth M_old/M_new to the answer model.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from RECAST.paths import DATA_ROOT, EMBEDDING_MODEL_ROOT, RECAST_ROOT, REPO_ROOT

ROOT = REPO_ROOT
RECAST = RECAST_ROOT
DATASET = DATA_ROOT / "outputs" / "STALE_MAIN.json"
ENV_FILE = RECAST_ROOT / ".env"
EMBED_PATH = EMBEDDING_MODEL_ROOT

DEFAULT_T1_UIDS = [
    "89b77229", "7ee76c41", "1a85388f", "f6d12075", "d9545076",
    "e229c5cd", "eacb64ff", "fdada4cc", "a4b2e2fd", "2006d545",
]
DEFAULT_T2_UIDS = [
    "d806d94c", "feef3933", "14897e47", "c9cc370e", "2c711459",
    "993152aa", "c03f7b53", "60604200", "06071a3e", "2d92d1c2",
]

_EMBEDDER: Any = None

ATTRIBUTION_PROMPT = """\
Answer the user's question using only this baseline system's retrieved memories
and the most recent conversation context.

User question:
{query}

Retrieved memories from this baseline system:
<memories>
{memories}
</memories>

Recent conversation context:
<recent_context>
{recent_context}
</recent_context>

Work through the following before writing the final answer:

1. State the specific assumption the question makes about the user's current
   situation. Include implicit assumptions.
2. Check that assumption against the retrieved memories and recent context.
   Do not assume the question's premise is true. Retrieved memories may include
   old, current, or contradictory user states, so prefer the most concrete and
   recent evidence available in the provided context.
3. If the assumption is unsupported, outdated, or contradicted, open the answer
   by naming that discrepancy. Do not give advice, a plan, a comparison, or a
   template that only makes sense if the old premise is still true.
4. Ground the rest of the answer in the evidence you can cite from the retrieved
   memories or recent context. If the evidence is insufficient, say the current
   state is unknown rather than inventing one.

Output JSON only:
{{
  "assumption": "the current-state assumption made by the question, or 'none'",
  "evidence_check": "which retrieved/recent facts support, contradict, or fail to support that assumption",
  "answer": "final natural-language answer"
}}
"""


def load_env(path: Path, override: bool = True) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if override:
            os.environ[key.strip()] = value.strip().strip("'\"")
        else:
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def format_session(session: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for turn in session:
        role = "User" if turn.get("role") == "user" else "Assistant"
        lines.append(f"{role}: {turn.get('content', '')}")
    return "\n".join(lines)


def load_records() -> List[Dict[str, Any]]:
    return json.loads(DATASET.read_text(encoding="utf-8"))


def select_records(records: List[Dict[str, Any]], t1_uids: List[str], t2_uids: List[str]) -> List[Tuple[Dict[str, Any], int]]:
    prefixes = [(u, "T1") for u in t1_uids] + [(u, "T2") for u in t2_uids]
    selected: List[Tuple[Dict[str, Any], int]] = []
    used = set()
    for prefix, task_type in prefixes:
        for idx, rec in enumerate(records):
            uid = str(rec.get("uid", ""))
            if uid.startswith(prefix) and rec.get("type") == task_type and uid not in used:
                selected.append((rec, idx))
                used.add(uid)
                break
        else:
            raise ValueError(f"Could not find {task_type} UID prefix {prefix}")
    return selected


def runtime() -> Tuple[OpenAI, str, Dict[str, Any]]:
    # Preserve explicit launch-time overrides for controlled backbone smoke tests.
    load_env(ENV_FILE, override=False)
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    model = os.environ.get("TARGET_MODEL", "deepseek-v4-flash")
    if not key:
        raise RuntimeError("Missing DEEPSEEK_API_KEY or OPENAI_API_KEY")
    extra: Dict[str, Any] = {}
    if "qwen" in model.lower():
        extra = {"extra_body": {"enable_thinking": False}}
    elif "openrouter.ai" in base_url.lower():
        extra = {"extra_body": {"reasoning": {"enabled": False, "exclude": True}}}
    else:
        extra = {"extra_body": {"thinking": {"type": "disabled"}}}
    return OpenAI(api_key=key, base_url=base_url), model, extra


def call_answer(client: OpenAI, model: str, extra: Dict[str, Any], query: str, memories: str, recent_context: str) -> Tuple[str, Dict[str, Any]]:
    prompt = ATTRIBUTION_PROMPT.format(
        memories=memories or "(no relevant memories found)",
        recent_context=recent_context or "(none)",
        query=query,
    )
    start = time.perf_counter()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=700,
        **extra,
    )
    elapsed = time.perf_counter() - start

    def make_visible_answer(text: str) -> str:
        visible_text = text.strip()
        try:
            payload = json.loads(visible_text)
            assumption = str(payload.get("assumption", "")).strip()
            evidence_check = str(payload.get("evidence_check", "")).strip()
            answer = str(payload.get("answer", "")).strip()
            parts = []
            if assumption:
                parts.append(f"Assumption check: {assumption}")
            if evidence_check:
                parts.append(f"Evidence check: {evidence_check}")
            if answer:
                parts.append(f"Answer: {answer}")
            visible_text = "\n".join(parts) or visible_text
        except Exception:
            pass
        return visible_text

    if isinstance(response, str):
        return make_visible_answer(response), {
            "elapsed_seconds": elapsed,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "raw_response_type": "str",
        }
    if isinstance(response, dict):
        choices = response.get("choices", [])
        message = choices[0].get("message", {}) if choices else {}
        text = str(message.get("content") or "").strip()
        usage_obj = response.get("usage") or {}
        return make_visible_answer(text), {
            "elapsed_seconds": elapsed,
            "usage": {
                "prompt_tokens": int(usage_obj.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage_obj.get("completion_tokens", 0) or 0),
                "total_tokens": int(usage_obj.get("total_tokens", 0) or 0),
            },
            "raw_response_type": "dict",
        }

    usage = getattr(response, "usage", None)
    usage_dict = {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }
    text = (response.choices[0].message.content or "").strip()
    return make_visible_answer(text), {"elapsed_seconds": elapsed, "usage": usage_dict}


def cosine_similarity(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    query_norm = query / (np.linalg.norm(query) + 1e-10)
    matrix_norm = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-10)
    return matrix_norm @ query_norm


def retrieved_by_naive(record: Dict[str, Any], queries: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, Any], str]:
    from sentence_transformers import SentenceTransformer
    global _EMBEDDER

    sessions = record.get("haystack_session", [])
    session_texts = [format_session(s) for s in sessions]
    recent_context = session_texts[-1] if session_texts else ""
    if _EMBEDDER is None:
        _EMBEDDER = SentenceTransformer(str(EMBED_PATH))
    embeddings = _EMBEDDER.encode(session_texts, convert_to_numpy=True)
    contexts: Dict[str, str] = {}
    retrieval_log: Dict[str, Any] = {}
    for label, query in queries.items():
        query_vec = _EMBEDDER.encode([str(query)], convert_to_numpy=True)[0]
        sims = cosine_similarity(query_vec, embeddings)
        top_idx = np.argsort(sims)[::-1][:10]
        contexts[label] = "\n\n---\n\n".join(
            f"[Session {int(i) + 1} | sim={float(sims[i]):.4f}]\n{session_texts[int(i)]}"
            for i in top_idx
        )
        retrieval_log[label] = [{"session_index": int(i), "similarity": float(sims[i])} for i in top_idx]
    return contexts, retrieval_log, recent_context


def retrieved_by_amem(record: Dict[str, Any], queries: Dict[str, str], abs_idx: int) -> Tuple[Dict[str, str], Dict[str, Any], str]:
    sys.path.insert(0, str(ROOT))
    from agentic_memory.memory_system import AgenticMemorySystem

    sessions = record.get("haystack_session", [])
    recent_context = format_session(sessions[-1]) if sessions else ""
    chroma_tmp = tempfile.mkdtemp(prefix=f"fair_amem_{abs_idx}_")
    try:
        _client, model, _extra = runtime()
        os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
        os.environ["OPENAI_BASE_URL"] = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
        mem = AgenticMemorySystem(
            model_name=str(EMBED_PATH),
            llm_backend="openai",
            llm_model=model,
            storage_path=chroma_tmp,
            evo_threshold=9999,
        )
        fast_ingest = os.environ.get("AMEM_FAST_INGEST", "").strip() == "1"
        if fast_ingest:
            mem.process_memory = lambda note: (False, note)  # type: ignore[method-assign]
        for session in sessions:
            text = format_session(session)
            if text.strip():
                if fast_ingest:
                    mem.add_note(text, keywords=["session"], context="Session", tags=["session"])
                else:
                    mem.add_note(text)
        contexts: Dict[str, str] = {}
        retrieval_log: Dict[str, Any] = {}
        for label, query in queries.items():
            results = mem.search_agentic(str(query), k=10)
            resolved_results = []
            rendered = []
            for result in results:
                row = dict(result)
                memory_id = row.get("id")
                note = mem.read(memory_id) if memory_id else None
                content = note.content if note else ""
                row["content"] = content
                resolved_results.append(row)
                if content:
                    rendered.append(f"- {content}")
            contexts[label] = "\n".join(rendered)
            retrieval_log[label] = resolved_results
        return contexts, retrieval_log, recent_context
    finally:
        shutil.rmtree(chroma_tmp, ignore_errors=True)


def retrieved_by_mem0(record: Dict[str, Any], queries: Dict[str, str], abs_idx: int) -> Tuple[Dict[str, str], Dict[str, Any], str]:
    sys.path.insert(0, str(ROOT))
    from mem0 import Memory
    from mem0_eval.run_mem0_stale import build_messages_from_sessions, make_mem0_config

    sessions = record.get("haystack_session", [])
    recent_context = format_session(sessions[-1]) if sessions else ""
    _client, model, _extra = runtime()
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    qdrant_tmp = tempfile.mkdtemp(prefix=f"fair_mem0_{abs_idx}_")
    try:
        mem = Memory.from_config(make_mem0_config(api_key, base_url, model, str(EMBED_PATH), True, qdrant_tmp))
        user_id = f"fair_user_{abs_idx}"
        batch_size = max(1, int(os.environ.get("MEM0_FAIR_BATCH_SESSIONS", "8")))
        for start in range(0, len(sessions), batch_size):
            batch = sessions[start : start + batch_size]
            try:
                mem.add(build_messages_from_sessions(batch), user_id=user_id)
            except Exception as exc:
                if batch_size == 1 or "Prompt tokens limit exceeded" not in str(exc):
                    raise
                for session in batch:
                    mem.add(build_messages_from_sessions([session]), user_id=user_id)
        contexts: Dict[str, str] = {}
        retrieval_log: Dict[str, Any] = {}
        for label, query in queries.items():
            results = mem.search(str(query), user_id=user_id, limit=10)
            entries = results.get("results", []) if isinstance(results, dict) else results
            if not isinstance(entries, list):
                entries = []
            contexts[label] = "\n".join(f"- {e.get('memory', e.get('text', str(e)))}" for e in entries)
            retrieval_log[label] = entries
        return contexts, retrieval_log, recent_context
    finally:
        shutil.rmtree(qdrant_tmp, ignore_errors=True)


def run_one(method: str, record: Dict[str, Any], abs_idx: int, out_dir: Path) -> Dict[str, Any]:
    uid = str(record.get("uid", abs_idx))
    sample_dir = out_dir / f"{abs_idx:04d}_{uid[:8]}"
    answer_path = sample_dir / "answer.json"
    if answer_path.exists():
        return json.loads(answer_path.read_text(encoding="utf-8"))

    queries = record.get("probing_queries", {})
    if method == "naive_rag":
        contexts, retrieval_log, recent_context = retrieved_by_naive(record, queries)
    elif method == "amem":
        contexts, retrieval_log, recent_context = retrieved_by_amem(record, queries, abs_idx)
    elif method == "mem0":
        contexts, retrieval_log, recent_context = retrieved_by_mem0(record, queries, abs_idx)
    else:
        raise ValueError(f"Unsupported method: {method}")

    client, model, extra = runtime()
    responses: Dict[str, str] = {}
    meta: Dict[str, Any] = {}
    for query_key, query in queries.items():
        dim = query_key.replace("_query", "")
        answer, call_meta = call_answer(client, model, extra, str(query), contexts.get(query_key, ""), recent_context)
        responses[f"{dim}_response"] = answer
        meta[f"{dim}_meta"] = call_meta

    answer_obj = {
        "uid": uid,
        "sample_index": abs_idx,
        "type": record.get("type", ""),
        "method": f"{method}_fair_attribution",
        "target_model_responses": {
            "dim1_response": responses.get("dim1_response", ""),
            "dim2_response": responses.get("dim2_response", ""),
            "dim3_response": responses.get("dim3_response", ""),
        },
        "target_model_meta": meta,
        "retrieval_log": retrieval_log,
    }
    sample_dir.mkdir(parents=True, exist_ok=True)
    answer_path.write_text(json.dumps(answer_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return answer_obj


def write_split_answers(out_dir: Path, answers: List[Dict[str, Any]]) -> None:
    for task_type in ("T1", "T2"):
        rows = [a for a in answers if a.get("type") == task_type]
        (out_dir / f"answers_{task_type}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--method", choices=["naive_rag", "amem", "mem0"], required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--t1-uids", default=",".join(DEFAULT_T1_UIDS))
    parser.add_argument("--t2-uids", default=",".join(DEFAULT_T2_UIDS))
    parser.add_argument("--workers", type=int, default=1)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    load_env(ENV_FILE, override=False)

    records = load_records()
    selected = select_records(
        records,
        [x.strip() for x in args.t1_uids.split(",") if x.strip()],
        [x.strip() for x in args.t2_uids.split(",") if x.strip()],
    )

    print(f"fair-attribution rerun: method={args.method} samples={len(selected)} out={out_dir}")
    print(f"model={os.environ.get('TARGET_MODEL')} base_url={os.environ.get('OPENAI_BASE_URL')}")

    answers: List[Dict[str, Any]] = []
    if args.workers <= 1:
        for rec, idx in selected:
            print(f"[start] {idx:04d} {rec.get('uid')} {rec.get('type')}", flush=True)
            answers.append(run_one(args.method, rec, idx, out_dir))
            write_split_answers(out_dir, answers)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(run_one, args.method, rec, idx, out_dir): (rec, idx) for rec, idx in selected}
            for fut in as_completed(futures):
                rec, idx = futures[fut]
                print(f"[done] {idx:04d} {rec.get('uid')}", flush=True)
                answers.append(fut.result())
                write_split_answers(out_dir, answers)

    answers.sort(key=lambda row: row.get("sample_index", 0))
    write_split_answers(out_dir, answers)
    (out_dir / "run_config.json").write_text(
        json.dumps(
            {
                "method": args.method,
                "model": os.environ.get("TARGET_MODEL"),
                "base_url": os.environ.get("OPENAI_BASE_URL"),
                "sample_count": len(answers),
                "t1_uids": args.t1_uids,
                "t2_uids": args.t2_uids,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out_dir / 'answers_T1.json'} and answers_T2.json")


if __name__ == "__main__":
    main()
