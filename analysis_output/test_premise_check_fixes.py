"""
Test premise_check fixes on failing cases from baseline_s137 and t1t2_v1 runs.

Fix A (prompt): active-vs-active conflict detection rule.
Fix B (prompt): stale_reason scrutiny — indirect-inference stale_reasons are treated
                as uncertain rather than confirmed-outdated.
Fix C (code): active memories now carry session numbers; stale memories show
              created_session and stale_session for gap analysis.
Fix D (code): top_k=12 instead of 8 for premise_check retrieval.

This script replays premise_check LLM calls from recorded traces with the NEW
PREMISE_CHECK_PROMPT and new memory formats, then prints old vs new results.
Manual judgement determines whether the fix helped, hurt, or had no effect.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from RECAST.prompt_lib.new_templates import PREMISE_CHECK_PROMPT

# ── target cases ──────────────────────────────────────────────────────────────
# Key: (uid_prefix, dim_label, run_dir_name, run_subdir, sample_folder)
# Failure types:
#   "direction_reversal" — stale_reason or outdated_facts list M_new as past
#   "active_vs_active"   — two active memories mutually exclusive, premise_safe=True
#   "vague_correction"   — correction too imprecise (upstream limit, informational only)

CASES = [
    # direction_reversal: m_00117 (M_new verification condition) staled by values-inference
    ("8aeb8778", "dim3_query", "7094eb6",  "t1t2_v1",      "0058", "direction_reversal"),
    # direction_reversal: M_new "recharged when out" staled in baseline (pre-Fix A/B)
    ("0af76ce2", "dim3_query", "fb52cd3",  "baseline_s137", "0122", "direction_reversal"),
    # direction_reversal: M_new "batch-cooking" staled in baseline
    ("4bb62dbf", "dim3_query", "fb52cd3",  "baseline_s137", "0191", "direction_reversal"),
    # active_vs_active: conflicting active memories on relationship dimension (dim1)
    ("321e33b5", "dim1_query", "fb52cd3",  "baseline_s137", "0247", "active_vs_active"),
    # additional: check dim1/dim2 direction for 8aeb8778 (correction content quality)
    ("8aeb8778", "dim1_query", "7094eb6",  "t1t2_v1",      "0058", "direction_reversal"),
    ("8aeb8778", "dim2_query", "7094eb6",  "t1t2_v1",      "0058", "direction_reversal"),
]

RUNS_DIR = ROOT / "runs"


def load_trace(run: str, subdir: str, folder: str) -> Optional[Dict]:
    p = RUNS_DIR / run / subdir / folder / "trace.json"
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def extract_premise_call(trace: Dict, dim_label: str) -> Optional[Dict]:
    for c in trace.get("call_records", []):
        if c.get("phase") == "premise_check" and c.get("query_label") == dim_label:
            return c
    return None


def format_memories_new(system_prompt_old: str) -> str:
    """
    Extract memory sections from the old system prompt and reformat them
    with created_session info (simulated from content where available)
    plus the new PREMISE_CHECK_PROMPT template.

    Since we're replaying from recorded prompts, the memories are already
    formatted in the old style. We parse them out and rebuild with new format.
    """
    # The old format had these section headers:
    # "Current active memories (confirmed true):\n..."
    # "Uncertain memories (may be outdated...):\n..."
    # "Stale memories (used to be true but has changed):\n..."
    # and "User question: ...\n\n"
    import re

    query_match = re.search(r"User question: (.+?)(?:\n\nCurrent active)", system_prompt_old, re.DOTALL)
    query_text = query_match.group(1).strip() if query_match else ""

    active_match = re.search(
        r"Current active memories \(confirmed true\):\n(.+?)(?:\nUncertain memories|\nStale memories)",
        system_prompt_old, re.DOTALL
    )
    uncertain_match = re.search(
        r"Uncertain memories \(may be outdated.*?\):\n(.+?)(?:\nStale memories)",
        system_prompt_old, re.DOTALL
    )
    stale_match = re.search(
        r"Stale memories \(used to be true but has changed\):\n(.+?)(?:\nIdentify what)",
        system_prompt_old, re.DOTALL
    )

    active_raw = active_match.group(1).strip() if active_match else "(none)"
    uncertain_raw = uncertain_match.group(1).strip() if uncertain_match else "(none)"
    stale_raw = stale_match.group(1).strip() if stale_match else "(none)"

    # Reformat active: add dummy session marker "(session ?)" since we don't have it in old traces
    # Real runs will have created_session from MemoryItem; in replay we mark as (session ?)
    active_lines = []
    for line in active_raw.splitlines():
        line = line.strip()
        if line and not line.startswith("(none"):
            # Insert session placeholder after the item id bracket
            active_lines.append(re.sub(r"^(- \[\S+\])", r"\1 (session ?)", line))
        elif line:
            active_lines.append(line)
    active_new = "\n".join(active_lines) if active_lines else "(none)"

    # Reformat stale: transform old "(stale since session N: reason)" to
    # "(created session ?, staled session N: reason)"
    stale_lines = []
    for line in stale_raw.splitlines():
        line = line.strip()
        if line and not line.startswith("(none"):
            line = re.sub(
                r"\(stale since session (\d+): ",
                r"(created session ?, staled session \1: ",
                line
            )
            stale_lines.append(line)
        elif line:
            stale_lines.append(line)
    stale_new = "\n".join(stale_lines) if stale_lines else "(none)"

    new_prompt = (
        PREMISE_CHECK_PROMPT
        .replace("{query_text}", query_text)
        .replace("{active_memories}", active_new)
        .replace("{uncertain_memories}", uncertain_raw)
        .replace("{stale_memories}", stale_new)
    )
    return new_prompt, query_text


def call_llm(new_prompt: str, provider: str, model: str) -> str:
    """Re-run premise_check with the new prompt using the same provider/model."""
    if provider == "openai" or "deepseek" in model.lower():
        import openai
        client = openai.OpenAI(
            api_key=os.environ.get("DEEPSEEK_API_KEY", os.environ.get("OPENAI_API_KEY", "")),
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": new_prompt},
                {"role": "user", "content": "Check premise."},
            ],
            temperature=0,
        )
        return resp.choices[0].message.content
    raise ValueError(f"Unsupported provider: {provider}")


def parse_json_response(raw: str) -> Dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except Exception:
        return {"_parse_error": raw[:200]}


def judge_direction_reversal(old_result: Dict, new_result: Dict, case_notes: str) -> str:
    """
    For direction_reversal cases: check if outdated_facts still include M_new memories.
    Manual judgement needed — this prints both for comparison.
    """
    old_outdated = old_result.get("outdated_facts", [])
    new_outdated = new_result.get("outdated_facts", [])
    old_safe = old_result.get("premise_safe")
    new_safe = new_result.get("premise_safe")
    old_corr = old_result.get("correction", "")
    new_corr = new_result.get("correction", "")

    lines = []
    lines.append(f"  premise_safe: {old_safe} → {new_safe}")
    lines.append(f"  correction OLD: {old_corr[:120]}")
    lines.append(f"  correction NEW: {new_corr[:120]}")
    lines.append(f"  outdated_facts OLD ({len(old_outdated)}):")
    for f in old_outdated:
        lines.append(f"    - {str(f)[:120]}")
    lines.append(f"  outdated_facts NEW ({len(new_outdated)}):")
    for f in new_outdated:
        lines.append(f"    - {str(f)[:120]}")
    return "\n".join(lines)


def judge_active_vs_active(old_result: Dict, new_result: Dict) -> str:
    old_safe = old_result.get("premise_safe")
    new_safe = new_result.get("premise_safe")
    old_corr = old_result.get("correction", "")
    new_corr = new_result.get("correction", "")

    lines = []
    lines.append(f"  premise_safe: {old_safe} → {new_safe}")
    lines.append(f"  correction OLD: {old_corr[:160]}")
    lines.append(f"  correction NEW: {new_corr[:160]}")
    lines.append(f"  presuppositions NEW: {new_result.get('presuppositions', [])}")
    return "\n".join(lines)


def main():
    print("=" * 70)
    print("PREMISE_CHECK FIX REPLAY TEST")
    print("  Fix A: active-vs-active detection rule")
    print("  Fix B: stale_reason scrutiny rule + session gap signal")
    print("  Fix C: session numbers in active/stale memory format")
    print("  Fix D: top_k=12 for retrieval (not testable from old traces)")
    print("=" * 70)

    results_summary = []

    for uid_prefix, dim, run, subdir, folder, failure_type in CASES:
        print(f"\n{'─'*60}")
        print(f"UID: {uid_prefix}  DIM: {dim}  TYPE: {failure_type}")
        print(f"Run: {run}/{subdir}/{folder}")

        trace = load_trace(run, subdir, folder)
        if trace is None:
            print("  SKIP: trace file not found")
            continue

        call = extract_premise_call(trace, dim)
        if call is None:
            print(f"  SKIP: no premise_check call for {dim}")
            continue

        msgs = call.get("messages", [])
        if not msgs:
            print("  SKIP: no messages in call record")
            continue

        old_system_prompt = msgs[0].get("content", "")
        old_response_raw = call.get("response", "")
        old_result = parse_json_response(old_response_raw) if old_response_raw else {}

        provider = call.get("provider", "openai")
        model = call.get("model", "deepseek-chat")

        # Build new prompt
        try:
            new_prompt, query_text = format_memories_new(old_system_prompt)
        except Exception as e:
            print(f"  SKIP: prompt parsing error: {e}")
            continue

        print(f"  Query: {query_text[:100]}")

        # Call LLM with new prompt
        try:
            new_response_raw = call_llm(new_prompt, provider, model)
            new_result = parse_json_response(new_response_raw)
        except Exception as e:
            print(f"  LLM ERROR: {e}")
            new_result = {"_error": str(e)}

        # Print comparison
        if failure_type == "direction_reversal":
            comparison = judge_direction_reversal(old_result, new_result, f"{uid_prefix}:{dim}")
        else:
            comparison = judge_active_vs_active(old_result, new_result)

        print(comparison)

        results_summary.append({
            "uid": uid_prefix,
            "dim": dim,
            "type": failure_type,
            "old_safe": old_result.get("premise_safe"),
            "new_safe": new_result.get("premise_safe"),
            "old_correction": old_result.get("correction", "")[:80],
            "new_correction": new_result.get("correction", "")[:80],
            "old_outdated_count": len(old_result.get("outdated_facts", [])),
            "new_outdated_count": len(new_result.get("outdated_facts", [])),
        })

    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    for r in results_summary:
        safe_change = f"{r['old_safe']} → {r['new_safe']}"
        print(f"{r['uid']} {r['dim']:12s} ({r['type']:20s}): premise_safe {safe_change}")


if __name__ == "__main__":
    main()
