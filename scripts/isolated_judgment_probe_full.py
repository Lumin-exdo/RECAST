#!/usr/bin/env python3
"""
Re-ask DeepSeek the EXACT original abductive_judgment call — same statement,
same real hypotheses, same FULL candidate list (all ~20-60 distractors, not
just the one target) — reconstructed from the trace, to test whether the
original no_conflict verdict reproduces under a fresh (not cached) call with
the full clutter, confirming or refuting the "too many candidates dilutes
judgment" theory from the isolated single-candidate probe.

Run from /mnt/laq:
  python -m RECAST.scripts.isolated_judgment_probe_full
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from RECAST.scripts.replay_from_trace import find_trace  # noqa: E402
from RECAST.run_new_mem import load_env_file, get_env, DEFAULT_ENV_FILE  # noqa: E402
from RECAST.llm_layer.client import LLMClient  # noqa: E402
from RECAST.prompt_lib.new_templates import ABDUCTIVE_JUDGMENT_PROMPT  # noqa: E402

RUNS_ROOT = _REPO / "RECAST" / "runs"

# (abs_idx, session_index, trigger_statement_text, target_item_id)
CASES = [
    (148, 36, "been getting back into long walks lately", "m_00028"),
    (11, 4, "the user has a small office with hardwood floor", "m_00023"),
    (62, 34, "most windows face a parking lot", "m_00050"),
]


def build_id_to_content(trace) -> dict:
    out = {}
    for slog in trace["result"]["session_logs"]:
        for stmt in slog.get("statement_log", []):
            nid = stmt.get("new_item_id")
            if nid:
                out[nid] = stmt.get("statement")
    return out


def find_statement_entry(trace, session_index, trigger_text):
    for slog in trace["result"]["session_logs"]:
        if slog.get("session_index") != session_index:
            continue
        for stmt in slog.get("statement_log", []):
            if stmt.get("statement") == trigger_text:
                return stmt
    return None


def main():
    load_env_file(DEFAULT_ENV_FILE)
    model = get_env("TARGET_MODEL")
    api_key = get_env("OPENAI_API_KEY", "DEEPSEEK_API_KEY")
    base_url = get_env("OPENAI_BASE_URL") or "https://openrouter.ai/api/v1"
    llm = LLMClient(
        model=model,
        api_key=api_key,
        base_url=base_url,
        default_extra_request_kwargs={"extra_body": {"thinking": {"type": "disabled"}}},
    )

    results = []
    for abs_idx, session_index, trigger_text, target_item_id in CASES:
        trace_path = find_trace(abs_idx, RUNS_ROOT)
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        id_to_content = build_id_to_content(trace)
        stmt_entry = find_statement_entry(trace, session_index, trigger_text)
        if stmt_entry is None:
            print(f"abs_idx={abs_idx}: could not relocate statement entry, skipping")
            continue

        hypotheses = stmt_entry.get("hypotheses", [])
        candidate_ids = stmt_entry.get("candidate_ids", [])
        candidates_text = "\n".join(
            f"[{cid}] {id_to_content.get(cid, '(content not found)')}" for cid in candidate_ids
        )
        hypotheses_text = "\n".join(f"- {h}" for h in hypotheses) if hypotheses else "- (none)"

        prompt = (
            ABDUCTIVE_JUDGMENT_PROMPT
            .replace("{statement}", trigger_text)
            .replace("{hypotheses}", hypotheses_text)
            .replace("{candidates}", candidates_text)
        )

        print(f"\n=== abs_idx={abs_idx} — FULL reconstructed call ({len(candidate_ids)} candidates) ===")
        raw = llm.call_text(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": "Run abductive judgment."},
            ],
            extra_meta={"phase": "isolated_probe_full"},
        )
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"_parse_error": True, "raw": raw}

        # extract just the judgment for our target item from the full response
        target_judgment = None
        for j in parsed.get("judgments", []) if isinstance(parsed, dict) else []:
            if j.get("target_item_id") == target_item_id:
                target_judgment = j
                break

        print(f"target_item_id={target_item_id} verdict this fresh call:")
        print(json.dumps(target_judgment, indent=2) if target_judgment else "  (no judgment returned for this item — implicitly no_conflict / skipped)")

        results.append({
            "abs_idx": abs_idx,
            "trigger_statement": trigger_text,
            "target_item_id": target_item_id,
            "n_candidates": len(candidate_ids),
            "fresh_full_call_target_judgment": target_judgment,
            "full_response": parsed,
        })

    out_path = _REPO / "RECAST" / "analysis_output" / "isolated_judgment_probe_full_results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
