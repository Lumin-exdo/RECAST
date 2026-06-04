"""
Standalone test: replay answer_generation for 4 failing cases using the NEW prompt.

For each case we reconstruct exactly the inputs that _generate_answer() would receive
(extracted from the trace's premise_check output + the original answer_gen system prompt),
then build the new prompt and call the LLM directly.

Usage:
    cd /mnt/laq
    python -m RECAST.analysis_output.test_answer_gen_fix
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# LLM call helper (reuse openai SDK, same endpoint as the runs)
# ---------------------------------------------------------------------------
from openai import OpenAI

DOTENV = Path(__file__).parent.parent / ".env"
_env: Dict[str, str] = {}
if DOTENV.exists():
    for line in DOTENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            _env[k.strip()] = v.strip()

API_KEY = _env.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
BASE_URL = _env.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL = "deepseek-v4-flash"

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)


def call_llm(system_prompt: str) -> str:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Generate answer."},
        ],
        temperature=0,
    )
    return resp.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# New ANSWER_GENERATION_PROMPT (copied from prompt_lib/new_templates.py)
# Keep in sync manually when the template changes.
# ---------------------------------------------------------------------------
ANSWER_GENERATION_PROMPT = """Answer the user's question using available memory about them.
{correction_header}
User question: {query_text}

User profile summary (authoritative overview — use for disambiguation when facts conflict):
{profile_summary}

Relevant current facts (confirmed active memories):
{active_facts}

Relevant uncertain facts (may be outdated):
{uncertain_facts}

Outdated information to be aware of (stale memories):
{stale_facts}

Premise assessment:
- Premise is safe: {premise_safe}
- Correction needed: {correction}

Output JSON only:
{{
  "answer": "your response to the user's question"
}}

