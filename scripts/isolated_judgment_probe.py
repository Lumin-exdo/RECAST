#!/usr/bin/env python3
"""
Take the EXACT (trigger_statement, target_item_content) pairs that the pipeline
already judged as no_conflict/weak in production, and re-ask DeepSeek the SAME
ABDUCTIVE_JUDGMENT_PROMPT in ISOLATION — one candidate, no other 40 distractors,
no thinking disabled (matches --no-thinking production setting) — to see whether
the original verdict was a context-pollution artifact (too many candidates
crowding the call) or a genuine, reproducible model judgment.

Run from /mnt/laq:
  python -m RECAST.scripts.isolated_judgment_probe
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from RECAST.run_new_mem import load_env_file, get_env, DEFAULT_ENV_FILE  # noqa: E402
from RECAST.llm_layer.client import LLMClient  # noqa: E402
from RECAST.prompt_lib.new_templates import ABDUCTIVE_JUDGMENT_PROMPT  # noqa: E402

# (abs_idx, trigger_statement, target_item_id, target_content, hypotheses_used)
CASES = [
    (11, "the user has a small office with hardwood floor", "m_00023",
     "the user can hear a clock ticking across the hall", []),
    (62, "most windows face a parking lot", "m_00050",
     "knows most neighbors by name", []),
    (70, "setting up automatic transfers into a brokerage account", "m_00065",
     "still paying off the auto loan from when I bought my car", []),
    (77, "has been thinking about getting more serious with French lately", "m_00044",
     "not confident enough to hold a conversation in French", []),
    (140, "uses both Windows and iPhone", "m_00056",
     "keeps files stored locally on my device rather than using online cloud services", []),
    (148, "been getting back into long walks lately", "m_00028",
     "has arthritis that acts up if they stand too long", []),
]


def run_one(llm: LLMClient, statement: str, target_item_id: str, target_content: str):
    candidates_text = f"[{target_item_id}] {target_content}"
    prompt = (
        ABDUCTIVE_JUDGMENT_PROMPT
        .replace("{statement}", statement)
        .replace("{hypotheses}", "- (none provided — isolated probe)")
        .replace("{candidates}", candidates_text)
    )
    text = llm.call_text(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Run abductive judgment."},
        ],
        extra_meta={"phase": "isolated_probe"},
    )
    return text


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
    for abs_idx, statement, item_id, content, _ in CASES:
        print(f"\n=== abs_idx={abs_idx} ===")
        print(f"statement: {statement!r}")
        print(f"candidate [{item_id}]: {content!r}")
        raw = run_one(llm, statement, item_id, content)
        print("RAW RESPONSE:")
        print(raw)
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"_parse_error": True, "raw": raw}
        results.append({
            "abs_idx": abs_idx,
            "statement": statement,
            "item_id": item_id,
            "target_content": content,
            "isolated_result": parsed,
        })

    out_path = _REPO / "RECAST" / "analysis_output" / "isolated_judgment_probe_results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
