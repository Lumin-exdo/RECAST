#!/usr/bin/env python3
"""
Standalone test: 5 E2E prompt variants on sample 79e4cc40 (T1, abs_idx=133)
Case: user HAD a rule to decline crowded parties → now enjoys big street festivals
Tests whether each variant correctly refuses to write a polite decline message.

Run from any directory (no RECAST pipeline needed).
Output: /mnt/laq/RECAST/analysis_output/prompt_variant_test_20260617.md
"""
import json
import re
import sys
import time
from pathlib import Path

# ── CONFIG ──────────────────────────────────────────────────────────────────
QWEN_KEY = "${QWEN_API_KEY}"
QWEN_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3.5-plus"
OUTPUT = Path("/mnt/laq/RECAST/analysis_output/prompt_variant_test_20260617.md")

CACHE_DIR = Path("/mnt/laq/RECAST/runs/474e206/remaining45/0133/.cache")
DIM2_CACHE = "14e3629804acf045d58b930e83571d541156d02f130c1e0984975226600eeb6d.json"
DIM3_CACHE = "bf661e566c3ac75414fa9e7d2afff68020f1cd06c77ebd9134b9cc4dc82abe94.json"


# ── LOAD INPUTS ──────────────────────────────────────────────────────────────
def load_input(filename: str) -> tuple[str, str]:
    """Returns (system_prompt, user_message)."""
    with open(CACHE_DIR / filename) as f:
        d = json.load(f)
    msgs = d["messages"]
    return msgs[0]["content"], msgs[1]["content"]


# ── PROMPT VARIANT CONSTRUCTORS ───────────────────────────────────────────────
BASELINE_INJECTION = ""  # no change

STEP5_IA = """\n5. If your answer includes any recommendation, plan, or action: explicitly confirm that the recommendation is built on the *new* confirmed state, NOT on any [STALE] or [UNCERTAIN] memory. If the request only makes sense under a [STALE] assumption, say so and decline to fulfill that specific part rather than complying with the outdated premise."""

FORBIDDEN_ID = """\n   Before proceeding to Step 4: list every action or recommendation that would be appropriate ONLY if the [STALE] information were still true. Label this "FORBIDDEN ACTIONS (based on outdated state):" followed by the list. Then confirm your answer avoids all of them."""

CRITIC_VIB = """\n5. Critic pass: Re-read your draft answer from Step 4. Identify any part that implicitly helps the user do something that only makes sense if a [STALE] memory were still true. If found, revise that part before outputting your final answer."""

REFRAME_VIC = """Your task is NOT simply to answer the user's question. Your primary obligation is to protect the user from acting on outdated information.

If the question embeds an assumption that conflicts with a [STALE] memory, you must:
a) Refuse to help with the action that relies on the false assumption
b) Explain what has actually changed
c) Offer to help based on the user's CURRENT confirmed state instead

Only after addressing (a)–(c) should you provide any positive suggestions."""


def make_variant(base_sys: str, variant: str) -> str:
    # Find the insertion point: just before "Output JSON only:"
    marker = "\nOutput JSON only:"

    if variant == "baseline":
        return base_sys

    elif variant == "I-A":
        return base_sys.replace(marker, STEP5_IA + marker)

    elif variant == "I-B":
        # Replace [STALE] item labels: strip the "(was true, changed — ...)" suffix
        # Pattern: [STALE] id: content  (was true, changed — ...)
        def strip_reason(m):
            item_part = m.group(1)  # "id: content"
            return f"[OUTDATED — DO NOT BASE ACTIONS ON THIS] {item_part}"
        result = re.sub(
            r'\[STALE\] ([^\n]+?)\s+\(was true, changed[^\n]*\)',
            strip_reason,
            base_sys
        )
        return result

    elif variant == "I-D":
        # Add forbidden-actions enumeration requirement inside Step 3
        old_step3 = "3. If the assumption is contradicted or cast in doubt, open your answer by naming the discrepancy clearly. Do not reconcile conflicting information by assuming one side is right — surface the conflict and let the user know what has changed."
        new_step3 = old_step3 + FORBIDDEN_ID
        if old_step3 in base_sys:
            return base_sys.replace(old_step3, new_step3)
        # fallback: insert before Output JSON
        return base_sys.replace(marker, FORBIDDEN_ID + marker)

    elif variant == "VI-B":
        return base_sys.replace(marker, CRITIC_VIB + marker)

    elif variant == "VI-C":
        # Replace the opening framing sentence
        old_open = "Answer the user's question using their stored memories."
        if old_open in base_sys:
            return base_sys.replace(old_open, old_open + "\n\n" + REFRAME_VIC, 1)
        return REFRAME_VIC + "\n\n" + base_sys

    return base_sys


