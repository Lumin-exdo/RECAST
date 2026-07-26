#!/usr/bin/env python3
"""Fair-attribution answer rerun for RECAST+GPT-4o-mini cross-backbone pilot.

Same methodology as run_fair_attribution_rerun.py (baseline diagnostic), but
applied to RECAST's own GPT-4o-mini traces:
- Keeps RECAST's retrieved memory context (same as what the original run used)
- Re-runs answer generation with an attribution-aware prompt that forces explicit
  evidence citation (preventing the model from anchoring on STALE annotation text)
- Scores with the same strict qwen3.6-plus judge

This separates "answer-prompt attribution failure" from genuine memory-system
capability differences between GPT-4o-mini and DeepSeek-V4-Flash.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path("/mnt/laq")
RECAST_ROOT = ROOT / "RECAST"
RUNS_ROOT = RECAST_ROOT / "runs" / "budget_plan_t15_t15"
GPT4O_RUN = RUNS_ROOT / "cross_gpt4omini_30"
ENV_FILE = RECAST_ROOT / ".env"
DATASET = RECAST_ROOT / "STALE" / "STALE" / "outputs" / "STALE_MAIN.json"

OUTPUT_DIR = RUNS_ROOT / "cross_gpt4omini_fairattr_10"

ATTRIBUTION_PROMPT = """\
Answer the user's question using only the retrieved memories below.

User question:
{query}

Retrieved memories (tagged with reliability):
<memories>
{memories}
</memories>

Work through the following before writing the final answer:

1. State the specific assumption the question makes about the user's current situation.
2. Check that assumption against the retrieved memories. Prefer [ACTIVE] memories
   as primary evidence. [STALE] memories describe what changed but their change
   reasons may be approximate — use the [ACTIVE] memory content as the ground truth
   for the new state, not the change description.
3. If the assumption is unsupported or contradicted by [ACTIVE] memories, open the
   answer by naming the discrepancy and citing the specific [ACTIVE] memory content
   that establishes the current true state.
4. Ground the rest of the answer in the [ACTIVE] memories, citing their content
   explicitly.

