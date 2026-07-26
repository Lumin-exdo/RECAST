#!/usr/bin/env python3
"""Audit answer-prompt fairness using existing STALE result artifacts.

This script does not call any model. It compares strict and lenient score files,
summarizes answer lengths/markers, and exports concrete cases where lenient
passes but strict fails. Those cases are the main evidence for response-format
sensitivity.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Tuple


ROOT = Path("/mnt/laq")
DATASET = ROOT / "RECAST" / "STALE" / "STALE" / "outputs" / "STALE_MAIN.json"

METHODS = {
    "CupMem": {
        "answers": {
            "T1": ROOT / "cup_mem" / "eval_answers3" / "cupmem_full_T1.json",
            "T2": ROOT / "cup_mem" / "eval_answers3" / "cupmem_full_T2.json",
        },
        "strict": {
            "T1": ROOT / "cup_mem" / "eval_answers3" / "scores_T1_rescore.json",
            "T2": ROOT / "cup_mem" / "eval_answers3" / "scores_T2_rescore.json",
        },
        "lenient": {},
        "prompt_file": ROOT / "cup_mem" / "prompt_lib" / "templates.py",
        "prompt_anchor": "ANSWER_COMPOSER_PROMPT",
    },
    "A-MEM": {
        "answers": {
            "T1": ROOT / "amem_eval" / "runs" / "amem_v026_full" / "answers_T1.json",
            "T2": ROOT / "amem_eval" / "runs" / "amem_v026_full" / "answers_T2.json",
        },
        "strict": {
            "T1": ROOT / "amem_eval" / "runs" / "amem_v026_full" / "scores_T1_strict.json",
            "T2": ROOT / "amem_eval" / "runs" / "amem_v026_full" / "scores_T2_strict.json",
        },
        "lenient": {
            "T1": ROOT / "amem_eval" / "runs" / "amem_v026_full" / "scores_T1.json",
            "T2": ROOT / "amem_eval" / "runs" / "amem_v026_full" / "scores_T2.json",
        },
        "prompt_file": ROOT / "amem_eval" / "run_amem_stale.py",
        "prompt_anchor": "ANSWER_PROMPT",
    },
    "mem-0": {
        "answers": {
            "T1": ROOT / "mem0_eval" / "runs" / "mem0_v0100_full" / "answers_T1.json",
            "T2": ROOT / "mem0_eval" / "runs" / "mem0_v0100_full" / "answers_T2.json",
        },
        "strict": {
            "T1": ROOT / "mem0_eval" / "runs" / "mem0_v0100_full" / "scores_T1_strict.json",
            "T2": ROOT / "mem0_eval" / "runs" / "mem0_v0100_full" / "scores_T2_strict.json",
        },
        "lenient": {},
        "prompt_file": ROOT / "mem0_eval" / "run_mem0_stale.py",
        "prompt_anchor": "ANSWER_PROMPT",
    },
    "Naive-RAG": {
        "answers": {
            "T1": ROOT / "naive_rag" / "runs" / "naive_rag_full" / "answers_T1.json",
            "T2": ROOT / "naive_rag" / "runs" / "naive_rag_full" / "answers_T2.json",
        },
        "strict": {
            "T1": ROOT / "naive_rag" / "runs" / "naive_rag_full" / "scores_T1_strict.json",
            "T2": ROOT / "naive_rag" / "runs" / "naive_rag_full" / "scores_T2_strict.json",
        },
        "lenient": {},
        "prompt_file": ROOT / "naive_rag" / "run_naive_rag_stale.py",
        "prompt_anchor": "ANSWER_PROMPT",
    },
}

MARKERS = (
    "because",
    "since",
    "based on",
    "according",
    "recent",
    "current",
    "new",
    "changed",
    "contradict",
    "outdated",
    "memory",
)


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def records(obj: Any) -> List[Dict[str, Any]]:
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and isinstance(obj.get("data"), list):
        return obj["data"]
    raise TypeError("expected a JSON list or object with data list")


def score_details(path: Path) -> Dict[str, Dict[str, Dict[str, Any]]]:
    if not path.exists():
        return {}
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for row in load_json(path).get("details", []):
        uid = row.get("uid")
        if not uid:
            continue
        out[uid] = {}
        ev = row.get("evaluation", {})
        for dim in ("dim1", "dim2", "dim3"):
            out[uid][dim] = ev.get(f"{dim}_eval", {})
    return out


def accuracy(path: Path, task: str) -> Dict[str, float]:
    if not path.exists():
        return {}
    acc = load_json(path).get("summary", {}).get("accuracy", {}).get(task, {})
    return {dim: round(100 * acc.get(dim, {}).get("accuracy", 0), 1) for dim in ("dim1", "dim2", "dim3", "overall")}


def answer_stats(paths: Dict[str, Path]) -> Dict[str, Any]:
    all_text: List[str] = []
    first_examples: List[Dict[str, str]] = []
    for task, path in paths.items():
        for row in records(load_json(path)):
            responses = row.get("target_model_responses", {})
            if len(first_examples) < 3:
                first_examples.append({
                    "task": task,
                    "uid": row.get("uid", ""),
                    **{k: str(v).replace("\n", " ")[:220] for k, v in responses.items()},
                })
            all_text.extend(str(v) for v in responses.values())
    lengths = [len(t.split()) for t in all_text]
    marker_hits = sum(any(m in t.lower() for m in MARKERS) for t in all_text)
    return {
        "responses": len(all_text),
        "avg_words": round(sum(lengths) / len(lengths), 1) if lengths else 0,
        "median_words": int(median(lengths)) if lengths else 0,
        "reasoning_marker_pct": round(100 * marker_hits / len(all_text), 1) if all_text else 0,
        "examples": first_examples,
    }


def prompt_excerpt(path: Path, anchor: str, width: int = 1800) -> str:
    text = path.read_text(encoding="utf-8")
    pos = text.find(anchor)
    if pos < 0:
        return text[:width]
    return text[pos:pos + width]


def find_disagreements(method: str, task: str, limit: int) -> List[Dict[str, Any]]:
    cfg = METHODS[method]
    strict_path = cfg["strict"].get(task)
    lenient_path = cfg["lenient"].get(task)
    if not strict_path or not lenient_path or not strict_path.exists() or not lenient_path.exists():
        return []
    strict = score_details(strict_path)
    lenient = score_details(lenient_path)
    answers = {r["uid"]: r for r in records(load_json(cfg["answers"][task]))}
    dataset = {r["uid"]: r for r in records(load_json(DATASET)) if r.get("type") == task}
    cases: List[Dict[str, Any]] = []
    for uid, strict_dims in strict.items():
        for dim in ("dim1", "dim2", "dim3"):
            if lenient.get(uid, {}).get(dim, {}).get("pass") and not strict_dims.get(dim, {}).get("pass"):
                response_key = f"{dim}_response"
                query_key = f"{dim}_query"
                info = dataset.get(uid, {})
                cases.append({
                    "method": method,
                    "task": task,
                    "uid": uid,
                    "dimension": dim,
                    "query": info.get("probing_queries", {}).get(query_key, ""),
                    "M_old": info.get("M_old") or info.get("old_info", ""),
                    "M_new": info.get("M_new", ""),
                    "response": answers.get(uid, {}).get("target_model_responses", {}).get(response_key, ""),
                    "lenient_reason": lenient.get(uid, {}).get(dim, {}).get("reasoning", ""),
                    "strict_reason": strict_dims.get(dim, {}).get("reasoning", ""),
                })
                if len(cases) >= limit:
                    return cases
    return cases


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="codex_fairness_audit/existing_result_audit.json")
    ap.add_argument("--case-limit", type=int, default=12)
    args = ap.parse_args()

    result: Dict[str, Any] = {"methods": {}, "disagreement_cases": []}
    for method, cfg in METHODS.items():
        result["methods"][method] = {
            "prompt_file": str(cfg["prompt_file"]),
            "prompt_excerpt": prompt_excerpt(cfg["prompt_file"], cfg["prompt_anchor"]),
            "answer_stats": answer_stats(cfg["answers"]),
            "strict_accuracy": {
                task: accuracy(path, task) for task, path in cfg["strict"].items() if path.exists()
            },
            "lenient_accuracy": {
                task: accuracy(path, task) for task, path in cfg["lenient"].items() if path.exists()
            },
        }
        for task in ("T1", "T2"):
            result["disagreement_cases"].extend(find_disagreements(method, task, args.case_limit))

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