VARIANTS = ["baseline", "I-A", "I-B", "I-D", "VI-B", "VI-C"]


# ── API CALL ──────────────────────────────────────────────────────────────────
def call_llm(system: str, user: str, label: str) -> str:
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("openai package not installed")

    client = OpenAI(api_key=QWEN_KEY, base_url=QWEN_BASE)
    t0 = time.time()
    print(f"  → {label} ...", end="", flush=True)
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        extra_body={"enable_thinking": False},
        timeout=120,
    )
    elapsed = time.time() - t0
    text = resp.choices[0].message.content
    print(f" done ({elapsed:.1f}s)")
    return text


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    sys2, user2 = load_input(DIM2_CACHE)
    sys3, user3 = load_input(DIM3_CACHE)

    query2 = next(l for l in sys2.splitlines() if "User question:" in l)
    query3 = next(l for l in sys3.splitlines() if "User question:" in l)

    sections = []
    sections.append(f"""# Prompt Variant A/B Test — Sample 79e4cc40 (T1, abs_idx=133)

**Sample**: User HAD a rule to decline crowded parties → NOW enjoys big street festivals
**Model**: {MODEL} (no-thinking)
**Date**: 2026-06-17
**Variants tested**: {', '.join(VARIANTS)}

## Test Case Setup

**dim2 probe query** (embeds false premise — asks model to help with old-state action):
> {query2.replace("User question: ", "")}

**dim3 action query** (open-ended — model must volunteer current preference):
> {query3.replace("User question: ", "")}

**Key stale memory in both inputs**:
- `[STALE] m_00020: has made it a rule to decline invitations to crowded parties`
- `[ACTIVE] m_00169/m_00170: has been really enjoying big street festivals; being in a lively crowd feels energizing`

**Correct behavior**:
- dim2: refuse to write decline message; explain user now enjoys crowds
- dim3: recommend crowd-based activities (street festivals) based on CURRENT preference

---
""")

    for query_label, sys_prompt, user_msg in [
        ("DIM2 PROBE (false premise embedded)", sys2, user2),
        ("DIM3 ACTION (open-ended compliance)", sys3, user3),
    ]:
        sections.append(f"## Query: {query_label}\n")
        for v in VARIANTS:
            modified_sys = make_variant(sys_prompt, v)
            answer = call_llm(modified_sys, user_msg, f"{query_label[:6]}/{v}")

            # Parse JSON if present
            try:
                parsed = json.loads(answer)
                assumption = parsed.get("assumption", "?")
                ans_text = parsed.get("answer", answer)
            except Exception:
                assumption = "(unparseable)"
                ans_text = answer

            # Quick verdict: does it refuse/redirect for old-state action?
            bad_signals = ["polite message", "politely decline", "turn down this invitation", "suggest a message"]
            good_signals = ["outdated", "no longer", "now enjoys", "street festival", "changed", "stale", "currently", "energiz"]
            has_bad = any(s.lower() in ans_text.lower() for s in bad_signals)
            has_good = any(s.lower() in ans_text.lower() for s in good_signals)

            if query_label.startswith("DIM2"):
                verdict = "✅ CORRECT" if (has_good and not has_bad) else ("❌ FAIL (gave decline msg)" if has_bad else "⚠️ PARTIAL")
            else:
                verdict = "✅ CORRECT" if has_good else "❌ FAIL (missed crowd preference)"

            sections.append(f"""### Variant {v} — {verdict}

**Assumption extracted**: {assumption}

**Answer**:
{ans_text}

---
""")

    output = "\n".join(sections)
    OUTPUT.write_text(output, encoding="utf-8")
    print(f"\nSaved to {OUTPUT}")
    print(output[:2000])


if __name__ == "__main__":
    main()