Rules:
- If premise_safe=False:
  * Treat the premise as definitively FALSE, not "possibly outdated." The correction describes
    the user's CURRENT reality.
  * START the answer by clearly stating the correction.
  * Every specific recommendation — plans, schedules, counts, amounts, names, behaviors — must
    fit the corrected state. Do NOT suggest anything that only makes sense under the old state.
  * If the query explicitly asks for advice built on the outdated premise (e.g., "since you
    always do X, help me with Y"), redirect: give advice for the corrected reality, not for X.
  * Numbers and quantities stated in the correction override conflicting values from active
    memories on the same topic.
  * If the correction or the profile summary describes a constraint, condition, or obligation as
    resolved, cleared, or discharged (medical clearance, debt paid off, legal matter closed,
    restriction lifted), treat this as complete resolution. Do not add residual caution or
    reduced capacity for a condition that has been fully resolved.
- If premise_safe=True: Answer directly using active facts.
- Prioritize active facts over uncertain facts; never present stale information as currently true.
- Disambiguation: when active facts conflict on the same dimension, more recent session memories
  take precedence; use profile summary as tiebreaker only.
- If we lack sufficient information to answer well, say so honestly and ask a clarifying question.
- Keep the answer conversational and natural; be specific and practical using actual facts.
"""


# ---------------------------------------------------------------------------
# Helpers to build the new prompt from trace data
# ---------------------------------------------------------------------------

def build_new_prompt(
    query_text: str,
    profile_summary: str,
    premise_result: Dict[str, Any],
    active_items_text: List[str],      # raw content strings from original trace
    uncertain_items_text: List[str],
    stale_items_text: List[str],
) -> str:
    premise_safe = bool(premise_result.get("premise_safe", True))
    correction = str(premise_result.get("correction", "")).strip()

    usable_active = premise_result.get("usable_active_facts", [])
    if usable_active:
        active_facts_text = "\n".join(f"- {f}" for f in usable_active)
    elif premise_safe:
        active_facts_text = "\n".join(f"- {t}" for t in active_items_text) or "(none)"
    else:
        active_facts_text = "(no unambiguously current facts identified — see correction above)"

    uncertain_facts_text = "\n".join(f"- {t} (uncertain)" for t in uncertain_items_text) or "(none)"

    outdated = premise_result.get("outdated_facts", [])
    if outdated:
        stale_facts_text = "\n".join(f"- {f}" for f in outdated)
    else:
        stale_facts_text = "\n".join(f"- {t} (was true, now outdated)" for t in stale_items_text) or "(none)"

    if not premise_safe and correction:
        correction_header = (
            "\n⚠️ CURRENT REALITY — this supersedes any conflicting memory or query assumption shown below:\n"
            f"{correction}\n"
            "The query may embed an outdated assumption. Redirect your answer to fit the corrected "
            "state above — do not answer as though the old state is still true.\n"
        )
    else:
        correction_header = ""

    return (
        ANSWER_GENERATION_PROMPT
        .replace("{correction_header}", correction_header)
        .replace("{query_text}", query_text)
        .replace("{active_facts}", active_facts_text)
        .replace("{uncertain_facts}", uncertain_facts_text)
        .replace("{stale_facts}", stale_facts_text)
        .replace("{premise_safe}", str(premise_safe))
        .replace("{correction}", correction or "none")
        .replace("{profile_summary}", profile_summary or "(no profile)")
    )


def extract_section(prompt: str, header: str, next_headers: List[str]) -> str:
    """Extract a section from the original system prompt between two known headers."""
    start = prompt.find(header)
    if start == -1:
        return ""
    start += len(header)
    end = len(prompt)
    for nh in next_headers:
        pos = prompt.find(nh, start)
        if pos != -1 and pos < end:
            end = pos
    return prompt[start:end].strip()


def parse_bullet_list(text: str) -> List[str]:
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- "):
            lines.append(line[2:].strip())
    return lines


# ---------------------------------------------------------------------------
# Test cases: (uid, trace_path, target_query_label, description)
# ---------------------------------------------------------------------------
CASES = [
    (
        "ad8b0a1f",
        "/mnt/laq/RECAST/runs/fb52cd3/baseline_s137/0042/trace.json",
        "dim2_query",
        "Spouse coordination — correction correct, answer still gives spouse sync system",
    ),
    (
        "55106a99",
        "/mnt/laq/RECAST/runs/fb52cd3/baseline_s137/0055/trace.json",
        "dim3_query",
        "Friend circle — correction OK, answer uses stale '3 people' count",
    ),
    (
        "ebae7623",
        "/mnt/laq/RECAST/runs/fb52cd3/baseline_s137/0151/trace.json",
        "dim3_query",
        "PT clearance — cleared but answer hedges with cautious ramp-up",
    ),
    (
        "d806d94c",
        "/mnt/laq/RECAST/runs/7094eb6/t1t2_v1/0274/trace.json",
        "dim3_query",
        "Student loan discharge — correction only fixes take-home pay, loan still active",
    ),
]


def run_case(uid: str, trace_path: str, query_label: str, description: str) -> None:
    print(f"\n{'='*70}")
    print(f"CASE: {uid} / {query_label}")
    print(f"DESC: {description}")
    print("="*70)

    trace = json.load(open(trace_path))
    crs = trace["call_records"]

    # Get premise_check result
    pc_records = [cr for cr in crs if cr.get("phase") == "premise_check" and cr.get("query_label") == query_label]
    if not pc_records:
        print("  [SKIP] no premise_check record found")
        return
    try:
        premise_result = json.loads(pc_records[0]["response"])
    except Exception as e:
        print(f"  [SKIP] could not parse premise_check response: {e}")
        return

    # Get original answer_gen system prompt (to extract profile_summary, query_text, item lists)
    ag_records = [cr for cr in crs if cr.get("phase") == "answer_generation" and cr.get("query_label") == query_label]
    if not ag_records:
        print("  [SKIP] no answer_gen record found")
        return
    ag = ag_records[0]
    orig_system = ag["messages"][0]["content"]
    orig_response = ag.get("response", "")

    # Parse original prompt sections
    query_text = extract_section(orig_system, "User question: ", ["\nUser profile summary"])
    profile_summary = extract_section(
        orig_system,
        "User profile summary (authoritative overview — use for disambiguation when facts conflict):\n",
        ["\nRelevant current facts"],
    )
    active_raw = extract_section(
        orig_system,
        "Relevant current facts (confirmed active memories):\n",
        ["\nRelevant uncertain facts"],
    )
    uncertain_raw = extract_section(
        orig_system,
        "Relevant uncertain facts (may be outdated):\n",
        ["\nOutdated information"],
    )
    stale_raw = extract_section(
        orig_system,
        "Outdated information to be aware of (stale memories):\n",
        ["\nPremise assessment"],
    )

    active_items = parse_bullet_list(active_raw)
    uncertain_items = parse_bullet_list(uncertain_raw)
    stale_items = parse_bullet_list(stale_raw)

    # Build new prompt
    new_prompt = build_new_prompt(
        query_text=query_text,
        profile_summary=profile_summary,
        premise_result=premise_result,
        active_items_text=active_items,
        uncertain_items_text=uncertain_items,
        stale_items_text=stale_items,
    )

    # Print what changed
    premise_safe = premise_result.get("premise_safe", True)
    correction = premise_result.get("correction", "")
    print(f"\npremise_safe: {premise_safe}")
    print(f"correction  : {correction}")
    print(f"\n--- NEW PROMPT (first 600 chars) ---")
    print(new_prompt[:600])
    print("...")

    # Call LLM
    print("\n--- CALLING LLM ---")
    new_response = call_llm(new_prompt)
    try:
        new_answer = json.loads(new_response).get("answer", new_response)
    except Exception:
        new_answer = new_response

    # Print comparison
    try:
        orig_answer = json.loads(orig_response).get("answer", orig_response)
    except Exception:
        orig_answer = orig_response

    print("\n[ORIGINAL ANSWER]")
    print(orig_answer[:600])

    print("\n[NEW ANSWER]")
    print(new_answer[:600])

    print("\n[MANUAL JUDGMENT NEEDED] ^^^")


if __name__ == "__main__":
    for args in CASES:
        run_case(*args)
    print("\nDone.")
