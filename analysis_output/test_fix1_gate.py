"""
Test Fix 1: before/after TRIGGER_GATE_PROMPT on the 4 RC-1 dropped statements.

Run from repo root:
    python -m MyMem.analysis_output.test_fix1_gate
"""
from __future__ import annotations
import os, sys, json
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from MyMem.llm_layer.client import LLMClient
from MyMem.prompt_lib.new_templates import TRIGGER_GATE_PROMPT as LIVE_TRIGGER_GATE_PROMPT

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = BASE_DIR.parent / "STALE" / "STALE" / ".env"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip("'\"")
        if k:
            os.environ.setdefault(k, v)


def make_llm() -> LLMClient:
    load_env(DEFAULT_ENV_FILE)
    model = os.environ.get("TARGET_MODEL", "")
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    if not model or not key:
        raise ValueError("TARGET_MODEL / API key not set")
    return LLMClient(model=model, api_key=key, base_url=base_url)


# ── Exact statements and impressions extracted from eval traces ───────────────

RC1_CASES = [
    {
        "id": "T1-1 / 0c0086f5 / session 13",
        "statement": "I generally keep the hours between eight and eleven in the morning reserved for deep focus work.",
        "impression": (
            "The user has recently incorporated yoga into their evening wind-down routine, "
            "using props like a block and strap to compensate for reduced flexibility. They enjoy "
            "poses such as downward-facing dog, Legs Up The Wall, and Reclined Pigeon. This practice "
            "has improved their sleep quality and morning energy levels."
        ),
        "original_drop_reason": (
            "The statement provides additional information about the user's morning routine but does "
            "not contradict or invalidate the existing memory about their evening yoga practice."
        ),
    },
    {
        "id": "T1-2 / 5f77adc7 / session 10",
        "statement": (
            "I usually wait until my current device completely stops meeting my needs before I upgrade, "
            "because the newer features seldom change how I actually get things done."
        ),
        "impression": (
            "The user is a researcher (likely academic or professional) working on an AI in healthcare paper, "
            "currently struggling with data analysis. Since early January, they have dedicated at least 10 hours "
            "weekly to this project. Recently, they have adopted sustainable habits: using reusable bags and "
            "containers, shopping with their sister, and reducing food waste by planning meals around leftovers "
            "and pantry staples."
        ),
        "original_drop_reason": (
            "The statement describes a consistent personal habit that does not conflict with or change any "
            "existing memory about the user's current situation."
        ),
    },
    {
        "id": "T1-3 / 5308c7fd / session 14",
        "statement": (
            "The majority of my holdings are situated in broad market index funds, "
            "with the remaining portion allocated to corporate bonds."
        ),
        "impression": (
            "The user is an adult who recently moved to a new apartment four weeks ago, now has a 30-minute "
            "commute by new bike. They actively purchase gifts online and track spending, and enjoy culinary "
            "exploration with ingredients like gochujang and various rice types."
        ),
        "original_drop_reason": (
            "The statement provides new financial information that does not contradict or change any "
            "existing memories about the user."
        ),
    },
    {
        "id": "T2-1 / 3e3af301 / session 7",
        "statement": "I've kept a backup of all the license keys for the software I've purchased over the years.",
        "impression": (
            "The user is a working adult who commutes to work daily via a consistent bus route, paying per ride. "
            "They recently changed their wake-up time to 7:15 AM, suggesting an earlier start to their day."
        ),
        "original_drop_reason": (
            "The statement about backing up software license keys is a new personal detail that does not "
            "contradict or update any existing memory about the user's commute or wake-up habits."
        ),
    },
]

# ── Prompt variants ───────────────────────────────────────────────────────────

PROMPT_BEFORE = """Decide whether this user statement might require updating or invalidating existing memory about the user.

Statement: {statement}

Current user profile summary:
{global_impression}

Output JSON only:
{{
  "should_trigger": true,
  "reason": "one sentence explanation"
}}

Rules:
- should_trigger=true if the statement might change, contradict, or make obsolete any existing memory about the user
- should_trigger=true if the profile is empty (any factual personal statement is worth storing)
- should_trigger=false only for clearly irrelevant statements: weather comments, external world facts, task-only content with no personal state implication
- Common triggers: change of location, change of job/employer, change of relationship status, change of health, change of habits, change of living situation
- Even indirect statements can trigger: "adapting to life in a new city" implies a location change without naming the city
- Be generous with triggering — a false negative (missing an important update) is worse than a false positive
"""

PROMPT_AFTER = """Decide whether this user statement should be stored in the user's memory profile.

Statement: {statement}
Statement category: {category}

Current user profile summary (INCOMPLETE — captures recent highlights only, not every stored fact):
{global_impression}

Output JSON only:
{{
  "should_trigger": true,
  "reason": "one sentence explanation"
}}

Rules (apply the FIRST rule that matches):
1. should_trigger=true if the statement introduces a specific personal attribute
   (preference, habit, belief, portfolio, routine, possession, identity) —
   even if it does not conflict with anything in the summary.
   The summary is incomplete; absence of a topic does NOT mean the fact is already stored.
2. should_trigger=true if the statement might change, contradict, or make obsolete any
   existing memory about the user (location, job, relationship, health, habits, living situation).
3. should_trigger=true if the profile is empty.
4. should_trigger=false ONLY for: weather/nature observations, external world news,
   one-shot tasks with no personal state implication, generic filler.

Key: a false negative (failing to store a real personal fact) is far worse than
a false positive (storing a mildly redundant fact). When in doubt, trigger=true.
"""


def call_gate(llm: LLMClient, prompt_template: str, statement: str, impression: str, category: str = "") -> dict:
    prompt = (
        prompt_template
        .replace("{statement}", statement)
        .replace("{global_impression}", impression)
        .replace("{category}", category or "unspecified")
    )
    return llm.call_json(prompt, "Assess trigger.")


def run_all(llm: LLMClient) -> None:
    print("=" * 72)
    print("RC-1 FIX 1 BEFORE/AFTER TEST")
    print("=" * 72)
    print()

    for case in RC1_CASES:
        print(f"── {case['id']} ──")
        print(f"  STATEMENT : {case['statement'][:100]}")
        print(f"  IMPRESSION: {case['impression'][:120]}...")
        print(f"  ORIGINAL DROP REASON: {case['original_drop_reason'][:100]}")
        print()

        before = call_gate(llm, PROMPT_BEFORE, case["statement"], case["impression"])
        after = call_gate(llm, LIVE_TRIGGER_GATE_PROMPT, case["statement"], case["impression"], category="lasting_preference")

        b_trigger = before.get("should_trigger", "?")
        a_trigger = after.get("should_trigger", "?")
        b_reason = before.get("reason", "")
        a_reason = after.get("reason", "")

        status = "✓ FIXED" if (not b_trigger and a_trigger) else (
            "ALREADY PASS" if (b_trigger and a_trigger) else
            "STILL FAIL" if (not b_trigger and not a_trigger) else
            "REGRESSION"
        )

        print(f"  BEFORE  should_trigger={b_trigger}  [{status}]")
        print(f"    reason: {b_reason}")
        print(f"  AFTER   should_trigger={a_trigger}")
        print(f"    reason: {a_reason}")
        print()


if __name__ == "__main__":
    llm = make_llm()
    run_all(llm)
