#!/usr/bin/env python3
"""
Experiment: Two-section anchor design for current_state memories.

Tests whether separating current_state items into a dedicated PRIORITY section
(vs. mixing all into one MANDATORY list as in Change A) improves hypothesis
quality for the M_old memory that should be detected as stale.

Variants tested per statement:
  BASELINE: persistent anchors only (lasting_preference + biographical)
  NEW:      persistent anchors + top-8 current_state in separate PRIORITY section
"""

import json
import os
import time
from openai import OpenAI

client = OpenAI(
    api_key="${DEEPSEEK_API_KEY}",
    base_url="https://openrouter.ai/api/v1"
)
MODEL = "deepseek-v4-flash"

INPUT_FILE = "/tmp/exp_inputs_v2.json"
OUTPUT_FILE = "/mnt/laq/RECAST/analysis_output/exp_cs_anchor_results.json"


def call_llm(system_prompt, user_message):
    for attempt in range(4):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.0,
            )
            return resp.choices[0].message.content
        except Exception as e:
            if attempt < 3:
                wait = 2 ** attempt
                print(f"  API error attempt {attempt+1}: {e}, retrying in {wait}s")
                time.sleep(wait)
            else:
                return f"ERROR: {e}"


# ─── BASELINE prompt (persistent anchors only, same as e2ev3 baseline) ─────────

BASELINE_PROMPT = """You are a detective reconstructing everything that has changed in this user's life.

New statement: {statement}

Current user profile summary (compressed snapshot — may omit some stored facts):
{global_impression}

Stored memory facts about the user (direct from memory store — complete list):
{preference_anchors}

Work through two internal steps, then output your hypotheses.

STEP 1 — DERIVE INTERMEDIATE STATES (do not output this reasoning):
What does this statement NECESSARILY imply about the user's current reality?
Think across five dimensions:

TEMPORAL: What time windows are now blocked, required, or shifted?
PHYSICAL / SPATIAL: What locations, tools, or objects are now inaccessible or obsolete?
ECONOMIC: What financial commitments, costs, or resources have changed?
ENABLING CONTEXT: What background conditions that other habits depend on have changed?
SOCIAL: What relationships, shared activities, or social patterns have changed?

STEP 2 — CROSS-REFERENCE (do not output this reasoning):
Part A — Profile cross-reference:
  For EACH detail in the profile summary, ask:
  "Does any intermediate state from Step 1 conflict with this detail — even indirectly?"

Part B — Preference anchor cross-reference (MANDATORY):
  For EACH item listed under "Stored memory facts" above, ask:
  "Does any intermediate state from Step 1 conflict with this stored trait — even via a 2-3 step chain?"
  Generate a hypothesis for each anchor where a plausible 2-3 step connection exists.
  Skip anchors only when no reasonable chain can be constructed — do not force implausible links.

Be aggressive in both parts. A tenuous connection is worth surfacing; the next step will verify it.

STEP 3 — OUTPUT your hypotheses.

Output JSON only:
{{
  "hypothetical_impacts": [
    "short description of what used to be true about the user but might now be outdated"
  ]
}}

Rules:
- Generate 6-12 hypotheses. Err on the side of more.
- Each hypothesis must describe a PAST OR CURRENT BELIEF that might now be wrong.
- Prioritize hypotheses that target SPECIFIC items from the anchor list or profile summary.
- Include hypotheses from at least two different dimensions.
"""

# ─── NEW prompt (two sections: persistent MANDATORY + current_state PRIORITY) ──