Output JSON only:
{{
  "assumption": "the current-state assumption made by the question, or 'none'",
  "evidence_check": "which specific [ACTIVE] memories support or contradict the assumption, quoted verbatim",
  "answer": "final natural-language answer grounded in [ACTIVE] memory content"
}}
"""

JUDGE_SYSTEM = """\
You are a strict answer evaluator for the STALE benchmark.
Evaluate whether the assistant response correctly handles a potentially stale memory.
Respond in JSON with dim1_eval, dim2_eval, dim3_eval each having {reasoning, pass}.
"""


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def get_openai_client(model: str = "openai/gpt-4o-mini") -> Any:
    from openai import OpenAI
    load_env(ENV_FILE)
    key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    return OpenAI(api_key=key, base_url=base_url), model


def extract_memories_from_trace(trace_path: Path, dim: str) -> Optional[str]:
    """Extract the Stored memories block from answer_generation_v2 system message."""
    trace = json.loads(trace_path.read_text())
    calls = trace.get("call_records", [])
    answer_calls = [c for c in calls if c.get("phase") == "answer_generation_v2"]

    dim_idx = {"dim1": 0, "dim2": 1, "dim3": 2}.get(dim, 0)
    if dim_idx >= len(answer_calls):
        return None

    call = answer_calls[dim_idx]
    for msg in call.get("messages", []):
        if msg.get("role") == "system":
            content = msg["content"]
            # Extract the Stored memories block
            m = re.search(
                r"(Stored memories \(tagged with reliability\):.*?)(?:Profile summary|Work through the following)",
                content,
                re.DOTALL,
            )
            if m:
                return m.group(1).strip()
            return content  # fallback: use full system message
    return None


def extract_query_from_trace(trace_path: Path, dim: str) -> Optional[str]:
    """Extract the query text from answer_generation_v2 system message."""
    trace = json.loads(trace_path.read_text())
    calls = trace.get("call_records", [])
    answer_calls = [c for c in calls if c.get("phase") == "answer_generation_v2"]

    dim_idx = {"dim1": 0, "dim2": 1, "dim3": 2}.get(dim, 0)
    if dim_idx >= len(answer_calls):
        return None

    call = answer_calls[dim_idx]
    for msg in call.get("messages", []):
        if msg.get("role") == "system":
            content = msg["content"]
            m = re.search(r"User question:\s*(.*?)\n\nStored memories", content, re.DOTALL)
            if m:
                return m.group(1).strip()
    return None


def call_answer(client: Any, model: str, query: str, memories: str) -> Tuple[str, float]:
    prompt = ATTRIBUTION_PROMPT.format(query=query, memories=memories)
    start = time.perf_counter()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=800,
        extra_body={"reasoning": {"enabled": False, "exclude": True}},
    )
    elapsed = time.perf_counter() - start
    text = resp.choices[0].message.content or ""
    # Parse JSON and return answer field
    try:
        payload = json.loads(text.strip())
        parts = []
        if payload.get("evidence_check"):
            parts.append(f"Evidence: {payload['evidence_check']}")
        if payload.get("answer"):
            parts.append(payload["answer"])
        return "\n".join(parts) if parts else text, elapsed
    except Exception:
        return text, elapsed


def process_uid(uid_prefix: str, abs_idx: int, client: Any, model: str) -> Optional[Dict]:
    trace_path = GPT4O_RUN / f"{abs_idx:04d}" / "trace.json"
    if not trace_path.exists():
        print(f"  SKIP {uid_prefix}: no trace at {trace_path}")
        return None

    trace = json.loads(trace_path.read_text())
    uid = trace.get("uid", "")

    responses = {}
    metas = {}
    for dim in ["dim1", "dim2", "dim3"]:
        memories = extract_memories_from_trace(trace_path, dim)
        query = extract_query_from_trace(trace_path, dim)
        if not memories or not query:
            print(f"  WARN {uid_prefix} {dim}: could not extract memories/query")
            responses[f"{dim}_response"] = "(extraction failed)"
            metas[f"{dim}_meta"] = {"elapsed_seconds": 0}
            continue

        answer, elapsed = call_answer(client, model, query, memories)
        responses[f"{dim}_response"] = answer
        metas[f"{dim}_meta"] = {"elapsed_seconds": elapsed}
        print(f"  {uid_prefix} {dim}: {elapsed:.1f}s")

    return {
        "uid": uid,
        "target_model_responses": responses,
        "target_model_meta": metas,
        "sample_index": abs_idx,
        "type": trace.get("result", {}).get("sample_meta", {}).get("conflict_type", "?"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="openai/gpt-4o-mini")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_env(ENV_FILE)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Same 10 UIDs as cross_gpt4omini_30
    uid_to_abs = {
        "1a85388f": 6,
        "7ee76c41": 29,
        "d9545076": 72,
        "89b77229": 167,
        "f6d12075": 194,
        "feef3933": 201,
        "14897e47": 240,
        "d806d94c": 274,
        "2c711459": 312,
        "c9cc370e": 382,
    }

    if args.dry_run:
        print("DRY RUN: would process UIDs:", list(uid_to_abs.keys()))
        return

    client, model = get_openai_client(args.model)
    print(f"Fair-attribution rerun: model={model}, output={OUTPUT_DIR}")

    results = []
    for uid_prefix, abs_idx in uid_to_abs.items():
        print(f"Processing {uid_prefix} (abs_idx={abs_idx})")
        result = process_uid(uid_prefix, abs_idx, client, model)
        if result:
            results.append(result)
            # Save incrementally
            out_file = OUTPUT_DIR / f"{abs_idx:04d}_fairattr.json"
            out_file.write_text(json.dumps(result, indent=2))
            print(f"  Saved: {out_file}")

    # Save all results
    answers_file = OUTPUT_DIR / "answers.json"
    answers_file.write_text(json.dumps(results, indent=2))
    print(f"\nDone. {len(results)} results saved to {answers_file}")
    print(f"Score with: python3 -m RECAST.scripts.budget_plan_cross_autoscore --answers {answers_file} --scorer qwen3.6-plus")


if __name__ == "__main__":
    main()
