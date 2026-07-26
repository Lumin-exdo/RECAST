"""
Two-experiment abductive judgment test.

Case: new_stmt = "starting their first full-time job"
Key candidates:
  m_00011 = "currently in the middle of my master's program"  ← should be invalidated
  m_00008 = "I've been enjoying my role as Senior Software Engineer..."  ← possible conflict

Exp 1: minimal prompt, no thinking
Exp 2: same core input, thinking enabled
"""

import os, sys, json, time
from openai import OpenAI

# Minimal candidate set — include key conflict + a few distractors
CANDIDATES = [
    ("m_00011", "currently in the middle of my master's program"),
    ("m_00008", "I've been enjoying my role as Senior Software Engineer for a while, especially the part where I now lead a team of five engineers"),
    ("m_00072", "planning to set up a home office soon"),
    ("m_00081", "training for another charity 5K run"),
    ("m_00016", "has a family"),
    ("m_00048", "lives in the eastern part of the state, near the coast"),
]

NEW_STMT = "starting their first full-time job"

# ── prompts ──────────────────────────────────────────────────────────────────

MINIMAL_SYSTEM = """A user made a new statement. For each stored memory below, decide:
does this new statement make the memory outdated or less reliable?

Return JSON only:
{
  "judgments": [
    {
      "target_item_id": "m_XXXXX",
      "inference_chain": "brief reasoning",
      "confidence": 0.0,
      "verdict": "invalidated | weakened | ok"
    }
  ]
}

Only include memories that are genuinely affected (invalidated or weakened). Skip memories that are clearly independent.
"""

FULL_SYSTEM = """For each candidate memory, judge whether a user's new statement makes it outdated or less reliable.

New statement from user: starting their first full-time job

Supporting impact hypotheses (derived by reasoning from the statement through intermediate states,
then cross-referencing with the user's profile — use these as reasoning bridges):
- user was not committed to a fixed 40-hour workweek schedule
- user had flexible morning availability for training runs
- user was able to attend mid-day or late-afternoon appointments
- user had no daily commute time consuming their schedule
- user had more energy or time for creative projects like songwriting
- user was not earning a full-time salary (income source was different or nonexistent)
- user had no employer-provided health insurance or benefits
- user had more time for evening social activities
- user had fewer constraints on weekend availability
- user was not required to be at a specific physical location 5 days a week
- user had not yet integrated a full-time job with their master's program schedule
- user could practice guitar or write songs at any time of day
- user was not managing a new workplace social environment
- user's budget for hobbies was not supplemented by a full-time salary
- user had not yet formed new habits like packing lunch or planning around a work schedule

Output JSON only:
{
  "judgments": [
    {
      "target_item_id": "m_XXXXX",
      "target_content": "the memory content",
      "inference_chain": "step-by-step reasoning chain from statement to why this memory is affected",
      "confidence": 0.0,
      "type": "direct_invalidation|weakens_support|no_conflict"
    }
  ]
}

Rules:
- SKIP any candidate whose content is IDENTICAL or nearly identical to the new statement
- direct_invalidation: the memory is now almost certainly false (confidence >= 0.65)
- weakens_support: reliability is reduced but not conclusively invalidated (confidence 0.35-0.75)
- no_conflict: memory is unaffected (confidence < 0.35 OR clearly independent)
- Only include direct_invalidation and weakens_support — skip no_conflict items
"""

def build_user_msg(system_style: str) -> str:
    lines = [f"New statement from user: {NEW_STMT}\n\nCandidate memories to evaluate:"]
    for mid, content in CANDIDATES:
        lines.append(f"[{mid}] {content}")
    if system_style == "minimal":
        lines.append("\nJudge each memory. Return JSON.")
    else:
        lines.append("\nFor each candidate, reason using abductive inference: if the user's statement is true, does this memory remain valid?")
    return "\n".join(lines)


def call_api(system_prompt: str, user_msg: str, thinking: bool, label: str):
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    model = os.environ.get("TARGET_MODEL", "deepseek-v4-flash")

    client = OpenAI(api_key=api_key, base_url=base_url)

    kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_msg},
        ],
        max_tokens=2000,
    )
    if thinking:
        kwargs["extra_body"] = {"thinking": {"type": "enabled", "budget_tokens": 8000}}
    else:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  model={model}  thinking={'ON' if thinking else 'OFF'}")
    print(f"  system_style={'minimal' if 'verdict' in system_prompt else 'full-rules'}")
    print()

    t0 = time.time()
    resp = client.chat.completions.create(**kwargs)
    elapsed = time.time() - t0

    content = resp.choices[0].message.content or ""

    # Also grab thinking content if present
    thinking_text = ""
    if hasattr(resp.choices[0].message, 'reasoning_content'):
        thinking_text = resp.choices[0].message.reasoning_content or ""

    print(f"  [elapsed: {elapsed:.1f}s]")
    if thinking_text:
        print(f"\n--- THINKING ({len(thinking_text)} chars) ---")
        print(thinking_text[:2000])
        if len(thinking_text) > 2000:
            print(f"  ... [truncated, total {len(thinking_text)} chars]")
    print(f"\n--- RESPONSE ---")
    print(content)

    # Parse JSON
    try:
        j_start = content.find('{')
        if j_start >= 0:
            parsed = json.loads(content[j_start:])
            judgments = parsed.get("judgments", [])
            print(f"\n  → {len(judgments)} judgment(s) returned")
            for j in judgments:
                mid = j.get("target_item_id","?")
                conf = j.get("confidence", j.get("verdict","?"))
                jtype = j.get("type", j.get("verdict","?"))
                chain = j.get("inference_chain","")[:120]
                print(f"    {mid}: {jtype} conf={conf}")
                print(f"      chain: {chain}")
        else:
            print("  → could not parse JSON")
    except Exception as e:
        print(f"  → parse error: {e}")

    return content, thinking_text


def main():
    # Load env
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

    print("=" * 60)
    print("ABDUCTIVE JUDGMENT EXPERIMENT")
    print("Case: 7ee76c41 — 'starting first full-time job'")
    print("      vs m_00011 'currently in master's program'")
    print("      Original output: {judgments: []}  ← missed conflict")
    print("=" * 60)

    # EXP 1: minimal prompt, no thinking
    u1 = build_user_msg("minimal")
    r1, t1 = call_api(MINIMAL_SYSTEM, u1, thinking=False, label="EXP 1: Minimal prompt, NO thinking")

    # EXP 2: same input, thinking ON
    u2 = build_user_msg("full")
    r2, t2 = call_api(FULL_SYSTEM, u2, thinking=True, label="EXP 2: Full prompt, WITH thinking")

    print("\n\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Original (full rules, no thinking): {{judgments: []}}  ← missed")

    for label, resp in [("EXP1 minimal no-think", r1), ("EXP2 full thinking", r2)]:
        try:
            j_start = resp.find('{')
            parsed = json.loads(resp[j_start:])
            hits = [j for j in parsed.get("judgments",[]) if j.get("target_item_id") == "m_00011"]
            if hits:
                h = hits[0]
                print(f"{label}: m_00011 → conf={h.get('confidence')} type={h.get('type',h.get('verdict'))}")
            else:
                print(f"{label}: m_00011 → NOT in output (missed)")
        except:
            print(f"{label}: parse failed")


if __name__ == "__main__":
    main()