NEW_PROMPT = """You are a detective reconstructing everything that has changed in this user's life.

New statement: {statement}

Current user profile summary (compressed snapshot — may omit some stored facts):
{global_impression}

Stored stable traits (persistent — rarely change; these form your reference frame):
{persistent_anchors}

Stored current states to verify (temporary, mutable conditions — check each one actively):
{current_state_anchors}

Work through two internal steps, then output your hypotheses.

STEP 1 — DERIVE INTERMEDIATE STATES (do not output this reasoning):
What does this statement NECESSARILY imply about the user's current reality?
Think across five dimensions:

TEMPORAL: What time windows are now blocked, required, or shifted?
PHYSICAL / SPATIAL: What locations, tools, or objects are now inaccessible or obsolete?
ECONOMIC: What financial commitments, costs, or resources have changed?
ENABLING CONTEXT: What background conditions that other habits depend on have changed?
SOCIAL: What relationships, shared activities, or social patterns have changed?

STEP 2 — CROSS-REFERENCE (do not output this reasoning):
Part A — Profile cross-reference:
  For EACH detail in the profile summary, ask:
  "Does any intermediate state from Step 1 conflict with this detail — even indirectly?"

Part B — Stable trait cross-reference (MANDATORY):
  For EACH item listed under "Stored stable traits" above, ask:
  "Does any intermediate state from Step 1 conflict with this stored trait — even via a 2-3 step chain?"
  Generate a hypothesis for each anchor where a plausible 2-3 step connection exists.
  Skip anchors only when no reasonable chain can be constructed — do not force implausible links.

Part C — Current state verification (PRIORITY):
  For EACH item listed under "Stored current states to verify" above, ask:
  "Does this statement IMPLY — even via 1-2 steps — that this condition has changed, been resolved, or superseded?"
  Look for implied transitions, not just explicit contradictions.
  If the statement being true makes this current state questionable or uncertain, flag it.
  These are temporary conditions — err on the side of flagging potential changes.

Be aggressive across all parts. A tenuous connection is worth surfacing; the next step will verify it.

STEP 3 — OUTPUT your hypotheses.

Output JSON only:
{{
  "hypothetical_impacts": [
    "short description of what used to be true about the user but might now be outdated"
  ]
}}

Rules:
- Generate 6-14 hypotheses. Err on the side of more.
- Each hypothesis must describe a PAST OR CURRENT BELIEF that might now be wrong.
- Prioritize hypotheses targeting SPECIFIC items from the stable traits, current states, or profile.
- Include hypotheses from at least two different dimensions.
"""


def parse_hypotheses(text):
    try:
        # Strip markdown fences
        clean = text.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        data = json.loads(clean)
        return data.get("hypothetical_impacts", [])
    except Exception:
        return [f"[PARSE ERROR] {text[:200]}"]


def run_experiment():
    with open(INPUT_FILE) as f:
        samples = json.load(f)

    results = []
    total_runs = sum(len(s["statements"]) for s in samples if s["statements"])
    print(f"Running experiment on {len(samples)} samples, {total_runs} statement runs each × 2 variants")
    print()

    for s in samples:
        sample_id = s["sample"]
        if not s["statements"]:
            print(f"[{sample_id}] No statements — skipping")
            continue

        print(f"[{sample_id}] M_old: {s['M_old'][:70]}")
        print(f"         M_new: {s['M_new'][:70]}")
        print(f"         rel_sess={s['rel_idx']} stmts={len(s['statements'])} pers={s['n_persistent']} cs_total={s['n_cs_total']} top8cs={len(s['top8_cs'])}")

        sample_result = {
            "sample": sample_id,
            "M_old": s["M_old"],
            "M_new": s["M_new"],
            "rel_idx": s["rel_idx"],
            "n_persistent": s["n_persistent"],
            "n_cs_total": s["n_cs_total"],
            "statements": [],
        }

        for stmt in s["statements"]:
            print(f"  stmt: {stmt[:70]}")
            stmt_result = {"statement": stmt, "baseline": None, "new": None}

            # ─── BASELINE ───
            persistent_text = "\n".join(f"- {a}" for a in s["persistent_anchors"]) or "(none)"
            baseline_sys = BASELINE_PROMPT.format(
                statement=stmt,
                global_impression=s["impression"] or "(no profile yet)",
                preference_anchors=persistent_text,
            )
            baseline_raw = call_llm(baseline_sys, "Generate impact hypotheses.")
            baseline_hyps = parse_hypotheses(baseline_raw)
            stmt_result["baseline"] = baseline_hyps
            print(f"    baseline: {len(baseline_hyps)} hypotheses")

            # ─── NEW (two-section) ───
            cs_text = "\n".join(f"- {a}" for a in s["top8_cs"]) or "(none)"
            new_sys = NEW_PROMPT.format(
                statement=stmt,
                global_impression=s["impression"] or "(no profile yet)",
                persistent_anchors=persistent_text,
                current_state_anchors=cs_text,
            )
            new_raw = call_llm(new_sys, "Generate impact hypotheses.")
            new_hyps = parse_hypotheses(new_raw)
            stmt_result["new"] = new_hyps
            print(f"    new:      {len(new_hyps)} hypotheses")

            sample_result["statements"].append(stmt_result)
            time.sleep(0.3)

        results.append(sample_result)
        print()

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {OUTPUT_FILE}")
    return results


if __name__ == "__main__":
    run_experiment()
